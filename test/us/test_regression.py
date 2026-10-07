# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026
"""Pin results captured from the universal-FIFO calculator at 9110f88.

US: per-wallet rules only apply from 2025, so earlier years must not move.
UK: Section 104 pooling is untouched by the US work, so every year must match.
"""

import json
import os

from bittytax.config import config
from conftest import FIXTURES, gains_rows, run_tax, use_uk_rules, use_us_rules

GOLDEN = os.path.join(FIXTURES, 'golden')


def _golden(name):
    with open(os.path.join(GOLDEN, name), encoding='utf-8') as golden_file:
        return json.load(golden_file)


def test_us_pre_2025_gains_unchanged():
    use_us_rules()
    tax = run_tax(os.path.join(FIXTURES, 'multi_wallet.csv'), config.TAX_RULES_US_INDIVIDUAL)
    expected = [row for row in _golden('us_multi_wallet.json') if row[0] < 2025]
    assert gains_rows(tax, before_year=2025) == expected


def test_uk_gains_unchanged():
    use_uk_rules()
    tax = run_tax(os.path.join(GOLDEN, 'multi_wallet_gbp.csv'), config.TAX_RULES_UK_INDIVIDUAL)
    assert gains_rows(tax) == _golden('uk_multi_wallet.json')
