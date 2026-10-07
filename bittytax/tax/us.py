# -*- coding: utf-8 -*-
# (c) Nano Nano Ltd 2019
# (c) Steven Mirabito 2022

import sys
import copy
from datetime import date, datetime
from decimal import Decimal

from colorama import Fore, Back
from tqdm import tqdm
import dateutil.parser

from ..config import config
from ..transactions import Buy, Sell
from .base import TaxCalculator, TaxEvent, TaxEventCapitalGains, CalculateCapitalGains
from .lots import Lot, WalletLedger, FifoSelector, Shortfall
from .transfers import link_transfers, acquired_date

PRECISION = Decimal('0.00')


class TaxCalculatorUS(TaxCalculator):
    """US tax rules.

    Before the cutover (2025-01-01) every wallet is pooled into one FIFO queue per asset, the
    universal method used for returns already filed. From the cutover, Treas. Reg. 1.1012-1(j)
    requires basis to be identified wallet by wallet: lots are held in the wallet they were
    acquired in, follow transfers between wallets, and FIFO applies within each wallet.

    No Rev. Proc. 2024-28 allocation is applied: the lots in each wallet at the cutover are
    found by replaying the earlier history wallet by wallet (actual tracing).
    """
    DISPOSAL_FIFO = FifoSelector.name

    INCOME_TYPES = (Buy.TYPE_MINING, Buy.TYPE_STAKING, Buy.TYPE_DIVIDEND, Buy.TYPE_INTEREST,
                    Buy.TYPE_INCOME, Buy.TYPE_AIRDROP)

    # Units leave the taxpayer without a gain or loss being realized: a gift isn't a
    #  realization event for the donor, transfers between spouses fall under IRC 1041, a
    #  donation isn't a sale, and a casualty/theft loss isn't a capital loss
    NON_SALE_TYPES = (Sell.TYPE_GIFT_SENT, Sell.TYPE_GIFT_SPOUSE, Sell.TYPE_CHARITY_SENT,
                      Sell.TYPE_LOST)

    def __init__(self, transactions, tax_rules):
        super(TaxCalculatorUS, self).__init__(transactions, tax_rules)
        self.ordered = []
        self.links = None
        self.ledger = None
        self.transition = None
        self.selector = FifoSelector()
        self.integrity_failures = []
        self.warnings = []
        self._phase = None
        self._record_events = True
        self._moved_by_record = {}

    @staticmethod
    def get_cutover():
        value = config.us_wallet_cutover
        if isinstance(value, str):
            value = dateutil.parser.parse(value).date()
        return datetime(value.year, value.month, value.day, tzinfo=config.TZ_LOCAL)

    def calculate(self, skip_integrity_check=False):
        self.prepare_transactions()
        self.links = link_transfers(self.ordered)
        for error in self.links.errors:
            self.fail(error)

        cutover = self.get_cutover()
        before = [t for t in self.ordered if t.timestamp < cutover]
        after = [t for t in self.ordered if t.timestamp >= cutover]

        # Gains before the cutover, as reported under the universal method
        universal = self.replay(before, WalletLedger(per_wallet=False), 'universal', True)

        # Trace the same history wallet by wallet, only to find each wallet's lots at the cutover
        traced = self.replay(before, WalletLedger(per_wallet=True), 'tracing', False)
        self.transition = Transition(cutover, universal, traced)
        for failure in self.transition.failures:
            self.fail(failure)

        self.ledger = self.replay(after, traced, 'per-wallet', True)
        self.holdings = self.ledger.holdings()

    def prepare_transactions(self):
        transactions = copy.deepcopy(self.transactions)
        self.ordered = sorted((t for t in transactions if t.is_crypto()),
                              key=lambda t: (t.timestamp, t.tid))

        if config.debug:
            print("%sprepare: total transactions=%d" % (Fore.CYAN, len(self.ordered)))

    def fail(self, message):
        message = "%s%s" % ("[%s] " % self._phase if self._phase else '', message)
        self.integrity_failures.append(message)
        tqdm.write("%sERROR%s %s" % (Back.RED+Fore.BLACK, Back.RESET+Fore.RED, message))

    def warn(self, message):
        self.warnings.append(message)
        tqdm.write("%sWARNING%s %s" % (Back.YELLOW+Fore.BLACK, Back.RESET+Fore.YELLOW, message))

    def replay(self, transactions, ledger, phase, record_events):
        # Each pass splits its own copies, the transactions are shared between passes
        transactions = copy.deepcopy(transactions)
        self._phase = phase
        self._record_events = record_events
        self._moved_by_record = {}

        if config.debug:
            print("%slots: %s pass" % (Fore.CYAN, phase))

        for t in tqdm(transactions,
                      unit='t',
                      desc="%sprocess lots (%s)%s" % (Fore.CYAN, phase, Fore.GREEN),
                      disable=bool(config.debug or not sys.stdout.isatty())):
            if config.debug:
                print("%slots: %s" % (Fore.GREEN, t))

            if isinstance(t, Buy):
                if t.t_type == Sell.TYPE_LOST:
                    # The UK "negligible value" buy-back has no US equivalent
                    continue
                if t.acquisition:
                    self.add_lot(ledger, t)
                elif t.t_type == Buy.TYPE_DEPOSIT and ledger.per_wallet:
                    self.deposit(ledger, t)
            elif isinstance(t, Sell):
                if t.t_type in self.NON_SALE_TYPES:
                    self.remove_non_sale(ledger, t)
                elif t.disposal:
                    self.dispose(ledger, t)
                elif t.t_type == Sell.TYPE_WITHDRAWAL:
                    if ledger.per_wallet:
                        self.withdraw(ledger, t)
                else:
                    self.remove_without_disposal(ledger, t)

        self._phase = None
        return ledger

    def add_lot(self, ledger, t):
        lot = Lot.from_buy(t, ledger)
        ledger.add(lot)
        if config.debug:
            print("%slots:   add %s" % (Fore.YELLOW, lot))
        return lot

    def take_lots(self, ledger, t, quantity=None):
        quantity = t.quantity if quantity is None else quantity
        try:
            return ledger.remove(t.wallet, t.asset, quantity, self.selector, t), Decimal(0)
        except Shortfall as e:
            self.fail("Insufficient %s in %s for %s [TID:%s], %s units can't be traced to an "
                      "acquisition" % (t.asset,
                                       t.wallet if ledger.per_wallet else 'any wallet',
                                       t.t_type, t._format_tid(),
                                       '{:0,f}'.format((e.requested - e.available).normalize())))
            return e.taken, e.requested - e.available

    def dispose(self, ledger, t):
        lots, unmatched = self.take_lots(ledger, t)
        lots = merge_fragments(lots, t)

        if unmatched:
            # Units which can't be traced to an acquisition have a zero basis
            lots.append(Lot(t.asset, t.wallet, unmatched, Decimal(0), Decimal(0), None,
                            None, None))

        if not self._record_events:
            return

        for lot, s in self.split_sell(t, lots):
            tax_event = TaxEventCapitalGainsUS(self.selector.name, lot, s)
            self.tax_events[self.which_tax_year(tax_event.date)].append(tax_event)
            if config.debug:
                print("%slots:   %s <- %s" % (Fore.CYAN, tax_event, lot))

    def remove_non_sale(self, ledger, t):
        lots, unmatched = self.take_lots(ledger, t)
        lots = merge_fragments(lots, t)
        if unmatched:
            lots.append(Lot(t.asset, t.wallet, unmatched, Decimal(0), Decimal(0), None,
                            None, None))

        if not self._record_events:
            return

        if t.t_type == Sell.TYPE_LOST:
            self.warn("Lost %s %s [TID:%s] is not a capital loss, and a casualty or theft "
                      "loss is generally not deductible for individuals" % (
                          '{:0,f}'.format(t.quantity.normalize()), t.asset, t._format_tid()))

        for lot, s in self.split_sell(t, lots):
            tax_event = TaxEventNonSale(lot, s)
            self.tax_events[self.which_tax_year(tax_event.date)].append(tax_event)
            if config.debug:
                print("%slots:   %s <- %s" % (Fore.CYAN, tax_event, lot))

    def withdraw(self, ledger, t):
        deposit = self.links.deposit(t)
        if deposit is None:
            lots, _ = self.take_lots(ledger, t)
            self.fail("Withdrawal of %s %s from %s [TID:%s] has no matching Deposit, record "
                      "where it went (e.g. Gift-Sent, Spend) or add the Deposit" % (
                          '{:0,f}'.format(t.quantity.normalize()), t.asset, t.wallet,
                          t._format_tid()))
            return

        moved, _ = self.take_lots(ledger, t, deposit.quantity)
        if t.quantity > deposit.quantity:
            # Taken from the amount sent, record it as a fee to treat it as a disposal
            short = t.quantity - deposit.quantity
            removed, _ = self.take_lots(ledger, t, short)
            carry_basis(moved, removed)
            if self._record_events:
                self.warn("Deposit [TID:%s] is %s %s less than Withdrawal [TID:%s], the "
                          "difference is removed and its basis carried with the transfer" % (
                              deposit._format_tid(), '{:0,f}'.format(short.normalize()),
                              t.asset, t._format_tid()))

        ledger.move(moved, deposit.wallet, t.timestamp)
        self._moved_by_record[id(t.t_record)] = moved
        if config.debug:
            for lot in moved:
                print("%slots:   move %s" % (Fore.YELLOW, lot))

    def deposit(self, ledger, t):
        if self.links.is_linked(t):
            # The lots moved when the withdrawal was processed
            return

        acquired = acquired_date(t)
        if acquired is not None and t.declared_value is not None:
            # Units from outside the records, with their basis declared in the record
            lot = self.add_lot(ledger, t)
            lot.cost = t.declared_value
            lot.acquired = acquired
            return

        self.add_lot(ledger, t)
        self.fail("Deposit of %s %s to %s [TID:%s] has no matching Withdrawal, add the "
                  "Withdrawal, or declare its basis with a Buy Value and an "
                  "acquired:<date> note" % ('{:0,f}'.format(t.quantity.normalize()),
                                           t.asset, t.wallet, t._format_tid()))

    def remove_without_disposal(self, ledger, t):
        # e.g. a transfer fee which isn't treated as a disposal, its basis stays with the asset
        removed, _ = self.take_lots(ledger, t)
        moved = self._moved_by_record.get(id(t.t_record))
        if moved:
            carry_basis(moved, removed)
        else:
            remaining = FifoSelector.order(ledger.lots_in(t.wallet, t.asset))
            carry_basis(remaining[:1], removed)

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

    def calculate_capital_gains(self, tax_year):
        cgains = CalculateCapitalGainsUS(tax_year, self.tax_rules)

        for te in sorted(self.tax_events.get(tax_year, [])):
            if isinstance(te, TaxEventCapitalGainsUS):
                cgains.tax_summary(te)
            elif isinstance(te, TaxEventNonSale):
                cgains.non_sale(te)

        self.tax_report[tax_year] = {'CapitalGains': cgains}
        if self.transition and self.transition.cutover.year == tax_year:
            self.tax_report[tax_year]['Transition'] = self.transition

    def calculate_holdings(self, value_asset):
        """Open lots by wallet, with the same per asset roll-up as the UK report."""
        super(TaxCalculatorUS, self).calculate_holdings(value_asset)

        prices = {}
        for h in self.holdings_report['holdings'].values():
            if h['value'] is not None and h['quantity']:
                prices[h['asset']] = h['value'] / h['quantity']

        today = datetime.now(config.TZ_LOCAL)
        wallets = {}
        for lot in sorted(self.ledger.open_lots(),
                          key=lambda lot: (lot.wallet, lot.asset, lot.acquired or today)):
            holding = wallets.setdefault(lot.wallet, {}).setdefault(lot.asset, {
                'quantity': Decimal(0), 'cost': Decimal(0), 'value': None, 'gain': None,
                'lots': []})
            holding['quantity'] += lot.quantity
            holding['cost'] += lot.cost + lot.fees
            holding['lots'].append({
                'lot_id': lot.lot_id,
                'quantity': lot.quantity,
                'cost': (lot.cost + lot.fees).quantize(PRECISION),
                'acquired': lot.acquired,
                'holding_period': holding_period(lot.acquired, today),
            })

        for assets in wallets.values():
            for asset, holding in assets.items():
                holding['cost'] = holding['cost'].quantize(PRECISION)
                if asset in prices:
                    holding['value'] = (prices[asset] * holding['quantity']).quantize(PRECISION)
                    holding['gain'] = holding['value'] - holding['cost']

        self.holdings_report['wallets'] = wallets


