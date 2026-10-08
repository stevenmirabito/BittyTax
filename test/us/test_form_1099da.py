# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

import csv
import datetime
from decimal import Decimal
from itertools import permutations, product
from types import SimpleNamespace

import pytest

from bittytax.config import config
from bittytax.tax.form_1099da import QUANTITY_TOLERANCE, Reconciliation, Sale
from bittytax.us_records import Form1099DA
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


@pytest.mark.parametrize('fee', ['0.25', '0.99', '1.00'])
@pytest.mark.parametrize('reported', [False, True])
@pytest.mark.parametrize('gross', [False, True])
def test_exact_proceeds_preserve_small_selling_costs(tmp_path, fee, reported, gross):
    fee = Decimal(fee)
    broker = Decimal('50000') if gross else Decimal('50000') - fee
    basis = '10000' if reported else ''
    flag = 'Yes' if reported else 'No'
    tax = _run(tmp_path, [
        'Trade,50000,USD,,1,BTC,50000,%s,USD,,Coinbase,2026-03-01T12:00:00Z,' % fee,
    ], ['2026,Coinbase,BTC,2026-03-01,1,%s,%s,,,%s' % (broker, basis, flag)])
    assert tax.integrity_failures == []
    assert tax.warnings == []
    assert _lines(tax) == [
        ('J' if reported else 'K', format(broker, '.2f'), '10000.00',
         'E' if gross else '', format(-fee, '.2f') if gross else '0',
         format(Decimal('40000') - fee, '.2f')),
    ]
    assert _status(tax) == [('Adjusted', 'E') if gross else ('Match', '')]


@pytest.mark.parametrize('gross', [False, True])
def test_individual_rows_preserve_total_small_selling_costs(tmp_path, gross):
    sales, forms = [], []
    for i in range(1, 11):
        quantity = Decimal(i) / Decimal(100)
        proceeds = quantity * Decimal(10000)
        broker = proceeds if gross else proceeds - Decimal('0.99')
        sales.append('Trade,%s,USD,,%s,BTC,%s,0.99,USD,,Coinbase,2026-03-01T12:%02d:00Z,'
                     % (proceeds, quantity, proceeds, i))
        # Alternate categories to verify individual reported-basis preservation.
        basis = format(quantity * Decimal(10000), '.2f') if i % 2 else ''
        flag = 'Yes' if i % 2 else 'No'
        forms.append('2026,Coinbase,BTC,2026-03-01,%s,%s,%s,,,%s'
                     % (quantity, broker, basis, flag))
    tax = _run(tmp_path, sales, forms)
    assert tax.integrity_failures == []
    assert tax.warnings == []
    lines = _lines(tax)
    assert len(lines) == 10
    assert sum(Decimal(line[5]) for line in lines) == Decimal('-9.90')
    assert sum(Decimal(line[4]) for line in lines) == (Decimal('-9.90') if gross else 0)
    assert all(line[3] == ('E' if gross else '') for line in lines)
    assert [line[0] for line in lines] == ['J' if i % 2 else 'K' for i in range(1, 11)]


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


def test_combined_sales_preserve_different_execution_prices(tmp_path):
    tax = _run(tmp_path, [
        'Trade,40000,USD,,0.4,BTC,40000,10,USD,,Coinbase,2026-03-01T12:00:00Z,',
        'Trade,20000,USD,,0.6,BTC,20000,20,USD,,Coinbase,2026-03-01T15:00:00Z,',
    ], ['2026,Coinbase,BTC,2026-03-01,1,59970,,,,No'])
    assert tax.warnings == []
    assert _status(tax) == [('Match', '')]
    assert _lines(tax) == [
        ('K', '39990.00', '4000.00', '', '0', '35990.00'),
        ('K', '19980.00', '6000.00', '', '0', '13980.00'),
    ]


def test_combined_gross_proceeds_keep_selling_cost_adjustments(tmp_path):
    tax = _run(tmp_path, [
        'Trade,40000,USD,,0.4,BTC,40000,10,USD,,Coinbase,2026-03-01T12:00:00Z,',
        'Trade,20000,USD,,0.6,BTC,20000,20,USD,,Coinbase,2026-03-01T15:00:00Z,',
    ], ['2026,Coinbase,BTC,2026-03-01,1,60000,,,,No'])
    assert tax.warnings == []
    assert _status(tax) == [('Adjusted', 'E')]
    assert _lines(tax) == [
        ('K', '40000.00', '4000.00', 'E', '-10.00', '35990.00'),
        ('K', '20000.00', '6000.00', 'E', '-20.00', '13980.00'),
    ]


