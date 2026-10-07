# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026
"""Apply a broker's Form 1099-DA rows to the Form 8949 lines for the same sales.

Form 8949 instructions: column (d) takes the proceeds shown on the 1099-DA. If the 1099-DA
shows basis was reported to the IRS, column (e) takes that basis even when it's wrong, and
the correction goes in column (g) with code B. Your records control which units were sold
(Notice 2026-20), so column (h) always equals the gain from your records:

    (g) = gain from your records - ((d) - (e))

Adjustment codes: B basis reported to the IRS is incorrect, E selling costs not reflected in
the reported proceeds, O any other difference (also flagged for review).
"""

from decimal import Decimal

from ..config import config

PRECISION = Decimal('0.01')
QUANTITY_TOLERANCE = Decimal('0.00000001')
# Differences up to this per sale are rounding, the broker's figures are accepted
ROUNDING_TOLERANCE = Decimal('1.00')

MATCH = 'Match'
ADJUSTED = 'Adjusted'
REVIEW = 'Review'
NOT_IN_RECORDS = 'Not in records'
NOT_ON_1099DA = 'Not on 1099-DA'


def _local_date(timestamp):
    return timestamp.astimezone(config.TZ_LOCAL).date()


def _utc_date(timestamp):
    return timestamp.astimezone(config.TZ_UTC).date()


def allocate(total, weights):
    """Split a total in proportion to weights, rounded to cents, the last takes the rest."""
    whole = sum(weights, Decimal(0))
    total = total.quantize(PRECISION)
    parts = []
    for weight in weights[:-1]:
        parts.append((total * weight / whole).quantize(PRECISION) if whole else Decimal(0))
    parts.append(total - sum(parts, Decimal(0)))
    return parts


class Sale(object):
    """The Form 8949 lines (one per lot) for one disposal transaction."""

    def __init__(self, events):
        self.events = events
        first = events[0]
        self.wallet = first.wallet
        self.asset = first.asset
        self.timestamp = first.date
        self.quantity = sum((te.quantity for te in events), Decimal(0))
        self.proceeds = sum((te.proceeds - te.sale_fees for te in events), Decimal(0))
        self.basis = sum((te.basis - te.sale_fees for te in events), Decimal(0))
        self.methods = {te.disposal_type for te in events}
        self.transferred_in = any(te.transferred_in for te in events)
        self.terms = {te.holding_period for te in events}

    def dates(self):
        return {_local_date(self.timestamp), _utc_date(self.timestamp)}


class ReconciliationLine(object):
    def __init__(self, status, wallet, asset, date_sold, forms, sales, codes='', note=''):
        self.status = status
        self.wallet = wallet
        self.asset = asset
        self.date_sold = date_sold
        self.forms = forms
        self.sales = sales
        self.codes = codes
        self.note = note
        self.tax_year = date_sold.year

    def broker(self, name):
        if not self.forms:
            return None
        values = [getattr(f, name) for f in self.forms]
        if any(v is None for v in values):
            return None
        return sum(values, Decimal(0))

    def records(self, name):
        if not self.sales:
            return None
        return sum((getattr(s, name) for s in self.sales), Decimal(0))


