# -*- coding: utf-8 -*-
# (c) Nano Nano Ltd 2019

import sys
from decimal import Decimal

from colorama import Fore, Back

from ...config import config
from ..out_record import TransactionOutRecord
from ..dataparser import DataParser
from ..datarow import DataRow
from ..exceptions import DataRowError, UnexpectedTypeError

WALLET = "Uphold"

def preprocess_uphold_v2(data_rows, parser):
    offset = 0
    rows = []

    for data_row in data_rows:
        data_row.line_num += offset
        row_dict = data_row.row_dict
        if row_dict['Type'] == "in" and row_dict['Origin Currency'] != row_dict['Destination Currency']:
            sys.stderr.write("%spreprocess: row[%s] Inserting buy after deposit %s\n"
                             % (Fore.YELLOW, parser.in_header_row_num + data_row.line_num, data_row))

            deposit_dict = row_dict.copy()
            deposit_dict.update({
                'Destination Currency': row_dict['Origin Currency'],
                'Destination Amount': row_dict['Origin Amount'],
            })
            deposit_row = DataRow(data_row.line_num, deposit_dict.values(), parser.in_header)
            rows.append(deposit_row)

            trade_dict = row_dict.copy()
            trade_dict.update({
                'Origin': row_dict['Destination'],
                'Type': 'transfer',
            })
            trade_row = DataRow(data_row.line_num + 1, trade_dict.values(), parser.in_header)
            rows.append(trade_row)

            offset += 1
        elif row_dict['Type'] == "out" and row_dict['Origin Currency'] != row_dict['Destination Currency']:
            sys.stderr.write("%spreprocess: row[%s] Inserting sell before withdrawal %s\n"
                             % (Fore.YELLOW, parser.in_header_row_num + data_row.line_num, data_row))

            trade_dict = row_dict.copy()
            trade_dict.update({
                'Destination': row_dict['Origin'],
                'Type': 'transfer',
            })
            trade_row = DataRow(data_row.line_num, trade_dict.values(), parser.in_header)
            rows.append(trade_row)

            withdrawal_dict = row_dict.copy()
            withdrawal_dict.update({
                'Origin Currency': row_dict['Destination Currency'],
                'Origin Amount': row_dict['Destination Amount'],
            })
            withdrawal_row = DataRow(data_row.line_num + 1, withdrawal_dict.values(), parser.in_header)
            rows.append(withdrawal_row)

            offset += 1
        else:
            rows.append(data_row)

    data_rows[:] = rows

def parse_uphold_v2_row(data_row, parser, **kwargs):
    row_dict = data_row.row_dict
    data_row.timestamp = DataParser.parse_timestamp(row_dict['Date'])

    if row_dict['Status'] != "completed" and not kwargs['unconfirmed']:
        sys.stderr.write("%srow[%s] %s\n" % (
            Fore.YELLOW, parser.in_header_row_num + data_row.line_num, data_row))
        sys.stderr.write("%sWARNING%s Skipping unconfirmed transaction, "
                         "use the [-uc] option to include it\n" % (
                             Back.YELLOW+Fore.BLACK, Back.RESET+Fore.YELLOW))
        return

    if row_dict['Type'] == "in":
        data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_DEPOSIT,
                                                 data_row.timestamp,
                                                 buy_quantity=row_dict['Origin Amount'],
                                                 buy_asset=row_dict['Origin Currency'],
                                                 fee_quantity=row_dict['Fee Amount']
                                                 if row_dict['Fee Amount'] else None,
                                                 fee_asset=row_dict['Fee Currency'],
                                                 wallet=WALLET)
    elif row_dict['Type'] == "out":
        data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_WITHDRAWAL,
                                                 data_row.timestamp,
                                                 sell_quantity=row_dict['Destination Amount'],
                                                 sell_asset=row_dict['Destination Currency'],
                                                 fee_quantity=row_dict['Fee Amount']
                                                 if row_dict['Fee Amount'] else None,
                                                 fee_asset=row_dict['Fee Currency'],
                                                 wallet=WALLET)
    elif row_dict['Type'] == "transfer":
        data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_TRADE,
                                                 data_row.timestamp,
                                                 buy_quantity=row_dict['Destination Amount'],
                                                 buy_asset=row_dict['Destination Currency'],
                                                 sell_quantity=row_dict['Origin Amount'],
                                                 sell_asset=row_dict['Origin Currency'],
                                                 wallet=WALLET)
    else:
        raise UnexpectedTypeError(parser.in_header.index('Type'), 'Type', row_dict['Type'])

