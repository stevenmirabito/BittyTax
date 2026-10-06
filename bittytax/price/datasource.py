# -*- coding: utf-8 -*-
# (c) Nano Nano Ltd 2019

import os
import atexit
import json
import platform
from decimal import Decimal
from datetime import datetime, timedelta

from urllib.parse import quote

from colorama import Fore, Back
import dateutil.parser
import requests

from ..version import __version__
from ..config import config
from .exceptions import UnexpectedDataSourceAssetIdError

CRYPTOCOMPARE_MAX_DAYS = 2000
COINPAPRIKA_MAX_DAYS = 5000

class DataSourceBase(object):
    USER_AGENT = 'BittyTax/%s Python/%s %s/%s' % (__version__,
                                                  platform.python_version(),
                                                  platform.system(), platform.release())
    TIME_OUT = 30

    def __init__(self):
        self.assets = {}
        self.ids = {}
        self.prices = self.load_prices()

        for pair in sorted(self.prices):
            if config.debug:
                print("%sprice: %s (%s) data cache loaded" % (Fore.YELLOW, self.name(), pair))

        atexit.register(self.dump_prices)

    def name(self):
        return self.__class__.__name__

    def get_headers(self):
        return {'User-Agent': self.USER_AGENT}

    def get_json(self, url):
        if config.debug:
            print("%sprice: GET %s" % (Fore.YELLOW, url))

        response = requests.get(url, headers=self.get_headers(), timeout=self.TIME_OUT)

        if response.status_code in [429, 502, 503, 504]:
            response.raise_for_status()

        if response:
            return response.json()
        return {}

    def update_prices(self, pair, prices, timestamp):
        if pair not in self.prices:
            self.prices[pair] = {}

        # We are not interested in today's latest price, only the days closing price, also need to
        #  filter any erroneous future dates returned
        prices = {k: v
                  for k, v in prices.items()
                  if dateutil.parser.parse(k).date() < datetime.now().date()}

        # We might not receive data for the date requested, if so set to None to prevent repeat
        #  lookups, assuming date is in the past
        date = timestamp.strftime('%Y-%m-%d')
        if date not in prices and timestamp.date() < datetime.now().date():
            prices[date] = {'price': None,
                            'url': None}

        self.prices[pair].update(prices)

    def load_prices(self):
        filename = os.path.join(config.CACHE_DIR, self.name() + '.json')
        if not os.path.exists(filename):
            return {}

        try:
            with open(filename, 'r') as price_cache:
                json_prices = json.load(price_cache)
                return {pair: {date: {'price': self.str_to_decimal(price['price']),
                                      'url': price['url']}
                               for date, price in json_prices[pair].items()}
                        for pair in json_prices}
        except:
            print("%sWARNING%s Data cached for %s could not be loaded" % (
                Back.YELLOW+Fore.BLACK, Back.RESET+Fore.YELLOW, self.name()))
            return {}

    def dump_prices(self):
        with open(os.path.join(config.CACHE_DIR, self.name() + '.json'), 'w') as price_cache:
            json_prices = {pair: {date: {'price': self.decimal_to_str(price['price']),
                                         'url': price['url']}
                                  for date, price in self.prices[pair].items()}
                           for pair in self.prices}
            json.dump(json_prices, price_cache, indent=4, sort_keys=True)

    def get_config_assets(self):
        for symbol in config.data_source_select:
            for ds in config.data_source_select[symbol]:
                if ds.upper().startswith(self.name().upper() + ':'):
                    if symbol in self.assets:
                        self._update_asset(symbol, ds)
                    else:
                        self._add_asset(symbol, ds)

    def _update_asset(self, symbol, data_source):
        asset_id = data_source.split(':')[1]
        # Update an existing symbol, validate id belongs to that symbol
        if asset_id in self.ids and self.ids[asset_id]['symbol'] == symbol:
            self.assets[symbol] = {'id': asset_id, 'name': self.ids[asset_id]['name']}

            if config.debug:
                print("%sprice: %s updated as %s [ID:%s] (%s)" % (
                    Fore.YELLOW,
                    symbol,
                    self.name(),
                    asset_id,
                    self.ids[asset_id]['name']))
        else:
            raise UnexpectedDataSourceAssetIdError(data_source, symbol)

    def _add_asset(self, symbol, data_source):
        asset_id = data_source.split(':')[1]
        if asset_id in self.ids:
            self.assets[symbol] = {'id': asset_id, 'name': self.ids[asset_id]['name']}
            self.ids[asset_id] = {'symbol': symbol, 'name': self.ids[asset_id]['name']}

            if config.debug:
                print("%sprice: %s added as %s [ID:%s] (%s)" % (
                    Fore.YELLOW,
                    symbol,
                    self.name(),
                    asset_id,
                    self.ids[asset_id]['name']))
        else:
            raise UnexpectedDataSourceAssetIdError(data_source, symbol)

    def get_list(self):
        if self.ids:
            asset_list = {}
            for c in self.ids:
                symbol = self.ids[c]['symbol']
                if symbol not in asset_list:
                    asset_list[symbol] = []

                asset_list[symbol].append({'id': c, 'name': self.ids[c]['name']})

            # Include any custom symbols as well
            for symbol in asset_list:
                if self.assets[symbol] not in asset_list[symbol]:
                    asset_list[symbol].append(self.assets[symbol])

            return asset_list
        return {k: [{'id':None, 'name': v['name']}] for k, v in self.assets.items()}

    @staticmethod
    def pair(asset, quote):
        return asset + '/' + quote

    @staticmethod
    def str_to_decimal(price):
        if price:
            return Decimal(price)

        return None

    @staticmethod
    def decimal_to_str(price):
        if price:
            return '{0:f}'.format(price)

        return None

    @staticmethod
    def epoch_time(timestamp):
        epoch = (timestamp - datetime(1970, 1, 1, tzinfo=config.TZ_UTC)).total_seconds()
        return int(epoch)

