import sys
from abc import ABC, abstractmethod, abstractstaticmethod
from decimal import Decimal

from colorama import Fore
from tqdm import tqdm

from ..config import config
from ..transactions import Buy, Sell

from .taxevent import TaxEventCapitalGains, TaxEventIncome
from .taxrule import TaxRule

DEFAULT_TRANSFER_TYPES = [Buy.TYPE_DEPOSIT, Sell.TYPE_WITHDRAWAL]
DEFAULT_INCOME_TYPES = [Buy.TYPE_MINING, Buy.TYPE_STAKING,
                        Buy.TYPE_DIVIDEND, Buy.TYPE_INTEREST, Buy.TYPE_INCOME]


class TaxCalculator(ABC):
    calculators = {}

    def __init__(self, transactions, transfer_types=DEFAULT_TRANSFER_TYPES, income_types=DEFAULT_INCOME_TYPES):
        self.transfer_types = transfer_types
        self.income_types = income_types

        self.transactions = transactions
        self.buys_ordered = []
        self.sells_ordered = []
        self.other_transactions = []

        self.tax_events = {}
        self.holdings = {}

        self.tax_report = {}
        self.holdings_report = {}

    @classmethod
    def register(cls, ccy):
        def decorator(subclass):
            cls.calculators[ccy] = subclass
            return subclass

        return decorator

    @classmethod
    def for_currency(cls, ccy):
        if ccy not in cls.calculators:
            raise ValueError(
                'Tax calculations not supported for {}'.format(ccy))

        return cls.calculators[ccy]

    @abstractstaticmethod
    def which_tax_year():
        raise NotImplementedError('Implement in subclass')

    @abstractmethod
    def calculate(self):
        raise NotImplementedError('Implement in subclass')

    def add_transactions(self, transactions):
        self.transactions.append(transactions)

    def match(self, rule):
        if not isinstance(rule, TaxRule):
            raise ValueError('Rule must be an instance of TaxRule')

        sell_index = buy_index = 0

        if not self.buys_ordered:
            return

        if config.args.debug:
            print("%smatch %s transactions" %
                  (Fore.CYAN, rule.name.lower()))

        pbar = tqdm(total=len(self.sells_ordered),
                    unit='t',
                    desc="%smatch %s transactions%s" % (
                        Fore.CYAN, rule.name.lower(), Fore.GREEN),
                    disable=bool(config.args.debug or not sys.stdout.isatty()))

        while sell_index < len(self.sells_ordered):
            s = self.sells_ordered[sell_index]
            b = self.buys_ordered[buy_index]

            if (not s.matched and not b.matched and s.asset == b.asset and rule.match(s, b)):
                if config.args.debug:
                    if b.quantity > s.quantity:
                        print("%smatch: %s" %
                              (Fore.GREEN, s.__str__(quantity_bold=True)))
                        print("%smatch: %s" % (Fore.GREEN, b))
                    elif s.quantity > b.quantity:
                        print("%smatch: %s" % (Fore.GREEN, s))
                        print("%smatch: %s" %
                              (Fore.GREEN, b.__str__(quantity_bold=True)))
                    else:
                        print("%smatch: %s" %
                              (Fore.GREEN, s.__str__(quantity_bold=True)))
                        print("%smatch: %s" %
                              (Fore.GREEN, b.__str__(quantity_bold=True)))

                if b.quantity > s.quantity:
                    b_remainder = b.split_buy(s.quantity)
                    self.buys_ordered.insert(buy_index + 1, b_remainder)
                    if config.args.debug:
                        print("%smatch:   split: %s" %
                              (Fore.YELLOW, b.__str__(quantity_bold=True)))
                        print("%smatch:   split: %s" %
                              (Fore.YELLOW, b_remainder))
                elif s.quantity > b.quantity:
                    s_remainder = s.split_sell(b.quantity)
                    self.sells_ordered.insert(sell_index + 1, s_remainder)
                    if config.args.debug:
                        print("%smatch:   split: %s" %
                              (Fore.YELLOW, s.__str__(quantity_bold=True)))
                        print("%smatch:   split: %s" %
                              (Fore.YELLOW, s_remainder))
                    pbar.total += 1

                s.matched = b.matched = True
                tax_event = TaxEventCapitalGains(rule.name, b, s, b.cost,
                                                 (b.fee_value or Decimal(0)) +
                                                 (s.fee_value or Decimal(0)))
                self.tax_events[self._which_tax_year(
                    tax_event.date)].append(tax_event)
                if config.args.debug:
                    print("%smatch:   %s" % (Fore.CYAN, tax_event))

                # Find next sell
                sell_index += 1
                pbar.update(1)
                buy_index = 0
            else:
                buy_index += 1
                if buy_index >= len(self.buys_ordered):
                    sell_index += 1
                    pbar.update(1)
                    buy_index = 0

        pbar.close()

        if config.args.debug:
            print("%smatch: total transactions=%d" %
                  (Fore.CYAN, len(self.all_transactions())))

    def process_income(self):
        if config.args.debug:
            print("%sprocess income" % Fore.CYAN)

        for t in tqdm(self.transactions,
                      unit='t',
                      desc="%sprocess income%s" % (Fore.CYAN, Fore.GREEN),
                      disable=bool(config.args.debug or not sys.stdout.isatty())):
            if t.t_type in self.income_types:
                tax_event = TaxEventIncome(t)
                self.tax_events[self._which_tax_year(
                    tax_event.date)].append(tax_event)

    def all_transactions(self):
        if not config.transfers_include:
            # Ordered so transfers appear before the fee spend in the log
            return self.other_transactions + self.buys_ordered + self.sells_ordered
        return self.buys_ordered + self.sells_ordered + self.other_transactions

    def calculate_capital_gains(self, tax_year):
        self.tax_report[tax_year] = {}
        self.tax_report[tax_year]['CapitalGains'] = CalculateCapitalGains(
            tax_year)

        if tax_year in self.tax_events:
            for te in sorted(self.tax_events[tax_year]):
                if isinstance(te, TaxEventCapitalGains):
                    self.tax_report[tax_year]['CapitalGains'].tax_summary(te)

        self.tax_report[tax_year]['CapitalGains'].tax_estimate(tax_year)

    def calculate_income(self, tax_year):
        self.tax_report[tax_year]['Income'] = CalculateIncome()

        if tax_year in self.tax_events:
            for te in sorted(self.tax_events[tax_year]):
                if isinstance(te, TaxEventIncome):
                    self.tax_report[tax_year]['Income'].totalise(te)

        self.tax_report[tax_year]['Income'].totals_by_type()
        self.tax_report[tax_year]['Income'].totals_by_wallet()

    def calculate_holdings(self, value_asset):
        holdings = {}
        totals = {'cost': Decimal(0),
                  'value': Decimal(0),
                  'gain': Decimal(0)}

        if config.args.debug:
            print("%scalculating holdings" % Fore.CYAN)

        for h in tqdm(self.holdings,
                      unit='h',
                      desc="%scalculating holdings%s" % (
                          Fore.CYAN, Fore.GREEN),
                      disable=bool(config.args.debug or not sys.stdout.isatty())):
            if self.holdings[h].quantity > 0 or config.show_empty_wallets:
                holdings[h] = {}
                holdings[h]['asset'] = self.holdings[h].asset
                holdings[h]['quantity'] = self.holdings[h].quantity
                holdings[h]['cost'] = (self.holdings[h].cost +
                                       self.holdings[h].fees).quantize(config.PRECISION)

                value, name, data_source = value_asset.get_current_value(self.holdings[h].asset,
                                                                         self.holdings[h].quantity)
                holdings[h]['value'] = value.quantize(
                    config.PRECISION) if value is not None else None
                holdings[h]['name'] = name
                holdings[h]['data_source'] = data_source

                if holdings[h]['value'] is not None:
                    holdings[h]['gain'] = holdings[h]['value'] - \
                        holdings[h]['cost']
                    totals['value'] += holdings[h]['value']
                    totals['gain'] += holdings[h]['gain']

                totals['cost'] += holdings[h]['cost']

        self.holdings_report['holdings'] = holdings
        self.holdings_report['totals'] = totals

    def _which_tax_year(self, timestamp):
        tax_year = self.which_tax_year(timestamp)
        if tax_year not in self.tax_events:
            self.tax_events[tax_year] = []

        return tax_year


