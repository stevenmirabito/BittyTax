# -*- coding: utf-8 -*-
# Cryptocurrency tax calculator for UK tax rules
# (c) Nano Nano Ltd 2019

import argparse
import io
import sys
import codecs
import platform

import colorama
from colorama import Fore, Back
import xlrd

from .version import __version__
from .config import config
from .import_records import ImportRecords
from .export_records import ExportRecords
from .export_us import ExportForm8949, ExportLots
from .reconcile_1099da import Reconcile1099DA
from .transactions import TransactionHistory
from .audit import AuditRecords
from .price.valueasset import ValueAsset
from .price.exceptions import DataSourceError
from .tax import TaxCalculatorUK, TaxCalculatorUS, CalculateCapitalGains as CCG
from .report import ReportLog, ReportLogUS, ReportPdf
from .exceptions import ImportFailureError

if sys.stdout.encoding != 'UTF-8':
    if sys.version_info[:2] >= (3, 7):
        sys.stdout.reconfigure(encoding='utf-8')
    elif sys.version_info[:2] >= (3, 1):
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())
    else:
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout)

def main():
    colorama.init()
    parser = argparse.ArgumentParser()
    parser.add_argument('filename',
                        type=str,
                        nargs='?',
                        help="filename of transaction records, "
                             "or can read CSV data from standard input")
    parser.add_argument('-v',
                        '--version',
                        action='version',
                        version='%s v%s' % (parser.prog, __version__))
    parser.add_argument('-d',
                        '--debug',
                        action='store_true',
                        help="enable debug logging")
    parser.add_argument('-ty',
                        '--taxyear',
                        type=int,
                        help="tax year must be in the range (%s-%s) for UK tax rules" % (
                            min(CCG.CG_DATA_INDIVIDUAL),
                            max(CCG.CG_DATA_INDIVIDUAL)))
    parser.add_argument('--taxrules',
                        choices=[
                            config.TAX_RULES_US_INDIVIDUAL,
                            config.TAX_RULES_UK_INDIVIDUAL,
                            *config.TAX_RULES_UK_COMPANY,
                        ],
                        metavar='{US_INDIVIDUAL, UK_INDIVIDUAL, UK_COMPANY_XXX} '
                                'where XXX is the month which starts the financial year, '
                                'i.e. JAN, FEB, etc.',
                        default=config.TAX_RULES_UK_INDIVIDUAL,
                        type=str.upper,
                        dest='tax_rules',
                        help="specify tax rules to use, default: UK_INDIVIDUAL")
    parser.add_argument('--skipint',
                        dest='skip_integrity',
                        action='store_true',
                        help="skip integrity check")
    parser.add_argument('--summary',
                        action='store_true',
                        help="only output the capital gains summary in the tax report")
    parser.add_argument('-o',
                        dest='output_filename',
                        type=str,
                        help="specify the output filename for the tax report")
    parser.add_argument('--nopdf',
                        action='store_true',
                        help="don't output PDF report, output report to terminal only")
    parser.add_argument('--export',
                        action='store_true',
                        help="export your transaction records populated with price data")
    parser.add_argument('--export-8949',
                        dest='export_8949',
                        action='store_true',
                        help="US tax rules only, export capital gains as Form 8949 rows (CSV)")
    parser.add_argument('--export-lots',
                        dest='export_lots',
                        action='store_true',
                        help="US tax rules only, export per-wallet lot records (CSV)")
    parser.add_argument('--reconcile-1099da',
                        dest='reconcile_1099da',
                        metavar='FILENAME',
                        type=str,
                        help="US tax rules only, compare a Form 1099-DA CSV (Wallet, Asset, "
                             "Date Sold, Quantity, Proceeds, Cost Basis, Date Acquired) "
                             "with your records")

    args = parser.parse_args()
    config.debug = args.debug

    for flag, enabled in (('--export-8949', args.export_8949),
                          ('--export-lots', args.export_lots),
                          ('--reconcile-1099da', args.reconcile_1099da)):
        if enabled and args.tax_rules != config.TAX_RULES_US_INDIVIDUAL:
            parser.error("argument %s: requires --taxrules %s" % (
                flag, config.TAX_RULES_US_INDIVIDUAL))

    if args.taxyear and not is_supported_year(args.taxyear, args.tax_rules):
        parser.error("argument -ty/--taxyear: tax year %d is not supported, "
                     "must be in the range (%s-%s)" % (args.taxyear,
                                                       min(CCG.CG_DATA_INDIVIDUAL),
                                                       max(CCG.CG_DATA_INDIVIDUAL)))

    if config.debug:
        print("%s%s v%s" % (Fore.YELLOW, parser.prog, __version__))
        print("%spython: v%s" % (Fore.GREEN, platform.python_version()))
        print("%ssystem: %s, release: %s" % (Fore.GREEN, platform.system(), platform.release()))
        config.output_config()

    config.set_tax_rules(args.tax_rules)

    try:
        transaction_records, us_records = do_import(args.filename)
    except IOError:
        parser.exit("%sERROR%s File could not be read: %s" % (
            Back.RED+Fore.BLACK, Back.RESET+Fore.RED, args.filename))
    except ImportFailureError:
        parser.exit()

    if args.export:
        do_export(transaction_records)
        parser.exit()

    audit = AuditRecords(transaction_records)

    try:
        tax, value_asset = do_tax(transaction_records, args.tax_rules, args.skip_integrity,
                                  us_records)
        if not args.skip_integrity:
            int_passed = do_integrity_check(audit, tax)
            if not int_passed:
                parser.exit()

        if not args.summary:
            tax.process_income()

        do_each_tax_year(tax,
                         args.taxyear,
                         args.summary,
                         value_asset)

    except DataSourceError as e:
        parser.exit("%sERROR%s %s" % (
            Back.RED+Fore.BLACK, Back.RESET+Fore.RED, e))

    if args.export_8949:
        ExportForm8949(tax.tax_report).write_csv()
    if args.export_lots:
        ExportLots(tax).write_csv()
    if args.reconcile_1099da:
        try:
            Reconcile1099DA(args.reconcile_1099da, tax).write_csv()
        except (IOError, ValueError) as e:
            parser.exit("%sERROR%s 1099-DA reconciliation failed: %s" % (
                Back.RED+Fore.BLACK, Back.RESET+Fore.RED, e))

    if args.nopdf:
        report_log = ReportLogUS if args.tax_rules == config.TAX_RULES_US_INDIVIDUAL else ReportLog
        report_log(audit,
                  tax.tax_report,
                  value_asset.price_report,
                  tax.holdings_report,
                  args)
    else:
        ReportPdf(parser.prog,
                  audit,
                  tax.tax_report,
                  value_asset.price_report,
                  tax.holdings_report,
                  args)

