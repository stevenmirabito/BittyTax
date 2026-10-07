# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

from datetime import datetime
from decimal import Decimal

import pytest

from bittytax.config import config
from bittytax.tax.us import (LONG_TERM, SHORT_TERM, TaxEventCapitalGainsUS, TaxEventNonSale,
                             holding_period)
from conftest import run_tax, use_us_rules, write_csv


def _events(tax, cls):
    return [te for events in tax.tax_events.values() for te in events if isinstance(te, cls)]


@pytest.mark.parametrize('acquired, disposed, term', [
    ((2024, 3, 15), (2025, 3, 15), SHORT_TERM),
    ((2024, 3, 15), (2025, 3, 16), LONG_TERM),
    ((2024, 2, 29), (2025, 2, 28), SHORT_TERM),
    ((2024, 2, 29), (2025, 3, 1), LONG_TERM),
])
def test_holding_period(acquired, disposed, term):
    use_us_rules()
    tz = config.TZ_LOCAL
    assert holding_period(datetime(*acquired, 12, tzinfo=tz),
                          datetime(*disposed, 12, tzinfo=tz)) == term


def test_holding_period_uses_local_dates():
    use_us_rules()
    # 06:00 UTC on Mar 16th is still Mar 15th in Los Angeles: exactly one year, short-term
    acquired = datetime(2024, 3, 15, 12, tzinfo=config.TZ_LOCAL)
    disposed = datetime(2025, 3, 16, 6, tzinfo=config.TZ_UTC)
    assert holding_period(acquired, disposed) == SHORT_TERM
    assert holding_period(None, disposed) == SHORT_TERM


def test_holding_period_tacks_across_transfers(tmp_path):
    use_us_rules()
    tax = run_tax(write_csv(tmp_path, [
        'Trade,1,BTC,10000,10000,USD,,,,,Exchange,2024-01-10T12:00:00Z,',
        'Withdrawal,,,,1,BTC,,,,,Exchange,2024-12-01T12:00:00Z,',
        'Deposit,1,BTC,,,,,,,,Ledger,2024-12-01T12:30:00Z,',
        'Trade,50000,USD,,1,BTC,50000,,,,Ledger,2025-02-01T12:00:00Z,',
    ]), config.TAX_RULES_US_INDIVIDUAL)
    [event] = _events(tax, TaxEventCapitalGainsUS)
    assert event.holding_period == LONG_TERM


def test_gifts_donations_and_losses_are_not_sales(tmp_path):
    use_us_rules()
    tax = run_tax(write_csv(tmp_path, [
        'Trade,4,ETH,4000,4000,USD,,,,,Exchange,2023-01-01T12:00:00Z,',
        'Trade,2,ETH,4000,4000,USD,,,,,Exchange,2025-01-05T12:00:00Z,',
        'Gift-Sent,,,,1,ETH,2500,,,,Exchange,2025-02-01T12:00:00Z,to a friend',
        'Gift-Spouse,,,,1,ETH,2500,,,,Exchange,2025-02-02T12:00:00Z,',
        'Charity-Sent,,,,3,ETH,7500,,,,Exchange,2025-02-03T12:00:00Z,',
        'Lost,,,,1,ETH,,,,,Exchange,2025-02-04T12:00:00Z,',
    ]), config.TAX_RULES_US_INDIVIDUAL)

    assert _events(tax, TaxEventCapitalGainsUS) == []
    assert tax.ledger.open_lots() == []
    assert len(tax.warnings) == 1

    rows = [(te.type, te.quantity, te.basis, te.fair_market_value, te.holding_period,
             te.deductible_amount())
            for te in sorted(_events(tax, TaxEventNonSale), key=lambda te: te.date)]
    assert rows == [
        ('Gift-Sent', 1, Decimal('1000.00'), Decimal('2500.00'), LONG_TERM, Decimal('2500.00')),
        ('Gift-Spouse', 1, Decimal('1000.00'), Decimal('2500.00'), LONG_TERM, Decimal('2500.00')),
        ('Charity-Sent', 2, Decimal('2000.00'), Decimal('5000.00'), LONG_TERM,
         Decimal('5000.00')),
        # Short-term: the deduction is limited to basis
        ('Charity-Sent', 1, Decimal('2000.00'), Decimal('2500.00'), SHORT_TERM,
         Decimal('2000.00')),
        ('Lost', 1, Decimal('2000.00'), Decimal('0.00'), SHORT_TERM, Decimal('0.00')),
    ]


def test_lost_buyback_is_ignored(tmp_path):
    use_us_rules(lost_buyback=True)
    tax = run_tax(write_csv(tmp_path, [
        'Trade,1,ETH,1000,1000,USD,,,,,Exchange,2023-01-01T12:00:00Z,',
        'Lost,,,,1,ETH,,,,,Exchange,2025-02-04T12:00:00Z,',
    ]), config.TAX_RULES_US_INDIVIDUAL)
    assert tax.ledger.open_lots() == []


@pytest.mark.parametrize('wallets, acquired, sold, box', [
    ({}, '2023-01-01', '2024-06-01', 'F'),
    ({}, '2024-01-01', '2024-06-01', 'C'),
    ({}, '2023-01-01', '2025-06-01', 'L'),
    ({'Exchange': {'issues_1099da': True}}, '2025-01-01', '2025-06-01', 'H'),
    ({'Exchange': {'issues_1099da': True}}, '2023-01-01', '2025-06-01', 'K'),
    ({'Exchange': {'issues_1099da': True}}, '2026-01-02', '2026-06-01', 'G'),
    ({'Exchange': {'issues_1099da': True}}, '2026-01-02', '2027-06-01', 'J'),
    ({'Exchange': {'custody': 'self'}}, '2026-01-02', '2026-06-01', 'I'),
])
def test_form_8949_box(tmp_path, wallets, acquired, sold, box):
    use_us_rules(wallets=wallets)
    tax = run_tax(write_csv(tmp_path, [
        'Trade,1,ETH,1000,1000,USD,,,,,Exchange,%sT12:00:00Z,' % acquired,
        'Trade,2000,USD,,1,ETH,2000,,,,Exchange,%sT12:00:00Z,' % sold,
    ]), config.TAX_RULES_US_INDIVIDUAL)
    [event] = _events(tax, TaxEventCapitalGainsUS)
    assert event.form_8949_box == box


def test_transferred_in_units_are_noncovered(tmp_path):
    use_us_rules(wallets={'Exchange': {'issues_1099da': True}})
    tax = run_tax(write_csv(tmp_path, [
        'Trade,1,ETH,1000,1000,USD,,,,,Other,2026-01-02T12:00:00Z,',
        'Withdrawal,,,,1,ETH,,,,,Other,2026-02-01T12:00:00Z,',
        'Deposit,1,ETH,,,,,,,,Exchange,2026-02-01T12:30:00Z,',
        'Trade,2000,USD,,1,ETH,2000,,,,Exchange,2026-06-01T12:00:00Z,',
    ]), config.TAX_RULES_US_INDIVIDUAL)
    [event] = _events(tax, TaxEventCapitalGainsUS)
    assert event.form_8949_box == 'H'
