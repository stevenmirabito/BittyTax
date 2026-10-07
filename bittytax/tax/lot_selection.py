# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026
"""Which lots a disposal or transfer takes: FIFO unless units were identified in advance.

Treas. Reg. 1.1012-1(j)(3) requires adequate identification no later than the date and time
of the sale. A standing order applies only from when it was recorded, and a specific
identification recorded after the transaction it names is rejected.
"""

from .lots import FifoSelector


class LifoSelector(object):
    name = 'Last-In, First-Out'

    @staticmethod
    def order(lots, disposal=None):
        return sorted(lots, key=lambda lot: (lot.acquired, lot.seq), reverse=True)


class HifoSelector(object):
    name = 'Highest-In, First-Out'

    @staticmethod
    def order(lots, disposal=None):
        return sorted(lots, key=lambda lot: (-lot.unit_cost(), lot.acquired, lot.seq))


class LofoSelector(object):
    name = 'Lowest-In, First-Out'

    @staticmethod
    def order(lots, disposal=None):
        return sorted(lots, key=lambda lot: (lot.unit_cost(), lot.acquired, lot.seq))


class SpecificSelector(object):
    name = 'Specific Identification'

    def __init__(self, lot_id):
        self.lot_id = lot_id

    def order(self, lots, disposal=None):
        return FifoSelector.order([lot for lot in lots if lot.lot_id == self.lot_id])


METHODS = {'FIFO': FifoSelector, 'LIFO': LifoSelector, 'HIFO': HifoSelector,
           'LOFO': LofoSelector}


class StandingOrder(object):
    def __init__(self, wallet, method, effective_from):
        self.wallet = wallet
        self.method = method
        self.selector = METHODS[method]()
        self.name = '%s (standing order)' % self.selector.name
        self.effective_from = effective_from

    def order(self, lots, disposal=None):
        return self.selector.order(lots, disposal)


def standing_order_for(orders, wallet, timestamp):
    """Latest order in force at the time, a wallet's own order before a '*' order."""
    in_force = [o for o in orders if o.effective_from <= timestamp]
    for candidates in ([o for o in in_force if o.wallet == wallet],
                       [o for o in in_force if o.wallet == '*']):
        if candidates:
            return max(candidates, key=lambda o: o.effective_from)
    return None


class Identification(object):
    def __init__(self, source, wallet, timestamp, asset, quantity, lot_id, recorded_at):
        self.source = source
        self.wallet = wallet
        self.timestamp = timestamp
        self.asset = asset
        self.quantity = quantity
        self.lot_id = lot_id
        self.recorded_at = recorded_at
        self.used = False

    def key(self):
        return identification_key(self.wallet, self.asset, self.timestamp)


def identification_key(wallet, asset, timestamp):
    return (wallet, asset, timestamp.replace(microsecond=0).timestamp())
