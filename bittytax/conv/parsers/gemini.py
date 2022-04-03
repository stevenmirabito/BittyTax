# -*- coding: utf-8 -*-
# (c) Nano Nano Ltd 2020
# (c) Steven Mirabito 2022

import sys
from decimal import Decimal

from colorama import Fore

from ...config import config
from ..out_record import TransactionOutRecord
from ..dataparser import DataParser
from ..datarow import DataRow
from ..exceptions import DataRowError, UnexpectedTypeError, UnexpectedTradingPairError

WALLET = "Gemini"

QUOTE_ASSETS = ['BCH', 'BTC', 'DAI', 'ETH', 'EUR', 'FIL', 'GBP', 'GUSD', 'LTC', 'SGD', 'USD']

def split_trading_pair(trading_pair):
    for quote_asset in sorted(QUOTE_ASSETS, reverse=True):
        if trading_pair.endswith(quote_asset):
            return trading_pair[:-len(quote_asset)], quote_asset

    return None, None

def preprocess_gemini(data_rows, parser):
    offset = 0
    rows = []

    for data_row in data_rows:
        data_row.line_num += offset
        row_dict = data_row.row_dict
        if row_dict['Date'] == '':
            # Skip any blank lines (e.g. totals row)
            sys.stderr.write("%spreprocess: row[%s] Skipping blank row %s\n"
                             % (Fore.YELLOW, parser.in_header_row_num + data_row.line_num, data_row))
            offset -= 1
        elif row_dict['Type'] == 'Debit' and 'Gemini dollars' in row_dict['Specification']:
            sys.stderr.write("%spreprocess: row[%s] Found legacy GUSD withdrawal, inserting buy before withdrawal %s\n"
                             % (Fore.YELLOW, parser.in_header_row_num + data_row.line_num, data_row))

            amount = abs(Decimal(row_dict['USD Amount USD']))

            trade_dict = row_dict.copy()
            trade_dict.update({
                'Type': 'Buy',
                'Symbol': 'GUSDUSD',
                'Specification': 'Web',
                'Liquidity Indicator': 'Taker',
                'GUSD Amount GUSD': '%s' % amount,
                'GUSD Balance GUSD': '%s' % (Decimal(trade_dict['GUSD Balance GUSD']) + amount),
                'Order Date': trade_dict['Date'],
                'Order Time': trade_dict['Time (UTC)'],
                'Tx Hash': '',
                'Withdrawal Destination': '',
            })
            trade_row = DataRow(data_row.line_num, trade_dict.values(), parser.in_header)
            rows.append(trade_row)

            withdrawal_dict = row_dict.copy()
            withdrawal_dict.update({
                'Symbol': 'GUSD',
                'Specification': 'Withdrawal (GUSD)',
                'USD Amount USD': '',
                'Fee (USD) USD': '',
                'GUSD Amount GUSD': '%s' % amount,
                'Fee (GUSD) GUSD': row_dict['Fee (USD) USD'],
                'GUSD Balance GUSD': '%s' % (Decimal(row_dict['GUSD Balance GUSD']) - amount),
            })
            withdrawal_row = DataRow(data_row.line_num + 1, withdrawal_dict.values(), parser.in_header)
            rows.append(withdrawal_row)
            offset += 1
        else:
            rows.append(data_row)

    data_rows[:] = rows

def parse_gemini_row(data_row, parser, **_kwargs):
    row_dict = data_row.row_dict

    data_row.timestamp = DataParser.parse_timestamp(row_dict['Date'])

    if row_dict['Type'] in ('Credit', 'Debit'):
        asset = row_dict['Symbol']
        amount = abs(Decimal(row_dict['%s Amount %s' % (asset, asset)]))
        fee = row_dict['Fee (%s) %s' % (asset, asset)]
        fee_amount = abs(Decimal(fee)) if fee != '' else None

        if row_dict['Type'] == 'Credit':
            data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_DEPOSIT,
                                                     data_row.timestamp,
                                                     buy_quantity=amount,
                                                     buy_asset=asset,
                                                     fee_quantity=fee_amount,
                                                     fee_asset=asset,
                                                     wallet=WALLET)
        else:
            data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_WITHDRAWAL,
                                                     data_row.timestamp,
                                                     sell_quantity=amount,
                                                     sell_asset=asset,
                                                     fee_quantity=fee_amount,
                                                     fee_asset=asset,
                                                     wallet=WALLET)
    elif row_dict['Type'] in ('Buy', 'Sell'):
        base_asset, quote_asset = split_trading_pair(row_dict['Symbol'])
        if base_asset is None or quote_asset is None:
            raise UnexpectedTradingPairError(parser.in_header.index('Symbol'), 'Symbol', row_dict['Symbol'])

        base_amount = abs(Decimal(row_dict['%s Amount %s' % (base_asset, base_asset)]))
        quote_amount = abs(Decimal(row_dict['%s Amount %s' % (quote_asset, quote_asset)]))
        fee = row_dict['Fee (%s) %s' % (quote_asset, quote_asset)]
        fee_amount = abs(Decimal(fee)) if fee != '' else None

        if row_dict['Type'] == 'Buy':
            data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_TRADE,
                                                     data_row.timestamp,
                                                     buy_quantity=base_amount,
                                                     buy_asset=base_asset,
                                                     sell_quantity=quote_amount,
                                                     sell_asset=quote_asset,
                                                     fee_quantity=fee_amount,
                                                     fee_asset=quote_asset,
                                                     wallet=WALLET)
        else:
            data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_TRADE,
                                                     data_row.timestamp,
                                                     buy_quantity=quote_amount,
                                                     buy_asset=quote_asset,
                                                     sell_quantity=base_amount,
                                                     sell_asset=base_asset,
                                                     fee_quantity=fee_amount,
                                                     fee_asset=quote_asset,
                                                     wallet=WALLET)
    else:
        raise UnexpectedTypeError(parser.in_header.index('type'), 'type', row_dict['type'])

def parse_gemini(data_rows, parser, **kwargs):
    preprocess_gemini(data_rows, parser)
    for line_num, data_row in enumerate(data_rows):
        if config.debug:
            sys.stderr.write("%sconv: row[%s] %s\n" % (
                Fore.YELLOW, parser.in_header_row_num + data_row.line_num, data_row))

        if data_row.parsed:
            continue

        try:
            parse_gemini_row(data_row, parser, **kwargs)
        except DataRowError as e:
            data_row.failure = e

DataParser(DataParser.TYPE_EXCHANGE,
           "Gemini",
           ['Date', 'Time (UTC)', 'Type', 'Symbol', 'Specification', 'Liquidity Indicator',
            'Trading Fee Rate (bps)', DataParser.HEADER_WILDCARD, 'Trade ID', 'Order ID', 'Order Date',
            'Order Time', 'Client Order ID', 'API Session', 'Tx Hash', 'Deposit Destination',
            'Deposit Tx Output', 'Withdrawal Destination', 'Withdrawal Tx Output'],
           all_handler=parse_gemini)
