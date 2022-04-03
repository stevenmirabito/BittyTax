# -*- coding: utf-8 -*-
# (c) Nano Nano Ltd 2019

import sys
import copy
from decimal import Decimal
from datetime import timedelta

from colorama import Fore
from tqdm import tqdm

from ..config import config
from ..transactions import Buy, Sell
from .base import TaxCalculator, TaxEventCapitalGains, TaxRule

PRECISION = Decimal('0.00')

class TaxCalculatorUK(TaxCalculator):
    DISPOSAL_SAME_DAY = 'Same Day'
    DISPOSAL_TEN_DAY = 'Ten Day'
    DISPOSAL_BED_AND_BREAKFAST = 'Bed & Breakfast'
    DISPOSAL_SECTION_104 = 'Section 104'

    NO_GAIN_NO_LOSS_TYPES = (Sell.TYPE_GIFT_SPOUSE, Sell.TYPE_CHARITY_SENT)

    # These transactions are except from the "same day" & "bnb" rule
    NO_MATCH_TYPES = (Sell.TYPE_GIFT_SPOUSE, Sell.TYPE_CHARITY_SENT, Sell.TYPE_LOST)

    def calculate(self, skip_integrity_check=False):
        self.pool_same_day()
        self.match_sell(RULE_SAME_DAY)

        if self.tax_rules == config.TAX_RULES_UK_INDIVIDUAL:
            self.match_buyback(RULE_BED_AND_BREAKFAST)
        elif self.tax_rules in config.TAX_RULES_UK_COMPANY:
            self.match_sell(RULE_TEN_DAY)

        self.process_holdings(skip_integrity_check)

    def pool_same_day(self):
        transactions = copy.deepcopy(self.transactions)
        buy_transactions = {}
        sell_transactions = {}

        if config.debug:
            print("%spool same day transactions" % Fore.CYAN)

        for t in tqdm(transactions,
                      unit='t',
                      desc="%spool same day%s" % (Fore.CYAN, Fore.GREEN),
                      disable=bool(config.debug or not sys.stdout.isatty())):
            if isinstance(t, Buy) and t.is_crypto() and t.acquisition and \
                    t.t_type not in self.NO_MATCH_TYPES:
                if (t.asset, t.timestamp.date()) not in buy_transactions:
                    buy_transactions[(t.asset, t.timestamp.date())] = t
                else:
                    buy_transactions[(t.asset, t.timestamp.date())] += t
            elif isinstance(t, Sell) and t.is_crypto() and t.disposal and \
                    t.t_type not in self.NO_MATCH_TYPES:
                if (t.asset, t.timestamp.date()) not in sell_transactions:
                    sell_transactions[(t.asset, t.timestamp.date())] = t
                else:
                    sell_transactions[(t.asset, t.timestamp.date())] += t
            else:
                self.other_transactions.append(t)

        self.buys_ordered = sorted(buy_transactions.values())
        self.sells_ordered = sorted(sell_transactions.values())

        if config.debug:
            for t in sorted(self.all_transactions()):
                if len(t.pooled) > 1:
                    print("%spool: %s" % (Fore.GREEN, t.__str__(pooled_bold=True)))
                    for tp in t.pooled:
                        print("%spool:   (%s)" % (Fore.BLUE, tp))

        if config.debug:
            print("%spool: total transactions=%d" % (Fore.CYAN, len(self.all_transactions())))

    def process_disposal(self, t, cost, fees):
        if t.t_type in self.NO_GAIN_NO_LOSS_TYPES:
            # Change proceeds to make sure it balances
            t.proceeds = cost.quantize(PRECISION) + (fees + (t.fee_value or
                                                             Decimal(0))).quantize(PRECISION)
            t.proceeds_fixed = True
            tax_event = TaxEventCapitalGains(self.DISPOSAL_NO_GAIN_NO_LOSS,
                                             None, t, cost, fees + (t.fee_value or Decimal(0)))
        else:
            tax_event = TaxEventCapitalGains(self.DISPOSAL_SECTION_104,
                                             None, t, cost, fees + (t.fee_value or Decimal(0)))

        self.tax_events[self.which_tax_year(tax_event.date)].append(tax_event)
        if config.debug:
            print("%ssection104:   %s" % (Fore.CYAN, tax_event))


def _match_same_day(sell, buy):
    return buy.timestamp.date() == sell.timestamp.date()


RULE_SAME_DAY = TaxRule(
    TaxCalculatorUK.DISPOSAL_SAME_DAY, matcher=_match_same_day)


def _match_ten_day(sell, buy):
    # 10 days between buy and sell
    return buy.timestamp.date() < sell.timestamp.date() <= buy.timestamp.date() + timedelta(days=10)


RULE_TEN_DAY = TaxRule(
    TaxCalculatorUK.DISPOSAL_TEN_DAY, matcher=_match_ten_day)


def _match_bnb(sell, buy):
    # 30 days between sell and buy-back
    return sell.timestamp.date() < buy.timestamp.date() <= sell.timestamp.date() + timedelta(days=30)


RULE_BED_AND_BREAKFAST = TaxRule(
    TaxCalculatorUK.DISPOSAL_BED_AND_BREAKFAST, matcher=_match_bnb)