class Reconciliation(object):
    def __init__(self, forms, events):
        self.lines = []
        self.failures = []
        self.warnings = []

        covered = {(f.wallet, f.form_year) for f in forms}
        sales = self._sales(events)
        unmatched_sales = list(sales)
        unmatched_forms = sorted(forms, key=lambda f: (f.wallet, f.asset, f.date_sold))

        # Pass 1: a 1099-DA row for each sale
        for form in list(unmatched_forms):
            candidates = [s for s in unmatched_sales
                          if s.wallet == form.wallet and s.asset == form.asset
                          and form.date_sold in s.dates()
                          and abs(s.quantity - form.quantity) <= QUANTITY_TOLERANCE]
            if candidates:
                sale = min(candidates, key=lambda s: s.timestamp)
                self._apply([form], [sale])
                unmatched_forms.remove(form)
                unmatched_sales.remove(sale)

        # Pass 2: the broker split or combined sales differently on the same day
        days = {}
        for form in unmatched_forms:
            days.setdefault((form.wallet, form.asset, form.date_sold), [[], []])[0].append(form)
        for sale in unmatched_sales:
            for date in sorted(sale.dates()):
                if (sale.wallet, sale.asset, date) in days:
                    days[(sale.wallet, sale.asset, date)][1].append(sale)
                    break

        for (wallet, asset, date), (day_forms, day_sales) in sorted(days.items()):
            quantity = sum((f.quantity for f in day_forms), Decimal(0))
            if day_sales and abs(quantity - sum((s.quantity for s in day_sales),
                                                Decimal(0))) <= QUANTITY_TOLERANCE:
                self._apply(day_forms, day_sales)
                for sale in day_sales:
                    unmatched_sales.remove(sale)
            else:
                line = ReconciliationLine(
                    NOT_IN_RECORDS, wallet, asset, date, day_forms, [],
                    note="The broker reported a sale which isn't in your records, check for "
                         "missing or misdated transactions")
                self.lines.append(line)
                self.failures.append("%s: 1099-DA sale of %s %s from %s on %s isn't in your "
                                     "records" % (day_forms[0].source, quantity, asset, wallet,
                                                  date.isoformat()))

        for sale in unmatched_sales:
            if (sale.wallet, _local_date(sale.timestamp).year) in covered:
                self.lines.append(ReconciliationLine(
                    NOT_ON_1099DA, sale.wallet, sale.asset, _local_date(sale.timestamp), [],
                    [sale], note='Reported in box I/L, check whether the broker reported it '
                                 'on a different date'))

        for wallet, info in sorted((config.wallets or {}).items()):
            if (info or {}).get('issues_1099da'):
                years = {_local_date(s.timestamp).year for s in sales if s.wallet == wallet}
                for year in sorted(years - {y for w, y in covered if w == wallet}):
                    self.warnings.append("%s issues a 1099-DA but there are no '1099-DA' rows "
                                         "for %d, its sales are reported in box I/L" % (
                                             wallet, year))

        self.lines.sort(key=lambda l: (l.date_sold, l.wallet, l.asset))

    @staticmethod
    def _sales(events):
        by_sale = {}
        for te in events:
            if te.date.astimezone(config.TZ_LOCAL).year >= 2025:
                by_sale.setdefault(te.sale_id, []).append(te)
        return sorted((Sale(group) for group in by_sale.values()),
                      key=lambda s: (s.timestamp, s.events[0].sale_id))

    def _apply(self, forms, sales):
        events = [te for sale in sales for te in sale.events]
        weights = [te.quantity for te in events]
        basis_reported = all(f.basis_reported and f.cost_basis is not None for f in forms)

        proceeds = allocate(sum((f.proceeds for f in forms), Decimal(0)), weights)
        if basis_reported:
            bases = allocate(sum((f.cost_basis for f in forms), Decimal(0)), weights)

        codes = set()
        unexplained = Decimal(0)
        for i, te in enumerate(events):
            our_proceeds = te.proceeds - te.sale_fees
            our_basis = te.basis - te.sale_fees
            true_gain = te.gain

            te.form_proceeds = proceeds[i]
            te.form_basis = bases[i] if basis_reported else our_basis
            te.form_8949_box = ('G' if te.holding_period == 'Short-term' else 'J') \
                if basis_reported else ('H' if te.holding_period == 'Short-term' else 'K')

            event_codes = set()
            if basis_reported and te.form_basis != our_basis:
                event_codes.add('B')

            proceeds_diff = our_proceeds - te.form_proceeds
            if te.sale_fees and abs(proceeds_diff + te.sale_fees) <= ROUNDING_TOLERANCE:
                # The broker reported proceeds before selling costs
                event_codes.add('E')
                proceeds_diff += te.sale_fees
            if abs(proceeds_diff) > ROUNDING_TOLERANCE:
                event_codes.add('O')
                unexplained += proceeds_diff

            adjustment = true_gain - (te.form_proceeds - te.form_basis)
            if not event_codes:
                # Rounding only: accept the broker's figures
                adjustment = Decimal(0)

            te.adjustment = adjustment
            te.adjustment_code = ''.join(sorted(event_codes))
            te.gain = te.form_proceeds - te.form_basis + te.adjustment
            codes |= event_codes

        status, note = MATCH, ''
        if 'O' in codes:
            status = REVIEW
            note = 'Proceeds differ from your records by %s, check the sale value' % (
                '{:0,.2f}'.format(unexplained))
            self.warnings.append("%s: proceeds for %s %s on %s differ from your records" % (
                forms[0].source, forms[0].asset, forms[0].wallet,
                forms[0].date_sold.isoformat()))
        elif codes:
            status = ADJUSTED
            note = self._reason(codes, sales)

        terms = {f.term for f in forms if f.term} - {t for s in sales for t in s.terms}
        if terms:
            note = (note + '. ' if note else '') + 'The broker reported a different term, ' \
                'your records control (holding period tacks across transfers)'

        self.lines.append(ReconciliationLine(status, forms[0].wallet, forms[0].asset,
                                             forms[0].date_sold, forms, sales,
                                             ''.join(sorted(codes)), note))

    @staticmethod
    def _reason(codes, sales):
        reasons = []
        if 'B' in codes:
            methods = sorted({m for s in sales for m in s.methods}
                             - {'First-In, First-Out'})
            if methods:
                reasons.append('Units identified in your records (%s), Notice 2026-20: your '
                               'records control' % ', '.join(methods))
            elif any(s.transferred_in for s in sales):
                reasons.append('Units transferred in, the broker may not have their basis')
            else:
                reasons.append('Broker basis differs from the per-wallet FIFO lots')
        if 'E' in codes:
            reasons.append('Selling costs not deducted from the reported proceeds')
        return '. '.join(reasons)
