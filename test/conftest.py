# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

import copy
import os

import pytest

from bittytax.config import config
from bittytax.import_records import ImportRecords
from bittytax.transactions import TransactionHistory
from bittytax.tax import TaxCalculatorUK, TaxCalculatorUS, TaxEventCapitalGains

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
             config.start_of_year_month, config.start_of_year_day, config.TZ_LOCAL)
    yield config
    (config.config, config.ccy, config.asset_priority,
     config.start_of_year_month, config.start_of_year_day, config.TZ_LOCAL) = saved


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


def import_csv(path):
    import_records = ImportRecords()
    with open(path, newline='', encoding='utf-8') as csv_file:
        import_records.import_csv(csv_file)
    assert import_records.failure_cnt == 0
    return import_records.get_records()


def write_csv(tmp_path, rows, name='records.csv'):
    path = tmp_path / name
    header = ('Type,Buy Quantity,Buy Asset,Buy Value,Sell Quantity,Sell Asset,Sell Value,'
              'Fee Quantity,Fee Asset,Fee Value,Wallet,Timestamp,Note')
    path.write_text('\n'.join([header] + list(rows)) + '\n', encoding='utf-8')
    return str(path)


def run_tax(path, tax_rules, skip_integrity_check=True):
    records = import_csv(path)
    history = TransactionHistory(records, NoPriceLookup())

    if tax_rules == config.TAX_RULES_US_INDIVIDUAL:
        tax = TaxCalculatorUS(history.transactions, tax_rules)
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
