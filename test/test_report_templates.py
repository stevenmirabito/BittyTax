# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

import argparse
import os

from bittytax.audit import AuditRecords
from bittytax.config import config
from bittytax.report import ReportPdf
from conftest import (FIXTURES, import_csv, run_tax, use_uk_rules, use_us_rules,
                      write_workbook)


class NoCurrentPrice(object):
    price_report = {}

    @staticmethod
    def get_current_value(asset, quantity):
        return None, None, None


def _render(path, tax_rules, years):
    tax = run_tax(path, tax_rules)
    tax.process_income()
    for year in years:
        tax.calculate_capital_gains(year)
        tax.calculate_income(year)
    tax.calculate_holdings(NoCurrentPrice())

    args = argparse.Namespace(taxyear=None, summary=False, tax_rules=tax_rules)
    return ReportPdf.render_html('bittytax', AuditRecords(import_csv(path)), tax.tax_report,
                                 {}, tax.holdings_report, args)


def test_uk_report_uses_shared_templates():
    use_uk_rules()
    html = _render(os.path.join(FIXTURES, 'golden', 'multi_wallet_gbp.csv'),
                   config.TAX_RULES_UK_INDIVIDUAL, [2022, 2023])
    assert 'Tax Estimate' in html
    assert 'Form 8949' not in html


def test_us_report_uses_us_templates():
    use_us_rules()
    html = _render(os.path.join(FIXTURES, 'multi_wallet.csv'),
                   config.TAX_RULES_US_INDIVIDUAL, [2021, 2022, 2023, 2025, 2026])
    assert 'Tax Estimate' not in html
    assert 'Part II - Long-term' in html
    assert 'Box L - Long-term digital asset transactions not reported on Form 1099-DA' in html
    assert 'Box F - Long-term transactions not reported to you on Form 1099-B' in html
    assert 'Per-Wallet Basis at' in html
    assert 'Current Holdings by Wallet' in html


def test_us_report_with_1099da_sheets(tmp_path):
    use_us_rules()
    rows = open(os.path.join(FIXTURES, 'multi_wallet.csv')).read().splitlines()[1:]
    form = ['Form Year', 'Wallet', 'Asset', 'Date Sold', 'Quantity', 'Proceeds', 'Cost Basis',
            'Date Acquired', 'Term', 'Basis Reported']
    path = write_workbook(tmp_path, {
        'Transactions': rows,
        '1099-DA Coinbase': [form, [2026, 'Coinbase', 'ETH', '2026-01-15', 3, 9000, 4200,
                                    '2021-03-05', 'Long', 'Yes']],
    })
    html = _render(path, config.TAX_RULES_US_INDIVIDUAL, [2026])
    assert 'Form 1099-DA Reconciliation' in html
    assert 'Box J - Long-term digital asset transactions reported on Form 1099-DA' in html
    assert 'Adj. (g)' in html
