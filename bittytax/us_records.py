# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026
"""Workbook sheets which hold US records other than transactions.

The workbook is the single source of truth, alongside the transaction sheets it can hold:

  'Identifications'  units identified before each sale (Treas. Reg. 1.1012-1(j)(3))
  'Standing Orders'  which lots to sell first when units aren't identified
  '1099-DA ...'      a broker's Form 1099-DA rows, one sheet per broker, all years

"Timestamp" names a transaction, so it's read like the transaction sheets (no zone means
UTC). "Recorded At" and "Effective From" are when you made the record, a value with no zone
is local time (us_timezone).
"""

from decimal import Decimal, InvalidOperation

import dateutil.parser

from .config import config
from .tax.lot_selection import METHODS, Identification, StandingOrder


class SheetError(Exception):
    def __init__(self, sheet, row_num, message):
        super(SheetError, self).__init__("'%s' row %d: %s" % (sheet, row_num, message))


def parse_timestamp(value, default_tz):
    parsed = dateutil.parser.parse(value, tzinfos=config.TZ_INFOS)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed.astimezone(config.TZ_LOCAL)


def parse_decimal(value, required=True):
    value = (value or '').replace(',', '').replace('$', '').strip()
    if not value:
        if required:
            raise ValueError("value is required")
        return None
    return Decimal(value)


def parse_bool(value):
    value = (value or '').strip().lower()
    if value in ('', '0', '0.0', 'no', 'n', 'false'):
        return False
    if value in ('1', '1.0', 'yes', 'y', 'true', 'x'):
        return True
    raise ValueError("'%s' is not yes/no" % value)


def parse_term(value):
    value = (value or '').strip().lower()
    if not value:
        return None
    if value.startswith('s'):
        return 'Short-term'
    if value.startswith('l'):
        return 'Long-term'
    raise ValueError("Term '%s' must be Short-term or Long-term" % value)


class Form1099DA(object):
    """One sale as a broker reported it on Form 1099-DA."""

    def __init__(self, source, form_year, wallet, asset, date_sold, quantity, proceeds,
                 cost_basis, date_acquired, term, basis_reported):
        self.source = source
        self.form_year = form_year
        self.wallet = wallet
        self.asset = asset
        self.date_sold = date_sold
        self.quantity = quantity
        self.proceeds = proceeds
        self.cost_basis = cost_basis
        self.date_acquired = date_acquired
        self.term = term
        self.basis_reported = basis_reported


class UsRecords(object):
    IDENTIFICATIONS = 'Identifications'
    STANDING_ORDERS = 'Standing Orders'
    FORM_1099DA = '1099-DA'

    HEADERS = {
        IDENTIFICATIONS: ['Wallet', 'Timestamp', 'Asset', 'Quantity', 'Lot ID', 'Recorded At'],
        STANDING_ORDERS: ['Wallet', 'Method', 'Effective From'],
        FORM_1099DA: ['Form Year', 'Wallet', 'Asset', 'Date Sold', 'Quantity', 'Proceeds',
                      'Cost Basis', 'Date Acquired', 'Term', 'Basis Reported'],
    }

    def __init__(self):
        self.identifications = {}
        self.standing_orders = []
        self.forms_1099da = []

    @classmethod
    def sheet_kind(cls, name):
        if name.strip().lower() == cls.IDENTIFICATIONS.lower():
            return cls.IDENTIFICATIONS
        if name.strip().lower() == cls.STANDING_ORDERS.lower():
            return cls.STANDING_ORDERS
        if name.strip().upper().startswith(cls.FORM_1099DA):
            return cls.FORM_1099DA
        return None

    def parse_sheet(self, kind, sheet, rows):
        """Parse a sheet's rows (first row is the header), returns a list of SheetErrors."""
        if not rows:
            return []

        expected = self.HEADERS[kind]
        header = [str(cell).strip() for cell in rows[0][:len(expected)]]
        if [h.lower() for h in header] != [h.lower() for h in expected]:
            return [SheetError(sheet, 1, "header must be: %s" % ', '.join(expected))]

        errors = []
        parse_row = {self.IDENTIFICATIONS: self._identification,
                     self.STANDING_ORDERS: self._standing_order,
                     self.FORM_1099DA: self._form_1099da}[kind]

        for row_num, row in enumerate(rows[1:], start=2):
            row = (list(row) + [''] * len(expected))[:len(expected)]
            if all(not str(cell).strip() for cell in row):
                continue
            values = dict(zip(expected, (str(cell).strip() for cell in row)))
            try:
                parse_row(sheet, row_num, values)
            except (ValueError, InvalidOperation, OverflowError) as e:
                errors.append(SheetError(sheet, row_num, e))
        return errors

    def _identification(self, sheet, row_num, row):
        ident = Identification("'%s' row %d" % (sheet, row_num), row['Wallet'],
                               parse_timestamp(row['Timestamp'], config.TZ_UTC),
                               row['Asset'], parse_decimal(row['Quantity']), row['Lot ID'],
                               parse_timestamp(row['Recorded At'], config.TZ_LOCAL))
        if not ident.lot_id:
            raise ValueError("Lot ID is required")
        self.identifications.setdefault(ident.key(), []).append(ident)

    def _standing_order(self, sheet, row_num, row):
        method = row['Method'].upper()
        if method not in METHODS:
            raise ValueError("unknown method '%s', use one of %s" % (
                row['Method'], ', '.join(sorted(METHODS))))
        self.standing_orders.append(StandingOrder(
            row['Wallet'] or '*', method, parse_timestamp(row['Effective From'],
                                                          config.TZ_LOCAL)))

    def _form_1099da(self, sheet, row_num, row):
        acquired = row['Date Acquired']
        if acquired and acquired.upper() != 'VARIOUS':
            acquired = dateutil.parser.parse(acquired).date()
        else:
            acquired = None

        if not row['Wallet'] or not row['Asset']:
            raise ValueError("Wallet and Asset are required")

        self.forms_1099da.append(Form1099DA(
            "'%s' row %d" % (sheet, row_num),
            int(float(row['Form Year'])),
            row['Wallet'], row['Asset'],
            dateutil.parser.parse(row['Date Sold']).date(),
            parse_decimal(row['Quantity']),
            parse_decimal(row['Proceeds']),
            parse_decimal(row['Cost Basis'], required=False),
            acquired,
            parse_term(row['Term']),
            parse_bool(row['Basis Reported'])))
