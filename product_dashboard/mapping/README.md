# Product mapping — FI (Noveliska)

Draft mapping between the four data sources, generated before any dashboard was built.
**Not yet approved** — four open decisions are listed at the bottom.

## Files

| File | Content |
|---|---|
| `mapping_FI.csv` | One row per distinct FI product from the Funnel Sheet, with its DAILY ADSPEND name(s), Product Performance tab, winner/loser verdict and first/last ad-spend day. The `shopify_title` column is still empty (see blockers). |
| `unmatched_FI.csv` | Every value that could not be assigned to a product without guessing. |

## Sources

| # | Source | Location | Field used |
|---|---|---|---|
| 1 | Funnel Sheet | `1SkD7jrC-othtbwTYyz1VCK1HPIkEKHxc_hSAvRp2iL8`, tab `FI` | col **G** `new PR NAME`, col **R** status |
| 2 | DAILY ADSPEND | `1AycOl-3zM32yl09ogrEeVSx72FvadnYLV9ZcRH9Y49g`, tab `Raw Data` | Date, Product Name, Ad Account, Spending, Purchases, CPA, CPM, CTR, CPC, ROAS, FREQUENCY |
| 3 | Product Performance | `1vCbbwaDqlgI_5w2p5dyJtCJFHO9AuYHi` (.xlsx) | tab names = winners |
| 4 | Shopify Noveliska | `ska0c1-gz.myshopify.com` | not yet readable — see blockers |

A product counts as **tested** when its Funnel status is `Launched` or `Re-launch`.
`Ready to launch` and empty are excluded.

## Normalization rule

1. lowercase
2. strip `™ ® ©` and diacritics
3. drop standalone market tokens: `FI`, `FRCA`, `FRC`, `SE`, `UK`, `NL`, `BE`, `NLBE`
4. drop standalone version / relaunch tokens: `2.0`, `3.0`, `2.`, `relaunch`, `re-launch`, `Rel.`, `Copy`, `of`, `SLS`, `new`
5. remove all remaining spaces, dashes and punctuation

A digit glued to the name (`Hylon2`, `resigo2`) is **kept**, so `Hylon` and `Hylon2`
stay separate products, while `Hylon FI - 2.0` normalizes to `hylon`.

The market is taken from the **Ad Account** suffix first (`N6966 FI`), then from the
product name. Rows named `PPE*` or `BANNED` are dropped entirely.

## Result (FI, 2026-09-28)

| | |
|---|---|
| Distinct FI products in Funnel Sheet | 101 |
| Tested (Launched / Re-launch) | 97 |
| Winners (has a Product Performance tab) | 11 |
| Losers | 86 |
| Winner rate | 11.3 % |
| Tested but no FI row in DAILY ADSPEND | 8 |
| DAILY ADSPEND rows kept | 444 |
| PPE / BANNED rows excluded | 786 |
| Exact duplicate rows dropped | 6 |

The 11 FI winners: Arterio, circula, Flexura, Gastrox, liftala, Mobilixa, Nervora,
Optila, ozempa, pawox, ViroFlow.

Duplicates dropped (identical Date + Product Name + Ad Account + Spending):
`IntimaFit FRCA 2026-08-09`, `Fibaxo FI 2.0 2026-08-09`, `Orthix FRCA 2026-08-08`,
`Fibaxo FI 2026-08-08`, `Smokola FRCA 2026-08-07`, `Atriso FI 2026-08-07`.

## Open decisions

1. Keep `Hylon` / `Hylon2`, `resigo` / `resigo2`, `liftala` / `liftala2`,
   `cortisa` / `cortisa2`, `dermaflex` / `dermaflex2` as separate products?
2. `IntimaFix FI` in DAILY ADSPEND — map to `IntimaFit`, or leave unmatched?
3. `Pawox SLS FI` — currently folded into `pawox`. Separate test instead?
4. `Circula` is a winner purely because it has a tab, although it ran only two days.
   Keep the pure tab rule?

## Blockers

1. **Shopify** — the connector needs re-authorization, and the environment holds only
   `SHOPIFY_NOVELISKA_CLIENT_ID` / `SHOPIFY_NOVELISKA_CLIENT_SECRET`, no Admin API
   access token. A token (`read_orders`, `read_products`, `read_all_orders`) is needed
   as an environment secret before revenue can be pulled.
2. **Google Drive API is disabled** for the service account project `pnl-kizora`
   (project 414095030205). The Sheets API works, the Drive API does not, so the service
   account cannot read `Product Performance.xlsx` or create and share the output sheet.
3. The Product Performance tab list (29 tabs) was read through the Drive connector's
   text representation and may be truncated — to be confirmed once the Drive API is on.
