# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

from decimal import Decimal

from bittytax.audit import AuditRecords
from bittytax.config import config
from conftest import gains_rows, import_csv, run_tax, use_us_rules, write_csv


def _run(tmp_path, rows, **overrides):
    use_us_rules(**overrides)
    path = write_csv(tmp_path, rows)
    tax = run_tax(path, config.TAX_RULES_US_INDIVIDUAL)
    audit = AuditRecords(import_csv(path))
    return tax, audit.compare_wallets(tax.ledger.balances())


def _gains(tax, year=2025):
    """(wallet, quantity, cost, fees, proceeds, acquired) for each disposal in the year."""
    return [tuple(r[3:8]) + (r[9],) for r in gains_rows(tax) if r[0] == year]


def test_disposal_uses_lots_in_the_selling_wallet(tmp_path):
    tax, mismatches = _run(tmp_path, [
        'Trade,1,BTC,10000,10000,USD,,,,,A,2023-01-01T12:00:00Z,',
        'Trade,1,BTC,50000,50000,USD,,,,,B,2024-01-01T12:00:00Z,',
        'Trade,60000,USD,,1,BTC,60000,,,,B,2025-03-01T12:00:00Z,',
    ])
    assert _gains(tax) == [('B', '1', '50000.00', '0.00', '60000.00', '2024-01-01')]
    assert tax.integrity_failures == [] and mismatches == []


def test_pre_cutover_disposals_stay_universal(tmp_path):
    tax, _ = _run(tmp_path, [
        'Trade,1,BTC,10000,10000,USD,,,,,A,2023-01-01T12:00:00Z,',
        'Trade,1,BTC,50000,50000,USD,,,,,B,2024-01-01T12:00:00Z,',
        'Trade,60000,USD,,1,BTC,60000,,,,B,2024-03-01T12:00:00Z,',
    ])
    assert _gains(tax, 2024) == [('B', '1', '10000.00', '0.00', '60000.00', '2023-01-01')]


def test_tracing_builds_opening_lots_per_wallet(tmp_path):
    tax, mismatches = _run(tmp_path, [
        'Trade,1,BTC,10000,10000,USD,,,,,A,2023-01-01T12:00:00Z,',
        'Trade,1,BTC,50000,50000,USD,,,,,B,2024-01-01T12:00:00Z,',
        # Universal FIFO sells A's lot, but the units actually left B
        'Trade,60000,USD,,1,BTC,60000,,,,B,2024-03-01T12:00:00Z,',
        'Trade,70000,USD,,1,BTC,70000,,,,A,2025-03-01T12:00:00Z,',
    ])
    assert [(lot.wallet, lot.quantity, lot.cost) for lot in tax.transition.lots] == [
        ('A', 1, 10000)]
    assert tax.transition.assets['BTC']['basis_shift'] == Decimal(10000) - Decimal(50000)
    assert _gains(tax) == [('A', '1', '10000.00', '0.00', '70000.00', '2023-01-01')]
    assert tax.integrity_failures == [] and mismatches == []


def test_transfer_carries_basis_and_acquisition_date(tmp_path):
    tax, mismatches = _run(tmp_path, [
        'Trade,1,BTC,10000,10000,USD,,10,USD,,Exchange,2023-01-01T12:00:00Z,',
        'Withdrawal,,,,1,BTC,,,,,Exchange,2025-02-01T12:00:00Z,',
        'Deposit,1,BTC,,,,,,,,Ledger,2025-02-01T12:30:00Z,',
        'Trade,1,BTC,40000,40000,USD,,,,,Exchange,2025-02-02T12:00:00Z,',
        'Trade,50000,USD,,1,BTC,50000,,,,Ledger,2025-04-01T12:00:00Z,',
    ])
    assert _gains(tax) == [('Ledger', '1', '10000.00', '10.00', '50000.00', '2023-01-01')]
    lot = [lot for lot in tax.ledger.open_lots()][0]
    assert (lot.wallet, lot.cost) == ('Exchange', 40000)
    assert tax.integrity_failures == [] and mismatches == []


def test_transfer_fee_is_a_disposal_from_the_sender(tmp_path):
    tax, mismatches = _run(tmp_path, [
        'Trade,1,BTC,10000,10000,USD,,,,,Exchange,2023-01-01T12:00:00Z,',
        'Trade,1,BTC,40000,40000,USD,,,,,Ledger,2024-06-01T12:00:00Z,',
        'Withdrawal,,,,0.5,BTC,,0.001,BTC,60,Exchange,2025-02-01T12:00:00Z,',
        'Deposit,0.5,BTC,,,,,,,,Ledger,2025-02-01T12:30:00Z,',
    ])
    assert _gains(tax) == [('Exchange', '0.001', '10.00', '0.00', '60.00', '2023-01-01')]
    assert tax.integrity_failures == [] and mismatches == []


