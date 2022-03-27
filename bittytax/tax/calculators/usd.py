# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2021

import sys
import copy
from decimal import Decimal
from datetime import datetime, timedelta

from colorama import Fore, Back
from tqdm import tqdm

from ...config import config, Currency
from ...transactions import Buy, Sell
from ...holdings import Holdings

from ..taxcalculator import TaxCalculator
from ..taxevent import TaxEventCapitalGains
from ..taxrule import TaxRule


@TaxCalculator.register(Currency.US)
class TaxCalculatorUS(TaxCalculator):
    DISPOSAL_FIFO = 'First-In, First-Out'
    DISPOSAL_NO_GAIN_NO_LOSS = 'No Gain/No Loss'

    @staticmethod
    def which_tax_year(timestamp):
        return timestamp.year

    def calculate(self):
        self.prepare_transactions()
        self.match(RULE_FIFO)
        self.process_holdings()

    def prepare_transactions(self):
        transactions = copy.deepcopy(self.transactions)
        buy_transactions = []
        sell_transactions = []

        for t in tqdm(transactions,
                      unit='t',
                      desc="%sprepare transactions%s" % (
                          Fore.CYAN, Fore.GREEN),
                      disable=bool(config.args.debug or not sys.stdout.isatty())):
            if isinstance(t, Buy) and t.acquisition:
                buy_transactions.append(t)
            elif isinstance(t, Sell) and t.disposal and t.t_type != t.TYPE_GIFT_SPOUSE:
                sell_transactions.append(t)
            else:
                self.other_transactions.append(t)

        self.buys_ordered = sorted(buy_transactions)
        self.sells_ordered = sorted(sell_transactions)

        if config.args.debug:
            print("%sprepare: total transactions=%d" %
                  (Fore.CYAN, len(self.all_transactions())))
    
    def process_holdings(self):
        if config.args.debug:
            print("%sprocess holdings" % Fore.CYAN)

        for t in tqdm(sorted(self.all_transactions()),
                      unit='t',
                      desc="%sprocess holdings%s" % (Fore.CYAN, Fore.GREEN),
                      disable=bool(config.args.debug or not sys.stdout.isatty())):
            if t.asset not in self.holdings:
                self.holdings[t.asset] = Holdings(t.asset)

            if t.matched:
                if config.args.debug:
                    print("%sholdings: //%s <- matched" % (Fore.BLUE, t))
                continue
            elif not config.transfers_include and t.t_type in self.transfer_types:
                if config.args.debug:
                    print("%sholdings: //%s <- transfer" % (Fore.BLUE, t))
                continue
            else:
                if config.args.debug:
                    print("%sholdings: %s" % (Fore.GREEN, t))

            if isinstance(t, Buy):
                self._add_tokens(t)
            elif isinstance(t, Sell):
                self._subtract_tokens(t)

    def _add_tokens(self, t):
        if not t.acquisition:
            cost = fees = Decimal(0)
        else:
            cost = t.cost
            fees = t.fee_value or Decimal(0)

        self.holdings[t.asset].add_tokens(t.quantity, cost, fees,
                                          t.t_type == Buy.TYPE_DEPOSIT)

    def _subtract_tokens(self, t):
        if not t.disposal:
            cost = fees = Decimal(0)
        else:
            if self.holdings[t.asset].quantity:
                cost = self.holdings[t.asset].cost * (t.quantity /
                                                      self.holdings[t.asset].quantity)
                fees = self.holdings[t.asset].fees * (t.quantity /
                                                      self.holdings[t.asset].quantity)
            else:
                # Should never happen, only if incorrect transaction records
                cost = fees = Decimal(0)

        self.holdings[t.asset].subtract_tokens(t.quantity, cost, fees,
                                               t.t_type == Sell.TYPE_WITHDRAWAL)

        if t.disposal:
            if t.t_type == Sell.TYPE_GIFT_SPOUSE:
                # Change proceeds to make sure it balances
                t.proceeds = cost + fees + (t.fee_value or Decimal(0))
                t.proceeds_fixed = True
                tax_event = TaxEventCapitalGains(self.DISPOSAL_NO_GAIN_NO_LOSS,
                                                 None, t, cost, fees + (t.fee_value or Decimal(0)))

                self.tax_events[self._which_tax_year(
                    tax_event.date)].append(tax_event)
                if config.args.debug:
                    print("%sholdings:   %s" % (Fore.CYAN, tax_event))
            else:
                # Should never happen, all other disposals should be matched
                tqdm.write("%sWARNING%s Unmatched disposal of %s on %s, tax liability will be wrong" % (
                    Back.YELLOW+Fore.BLACK, Back.RESET+Fore.YELLOW, t.asset, t.timestamp.strftime(config.DATE_FORMAT)))

            if config.transfers_include and not config.args.skip_integrity:
                self.holdings[t.asset].check_transfer_mismatch()


def _match_fifo(sell, buy):
    return buy.timestamp.date() <= sell.timestamp.date()


RULE_FIFO = TaxRule(
    TaxCalculatorUS.DISPOSAL_FIFO, matcher=_match_fifo)
