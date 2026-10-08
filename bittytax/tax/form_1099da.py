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
    return config.to_local(timestamp).date()


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

        days = {}
        for form in unmatched_forms:
            days.setdefault((form.wallet, form.asset, form.date_sold), []).append(form)

        # Matching is read-only. Resolve dates, then construct complete row
        # attribution plans, and only then apply broker figures to tax events.
        resolved = self._resolve_dates(days, sales)
        rejected = {}
        for key, day_sales in resolved.items():
            plan, reason = self._attribution_plan(days[key], day_sales)
            if plan is None:
                rejected[key] = reason
                continue
            for row_forms, row_sales in plan:
                self._apply(row_forms, row_sales)
            for sale in day_sales:
                unmatched_sales.remove(sale)

        for (wallet, asset, date), day_forms in sorted(days.items()):
            key = (wallet, asset, date)
            if key in resolved and key not in rejected:
                continue
            quantity = sum((f.quantity for f in day_forms), Decimal(0))
            line = ReconciliationLine(
                NOT_IN_RECORDS, wallet, asset, date, day_forms, [],
                note=rejected.get(key, "The broker reported a sale which isn't uniquely "
                                  "matched in your records, check for missing, misdated "
                                  "or ambiguous transactions"))
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
            if config.to_local(te.date).year >= 2025:
                by_sale.setdefault(te.sale_id, []).append(te)
        return sorted((Sale(group) for group in by_sale.values()),
                      key=lambda s: (s.timestamp, s.events[0].sale_id))

    @classmethod
    def _resolve_dates(cls, days, sales):
        """Reserve unique, non-overlapping day groups without changing any tax event.

        Both local and UTC dates are evidence, not a preference order. A forced
        group can eliminate an alternative on another date; conflicting forced
        claims and unresolved alternatives remain unmatched.
        """
        pending, available, resolved = dict(days), list(sales), {}
        while pending:
            choices = {key: cls._day_candidates(key, forms, available)
                       for key, forms in pending.items()}
            unique = {key: groups[0] for key, groups in choices.items() if len(groups) == 1}
            ready = [key for key, group in unique.items()
                     if not any(set(group) & set(other) for other_key, other in unique.items()
                                if other_key != key)]
            if not ready:
                break
            for key in sorted(ready):
                resolved[key] = unique[key]
                pending.pop(key)
                for sale in unique[key]:
                    available.remove(sale)
        return resolved

    @staticmethod
    def _row_matches(forms, sales):
        """Quantity edges use the tolerance independently for each broker row."""
        return {form: [sale for sale in sales
                       if abs(sale.quantity - form.quantity) <= QUANTITY_TOLERANCE]
                for form in forms}

    @classmethod
    def _individual_plan(cls, forms, sales):
        """Return a unique row-to-sale assignment, or None; extra sales are allowed.

        A row with one available sale forces that edge. Remove forced edges and
        repeat. Two rows forcing the same sale are contradictory; a residual
        graph with multiple choices per row cannot establish unique attribution.
        No sums are checked here: each edge has already passed its own tolerance.
        """
        remaining_forms, remaining_sales, plan = list(forms), list(sales), []
        while remaining_forms:
            matches = cls._row_matches(remaining_forms, remaining_sales)
            forced = [(form, group[0]) for form, group in matches.items() if len(group) == 1]
            if any(not group for group in matches.values()) or not forced:
                return None
            if len({sale for _, sale in forced}) != len(forced):
                return None
            for form, sale in forced:
                plan.append(([form], [sale]))
                remaining_forms.remove(form)
                remaining_sales.remove(sale)
        return plan

    @classmethod
    def _day_candidates(cls, key, forms, sales):
        wallet, asset, date = key
        quantity = sum((f.quantity for f in forms), Decimal(0))
        eligible = [s for s in sales if s.wallet == wallet and s.asset == asset
                    and date in s.dates()]
        groups = [[s for s in eligible if date_of(s.timestamp) == date]
                  for date_of in (_local_date, _utc_date)]
        if len(forms) == 1:
            groups.extend([s] for s in eligible)
        candidates = []
        for group in groups:
            if group and abs(quantity - sum((s.quantity for s in group), Decimal(0))) \
                    <= QUANTITY_TOLERANCE and group not in candidates:
                candidates.append(group)

        # Partial-day rows are independent measurements, not a single daily
        # aggregate. Do not reject valid edges because their rounding adds up.
        individual = cls._individual_plan(forms, eligible)
        if individual is not None:
            identified = {sale for _, group in individual for sale in group}
            group = [s for s in eligible if s in identified]
            if group and group not in candidates:
                candidates.append(group)
        return candidates

    @classmethod
    def _attribution_plan(cls, forms, sales):
        """Validate the entire day before allocating proceeds or reported basis.

        Preserve forced individual rows. A split/combined remainder is allowed
        only when no individual quantity edges remain and every broker row has
        the same basis-reporting category. Never turn ambiguous individual rows
        into an aggregate merely because the daily quantity happens to balance.
        """
        if any(f.basis_reported and f.cost_basis is None for f in forms):
            return None, 'Broker basis was reported to the IRS but the basis value is missing'
        if len({f.form_year for f in forms}) != 1:
            return None, 'Broker rows on the same date have conflicting form years'
        remaining_forms, remaining_sales, plan = list(forms), list(sales), []
        while remaining_forms:
            matches = cls._row_matches(remaining_forms, remaining_sales)
            forced = [(form, group[0]) for form, group in matches.items() if len(group) == 1]
            if forced:
                if len({sale for _, sale in forced}) != len(forced):
                    return None, 'Ambiguous broker rows claim the same disposal transaction'
                for form, sale in forced:
                    plan.append(([form], [sale]))
                    remaining_forms.remove(form)
                    remaining_sales.remove(sale)
                continue
            if any(matches.values()):
                return None, 'Ambiguous individual broker rows cannot be attributed to sales'
            if len({f.basis_reported for f in remaining_forms}) != 1:
                return None, 'Ambiguous aggregate has mixed basis-reporting categories'
            quantity = sum((f.quantity for f in remaining_forms), Decimal(0))
            if not remaining_sales or abs(quantity - sum(
                    (s.quantity for s in remaining_sales), Decimal(0))) > QUANTITY_TOLERANCE:
                return None, 'Remaining broker aggregate quantity does not match the records'
            plan.append((remaining_forms, remaining_sales))
            remaining_sales = []
            break
        if remaining_sales:
            return None, 'Broker rows do not account for the complete resolved sale group'
        return plan, ''

    def _apply(self, forms, sales):
        events = [te for sale in sales for te in sale.events]
        weights = [te.quantity for te in events]
        basis_reported = all(f.basis_reported and f.cost_basis is not None for f in forms)

        # Combined broker rows do not imply a common execution price. Allocate
        # their net proceeds by actual net proceeds, not by token quantity.
        proceeds_weights = [te.proceeds - te.sale_fees for te in events]
        broker_proceeds = sum((f.proceeds for f in forms), Decimal(0))
        gross_proceeds = sum((te.proceeds for te in events), Decimal(0))
        net_proceeds = sum(proceeds_weights, Decimal(0))
        # Exact figures distinguish small fees from rounding; net wins when
        # there are no fees and gross and net are identical.
        if broker_proceeds == net_proceeds:
            gross_reporting = False
        elif broker_proceeds == gross_proceeds:
            gross_reporting = True
        else:
            gross_reporting = (abs(broker_proceeds - gross_proceeds) <= ROUNDING_TOLERANCE
                               and abs(broker_proceeds - net_proceeds) > ROUNDING_TOLERANCE)
        if gross_reporting:
            # A gross-reporting broker requires code E for each event's own fee.
            proceeds_weights = [te.proceeds for te in events]
        proceeds = allocate(broker_proceeds, proceeds_weights)
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
            if gross_reporting and te.sale_fees:
                # Decide gross vs net from the whole matched group, not each
                # tiny lot fee (which can itself be below the rounding tolerance).
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