def test_transfer_fee_without_disposal_carries_basis(tmp_path):
    tax, mismatches = _run(tmp_path, [
        'Trade,1,BTC,10000,10000,USD,,,,,Exchange,2023-01-01T12:00:00Z,',
        'Withdrawal,,,,0.5,BTC,,0.1,BTC,,Exchange,2025-02-01T12:00:00Z,',
        'Deposit,0.5,BTC,,,,,,,,Ledger,2025-02-01T12:30:00Z,',
    ], transfer_fee_disposal=False)
    assert _gains(tax) == []
    ledger_lot = [lot for lot in tax.ledger.open_lots() if lot.wallet == 'Ledger'][0]
    assert (ledger_lot.quantity, ledger_lot.cost) == (Decimal('0.5'), Decimal('6000'))
    assert tax.integrity_failures == [] and mismatches == []


def test_deposit_short_of_withdrawal_carries_basis(tmp_path):
    tax, mismatches = _run(tmp_path, [
        'Trade,1,BTC,10000,10000,USD,,,,,Exchange,2023-01-01T12:00:00Z,',
        'Withdrawal,,,,1,BTC,,,,,Exchange,2025-02-01T12:00:00Z,',
        'Deposit,0.999,BTC,,,,,,,,Ledger,2025-02-01T12:30:00Z,',
    ])
    lots = tax.ledger.open_lots()
    assert [(lot.wallet, lot.quantity, lot.cost) for lot in lots] == [
        ('Ledger', Decimal('0.999'), Decimal('10000'))]
    assert len(tax.warnings) == 1
    assert tax.integrity_failures == [] and mismatches == []


def test_transfer_token_overrides_automatic_matching(tmp_path):
    tax, mismatches = _run(tmp_path, [
        'Trade,2,BTC,20000,20000,USD,,,,,Exchange,2023-01-01T12:00:00Z,',
        'Withdrawal,,,,1,BTC,,,,,Exchange,2025-02-01T12:00:00Z,transfer:t1',
        'Deposit,1,BTC,,,,,,,,Wallet1,2025-02-01T12:10:00Z,',
        'Deposit,1,BTC,,,,,,,,Wallet2,2025-02-03T12:00:00Z,arrived late transfer:t1',
        'Withdrawal,,,,1,BTC,,,,,Exchange,2025-02-01T12:05:00Z,',
    ])
    assert tax.ledger.balances() == {('Wallet1', 'BTC'): 1, ('Wallet2', 'BTC'): 1}
    assert tax.integrity_failures == [] and mismatches == []


def test_transfer_across_cutover(tmp_path):
    tax, mismatches = _run(tmp_path, [
        'Trade,1,BTC,10000,10000,USD,,,,,Exchange,2023-01-01T12:00:00Z,',
        'Withdrawal,,,,1,BTC,,,,,Exchange,2024-12-31T23:50:00-08:00,',
        'Deposit,1,BTC,,,,,,,,Ledger,2025-01-01T00:20:00-08:00,',
        'Trade,50000,USD,,1,BTC,50000,,,,Ledger,2025-04-01T12:00:00Z,',
    ])
    assert [lot.wallet for lot in tax.transition.lots] == ['Ledger']
    assert _gains(tax) == [('Ledger', '1', '10000.00', '0.00', '50000.00', '2023-01-01')]
    assert tax.integrity_failures == [] and mismatches == []


def test_unmatched_withdrawal_and_deposit_fail_integrity(tmp_path):
    tax, _ = _run(tmp_path, [
        'Trade,1,BTC,10000,10000,USD,,,,,Exchange,2023-01-01T12:00:00Z,',
        'Withdrawal,,,,1,BTC,,,,,Exchange,2025-02-01T12:00:00Z,',
        'Deposit,1,BTC,,,,,,,,Ledger,2025-02-05T12:00:00Z,',
    ])
    assert len(tax.integrity_failures) == 2
    assert 'no matching Deposit' in tax.integrity_failures[0]
    assert 'no matching Withdrawal' in tax.integrity_failures[1]


def test_deposit_with_declared_basis(tmp_path):
    tax, _ = _run(tmp_path, [
        'Deposit,1,BTC,8000,,,,,,,Ledger,2025-02-05T12:00:00Z,from old wallet acquired:2020-05-01',
        'Trade,50000,USD,,1,BTC,50000,,,,Ledger,2025-04-01T12:00:00Z,',
    ])
    assert _gains(tax) == [('Ledger', '1', '8000.00', '0.00', '50000.00', '2020-05-01')]
    assert tax.integrity_failures == []


def test_audit_detects_wallet_mismatch(tmp_path):
    tax, mismatches = _run(tmp_path, [
        'Trade,1,BTC,10000,10000,USD,,,,,A,2023-01-01T12:00:00Z,',
        'Trade,1,BTC,10000,10000,USD,,,,,B,2023-01-01T12:00:00Z,',
        'Trade,20000,USD,,1.5,BTC,20000,,,,B,2025-02-01T12:00:00Z,',
    ])
    assert any('Insufficient BTC in B' in f for f in tax.integrity_failures)
    assert mismatches == ['B:BTC audit balance -0.5, lots held 0']
