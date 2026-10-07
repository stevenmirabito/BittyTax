# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026

from decimal import Decimal

import pytest

from bittytax.config import config
from bittytax.tax.us import LONG_TERM, SHORT_TERM, TaxEventCapitalGainsUS
from conftest import run_tax, use_us_rules, write_csv

GIFT = 'Gift-Received,1,ETH,{fmv},,,,,,,Wallet,2025-03-01T12:00:00Z,{note}'
SALE = 'Trade,{proceeds},USD,,1,ETH,{proceeds},,,,Wallet,2025-06-01T12:00:00Z,'


def _sale(tmp_path, fmv, proceeds, note):
    use_us_rules()
    tax = run_tax(write_csv(tmp_path, [GIFT.format(fmv=fmv, note=note),
                                       SALE.format(proceeds=proceeds)]),
                  config.TAX_RULES_US_INDIVIDUAL)
    events = [te for events in tax.tax_events.values() for te in events
              if isinstance(te, TaxEventCapitalGainsUS)]
    assert len(events) == 1
    return tax, events[0]


def test_carryover_basis_and_holding_period(tmp_path):
    tax, te = _sale(tmp_path, 3000, 3500, 'donor_basis:1000 donor_acquired:2020-01-15')
    assert (te.cost, te.gain, te.gift_rule) == (Decimal('1000.00'), Decimal('2500.00'), None)
    assert te.acquisition_date.date().isoformat() == '2020-01-15'
    assert te.holding_period == LONG_TERM
    assert tax.integrity_failures == []


@pytest.mark.parametrize('proceeds, rule, cost, gain, term', [
    # FMV 1500 at the gift is below the donor's 2000 basis
    (2500, 'gain', '2000.00', '500.00', LONG_TERM),
    (1000, 'loss', '1500.00', '-500.00', SHORT_TERM),
    (1800, 'no gain or loss', '1800.00', '0', LONG_TERM),
])
def test_dual_basis(tmp_path, proceeds, rule, cost, gain, term):
    _, te = _sale(tmp_path, 1500, proceeds, 'donor_basis:2000 donor_acquired:2020-01-15')
    assert (te.gift_rule, te.cost, te.gain, te.holding_period) == (
        rule, Decimal(cost), Decimal(gain), term)
    if rule == 'loss':
        # The holding period for a loss starts at the gift
        assert te.acquisition_date.date().isoformat() == '2025-03-01'


def test_gift_tax_paid_adds_to_basis(tmp_path):
    # Appreciation 3000 of a 4000 gift, so 3/4 of the 400 gift tax is added
    _, te = _sale(tmp_path, 4000, 5000,
                  'donor_basis:1000 donor_acquired:2020-01-15 gift_tax_paid:400')
    assert te.cost == Decimal('1300.00')


def test_missing_donor_basis_fails_integrity(tmp_path):
    tax, te = _sale(tmp_path, 3000, 3500, 'birthday')
    assert te.cost == Decimal('3000.00')
    assert len(tax.integrity_failures) == 1
    assert 'donor_basis' in tax.integrity_failures[0]
