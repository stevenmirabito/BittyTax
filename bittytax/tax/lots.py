# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026
"""Tax lots and the ledger which tracks where they are held (US tax rules)."""

import copy
from decimal import Decimal

from ..holdings import Holdings

# Bucket used for every lot when wallets are pooled together (universal method)
UNIVERSAL = '<universal>'


class Lot(object):
    """Units of an asset acquired together, which share a cost basis and holding period.

    A lot keeps its identity when it is split or moved between wallets: every fragment has the
    same lot_id, basis per unit and acquisition date as the original.
    """

    def __init__(self, asset, wallet, quantity, cost, fees, acquired, lot_id, seq):
        self.asset = asset
        self.wallet = wallet
        self.quantity = quantity
        self.cost = cost
        self.fees = fees
        self.acquired = acquired
        self.lot_id = lot_id
        # Insertion order of the original acquisition, breaks ties between equal timestamps
        self.seq = seq
        # (timestamp, from wallet, to wallet) for each move between wallets
        self.history = []

    @classmethod
    def from_buy(cls, buy, seq):
        return cls(buy.asset, buy.wallet, buy.quantity,
                   buy.cost if buy.cost is not None else Decimal(0),
                   buy.fee_value or Decimal(0),
                   buy.timestamp,
                   cls.make_lot_id(buy),
                   seq)

    @staticmethod
    def make_lot_id(buy):
        return '%s:%s:%s:%s.%s' % (buy.asset, buy.wallet,
                                   buy.timestamp.strftime('%Y-%m-%dT%H:%M:%S'),
                                   buy.tid[0], buy.tid[1])

    def split(self, quantity):
        """Split off a fragment of `quantity` units, this lot keeps the remainder."""
        fragment = copy.copy(self)
        fragment.history = list(self.history)

        fragment.cost = self.cost * (quantity / self.quantity)
        fragment.fees = self.fees * (quantity / self.quantity)
        fragment.quantity = quantity

        self.cost -= fragment.cost
        self.fees -= fragment.fees
        self.quantity -= quantity
        return fragment

    def unit_cost(self):
        return (self.cost + self.fees) / self.quantity if self.quantity else Decimal(0)

    def __repr__(self):
        return 'Lot(%s %s %s cost=%s fees=%s @%s)' % (
            self.lot_id, self.wallet, self.quantity, self.cost, self.fees,
            self.acquired.isoformat())


class Shortfall(Exception):
    """More units were removed from a wallet than the ledger holds there."""

    def __init__(self, wallet, asset, requested, available):
        super(Shortfall, self).__init__(
            '%s:%s requested %s but only %s held' % (wallet, asset, requested, available))
        self.wallet = wallet
        self.asset = asset
        self.requested = requested
        self.available = available


class WalletLedger(object):
    """Open lots by (wallet, asset).

    When per_wallet is False every wallet shares one bucket per asset, which is the universal
    method allowed before 2025. Lots still remember the wallet they were acquired in.
    """

    def __init__(self, per_wallet):
        self.per_wallet = per_wallet
        self.lots = {}
        self.seq = 0

    def _bucket(self, wallet, asset):
        key = (wallet if self.per_wallet else UNIVERSAL, asset)
        if key not in self.lots:
            self.lots[key] = []
        return self.lots[key]

    def next_seq(self):
        self.seq += 1
        return self.seq

    def add(self, lot):
        self._bucket(lot.wallet, lot.asset).append(lot)

    def lots_in(self, wallet, asset):
        return list(self._bucket(wallet, asset))

    def balance(self, wallet, asset):
        return sum((lot.quantity for lot in self._bucket(wallet, asset)), Decimal(0))

    def remove(self, wallet, asset, quantity, selector, disposal=None):
        """Remove `quantity` units, returns the lots taken in the order the selector chose.

        If the wallet doesn't hold enough, everything held is removed and the Shortfall
        is raised with the lots taken attached, so the caller can decide how to handle it.
        """
        bucket = self._bucket(wallet, asset)
        taken = []
        remaining = quantity

        for lot in selector.order(bucket, disposal):
            if remaining <= 0:
                break

            if lot.quantity > remaining:
                taken.append(lot.split(remaining))
                remaining = Decimal(0)
            else:
                bucket.remove(lot)
                taken.append(lot)
                remaining -= lot.quantity

        if remaining > 0:
            shortfall = Shortfall(wallet, asset, quantity, quantity - remaining)
            shortfall.taken = taken
            raise shortfall

        return taken

    def open_lots(self):
        return [lot for bucket in self.lots.values() for lot in bucket if lot.quantity]

    def holdings(self):
        """Roll up open lots by asset, for the integrity check and holdings report."""
        holdings = {}
        for (_, asset), bucket in sorted(self.lots.items()):
            if asset not in holdings:
                holdings[asset] = Holdings(asset)
            for lot in bucket:
                holdings[asset].quantity += lot.quantity
                holdings[asset].cost += lot.cost
                holdings[asset].fees += lot.fees
        return holdings


class FifoSelector(object):
    """First-in, first-out by acquisition date, the default when units aren't identified."""

    name = 'First-In, First-Out'

    @staticmethod
    def order(lots, disposal=None):
        return sorted(lots, key=lambda lot: (lot.acquired, lot.seq))