def merge_fragments(lots, s):
    """Combine fragments of the same lot taken together, so a disposal reports each lot once.

    Fragments are only combined if they're reported in the same Form 8949 box, a fragment
    which arrived by transfer isn't covered for broker basis reporting.
    """
    def box(lot):
        return form_8949_box(lot, s, holding_period(lot.acquired, s.timestamp))

    merged = []
    for lot in lots:
        same = [m for m in merged if m.lot_id == lot.lot_id and lot.lot_id is not None
                and box(m) == box(lot)]
        if same:
            same[0].quantity += lot.quantity
            same[0].cost += lot.cost
            same[0].fees += lot.fees
        else:
            merged.append(copy.copy(lot))
    return merged


def carry_basis(lots, removed):
    """Add the basis of removed units to lots, in proportion to their quantity."""
    cost = sum((lot.cost for lot in removed), Decimal(0))
    fees = sum((lot.fees for lot in removed), Decimal(0))
    total = sum((lot.quantity for lot in lots), Decimal(0))
    if not total:
        return

    for lot in lots:
        lot.cost += cost * lot.quantity / total
        lot.fees += fees * lot.quantity / total


class Transition(object):
    """Lots per wallet at the cutover, compared against the universal method's remaining lots."""

    def __init__(self, cutover, universal, traced):
        self.cutover = cutover
        self.lots = sorted((copy.copy(lot) for lot in traced.open_lots()),
                           key=lambda lot: (lot.wallet, lot.asset, lot.acquired, lot.seq))
        self.assets = {}
        self.failures = []

        for name, ledger in (('universal', universal), ('traced', traced)):
            for lot in ledger.open_lots():
                totals = self.assets.setdefault(lot.asset, {
                    'universal_quantity': Decimal(0), 'universal_cost': Decimal(0),
                    'traced_quantity': Decimal(0), 'traced_cost': Decimal(0)})
                totals[name + '_quantity'] += lot.quantity
                totals[name + '_cost'] += lot.cost + lot.fees

        for asset, totals in sorted(self.assets.items()):
            totals['basis_shift'] = totals['traced_cost'] - totals['universal_cost']
            if totals['traced_quantity'] != totals['universal_quantity']:
                self.failures.append(
                    "%s held at the cutover differs between the universal (%s) and traced (%s) "
                    "methods, check for unmatched transfers" % (
                        asset, '{:0,f}'.format(totals['universal_quantity'].normalize()),
                        '{:0,f}'.format(totals['traced_quantity'].normalize())))


