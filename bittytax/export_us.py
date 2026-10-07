# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026
"""CSV exports for the US tax rules."""

import csv
import os

from colorama import Fore

from .config import config


def unique_filename(name, extension='csv'):
    filepath = '%s.%s' % (name, extension)
    i = 2
    while os.path.exists(filepath):
        filepath = '%s-%s.%s' % (name, i, extension)
        i += 1
    return filepath


def irs_date(timestamp):
    if timestamp is None:
        return 'UNKNOWN'
    return timestamp.astimezone(config.TZ_LOCAL).strftime('%m/%d/%Y')


class ExportForm8949(object):
    """One row per lot disposed of, in the order Form 8949 lists them."""

    DEFAULT_FILENAME = 'BittyTax_Form8949'
    HEADER = ['Tax Year', 'Part', 'Box', 'Description (a)', 'Date Acquired (b)',
              'Date Sold (c)', 'Proceeds (d)', 'Cost Basis (e)', 'Adjustment Code (f)',
              'Adjustment Amount (g)', 'Gain or Loss (h)', 'Wallet', 'Asset', 'Quantity',
              'Lot ID', 'Method', 'Gift Basis Rule']

    def __init__(self, tax_report):
        self.tax_report = tax_report

    def rows(self):
        for tax_year in sorted(self.tax_report):
            cgains = self.tax_report[tax_year]['CapitalGains']
            for part, term in (('I', 'Short-term'), ('II', 'Long-term')):
                boxes = cgains.terms[term]['boxes']
                for box in sorted(boxes):
                    for wallet in sorted(boxes[box]['wallets']):
                        for asset in sorted(boxes[box]['wallets'][wallet]):
                            for te in boxes[box]['wallets'][wallet][asset]:
                                yield [tax_year, part, box,
                                       '{:0,.8f} {}'.format(te.quantity, te.asset),
                                       irs_date(te.acquisition_date),
                                       irs_date(te.date),
                                       '{:0.2f}'.format(te.proceeds),
                                       '{:0.2f}'.format(te.basis),
                                       '', '',
                                       '{:0.2f}'.format(te.gain),
                                       te.wallet, te.asset,
                                       '{:0f}'.format(te.quantity.normalize()),
                                       te.lot_id or '', te.disposal_type,
                                       te.gift_rule or '']

    def write_csv(self, filename=None):
        filename = filename or unique_filename(self.DEFAULT_FILENAME)
        with open(filename, 'w', newline='', encoding='utf-8') as csv_file:
            writer = csv.writer(csv_file, lineterminator='\n')
            writer.writerow(self.HEADER)
            for row in self.rows():
                writer.writerow(row)

        print("%sForm 8949 export created: %s%s" % (Fore.WHITE, Fore.YELLOW, filename))
        return filename
