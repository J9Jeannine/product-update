# product-update

The Noveliska product dashboard. See `product_dashboard/README.md` for how the
pipeline works.

## Standing rules from Jeannine

**Always take the photo.** Every product shown anywhere — the cockpit cards,
the roster, a product detail — carries its real product photo. A letter
avatar is not acceptable, not even for a product that launched today. When a
new product appears, its photo is fetched with it in the same run; it is never
left for later. The photo comes from the Shopify product's featured image
(`product_dashboard/lib/images.py`), is cached in
`product_dashboard/data/product_images.json`, and is embedded in the page so it
keeps working offline.

If a product genuinely has no image in Shopify, say so by name rather than
quietly falling back to a letter.

## The three rules the pipeline follows

1. **Nothing is estimated.** A value no source delivers stays empty. An empty
   cell and a zero mean different things.
2. **No name is guessed.** A name that does not normalize onto a known product
   goes into the unmatched list and out of the numbers.
3. **One broken source never blanks the others.**

## Layout

The product tabs in the Google Sheet have **one column per day** with the
metrics down column A. The Noveliska Product Cockpit reads that layout, so it
must not be transposed back.

The cockpit carries its data as an **embedded JSON snapshot**, not a live read
of the sheet. Filling the sheet alone does not update it — the snapshot has to
be regenerated and the artifact republished.