SHORT_TERM = 'Short-term'
LONG_TERM = 'Long-term'


def holding_period(acquired, disposed):
    """Long-term if held more than one year (IRC 1222), the holding period starting the day
    after acquisition. Units with no known acquisition date are treated as short-term."""
    if acquired is None:
        return SHORT_TERM

    acquired = acquired.astimezone(config.TZ_LOCAL).date()
    disposed = disposed.astimezone(config.TZ_LOCAL).date()
    try:
        anniversary = acquired.replace(year=acquired.year + 1)
    except ValueError:
        # Acquired on Feb 29th
        anniversary = date(acquired.year + 1, 2, 28)
    return LONG_TERM if disposed > anniversary else SHORT_TERM


def wallet_info(wallet):
    return (config.wallets or {}).get(wallet) or {}


def form_8949_box(lot, s, term):
    """Form 8949 box: A-F before 2025, then the digital asset boxes G-L.

    G/J: reported on a 1099-DA with basis reported to the IRS (covered units)
    H/K: reported on a 1099-DA without basis (noncovered units)
    I/L: not reported on a 1099-DA
    """
    short = term == SHORT_TERM
    if s.timestamp.astimezone(config.TZ_LOCAL).year < 2025:
        # Digital assets were rarely reported on a 1099-B
        return 'C' if short else 'F'

    info = wallet_info(s.wallet)
    if not info.get('issues_1099da'):
        return 'I' if short else 'L'

    # Basis reporting is mandatory for units acquired in the account from 2026
    covered = (lot.acquired is not None and not lot.history
               and lot.acquired.astimezone(config.TZ_LOCAL).year >= 2026)
    if covered:
        return 'G' if short else 'J'
    return 'H' if short else 'K'


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
        self.holding_period = holding_period(lot.acquired, s.timestamp)
        self.form_8949_box = form_8949_box(lot, s, self.holding_period)
        # Form 8949 column (e): cost including acquisition and disposal fees
        self.basis = self.cost + self.fees


