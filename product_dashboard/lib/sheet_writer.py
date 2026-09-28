# -*- coding: utf-8 -*-
"""Writes the Google Sheet: Overview tab plus one tab per product."""

import collections

from model import DAY_COLUMNS, OVERVIEW_COLUMNS, winner_rate

LIGHT_BLUE = {"red": 0.812, "green": 0.894, "blue": 0.969}
PALE_BLUE = {"red": 0.929, "green": 0.957, "blue": 0.988}
DARK_BLUE = {"red": 0.043, "green": 0.235, "blue": 0.431}
BORDER_BLUE = {"red": 0.498, "green": 0.659, "blue": 0.816}
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
RED = {"red": 0.788, "green": 0.180, "blue": 0.157}
GREEN = {"red": 0.086, "green": 0.522, "blue": 0.282}

ROW_HEIGHT = 32
HEADER_HEIGHT = 40
FIRST_COL_WIDTH = 210
COL_WIDTH = 132

_BORDER = {"style": "SOLID", "width": 1, "color": BORDER_BLUE}
_BORDERS = {side: _BORDER for side in ("top", "bottom", "left", "right")}


def _a1(col_index):
    letters = ""
    index = col_index + 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _cell(value):
    if value is None or value == "":
        return {"userEnteredValue": {}}
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, (int, float)):
        return {"userEnteredValue": {"numberValue": float(value)}}
    text = str(value)
    if text.startswith("="):
        return {"userEnteredValue": {"formulaValue": text}}
    return {"userEnteredValue": {"stringValue": text}}


def _row(values):
    return {"values": [_cell(v) for v in values]}


# --------------------------------------------------------------- tab plumbing
def existing_tabs(sheets_service, spreadsheet_id):
    meta = sheets_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties").execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"]
            for s in meta["sheets"]}


DEFAULT_TAB_NAMES = ("Sheet1", "Tabellenblatt1", "Blad1", "Feuille 1", "Hoja 1")


def ensure_spreadsheet(sheets_service, spreadsheet_id, title, timezone):
    """The sheet is authored in English, so it is pinned to the en_US locale.

    This is not cosmetic: formulas are sent in the spreadsheet's locale, and a
    German locale expects ';' as the argument separator, which turns every
    TOTAL formula into #ERROR!.
    """
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"updateSpreadsheetProperties": {
            "properties": {"title": title, "locale": "en_US", "timeZone": timezone},
            "fields": "title,locale,timeZone"}}]}).execute()


def ensure_tabs(sheets_service, spreadsheet_id, wanted, market_codes=()):
    """Creates missing tabs, drops tabs this script owns but no longer needs,
    and returns {title: sheetId}. Tabs added by hand are left alone."""
    tabs = existing_tabs(sheets_service, spreadsheet_id)
    requests = [{"addSheet": {"properties": {"title": t}}}
                for t in wanted if t not in tabs]
    prefixes = tuple("%s " % code for code in market_codes)
    stale = [title for title, sheet_id in tabs.items()
             if title not in wanted
             and (title in DEFAULT_TAB_NAMES or title.startswith(prefixes))]
    # A spreadsheet must keep at least one tab, so anything stale is removed
    # only after the new tabs exist.
    if requests:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
        tabs = existing_tabs(sheets_service, spreadsheet_id)
    if stale:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"deleteSheet": {"sheetId": tabs[t]}} for t in stale]}).execute()
        tabs = existing_tabs(sheets_service, spreadsheet_id)
    return tabs


def _layout_requests(sheet_id, n_columns, n_rows, header_rows=1):
    """Light blue header with dark blue bold text, a border around every cell,
    tall rows and wide columns."""
    grid = {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": n_rows,
            "startColumnIndex": 0, "endColumnIndex": n_columns}
    return [
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id,
                           "gridProperties": {"frozenRowCount": header_rows}},
            "fields": "gridProperties.frozenRowCount"}},
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": header_rows,
                      "startColumnIndex": 0, "endColumnIndex": n_columns},
            "cell": {"userEnteredFormat": {
                "backgroundColor": LIGHT_BLUE,
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "wrapStrategy": "WRAP",
                "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": DARK_BLUE}}},
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,"
                      "verticalAlignment,wrapStrategy,textFormat)"}},
        {"updateBorders": dict({"range": grid, "innerHorizontal": _BORDER,
                                "innerVertical": _BORDER}, **_BORDERS)},
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS",
                      "startIndex": 0, "endIndex": header_rows},
            "properties": {"pixelSize": HEADER_HEIGHT}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS",
                      "startIndex": header_rows, "endIndex": max(n_rows, header_rows + 1)},
            "properties": {"pixelSize": ROW_HEIGHT}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": FIRST_COL_WIDTH}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": 1, "endIndex": n_columns},
            "properties": {"pixelSize": COL_WIDTH}, "fields": "pixelSize"}},
    ]


