# -*- coding: utf-8 -*-
# (c) Nano Nano Ltd 2020
# (c) Steven Mirabito 2022
# Generate the constants for the Gemini parser.

import requests

quote_assets = set()
base_assets = set()

symbols_resp = requests.get("https://api.gemini.com/v1/symbols")
if symbols_resp.ok:
    for symbol in symbols_resp.json():
        detail_resp = requests.get("https://api.gemini.com/v1/symbols/details/%s" % symbol)
        if detail_resp.ok:
            symbol_detail = detail_resp.json()
            quote_assets.add(symbol_detail["quote_currency"])
            base_assets.add(symbol_detail["base_currency"])

    rows = []
    for i in range(0, len(quote_assets), 10):
        rows.append(", ".join("\'{}\'".format(v)
                              for v in sorted(quote_assets)[i:i+10]))

    print("QUOTE_ASSETS = [%s]\n" % (',\n                '.join(rows)))

    rows = []
    for i in range(0, len(base_assets), 10):
        rows.append(", ".join("\'{}\'".format(v)
                              for v in sorted(base_assets)[i:i+10]))

    print("BASE_ASSETS = [%s]\n" % (',\n                '.join(rows)))