class CalculateCapitalGainsUS(CalculateCapitalGains):
    """Capital gains grouped as Form 8949 reports them: by term, then box, then wallet."""

    BOX_DESCRIPTIONS = {
        'C': 'Short-term transactions not reported to you on Form 1099-B',
        'F': 'Long-term transactions not reported to you on Form 1099-B',
        'G': 'Short-term digital asset transactions reported on Form 1099-DA '
             'with basis reported to the IRS',
        'H': 'Short-term digital asset transactions reported on Form 1099-DA '
             'with basis not reported to the IRS',
        'I': 'Short-term digital asset transactions not reported on Form 1099-DA',
        'J': 'Long-term digital asset transactions reported on Form 1099-DA '
             'with basis reported to the IRS',
        'K': 'Long-term digital asset transactions reported on Form 1099-DA '
             'with basis not reported to the IRS',
        'L': 'Long-term digital asset transactions not reported on Form 1099-DA',
    }

    def __init__(self, tax_year, tax_rules):
        super(CalculateCapitalGainsUS, self).__init__(tax_year, tax_rules)
        self.terms = {term: {'boxes': {}, 'totals': self._totals()}
                      for term in (SHORT_TERM, LONG_TERM)}
        self.non_sales = {}

    @staticmethod
    def _totals():
        return {'disposals': 0, 'proceeds': Decimal(0), 'basis': Decimal(0),
                'gain': Decimal(0)}

    @staticmethod
    def _add(totals, te):
        totals['disposals'] += 1
        totals['proceeds'] += te.proceeds
        totals['basis'] += te.basis
        totals['gain'] += te.gain

    def tax_summary(self, te):
        super(CalculateCapitalGainsUS, self).tax_summary(te)

        term = self.terms[te.holding_period]
        box = term['boxes'].setdefault(te.form_8949_box, {'wallets': {},
                                                          'totals': self._totals()})
        box['wallets'].setdefault(te.wallet, {}).setdefault(te.asset, []).append(te)
        self._add(box['totals'], te)
        self._add(term['totals'], te)

    def non_sale(self, te):
        self.non_sales.setdefault(te.type, []).append(te)


class TaxEventNonSale(TaxEvent):
    """Units given away, donated or lost: no gain or loss, but the basis is reported."""

    def __init__(self, lot, s):
        super(TaxEventNonSale, self).__init__(s.timestamp, s.asset)
        self.type = s.t_type
        self.quantity = s.quantity
        self.wallet = s.wallet
        self.note = s.note
        self.lot_id = lot.lot_id
        self.basis = (lot.cost + lot.fees).quantize(PRECISION)
        self.fair_market_value = (s.proceeds or Decimal(0)).quantize(PRECISION)
        self.acquisition_date = lot.acquired
        self.holding_period = holding_period(lot.acquired, s.timestamp)

    def deductible_amount(self):
        """Charitable deduction before AGI limits: FMV if long-term, else the lower of basis."""
        if self.holding_period == LONG_TERM:
            return self.fair_market_value
        return min(self.fair_market_value, self.basis)
