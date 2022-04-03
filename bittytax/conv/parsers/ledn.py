# -*- coding: utf-8 -*-
# (c) Nano Nano Ltd 2021
# (c) Steven Mirabito 2022

import sys

from colorama import Fore, Back

from ..out_record import TransactionOutRecord
from ..dataparser import DataParser
from ..exceptions import UnexpectedTypeError

WALLET = "Ledn"

def parse_ledn(data_row, parser, **kwargs):
    row_dict = data_row.row_dict
    data_row.timestamp = DataParser.parse_timestamp(row_dict['Posted Date'])

    if row_dict['Status'] != "Confirmed" and not kwargs['unconfirmed']:
        sys.stderr.write("%srow[%s] %s\n" % (
            Fore.YELLOW, parser.in_header_row_num + data_row.line_num, data_row))
        sys.stderr.write("%sWARNING%s Skipping unconfirmed transaction, "
                         "use the [-uc] option to include it\n" % (
                             Back.YELLOW+Fore.BLACK, Back.RESET+Fore.YELLOW))
        return

    if row_dict['Source'] == "Deposit":
        data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_DEPOSIT,
                                                 data_row.timestamp,
                                                 buy_quantity=row_dict['Amount'],
                                                 buy_asset=row_dict['Currency'],
                                                 fee_quantity=row_dict['Ledn Fee Amount'],
                                                 fee_asset=row_dict['Fee Currency'],
                                                 wallet=WALLET)
    elif row_dict['Source'] == "Withdrawal":
        data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_WITHDRAWAL,
                                                 data_row.timestamp,
                                                 sell_quantity=row_dict['Amount'],
                                                 sell_asset=row_dict['Currency'],
                                                 fee_quantity=row_dict['Ledn Fee Amount'],
                                                 fee_asset=row_dict['Fee Currency'],
                                                 wallet=WALLET)
    elif row_dict['Source'] == "Interest":
        data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_INTEREST,
                                                 data_row.timestamp,
                                                 buy_quantity=row_dict['Amount'],
                                                 buy_asset=row_dict['Currency'],
                                                 fee_quantity=row_dict['Ledn Fee Amount'],
                                                 fee_asset=row_dict['Fee Currency'],
                                                 wallet=WALLET)
    else:
        raise UnexpectedTypeError(parser.in_header.index('Source'), 'Source', row_dict['Source'])

DataParser(DataParser.TYPE_SAVINGS,
           "Ledn",
           ['Posted Date', 'Source', 'Amount', 'Type', 'Currency', 'Ledn Fee Amount',
            'Fee Currency', 'Status', 'Blockchain', 'Txn ID', 'Txn Hash', 'Direction of funds'],
           row_handler=parse_ledn)
