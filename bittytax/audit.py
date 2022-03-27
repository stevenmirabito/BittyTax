# -*- coding: utf-8 -*-
# (c) Nano Nano Ltd 2019

import sys
from decimal import Decimal

from colorama import Fore, Back, Style
from tqdm import tqdm

from .config import config

class AuditRecords(object):
    def __init__(self, transaction_records):
        self.wallets = {}
        self.totals = {}
        self.failures = []

        if config.args.debug:
            print("%saudit transaction records" % Fore.CYAN)

        for tr in tqdm(transaction_records,
                       unit='tr',
                       desc="%saudit transaction records%s" % (Fore.CYAN, Fore.GREEN),
                       disable=bool(config.args.debug or not sys.stdout.isatty())):
            if config.args.debug:
                print("%saudit: TR %s" % (Fore.MAGENTA, tr))
            if tr.buy:
                self._add_tokens(tr.wallet, tr.buy.asset, tr.buy.quantity)

            if tr.sell:
                self._subtract_tokens(tr.wallet, tr.sell.asset, tr.sell.quantity)

            if tr.fee:
                self._subtract_tokens(tr.wallet, tr.fee.asset, tr.fee.quantity)

        if config.args.debug:
            print("%saudit: final balances by wallet" % Fore.CYAN)
            for wallet in sorted(self.wallets, key=str.lower):
                for asset in sorted(self.wallets[wallet]):
                    print("%saudit: %s:%s=%s%s%s" % (
                        Fore.YELLOW,
                        wallet,
                        asset,
                        Style.BRIGHT,
                        '{:0,f}'.format(self.wallets[wallet][asset].normalize()),
                        Style.NORMAL))

            print("%saudit: final balances by asset" % Fore.CYAN)
            for asset in sorted(self.totals):
                print("%saudit: %s=%s%s%s" % (
                    Fore.YELLOW,
                    asset,
                    Style.BRIGHT,
                    '{:0,f}'.format(self.totals[asset].normalize()),
                    Style.NORMAL))

    def _add_tokens(self, wallet, asset, quantity):
        if wallet not in self.wallets:
            self.wallets[wallet] = {}

        if asset not in self.wallets[wallet]:
            self.wallets[wallet][asset] = Decimal(0)

        self.wallets[wallet][asset] += quantity

        if asset not in self.totals:
            self.totals[asset] = Decimal(0)

        self.totals[asset] += quantity

        if config.args.debug:
            print("%saudit:   %s:%s=%s (+%s)" % (
                Fore.GREEN,
                wallet,
                asset,
                '{:0,f}'.format(self.wallets[wallet][asset].normalize()),
                '{:0,f}'.format(quantity.normalize())))

    def _subtract_tokens(self, wallet, asset, quantity):
        if wallet not in self.wallets:
            self.wallets[wallet] = {}

        if asset not in self.wallets[wallet]:
            self.wallets[wallet][asset] = Decimal(0)

        self.wallets[wallet][asset] -= quantity

        if asset not in self.totals:
            self.totals[asset] = Decimal(0)

        self.totals[asset] -= quantity

        if config.args.debug:
            print("%saudit:   %s:%s=%s (-%s)" %(
                Fore.GREEN,
                wallet,
                asset,
                '{:0,f}'.format(self.wallets[wallet][asset].normalize()),
                '{:0,f}'.format(quantity.normalize())))

        if self.wallets[wallet][asset] < 0 and asset not in config.fiat_list:
            tqdm.write("%sWARNING%s Balance at %s:%s is negative %s" % (
                Back.YELLOW+Fore.BLACK, Back.RESET+Fore.YELLOW,
                wallet, asset, '{:0,f}'.format(self.wallets[wallet][asset].normalize())))

    def compare_holdings(self, holdings):
        passed = True
        for asset in sorted(self.totals):
            if asset in config.fiat_list:
                continue

            if asset in holdings:
                if self.totals[asset] == holdings[asset].quantity:
                    if config.args.debug:
                        print("%scheck holding: %s (ok)" %(Fore.GREEN, asset))
                else:
                    if config.args.debug:
                        print("%scheck holding: %s %s (mismatch)" %(Fore.RED, asset,
                            '{:+0,f}'.format((holdings[asset].quantity-
                                              self.totals[asset]).normalize())))

                    self._log_failure(asset, self.totals[asset], holdings[asset].quantity)
                    passed = False
            else:
                if config.args.debug:
                    print("%scheck holding: %s (missing)" %(Fore.RED, asset))

                self._log_failure(asset, self.totals[asset], None)
                passed = False

        return passed

    def _log_failure(self, asset, audit, quantity):
        failure = {}
        failure['asset'] = asset
        failure['audit'] = audit
        failure['quantity'] = quantity

        self.failures.append(failure)

    def report_failures(self):
        header = "%-8s %25s %25s %25s" % ('Asset',
                                          'Audit Balance',
                                          'Holdings',
                                          'Difference')

        print('\n%s%s' % (Fore.YELLOW, header))
        for failure in self.failures:
            if failure['quantity'] is not None:
                print("%s%-8s %25s %25s %s%25s" % (
                    Fore.WHITE,
                    failure['asset'],
                    '{:0,f}'.format(failure['audit'].normalize()),
                    '{:0,f}'.format(failure['quantity'].normalize()),
                    Fore.RED,
                    '{:+0,f}'.format((failure['quantity']-failure['audit']).normalize())))
            else:
                print("%s%-8s %25s %s%25s" % (
                    Fore.WHITE,
                    failure['asset'],
                    '{:0,f}'.format(failure['audit'].normalize()),
                    Fore.RED,
                    '<missing>'))
