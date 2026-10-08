"""The final CLI pool check must use the same US dust threshold as the lot ledger."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bittytax.audit import AuditRecords
from conftest import use_us_rules, use_uk_rules


@pytest.mark.parametrize('balance,held', [
    ('-0.00000000000006', '0'),
    ('-0.000000000000002', '0'),
    ('1.00000000000006', '1'),
    ('-0.000000000001', None),
])
def test_us_pool_check_accepts_only_existing_lot_dust_threshold(balance, held):
    use_us_rules()
    audit = AuditRecords([])
    audit.totals = {'TOKEN': Decimal(balance)}
    holdings = {} if held is None else {'TOKEN': SimpleNamespace(quantity=Decimal(held))}
    assert audit.compare_pools(holdings)
    assert audit.failures == []


@pytest.mark.parametrize('balance,held', [('0.000000000002', '0'), ('0.001', None)])
def test_us_pool_check_still_rejects_real_quantity_differences(balance, held):
    use_us_rules()
    audit = AuditRecords([])
    audit.totals = {'TOKEN': Decimal(balance)}
    holdings = {} if held is None else {'TOKEN': SimpleNamespace(quantity=Decimal(held))}
    assert not audit.compare_pools(holdings)
    assert len(audit.failures) == 1


def test_uk_pool_check_keeps_exact_comparison():
    use_uk_rules()
    audit = AuditRecords([])
    audit.totals = {'TOKEN': Decimal('-0.00000000000006')}
    assert not audit.compare_pools({'TOKEN': SimpleNamespace(quantity=Decimal(0))})
    assert len(audit.failures) == 1
