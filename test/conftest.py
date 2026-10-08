# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

import copy
import os

import pytest

from bittytax.config import config
from bittytax.import_records import ImportRecords
from bittytax.transactions import TransactionHistory
from bittytax.tax import TaxCalculatorUK, TaxCalculatorUS, TaxEventCapitalGains
from bittytax.us_records import UsRecords

FIXTURES = os.path.join(os.path.dirname(__file__), 'us', 'fixtures')


class NoPriceLookup(object):
    """Value asset stub: fixtures must carry explicit values, nothing is fetched."""
    price_report = {}

    @staticmethod
    def get_value(asset, timestamp, quantity):
        if asset == config.ccy:
            return quantity, True
        raise AssertionError("unexpected price lookup: %s %s %s" % (asset, timestamp, quantity))


@pytest.fixture(autouse=True)
def isolated_config():
    saved = (copy.deepcopy(config.config), config.ccy, config.asset_priority,
             config.start_of_year_month, config.start_of_year_day, config.TZ_LOCAL,
             config.tax_rules, config.tz_switch, config.TZ_BEFORE)
    yield config
    (config.config, config.ccy, config.asset_priority,
     config.start_of_year_month, config.start_of_year_day, config.TZ_LOCAL,
     config.tax_rules, config.tz_switch, config.TZ_BEFORE) = saved


def use_us_rules(**overrides):
    config.config['local_currency'] = 'USD'
    config.ccy = 'USD'
    config.config.update(overrides)
    config.set_tax_rules(config.TAX_RULES_US_INDIVIDUAL)


def use_uk_rules(**overrides):
    config.config['local_currency'] = 'GBP'
    config.ccy = 'GBP'
    config.config.update(overrides)
    config.set_tax_rules(config.TAX_RULES_UK_INDIVIDUAL)


HEADER = ('Type,Buy Quantity,Buy Asset,Buy Value,Sell Quantity,Sell Asset,Sell Value,'
          'Fee Quantity,Fee Asset,Fee Value,Wallet,Timestamp,Note')


def import_records(path, expect_failures=0):
    """Transaction records and US records from a CSV file or a workbook."""
    importer = ImportRecords()
    if path.endswith('.xlsx'):
        importer.import_excel(path)
    else:
        with open(path, newline='', encoding='utf-8') as csv_file:
            importer.import_csv(csv_file)
    assert importer.failure_cnt == expect_failures
    return importer.get_records(), importer.us_records


def import_csv(path):
    return import_records(path)[0]


def write_workbook(tmp_path, sheets, name='records.xlsx'):
    """Workbook from {sheet name: rows}, rows are lists of cells or comma separated strings.
    A sheet of transaction rows gets the standard header added."""
    import xlsxwriter

    path = str(tmp_path / name)
    workbook = xlsxwriter.Workbook(path)
    for sheet_name, rows in sheets.items():
        worksheet = workbook.add_worksheet(sheet_name)
        rows = [r.split(',') if isinstance(r, str) else r for r in rows]
        if rows and rows[0] and rows[0][0] in ('Trade', 'Deposit', 'Withdrawal'):
            rows = [HEADER.split(',')] + rows
        for row_num, row in enumerate(rows):
            for col_num, cell in enumerate(row):
                if cell not in ('', None):
                    worksheet.write(row_num, col_num, cell)
    workbook.close()
    return path


def write_csv(tmp_path, rows, name='records.csv'):
    path = tmp_path / name
    path.write_text('\n'.join([HEADER] + list(rows)) + '\n', encoding='utf-8')
    return str(path)


def us_records(**sheets):
    """US records from sheet rows, e.g. us_records(**{'Standing Orders': [header, row]})."""
    records = UsRecords()
    for name, rows in sheets.items():
        errors = records.parse_sheet(UsRecords.sheet_kind(name), name, rows)
        assert not errors, errors
    return records


def run_tax(path, tax_rules, skip_integrity_check=True, records=None):
    transaction_records, sheet_records = import_records(path)
    history = TransactionHistory(transaction_records, NoPriceLookup())

    if tax_rules == config.TAX_RULES_US_INDIVIDUAL:
        tax = TaxCalculatorUS(history.transactions, tax_rules, records or sheet_records)
    else:
        tax = TaxCalculatorUK(history.transactions, tax_rules)

    tax.calculate(skip_integrity_check)
    return tax


def gains_rows(tax, before_year=None):
    rows = []
    for year in sorted(tax.tax_events):
        if before_year and year >= before_year:
            continue
        for te in tax.tax_events[year]:
            if isinstance(te, TaxEventCapitalGains):
                rows.append([
                    year,
                    te.date.date().isoformat(),
                    te.asset,
                    te.wallet,
                    str(te.quantity.normalize()),
                    str(te.cost),
                    str(te.fees),
                    str(te.proceeds),
                    str(te.gain),
                    te.acquisition_date.date().isoformat() if te.acquisition_date else None,
                ])
    return sorted(rows, key=lambda r: [str(v) for v in r])