def _number_format(sheet_id, column_index, pattern, start_row, end_row):
    return {"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": start_row, "endRowIndex": end_row,
                  "startColumnIndex": column_index, "endColumnIndex": column_index + 1},
        "cell": {"userEnteredFormat": {
            "numberFormat": {"type": "NUMBER", "pattern": pattern}}},
        "fields": "userEnteredFormat.numberFormat"}}


def _roas_rules(sheet_id, column_index, start_row, end_row):
    """Red below 1, green from 1 up. Daily ROAS only."""
    span = {"sheetId": sheet_id, "startRowIndex": start_row, "endRowIndex": end_row,
            "startColumnIndex": column_index, "endColumnIndex": column_index + 1}
    return [
        {"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [span],
            "booleanRule": {
                "condition": {"type": "NUMBER_LESS", "values": [{"userEnteredValue": "1"}]},
                "format": {"textFormat": {"bold": True, "foregroundColor": RED}}}}}},
        {"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [span],
            "booleanRule": {
                "condition": {"type": "NUMBER_GREATER_THAN_EQ",
                              "values": [{"userEnteredValue": "1"}]},
                "format": {"textFormat": {"bold": True, "foregroundColor": GREEN}}}}}},
    ]


def conditional_format_counts(sheets_service, spreadsheet_id):
    """How many conditional-format rules each tab already carries. Read once,
    so a re-run replaces its rules instead of stacking new ones on top."""
    meta = sheets_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties.sheetId,conditionalFormats)").execute()
    return {s["properties"]["sheetId"]: len(s.get("conditionalFormats") or [])
            for s in meta["sheets"]}


def _drop_conditional_formats(sheet_id, count):
    return [{"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": 0}}
            for _ in range(count)]


def flush(sheets_service, spreadsheet_id, requests, chunk=400):
    """Send the requests in batches, backing off on the write-per-minute quota.

    The API allows 60 write calls per minute per project, so one call per
    product tab would stall at around 60 products. Batching keeps a full
    rebuild to a handful of calls.
    """
    import time
    sent = 0
    for start in range(0, len(requests), chunk):
        batch = requests[start:start + chunk]
        for attempt in range(6):
            try:
                sheets_service.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id, body={"requests": batch}).execute()
                break
            except Exception as error:
                if "429" not in str(error) and "Quota exceeded" not in str(error):
                    raise
                if attempt == 5:
                    raise
                time.sleep(20 * (attempt + 1))
        sent += len(batch)
    return sent


# ------------------------------------------------------------------ product tab
CURRENCY = "#,##0.00 €"
RATIO = "0.00"
PERCENT = "0.00%"


def product_tab_requests(sheet_id, series, existing_rules=0):
    """Header row, then a TOTAL row that adds itself up, then one row per day."""
    n_rows = len(series.days) + 2
    n_cols = len(DAY_COLUMNS)
    first_day_row = 3                       # 1 header, 2 total, 3.. days
    last_day_row = first_day_row + len(series.days) - 1

    def total(column_index, kind="SUM"):
        if not series.days:
            return ""
        letter = _a1(column_index)
        return "=IF(COUNT(%s%d:%s%d)=0,\"\",%s(%s%d:%s%d))" % (
            letter, first_day_row, letter, last_day_row,
            kind, letter, first_day_row, letter, last_day_row)

    index = {name: i for i, name in enumerate(DAY_COLUMNS)}
    totals = ["TOTAL"]
    for name in DAY_COLUMNS[1:]:
        if name in ("Revenue", "Orders", "Units", "Refunds", "Ad Spend", "Meta Purchases"):
            totals.append(total(index[name]))
        elif name == "ROAS":
            totals.append("=IF(%s2=0,\"\",%s2/%s2)" % (
                _a1(index["Ad Spend"]), _a1(index["Revenue"]), _a1(index["Ad Spend"])))
        elif name == "CPA":
            totals.append("=IF(%s2=0,\"\",%s2/%s2)" % (
                _a1(index["Orders"]), _a1(index["Ad Spend"]), _a1(index["Orders"])))
        elif name == "Ad Account":
            totals.append("")
        else:
            totals.append(total(index[name], "AVERAGE"))

    rows = [_row(DAY_COLUMNS), _row(totals)]
    for day in series.days:
        rows.append(_row([day[name] for name in DAY_COLUMNS]))

    requests = [{"updateCells": {
        "rows": rows, "fields": "userEnteredValue",
        "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0}}}]
    requests += _drop_conditional_formats(sheet_id, existing_rules)
    requests += _layout_requests(sheet_id, n_cols, max(n_rows, 2), header_rows=1)
    requests.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                  "startColumnIndex": 0, "endColumnIndex": n_cols},
        "cell": {"userEnteredFormat": {
            "backgroundColor": PALE_BLUE,
            "verticalAlignment": "MIDDLE",
            "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": DARK_BLUE}}},
        "fields": "userEnteredFormat(backgroundColor,verticalAlignment,textFormat)"}})
    for name, pattern in (("Revenue", CURRENCY), ("Refunds", CURRENCY),
                          ("Ad Spend", CURRENCY), ("CPA", CURRENCY),
                          ("Meta CPA", CURRENCY), ("Meta CPM", CURRENCY),
                          ("Meta CPC", CURRENCY), ("ROAS", RATIO),
                          ("Meta ROAS", RATIO), ("Meta Frequency", RATIO),
                          ("Meta CTR", PERCENT)):
        requests.append(_number_format(sheet_id, index[name], pattern, 1, max(n_rows, 2)))
    if series.days:
        requests += _roas_rules(sheet_id, index["ROAS"], first_day_row - 1, last_day_row)
    return requests


