# -*- coding: utf-8 -*-
# (c) Nano Nano Ltd 2019
# (c) Steven Mirabito 2022

import sys
import copy
from decimal import Decimal

from colorama import Fore, Back
from tqdm import tqdm

from ..config import config
from ..transactions import Buy, Sell
from .base import TaxCalculator, TaxEvent, TaxEventCapitalGains
from .lots import Lot, WalletLedger, FifoSelector, Shortfall

PRECISION = Decimal('0.00')


class TaxCalculatorUS(TaxCalculator):
    DISPOSAL_FIFO = FifoSelector.name

    INCOME_TYPES = (Buy.TYPE_MINING, Buy.TYPE_STAKING, Buy.TYPE_DIVIDEND, Buy.TYPE_INTEREST,
                    Buy.TYPE_INCOME, Buy.TYPE_AIRDROP)

    def __init__(self, transactions, tax_rules):
        super(TaxCalculatorUS, self).__init__(transactions, tax_rules)
        self.ordered = []
        self.ledger = None
        self.selector = FifoSelector()
        self.integrity_failures = []

    def calculate(self, skip_integrity_check=False):
        self.prepare_transactions()
        self.ledger = self.replay(self.ordered, WalletLedger(per_wallet=False))
        self.holdings = self.ledger.holdings()

    def prepare_transactions(self):
        transactions = copy.deepcopy(self.transactions)
        self.ordered = sorted((t for t in transactions if t.is_crypto()),
                              key=lambda t: (t.timestamp, t.tid))

        if config.debug:
            print("%sprepare: total transactions=%d" % (Fore.CYAN, len(self.ordered)))

    def replay(self, transactions, ledger):
        for t in tqdm(transactions,
                      unit='t',
                      desc="%sprocess lots%s" % (Fore.CYAN, Fore.GREEN),
                      disable=bool(config.debug or not sys.stdout.isatty())):
            if config.debug:
                print("%slots: %s" % (Fore.GREEN, t))

            if isinstance(t, Buy):
                if t.acquisition:
                    self.add_lot(ledger, t)
            elif isinstance(t, Sell):
                if t.disposal:
                    self.dispose(ledger, t)
                elif t.t_type != Sell.TYPE_WITHDRAWAL:
                    self.remove_without_disposal(ledger, t)

        return ledger

    def add_lot(self, ledger, t):
        lot = Lot.from_buy(t, ledger.next_seq())
        ledger.add(lot)
        if config.debug:
            print("%slots:   add %s" % (Fore.YELLOW, lot))

    def take_lots(self, ledger, t):
        try:
            return ledger.remove(t.wallet, t.asset, t.quantity, self.selector, t), Decimal(0)
        except Shortfall as e:
            self.integrity_failures.append(e)
            tqdm.write("%sWARNING%s Insufficient %s in %s for %s, %s units have no cost basis" % (
                Back.YELLOW+Fore.BLACK, Back.RESET+Fore.YELLOW, t.asset,
                'holdings' if not ledger.per_wallet else t.wallet, t._format_tid(),
                '{:0,f}'.format((e.requested - e.available).normalize())))
            return e.taken, e.requested - e.available

    def dispose(self, ledger, t):
        lots, unmatched = self.take_lots(ledger, t)

        if unmatched:
            # Units which can't be traced to an acquisition have a zero basis
            lots.append(Lot(t.asset, t.wallet, unmatched, Decimal(0), Decimal(0), None,
                            None, None))

        for lot, s in self.split_sell(t, lots):
            tax_event = TaxEventCapitalGainsUS(self.selector.name, lot, s)
            self.tax_events[self.which_tax_year(tax_event.date)].append(tax_event)
            if config.debug:
                print("%slots:   %s <- %s" % (Fore.CYAN, tax_event, lot))

    def remove_without_disposal(self, ledger, t):
        # e.g. a transfer fee which isn't treated as a disposal, its basis stays with the asset
        lots, _ = self.take_lots(ledger, t)
        basis = sum((lot.cost for lot in lots), Decimal(0))
        fees = sum((lot.fees for lot in lots), Decimal(0))

        remaining = FifoSelector.order(ledger.lots_in(t.wallet, t.asset))
        if remaining:
            remaining[0].cost += basis
            remaining[0].fees += fees

    @staticmethod
    def split_sell(s, lots):
        """Pair each lot with the matching portion of the sell, splitting proceeds and fees."""
        parts = []
        for lot in lots[:-1]:
            remainder = s.split_sell(lot.quantity)
            parts.append((lot, s))
            s = remainder

        if lots:
            parts.append((lots[-1], s))
        return parts

    def process_disposal(self, t, cost, fees):
        raise NotImplementedError("US disposals are matched to lots")


class TaxEventCapitalGainsUS(TaxEventCapitalGains):
    # pylint: disable=super-init-not-called
    def __init__(self, disposal_type, lot, s):
        TaxEvent.__init__(self, s.timestamp, s.asset)
        self.disposal_type = disposal_type
        self.quantity = s.quantity
        self.wallet = s.wallet
        self.lot_id = lot.lot_id
        self.cost = lot.cost.quantize(PRECISION)
        self.fees = (lot.fees + (s.fee_value or Decimal(0))).quantize(PRECISION)
        self.proceeds = s.proceeds.quantize(PRECISION)
        self.gain = self.proceeds - self.cost - self.fees
        self.acquisition_date = lot.acquired
