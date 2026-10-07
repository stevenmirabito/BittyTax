# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

import argparse
import os

from bittytax.audit import AuditRecords
from bittytax.config import config
from bittytax.report import ReportPdf
from conftest import FIXTURES, import_csv, run_tax, use_uk_rules, use_us_rules


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
    assert 'Proceeds from Broker' not in html


def test_us_report_uses_us_templates():
    use_us_rules()
    html = _render(os.path.join(FIXTURES, 'multi_wallet.csv'),
                   config.TAX_RULES_US_INDIVIDUAL, [2025])
    assert 'Proceeds from Broker' in html
    assert 'Tax Estimate' not in html