def test_net_combined_proceeds_do_not_deduct_small_fees_twice(tmp_path):
    tax = _run(tmp_path, [
        'Trade,40000,USD,,0.4,BTC,40000,0.1,USD,,Coinbase,2026-03-01T12:00:00Z,',
        'Trade,20000,USD,,0.6,BTC,20000,0.1,USD,,Coinbase,2026-03-01T15:00:00Z,',
    ], ['2026,Coinbase,BTC,2026-03-01,1,59999.80,,,,No'])
    assert _status(tax) == [('Match', '')]
    assert _lines(tax) == [
        ('K', '39999.90', '4000.00', '', '0', '35999.90'),
        ('K', '19999.90', '6000.00', '', '0', '13999.90'),
    ]


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


def test_combined_sales_on_overlapping_local_and_utc_dates(tmp_path):
    # Both broker dates are present. Evening Pacific sales must not be greedily
    # added to the earlier day's aggregate just because both dates are candidates.
    sales = [
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-03-01T13:00:00-08:00,',
        'Trade,15000,USD,,0.3,BTC,15000,,,,Coinbase,2026-03-01T14:00:00-08:00,',
        'Trade,5000,USD,,0.1,BTC,5000,,,,Coinbase,2026-03-01T20:00:00-08:00,',
        'Trade,20000,USD,,0.4,BTC,20000,,,,Coinbase,2026-03-01T21:00:00-08:00,',
    ]
    forms = [
        '2026,Coinbase,BTC,2026-03-01,0.5,25000,,,,No',
        '2026,Coinbase,BTC,2026-03-02,0.5,25000,,,,No',
    ]
    tax = _run(tmp_path, sales, forms)
    assert tax.integrity_failures == []
    assert _status(tax) == [('Match', ''), ('Match', '')]
    first, second = tax.reconciliation.lines
    assert [s.quantity for s in first.sales] == [Decimal('0.2'), Decimal('0.3')]
    assert [s.quantity for s in second.sales] == [Decimal('0.1'), Decimal('0.4')]
    assert sum(te.form_proceeds for events in tax.tax_events.values()
               for te in events if isinstance(te, TaxEventCapitalGainsUS)) == Decimal('50000')
    assert all(s.timestamp.day == 1 for line in (first, second) for s in line.sales)


def test_combined_sales_use_local_dates_when_utc_groups_do_not_match(tmp_path):
    tax = _run(tmp_path, [
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-03-01T13:00:00-08:00,',
        'Trade,15000,USD,,0.3,BTC,15000,,,,Coinbase,2026-03-01T20:00:00-08:00,',
        'Trade,5000,USD,,0.1,BTC,5000,,,,Coinbase,2026-03-02T13:00:00-08:00,',
        'Trade,20000,USD,,0.4,BTC,20000,,,,Coinbase,2026-03-02T20:00:00-08:00,',
    ], [
        # Deliberately reverse the form rows; matching must not depend on input order.
        '2026,Coinbase,BTC,2026-03-02,0.5,25000,,,,No',
        '2026,Coinbase,BTC,2026-03-01,0.5,25000,,,,No',
    ])
    assert tax.integrity_failures == []
    assert _status(tax) == [('Match', ''), ('Match', '')]
    assert [[s.timestamp.day for s in line.sales] for line in tax.reconciliation.lines] == [
        [1, 1], [2, 2]]
    sale_ids = [s.events[0].sale_id for line in tax.reconciliation.lines for s in line.sales]
    assert len(sale_ids) == len(set(sale_ids)) == 4


def test_combined_date_groups_do_not_hide_missing_quantity(tmp_path):
    tax = _run(tmp_path, [
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-03-01T13:00:00-08:00,',
        'Trade,15000,USD,,0.3,BTC,15000,,,,Coinbase,2026-03-01T20:00:00-08:00,',
    ], ['2026,Coinbase,BTC,2026-03-01,0.6,30000,,,,No'])
    assert len(tax.integrity_failures) == 1
    assert not any(line.status == 'Match' for line in tax.reconciliation.lines)
    assert sum(len(line.sales) for line in tax.reconciliation.lines) == 2


def test_ambiguous_combined_local_and_utc_groups_require_review(tmp_path):
    tax = _run(tmp_path, [
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-02-28T20:00:00-08:00,',
        'Trade,15000,USD,,0.3,BTC,15000,,,,Coinbase,2026-03-01T13:00:00-08:00,',
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-03-01T20:00:00-08:00,',
    ], ['2026,Coinbase,BTC,2026-03-01,0.5,25000,,,,No'])
    assert len(tax.integrity_failures) == 1
    [line] = [line for line in tax.reconciliation.lines if line.forms]
    assert line.status == 'Not in records'
    assert 'ambiguous' in line.note
    assert line.sales == []


