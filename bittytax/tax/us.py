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
from .lot_selection import SpecificSelector, identification_key, standing_order_for
from .form_1099da import Reconciliation
from .transfers import (link_transfers, acquired_date, note_token, parse_date, tid_key,
                        GIFT_TOKENS)

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

    def __init__(self, transactions, tax_rules, us_records=None):
        super(TaxCalculatorUS, self).__init__(transactions, tax_rules)
        self.us_records = us_records
        self.ordered = []
        self.links = None
        self.ledger = None
        self.transition = None
        self.reconciliation = None
        self.selector = FifoSelector()
        self.integrity_failures = []
        self.warnings = []
        self.standing_orders = []
        self.identifications = {}
        self.lot_log = []
        self._phase = None
        self._record_events = True
        self._moved_by_record = {}
        self._replayed = {}
        self._transferred = set()
        self._broker_order_warnings = set()

    @staticmethod
    def get_cutover():
        value = config.us_wallet_cutover
        if isinstance(value, str):
            value = dateutil.parser.parse(value).date()
        return datetime(value.year, value.month, value.day, tzinfo=config.TZ_LOCAL)

    def calculate(self, skip_integrity_check=False):
        if self.us_records is not None:
            self.standing_orders = self.us_records.standing_orders
            self.identifications = self.us_records.identifications
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

        events = [te for events in self.tax_events.values() for te in events
                  if isinstance(te, TaxEventCapitalGainsUS)]
        self.reconciliation = Reconciliation(
            self.us_records.forms_1099da if self.us_records else [], events)
        for failure in self.reconciliation.failures:
            self.fail(failure)
        for warning in self.reconciliation.warnings:
            self.warn(warning)

        for idents in self.identifications.values():
            for ident in idents:
                if not ident.used:
                    self.warn("Identification on %s doesn't match a disposal or transfer of "
                              "%s from %s at %s" % (ident.source, ident.asset, ident.wallet,
                                                    ident.timestamp.isoformat()))

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
        # This pass's copies, a deposit recorded before its withdrawal moves the lots early
        self._replayed = {tid_key(t): t for t in transactions}
        self._transferred = set()

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
        if t.t_type == Buy.TYPE_GIFT_RECEIVED:
            self.gift_basis(lot, t)
        elif t.t_type == Buy.TYPE_DEPOSIT and acquired_date(t) is not None \
                and t.declared_value is not None:
            # Units from outside the records, with their basis declared in the record
            lot.cost = t.declared_value
            lot.acquired = acquired_date(t)
        ledger.add(lot)
        self.log('Acquire', t, lot)
        if config.debug:
            print("%slots:   add %s" % (Fore.YELLOW, lot))
        return lot

    def log(self, action, t, lot, to_wallet=None):
        """Lot activity from the cutover: the per-wallet books and records."""
        if self._phase == 'per-wallet' and self._record_events:
            self.lot_log.append({
                'timestamp': t.timestamp, 'action': action, 'wallet': t.wallet,
                'to_wallet': to_wallet, 'asset': lot.asset, 'quantity': lot.quantity,
                'cost': lot.cost + lot.fees, 'acquired': lot.acquired, 'lot_id': lot.lot_id,
                'method': lot.method, 'tid': t._format_tid()})

    def gift_basis(self, lot, t):
        """Carryover basis for a gift received (IRC 1015, 1223(2)).

        Donor's basis, adjusted for gift tax paid on the appreciation (IRC 1015(d)(6)), and
        the donor's holding period. If FMV at the gift was below that basis, a loss is
        measured from FMV with the holding period starting at the gift.
        """
        tokens = {name: note_token(pattern, t.note) for name, pattern in GIFT_TOKENS.items()}
        if tokens['donor_basis'] is None or tokens['donor_acquired'] is None:
            self.fail("Gift-Received of %s %s [TID:%s] needs donor_basis:<value> and "
                      "donor_acquired:<date> in its note, using its fair market value as "
                      "basis" % ('{:0,f}'.format(t.quantity.normalize()), t.asset,
                                 t._format_tid()))
            return

        fmv = lot.cost
        basis = Decimal(tokens['donor_basis'])
        if tokens['gift_tax_paid'] is not None and fmv > basis and fmv:
            basis += Decimal(tokens['gift_tax_paid']) * (fmv - basis) / fmv

        lot.cost = basis
        lot.acquired = parse_date(tokens['donor_acquired'])
        if fmv < basis:
            lot.loss_basis = fmv
            lot.gift_date = t.timestamp

    def take_lots(self, ledger, t, quantity=None):
        """Lots for a disposal or transfer: identified units first, then the standing order
        in force, otherwise FIFO. Before the cutover everything is FIFO."""
        quantity = t.quantity if quantity is None else quantity
        if self._phase != 'per-wallet':
            return self.remove_lots(ledger, t, quantity, self.selector)

        taken = []
        for ident in self.identifications.get(
                identification_key(t.wallet, t.asset, t.timestamp), []):
            if ident.used or quantity <= 0:
                continue
            ident.used = True

            if ident.recorded_at > t.timestamp:
                self.fail("Identification of lot %s for [TID:%s] was recorded after the "
                          "transaction, it can't be used" % (ident.lot_id, t._format_tid()))
                continue

            selector = SpecificSelector(ident.lot_id)
            try:
                lots = ledger.remove(t.wallet, t.asset, min(ident.quantity, quantity),
                                     selector, t)
            except Shortfall as e:
                self.fail("Lot %s identified for [TID:%s] holds only %s %s in %s" % (
                    ident.lot_id, t._format_tid(), '{:0,f}'.format(e.available.normalize()),
                    t.asset, t.wallet))
                lots = e.taken

            for lot in lots:
                lot.method = selector.name
                quantity -= lot.quantity
            taken.extend(lots)

        order = standing_order_for(self.standing_orders, t.wallet, t.timestamp)
        if order is not None:
            self.check_broker_standing_order(order, t)

        lots, unmatched = self.remove_lots(ledger, t, quantity, order or self.selector)
        return taken + lots, unmatched

    def check_broker_standing_order(self, order, t):
        # Notice 2026-20 lets broker customers identify units in their own records to the
        #  end of 2026, after that the broker must be told
        if (t.timestamp.year >= 2027 and t.wallet not in self._broker_order_warnings
                and wallet_info(t.wallet).get('custody') == 'broker'):
            self._broker_order_warnings.add(t.wallet)
            self.warn("Standing order for %s is applied after 2026, broker-held units must "
                      "be identified to the broker unless the relief is extended" % t.wallet)

    def remove_lots(self, ledger, t, quantity, selector):
        if quantity <= 0:
            return [], Decimal(0)
        try:
            lots = ledger.remove(t.wallet, t.asset, quantity, selector, t)
            for lot in lots:
                lot.method = selector.name
            return lots, Decimal(0)
        except Shortfall as e:
            for lot in e.taken:
                lot.method = selector.name
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

        sale_id = t._format_tid()
        for lot, s in self.split_sell(t, lots):
            self.log('Dispose', t, lot)
            tax_event = TaxEventCapitalGainsUS(lot.method or self.selector.name, lot, s)
            tax_event.sale_id = sale_id
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
            self.log(t.t_type, t, lot)
            tax_event = TaxEventNonSale(lot, s)
            self.tax_events[self.which_tax_year(tax_event.date)].append(tax_event)
            if config.debug:
                print("%slots:   %s <- %s" % (Fore.CYAN, tax_event, lot))

    def withdraw(self, ledger, t):
        if tid_key(t) in self._transferred:
            # Already moved, its deposit was recorded first
            return
        self._transferred.add(tid_key(t))

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

        for lot in moved:
            self.log('Transfer', t, lot, deposit.wallet)
            lot.method = None
        ledger.move(moved, deposit.wallet, t.timestamp)
        self._moved_by_record[id(t.t_record)] = moved
        if config.debug:
            for lot in moved:
                print("%slots:   move %s" % (Fore.YELLOW, lot))

    def deposit(self, ledger, t):
        if self.links.is_linked(t):
            withdrawal = self._replayed.get(tid_key(self.links.withdrawal(t) or t))
            if withdrawal is not None and withdrawal is not t \
                    and ledger.balance(withdrawal.wallet, withdrawal.asset) >= withdrawal.quantity:
                # Recorded before its withdrawal: move the lots now if the sending wallet
                #  already holds them, otherwise when the withdrawal is processed
                self.withdraw(ledger, withdrawal)
            return

        self.add_lot(ledger, t)
        if acquired_date(t) is not None and t.declared_value is not None:
            return

        self.fail("Deposit of %s %s to %s [TID:%s] has no matching Withdrawal, add the "
                  "Withdrawal, or declare its basis with a Buy Value and an "
                  "acquired:<date> note" % ('{:0,f}'.format(t.quantity.normalize()),
                                           t.asset, t.wallet, t._format_tid()))

    def remove_without_disposal(self, ledger, t):
        # e.g. a transfer fee which isn't treated as a disposal, its basis stays with the asset
        removed, _ = self.take_lots(ledger, t)
        for lot in removed:
            self.log('Remove', t, lot)
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

        lines = [line for line in self.reconciliation.lines if line.tax_year == tax_year] \
            if self.reconciliation else []
        if lines:
            self.tax_report[tax_year]['Reconciliation'] = lines

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
        return form_8949_box(s, holding_period(lot.acquired, s.timestamp))

    merged = []
    for lot in lots:
        same = [m for m in merged if m.lot_id == lot.lot_id and lot.lot_id is not None
                and box(m) == box(lot)]
        if same:
            same[0].quantity += lot.quantity
            same[0].cost += lot.cost
            same[0].fees += lot.fees
            if lot.loss_basis is not None:
                same[0].loss_basis += lot.loss_basis
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


