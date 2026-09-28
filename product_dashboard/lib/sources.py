# -*- coding: utf-8 -*-
"""Readers for the three Google-side sources: funnel, ad spend, winners."""

import collections
import datetime as dt
import io
import os
import tempfile

import openpyxl
from googleapiclient.http import MediaIoBaseDownload

from normalize import normalize, detect_market


def _cell(row, index):
    return str(row[index]).strip() if index < len(row) else ""


# --------------------------------------------------------------- funnel sheet
class Product(object):
    """One product of one market, as the Funnel Sheet defines it."""

    def __init__(self, key, name, market):
        self.key = key
        self.name = name
        self.market = market
        self.names = [name]
        self.statuses = []

    @property
    def tested(self):
        return any(s in self._tested for s in (x.strip().lower() for x in self.statuses))

    @property
    def status(self):
        low = [s.strip().lower() for s in self.statuses]
        if any(s in ("re-launch", "relaunch") for s in low):
            return "Re-launch"
        if self.tested:
            return "Launched"
        return next((s for s in self.statuses if s), "") or "(empty)"

    _tested = ("launched", "re-launch", "relaunch")


def read_funnel(sheets_service, config, market):
    """-> {key: Product} for one market tab. Products are keyed by normalized
    name, so the same product listed twice (relaunch row) collapses into one."""
    from gclients import read_values

    spec = config["sources"]["funnel"]
    tab = config["markets"][market]["funnel_tab"]
    rows = read_values(sheets_service, spec["spreadsheet_id"],
                       "'%s'!A1:Z1000" % tab)
    products = collections.OrderedDict()
    for row in rows[spec["header_rows"]:]:
        name = _cell(row, spec["product_name_column"])
        status = _cell(row, spec["status_column"])
        if not name:
            continue
        key = normalize(name)
        if not key:
            continue
        product = products.get(key)
        if product is None:
            product = products[key] = Product(key, name, market)
        elif name not in product.names:
            product.names.append(name)
        product.statuses.append(status)
    return products


# ----------------------------------------------------------------- ad spend
def _number(value):
    """Ad-spend cells use '-', '-%' and '' for 'no value'. All mean empty."""
    text = str(value).strip().replace("€", "").replace("%", "").replace(",", ".")
    if text in ("", "-", "–"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


class AdSpendDay(object):
    __slots__ = ("date", "raw_name", "ad_account", "spend", "purchases",
                 "cpa", "cpm", "ctr", "cpc", "roas", "frequency")

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))


def read_adspend(sheets_service, config, market):
    """-> ({key: {date: AdSpendDay}}, log) for one market.

    Rows identical in Date + Product Name + Ad Account + Spending are counted
    once. Several ad accounts on the same day for the same product are summed.
    """
    from gclients import read_values

    spec = config["sources"]["adspend"]
    col = spec["columns"]
    rows = read_values(sheets_service, spec["spreadsheet_id"],
                       "'%s'!A1:Z50000" % spec["tab"])

    by_product = collections.defaultdict(dict)
    log = {"rows_kept": 0, "excluded": 0, "duplicates": [], "raw_names": collections.defaultdict(set),
           "other_markets": collections.defaultdict(set), "unknown_market": collections.defaultdict(set)}
    seen = set()

    for row in rows[1:]:
        date = _cell(row, col["date"])
        name = _cell(row, col["product"])
        if not date or not name:
            continue
        lowered = name.lower()
        if any(lowered.startswith(p) for p in spec["exclude_name_prefixes"]):
            log["excluded"] += 1
            continue
        account = _cell(row, col["ad_account"])
        spend_raw = _cell(row, col["spend"])
        fingerprint = (date, name, account, spend_raw)
        if fingerprint in seen:
            log["duplicates"].append(fingerprint)
            continue
        seen.add(fingerprint)
        log["rows_kept"] += 1

        row_market = detect_market(name, account)
        key = normalize(name)
        if not row_market:
            log["unknown_market"][key].add(name)
            continue
        if row_market != market:
            log["other_markets"][key].add(name)
            continue

        log["raw_names"][key].add(name)
        day = by_product[key].get(date)
        spend = _number(spend_raw)
        if day is None:
            by_product[key][date] = AdSpendDay(
                date=date, raw_name=name, ad_account=account, spend=spend,
                purchases=_number(_cell(row, col["purchases"])),
                cpa=_number(_cell(row, col["cpa"])),
                cpm=_number(_cell(row, col["cpm"])),
                ctr=_number(_cell(row, col["ctr"])),
                cpc=_number(_cell(row, col["cpc"])),
                roas=_number(_cell(row, col["roas"])),
                frequency=_number(_cell(row, col["frequency"])))
        else:
            # Same product, same day, a second ad account: spend and purchases
            # add up. The ratios (CPM, CTR, CPC, ROAS, frequency) cannot be
            # added, and averaging them would be an estimate - so they go empty.
            day.spend = (day.spend or 0) + (spend or 0) if (day.spend is not None or spend is not None) else None
            purchases = _number(_cell(row, col["purchases"]))
            if day.purchases is not None or purchases is not None:
                day.purchases = (day.purchases or 0) + (purchases or 0)
            day.ad_account = "%s + %s" % (day.ad_account, account)
            for ratio in ("cpa", "cpm", "ctr", "cpc", "roas", "frequency"):
                setattr(day, ratio, None)
    return by_product, log


# ------------------------------------------------------------- winner tabs
def read_performance_tabs(drive_service, config):
    """-> [(tab_name, market)] - every tab of Product Performance.xlsx.

    The market comes from the tab's own Country column, and falls back to the
    ad-account suffix in its rows when that column is missing (the NLBE tabs).
    """
    spec = config["sources"]["performance"]
    handle, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(handle)
    try:
        with io.FileIO(path, "wb") as sink:
            downloader = MediaIoBaseDownload(
                sink, drive_service.files().get_media(fileId=spec["file_id"]),
                chunksize=1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        out = []
        for name in book.sheetnames:
            out.append((name, _tab_market(book[name])))
        book.close()
        return out
    finally:
        os.unlink(path)


# Ad accounts are numbered per market; the NLBE tabs carry no Country column.
_ACCOUNT_MARKET = {
    "N0047": "NLBE", "N0987": "NLBE", "N0988": "NLBE", "N0138": "NLBE", "N1111": "NLBE",
    "N4925": "FRCA", "N4926": "FRCA", "N6965": "FRCA",
    "N4839": "UK", "N4840": "UK",
    "N1580": "SE", "N1645": "SE",
}


def _tab_market(worksheet):
    from normalize import MARKET_TOKENS
    account = ""
    for row in worksheet.iter_rows(min_row=1, max_row=8, values_only=True):
        for cell in row:
            if cell in (None, ""):
                continue
            text = str(cell).strip()
            upper = text.upper()
            if upper in MARKET_TOKENS:
                return MARKET_TOKENS[upper]
            if not account and len(text) >= 5 and text[0].upper() == "N" and text[1:5].isdigit():
                account = text[:5].upper()
    return _ACCOUNT_MARKET.get(account, "")


def winners_for_market(tabs, market):
    """-> {normalized key: tab name} for one market."""
    return {normalize(name): name for name, tab_market in tabs if tab_market == market}
