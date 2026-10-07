# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

from bittytax.config import config
from bittytax.reconcile_1099da import Reconcile1099DA
from conftest import run_tax, use_us_rules, write_csv


def test_reconcile_1099da(tmp_path):
    use_us_rules(us_standing_orders=[
        {'wallet': 'Coinbase', 'method': 'HIFO', 'effective_from': '2025-01-01'}])
    tax = run_tax(write_csv(tmp_path, [
        'Trade,2,BTC,20000,20000,USD,,,,,Coinbase,2023-01-01T12:00:00Z,',
        'Trade,1,BTC,60000,60000,USD,,,,,Coinbase,2024-01-01T12:00:00Z,',
        'Trade,1,ETH,1000,1000,USD,,,,,Ledger,2023-01-01T12:00:00Z,',
        'Withdrawal,,,,1,ETH,,,,,Ledger,2025-01-10T12:00:00Z,',
        'Deposit,1,ETH,,,,,,,,Coinbase,2025-01-10T12:30:00Z,',
        # HIFO sale: the broker would report FIFO basis
        'Trade,50000,USD,,1,BTC,50000,10,USD,,Coinbase,2025-03-01T12:00:00Z,',
        'Trade,3000,USD,,1,ETH,3000,,,,Coinbase,2025-04-01T12:00:00Z,',
        'Trade,45000,USD,,0.5,BTC,45000,,,,Coinbase,2025-05-01T12:00:00Z,',
        'Trade,30000,USD,,0.5,BTC,30000,,,,Coinbase,2025-06-01T12:00:00Z,',
    ]), config.TAX_RULES_US_INDIVIDUAL)

    form = tmp_path / '1099da.csv'
    form.write_text('\n'.join([
        'Wallet,Asset,Date Sold,Quantity,Proceeds,Cost Basis,Date Acquired',
        'Coinbase,BTC,2025-03-01,1,"$49,990.00","10,000.00",01/01/2023',
        'Coinbase,ETH,2025-04-01,1,3000.00,,',
        'Coinbase,BTC,2025-05-01,0.5,45000.00,,',
        'Coinbase,BTC,2025-07-15,0.1,6000.00,,',
    ]) + '\n', encoding='utf-8')

    results = {(key[1], key[2].isoformat()): (status, why)
               for status, key, _, _, why in Reconcile1099DA(str(form), tax).results}
    assert results[('BTC', '2025-03-01')][0] == 'Basis differs'
    assert 'Highest-In, First-Out (standing order)' in results[('BTC', '2025-03-01')][1]
    assert results[('ETH', '2025-04-01')] == ('Match', '')
    assert results[('BTC', '2025-05-01')] == ('Match', '')
    assert results[('BTC', '2025-06-01')][0] == 'Not on 1099-DA'
    assert results[('BTC', '2025-07-15')][0] == 'Not in records'