class BittyTaxAPI(DataSourceBase):
    def __init__(self):
        super(BittyTaxAPI, self).__init__()
        json_resp = self.get_json("https://api.bitty.tax/v1/symbols")
        self.assets = {k: {'name': v}
                       for k, v in json_resp['symbols'].items()}

    def get_latest(self, asset, quote, _asset_id=None):
        json_resp = self.get_json(
            "https://api.bitty.tax/v1/latest?base=%s&symbols=%s" % (asset, quote)
        )
        return Decimal(repr(json_resp['rates'][quote])) \
                if 'rates' in json_resp and quote in json_resp['rates'] else None

    def get_historical(self, asset, quote, timestamp, _asset_id=None):
        url = "https://api.bitty.tax/v1/%s?base=%s&symbols=%s" % (
            timestamp.strftime('%Y-%m-%d'), asset, quote)
        json_resp = self.get_json(url)
        pair = self.pair(asset, quote)
        # Date returned in response might not be date requested due to weekends/holidays
        self.update_prices(pair,
                           {timestamp.strftime('%Y-%m-%d'): {
                               'price': Decimal(repr(json_resp['rates'][quote])) \
                                        if 'rates' in json_resp and quote \
                                        in json_resp['rates'] else None,
                               'url': url}},
                           timestamp)

class Frankfurter(DataSourceBase):
    def __init__(self):
        super(Frankfurter, self).__init__()
        currencies = ['EUR', 'USD', 'JPY', 'BGN', 'CYP', 'CZK', 'DKK', 'EEK', 'GBP', 'HUF',
                      'LTL', 'LVL', 'MTL', 'PLN', 'ROL', 'RON', 'SEK', 'SIT', 'SKK', 'CHF',
                      'ISK', 'NOK', 'HRK', 'RUB', 'TRL', 'TRY', 'AUD', 'BRL', 'CAD', 'CNY',
                      'HKD', 'IDR', 'ILS', 'INR', 'KRW', 'MXN', 'MYR', 'NZD', 'PHP', 'SGD',
                      'THB', 'ZAR']
        self.assets = {c: {'name': 'Fiat ' + c} for c in currencies}

    def get_latest(self, asset, quote, _asset_id=None):
        json_resp = self.get_json(
            "https://api.frankfurter.app/latest?from=%s&to=%s" % (asset, quote)
        )
        return Decimal(repr(json_resp['rates'][quote])) \
                if 'rates' in json_resp and quote in json_resp['rates'] else None

    def get_historical(self, asset, quote, timestamp, _asset_id=None):
        url = "https://api.frankfurter.app/%s?from=%s&to=%s" % (
            timestamp.strftime('%Y-%m-%d'), asset, quote)
        json_resp = self.get_json(url)
        pair = self.pair(asset, quote)
        # Date returned in response might not be date requested due to weekends/holidays
        self.update_prices(pair,
                           {timestamp.strftime('%Y-%m-%d'): {
                               'price': Decimal(repr(json_resp['rates'][quote])) \
                                        if 'rates' in json_resp and quote \
                                        in json_resp['rates'] else None,
                               'url': url}},
                           timestamp)

