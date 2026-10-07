# -*- coding: utf-8 -*-
"""Regenerate golden baselines. Only run against the calculator they are meant to pin."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conftest  # noqa: E402
from bittytax.config import config  # noqa: E402

GOLDEN = os.path.join(conftest.FIXTURES, 'golden')


def main():
    os.makedirs(GOLDEN, exist_ok=True)
    src = os.path.join(conftest.FIXTURES, 'multi_wallet.csv')

    conftest.use_us_rules()
    us_rows = conftest.gains_rows(conftest.run_tax(src, config.TAX_RULES_US_INDIVIDUAL))

    uk_src = os.path.join(GOLDEN, 'multi_wallet_gbp.csv')
    with open(src, encoding='utf-8') as f_in, open(uk_src, 'w', encoding='utf-8') as f_out:
        f_out.write(f_in.read().replace('USD', 'GBP'))
    conftest.use_uk_rules()
    uk_rows = conftest.gains_rows(conftest.run_tax(uk_src, config.TAX_RULES_UK_INDIVIDUAL))

    for name, rows in (('us_multi_wallet.json', us_rows), ('uk_multi_wallet.json', uk_rows)):
        with open(os.path.join(GOLDEN, name), 'w', encoding='utf-8') as f:
            json.dump(rows, f, indent=1)
            f.write('\n')


if __name__ == '__main__':
    main()
