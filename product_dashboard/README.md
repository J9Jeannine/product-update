# PRODUCT DASHBOARD - NOVELISKA

Every tested product, per market, per day: what it cost in ads, what it made on
Shopify, and whether it turned into a winner.

| | |
|---|---|
| Google Sheet | [PRODUCT DASHBOARD - NOVELISKA](https://docs.google.com/spreadsheets/d/1J4Q5dV0q2selwCMIVYHYgsGpH3M94b056siX1ZIwdSk/edit) |
| HTML dashboard | [Noveliska Product Cockpit](https://claude.ai/artifact/4tMJJgTEdvFWbWy3VDEufb) (also `dashboard.html` in this folder) |
| Markets live | FI (Finland). FRCA, SE, UK and NLBE are wired up but switched off. |
| Runs | daily 07:45 Europe/Lisbon, re-pulling the last 7 days |

## The three rules everything else follows

1. **Nothing is estimated.** A value no source delivers stays empty. An empty
   cell and a zero mean different things and never stand in for each other.
2. **No name is guessed.** A name that does not normalize onto a known product
   goes into the unmatched list and out of the numbers.
3. **One broken source never blanks the others.** A failing Shopify call leaves
   revenue empty and still writes ad spend.

## Sources

| # | Source | What is taken |
|---|---|---|
| 1 | **Funnel Sheet** `1SkD7jrC…`, tab per market | Column **G** `new PR NAME` is our product name (column C is the competitor and is never used). Column **R** is the status: `Launched` or `Re-launch` means tested; `Ready to launch` and empty are excluded. |
| 2 | **DAILY ADSPEND** `1AycOl-3z…`, tab `Raw Data` | Date, Product Name, Ad Account, Spending, Purchases, CPA, CPM, CTR, CPC, ROAS, FREQUENCY. |
| 3 | **Product Performance** `1vCbbwaD…` (.xlsx) | Tab names. A tested product with a tab is a winner. Every other tested product is a loser. Nothing else counts. |
| 4 | **Shopify Admin API**, store Noveliska `ska0c1-gz` | Orders per day, line items per product: revenue after discounts, orders, units, refunds. |

## How names are matched

The same product is spelled four ways. `lib/normalize.py` reduces all four to
one key:

```
"ViroFlow"            Funnel Sheet
"ViroFlow FI 2.0"     DAILY ADSPEND          ->  viroflow
"ViroFlow FI 2.0"     Product Performance tab
"ViroFlow(tm)"        Shopify product title
```

1. lowercase
2. strip `™ ® ©`, diacritics and invisible characters (Shopify's `Orthix` title
   carries a soft hyphen)
3. drop standalone market tokens: `FI`, `FRCA`, `FRC`, `SE`, `UK`, `NL`, `BE`, `NLBE`
4. drop standalone version and relaunch tokens: `2.0`, `3.0`, `2.`, `relaunch`,
   `Rel.`, `Copy of`, `SLS`, `new`
5. remove the remaining spaces, dashes and punctuation

A digit glued to the name is **kept**, so `Hylon` and `Hylon2` stay separate
products (the Funnel Sheet lists them separately), while `Hylon FI - 2.0`
reduces to `hylon`.

The **market** comes from the ad-account suffix first (`N6966 FI`), then from
the product name. Rows named `PPE*` or `BANNED` are dropped outright.

**Files**

| File | Content |
|---|---|
| `mapping/mapping_FI.csv` | one row per FI product: funnel name, ad-spend names, performance tab, Shopify title, winner/loser, first ad-spend day |
| `mapping/unmatched_FI.csv` | everything that could not be assigned without guessing |
| `mapping/overrides.csv` | hand-reviewed exceptions. Written by a person, never generated. |

`overrides.csv` currently holds one line: `IntimaFix FI` in DAILY ADSPEND is a
typo for `IntimaFit` (Funnel Sheet FI row 56, launched 09.08.2026 — the same
day as the ad-spend row).

## What each day's row contains

| Column | Where it comes from |
|---|---|
| Revenue | Shopify line items after discounts, order day in the shop's timezone (Europe/Helsinki) |
| Orders | Shopify orders containing that product, counted once per order |
| Units | Shopify line-item quantity |
| Refunds | Refund line items dated by `refund.createdAt`, plus finally lost chargebacks |
| Ad Spend | DAILY ADSPEND `Spending` |
| ROAS | Shopify revenue / ad spend — the real one |
| CPA | ad spend / Shopify orders — the real one |
| Meta Purchases, Meta CPA, CPM, CTR, CPC, ROAS, Frequency | straight from DAILY ADSPEND, kept separate from the real figures |

A product's series starts on its **first day in DAILY ADSPEND** and ends on its
last. Several ad accounts on the same day are summed for spend and purchases;
the ratios cannot be added and averaging them would be an estimate, so they go
empty for that day.

Duplicate ad-spend rows — identical in Date + Product Name + Ad Account +
Spending — are counted once and listed in the run report.

### Refunds

Same rule as `pnl-KIZORA`:

1. **Refunds we issued** — refund objects dated by `refund.createdAt`, not by
   the order date. The orders are fetched via `updated_at`, so a late refund on
   an old order is still caught. A refund of 0.00 (a restock with no money
   moving) is skipped and reported.
2. **Finally lost chargebacks** — disputes with status `LOST` or `ACCEPTED`,
   dated by `finalizedOn`, split across exactly the products in that order by
   their share of it.

Not counted: `WON` and `PREVENTED` (won) and `NEEDS_RESPONSE` /
`UNDER_REVIEW` (still open). A chargeback in a currency other than EUR is not
converted and not counted — it is reported for manual entry.

## The two limits you will notice

**Shopify keeps 60 days.** The app has `read_orders` but not `read_all_orders`,
so Shopify only answers for roughly the last 60 days. Products launched before
that — `liftala`, `Mobilixa`, `circula`, `ozempa`, `Optila` among the winners —
have ad spend but no revenue, and the cells stay empty rather than reading as
zero. Adding `read_all_orders` to the app fills them in on the next full run.

**Four products have no Shopify product of their own.** `Hylon2`, `liftala2`,
`resigo2` and `cortisa2` are separate products in the Funnel Sheet, but Shopify
has only one `Hylon`, `Liftala`, `Resigo` and `Cortisa`. Their revenue, orders
and units stay empty. To count them, either create the Shopify products or add
a line to `mapping/overrides.csv`.

## The cross-check

Every run compares, for the last two days it covers:

* **dashboard** — the sum of every product row written to the sheet
* **all products** — every FI line item that day, same revenue definition
* **order subtotal** — the order-level total, which is after later refunds and
  edits

`dashboard` minus `all products` is revenue from products the dashboard does
not show that day. `all products` minus `order subtotal` is what was refunded
or edited afterwards. On 28.09.2026 the first difference was 0.00 on both days.

A comparison against the Shopify **Analytics** reports themselves needs the
`read_reports` scope plus Level 2 protected-customer-data access, which this
app does not have; `shopifyqlQuery` returns `ACCESS_DENIED`. The order-level
figures above are the same underlying numbers.

## Running it

```bash
./run_daily.sh                 # the nightly run: last 7 days
./run_daily.sh --full          # rebuild every day since each launch
python3 build_dashboard.py --dry-run     # read everything, write nothing
python3 build_dashboard.py --days 14
python3 export_html.py                   # rebuild dashboard.html from the sheet
```

Environment:

| Variable | Use |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` or `GOOGLE_SA_KEY_B64` | Sheets and Drive. The service account needs write access to the output sheet and read access to the three sources. |
| `SHOPIFY_NOVELISKA_DOMAIN` | `ska0c1-gz.myshopify.com` |
| `SHOPIFY_NOVELISKA_CLIENT_ID` / `_CLIENT_SECRET` | exchanged for a 24h Admin API token via the client-credentials grant, exactly as `pnl-KIZORA/scripts/shopify_client.py` does it. Nothing is stored. |

Google Cloud project `pnl-kizora` needs both the **Sheets API** and the
**Drive API** enabled. Sheets allows 60 reads and 60 writes per minute per
project; the scripts batch their calls and back off, but a full rebuild across
a hundred product tabs still takes a minute or two.

## The Google Sheet

* **Overview** — one block per market. Winner-rate box (Tested, Winners,
  Losers, Winner Rate), then a WINNERS table and a LOSERS table with name,
  launch date, days live, total spend, total revenue, total orders, ROAS, CPA.
* **One tab per product** — `FI Nervora`, `FI ViroFlow`, … header row, then a
  TOTAL row directly beneath it that recalculates itself, then one row per day.
* Light blue header, dark blue bold text, a border on every cell, tall rows,
  wide columns. Daily ROAS is red below 1 and green from 1 up.

The spreadsheet is pinned to the **en_US locale**. This is not cosmetic:
formulas are sent in the spreadsheet's locale, and a German locale expects `;`
as the argument separator, which turns every TOTAL formula into `#ERROR!`.

Tabs the scripts own (`Overview` and `<MARKET> <product>`) are rewritten each
run and removed when a product disappears. Any tab you add by hand is left
alone.

## The HTML dashboard

`dashboard_template.html` is the page; `export_html.py` reads the Google Sheet
and writes `dashboard.html` with the data embedded. The sheet is the source, so
the page can never disagree with it.

Market filter, winner-rate tiles, a Winners / Losers / All table, a product
picker with search, and a per-day table with a revenue-versus-ad-spend chart.
Daily ROAS is green from 1 up and red below.

## Adding a market

1. Set `enabled: true` for that market in `config/dashboard.config.json` and
   check its `funnel_tab` matches the tab name in the Funnel Sheet.
2. Confirm `shopify_country` is the shipping country code the market ships to.
3. Run `python3 build_dashboard.py --dry-run --full` and read the unmatched
   list before writing anything.

Ad accounts are numbered per market. `lib/sources.py` carries the mapping used
for the Product Performance tabs that have no Country column (the NLBE ones);
a new account number goes in there.

## Schedule

A Routine fires daily at **07:45 Europe/Lisbon**, re-pulls the last 7 days and
overwrites them, so late orders and late ad spend land. New products in the
Funnel Sheet are picked up automatically.

Every run ends with a report: rows written, the unmatched list, dropped
duplicates, refund notes and the cross-check difference. It is also saved to
`data/last_run.json`.
