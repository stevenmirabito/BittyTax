# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

from bittytax.bittytax import is_supported_year
from bittytax.config import config
from conftest import run_tax, use_uk_rules, use_us_rules, write_csv

ROWS = [
    'Trade,1,BTC,10000,10000,USD,,,,,Coinbase,2024-06-01T12:00:00Z,',
    # 2025-01-01 07:30 UTC is still New Year's Eve in Los Angeles
    'Trade,1000,USD,,0.1,BTC,1000,,,,Coinbase,2024-12-31T23:30:00-08:00,',
    'Trade,1000,USD,,0.1,BTC,1000,,,,Coinbase,2025-01-01T00:30:00-08:00,',
]


def _years(tax):
    return {year: len(events) for year, events in tax.tax_events.items() if events}


def test_us_year_boundary_is_pacific_time(tmp_path):
    # Pacific time for every year
    use_us_rules(us_timezone_from='2000-01-01')
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL)
    assert _years(tax) == {2024: 1, 2025: 1}
    event_2024 = tax.tax_events[2024][0]
    assert event_2024.date.isoformat() == '2024-12-31T23:30:00-08:00'


def test_us_timezone_is_configurable(tmp_path):
    use_us_rules(us_timezone='America/New_York', us_timezone_from='2000-01-01')
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL)
    # 02:30 and 03:30 in New York, both already in 2025
    assert _years(tax) == {2025: 2}


def test_filed_years_keep_their_time_zone(tmp_path):
    # Default: London before 2025 (as filed), Pacific from 2025-01-01 00:00 Pacific
    use_us_rules()
    tax = run_tax(write_csv(tmp_path, ROWS + [
        'Trade,1000,USD,,0.1,BTC,1000,,,,Coinbase,2024-12-31T23:30:00Z,',
        'Trade,1000,USD,,0.1,BTC,1000,,,,Coinbase,2024-12-30T23:30:00-08:00,',
    ]), config.TAX_RULES_US_INDIVIDUAL)
    dates = [(te.date.isoformat(), year) for year, te in sorted(
        ((year, te) for year, events in tax.tax_events.items() for te in events),
        key=lambda item: item[1].date)]
    assert dates == [
        # 07:30 UTC on Dec 31st, London date (Pacific would have been Dec 30th)
        ('2024-12-31T07:30:00+00:00', 2024),
        ('2024-12-31T23:30:00+00:00', 2024),
        # Before Pacific midnight, so London: already Jan 1st 2025, never added to 2024
        ('2025-01-01T07:30:00+00:00', 2025),
        ('2025-01-01T00:30:00-08:00', 2025),
    ]
    assert config.get_tax_year_end(2024).isoformat() == '2024-12-31T23:59:59.999999+00:00'
    assert config.get_tax_year_start(2025).isoformat() == '2025-01-01T00:00:00-08:00'


def test_uk_rules_keep_london_time():
    use_us_rules()
    use_uk_rules()
    assert config.TZ_LOCAL is config.TZ_UK


def test_supported_years():
    assert is_supported_year(2026, config.TAX_RULES_US_INDIVIDUAL)
    assert is_supported_year(2030, config.TAX_RULES_US_INDIVIDUAL)
    assert not is_supported_year(2030, config.TAX_RULES_UK_INDIVIDUAL)


def test_us_capital_gains_without_uk_tables(tmp_path):
    use_us_rules()
    rows = ROWS[:1] + ['Trade,1000,USD,,0.1,BTC,1000,,,,Coinbase,2026-03-01T12:00:00Z,']
    tax = run_tax(write_csv(tmp_path, rows), config.TAX_RULES_US_INDIVIDUAL)
    tax.calculate_capital_gains(2026)
    cgains = tax.tax_report[2026]['CapitalGains']
    assert cgains.estimate is None
    assert cgains.summary['disposals'] == 1
