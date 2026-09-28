# -*- coding: utf-8 -*-
"""Google Sheets and Drive clients built from the service account."""

import base64
import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _credentials_info():
    """Same lookup order as pnl-KIZORA: raw JSON, then base64, then a file."""
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        return json.loads(raw)
    encoded = os.environ.get("GOOGLE_SA_KEY_B64")
    if encoded:
        return json.loads(base64.b64decode(encoded))
    path = os.environ.get("GOOGLE_SA_KEY_FILE") or os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS")
    if path and os.path.exists(path):
        return json.load(open(path))
    raise RuntimeError(
        "No service account credentials: set GOOGLE_SERVICE_ACCOUNT_JSON, "
        "GOOGLE_SA_KEY_B64 or GOOGLE_SA_KEY_FILE.")


def credentials():
    return service_account.Credentials.from_service_account_info(
        _credentials_info(), scopes=SCOPES)


def service_account_email():
    return _credentials_info()["client_email"]


def sheets():
    return build("sheets", "v4", credentials=credentials(), cache_discovery=False)


def drive():
    return build("drive", "v3", credentials=credentials(), cache_discovery=False)


def call(request, attempts=6):
    """Execute a Sheets request, backing off on the per-minute quota.

    The API allows 60 reads and 60 writes per minute per project, and a
    rebuild across a hundred product tabs brushes both.
    """
    import time
    for attempt in range(attempts):
        try:
            return request.execute()
        except Exception as error:
            text = str(error)
            if "429" not in text and "Quota exceeded" not in text:
                raise
            if attempt == attempts - 1:
                raise
            time.sleep(20 * (attempt + 1))


def read_values(sheets_service, spreadsheet_id, a1_range):
    return sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=a1_range,
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="FORMATTED_STRING",
    ).execute().get("values", [])
