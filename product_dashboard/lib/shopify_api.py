# -*- coding: utf-8 -*-
"""Shopify Admin API: daily revenue, orders, units and refunds per product.

Authentication is the client-credentials grant, exactly as
pnl-KIZORA/scripts/update_revenue.py does it: the store's client id and
secret are exchanged for a short-lived Admin API token at call time. Nothing
is stored.

Everything here is read-only.
"""

import collections
import datetime as dt
import json
import os
import subprocess
import zoneinfo

from normalize import normalize

SHOP_QUERY = "{ shop { name currencyCode ianaTimezone } }"

ORDERS_QUERY = """
query($q: String!, $cursor: String) {
  orders(first: 50, after: $cursor, query: $q) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      name createdAt test cancelledAt
      shippingAddress { countryCodeV2 }
      currentTotalPriceSet { shopMoney { amount } }
      currentSubtotalPriceSet { shopMoney { amount } }
      lineItems(first: 50) { edges { node {
        title quantity
        product { title }
        discountedTotalSet { shopMoney { amount } }
      } } }
    } }
  }
}
"""

# Refunds are dated by refund.createdAt, so the orders are fetched via
# updated_at: a refund always touches its order, even a months-old one.
REFUNDS_QUERY = """
query($q: String!, $cursor: String) {
  orders(first: 50, after: $cursor, query: $q) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      name test cancelledAt
      shippingAddress { countryCodeV2 }
      refunds {
        createdAt
        totalRefundedSet { shopMoney { amount currencyCode } }
        refundLineItems(first: 50) { edges { node {
          quantity
          subtotalSet { shopMoney { amount } }
          lineItem { title product { title } }
        } } }
      }
    } }
  }
}
"""

DISPUTES_QUERY = """
query($cursor: String) {
  shopifyPaymentsAccount {
    disputes(first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      edges { node {
        id status type finalizedOn
        amount { amount currencyCode }
        order { name shippingAddress { countryCodeV2 }
                lineItems(first: 50) { edges { node {
                  title quantity product { title }
                  discountedTotalSet { shopMoney { amount } } } } } }
      } }
    }
  }
}
"""

PRODUCTS_QUERY = """
query($cursor: String) {
  products(first: 250, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    edges { node { title status } }
  }
}
"""

DISPUTE_LOST = ("LOST", "ACCEPTED")
DISPUTE_OPEN = ("NEEDS_RESPONSE", "UNDER_REVIEW")

# Without read_all_orders Shopify only returns the last 60 days.
RETENTION_DAYS = 60


class ShopifyError(RuntimeError):
    pass


def _post(url, headers, payload):
    """curl rather than requests, for the same reason as in pnl-KIZORA:
    it goes through the environment's proxy configuration unchanged."""
    cmd = ["curl", "-sS", "--max-time", "90", "-X", "POST", "-w", "\n%{http_code}"]
    for key, value in headers.items():
        cmd += ["-H", "%s: %s" % (key, value)]
    cmd += ["-d", json.dumps(payload), url]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ShopifyError("curl failed: " + (proc.stderr or "").strip()[:300])
    body, _, status = proc.stdout.rpartition("\n")
    return status.strip(), body


