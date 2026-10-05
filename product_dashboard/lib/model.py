# -*- coding: utf-8 -*-
"""Joins the sources into the rows the dashboard shows.

The product tabs are laid out with one COLUMN per day and the metrics down
column A, which is how the sheet and the cockpit that reads it are built.
METRICS is that row order, top to bottom, and must not be reordered without
rebuilding every tab.
"""

import collections

# row order of a product tab, column A top to bottom
METRICS = [
    "Revenue", "Orders", "Units", "Refunds", "Ad Spend", "ROAS", "CPA",
    "Profit", "Shipping Income", "COGS", "SP Fee", "Transaction Fee", "FB Fee",
    "Meta Purchases", "Meta CPA", "Meta CPM", "Meta CTR", "Meta CPC",
    "Meta ROAS", "Meta Frequency",
]

# metrics whose TOTAL is a sum; the rest are averaged, and ROAS and CPA are
# recomputed from the totals instead
SUMMED = {"Revenue", "Orders", "Units", "Refunds", "Ad Spend", "Profit",
          "Shipping Income", "COGS", "SP Fee", "Transaction Fee", "FB Fee",
          "Meta Purchases"}

OVERVIEW_COLUMNS = [
    "Product", "Launch Date", "Days Live", "Total Spend", "Total Revenue",
    "Total Orders", "ROAS", "CPA", "Status",
]

# The fee rates the sheet already uses, checked against Nervora on 27.08.2026:
# SP Fee 32.17, Transaction Fee 19.98, FB Fee 6.62 on revenue 439.65,
# shipping 19.96 and ad spend 132.37.
SP_FEE_RATE = 0.07           # of revenue + shipping
TRANSACTION_DIVISOR = 23.0   # of revenue + shipping
FB_FEE_RATE = 0.05           # of ad spend


class ProductSeries(object):
    """One product of one market, with one entry per day."""

    def __init__(self, product, winner, adspend_names, shopify_titles):
        self.key = product.key
        self.name = product.names[0]
        self.all_names = product.names
        self.market = product.market
        self.status = product.status
        self.winner = winner              # tab name, or "" for a loser
        self.adspend_names = sorted(adspend_names)
        self.shopify_titles = sorted(shopify_titles)
        self.days = []                    # dicts keyed by "Date" + METRICS

    @property
    def is_winner(self):
        return bool(self.winner)

    @property
    def launch_date(self):
        return self.days[0]["Date"] if self.days else ""

    @property
    def days_live(self):
        return len(self.days)

    def _total(self, metric):
        values = [d[metric] for d in self.days if d[metric] not in (None, "")]
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
        """Tab names cap at 100 characters and cannot hold [ ] * ? / \\ : ."""
        clean = "".join(c for c in "%s %s" % (self.market, self.name)
                        if c not in "[]*?/\\:")
        return clean.strip()[:95]


def build_series(products, adspend, winners, sales, refunds, revenue_from,
                 catalog, cogs):
    """-> [ProductSeries] for the tested products of one market.

    A day exists for every day the product appears in DAILY ADSPEND. Revenue
    reads as 0.00 only where Shopify could have answered: inside the retention
    window AND for a product that exists in the Shopify catalogue. Outside
    either it stays empty, so an unmapped product never looks like one that
    sold nothing. Profit is only computed where every part of it is known.
    """
    series = []
    for key, product in products.items():
        if not product.tested:
            continue
        days = adspend.get(key, {})
        product_sales = sales.get(key, {})
        product_refunds = refunds.get(key, {})
        product_cogs = cogs.get(key, {})
        known_to_shopify = key in catalog
        item = ProductSeries(product, winners.get(key, ""),
                             {d.raw_name for d in days.values()},
                             catalog.get(key, set()))
        for date in sorted(days):
            spend_day = days[date]
            sale = product_sales.get(date)
            in_window = known_to_shopify and (revenue_from is None or date >= revenue_from)
            revenue = sale.revenue if sale else (0.0 if in_window else None)
            orders = sale.orders if sale else (0 if in_window else None)
            units = sale.units if sale else (0 if in_window else None)
            shipping = sale.shipping if sale else (0.0 if in_window else None)
            refund = product_refunds.get(date)
            spend = spend_day.spend
            cost = product_cogs.get(date)
            if cost is None and in_window and not revenue:
                cost = 0.0        # nothing sold, so nothing was bought in

            gross = None
            if revenue is not None:
                gross = revenue + (shipping or 0.0)
            sp_fee = gross * SP_FEE_RATE if gross is not None else None
            transaction = gross / TRANSACTION_DIVISOR if gross is not None else None
            fb_fee = spend * FB_FEE_RATE if spend is not None else None

            profit = None
            if None not in (gross, cost, sp_fee, transaction, fb_fee) and spend is not None:
                profit = (gross - spend - cost - sp_fee - transaction - fb_fee
                          - (refund or 0.0))

            item.days.append({
                "Date": date,
                "Revenue": revenue,
                "Orders": orders,
                "Units": units,
                "Refunds": refund,
                "Ad Spend": spend,
                "ROAS": (revenue / spend) if (spend and revenue is not None) else None,
                "CPA": (spend / orders) if (spend and orders) else None,
                "Profit": profit,
                "Shipping Income": shipping,
                "COGS": cost,
                "SP Fee": sp_fee,
                "Transaction Fee": transaction,
                "FB Fee": fb_fee,
                "Meta Purchases": spend_day.purchases,
                "Meta CPA": spend_day.cpa,
                "Meta CPM": spend_day.cpm,
                "Meta CTR": spend_day.ctr,
                "Meta CPC": spend_day.cpc,
                "Meta ROAS": spend_day.roas,
                "Meta Frequency": spend_day.frequency,
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