class CalculateCapitalGains(object):
    CG_DATA_INDIVIDUALS = {2009: {'allowance': 9600, 'basic_rate': 18, 'higher_rate': 18},
                           2010: {'allowance': 10100, 'basic_rate': 18, 'higher_rate': 18},
                           2011: {'allowance': 10100, 'basic_rate': 18, 'higher_rate': 28},
                           2012: {'allowance': 10600, 'basic_rate': 18, 'higher_rate': 28},
                           2013: {'allowance': 10600, 'basic_rate': 18, 'higher_rate': 28},
                           2014: {'allowance': 10900, 'basic_rate': 18, 'higher_rate': 28},
                           2015: {'allowance': 11000, 'basic_rate': 18, 'higher_rate': 28},
                           2016: {'allowance': 11100, 'basic_rate': 18, 'higher_rate': 28},
                           2017: {'allowance': 11100, 'basic_rate': 10, 'higher_rate': 20},
                           2018: {'allowance': 11300, 'basic_rate': 10, 'higher_rate': 20},
                           2019: {'allowance': 11700, 'basic_rate': 10, 'higher_rate': 20},
                           2020: {'allowance': 12000, 'basic_rate': 10, 'higher_rate': 20},
                           2021: {'allowance': 12300, 'basic_rate': 10, 'higher_rate': 20}}

    def __init__(self, tax_year):
        self.totals = {'cost': Decimal(0),
                       'fees': Decimal(0),
                       'proceeds': Decimal(0),
                       'gain': Decimal(0)}
        self.summary = {'disposals': Decimal(0),
                        'total_gain': Decimal(0),
                        'total_loss': Decimal(0)}
        self.estimate = {'allowance': Decimal(self.CG_DATA_INDIVIDUALS[tax_year]['allowance']),
                         'allowance_used': Decimal(0),
                         'taxable_gain': Decimal(0),
                         'cgt_basic': Decimal(0),
                         'cgt_higher': Decimal(0),
                         'proceeds_warning': False}
        self.assets = {}

    def tax_summary(self, te):
        self.summary['disposals'] += 1
        self.totals['cost'] += te.cost
        self.totals['fees'] += te.fees
        self.totals['proceeds'] += te.proceeds
        self.totals['gain'] += te.gain
        if te.gain >= 0:
            self.summary['total_gain'] += te.gain
        else:
            self.summary['total_loss'] += te.gain

        if te.asset not in self.assets:
            self.assets[te.asset] = []

        self.assets[te.asset].append(te)

    def tax_estimate(self, tax_year):
        if self.totals['gain'] > self.estimate['allowance']:
            self.estimate['allowance_used'] = self.estimate['allowance']
            self.estimate['taxable_gain'] = self.totals['gain'] - \
                self.estimate['allowance']
            self.estimate['cgt_basic'] = self.estimate['taxable_gain'] * \
                self.CG_DATA_INDIVIDUALS[tax_year]['basic_rate'] / 100
            self.estimate['cgt_higher'] = self.estimate['taxable_gain'] * \
                self.CG_DATA_INDIVIDUALS[tax_year]['higher_rate'] / 100
        elif self.totals['gain'] > 0:
            self.estimate['allowance_used'] = self.totals['gain']

        if self.totals['proceeds'] >= self.estimate['allowance'] * 4:
            self.estimate['proceeds_warning'] = True