class CoinDesk(DataSourceBase):
    def __init__(self):
        super(CoinDesk, self).__init__()
        self.assets = {'BTC': {'name': 'Bitcoin'}}

    def get_latest(self, _asset, quote, _asset_id=None):
        json_resp = self.get_json("https://api.coindesk.com/v1/bpi/currentprice.json")
        return Decimal(repr(json_resp['bpi'][quote]['rate_float'])) \
                if 'bpi' in json_resp and quote in json_resp['bpi'] else None

    def get_historical(self, asset, quote, timestamp, _asset_id=None):
        url = "https://api.coindesk.com/v1/bpi/historical/close.json" \
              "?start=%s&end=%s&currency=%s" % (
                  timestamp.strftime('%Y-%m-%d'), datetime.now().strftime('%Y-%m-%d'), quote)
        json_resp = self.get_json(url)
        pair = self.pair(asset, quote)
        if 'bpi' in json_resp:
            self.update_prices(pair,
                               {k: {
                                   'price': Decimal(repr(v)) if v else None,
                                   'url': url} for k, v in json_resp['bpi'].items()},
                               timestamp)

class CryptoCompare(DataSourceBase):
    def __init__(self):
        super(CryptoCompare, self).__init__()
        json_resp = self.get_json("https://min-api.cryptocompare.com/data/all/coinlist")
        self.assets = {c[1]['Symbol'].strip().upper(): {'name': c[1]['CoinName'].strip()}
                       for c in json_resp['Data'].items()}
        # CryptoCompare symbols are unique, so no ID required

    def get_latest(self, asset, quote, _asset_id=None):
        json_resp = self.get_json("https://min-api.cryptocompare.com/data/price" \
            "?extraParams=%s&fsym=%s&tsyms=%s" % (self.USER_AGENT, asset, quote))
        return Decimal(repr(json_resp[quote])) if quote in json_resp else None

    def get_historical(self, asset, quote, timestamp, _asset_id=None):
        url = "https://min-api.cryptocompare.com/data/histoday?aggregate=1&extraParams=%s" \
              "&fsym=%s&tsym=%s&limit=%s&toTs=%d" % (
                  self.USER_AGENT, asset, quote, CRYPTOCOMPARE_MAX_DAYS,
                  self.epoch_time(timestamp + timedelta(days=CRYPTOCOMPARE_MAX_DAYS)))

        json_resp = self.get_json(url)
        pair = self.pair(asset, quote)
        # Warning - CryptoCompare returns 0 as data for missing dates, convert these to None.
        if 'Data' in json_resp:
            self.update_prices(pair,
                               {datetime.fromtimestamp(d['time']).strftime('%Y-%m-%d'): {
                                   'price': Decimal(repr(d['close'])) if 'close' in d and \
                                           d['close'] else None,
                                   'url': url} for d in json_resp['Data']},
                               timestamp)

