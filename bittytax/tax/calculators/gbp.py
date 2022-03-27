# -*- coding: utf-8 -*-
# (c) Nano Nano Ltd 2019

import sys
import copy
from decimal import Decimal
from datetime import datetime, timedelta

from colorama import Fore
from tqdm import tqdm

from ...config import config, Currency
from ...transactions import Buy, Sell
from ...holdings import Holdings

from ..taxcalculator import TaxCalculator
from ..taxevent import TaxEventCapitalGains
from ..taxrule import TaxRule


@TaxCalculator.register(Currency.GB)
class TaxCalculatorGB(TaxCalculator):
    DISPOSAL_SAME_DAY = 'Same Day'
    DISPOSAL_BED_AND_BREAKFAST = 'Bed & Breakfast'
    DISPOSAL_SECTION_104 = 'Section 104'
    DISPOSAL_NO_GAIN_NO_LOSS = 'No Gain/No Loss'
    
    @staticmethod
    def which_tax_year(timestamp):
        if timestamp >= datetime(timestamp.year, 4, 6, tzinfo=config.TZ_LOCAL):
            return timestamp.year + 1

        return timestamp.year

    def calculate(self):
        self.pool_same_day()
        self.match(RULE_SAME_DAY)
        self.match(RULE_BED_AND_BREAKFAST)
        self.process_section104()

    def pool_same_day(self):
        transactions = copy.deepcopy(self.transactions)
        buy_transactions = {}
        sell_transactions = {}

        if config.args.debug:
            print("%spool same day transactions" % Fore.CYAN)

        for t in tqdm(transactions,
                      unit='t',
                      desc="%spool same day%s" % (Fore.CYAN, Fore.GREEN),
                      disable=bool(config.args.debug or not sys.stdout.isatty())):
            if isinstance(t, Buy) and t.acquisition:
                if (t.asset, t.timestamp.date()) not in buy_transactions:
                    buy_transactions[(t.asset, t.timestamp.date())] = t
                else:
                    buy_transactions[(t.asset, t.timestamp.date())] += t
            elif isinstance(t, Sell) and t.disposal and t.t_type != t.TYPE_GIFT_SPOUSE:
                if (t.asset, t.timestamp.date()) not in sell_transactions:
                    sell_transactions[(t.asset, t.timestamp.date())] = t
                else:
                    sell_transactions[(t.asset, t.timestamp.date())] += t
            else:
                self.other_transactions.append(t)

        self.buys_ordered = sorted(buy_transactions.values())
        self.sells_ordered = sorted(sell_transactions.values())

        if config.args.debug:
            for t in sorted(self.all_transactions()):
                if len(t.pooled) > 1:
                    print("%spool: %s" %
                          (Fore.GREEN, t.__str__(pooled_bold=True)))
                    for tp in t.pooled:
                        print("%spool:   (%s)" % (Fore.BLUE, tp))

        if config.args.debug:
            print("%spool: total transactions=%d" %
                  (Fore.CYAN, len(self.all_transactions())))

    def process_section104(self):
        if config.args.debug:
            print("%sprocess section 104" % Fore.CYAN)

        for t in tqdm(sorted(self.all_transactions()),
                      unit='t',
                      desc="%sprocess section 104%s" % (Fore.CYAN, Fore.GREEN),
                      disable=bool(config.args.debug or not sys.stdout.isatty())):
            if t.asset not in self.holdings:
                self.holdings[t.asset] = Holdings(t.asset)

            if t.matched:
                if config.args.debug:
                    print("%ssection104: //%s <- matched" % (Fore.BLUE, t))
                continue
            elif not config.transfers_include and t.t_type in self.transfer_types:
                if config.args.debug:
                    print("%ssection104: //%s <- transfer" % (Fore.BLUE, t))
                continue
            else:
                if config.args.debug:
                    print("%ssection104: %s" % (Fore.GREEN, t))

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
            else:
                tax_event = TaxEventCapitalGains(self.DISPOSAL_SECTION_104,
                                                 None, t, cost, fees + (t.fee_value or Decimal(0)))

            self.tax_events[self._which_tax_year(
                tax_event.date)].append(tax_event)
            if config.args.debug:
                print("%ssection104:   %s" % (Fore.CYAN, tax_event))

            if config.transfers_include and not config.args.skip_integrity:
                self.holdings[t.asset].check_transfer_mismatch()


def _match_same_day(sell, buy):
    return buy.timestamp.date() == sell.timestamp.date()


RULE_SAME_DAY = TaxRule(
    TaxCalculatorGB.DISPOSAL_SAME_DAY, matcher=_match_same_day)


def _match_bnb(sell, buy):
    return (sell.timestamp.date() < buy.timestamp.date() and
            buy.timestamp.date() <= sell.timestamp.date() + timedelta(days=30))


RULE_BED_AND_BREAKFAST = TaxRule(
    TaxCalculatorGB.DISPOSAL_BED_AND_BREAKFAST, matcher=_match_bnb)