class Store(object):
    def __init__(self, config):
        spec = config["sources"]["shopify"]
        self.domain = os.environ[spec["domain_env"]]
        self.client_id = os.environ[spec["client_id_env"]]
        self.client_secret = os.environ[spec["client_secret_env"]]
        self.version = spec["api_version"]
        self.tz = zoneinfo.ZoneInfo(spec["timezone"])
        self._token = None

    # -- auth ---------------------------------------------------------------
    @property
    def token(self):
        if self._token is None:
            status, body = _post(
                "https://%s/admin/oauth/access_token" % self.domain,
                {"Content-Type": "application/json", "Accept": "application/json"},
                {"client_id": self.client_id, "client_secret": self.client_secret,
                 "grant_type": "client_credentials"})
            if status != "200":
                raise ShopifyError("token request HTTP %s: %s" % (status, body[:300]))
            self._token = json.loads(body).get("access_token")
            if not self._token:
                raise ShopifyError("token response carried no access_token")
        return self._token

    def graphql(self, query, variables=None):
        """Shopify answers errors with HTTP 200 and an 'errors' field, so a
        response only counts when it is 200, has no errors and has data."""
        status, body = _post(
            "https://%s/admin/api/%s/graphql.json" % (self.domain, self.version),
            {"X-Shopify-Access-Token": self.token, "Content-Type": "application/json"},
            {"query": query, "variables": variables or {}})
        if status != "200":
            raise ShopifyError("HTTP %s: %s" % (status, body[:300]))
        payload = json.loads(body)
        if payload.get("errors"):
            raise ShopifyError("errors: " + json.dumps(payload["errors"])[:400])
        if "data" not in payload:
            raise ShopifyError("response without data: " + body[:200])
        return payload["data"]

    def _page(self, query, query_string):
        out, cursor = [], None
        while True:
            block = self.graphql(query, {"q": query_string, "cursor": cursor})["orders"]
            out += [edge["node"] for edge in block["edges"]]
            if not block["pageInfo"]["hasNextPage"]:
                return out
            cursor = block["pageInfo"]["endCursor"]

    def day(self, timestamp):
        return dt.datetime.fromisoformat(
            timestamp.replace("Z", "+00:00")).astimezone(self.tz).date().isoformat()

    def _window(self, start, end):
        begin = dt.datetime.combine(start, dt.time.min, self.tz)
        stop = dt.datetime.combine(end, dt.time.min, self.tz) + dt.timedelta(days=1)
        return begin, stop


def _line_items(node):
    for edge in node["lineItems"]["edges"]:
        item = edge["node"]
        title = (item.get("product") or {}).get("title") or item["title"]
        amount = float(item["discountedTotalSet"]["shopMoney"]["amount"])
        yield title, item["quantity"], amount


class ProductDay(object):
    __slots__ = ("revenue", "orders", "units", "refunds")

    def __init__(self):
        self.revenue = 0.0
        self.orders = 0
        self.units = 0
        self.refunds = 0.0


def fetch_catalog(store):
    """-> {normalized key: {title, ...}} over the whole product catalogue.

    This is what the mapping column is built from. The order window only shows
    products that happened to sell, which would make an unsold product look
    like a mapping gap.
    """
    catalog = collections.defaultdict(set)
    cursor = None
    while True:
        block = store.graphql(PRODUCTS_QUERY, {"cursor": cursor})["products"]
        for edge in block["edges"]:
            title = edge["node"]["title"]
            catalog[normalize(title)].add(title)
        if not block["pageInfo"]["hasNextPage"]:
            break
        cursor = block["pageInfo"]["endCursor"]
    return dict(catalog)


def fetch_sales(store, start, end, country):
    """-> ({key: {date: ProductDay}}, {date: store_total}, log).

    Excludes test and cancelled orders and keeps only orders shipping to
    `country`. Revenue per product is the line item total after discounts,
    which sums exactly to the order subtotal.
    """
    begin, stop = store._window(start, end)
    orders = store._page(
        ORDERS_QUERY,
        "created_at:>='%s' AND created_at:<'%s'" % (begin.isoformat(), stop.isoformat()))

    by_product = collections.defaultdict(dict)
    store_total = collections.defaultdict(lambda: {"line_items": 0.0, "order_subtotal": 0.0})
    log = {"orders_seen": len(orders), "orders_used": 0, "skipped_test": 0,
           "skipped_cancelled": 0, "skipped_country": collections.Counter(),
           "titles": collections.defaultdict(set), "earliest": None}

    for order in orders:
        if order.get("test"):
            log["skipped_test"] += 1
            continue
        if order.get("cancelledAt"):
            log["skipped_cancelled"] += 1
            continue
        order_country = (order.get("shippingAddress") or {}).get("countryCodeV2")
        if order_country != country:
            log["skipped_country"][order_country] += 1
            continue
        date = store.day(order["createdAt"])
        log["orders_used"] += 1
        if log["earliest"] is None or date < log["earliest"]:
            log["earliest"] = date
        store_total[date]["order_subtotal"] += float(
            order["currentSubtotalPriceSet"]["shopMoney"]["amount"])
        seen_here = set()
        for title, quantity, amount in _line_items(order):
            key = normalize(title)
            log["titles"][key].add(title)
            store_total[date]["line_items"] += amount
            entry = by_product[key].setdefault(date, ProductDay())
            entry.revenue += amount
            entry.units += quantity
            if key not in seen_here:      # one order counts once per product
                entry.orders += 1
                seen_here.add(key)
    return by_product, dict(store_total), log


