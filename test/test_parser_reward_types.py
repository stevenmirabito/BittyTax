# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026
"""Rewards and rebates from the conversion tool use the specific types, not Gift-Received."""

import pytest

from bittytax.conv.parsers.binance import parse_binance_statements
from bittytax.conv.parsers.bitfinex import parse_bitfinex_ledger
from bittytax.conv.parsers.blockfi import parse_blockfi
from bittytax.conv.parsers.celsius import parse_celsius
from conftest import use_us_rules


class Row(object):
    def __init__(self, row_dict):
        self.row_dict = row_dict
        self.t_record = None
        self.timestamp = None
        self.line_num = 1
        self.parsed = False


@pytest.mark.parametrize('operation, t_type', [
    ('Referrer rebates', 'Referral'),
    ('Commission History', 'Referral'),
    ('Cash Voucher distribution', 'Referral'),
    ('Referral Kickback', 'Fee-Rebate'),
    ('Commission Fee Shared With You', 'Fee-Rebate'),
])
def test_binance_statement_rewards(operation, t_type):
    use_us_rules()
    row = Row({'UTC_Time': '2023-01-01 10:00:00', 'Operation': operation, 'Change': '0.5',
               'Coin': 'BNB', 'Account': 'Spot', 'Remark': ''})
    parse_binance_statements([row], None)
    assert row.t_record.t_type == t_type


def test_bitfinex_affiliate_rebate_is_referral():
    use_us_rules()
    row = Row({'DATE': '01-02-21 10:00:00', 'DESCRIPTION': 'Affiliate Rebate (Referral)',
               'AMOUNT': '1.5', 'CURRENCY': 'USD'})
    parse_bitfinex_ledger(row, None)
    assert row.t_record.t_type == 'Referral'


def test_blockfi_bonus_is_referral():
    use_us_rules()
    row = Row({'Confirmed At': '2021-02-01 10:00:00', 'Transaction Type': 'Bonus Payment',
               'Amount': '0.0005', 'Cryptocurrency': 'BTC'})
    parse_blockfi(row, None, unconfirmed=False)
    assert row.t_record.t_type == 'Referral'


@pytest.mark.parametrize('kind', ['promo_code_reward', 'referrer_award'])
def test_celsius_promo_is_referral(kind):
    use_us_rules()
    row = Row({'Date and time': 'February 1, 2021 10:00 AM', 'Confirmed': 'Yes',
               'Transaction type': kind, 'Coin amount': '10', 'Coin type': 'CEL',
               'USD Value': '20'})
    parse_celsius(row, None, unconfirmed=False)
    assert row.t_record.t_type == 'Referral'
