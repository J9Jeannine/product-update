#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh the Noveliska Product Cockpit snapshot from the current run.

The cockpit carries its data as an embedded JSON snapshot, so it shows
whatever was baked in at publish time - 29 Sep. This rebuilds that snapshot
from the latest pipeline run and keeps everything the page owns:

  image            the product photo, carried over by product key
  status_default   an Active/Killed decision already baked in; a product that
                   has none is left without one so the page's own autoStatus
                   decides, which makes anything still running show as Active
  thresholds       per-product targets

Manual Active/Killed toggles live in the artifact's own database, not in the
snapshot, so they are untouched.
"""
import io, json, os, re, sys

sys.path.insert(0, "/home/user/product-update/product_dashboard/lib")
from model import METRICS, SUMMED

SAVED = ("/root/.claude/projects/-home-user-product-update/"
         "a6e873e3-7814-51b7-9b90-d95468dd928f/tool-results/"
         "artifact-1f76db88-1790788698-677a.html")
RUN = "/home/user/product-update/product_dashboard/data/dashboard_data.json"
OUT = ("/tmp/claude-0/-home-user-product-update/"
       "a6e873e3-7814-51b7-9b90-d95468dd928f/scratchpad/noveliska_cockpit.html")

html = io.open(SAVED, encoding="utf-8").read()
OPEN = '<script type="application/json" id="dashboard-data">'
start = html.index(OPEN) + len(OPEN)
end = html.index("</script>", start)
old = json.loads(html[start:end])
run = json.load(open(RUN))

print("old snapshot: %s | markets %s" % (old["generated_at"], list(old["markets"])))
print("new run:      %s" % run["generated_at"])


def totals_of(days):
    out = {}
    for metric in METRICS:
        values = [d[metric] for d in days if d.get(metric) is not None]
        if not values:
            out[metric] = None
        elif metric in SUMMED:
            out[metric] = sum(values)
        else:
            out[metric] = sum(values) / len(values)
    spend, revenue, orders = out.get("Ad Spend"), out.get("Revenue"), out.get("Orders")
    out["ROAS"] = (revenue / spend) if (spend and revenue is not None) else None
    out["CPA"] = (spend / orders) if (spend and orders) else None
    return out


newest = max((d["Date"] for m in run["markets"].values()
              for p in m["products"] for d in p["days"]), default="")


def still_running(days):
    """The page calls a product Active when its last day is within a day of
    the newest day in the data. A baked 'killed' from a run where the product
    had no data yet would freeze it out, so it is dropped for anything that is
    actually still running."""
    if not days:
        return False
    import datetime as dt
    last = dt.date.fromisoformat(days[-1]["Date"])
    return (dt.date.fromisoformat(newest) - last).days <= 1


carried, fresh, revived = 0, [], []
for code, market in run["markets"].items():
    previous = {p["key"]: p for p in old["markets"].get(code, {}).get("products", [])}
    products = []
    for p in market["products"]:
        before = previous.get(p["key"])
        item = {
            "name": p["name"], "key": p["key"], "winner": p["winner"],
            "performance_tab": p["performance_tab"], "status": p["status"],
            "launch_date": p["launch_date"], "days_live": p["days_live"],
            "total_spend": p["total_spend"], "total_revenue": p["total_revenue"],
            "total_orders": p["total_orders"], "roas": p["roas"], "cpa": p["cpa"],
            "adspend_names": p["adspend_names"], "shopify_titles": p["shopify_titles"],
            "days": p["days"],
            "totals": totals_of(p["days"]),
        }
        if before:
            carried += 1
            baked = before.get("status_default")
            if baked == "killed" and still_running(p["days"]):
                baked = None
                revived.append(p["name"])
            item["status_default"] = baked
            item["thresholds"] = before.get("thresholds")
            photo = p.get("image") or before.get("image")
            if photo:
                item["image"] = photo
        else:
            fresh.append(p["name"])
            # No baked decision: the page's autoStatus sees a product whose
            # last day is current and shows it as Active.
            item["status_default"] = None
            item["thresholds"] = None
            if p.get("image"):
                item["image"] = p["image"]
        products.append(item)

    old_market = old["markets"].get(code, {})
    new_market = {
        "name": market["name"],
        "stats": market["stats"],
        "products": products,
    }
    if "shopify_from" in old_market:
        new_market["shopify_from"] = run.get("shopify_from") or old_market["shopify_from"]
    old["markets"][code] = new_market

old["generated_at"] = run["generated_at"]
if "spreadsheet_url" in run:
    old["spreadsheet_url"] = run["spreadsheet_url"]

last = max((d["Date"] for m in old["markets"].values()
            for p in m["products"] for d in p["days"]), default="-")
print("products carried over: %d | new: %s" % (carried, ", ".join(fresh) or "none"))
print("baked 'killed' dropped because still running: %s" % (", ".join(revived) or "none"))
print("newest day in snapshot now: %s" % last)
missing = [q["name"] for m in old["markets"].values() for q in m["products"] if not q.get("image")]
print("products without a photo: %s" % (", ".join(missing) or "none"))
for code, m in old["markets"].items():
    print("  %s stats: %s" % (code, m["stats"]))

payload = json.dumps(old, separators=(",", ":")).replace("</", "<\\/")
io.open(OUT, "w", encoding="utf-8").write(html[:start] + payload + html[end:])
print("wrote %s (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024.0))
