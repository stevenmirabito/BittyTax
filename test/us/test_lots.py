# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

from datetime import datetime
from decimal import Decimal

import pytest

from bittytax.config import config
from bittytax.tax.lots import FifoSelector, Lot, Shortfall, WalletLedger
from conftest import gains_rows, run_tax, use_us_rules, write_csv


def _lot(ledger, wallet, quantity, cost, day):
    lot = Lot('BTC', wallet, Decimal(quantity), Decimal(cost), Decimal(0),
              datetime(2024, 1, day), 'lot%d' % day, ledger.next_seq())
    ledger.add(lot)
    return lot


def test_split_keeps_identity_and_basis():
    ledger = WalletLedger(per_wallet=True)
    lot = _lot(ledger, 'A', '2', '1000', 1)
    fragment = lot.split(Decimal('0.5'))
    assert (fragment.lot_id, fragment.acquired) == (lot.lot_id, lot.acquired)
    assert (fragment.quantity, fragment.cost) == (Decimal('0.5'), Decimal('250'))
    assert (lot.quantity, lot.cost) == (Decimal('1.5'), Decimal('750'))


def test_fifo_within_wallet():
    ledger = WalletLedger(per_wallet=True)
    _lot(ledger, 'A', '1', '100', 1)
    _lot(ledger, 'B', '1', '200', 2)
    _lot(ledger, 'B', '1', '300', 3)
    taken = ledger.remove('B', 'BTC', Decimal('1.5'), FifoSelector())
    assert [(lot.lot_id, lot.quantity) for lot in taken] == [('lot2', 1), ('lot3', Decimal('0.5'))]
    assert ledger.balance('A', 'BTC') == 1


def test_universal_pools_wallets():
    ledger = WalletLedger(per_wallet=False)
    _lot(ledger, 'A', '1', '100', 1)
    _lot(ledger, 'B', '1', '200', 2)
    taken = ledger.remove('B', 'BTC', Decimal('1'), FifoSelector())
    assert [lot.lot_id for lot in taken] == ['lot1']


def test_shortfall_reports_lots_taken():
    ledger = WalletLedger(per_wallet=True)
    _lot(ledger, 'A', '1', '100', 1)
    with pytest.raises(Shortfall) as e:
        ledger.remove('A', 'BTC', Decimal('1.5'), FifoSelector())
    assert e.value.available == 1
    assert [lot.lot_id for lot in e.value.taken] == ['lot1']


def test_buy_later_same_day_is_not_matched_to_earlier_sell(tmp_path):
    use_us_rules()
    path = write_csv(tmp_path, [
        'Trade,1,BTC,10000,10000,USD,,,,,Coinbase,2023-01-01T12:00:00Z,',
        'Trade,30000,USD,,1,BTC,30000,,,,Coinbase,2024-03-01T17:00:00Z,',
        'Trade,1,BTC,29000,29000,USD,,,,,Coinbase,2024-03-01T23:00:00Z,',
    ])
    tax = run_tax(path, config.TAX_RULES_US_INDIVIDUAL)
    assert gains_rows(tax) == [
        [2024, '2024-03-01', 'BTC', 'Coinbase', '1', '10000.00', '0.00', '30000.00', '20000.00',
         '2023-01-01'],
    ]


def test_sell_more_than_held_is_an_integrity_failure(tmp_path):
    use_us_rules()
    path = write_csv(tmp_path, [
        'Trade,1,BTC,10000,10000,USD,,,,,Coinbase,2023-01-01T12:00:00Z,',
        'Trade,30000,USD,,1.5,BTC,30000,,,,Coinbase,2024-03-01T17:00:00Z,',
    ])
    tax = run_tax(path, config.TAX_RULES_US_INDIVIDUAL)
    assert len(tax.integrity_failures) == 1
    rows = gains_rows(tax)
    assert sorted([r[4:6] + r[9:] for r in rows], key=str) == sorted(
        [['1', '10000.00', '2023-01-01'], ['0.5', '0.00', None]], key=str)
