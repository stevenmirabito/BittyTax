# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2022

from ..out_record import TransactionOutRecord
from ..dataparser import DataParser
from ..exceptions import UnexpectedTypeError


# Documentation: https://docs.stake.tax/export-csv-formats/default-stake.tax-csv
def parse_staketax(data_row, parser, **_kwargs):
    row_dict = data_row.row_dict
    data_row.timestamp = DataParser.parse_timestamp(row_dict['timestamp'])

    if row_dict['tx_type'] == 'STAKING':
        data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_STAKING,
                                                 data_row.timestamp,
                                                 buy_quantity=row_dict['received_amount'],
                                                 buy_asset=row_dict['received_currency'],
                                                 fee_quantity=row_dict['fee'] if row_dict['fee'] else None,
                                                 fee_asset=row_dict['fee_currency'],
                                                 wallet=row_dict['wallet_address'])
    elif row_dict['tx_type'] == 'AIRDROP':
        data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_AIRDROP,
                                                 data_row.timestamp,
                                                 buy_quantity=row_dict['received_amount'],
                                                 buy_asset=row_dict['received_currency'],
                                                 fee_quantity=row_dict['fee'] if row_dict['fee'] else None,
                                                 fee_asset=row_dict['fee_currency'],
                                                 wallet=row_dict['wallet_address'])
    elif row_dict['tx_type'] in ['TRADE', 'LP_DEPOSIT', 'LP_WITHDRAW']:
        data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_TRADE,
                                                 data_row.timestamp,
                                                 buy_quantity=row_dict['received_amount'],
                                                 buy_asset=row_dict['received_currency'],
                                                 sell_quantity=row_dict['sent_amount'],
                                                 sell_asset=row_dict['sent_currency'],
                                                 fee_quantity=row_dict['fee'] if row_dict['fee'] else None,
                                                 fee_asset=row_dict['fee_currency'],
                                                 wallet=row_dict['wallet_address'])
    elif row_dict['tx_type'] == 'TRANSFER':
        if row_dict['received_amount'] != '':
            # Deposit
            data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_DEPOSIT,
                                                     data_row.timestamp,
                                                     buy_quantity=row_dict['received_amount'],
                                                     buy_asset=row_dict['received_currency'],
                                                     fee_quantity=row_dict['fee'] if row_dict['fee'] else None,
                                                     fee_asset=row_dict['fee_currency'],
                                                     wallet=row_dict['wallet_address'])
        else:
            # Withdrawal
            data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_WITHDRAWAL,
                                                     data_row.timestamp,
                                                     sell_quantity=row_dict['sent_amount'],
                                                     sell_asset=row_dict['sent_currency'],
                                                     fee_quantity=row_dict['fee'] if row_dict['fee'] else None,
                                                     fee_asset=row_dict['fee_currency'],
                                                     wallet=row_dict['wallet_address'])
    elif row_dict['tx_type'] == 'SPEND':
        data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_SPEND,
                                                 data_row.timestamp,
                                                 sell_quantity=row_dict['sent_amount'],
                                                 sell_asset=row_dict['sent_currency'],
                                                 fee_quantity=row_dict['fee'] if row_dict['fee'] else None,
                                                 fee_asset=row_dict['fee_currency'],
                                                 wallet=row_dict['wallet_address'])
    elif row_dict['tx_type'] in ['BORROW', 'REPAY'] or row_dict['tx_type'].startswith('_'):
        # Skip loan operations and non-base transaction types (stake/unstake, etc.)
        # which are not disposals, but record any fees
        if row_dict['fee']:
            data_row.t_record = TransactionOutRecord(TransactionOutRecord.TYPE_SPEND,
                                                     data_row.timestamp,
                                                     sell_quantity=row_dict['fee'],
                                                     sell_asset=row_dict['fee_currency'],
                                                     wallet=row_dict['wallet_address'])
    else:
        raise UnexpectedTypeError(parser.in_header.index('tx_type'), 'tx_type', row_dict['tx_type'])


DataParser(DataParser.TYPE_ACCOUNTING,
           "StakeTax",
           ['timestamp', 'tx_type', 'received_amount', 'received_currency', 'sent_amount', 'sent_currency',
            'fee', 'fee_currency', 'comment', 'txid', 'url', 'exchange', 'wallet_address'],
           worksheet_name="StakeTax",
           row_handler=parse_staketax)