def form_8949_box(s, term):
    """Form 8949 box before any 1099-DA is applied: C/F before 2025, I/L after.

    A matching row on a '1099-DA' sheet moves the sale to G/J (basis reported to the IRS)
    or H/K (basis not reported), see form_1099da.
    """
    short = term == SHORT_TERM
    if s.timestamp.astimezone(config.TZ_LOCAL).year < 2025:
        # Digital assets were rarely reported on a 1099-B
        return 'C' if short else 'F'
    return 'I' if short else 'L'


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
        # Selling costs, a 1099-DA reports proceeds net of these
        self.sale_fees = (s.fee_value or Decimal(0)).quantize(PRECISION)
        self.transferred_in = bool(lot.history)
        self.proceeds = s.proceeds.quantize(PRECISION)
        self.gain = self.proceeds - self.cost - self.fees
        self.acquisition_date = lot.acquired
        self.gift_rule = None

        if lot.loss_basis is not None:
            self.dual_basis(lot)

        self.holding_period = holding_period(self.acquisition_date, s.timestamp)
        self.form_8949_box = form_8949_box(s, self.holding_period)
        # Cost including acquisition and disposal fees
        self.basis = self.cost + self.fees
        self.sale_id = None

        # Form 8949 columns (d) to (g), replaced by the broker's figures if a 1099-DA
        #  reported this sale. Column (h) is self.gain
        self.form_proceeds = self.proceeds
        self.form_basis = self.basis
        self.adjustment_code = ''
        self.adjustment = Decimal(0)

    def dual_basis(self, lot):
        """Gift received with FMV below the donor's basis (Treas. Reg. 1.1015-1(a))."""
        loss_basis = lot.loss_basis.quantize(PRECISION)
        if self.gain > 0:
            # Gain against the donor's basis, holding period tacks
            self.gift_rule = 'gain'
        elif self.proceeds - loss_basis - self.fees < 0:
            # Loss against FMV at the gift, holding period starts at the gift
            self.gift_rule = 'loss'
            self.cost = loss_basis
            self.gain = self.proceeds - self.cost - self.fees
            self.acquisition_date = lot.gift_date
        else:
            # Sold between the two bases: no gain or loss
            self.gift_rule = 'no gain or loss'
            self.cost = self.proceeds - self.fees
            self.gain = Decimal(0)


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
        self.form_totals = self._totals()
        self.non_sales = {}

    @staticmethod
    def _totals():
        return {'disposals': 0, 'proceeds': Decimal(0), 'basis': Decimal(0),
                'adjustment': Decimal(0), 'gain': Decimal(0)}

    @staticmethod
    def _add(totals, te):
        totals['disposals'] += 1
        totals['proceeds'] += te.form_proceeds
        totals['basis'] += te.form_basis
        totals['adjustment'] += te.adjustment
        totals['gain'] += te.gain

    def tax_summary(self, te):
        super(CalculateCapitalGainsUS, self).tax_summary(te)

        term = self.terms[te.holding_period]
        box = term['boxes'].setdefault(te.form_8949_box, {'wallets': {},
                                                          'totals': self._totals()})
        box['wallets'].setdefault(te.wallet, {}).setdefault(te.asset, []).append(te)
        self._add(box['totals'], te)
        self._add(term['totals'], te)
        self._add(self.form_totals, te)

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
