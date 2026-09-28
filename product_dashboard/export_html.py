#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reads the PRODUCT DASHBOARD sheet and writes dashboard.html from it.

The sheet is the single source of truth: whatever the nightly run put there is
exactly what the HTML shows. Nothing is recomputed here.

    python3 export_html.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

import gclients            # noqa: E402
from model import DAY_COLUMNS   # noqa: E402

CONFIG_PATH = os.path.join(HERE, "config", "dashboard.config.json")
TEMPLATE = os.path.join(HERE, "dashboard_template.html")
OUTPUT = os.path.join(HERE, "dashboard.html")

MONEY = {"Revenue", "Refunds", "Ad Spend", "CPA", "Meta CPA", "Meta CPM", "Meta CPC"}
NUMERIC = set(DAY_COLUMNS[1:]) - {"Ad Account"}


def value(raw):
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        return raw
    return raw


def main():
    config = json.load(open(CONFIG_PATH))
    spreadsheet_id = config["output"]["spreadsheet_id"]
    if not spreadsheet_id:
        raise SystemExit("output.spreadsheet_id is empty in config/dashboard.config.json")
    sheets = gclients.sheets()

    meta = gclients.call(sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties.title"))
    titles = [s["properties"]["title"] for s in meta["sheets"]]

    enabled = [(code, spec) for code, spec in config["markets"].items()
               if spec.get("enabled")]
    export = {"generated_at": "", "spreadsheet_url":
              "https://docs.google.com/spreadsheets/d/%s/edit" % spreadsheet_id,
              "markets": {}}

    # The per-product state (winner, status, launch date) already sits in the
    # run's own export; the sheet holds the numbers. Both come from the same run.
    run = json.load(open(os.path.join(HERE, "data", "dashboard_data.json")))
    export["generated_at"] = run["generated_at"]

    for code, spec in enabled:
        block = run["markets"].get(code)
        if not block:
            continue
        tabs = []
        for product in block["products"]:
            tab = "%s %s" % (code, product["name"])
            tabs.append("".join(c for c in tab if c not in "[]*?/\\:").strip()[:95])

        # One batchGet per 60 tabs: a call per product would trip the
        # 60-reads-per-minute quota at the twentieth product.
        rows_by_tab = {}
        wanted = [t for t in tabs if t in titles]
        for start in range(0, len(wanted), 60):
            chunk = wanted[start:start + 60]
            answer = gclients.call(sheets.spreadsheets().values().batchGet(
                spreadsheetId=spreadsheet_id,
                ranges=["'%s'!A3:P400" % t for t in chunk],
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="FORMATTED_STRING"))
            for tab, result in zip(chunk, answer.get("valueRanges", [])):
                rows_by_tab[tab] = result.get("values", [])

        products = []
        for product, tab in zip(block["products"], tabs):
            days = []
            for row in rows_by_tab.get(tab, []):
                day = {}
                for index, name in enumerate(DAY_COLUMNS):
                    day[name] = value(row[index]) if index < len(row) else None
                if day["Date"]:
                    days.append(day)
            item = dict(product)
            item["days"] = days
            products.append(item)
        export["markets"][code] = {"name": spec["name"], "stats": block["stats"],
                                   "products": products}

    template = open(TEMPLATE).read()
    payload = json.dumps(export, separators=(",", ":"))
    html = template.replace("__DASHBOARD_DATA__", payload.replace("</", "<\\/"))
    with open(OUTPUT, "w") as handle:
        handle.write(html)
    print("wrote %s (%.0f KB, %d markets, %d products)"
          % (OUTPUT, len(html) / 1024.0, len(export["markets"]),
             sum(len(m["products"]) for m in export["markets"].values())))


if __name__ == "__main__":
    main()
