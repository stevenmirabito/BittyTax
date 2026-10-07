# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

from decimal import Decimal

import pytest

from bittytax.config import config
from bittytax.conv.out_record import TransactionOutRecord
from bittytax.conv.parsers.cryptocom import parse_crypto_com
from bittytax.tax import TaxEventIncome
from bittytax.tax.us import TaxEventCapitalGainsUS
from conftest import gains_rows, run_tax, use_uk_rules, use_us_rules, write_csv

ROWS = [
    'Cashback,100,CRO,15,,,,,,,Crypto.com,2025-02-01T12:00:00Z,Card Rebate: Netflix',
    'Fee-Rebate,0.5,BNB,150,,,,,,,Binance,2025-02-02T12:00:00Z,',
    'Referral,0.001,BTC,50,,,,,,,Nexo,2025-02-03T12:00:00Z,Sign-up bonus',
    'Trade,30,USD,,100,CRO,30,,,,Crypto.com,2025-06-01T12:00:00Z,',
]


def _events(tax, cls):
    return [te for events in tax.tax_events.values() for te in events if isinstance(te, cls)]


def test_us_cashback_and_fee_rebate_are_fmv_basis_not_income(tmp_path):
    use_us_rules()
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL)
    tax.process_income()
    tax.calculate_capital_gains(2025)
    tax.calculate_income(2025)

    assert [(te.type, te.amount) for te in _events(tax, TaxEventIncome)] == [
        ('Referral', Decimal('50.00'))]
    assert [(te.type, te.amount) for te in tax.tax_report[2025]['Rebates']] == [
        ('Cashback', Decimal('15.00')), ('Fee-Rebate', Decimal('150.00'))]
    # The cashback lot's basis is its FMV when received
    [sale] = _events(tax, TaxEventCapitalGainsUS)
    assert (sale.cost, sale.gain) == (Decimal('15.00'), Decimal('15.00'))
    assert tax.integrity_failures == []


def test_uk_new_types_are_not_income(tmp_path):
    use_uk_rules()
    tax = run_tax(write_csv(tmp_path, [r.replace('USD', 'GBP') for r in ROWS]),
                  config.TAX_RULES_UK_INDIVIDUAL)
    tax.process_income()
    assert _events(tax, TaxEventIncome) == []
    assert gains_rows(tax)[0][5] == '15.00'


class Row(object):
    def __init__(self, kind, description):
        self.row_dict = {'Timestamp (UTC)': '2024-01-05 10:00:00', 'Currency': 'CRO',
                         'Amount': '12.5', 'Native Currency': 'USD', 'Native Amount': '1.10',
                         'Transaction Kind': kind, 'Transaction Description': description,
                         'To Currency': '', 'To Amount': ''}
        self.t_record = None
        self.timestamp = None


@pytest.mark.parametrize('kind, t_type', [
    ('referral_card_cashback', 'Cashback'),
    ('reimbursement', 'Cashback'),
    ('transfer_cashback', 'Cashback'),
    ('referral_bonus', 'Referral'),
    ('referral_gift', 'Referral'),
    ('campaign_reward', 'Gift-Received'),
])
def test_crypto_com_reward_mapping(kind, t_type):
    use_us_rules()
    row = Row(kind, 'CRO Rewards')
    parse_crypto_com(row, None)
    assert row.t_record.t_type == t_type
    assert t_type in TransactionOutRecord.BUY_TYPES