def test_aggregate_day_is_not_preempted_by_equal_quantity_evening_sale(tmp_path):
    tax = _run(tmp_path, [
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-03-01T13:00:00-08:00,',
        'Trade,15000,USD,,0.3,BTC,15000,,,,Coinbase,2026-03-01T14:00:00-08:00,',
        'Trade,25000,USD,,0.5,BTC,25000,,,,Coinbase,2026-03-01T20:00:00-08:00,',
    ], [
        '2026,Coinbase,BTC,2026-03-01,0.5,25000,,,,No',
        '2026,Coinbase,BTC,2026-03-02,0.5,25000,,,,No',
    ])
    assert tax.integrity_failures == []
    assert _status(tax) == [('Match', ''), ('Match', '')]
    assert [[s.quantity for s in line.sales] for line in tax.reconciliation.lines] == [
        [Decimal('0.2'), Decimal('0.3')], [Decimal('0.5')]]


def test_individual_sale_with_two_possible_dates_is_not_guessed(tmp_path):
    tax = _run(tmp_path, [
        'Trade,25000,USD,,0.5,BTC,25000,,,,Coinbase,2026-02-28T20:00:00-08:00,',
        'Trade,25000,USD,,0.5,BTC,25000,,,,Coinbase,2026-03-01T20:00:00-08:00,',
    ], ['2026,Coinbase,BTC,2026-03-01,0.5,25000,,,,No'])
    assert len(tax.integrity_failures) == 1
    assert not any(line.status == 'Match' for line in tax.reconciliation.lines)
    assert sum(len(line.sales) for line in tax.reconciliation.lines) == 2


def test_same_sale_cannot_satisfy_two_broker_dates(tmp_path):
    tax = _run(tmp_path, [
        'Trade,25000,USD,,0.5,BTC,25000,,,,Coinbase,2026-03-01T20:00:00-08:00,',
    ], [
        '2026,Coinbase,BTC,2026-03-01,0.5,25000,,,,No',
        '2026,Coinbase,BTC,2026-03-02,0.5,25000,,,,No',
    ])
    assert len(tax.integrity_failures) == 2
    assert not any(line.status == 'Match' for line in tax.reconciliation.lines)


def test_resolved_day_keeps_individual_broker_basis_and_proceeds(tmp_path):
    tax = _run(tmp_path, [
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-03-01T13:00:00-08:00,',
        'Trade,20000,USD,,0.3,BTC,20000,,,,Coinbase,2026-03-01T14:00:00-08:00,',
    ], [
        '2026,Coinbase,BTC,2026-03-01,0.2,10000,1000,,,Yes',
        '2026,Coinbase,BTC,2026-03-01,0.3,20000,2000,,,Yes',
    ])
    assert tax.integrity_failures == []
    assert _lines(tax) == [
        ('J', '10000.00', '1000.00', 'B', '-1000.00', '8000.00'),
        ('J', '20000.00', '2000.00', 'B', '-1000.00', '17000.00'),
    ]


def test_partial_day_keeps_uniquely_identified_broker_rows(tmp_path):
    tax = _run(tmp_path, [
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-03-01T12:00:00Z,',
        'Trade,15000,USD,,0.3,BTC,15000,,,,Coinbase,2026-03-01T14:00:00Z,',
        'Trade,5000,USD,,0.1,BTC,5000,,,,Coinbase,2026-03-01T16:00:00Z,',
    ], [
        '2026,Coinbase,BTC,2026-03-01,0.2,10000,2000,,,Yes',
        '2026,Coinbase,BTC,2026-03-01,0.3,15000,3000,,,Yes',
    ])
    assert tax.integrity_failures == []
    assert _status(tax).count(('Match', '')) == 2
    assert _status(tax).count(('Not on 1099-DA', '')) == 1
    assert _lines(tax) == [
        ('J', '10000.00', '2000.00', '', '0', '8000.00'),
        ('J', '15000.00', '3000.00', '', '0', '12000.00'),
        ('L', '5000.00', '1000.00', '', '0', '4000.00'),
    ]