def is_supported_year(year, tax_rules):
    if tax_rules == config.TAX_RULES_US_INDIVIDUAL:
        # No rate tables are needed, gains and income are reported for any year
        return True
    return year in CCG.CG_DATA_INDIVIDUAL

def do_import(filename):
    import_records = ImportRecords()

    if filename:
        try:
            import_records.import_excel(filename)
        except xlrd.XLRDError:
            with io.open(filename, newline='', encoding='utf-8') as csv_file:
                import_records.import_csv(csv_file)
    else:
        if sys.version_info[0] < 3:
            import_records.import_csv(codecs.getreader('utf-8')(sys.stdin))
        else:
            import_records.import_csv(sys.stdin)

    print("%simport %s (success=%s, failure=%s)" % (
        Fore.WHITE, 'successful' if import_records.failure_cnt <= 0 else 'failure',
        import_records.success_cnt, import_records.failure_cnt))

    if import_records.failure_cnt > 0:
        raise ImportFailureError

    return import_records.get_records(), import_records.us_records

def do_tax(transaction_records, tax_rules, skip_integrity_check, us_records=None):
    value_asset = ValueAsset()
    transaction_history = TransactionHistory(transaction_records, value_asset)

    if tax_rules == config.TAX_RULES_US_INDIVIDUAL:
        tax = TaxCalculatorUS(transaction_history.transactions, tax_rules, us_records)
        tax.calculate(skip_integrity_check)
    elif tax_rules == config.TAX_RULES_UK_INDIVIDUAL or tax_rules in config.TAX_RULES_UK_COMPANY:
        tax = TaxCalculatorUK(transaction_history.transactions, tax_rules)
        tax.calculate(skip_integrity_check)
    else:
        raise ValueError('Unsupported tax rules')

    return tax, value_asset

def do_integrity_check(audit, tax):
    int_passed = True
    holdings = tax.holdings

    if config.transfers_include:
        transfer_mismatch = transfer_mismatches(holdings)
    else:
        transfer_mismatch = False

    pools_match = audit.compare_pools(holdings)
    lot_failures = list(getattr(tax, 'integrity_failures', []))
    if getattr(tax, 'ledger', None) is not None:
        lot_failures += audit.compare_wallets(tax.ledger.balances())

    if not pools_match or transfer_mismatch or lot_failures:
        int_passed = False

    print("%sintegrity check: %s%s" % (
        Fore.CYAN, Fore.YELLOW, 'passed' if int_passed else 'failed'))

    if lot_failures:
        print("%sERROR%s Integrity check failed: units removed which aren't held, "
              "turn on logging [-d] to see transactions" % (
                  Back.RED+Fore.BLACK, Back.RESET+Fore.RED))
        for failure in lot_failures:
            print("%s  %s" % (Fore.RED, failure))
    elif transfer_mismatch:
        print("%sWARNING%s Integrity check failed: disposal(s) detected during transfer, "
              "turn on logging [-d] to see transactions" % (
                  Back.YELLOW+Fore.BLACK, Back.RESET+Fore.YELLOW))
    elif not pools_match:
        if not config.transfers_include:
            print("%sWARNING%s Integrity check failed: audit does not match section 104 pools, "
                  "please check Withdrawals and Deposits for missing fees" % (
                      Back.YELLOW+Fore.BLACK, Back.RESET+Fore.YELLOW))
        else:
            print("%sERROR%s Integrity check failed: audit does not match section 104 pools" % (
                Back.RED+Fore.BLACK, Back.RESET+Fore.RED))
        audit.report_failures()
    return int_passed

def transfer_mismatches(holdings):
    return bool([asset for asset in holdings if holdings[asset].mismatches])

def do_each_tax_year(tax, tax_year, summary, value_asset):
    if tax_year:
        print("%scalculating tax year %s" % (
            Fore.CYAN,
            config.format_tax_year(tax_year)))

        tax.calculate_capital_gains(tax_year)
        if not summary:
            tax.calculate_income(tax_year)
    else:
        # Calculate for all years
        for year in sorted(tax.tax_events):
            print("%scalculating tax year %s" % (
                Fore.CYAN,
                config.format_tax_year(year)))

            if is_supported_year(year, tax.tax_rules):
                tax.calculate_capital_gains(year)
                if not summary:
                    tax.calculate_income(year)
            else:
                print("%sWARNING%s Tax year %s is not supported" % (
                    Back.YELLOW+Fore.BLACK, Back.RESET+Fore.YELLOW, year))

        if not summary:
            tax.calculate_holdings(value_asset)

    return tax, value_asset

def do_export(transaction_records):
    value_asset = ValueAsset()
    TransactionHistory(transaction_records, value_asset)
    ExportRecords(transaction_records).write_csv()
