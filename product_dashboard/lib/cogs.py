# -*- coding: utf-8 -*-
"""Per-product, per-day COGS in EUR from DropshippingLite (ns-client AI API).

COGS = product cost + shipping, both as the portal invoices them.

    product cost  each row's total_item_cost, exactly as delivered
    shipping      each invoice's shipping is its total_cost minus the product
                  costs of its rows; that amount is split onto the rows
    per product   the rows of that product, dated by their order
    then          USD -> EUR at that day's rate

The API returns one row per product line, each with the shipping that line
would cost on its own (`shipping_cost`). The warehouse ships everything that
carries the same tracking number as one parcel and charges one base fee for
it, so an invoice comes in below the sum of its rows by one base fee for
every extra row in a shared parcel (checked on all 115 Noveliska invoices up
to 4 Oct 2026: 109 match to the cent; the other six are a rate change in late
June and small adjustments of a few dollars).
The split follows that:

    1. a parcel of k rows gets (k - 1) shares of the invoice's saving, never
       more than all but its dearest row; within the parcel the saving is
       split by the rows' own shipping
    2. whatever is left (a credit or a rate change on the invoice, or a
       saving with no shared parcel) goes onto every row of the invoice by
       its share of shipping

So every invoice adds up to its total to the cent, and a parcel's saving
stays with the products that were in it. A row with no invoice yet keeps
its own shipping_cost until the invoice arrives; the nightly re-pull of the
last 7 days replaces it.

Dates are the order's date in the shop's timezone, exactly as the revenue is
dated. The API timestamps are UTC and Helsinki is three hours ahead, so a
late-evening order otherwise lands on the day before.

Checked against invoice MU11341abb0b27bafea (4 Oct 2026, $211.84): every
invoice adds up to its total; ViroFlow's 18.16 shipping is the invoice's
Sensitive line to the cent; the 13.26 saving sits on the two shared parcels,
#NOVSE2186 (Lympha) and #NOVSE2184/2185 (FlexiVera).
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


def allocate_invoice(rows, total, notes, number):
    """Set row["ship_final"] so the rows add up to the invoice total. See the
    module docstring for the rule."""
    pool = total - sum(r["item"] for r in rows)
    raw = sum(r["ship"] for r in rows)
    saving = raw - pool
    if abs(saving) < 0.005:
        return
    parcels = collections.defaultdict(list)
    for row in rows:
        parcels[row["parcel"]].append(row)
    shared = [p for p in parcels.values() if len(p) > 1]
    extra = sum(len(p) - 1 for p in shared)
    left = saving
    if saving > 0 and extra:
        per_extra = saving / extra
        for parcel in shared:
            own = sum(r["ship"] for r in parcel)
            cap = own - max(r["ship"] for r in parcel)
            cut = min(per_extra * (len(parcel) - 1), cap)
            for row in parcel:
                row["ship_final"] -= cut * (row["ship"] / own if own else 1.0 / len(parcel))
            left -= cut
    if abs(left) >= 0.005:
        if raw <= 0:
            notes.append("invoice %s: %.2f of shipping and no row to carry it"
                         % (number, -left))
            return
        for row in rows:
            row["ship_final"] -= left * row["ship"] / raw
        if abs(left) >= 0.5:
            notes.append("invoice %s: %+.2f not explained by shared parcels, "
                         "spread over its rows by shipping" % (number, -left))


def fetch(domain, timezone, log=None):
    """-> ({normalized product key: {date: EUR}}, notes)."""
    tz = zoneinfo.ZoneInfo(timezone)
    base, key, secret = credentials()
    notes = []
    orders = _all(base, key, secret, ORDERS_ENDPOINT, "orders")
    invoices = _all(base, key, secret, INVOICES_ENDPOINT, "orders")

    rows = []
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
        rows.append({
            "date": date,
            "invoice": (order.get("invoice_number") or "").strip(),
            # One parcel = one tracking number; before it has one, its order.
            "parcel": (order.get("tracking_number") or "").strip() or name,
            "title": (order.get("title") or order.get("product_title") or "").strip(),
            "item": _number(order.get("total_item_cost")),
            "ship": _number(order.get("shipping_cost")),
        })
    for row in rows:
        row["ship_final"] = row["ship"]

    totals = {(i.get("invoice_number") or "").strip(): _number(i.get("total_cost"))
              for i in invoices}
    by_invoice = collections.defaultdict(list)
    for row in rows:
        if row["invoice"]:
            by_invoice[row["invoice"]].append(row)
    invoiced = 0
    for number, inv_rows in by_invoice.items():
        if number not in totals:
            continue
        invoiced += len(inv_rows)
        allocate_invoice(inv_rows, totals[number], notes, number)
    uninvoiced = len(rows) - invoiced

    usd = collections.defaultdict(lambda: collections.defaultdict(float))
    for row in rows:
        usd[normalize(row["title"])][row["date"]] += row["item"] + row["ship_final"]

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
    notes.append("rows %d (invoiced %d, not yet invoiced %d, hidden skipped %d), "
                 "products %d" % (len(rows), invoiced, uninvoiced, hidden, len(out)))
    if log is not None:
        log.extend(notes)
    return out, notes
