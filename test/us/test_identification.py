# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

import csv

from bittytax.config import config
from bittytax.export_us import ExportLots
from bittytax.tax.us import TaxEventCapitalGainsUS
from conftest import run_tax, use_us_rules, write_csv

ROWS = [
    'Trade,1,BTC,10000,10000,USD,,,,,Coinbase,2023-01-01T12:00:00Z,',
    'Trade,1,BTC,60000,60000,USD,,,,,Coinbase,2024-01-01T12:00:00Z,',
    'Trade,1,BTC,30000,30000,USD,,,,,Coinbase,2024-06-01T12:00:00Z,',
    'Trade,50000,USD,,1,BTC,50000,,,,Coinbase,2025-03-01T12:00:00Z,',
    'Trade,50000,USD,,1,BTC,50000,,,,Coinbase,2026-11-01T12:00:00Z,',
]


def _costs(tax):
    events = sorted((te for events in tax.tax_events.values() for te in events
                     if isinstance(te, TaxEventCapitalGainsUS)), key=lambda te: te.date)
    return [(te.date.year, str(te.cost), te.disposal_type) for te in events]


def test_no_identification_is_fifo(tmp_path):
    use_us_rules()
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL)
    assert _costs(tax) == [(2025, '10000.00', 'First-In, First-Out'),
                           (2026, '60000.00', 'First-In, First-Out')]


def test_standing_order_applies_only_from_when_recorded(tmp_path):
    use_us_rules(us_standing_orders=[
        {'wallet': 'Coinbase', 'method': 'HIFO', 'effective_from': '2026-10-08T00:00:00'}])
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL)
    assert _costs(tax) == [
        (2025, '10000.00', 'First-In, First-Out'),
        (2026, '60000.00', 'Highest-In, First-Out (standing order)')]
    assert tax.integrity_failures == []


def test_wallet_order_beats_global_order(tmp_path):
    use_us_rules(us_standing_orders=[
        {'wallet': '*', 'method': 'HIFO', 'effective_from': '2025-01-01'},
        {'wallet': 'Coinbase', 'method': 'LIFO', 'effective_from': '2025-01-01'}])
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL)
    assert [c[1] for c in _costs(tax)] == ['30000.00', '60000.00']


def test_unknown_standing_order_method_fails(tmp_path):
    use_us_rules(us_standing_orders=[{'method': 'AVG', 'effective_from': '2025-01-01'}])
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL)
    assert 'unknown method' in tax.integrity_failures[0]


def _identifications(tmp_path, rows):
    path = tmp_path / 'identifications.csv'
    path.write_text('\n'.join(['Wallet,Timestamp,Asset,Quantity,Lot ID,Recorded At'] + rows)
                    + '\n', encoding='utf-8')
    return str(path)


def test_specific_identification(tmp_path):
    use_us_rules(us_identifications_file=_identifications(tmp_path, [
        'Coinbase,2025-03-01T12:00:00Z,BTC,0.5,BTC:Coinbase:2024-06-01T12:00:00Z,'
        '2025-03-01T11:59:00Z',
    ]))
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL)
    # Half identified, the rest falls back to FIFO
    assert _costs(tax) == [(2025, '15000.00', 'Specific Identification'),
                           (2025, '5000.00', 'First-In, First-Out'),
                           (2026, '5000.00', 'First-In, First-Out'),
                           (2026, '30000.00', 'First-In, First-Out')]
    assert tax.integrity_failures == [] and tax.warnings == []


def test_identification_recorded_after_sale_is_rejected(tmp_path):
    use_us_rules(us_identifications_file=_identifications(tmp_path, [
        'Coinbase,2025-03-01T12:00:00Z,BTC,1,BTC:Coinbase:2024-06-01T12:00:00Z,'
        '2026-04-01T00:00:00Z',
    ]))
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL)
    assert _costs(tax)[0] == (2025, '10000.00', 'First-In, First-Out')
    assert 'recorded after the transaction' in tax.integrity_failures[0]


def test_unused_identification_warns(tmp_path):
    use_us_rules(us_identifications_file=_identifications(tmp_path, [
        'Kraken,2025-03-01T12:00:00Z,BTC,1,BTC:Coinbase:2024-06-01T12:00:00Z,'
        '2025-03-01T00:00:00Z',
    ]))
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL)
    assert len(tax.warnings) == 1


def test_broker_standing_order_after_2026_warns(tmp_path):
    use_us_rules(wallets={'Coinbase': {'custody': 'broker', 'issues_1099da': True}},
                 us_standing_orders=[{'method': 'HIFO', 'effective_from': '2026-10-08'}])
    tax = run_tax(write_csv(tmp_path, ROWS + [
        'Trade,50000,USD,,0.5,BTC,50000,,,,Coinbase,2027-02-01T12:00:00Z,']),
        config.TAX_RULES_US_INDIVIDUAL)
    assert len(tax.warnings) == 1 and 'after 2026' in tax.warnings[0]


def test_lot_records_export(tmp_path):
    use_us_rules()
    tax = run_tax(write_csv(tmp_path, ROWS[:2] + [
        'Withdrawal,,,,1,BTC,,,,,Coinbase,2025-02-01T12:00:00Z,',
        'Deposit,1,BTC,,,,,,,,Ledger,2025-02-01T12:30:00Z,',
        'Trade,1,BTC,90000,90000,USD,,,,,Coinbase,2025-04-01T12:00:00Z,',
    ]), config.TAX_RULES_US_INDIVIDUAL)
    filename = ExportLots(tax).write_csv(str(tmp_path / 'lots.csv'))
    with open(filename, encoding='utf-8') as csv_file:
        rows = [(r['Action'], r['Wallet'], r['To Wallet'], r['Quantity'], r['Cost Basis'],
                 r['Lot ID']) for r in csv.DictReader(csv_file)]
    assert rows == [
        ('Open', 'Coinbase', '', '1', '10000.00', 'BTC:Coinbase:2023-01-01T12:00:00Z'),
        ('Open', 'Coinbase', '', '1', '60000.00', 'BTC:Coinbase:2024-01-01T12:00:00Z'),
        ('Transfer', 'Coinbase', 'Ledger', '1', '10000.00',
         'BTC:Coinbase:2023-01-01T12:00:00Z'),
        ('Acquire', 'Coinbase', '', '1', '90000.00', 'BTC:Coinbase:2025-04-01T12:00:00Z'),
        ('Held', 'Coinbase', '', '1', '60000.00', 'BTC:Coinbase:2024-01-01T12:00:00Z'),
        ('Held', 'Coinbase', '', '1', '90000.00', 'BTC:Coinbase:2025-04-01T12:00:00Z'),
        ('Held', 'Ledger', '', '1', '10000.00', 'BTC:Coinbase:2023-01-01T12:00:00Z'),
    ]
