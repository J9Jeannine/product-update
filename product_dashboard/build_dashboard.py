#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRODUCT DASHBOARD - NOVELISKA: pull the four sources and write the sheet.

    python3 build_dashboard.py --dry-run          # read everything, write nothing
    python3 build_dashboard.py --days 7           # the nightly run
    python3 build_dashboard.py --full             # every day since each launch

Principles, in order of precedence:
  * Never estimate. A value that no source delivers stays empty.
  * Never guess a name. Anything that does not normalize onto a known product
    is listed under "Unmatched" and left out of the numbers.
  * One failing source never blanks the others.
"""

import argparse
import collections
import csv
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

import gclients            # noqa: E402
import sources            # noqa: E402
import shopify_api        # noqa: E402
import sheet_writer       # noqa: E402
from model import build_series, winner_rate   # noqa: E402
from normalize import normalize               # noqa: E402

CONFIG_PATH = os.path.join(HERE, "config", "dashboard.config.json")
MAPPING_DIR = os.path.join(HERE, "mapping")
DATA_DIR = os.path.join(HERE, "data")


def load_config():
    with open(CONFIG_PATH) as handle:
        return json.load(handle)


# --------------------------------------------------------------------- mapping
def write_mapping(market, products, adspend_log, winners, catalog, sold_titles,
                  overrides, series):
    """The mapping table and the unmatched list, as files in the repo."""
    os.makedirs(MAPPING_DIR, exist_ok=True)
    by_key = {s.key: s for s in series}
    rows = []
    for key, product in products.items():
        item = by_key.get(key)
        rows.append({
            "market": market,
            "funnel_name": " / ".join(product.names),
            "normalized_key": key,
            "funnel_status": product.status,
            "tested": "YES" if product.tested else "no",
            "adspend_names": " | ".join(sorted(adspend_log["raw_names"].get(key, []))),
            "performance_tab": winners.get(key, ""),
            "shopify_title": " | ".join(sorted(catalog.get(key, []))),
            "winner_loser": ("WINNER" if key in winners else "LOSER") if product.tested else "",
            "first_adspend_date": item.launch_date if item else "",
            "days_live": item.days_live if item else 0,
        })
    rows.sort(key=lambda r: r["funnel_name"].lower())
    path = os.path.join(MAPPING_DIR, "mapping_%s.csv" % market)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    known = set(products)
    unmatched = []
    for key, names in sorted(adspend_log["raw_names"].items()):
        if key not in known and key not in overrides:
            unmatched.append(["DAILY ADSPEND (%s)" % market, " | ".join(sorted(names)), key,
                              "no product with this base name in the Funnel Sheet tab"])
    for key, names in sorted(adspend_log["unknown_market"].items()):
        unmatched.append(["DAILY ADSPEND (market unknown)", " | ".join(sorted(names)), key,
                          "market not derivable from product name or ad account"])
    for key, tab in sorted(winners.items()):
        if key not in known:
            unmatched.append(["Product Performance tab", tab, key,
                              "no product with this base name in the Funnel Sheet tab"])
    for key, titles in sorted(sold_titles.items()):
        # Only titles that actually produced revenue in this market's window.
        # An unsold catalogue entry is not a mapping gap, it is just unsold.
        if key not in known and key not in overrides:
            unmatched.append(["Shopify title with revenue", " | ".join(sorted(titles)), key,
                              "sold in this market but no product with this base name "
                              "in the Funnel Sheet tab"])
    for item in series:
        if item.days and not catalog.get(item.key) and item.key not in overrides:
            unmatched.append(["Funnel product without Shopify product", item.name, item.key,
                              "no Shopify product normalizes onto this name - "
                              "revenue, orders and units stay empty"])
    path = os.path.join(MAPPING_DIR, "unmatched_%s.csv" % market)
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "raw_value", "normalized_key", "reason"])
        writer.writerows(unmatched)
    return rows, unmatched


def load_overrides():
    """mapping/overrides.csv: shopify_title,normalized_key - one line per title
    that cannot be derived mechanically. Reviewed by hand, never generated."""
    path = os.path.join(MAPPING_DIR, "overrides.csv")
    out = {}
    if not os.path.exists(path):
        return out
    with open(path) as handle:
        for row in csv.DictReader(handle):
            source = (row.get("source_value") or "").strip()
            key = (row.get("normalized_key") or "").strip()
            if source and key:
                out[normalize(source)] = key
    return out


def apply_overrides(mapping, overrides):
    """Move entries whose normalized key has a hand-reviewed target."""
    if not overrides:
        return mapping
    out = collections.defaultdict(dict)
    for key, value in mapping.items():
        out[overrides.get(key, key)].update(value)
    return out


# ------------------------------------------------------------------ cross-check
def cross_check(store_totals, series, days):
    """For the last complete days the dashboard covers, three figures:

      dashboard        what the sheet shows, summed over all products
      shopify_products all line items of that day, same revenue definition
      shopify_orders   the order-level subtotal, i.e. after later refunds

    dashboard - shopify_products is revenue from products the dashboard does
    not show that day (no ad-spend row, or no mapping). shopify_products -
    shopify_orders is what was refunded or edited after the fact.
    """
    per_day = collections.defaultdict(float)
    covered = set()
    for item in series:
        for row in item.days:
            covered.add(row["Date"])
            if row["Revenue"] is not None:
                per_day[row["Date"]] += row["Revenue"]
    dates = sorted(d for d in store_totals if d in covered)[-days:]
    out = []
    for date in dates:
        dashboard = per_day.get(date, 0.0)
        totals = store_totals[date]
        out.append({
            "date": date,
            "dashboard": round(dashboard, 2),
            "shopify_products": round(totals["line_items"], 2),
            "shopify_orders": round(totals["order_subtotal"], 2),
            "difference_vs_products": round(dashboard - totals["line_items"], 2),
            "difference_vs_orders": round(dashboard - totals["order_subtotal"], 2),
        })
    return out


# ------------------------------------------------------------------------- main
def run(args):
    config = load_config()
    report = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "markets": {}, "notes": []}
    sheets = gclients.sheets()
    drive = gclients.drive()

    tabs = sources.read_performance_tabs(drive, config)
    report["notes"].append("Product Performance: %d tabs read" % len(tabs))

    overrides = load_overrides()
    spreadsheet_id = config["output"]["spreadsheet_id"]
    market_blocks = []
    export = {"generated_at": report["generated_at"], "markets": {}}

    for market, spec in config["markets"].items():
        if not spec.get("enabled"):
            continue
        products = sources.read_funnel(sheets, config, market)
        adspend, adspend_log = sources.read_adspend(sheets, config, market)
        adspend = apply_overrides(adspend, overrides)
        winners = sources.winners_for_market(tabs, market)

        store = shopify_api.Store(config)
        today = dt.datetime.now(store.tz).date()
        earliest_adspend = min(
            (min(days) for days in adspend.values() if days), default=today.isoformat())
        if args.full:
            start = dt.date.fromisoformat(earliest_adspend)
        else:
            start = today - dt.timedelta(days=args.days)
        retention_floor = today - dt.timedelta(days=shopify_api.RETENTION_DAYS - 1)
        if start < retention_floor:
            report["notes"].append(
                "Shopify returns only the last %d days (no read_all_orders scope). "
                "Revenue before %s stays empty."
                % (shopify_api.RETENTION_DAYS, retention_floor.isoformat()))
            start = retention_floor

        catalog = shopify_api.fetch_catalog(store)
        sales, store_totals, sales_log = shopify_api.fetch_sales(
            store, start, today, spec["shopify_country"])
        refunds, refund_log = shopify_api.fetch_refunds(
            store, start, spec["shopify_country"])
        sold_titles = dict(sales_log["titles"])
        sales = apply_overrides(sales, overrides)
        refunds = apply_overrides(refunds, overrides)
        for source_key, target_key in overrides.items():
            if source_key in catalog:
                catalog.setdefault(target_key, set()).update(catalog[source_key])

        series = build_series(products, adspend, winners, sales, refunds,
                              start.isoformat(), catalog)
        rows, unmatched = write_mapping(market, products, adspend_log, winners,
                                        catalog, sold_titles, overrides, series)
        stats = winner_rate(series)
        market_blocks.append((market, spec["name"], series))

        report["markets"][market] = {
            "products_in_funnel": len(products),
            "tested": stats["tested"],
            "winners": stats["winners"],
            "losers": stats["losers"],
            "winner_rate": stats["rate"],
            "adspend_rows_kept": adspend_log["rows_kept"],
            "adspend_rows_excluded_ppe_banned": adspend_log["excluded"],
            "adspend_duplicate_rows": len(adspend_log["duplicates"]),
            "adspend_duplicates": adspend_log["duplicates"][:20],
            "shopify_orders_used": sales_log["orders_used"],
            "shopify_orders_skipped_test": sales_log["skipped_test"],
            "shopify_orders_skipped_cancelled": sales_log["skipped_cancelled"],
            "shopify_orders_other_country": dict(sales_log["skipped_country"]),
            "shopify_window_from": start.isoformat(),
            "refund_lines": refund_log["refund_rows"],
            "disputes_counted": refund_log["disputes_counted"],
            "refund_notes": refund_log["notes"][:20],
            "unmatched": unmatched,
            "cross_check": cross_check(store_totals, series, 2),
        }
        export["markets"][market] = {
            "name": spec["name"],
            "stats": stats,
            "products": [{
                "name": item.name, "key": item.key, "winner": item.is_winner,
                "performance_tab": item.winner, "status": item.status,
                "launch_date": item.launch_date, "days_live": item.days_live,
                "total_spend": item.total_spend, "total_revenue": item.total_revenue,
                "total_orders": item.total_orders, "roas": item.roas, "cpa": item.cpa,
                "adspend_names": item.adspend_names,
                "shopify_titles": item.shopify_titles,
                "days": item.days,
            } for item in series],
        }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "dashboard_data.json"), "w") as handle:
        json.dump(export, handle, indent=1)

    if args.dry_run:
        report["notes"].append("dry run - nothing written to the Google Sheet")
    elif not spreadsheet_id:
        report["notes"].append(
            "output.spreadsheet_id is empty in config/dashboard.config.json - "
            "nothing written. Create the sheet first (see README).")
    else:
        written = {"overview_rows": 0, "product_tabs": 0, "day_rows": 0,
                   "api_requests": 0}
        wanted = ["Overview"] + [s.tab_name() for _, _, block in market_blocks for s in block]
        sheet_writer.ensure_spreadsheet(
            sheets, spreadsheet_id, config["output"]["title"],
            config["schedule"]["timezone"])
        ids = sheet_writer.ensure_tabs(
            sheets, spreadsheet_id, wanted,
            [code for code, spec in config["markets"].items() if spec.get("enabled")])
        rules = sheet_writer.conditional_format_counts(sheets, spreadsheet_id)

        requests, written["overview_rows"] = sheet_writer.overview_requests(
            ids["Overview"], market_blocks, rules.get(ids["Overview"], 0))
        for _, _, block in market_blocks:
            for item in block:
                sheet_id = ids[item.tab_name()]
                requests += sheet_writer.product_tab_requests(
                    sheet_id, item, rules.get(sheet_id, 0))
                written["product_tabs"] += 1
                written["day_rows"] += item.days_live
        written["api_requests"] = sheet_writer.flush(sheets, spreadsheet_id, requests)
        report["written"] = written
        report["spreadsheet_url"] = (
            "https://docs.google.com/spreadsheets/d/%s/edit" % spreadsheet_id)

    return report


def print_report(report):
    print("=" * 72)
    print("PRODUCT DASHBOARD - NOVELISKA   run %s" % report["generated_at"])
    print("=" * 72)
    for note in report["notes"]:
        print("note: %s" % note)
    for market, block in report["markets"].items():
        print("\n--- %s ---" % market)
        print("  funnel products %d | tested %d | winners %d | losers %d | winner rate %s"
              % (block["products_in_funnel"], block["tested"], block["winners"],
                 block["losers"],
                 "-" if block["winner_rate"] is None else "%.1f%%" % (block["winner_rate"] * 100)))
        print("  ad spend: %d rows kept, %d PPE/BANNED excluded, %d duplicates dropped"
              % (block["adspend_rows_kept"], block["adspend_rows_excluded_ppe_banned"],
                 block["adspend_duplicate_rows"]))
        for dup in block["adspend_duplicates"]:
            print("      duplicate: %s" % (dup,))
        print("  shopify: %d orders used from %s, %d test, %d cancelled, other countries %s"
              % (block["shopify_orders_used"], block["shopify_window_from"],
                 block["shopify_orders_skipped_test"],
                 block["shopify_orders_skipped_cancelled"],
                 block["shopify_orders_other_country"] or "{}"))
        print("  refunds: %d refund lines, %d lost chargebacks counted"
              % (block["refund_lines"], block["disputes_counted"]))
        for note in block["refund_notes"]:
            print("      %s" % note)
        print("  cross-check against Shopify, same day, same revenue definition:")
        for row in block["cross_check"]:
            print("      %s  dashboard %9.2f | all products %9.2f (%+.2f) | "
                  "order subtotal %9.2f (%+.2f)"
                  % (row["date"], row["dashboard"], row["shopify_products"],
                     row["difference_vs_products"], row["shopify_orders"],
                     row["difference_vs_orders"]))
        print("  unmatched (%d):" % len(block["unmatched"]))
        for row in block["unmatched"]:
            print("      %-38s %-32s %s" % (row[0], row[1], row[3]))
    if "written" in report:
        print("\nwritten: Overview %d rows, %d product tabs, %d day rows "
              "(%d API requests)"
              % (report["written"]["overview_rows"], report["written"]["product_tabs"],
                 report["written"]["day_rows"], report["written"]["api_requests"]))
        print("sheet: %s" % report["spreadsheet_url"])
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7,
                        help="how many days back to re-pull and overwrite (default 7)")
    parser.add_argument("--full", action="store_true",
                        help="rebuild every day since each product's first ad-spend day")
    parser.add_argument("--dry-run", action="store_true",
                        help="read and report, write nothing to the sheet")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args()

    report = run(args)
    if args.json:
        print(json.dumps(report, indent=1, default=str))
    else:
        print_report(report)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "last_run.json"), "w") as handle:
        json.dump(report, handle, indent=1, default=str)


if __name__ == "__main__":
    main()
