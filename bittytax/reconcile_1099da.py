# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026
"""Reconcile broker Form 1099-DA rows with the disposals in your records.

Notice 2026-20: the units identified in your own books and records are the ones sold, even
when the broker reported something else. The differences must be explainable, this lists
them with the likely reason.

Input CSV, one row per sale as the broker reported it (Cost Basis and Date Acquired may be
blank when the broker didn't report basis):

    Wallet, Asset, Date Sold, Quantity, Proceeds, Cost Basis, Date Acquired
"""

import csv
from decimal import Decimal, InvalidOperation

from colorama import Fore, Style

from .config import config
from .export_us import unique_filename
from .tax.transfers import parse_date
from .tax.us import TaxEventCapitalGainsUS

VALUE_TOLERANCE = Decimal('1.00')
QUANTITY_TOLERANCE = Decimal('0.00000001')


def _date(timestamp):
    return timestamp.astimezone(config.TZ_LOCAL).date()


class Reconcile1099DA(object):
    DEFAULT_FILENAME = 'BittyTax_1099DA_Reconciliation'
    HEADER = ['Status', 'Wallet', 'Asset', 'Date Sold', 'Quantity (1099-DA)',
              'Quantity (Records)', 'Proceeds (1099-DA)', 'Proceeds (Records)',
              'Cost Basis (1099-DA)', 'Cost Basis (Records)', 'Explanation']

    def __init__(self, filename, tax):
        self.broker = self._load(filename)
        self.records = self._aggregate_records(tax, {key[0] for key in self.broker},
                                               {key[2].year for key in self.broker})
        self.results = self._compare()

    @staticmethod
    def _decimal(value):
        value = (value or '').replace(',', '').replace('$', '').strip()
        return Decimal(value) if value else None

    def _load(self, filename):
        broker = {}
        with open(filename, newline='', encoding='utf-8') as csv_file:
            for row_num, row in enumerate(csv.DictReader(csv_file), start=2):
                try:
                    key = (row['Wallet'], row['Asset'], parse_date(row['Date Sold']).date())
                    quantity = self._decimal(row['Quantity'])
                    proceeds = self._decimal(row['Proceeds'])
                    basis = self._decimal(row.get('Cost Basis'))
                except (KeyError, ValueError, InvalidOperation) as e:
                    raise ValueError("%s row %d: %s" % (filename, row_num, e))

                totals = broker.setdefault(key, {'quantity': Decimal(0),
                                                 'proceeds': Decimal(0), 'basis': None})
                totals['quantity'] += quantity
                totals['proceeds'] += proceeds
                if basis is not None:
                    totals['basis'] = (totals['basis'] or Decimal(0)) + basis
        return broker

    @staticmethod
    def _aggregate_records(tax, wallets, years):
        records = {}
        for events in tax.tax_events.values():
            for te in events:
                if not isinstance(te, TaxEventCapitalGainsUS) or te.wallet not in wallets \
                        or _date(te.date).year not in years:
                    continue
                key = (te.wallet, te.asset, _date(te.date))
                totals = records.setdefault(key, {'quantity': Decimal(0),
                                                  'proceeds': Decimal(0),
                                                  'basis': Decimal(0), 'methods': set(),
                                                  'transferred': False})
                totals['quantity'] += te.quantity
                totals['proceeds'] += te.proceeds - te.sale_fees
                totals['basis'] += te.basis - te.sale_fees
                totals['methods'].add(te.disposal_type)
                totals['transferred'] |= te.transferred_in
        return records

    def _compare(self):
        results = []
        for key in sorted(set(self.broker) | set(self.records)):
            broker = self.broker.get(key)
            records = self.records.get(key)

            if records is None:
                status, why = 'Not in records', 'The broker reported a sale your records ' \
                    "don't have on this date, check for missing or misdated transactions"
            elif broker is None:
                status, why = 'Not on 1099-DA', 'No 1099-DA row for this sale, it may be ' \
                    'reported on a different date or not reported by the broker'
            elif abs(broker['quantity'] - records['quantity']) > QUANTITY_TOLERANCE:
                status, why = 'Quantity differs', 'Check for sales split or merged ' \
                    'differently, or fees paid in the asset'
            elif abs(broker['proceeds'] - records['proceeds']) > VALUE_TOLERANCE:
                status, why = 'Proceeds differ', 'Check the sale value and the fees ' \
                    'deducted from proceeds'
            elif broker['basis'] is not None and \
                    abs(broker['basis'] - records['basis']) > VALUE_TOLERANCE:
                status = 'Basis differs'
                why = self._basis_reason(records)
            else:
                status, why = 'Match', ''

            results.append((status, key, broker, records, why))
        return results

    @staticmethod
    def _basis_reason(records):
        identified = sorted(m for m in records['methods'] if m != 'First-In, First-Out')
        if identified:
            return 'Units identified in your records (%s), Notice 2026-20: your records ' \
                'control' % ', '.join(identified)
        if records['transferred']:
            return 'Units transferred in, the broker may not have their basis'
        return 'Lots taken FIFO within this wallet, check the broker has the same lots'

    def summary(self):
        counts = {}
        for status, _, _, _, _ in self.results:
            counts[status] = counts.get(status, 0) + 1
        return counts

    def write_csv(self, filename=None):
        filename = filename or unique_filename(self.DEFAULT_FILENAME)
        with open(filename, 'w', newline='', encoding='utf-8') as csv_file:
            writer = csv.writer(csv_file, lineterminator='\n')
            writer.writerow(self.HEADER)
            for status, (wallet, asset, sold), broker, records, why in self.results:
                def value(totals, name, fmt='{:0.2f}'):
                    if totals is None or totals[name] is None:
                        return ''
                    return fmt.format(totals[name])

                writer.writerow([status, wallet, asset, sold.strftime('%m/%d/%Y'),
                                 value(broker, 'quantity', '{:0f}'),
                                 value(records, 'quantity', '{:0f}'),
                                 value(broker, 'proceeds'), value(records, 'proceeds'),
                                 value(broker, 'basis'), value(records, 'basis'), why])

        counts = self.summary()
        print("%s1099-DA reconciliation: %s%s" % (
            Fore.WHITE, Style.BRIGHT,
            ', '.join('%s %d' % (status, counts[status]) for status in sorted(counts))))
        print("%s1099-DA reconciliation created: %s%s" % (Fore.WHITE, Fore.YELLOW, filename))
        return filename