def test_partial_day_subset_does_not_guess_ambiguous_sale(tmp_path):
    tax = _run(tmp_path, [
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-02-28T20:00:00-08:00,',
        'Trade,15000,USD,,0.3,BTC,15000,,,,Coinbase,2026-03-01T13:00:00-08:00,',
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-03-01T20:00:00-08:00,',
    ], [
        '2026,Coinbase,BTC,2026-03-01,0.2,10000,2000,,,Yes',
        '2026,Coinbase,BTC,2026-03-01,0.3,15000,3000,,,Yes',
    ])
    assert len(tax.integrity_failures) == 1
    assert not any(line.status == 'Match' for line in tax.reconciliation.lines)


def test_partial_day_subset_does_not_reuse_sale_for_duplicate_rows(tmp_path):
    tax = _run(tmp_path, [
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-03-01T12:00:00Z,',
        'Trade,5000,USD,,0.1,BTC,5000,,,,Coinbase,2026-03-01T16:00:00Z,',
    ], [
        '2026,Coinbase,BTC,2026-03-01,0.2,10000,2000,,,Yes',
        '2026,Coinbase,BTC,2026-03-01,0.2,10000,2000,,,Yes',
    ])
    assert len(tax.integrity_failures) == 1
    assert not any(line.status == 'Match' for line in tax.reconciliation.lines)


def test_ambiguous_equal_sales_do_not_discard_reported_basis_category(tmp_path):
    tax = _run(tmp_path, [
        'Trade,25000,USD,,0.5,BTC,25000,,,,Coinbase,2026-03-01T12:00:00Z,',
        'Trade,25000,USD,,0.5,BTC,25000,,,,Coinbase,2026-03-01T14:00:00Z,',
    ], [
        '2026,Coinbase,BTC,2026-03-01,0.5,25000,6000,,,Yes',
        '2026,Coinbase,BTC,2026-03-01,0.5,25000,,,,No',
    ])
    # Quantity/proceeds cannot establish which sale belongs to which category.
    # Reject ambiguity instead of merging both into the unreported-basis box.
    assert tax.integrity_failures
    assert not any(line.status == 'Match' for line in tax.reconciliation.lines)


def test_partial_day_individual_matches_use_per_row_quantity_tolerance(tmp_path):
    tax = _run(tmp_path, [
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-03-01T12:00:00Z,',
        'Trade,15000,USD,,0.3,BTC,15000,,,,Coinbase,2026-03-01T14:00:00Z,',
        'Trade,5000,USD,,0.1,BTC,5000,,,,Coinbase,2026-03-01T16:00:00Z,',
    ], [
        '2026,Coinbase,BTC,2026-03-01,0.200000008,10000,2000,,,Yes',
        '2026,Coinbase,BTC,2026-03-01,0.300000008,15000,3000,,,Yes',
    ])
    assert tax.integrity_failures == []
    assert _status(tax).count(('Match', '')) == 2
    assert _status(tax).count(('Not on 1099-DA', '')) == 1


def test_rejected_day_does_not_partially_change_tax_events(tmp_path):
    tax = _run(tmp_path, [
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-03-01T12:00:00Z,',
        'Trade,20000,USD,,0.4,BTC,20000,,,,Coinbase,2026-03-01T14:00:00Z,',
        'Trade,20000,USD,,0.4,BTC,20000,,,,Coinbase,2026-03-01T16:00:00Z,',
    ], [
        '2026,Coinbase,BTC,2026-03-01,0.2,10000,1000,,,Yes',
        '2026,Coinbase,BTC,2026-03-01,0.4,20000,6000,,,Yes',
        '2026,Coinbase,BTC,2026-03-01,0.4,20000,,,,No',
    ])
    assert tax.integrity_failures
    # The first row is individually identifiable, but the rest of the day is
    # ambiguous. Even that first event must retain its original records figures.
    assert all(box == 'L' and not code for box, _, _, code, _, _ in _lines(tax))
    assert _lines(tax)[0][2] == '2000.00'
    assert not any(line.status in ('Match', 'Adjusted') for line in tax.reconciliation.lines)


def test_missing_reported_basis_is_not_downgraded_to_unreported(tmp_path):
    tax = _run(tmp_path, ['Trade,50000,USD,,1,BTC,50000,,,,Coinbase,2026-03-01T12:00:00Z,'],
               ['2026,Coinbase,BTC,2026-03-01,1,50000,,,,Yes'])
    assert tax.integrity_failures
    assert _lines(tax)[0][0] == 'L'
    assert any('basis value is missing' in line.note for line in tax.reconciliation.lines)


