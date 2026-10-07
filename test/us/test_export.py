# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

import csv
import os

from bittytax.config import config
from bittytax.export_us import ExportForm8949
from conftest import FIXTURES, run_tax, use_us_rules


def test_form_8949_export(tmp_path):
    use_us_rules()
    tax = run_tax(os.path.join(FIXTURES, 'multi_wallet.csv'), config.TAX_RULES_US_INDIVIDUAL)
    for year in (2025, 2026):
        tax.calculate_capital_gains(year)

    filename = ExportForm8949(tax.tax_report).write_csv(str(tmp_path / 'f8949.csv'))
    with open(filename, encoding='utf-8') as csv_file:
        rows = list(csv.DictReader(csv_file))

    assert [(r['Tax Year'], r['Part'], r['Box'], r['Description (a)'], r['Date Acquired (b)'],
             r['Date Sold (c)'], r['Proceeds (d)'], r['Cost Basis (e)'], r['Gain or Loss (h)'],
             r['Wallet']) for r in rows] == [
        ('2025', 'I', 'I', '0.20000000 BTC', '04/01/2024', '02/01/2025', '20000.00',
         '13000.00', '7000.00', 'Kraken'),
        ('2025', 'II', 'L', '0.50000000 BTC', '01/10/2021', '06/01/2025', '50000.00',
         '15005.00', '34995.00', 'Coinbase'),
        ('2025', 'II', 'L', '0.20000000 BTC', '06/01/2021', '02/01/2025', '20000.00',
         '7200.00', '12800.00', 'Kraken'),
        ('2025', 'II', 'L', '0.00010000 BTC', '01/10/2021', '03/10/2025', '9.00', '3.00',
         '6.00', 'Ledger'),
        ('2026', 'II', 'L', '3.00000000 ETH', '03/05/2021', '01/15/2026', '9000.00',
         '4501.50', '4498.50', 'Coinbase'),
    ]
    assert rows[0]['Lot ID'] == 'BTC:Kraken:2024-04-01T18:00:00Z'