class CoinGecko(DataSourceBase):
    # CoinGecko's public (keyless) API only serves the last 365 days of history. A free "Demo"
    #  API key raises the limit and unlocks the full history required for tax calculations.
    #  Set 'coingecko_api_key' in bittytax.conf, or export COINGECKO_API_KEY.
    API_KEY_HEADER = 'x-cg-demo-api-key'
    KEYLESS_MAX_DAYS = 365
    MARKETS_PER_PAGE = 250

    def __init__(self):
        super(CoinGecko, self).__init__()
        json_resp = self.get_json("https://api.coingecko.com/api/v3/coins/list")
        self.ids = {c['id']: {'symbol': c['symbol'].strip().upper(), 'name': c['name'].strip()}
                    for c in json_resp}

        # CoinGecko symbols are not unique (12 coins are called 'BTC', 49 are called 'USDT'), so
        #  the symbol must be resolved to the most significant coin by market cap, otherwise an
        #  arbitrary duplicate (e.g. 'osmosis-allbtc' or 'the-ticker-is-eth') would be priced.
        self.rank = {c['id']: i
                     for i, c in enumerate(self.get_json(self.markets_url()))}
        self.assets = {}
        for listing in json_resp:
            symbol = listing['symbol'].strip().upper()
            asset = {'id': listing['id'], 'name': listing['name'].strip()}
            if symbol not in self.assets or self._more_significant(asset, self.assets[symbol]):
                self.assets[symbol] = asset

        self.get_config_assets()

        if not self.api_key():
            print("%sWARNING%s CoinGecko API key not set, only the last %d days of price data "
                  "are available; historic prices before that will be valued at 0. Add "
                  "'coingecko_api_key' to %s (or export COINGECKO_API_KEY) for full history." % (
                      Back.YELLOW+Fore.BLACK, Back.RESET+Fore.YELLOW, self.KEYLESS_MAX_DAYS,
                      os.path.join(config.BITTYTAX_PATH, config.BITTYTAX_CONFIG)))

    @classmethod
    def markets_url(cls):
        return "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd" \
               "&order=market_cap_desc&per_page=%d&page=1" % cls.MARKETS_PER_PAGE

    def _more_significant(self, asset, current):
        # An unranked (long tail) coin never displaces a ranked one; ties keep the first seen
        return asset['id'] in self.rank and \
            self.rank[asset['id']] < self.rank.get(current['id'], len(self.rank))

    @staticmethod
    def api_key():
        return config.coingecko_api_key or os.environ.get('COINGECKO_API_KEY') or None

    def get_headers(self):
        headers = super(CoinGecko, self).get_headers()
        api_key = self.api_key()
        if api_key:
            headers[self.API_KEY_HEADER] = api_key

        return headers

    def max_days(self):
        # 'max' is only available to API key holders, the public API rejects it
        return 'max' if self.api_key() else str(self.KEYLESS_MAX_DAYS)

    def get_latest(self, asset, quote, asset_id=None):
        if asset_id is None:
            asset_id = self.assets[asset]['id']

        json_resp = self.get_json("https://api.coingecko.com/api/v3/coins/%s?localization=false" \
            "&community_data=false&developer_data=false" % asset_id)
        return Decimal(repr(json_resp['market_data']['current_price'][quote.lower()])) \
                if 'market_data' in json_resp and 'current_price' in json_resp['market_data'] and \
                quote.lower() in json_resp['market_data']['current_price'] else None

    def get_historical(self, asset, quote, timestamp, asset_id=None):
        if asset_id is None:
            asset_id = self.assets[asset]['id']

        url = "https://api.coingecko.com/api/v3/coins/%s/market_chart?vs_currency=%s&days=%s" % (
            asset_id, quote, self.max_days())
        json_resp = self.get_json(url)
        pair = self.pair(asset, quote)
        if 'prices' in json_resp:
            self.update_prices(pair,
                               {datetime.utcfromtimestamp(p[0]/1000).strftime('%Y-%m-%d'): {
                                   'price': Decimal(repr(p[1])) if p[1] else None,
                                   'url': url} for p in json_resp['prices']},
                               timestamp)

class _YahooAssets(dict):
    # Yahoo publishes no asset list, so every symbol is accepted and an unknown one is resolved
    #  to a ticker built from the asset and quote when the price is requested.
    def __contains__(self, symbol):
        return True

    def __missing__(self, symbol):
        return {'id': None, 'name': symbol}