def fetch_refunds(store, start, country):
    """-> ({key: {date: amount}}, log). Refund line items dated by the refund,
    plus finally lost chargebacks dated by finalizedOn."""
    begin = dt.datetime.combine(start, dt.time.min, store.tz)
    orders = store._page(REFUNDS_QUERY, "updated_at:>='%s'" % begin.isoformat())

    refunds = collections.defaultdict(lambda: collections.defaultdict(float))
    log = {"notes": [], "refund_rows": 0, "disputes_counted": 0}

    for order in orders:
        if order.get("test"):
            continue
        if (order.get("shippingAddress") or {}).get("countryCodeV2") != country:
            continue
        for refund in order.get("refunds") or []:
            date = store.day(refund["createdAt"])
            total = float(refund["totalRefundedSet"]["shopMoney"]["amount"])
            if total == 0.0:
                log["notes"].append(
                    "%s %s: refund of 0.00 (restock only) - not counted"
                    % (date, order.get("name")))
                continue
            for edge in refund["refundLineItems"]["edges"]:
                item = edge["node"]
                line = item["lineItem"]
                title = (line.get("product") or {}).get("title") or line["title"]
                amount = float(item["subtotalSet"]["shopMoney"]["amount"])
                if amount:
                    refunds[normalize(title)][date] += amount
                    log["refund_rows"] += 1

    # Chargebacks hang off the payments account, not off a refund object.
    try:
        disputes, cursor = [], None
        while True:
            account = store.graphql(DISPUTES_QUERY, {"cursor": cursor}).get(
                "shopifyPaymentsAccount")
            if not account:
                raise ShopifyError("no Shopify Payments account on this store")
            block = account["disputes"]
            disputes += [edge["node"] for edge in block["edges"]]
            if not block["pageInfo"]["hasNextPage"]:
                break
            cursor = block["pageInfo"]["endCursor"]
    except ShopifyError as error:
        log["notes"].append("disputes not readable: %s" % error)
        disputes = []

    for dispute in disputes:
        status = dispute.get("status")
        money = dispute.get("amount") or {}
        order = dispute.get("order") or {}
        label = "%s %s %s" % (order.get("name") or dispute.get("id"),
                              dispute.get("type"), status)
        if status in DISPUTE_OPEN:
            log["notes"].append("%s: still open (%s %s) - not counted"
                                % (label, money.get("amount"), money.get("currencyCode")))
            continue
        if status not in DISPUTE_LOST or not dispute.get("finalizedOn"):
            continue
        if (order.get("shippingAddress") or {}).get("countryCodeV2") != country:
            continue
        if money.get("currencyCode") != "EUR":
            log["notes"].append(
                "%s: %s %s - not EUR, NOT counted, check by hand"
                % (label, money.get("amount"), money.get("currencyCode")))
            continue
        date = store.day(dispute["finalizedOn"])
        amount = float(money["amount"])
        lines = list(_line_items(order)) if order.get("lineItems") else []
        total = sum(line[2] for line in lines)
        if not lines or total <= 0:
            log["notes"].append("%s on %s: %.2f EUR - order lines unreadable, "
                                "not assigned to a product" % (label, date, amount))
            continue
        # Split across exactly the products in that order, by their share of
        # the order. Arithmetic on known values, not an estimate.
        for title, _quantity, line_amount in lines:
            refunds[normalize(title)][date] += amount * (line_amount / total)
        log["disputes_counted"] += 1
    return refunds, log