class CalculateIncome(object):
    def __init__(self):
        self.totals = {'amount': Decimal(0),
                       'fees': Decimal(0)}
        self.assets = {}
        self.types = {}
        self.type_totals = {}
        self.wallets = {}
        self.wallet_totals = {}

    def totalise(self, te):
        self.totals['amount'] += te.amount
        self.totals['fees'] += te.fees

        if te.asset not in self.assets:
            self.assets[te.asset] = []

        self.assets[te.asset].append(te)

        if te.type not in self.types:
            self.types[te.type] = []

        self.types[te.type].append(te)

        if te.wallet not in self.wallets:
            self.wallets[te.wallet] = []

        self.wallets[te.wallet].append(te)

    def totals_by_type(self):
        for income_type in self.types:
            for te in self.types[income_type]:
                if income_type not in self.type_totals:
                    self.type_totals[income_type] = {}
                    self.type_totals[income_type]['amount'] = te.amount
                    self.type_totals[income_type]['fees'] = te.fees
                else:
                    self.type_totals[income_type]['amount'] += te.amount
                    self.type_totals[income_type]['fees'] += te.fees

    def totals_by_wallet(self):
        for income_wallet in self.wallets:
            for te in self.wallets[income_wallet]:
                if income_wallet not in self.wallet_totals:
                    self.wallet_totals[income_wallet] = {}
                    self.wallet_totals[income_wallet]['amount'] = te.amount
                    self.wallet_totals[income_wallet]['fees'] = te.fees
                else:
                    self.wallet_totals[income_wallet]['amount'] += te.amount
                    self.wallet_totals[income_wallet]['fees'] += te.fees


from .calculators import *  # noqa
