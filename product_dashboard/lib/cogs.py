# -*- coding: utf-8 -*-
"""Per-product, per-day COGS in EUR from DropshippingLite (ns-client AI API).

Same method as pnl-KIZORA/scripts/sync_cogs.py, one level finer:

    per order    sum of its line item costs + shipping ONCE (the largest line);
                 the API returns one row per product, each carrying the full
                 shipping, but the portal charges collected shipping once
    per invoice  scale that invoice's orders so their sum equals the real
                 invoice total; a factor further than 50 % from 1 is ignored
                 and the estimate kept
    per product  split the order across its products by their share of item cost
    then         USD -> EUR at that day's rate

Dates are the order's date in the shop's timezone, exactly as the revenue is
dated. The API timestamps are UTC and Helsinki is three hours ahead, so a
late-evening order otherwise lands on the day before.

Checked on 2026-10-05 against the values already in the dashboard:
Nervora 27.08. 84.54 vs 84.53, Nervora 28.08. 70.84 vs 70.84.
"""

import collections
import datetime as dt
import json
import os
import subprocess
import urllib.parse
import zoneinfo

from normalize import normalize

ORDERS_ENDPOINT = "/api/ai/v1/orders"
INVOICES_ENDPOINT = "/api/ai/v1/invoice_orders"
PER_PAGE = 100
MAX_SCALE_DRIFT = 0.5          # beyond this the invoice factor is implausible
FX_URL = "https://api.frankfurter.dev/v1/{start}..{end}?base=USD&symbols=EUR"


class CogsError(RuntimeError):
    pass


def credentials():
    base = os.environ.get("NS_CLIENT_AI_BASE_URL") or os.environ.get("BASE_URL")
    key = (os.environ.get("NS_CLIENT_AI_ACCESS_KEY_ID")
           or os.environ.get("DSL_ACCESS_KEY_ID"))
    secret = os.environ.get("NS_CLIENT_AI_SECRET") or os.environ.get("DSL_SECRET")
    missing = [n for n, v in (("NS_CLIENT_AI_BASE_URL", base),
                              ("NS_CLIENT_AI_ACCESS_KEY_ID", key),
                              ("NS_CLIENT_AI_SECRET", secret)) if not v]
    if missing:
        raise CogsError("missing credentials: " + ", ".join(missing))
    return base.rstrip("/"), key, secret


def _get(base, key, secret, endpoint, params, retries=2):
    """curl rather than a Python client: Cloudflare blocks the Python
    user agent with error 1010. Same call pnl-KIZORA makes."""
    import time
    url = base + endpoint + "?" + urllib.parse.urlencode(params)
    cmd = ["curl", "-sS", "--max-time", "90", "-w", "\n%{http_code}",
           "-H", "Authorization: Bearer {}.{}".format(key, secret),
           "-H", "Accept: application/json", url]
    for attempt in range(retries + 1):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise CogsError("curl failed: " + (proc.stderr or "").strip()[:200])
        body, _, status = proc.stdout.rpartition("\n")
        status = status.strip()
        if status == "429" and attempt < retries:
            time.sleep(5)
            continue
        if status != "200":
            raise CogsError("ns-client HTTP %s: %s" % (status, body[:200]))
        return json.loads(body)
    raise CogsError("ns-client rate limited")


def _all(base, key, secret, endpoint, list_key):
    items, page, total = [], 1, None
    while True:
        data = _get(base, key, secret, endpoint, {"page": page, "per_page": PER_PAGE})
        batch = data.get(list_key) or []
        items.extend(batch)
        if total is None:
            total = data.get("total_count", 0)
        if not batch or len(items) >= total or page > 500:
            return items
        page += 1


def _number(value):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _day(order, tz):
    """The order's date in the shop's timezone."""
    local = order.get("source_created_at_str")
    if local:
        try:
            return dt.datetime.fromisoformat(local).astimezone(tz).date().isoformat()
        except ValueError:
            pass
    utc = order.get("source_created_at") or order.get("created_at")
    if not utc:
        return None
    try:
        return dt.datetime.fromisoformat(
            utc.replace("Z", "+00:00")).astimezone(tz).date().isoformat()
    except ValueError:
        return None


