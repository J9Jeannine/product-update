# -*- coding: utf-8 -*-
"""Name normalization shared by every source.

One product appears under four different spellings:

    Funnel Sheet         "ViroFlow"
    DAILY ADSPEND        "ViroFlow FI 2.0"
    Product Performance  "ViroFlow FI 2.0"   (tab name)
    Shopify              "ViroFlow(tm)"

`normalize()` reduces all four to the same key, `viroflow`, so they can be
joined without guessing. Anything that does not reduce to a known key is
reported as unmatched — it is never assigned by similarity.
"""

import re
import unicodedata

# Market tokens. Removed from the base name, and used to detect the market.
MARKET_TOKENS = {
    "FI": "FI",
    "FRCA": "FRCA", "FRC": "FRCA", "FR": "FRCA", "CA": "FRCA",
    "SE": "SE",
    "UK": "UK",
    "NLBE": "NLBE", "NL": "NLBE", "BE": "NLBE",
}

# Standalone tokens that mark a new iteration of the SAME product, not a new
# product: "ViroFlow FI 2.0", "Liftala FI - relaunch", "Copy of Lymvara".
VERSION_TOKENS = {
    "relaunch", "relaunched", "rel", "copy", "of", "new", "sls", "v2", "v3",
}

_VERSION_NUMBER = re.compile(r"^\d+(\.\d+)?$")


def strip_marks(text):
    """Drop (tm)/(R)/(C), soft hyphens and other invisible format characters,
    and fold diacritics. Shopify titles contain a soft hyphen in "Orthix(tm)"."""
    text = text.replace("™", " ").replace("®", " ").replace("©", " ")
    text = unicodedata.normalize("NFKD", text)
    return "".join(
        c for c in text
        if not unicodedata.combining(c) and unicodedata.category(c) != "Cf"
    )


def tokens(text):
    text = strip_marks(text or "")
    text = text.replace("–", " - ").replace("—", " - ")
    text = re.sub(r"[\-_/,()\[\]]+", " ", text)
    return [t for t in text.split() if t]


def normalize(name):
    """-> base key: lowercase, no marks, no market suffix, no version token.

    A digit glued to the name is kept, so `Hylon` and `Hylon2` stay apart,
    while `Hylon FI - 2.0` reduces to `hylon`.
    """
    out = []
    for token in tokens(name):
        upper = token.upper().strip(".")
        lower = token.lower().strip(".")
        if upper in MARKET_TOKENS:
            continue
        if lower in VERSION_TOKENS:
            continue
        if _VERSION_NUMBER.match(lower):
            continue
        out.append(lower)
    return re.sub(r"[^a-z0-9]", "", "".join(out))


def detect_market(name, ad_account=""):
    """Market of an ad-spend row. The ad account wins: its suffix ("N6966 FI")
    is maintained per market, while the product name sometimes omits it."""
    for source in (ad_account, name):
        for token in tokens(source):
            upper = token.upper().strip(".")
            if upper in MARKET_TOKENS:
                return MARKET_TOKENS[upper]
    return ""
