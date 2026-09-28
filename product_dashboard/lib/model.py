# -*- coding: utf-8 -*-
"""Joins the four sources into the rows the dashboard shows."""

import collections
import datetime as dt

DAY_COLUMNS = [
    "Date", "Revenue", "Orders", "Units", "Refunds", "Ad Spend", "ROAS", "CPA",
    "Meta Purchases", "Meta CPA", "Meta CPM", "Meta CTR", "Meta CPC",
    "Meta ROAS", "Meta Frequency", "Ad Account",
]

OVERVIEW_COLUMNS = [
    "Product", "Launch Date", "Days Live", "Total Spend", "Total Revenue",
    "Total Orders", "ROAS", "CPA", "Status",
]


class ProductSeries(object):
    """One product of one market, with one row per day."""

    def __init__(self, product, winner, adspend_names, shopify_titles):
        self.key = product.key
        self.name = product.names[0]
        self.all_names = product.names
        self.market = product.market
        self.status = product.status
        self.winner = winner              # tab name, or "" for a loser
        self.adspend_names = sorted(adspend_names)
        self.shopify_titles = sorted(shopify_titles)
        self.days = []                    # list of dicts keyed by DAY_COLUMNS

    @property
    def is_winner(self):
        return bool(self.winner)

    @property
    def launch_date(self):
        return self.days[0]["Date"] if self.days else ""

    @property
    def days_live(self):
        return len(self.days)

    def _total(self, column):
        values = [d[column] for d in self.days if d[column] not in (None, "")]
        return sum(values) if values else None

    @property
    def total_spend(self):
        return self._total("Ad Spend")

    @property
    def total_revenue(self):
        return self._total("Revenue")

    @property
    def total_orders(self):
        return self._total("Orders")

    @property
    def roas(self):
        spend, revenue = self.total_spend, self.total_revenue
        if not spend or revenue is None:
            return None
        return revenue / spend

    @property
    def cpa(self):
        spend, orders = self.total_spend, self.total_orders
        if not spend or not orders:
            return None
        return spend / orders

    def tab_name(self):
        """Sheet tab names are capped at 100 chars and cannot hold [ ] * ? / \\ ."""
        clean = "".join(c for c in "%s %s" % (self.market, self.name)
                        if c not in "[]*?/\\:")
        return clean.strip()[:95]


def build_series(products, adspend, winners, sales, refunds, revenue_from, catalog):
    """-> [ProductSeries] for the tested products of one market.

    The series starts on the product's first day in DAILY ADSPEND and runs to
    its last. Revenue reads as 0.00 only where Shopify could actually have
    answered: inside the retention window AND for a product that exists in the
    Shopify catalogue. Outside either, the cell stays empty - an unmapped
    product must never look like a product that sold nothing.
    """
    series = []
    for key, product in products.items():
        if not product.tested:
            continue
        days = adspend.get(key, {})
        product_sales = sales.get(key, {})
        product_refunds = refunds.get(key, {})
        known_to_shopify = key in catalog
        item = ProductSeries(
            product, winners.get(key, ""),
            {d.raw_name for d in days.values()},
            catalog.get(key, set()),
        )
        for date in sorted(days):
            spend_day = days[date]
            sale = product_sales.get(date)
            in_window = known_to_shopify and (revenue_from is None or date >= revenue_from)
            revenue = sale.revenue if sale else (0.0 if in_window else None)
            orders = sale.orders if sale else (0 if in_window else None)
            units = sale.units if sale else (0 if in_window else None)
            refund = product_refunds.get(date)
            spend = spend_day.spend
            item.days.append({
                "Date": date,
                "Revenue": revenue,
                "Orders": orders,
                "Units": units,
                "Refunds": refund,
                "Ad Spend": spend,
                "ROAS": (revenue / spend) if (spend and revenue is not None) else None,
                "CPA": (spend / orders) if (spend and orders) else None,
                "Meta Purchases": spend_day.purchases,
                "Meta CPA": spend_day.cpa,
                "Meta CPM": spend_day.cpm,
                "Meta CTR": spend_day.ctr,
                "Meta CPC": spend_day.cpc,
                "Meta ROAS": spend_day.roas,
                "Meta Frequency": spend_day.frequency,
                "Ad Account": spend_day.ad_account,
            })
        series.append(item)
    series.sort(key=lambda s: (s.launch_date or "9999", s.name.lower()))
    return series


def winner_rate(series):
    tested = len(series)
    winners = sum(1 for s in series if s.is_winner)
    return {
        "tested": tested,
        "winners": winners,
        "losers": tested - winners,
        "rate": (winners / tested) if tested else None,
    }
