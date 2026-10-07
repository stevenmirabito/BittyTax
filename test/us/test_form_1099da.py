# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

import csv
from decimal import Decimal

from bittytax.config import config
from bittytax.export_us import ExportForm8949
from bittytax.tax.us import TaxEventCapitalGainsUS
from conftest import run_tax, us_records, use_us_rules, write_csv, write_workbook

FORM = ['Form Year', 'Wallet', 'Asset', 'Date Sold', 'Quantity', 'Proceeds', 'Cost Basis',
        'Date Acquired', 'Term', 'Basis Reported']
BUYS = [
    'Trade,1,BTC,10000,10000,USD,,,,,Coinbase,2023-01-01T12:00:00Z,',
    'Trade,1,BTC,60000,60000,USD,,,,,Coinbase,2026-01-05T12:00:00Z,',
]


def _run(tmp_path, sales, forms, orders=None):
    use_us_rules()
    sheets = {'1099-DA Coinbase': [FORM] + [f.split(',') for f in forms]}
    if orders:
        sheets['Standing Orders'] = [['Wallet', 'Method', 'Effective From']] + orders
    return run_tax(write_csv(tmp_path, BUYS + sales), config.TAX_RULES_US_INDIVIDUAL,
                   records=us_records(**sheets))


def _lines(tax):
    """(box, (d), (e), (f), (g), (h)) for each Form 8949 line."""
    events = sorted((te for events in tax.tax_events.values() for te in events
                     if isinstance(te, TaxEventCapitalGainsUS)),
                    key=lambda te: (te.date, te.lot_id))
    return [(te.form_8949_box, str(te.form_proceeds), str(te.form_basis), te.adjustment_code,
             str(te.adjustment), str(te.gain)) for te in events]


def _status(tax):
    return [(line.status, line.codes) for line in tax.reconciliation.lines]


def test_matching_sale_takes_broker_figures(tmp_path):
    tax = _run(tmp_path, ['Trade,50000,USD,,1,BTC,50000,,,,Coinbase,2026-03-01T12:00:00Z,'],
               ['2026,Coinbase,BTC,2026-03-01,1,50000,10000,2023-01-01,Long,Yes'])
    assert _lines(tax) == [('J', '50000.00', '10000.00', '', '0', '40000.00')]
    assert _status(tax) == [('Match', '')]
    assert tax.integrity_failures == []


def test_identified_lot_gives_code_b(tmp_path):
    # HIFO in your records, the broker reports FIFO basis to the IRS
    tax = _run(tmp_path, ['Trade,50000,USD,,1,BTC,50000,,,,Coinbase,2026-03-01T12:00:00Z,'],
               ['2026,Coinbase,BTC,2026-03-01,1,50000,10000,2023-01-01,Long,Yes'],
               orders=[['Coinbase', 'HIFO', '2026-01-10']])
    # (e) is the broker's basis, (g) corrects it, (h) is the gain from your records
    assert _lines(tax) == [('G', '50000.00', '10000.00', 'B', '-50000.00', '-10000.00')]
    [line] = tax.reconciliation.lines
    assert (line.status, line.codes) == ('Adjusted', 'B')
    assert 'Notice 2026-20' in line.note
    assert 'different term' in line.note


def test_selling_costs_not_deducted_give_code_e(tmp_path):
    tax = _run(tmp_path, ['Trade,50000,USD,,1,BTC,50000,25,USD,,Coinbase,2026-03-01T12:00:00Z,'],
               ['2026,Coinbase,BTC,2026-03-01,1,50000,,,,No'])
    assert _lines(tax) == [('K', '50000.00', '10000.00', 'E', '-25.00', '39975.00')]
    assert _status(tax) == [('Adjusted', 'E')]


def test_net_proceeds_match_without_adjustment(tmp_path):
    tax = _run(tmp_path, ['Trade,50000,USD,,1,BTC,50000,25,USD,,Coinbase,2026-03-01T12:00:00Z,'],
               ['2026,Coinbase,BTC,2026-03-01,1,49975,,,,No'])
    assert _lines(tax) == [('K', '49975.00', '10000.00', '', '0', '39975.00')]
    assert _status(tax) == [('Match', '')]