# ----------------------------------------------------------------- overview tab
def build_overview_rows(markets):
    """markets: [(market_code, market_name, [ProductSeries])] -> (rows, spans)."""
    rows = []
    marks = {"headers": [], "boxes": [], "winner_rows": [], "loser_rows": []}

    for code, name, series in markets:
        stats = winner_rate(series)
        rows.append(["%s – %s" % (code, name)] + [""] * (len(OVERVIEW_COLUMNS) - 1))
        marks["headers"].append(len(rows) - 1)
        rows.append(["Tested", "Winners", "Losers", "Winner Rate", "", "", "", "", ""])
        marks["boxes"].append(len(rows) - 1)
        rows.append([stats["tested"], stats["winners"], stats["losers"],
                     stats["rate"] if stats["rate"] is not None else "",
                     "", "", "", "", ""])
        rows.append([""] * len(OVERVIEW_COLUMNS))

        for label, wanted in (("WINNERS", True), ("LOSERS", False)):
            group = [s for s in series if s.is_winner == wanted]
            rows.append([label] + [""] * (len(OVERVIEW_COLUMNS) - 1))
            marks["headers"].append(len(rows) - 1)
            rows.append(list(OVERVIEW_COLUMNS))
            (marks["winner_rows"] if wanted else marks["loser_rows"]).append(len(rows) - 1)
            for item in group:
                rows.append([
                    item.name, item.launch_date, item.days_live,
                    item.total_spend, item.total_revenue, item.total_orders,
                    item.roas, item.cpa, item.status,
                ])
            if not group:
                rows.append(["(none)"] + [""] * (len(OVERVIEW_COLUMNS) - 1))
            rows.append([""] * len(OVERVIEW_COLUMNS))
    return rows, marks


def overview_requests(sheet_id, markets, existing_rules=0):
    rows, marks = build_overview_rows(markets)
    n_cols = len(OVERVIEW_COLUMNS)
    n_rows = len(rows)

    requests = [{"updateCells": {
        "rows": [_row(r) for r in rows], "fields": "userEnteredValue",
        "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0}}}]
    requests += _drop_conditional_formats(sheet_id, existing_rules)
    requests += _layout_requests(sheet_id, n_cols, n_rows, header_rows=1)

    for row_index in marks["headers"] + marks["boxes"] + marks["winner_rows"] + marks["loser_rows"]:
        requests.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": row_index,
                      "endRowIndex": row_index + 1,
                      "startColumnIndex": 0, "endColumnIndex": n_cols},
            "cell": {"userEnteredFormat": {
                "backgroundColor": LIGHT_BLUE, "verticalAlignment": "MIDDLE",
                "textFormat": {"bold": True, "fontSize": 11,
                               "foregroundColor": DARK_BLUE}}},
            "fields": "userEnteredFormat(backgroundColor,verticalAlignment,textFormat)"}})
    index = {name: i for i, name in enumerate(OVERVIEW_COLUMNS)}
    for name, pattern in (("Total Spend", CURRENCY), ("Total Revenue", CURRENCY),
                          ("CPA", CURRENCY), ("ROAS", RATIO)):
        requests.append(_number_format(sheet_id, index[name], pattern, 1, n_rows))

    # The winner-rate box sits in the same columns as the tables below it, so
    # its formats have to be applied after the column-wide ones.
    for row_index in marks["boxes"]:
        requests.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": row_index + 1,
                      "endRowIndex": row_index + 2,
                      "startColumnIndex": 0, "endColumnIndex": 4},
            "cell": {"userEnteredFormat": {
                "backgroundColor": PALE_BLUE, "verticalAlignment": "MIDDLE",
                "horizontalAlignment": "CENTER",
                "numberFormat": {"type": "NUMBER", "pattern": "0"},
                "textFormat": {"bold": True, "fontSize": 13,
                               "foregroundColor": DARK_BLUE}}},
            "fields": "userEnteredFormat(backgroundColor,verticalAlignment,"
                      "horizontalAlignment,numberFormat,textFormat)"}})
        requests.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": row_index + 1,
                      "endRowIndex": row_index + 2,
                      "startColumnIndex": 3, "endColumnIndex": 4},
            "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
            "fields": "userEnteredFormat.numberFormat"}})
    return requests, n_rows