def test_ambiguous_same_category_rows_do_not_net_away_basis_adjustments(tmp_path):
    tax = _run(tmp_path, [
        'Trade,25000,USD,,0.5,BTC,25000,,,,Coinbase,2026-03-01T12:00:00Z,',
        'Trade,25000,USD,,0.5,BTC,25000,,,,Coinbase,2026-03-01T14:00:00Z,',
    ], [
        '2026,Coinbase,BTC,2026-03-01,0.5,25000,6000,,,Yes',
        '2026,Coinbase,BTC,2026-03-01,0.5,25000,4000,,,Yes',
    ])
    assert tax.integrity_failures
    assert all(box == 'L' for box, *_ in _lines(tax))


def test_split_sale_with_mixed_basis_categories_is_rejected(tmp_path):
    tax = _run(tmp_path, ['Trade,50000,USD,,1,BTC,50000,,,,Coinbase,2026-03-01T12:00:00Z,'],
               [
                   '2026,Coinbase,BTC,2026-03-01,0.4,20000,4000,,,Yes',
                   '2026,Coinbase,BTC,2026-03-01,0.6,30000,,,,No',
               ])
    assert tax.integrity_failures
    assert _lines(tax)[0][0] == 'L'
    assert any('mixed basis-reporting categories' in line.note
               for line in tax.reconciliation.lines)


def test_split_sale_with_uniform_reported_basis_preserves_broker_totals(tmp_path):
    tax = _run(tmp_path, ['Trade,50000,USD,,1,BTC,50000,,,,Coinbase,2026-03-01T12:00:00Z,'],
               [
                   '2026,Coinbase,BTC,2026-03-01,0.4,20000,5000,,,Yes',
                   '2026,Coinbase,BTC,2026-03-01,0.6,30000,7000,,,Yes',
               ])
    assert tax.integrity_failures == []
    assert _lines(tax) == [('J', '50000.00', '12000.00', 'B', '2000.00', '40000.00')]


def test_overlapping_tolerance_edges_resolve_unique_assignment_in_any_order(tmp_path):
    sales = [
        'Trade,10000,USD,,0.2,BTC,10000,,,,Coinbase,2026-03-01T12:00:00Z,',
        'Trade,12000,USD,,0.200000018,BTC,12000,,,,Coinbase,2026-03-01T14:00:00Z,',
        'Trade,5000,USD,,0.1,BTC,5000,,,,Coinbase,2026-03-01T16:00:00Z,',
    ]
    forms = [
        '2026,Coinbase,BTC,2026-03-01,0.200000009,10000,2000,,,Yes',
        '2026,Coinbase,BTC,2026-03-01,0.200000020,12000,,,,No',
    ]
    for ordered in (forms, forms[::-1]):
        tax = _run(tmp_path, sales, ordered)
        assert tax.integrity_failures == []
        assert [box for box, *_ in _lines(tax)] == ['J', 'K', 'L']
        assigned = {line.forms[0].quantity: line.sales[0].quantity
                    for line in tax.reconciliation.lines if line.forms}
        assert assigned == {Decimal('0.200000009'): Decimal('0.2'),
                            Decimal('0.200000020'): Decimal('0.200000018')}


def test_individual_plan_agrees_with_exhaustive_injective_matching():
    # Independent brute-force oracle: every injective row assignment for small
    # tolerance-overlap graphs. Exactly one solution may produce a plan.
    use_us_rules()
    date = datetime.datetime(2026, 3, 1, 12, tzinfo=datetime.timezone.utc)
    quantities = [Decimal(1) + offset * QUANTITY_TOLERANCE
                  for offset in (Decimal(0), Decimal('0.75'), Decimal('1.5'))]
    for form_count, sale_count in product(range(1, 4), repeat=2):
        for form_quantities in product(quantities, repeat=form_count):
            forms = [Form1099DA(str(i), 2026, 'Coinbase', 'BTC', date.date(), q,
                                Decimal(1), None, None, None, False)
                     for i, q in enumerate(form_quantities)]
            for sale_quantities in product(quantities, repeat=sale_count):
                sales = [Sale([SimpleNamespace(
                    wallet='Coinbase', asset='BTC', date=date, quantity=q,
                    proceeds=Decimal(1), sale_fees=Decimal(0), basis=Decimal(1),
                    disposal_type='First-In, First-Out', transferred_in=False,
                    holding_period='Long-term')]) for q in sale_quantities]
                solutions = [order for order in permutations(sales, form_count)
                             if all(abs(form.quantity - sale.quantity) <= QUANTITY_TOLERANCE
                                    for form, sale in zip(forms, order))]
                plan = Reconciliation._individual_plan(forms, sales)
                if len(solutions) != 1:
                    assert plan is None
                else:
                    assert plan is not None
                    assignment = {fs[0]: ss[0] for fs, ss in plan}
                    assert tuple(assignment[f] for f in forms) == solutions[0]


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
