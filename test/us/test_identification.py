# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

import csv

from bittytax.config import config
from bittytax.export_us import ExportLots
from bittytax.tax.us import TaxEventCapitalGainsUS
from bittytax.us_records import UsRecords
from conftest import (import_records, run_tax, us_records, use_us_rules, write_csv,
                      write_workbook)

ORDERS = ['Wallet', 'Method', 'Effective From']
IDENTS = ['Wallet', 'Timestamp', 'Asset', 'Quantity', 'Lot ID', 'Recorded At']

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
    use_us_rules()
    records = us_records(**{'Standing Orders': [
        ORDERS, ['Coinbase', 'HIFO', '2026-10-08T00:00:00']]})
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL, records=records)
    assert _costs(tax) == [
        (2025, '10000.00', 'First-In, First-Out'),
        (2026, '60000.00', 'Highest-In, First-Out (standing order)')]
    assert tax.integrity_failures == []


def test_wallet_order_beats_global_order(tmp_path):
    use_us_rules()
    records = us_records(**{'Standing Orders': [
        ORDERS, ['*', 'HIFO', '2025-01-01'], ['Coinbase', 'LIFO', '2025-01-01']]})
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL, records=records)
    assert [c[1] for c in _costs(tax)] == ['30000.00', '60000.00']


def test_sheet_errors():
    use_us_rules()
    records = UsRecords()
    errors = records.parse_sheet(UsRecords.STANDING_ORDERS, 'Standing Orders', [
        ORDERS, ['', 'AVG', '2025-01-01'], ['', 'FIFO', 'not a date']])
    assert str(errors[0]) == ("'Standing Orders' row 2: unknown method 'AVG', use one of "
                              "FIFO, HIFO, LIFO, LOFO")
    assert str(errors[1]).startswith("'Standing Orders' row 3: ")
    errors = records.parse_sheet(UsRecords.IDENTIFICATIONS, 'Identifications', [ORDERS])
    assert 'header must be' in str(errors[0])


def _idents(*rows):
    return us_records(Identifications=[IDENTS] + [r.split(',') for r in rows])


def test_specific_identification(tmp_path):
    use_us_rules()
    records = _idents('Coinbase,2025-03-01T12:00:00Z,BTC,0.5,BTC:Coinbase:2024-06-01T12:00:00Z,'
                      '2025-03-01T11:59:00Z')
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL, records=records)
    # Half identified, the rest falls back to FIFO
    assert _costs(tax) == [(2025, '15000.00', 'Specific Identification'),
                           (2025, '5000.00', 'First-In, First-Out'),
                           (2026, '5000.00', 'First-In, First-Out'),
                           (2026, '30000.00', 'First-In, First-Out')]
    assert tax.integrity_failures == [] and tax.warnings == []


def test_identification_recorded_after_sale_is_rejected(tmp_path):
    use_us_rules()
    records = _idents('Coinbase,2025-03-01T12:00:00Z,BTC,1,BTC:Coinbase:2024-06-01T12:00:00Z,'
                      '2026-04-01T00:00:00Z')
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL, records=records)
    assert _costs(tax)[0] == (2025, '10000.00', 'First-In, First-Out')
    assert 'recorded after the transaction' in tax.integrity_failures[0]


def test_unused_identification_warns(tmp_path):
    use_us_rules()
    records = _idents('Kraken,2025-03-01T12:00:00Z,BTC,1,BTC:Coinbase:2024-06-01T12:00:00Z,'
                      '2025-03-01T00:00:00Z')
    tax = run_tax(write_csv(tmp_path, ROWS), config.TAX_RULES_US_INDIVIDUAL, records=records)
    assert len(tax.warnings) == 1


def test_broker_standing_order_after_2026_warns(tmp_path):
    use_us_rules(wallets={'Coinbase': {'custody': 'broker'}})
    records = us_records(**{'Standing Orders': [ORDERS, ['*', 'HIFO', '2026-10-08']]})
    tax = run_tax(write_csv(tmp_path, ROWS + [
        'Trade,50000,USD,,0.5,BTC,50000,,,,Coinbase,2027-02-01T12:00:00Z,']),
        config.TAX_RULES_US_INDIVIDUAL, records=records)
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


def test_workbook_is_the_source_of_truth(tmp_path):
    use_us_rules()
    path = write_workbook(tmp_path, {
        'Transactions': ROWS,
        'Standing Orders': [ORDERS, ['Coinbase', 'HIFO', '2026-10-08']],
        'Identifications': [IDENTS, ['Coinbase', '2025-03-01T12:00:00Z', 'BTC', 1,
                                     'BTC:Coinbase:2024-06-01T12:00:00Z',
                                     # Local time, 03:00 PT is before the 12:00 UTC sale
                                     '2025-03-01T03:00:00']],
    })
    transaction_records, records = import_records(path)
    assert len(transaction_records) == len(ROWS)
    assert len(records.standing_orders) == 1 and len(records.identifications) == 1

    tax = run_tax(path, config.TAX_RULES_US_INDIVIDUAL)
    assert _costs(tax) == [(2025, '30000.00', 'Specific Identification'),
                           (2026, '60000.00', 'Highest-In, First-Out (standing order)')]
    assert tax.integrity_failures == [] and tax.warnings == []