class YahooFinance(DataSourceBase):
    # The Yahoo Finance chart API needs no API key and returns the whole daily price history in a
    #  single request (crypto back to 2014), so it can price dates outside CoinGecko's keyless
    #  365 day window. It has no asset list and accepts any symbol, so it works best as a
    #  fallback after a source that knows which assets it supports.
    #
    #  Tickers are derived from the asset and quote, crypto is 'BTC-GBP' or 'ETH-BTC' and fiat is
    #  'EURGBP=X'. If Yahoo does not recognise the ticker no price is returned and the next data
    #  source is tried.
    LATEST_DAYS = 5
    # Only used to populate 'bittytax_price list', any symbol can be priced
    SYMBOLS = ['BTC', 'ETH', 'XRP', 'LTC', 'BCH', 'BNB', 'USDT', 'USDC', 'DOGE', 'ADA', 'SOL',
               'DOT', 'LINK', 'XLM', 'TRX', 'ATOM', 'AVAX', 'ETC', 'XMR', 'ALGO', 'MATIC', 'DAI']

    def __init__(self):
        super(YahooFinance, self).__init__()
        self.assets = _YahooAssets()
        self.ids = {}

    def get_config_assets(self):
        # Every symbol is already accepted, so only an explicit ticker override is recorded
        for symbol in config.data_source_select:
            for data_source in config.data_source_select[symbol]:
                prefix, _, ticker = data_source.partition(':')
                if prefix.upper() == self.name().upper() and ticker:
                    self.assets[symbol] = {'id': ticker, 'name': symbol}

    def get_list(self):
        return {symbol: [{'id': None, 'name': symbol}] for symbol in self.SYMBOLS}

    @staticmethod
    def build_ticker(asset, quote):
        if asset in config.fiat_list:
            return '%s%s=X' % (asset, quote)
        return '%s-%s' % (asset, quote)

    def ticker(self, asset, quote, asset_id=None):
        return asset_id or self.assets[asset]['id'] or self.build_ticker(asset, quote)

    def get_latest(self, asset, quote, asset_id=None):
        now = int(datetime.now().timestamp())
        json_resp = self.get_json(self.chart_url(self.ticker(asset, quote, asset_id),
                                                 now - self.LATEST_DAYS * 86400, now))
        result = self.chart_result(json_resp)
        if result and 'regularMarketPrice' in result['meta']:
            return Decimal(repr(result['meta']['regularMarketPrice']))

        return None

    def get_historical(self, asset, quote, timestamp, asset_id=None):
        url = self.chart_url(self.ticker(asset, quote, asset_id), 0,
                             int(datetime.now().timestamp()) + 86400)
        result = self.chart_result(self.get_json(url))
        if result and 'timestamp' in result:
            self.update_prices(self.pair(asset, quote),
                               {datetime.fromtimestamp(ts, config.TZ_UTC).strftime('%Y-%m-%d'): {
                                   'price': Decimal(repr(close)), 'url': url}
                                for ts, close in zip(result['timestamp'],
                                                     result['indicators']['quote'][0]['close'])
                                if close is not None},
                               timestamp)

    @staticmethod
    def chart_url(ticker, period1, period2):
        return "https://query1.finance.yahoo.com/v8/finance/chart/%s" \
               "?period1=%d&period2=%d&interval=1d" % (quote(ticker, safe='-^='), period1,
                                                       period2)

    @staticmethod
    def chart_result(json_resp):
        results = json_resp.get('chart', {}).get('result')
        return results[0] if results else None


class CoinPaprika(DataSourceBase):
    def __init__(self):
        super(CoinPaprika, self).__init__()
        json_resp = self.get_json("https://api.coinpaprika.com/v1/coins")
        self.ids = {c['id']: {'symbol': c['symbol'].strip().upper(), 'name': c['name'].strip()}
                    for c in json_resp}
        self.assets = {c['symbol'].strip().upper(): {'id': c['id'], 'name': c['name'].strip()}
                       for c in json_resp}
        self.get_config_assets()

    def get_latest(self, asset, quote, asset_id=None):
        if asset_id is None:
            asset_id = self.assets[asset]['id']

        json_resp = self.get_json("https://api.coinpaprika.com/v1/tickers/%s?quotes=%s" % (
            (asset_id, quote)))
        return Decimal(repr(json_resp['quotes'][quote]['price'])) \
                if 'quotes' in json_resp and quote in json_resp['quotes'] else None

    def get_historical(self, asset, quote, timestamp, asset_id=None):
        # Historic prices only available in USD or BTC
        if quote not in ('USD', 'BTC'):
            return

        if asset_id is None:
            asset_id = self.assets[asset]['id']

        url = "https://api.coinpaprika.com/v1/tickers/%s/historical" \
              "?start=%s&limit=%s&quote=%s&interval=1d" % (
                  asset_id, timestamp.strftime('%Y-%m-%d'), COINPAPRIKA_MAX_DAYS, quote)

        json_resp = self.get_json(url)
        pair = self.pair(asset, quote)
        self.update_prices(pair,
                           {dateutil.parser.parse(p['timestamp']).strftime('%Y-%m-%d'): {
                               'price': Decimal(repr(p['price'])) if p['price'] else None,
                               'url': url} for p in json_resp},
                           timestamp)