def parse_uphold_v2(data_rows, parser, **kwargs):
    preprocess_uphold_v2(data_rows, parser)
    for line_num, data_row in enumerate(data_rows):
        if config.debug:
            sys.stderr.write("%sconv: row[%s] %s\n" % (
                Fore.YELLOW, parser.in_header_row_num + data_row.line_num, data_row))

        if data_row.parsed:
            continue

        try:
            parse_uphold_v2_row(data_row, parser, **kwargs)
        except DataRowError as e:
            data_row.failure = e

def parse_uphold_v1(data_row, parser, **_kwargs):
    row_dict = data_row.row_dict
    data_row.timestamp = DataParser.parse_timestamp(row_dict['date'])

    if row_dict['type'] in ("deposit", "in"):
        data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_DEPOSIT,
                                                 data_row.timestamp,
                                                 buy_quantity=row_dict['origin_amount'],
                                                 buy_asset=row_dict['origin_currency'],
                                                 fee_quantity=
                                                     Decimal(row_dict['origin_amount']) - \
                                                     Decimal(row_dict['destination_amount']),
                                                 fee_asset=row_dict['origin_currency'],
                                                 wallet=WALLET)
    elif row_dict['type'] in ("withdrawal", "out"):
        # Check if origin and destination currency are the same
        if row_dict['origin_currency'] == row_dict['destination_currency']:
            data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_WITHDRAWAL,
                                                     data_row.timestamp,
                                                     sell_quantity=row_dict['destination_amount'],
                                                     sell_asset=row_dict['origin_currency'],
                                                     fee_quantity= \
                                                         Decimal(row_dict['origin_amount']) - \
                                                         Decimal(row_dict['destination_amount']),
                                                     fee_asset=row_dict['origin_currency'],
                                                     wallet=WALLET)
        else:
            data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_WITHDRAWAL,
                                                     data_row.timestamp,
                                                     sell_quantity=row_dict['origin_amount'],
                                                     sell_asset=row_dict['origin_currency'],
                                                     wallet=WALLET)
    elif row_dict['type'] == "transfer":
        data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_TRADE,
                                                 data_row.timestamp,
                                                 buy_quantity=row_dict['destination_amount'],
                                                 buy_asset=row_dict['destination_currency'],
                                                 sell_quantity=row_dict['origin_amount'],
                                                 sell_asset=row_dict['origin_currency'],
                                                 wallet=WALLET)
    else:
        raise UnexpectedTypeError(parser.in_header.index('type'), 'type', row_dict['type'])

DataParser(DataParser.TYPE_EXCHANGE,
           "Uphold",
           ['Date', 'Destination', 'Destination Amount', 'Destination Currency', 'Fee Amount',
            'Fee Currency', 'Id', 'Origin', 'Origin Amount', 'Origin Currency', 'Status', 'Type'],
           worksheet_name="Uphold",
           all_handler=parse_uphold_v2)

DataParser(DataParser.TYPE_EXCHANGE,
           "Uphold",
           ['date', 'id', 'type', "value_in_%s" % config.ccy, "commission_in_%s" % config.ccy, 'pair',
            'rate', 'origin_currency', 'origin_amount', 'origin_commission', 'destination_currency',
            'destination_amount', 'destination_commission'],
           worksheet_name="Uphold",
           row_handler=parse_uphold_v1)