def _rates(start, end):
    url = FX_URL.format(start=start, end=end)
    proc = subprocess.run(["curl", "-sS", "--max-time", "60", url],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise CogsError("exchange rates unreachable: " + (proc.stderr or "")[:160])
    data = json.loads(proc.stdout)
    out = {}
    for day, values in (data.get("rates") or {}).items():
        rate = values.get("EUR") if isinstance(values, dict) else values
        if rate:
            out[day] = float(rate)
    if not out:
        raise CogsError("exchange rates came back empty")
    return out


def fetch(domain, timezone, log=None):
    """-> ({normalized product key: {date: EUR}}, notes)."""
    tz = zoneinfo.ZoneInfo(timezone)
    base, key, secret = credentials()
    notes = []
    orders = _all(base, key, secret, ORDERS_ENDPOINT, "orders")
    invoices = _all(base, key, secret, INVOICES_ENDPOINT, "orders")

    groups = collections.defaultdict(list)
    hidden = 0
    for order in orders:
        # 'hidden' orders are never invoiced, so they carry no cost.
        if (order.get("fulfillment_status") or "").lower() == "hidden":
            hidden += 1
            continue
        if (order.get("myshopify_domain") or "").strip().lower() != domain:
            continue
        date = _day(order, tz)
        if not date:
            continue
        name = (order.get("source_order_name") or order.get("order_number") or "").strip()
        groups[(date, name, (order.get("invoice_number") or "").strip())].append({
            "title": (order.get("title") or order.get("product_title") or "").strip(),
            "item": _number(order.get("total_item_cost")),
            "ship": _number(order.get("shipping_cost")),
        })

    estimate = {k: sum(r["item"] for r in rows) + max(r["ship"] for r in rows)
                for k, rows in groups.items()}

    totals = {(i.get("invoice_number") or "").strip(): _number(i.get("total_cost"))
              for i in invoices}
    by_invoice = collections.defaultdict(list)
    for k in estimate:
        if k[2]:
            by_invoice[k[2]].append(k)
    scaled = 0
    for number, keys in by_invoice.items():
        target = totals.get(number)
        if not target:
            continue
        current = sum(estimate[k] for k in keys)
        if current <= 0:
            continue
        factor = target / current
        if abs(factor - 1) > MAX_SCALE_DRIFT:
            notes.append("invoice %s: factor %.2f is implausible, estimate kept"
                         % (number, factor))
            continue
        for k in keys:
            estimate[k] *= factor
        scaled += len(keys)

    usd = collections.defaultdict(lambda: collections.defaultdict(float))
    for (date, name, invoice), rows in groups.items():
        value = estimate[(date, name, invoice)]
        item_total = sum(r["item"] for r in rows)
        for row in rows:
            share = (row["item"] / item_total) if item_total > 0 else (1.0 / len(rows))
            usd[normalize(row["title"])][date] += value * share

    days = sorted({d for product in usd.values() for d in product})
    if not days:
        return {}, notes + ["no orders found for %s" % domain]
    rates = _rates(days[0], days[-1])

    def to_eur(date, amount):
        rate = rates.get(date)
        if rate is None:
            earlier = [d for d in sorted(rates) if d <= date]
            rate = rates[earlier[-1]] if earlier else None
        return amount * rate if rate else None

    out = {}
    unconverted = set()
    for product, per_day in usd.items():
        converted = {}
        for date, amount in per_day.items():
            value = to_eur(date, amount)
            if value is None:
                unconverted.add(date)
                continue
            converted[date] = value
        if converted:
            out[product] = converted
    if unconverted:
        notes.append("no USD/EUR rate for: %s" % ", ".join(sorted(unconverted)[:10]))
    notes.append("orders %d (invoice-scaled %d, hidden skipped %d), products %d"
                 % (len(groups), scaled, hidden, len(out)))
    if log is not None:
        log.extend(notes)
    return out, notes