def test_rounding_accepts_broker_figures(tmp_path):
    tax = _run(tmp_path, ['Trade,50000,USD,,1,BTC,50000,,,,Coinbase,2026-03-01T12:00:00Z,'],
               ['2026,Coinbase,BTC,2026-03-01,1,50000.40,,,,No'])
    assert _lines(tax) == [('K', '50000.40', '10000.00', '', '0', '40000.40')]


def test_unexplained_proceeds_are_code_o_for_review(tmp_path):
    tax = _run(tmp_path, ['Trade,50000,USD,,1,BTC,50000,,,,Coinbase,2026-03-01T12:00:00Z,'],
               ['2026,Coinbase,BTC,2026-03-01,1,48000,,,,No'])
    assert _lines(tax) == [('K', '48000.00', '10000.00', 'O', '2000.00', '40000.00')]
    assert _status(tax) == [('Review', 'O')]
    assert len(tax.warnings) == 1


def test_broker_combined_sales_are_allocated(tmp_path):
    tax = _run(tmp_path, [
        'Trade,25000,USD,,0.5,BTC,25000,,,,Coinbase,2026-03-01T12:00:00Z,',
        'Trade,25000,USD,,0.5,BTC,25000,,,,Coinbase,2026-03-01T15:00:00Z,',
    ], ['2026,Coinbase,BTC,2026-03-01,1,50000,12000,,,Yes'])
    assert _lines(tax) == [
        ('J', '25000.00', '6000.00', 'B', '1000.00', '20000.00'),
        ('J', '25000.00', '6000.00', 'B', '1000.00', '20000.00'),
    ]
    assert _status(tax) == [('Adjusted', 'B')]


def test_unmatched_rows(tmp_path):
    tax = _run(tmp_path, ['Trade,50000,USD,,1,BTC,50000,,,,Coinbase,2026-03-01T12:00:00Z,'],
               ['2026,Coinbase,BTC,2026-04-01,0.2,9000,,,,No'])
    assert _status(tax) == [('Not on 1099-DA', ''), ('Not in records', '')]
    assert _lines(tax)[0][0] == 'L'
    assert len(tax.integrity_failures) == 1


def test_late_evening_sale_matches_utc_date(tmp_path):
    # 8pm Pacific on Dec 31st, the broker dates it Jan 1st (UTC)
    tax = _run(tmp_path, ['Trade,50000,USD,,1,BTC,50000,,,,Coinbase,2026-12-31T20:00:00-08:00,'],
               ['2027,Coinbase,BTC,2027-01-01,1,50000,,,,No'])
    assert _status(tax) == [('Match', '')]
    assert _lines(tax)[0][0] == 'K'


def test_wallet_issuing_1099da_without_rows_warns(tmp_path):
    use_us_rules(wallets={'Coinbase': {'issues_1099da': True}})
    tax = run_tax(write_csv(tmp_path, BUYS + [
        'Trade,50000,USD,,1,BTC,50000,,,,Coinbase,2026-03-01T12:00:00Z,']),
        config.TAX_RULES_US_INDIVIDUAL)
    assert any('no \'1099-DA\' rows for 2026' in w for w in tax.warnings)


def test_workbook_1099da_sheet_and_export(tmp_path):
    use_us_rules()
    path = write_workbook(tmp_path, {
        'Transactions': BUYS + ['Trade,50000,USD,,1,BTC,50000,,,,Coinbase,'
                                '2026-03-01T12:00:00Z,'],
        '1099-DA Coinbase': [FORM, [2026, 'Coinbase', 'BTC', '2026-03-01', 1, 50000, 12000,
                                    '2023-01-01', 'Long-term', 'Yes']],
    })
    tax = run_tax(path, config.TAX_RULES_US_INDIVIDUAL)
    tax.calculate_capital_gains(2026)
    assert tax.tax_report[2026]['CapitalGains'].form_totals['adjustment'] == Decimal('2000.00')

    filename = ExportForm8949(tax.tax_report).write_csv(str(tmp_path / 'f8949.csv'))
    with open(filename, encoding='utf-8') as csv_file:
        [row] = list(csv.DictReader(csv_file))
    assert (row['Box'], row['Proceeds (d)'], row['Cost Basis (e)'], row['Adjustment Code (f)'],
            row['Adjustment Amount (g)'], row['Gain or Loss (h)']) == (
        'J', '50000.00', '12000.00', 'B', '2000.00', '40000.00')
