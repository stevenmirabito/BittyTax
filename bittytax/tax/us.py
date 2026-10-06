# -*- coding: utf-8 -*-
# (c) Nano Nano Ltd 2019
# (c) Steven Mirabito 2022

import sys
import copy
from decimal import Decimal

from colorama import Fore
from tqdm import tqdm

from ..config import config
from ..transactions import Buy, Sell
from ..holdings import Holdings
from .base import TaxCalculator, TaxEventCapitalGains, TaxRule

PRECISION = Decimal('0.00')

class TaxCalculatorUS(TaxCalculator):
    DISPOSAL_FIFO = 'First-In, First-Out'

    INCOME_TYPES = (Buy.TYPE_MINING, Buy.TYPE_STAKING, Buy.TYPE_DIVIDEND, Buy.TYPE_INTEREST,
                    Buy.TYPE_INCOME, Buy.TYPE_AIRDROP)

    def calculate(self, skip_integrity_check=False):
        self.prepare_transactions()
        self.match_sell(RULE_FIFO)
        self.process_holdings(skip_integrity_check)

    def prepare_transactions(self):
        transactions = copy.deepcopy(self.transactions)
        buy_transactions = []
        sell_transactions = []

        if config.debug:
            print("%sprepare transactions" % Fore.CYAN)

        for t in tqdm(transactions,
                      unit='t',
                      desc="%sprepare transactions%s" % (Fore.CYAN, Fore.GREEN),
                      disable=bool(config.debug or not sys.stdout.isatty())):
            if isinstance(t, Buy) and t.is_crypto() and t.acquisition and t.t_type:
                buy_transactions.append(t)
            elif isinstance(t, Sell) and t.is_crypto() and t.disposal and t.t_type:
                sell_transactions.append(t)
            else:
                self.other_transactions.append(t)

        self.buys_ordered = sorted(buy_transactions)
        self.sells_ordered = sorted(sell_transactions)

        if config.debug:
            print("%sprepare: total transactions=%d" % (Fore.CYAN, len(self.all_transactions())))

    def process_disposal(self, t, cost, fees):
        if t.t_type in self.NO_GAIN_NO_LOSS_TYPES:
            # Change proceeds to make sure it balances
            t.proceeds = cost.quantize(PRECISION) + (fees + (t.fee_value or
                                                             Decimal(0))).quantize(PRECISION)
            t.proceeds_fixed = True
            tax_event = TaxEventCapitalGains(self.DISPOSAL_NO_GAIN_NO_LOSS,
                                             None, t, cost, fees + (t.fee_value or Decimal(0)))
        else:
            tax_event = TaxEventCapitalGains(self.DISPOSAL_FIFO,
                                             None, t, cost, fees + (t.fee_value or Decimal(0)))

        self.tax_events[self.which_tax_year(tax_event.date)].append(tax_event)
        if config.debug:
            print("%sholdings:   %s" % (Fore.CYAN, tax_event))


def _match_fifo(sell, buy):
    return buy.timestamp.date() <= sell.timestamp.date()


RULE_FIFO = TaxRule(
    TaxCalculatorUS.DISPOSAL_FIFO, matcher=_match_fifo)
