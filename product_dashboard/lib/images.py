# -*- coding: utf-8 -*-
"""Product photos, so every product on the dashboard shows as itself.

Jeannine's rule: always take the photo. A letter avatar is not acceptable, and
a product that launched today gets its photo in the same run that adds it.

The photo is the Shopify product's featured image, requested already scaled
down by Shopify so nothing has to be resized here, then embedded as a data URI
so the page keeps working without network access. Results are cached in
data/product_images.json and only missing products are fetched.
"""

import base64
import json
import os
import subprocess

from normalize import normalize

# Shopify scales the image for us; 160px covers the 56px card avatar on a
# high-density screen with room to spare.
PRODUCTS_WITH_IMAGE = """
query($cursor: String) {
  products(first: 250, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      title
      featuredMedia { preview { image { url(transform: {maxWidth: 160, maxHeight: 160}) } } }
    } }
  }
}
"""

CACHE_NAME = "product_images.json"


def _download(url):
    """-> data URI, or None. A missing photo is reported, never faked."""
    proc = subprocess.run(
        ["curl", "-sS", "--max-time", "60", "-L", "-w", "\n%{http_code}\n%{content_type}",
         "--output", "-", url],
        capture_output=True)
    if proc.returncode != 0:
        return None
    raw = proc.stdout
    try:
        body, status, content_type = raw.rsplit(b"\n", 2)
    except ValueError:
        return None
    if status.strip() != b"200" or not body:
        return None
    mime = content_type.decode("ascii", "ignore").strip() or "image/jpeg"
    if not mime.startswith("image/"):
        return None
    return "data:%s;base64,%s" % (mime, base64.b64encode(body).decode("ascii"))


def fetch(store, data_dir, wanted=None, log=None):
    """-> ({normalized key: data URI}, notes).

    `wanted` limits the work to the product keys the dashboard actually shows.
    Anything already cached is reused, so a normal run downloads only the
    products that appeared since the last one.
    """
    notes = []
    path = os.path.join(data_dir, CACHE_NAME)
    cache = {}
    if os.path.exists(path):
        try:
            cache = json.load(open(path))
        except ValueError:
            notes.append("%s was unreadable and has been rebuilt" % CACHE_NAME)

    urls = {}
    cursor = None
    while True:
        block = store.graphql(PRODUCTS_WITH_IMAGE, {"cursor": cursor})["products"]
        for edge in block["edges"]:
            node = edge["node"]
            image = (((node.get("featuredMedia") or {}).get("preview") or {})
                     .get("image") or {})
            url = image.get("url")
            if url:
                urls.setdefault(normalize(node["title"]), url)
        if not block["pageInfo"]["hasNextPage"]:
            break
        cursor = block["pageInfo"]["endCursor"]

    keys = set(wanted) if wanted else set(urls)
    missing = [k for k in sorted(keys) if k in urls and not cache.get(k)]
    fetched, failed = 0, []
    for key in missing:
        data = _download(urls[key])
        if data:
            cache[key] = data
            fetched += 1
        else:
            failed.append(key)

    without = sorted(k for k in keys if not cache.get(k))
    if fetched:
        notes.append("photos downloaded: %d" % fetched)
    if failed:
        notes.append("photo download failed: %s" % ", ".join(failed))
    if without:
        notes.append("no Shopify photo for: %s" % ", ".join(without))

    os.makedirs(data_dir, exist_ok=True)
    with open(path, "w") as handle:
        json.dump(cache, handle, indent=1, sort_keys=True)
    if log is not None:
        log.extend(notes)
    return cache, notes
