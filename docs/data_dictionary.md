# Data Dictionary — `data/data.csv`

> Unified dataset · 4,422 rows · 11 sources · February 2026

---

## Column Reference

| Column | Type | Example | Description |
|---|---|---|---|
| `source` | string | `birmarket` | Retailer identifier (slug, lowercase, no `.az`). See [sources.md](sources.md) for full list. |
| `product_id` | string | `1066412` | Retailer-assigned product identifier. Unique within a source, not across sources. |
| `name` | string | `Samsung Galaxy A55 8/256GB Black` | Full product listing title as displayed on the retailer's site. May include storage/RAM specs and colour. |
| `brand` | string | `Samsung` | Normalised brand name. Derived from the retailer's own brand field where available; otherwise extracted from `name` via regex. See [methodology.md](methodology.md#brand-normalisation). |
| `price_current` | float | `649.00` | Current selling price in AZN. Always populated; rows without a price are dropped during combining. |
| `price_original` | float | `899.00` | Pre-discount list price in AZN. Blank if the retailer shows no crossed-out original price. |
| `discount_amt` | float | `250.00` | Absolute discount in AZN (`price_original − price_current`). Blank if `price_original` is blank. |
| `discount_pct` | float | `27.8` | Percentage discount. Sourced from the retailer field where available; otherwise calculated as `(discount_amt / price_original) × 100`. Stored as a plain number (e.g. `27.8`, not `27.8%`). Negative values (irshad raw format `-14%`) are cleaned to positive. |
| `currency` | string | `AZN` | Always `AZN` (Azerbaijani Manat) for all current records. |
| `installment_6m` | float | `108.33` | Monthly payment amount for a 6-month instalment plan in AZN. Blank if not offered. |
| `installment_12m` | float | `54.17` | Monthly payment for a 12-month plan in AZN. Blank if not offered. |
| `installment_18m` | float | `36.11` | Monthly payment for an 18-month plan in AZN. Blank if not offered. |
| `in_stock` | string | `Yes` | Stock status. Values: `Yes`, `No`, or blank (not reported by retailer). |
| `url` | string | `https://birmarket.az/...` | Canonical product page URL on the retailer's site. |
| `image` | string | `https://...jpg` | Primary product image URL. Blank for a small number of listings that have no image. |

---

## Notes on Specific Columns

### `price_current`
- Prices scraped from rendered HTML or JSON API at collection time.
- Prices may include temporary promotions that have since expired.
- Not adjusted for inflation or exchange rates.

### `price_original` / `discount_amt` / `discount_pct`
- Several retailers (BakuElectronics, DigitalHome, Bytelecom, Soliton) display a crossed-out price on virtually every listing — this is a systematic display convention, not necessarily a genuine markdown from a previous higher price.
- W-T.az shows no crossed-out prices; `price_original`, `discount_amt`, and `discount_pct` are always blank for that source.

### `installment_6m` / `installment_12m` / `installment_18m`
- Represents the monthly instalment amount, not the total financed cost.
- Total cost = `installment_Xm × X`. Instalments are typically interest-free as displayed; actual credit terms are set by the issuing bank.
- Birmarket, Telsat, Almali, and W-T.az do not surface instalment data in product listings → all three columns blank for those sources.

### `in_stock`
- elitoptimal raw values `InStock` and `LimitedQuantity` are both mapped to `Yes`; `OutOfStock` maps to `No`.
- DigitalHome raw value `Mövcuddur` (Azerbaijani: "Available") maps to `Yes`.
- Retailers that do not report stock status (Telsat, Kontakt, W-T.az) leave this column blank.

---

## Source-Specific Raw Schemas

Each retailer CSV in `data/` has its own column layout before combining. The table below maps raw columns to their unified equivalents.

| Source | Raw column(s) | Unified column |
|---|---|---|
| telsat | `price` | `price_current` |
| telsat | `price_old` | `price_original` |
| birmarket | `price_old` | `price_original` |
| elitoptimal | `available` | `in_stock` |
| bakuelectronics | `installment_monthly` + `installment_months` | `installment_6m` / `12m` / `18m` |
| digitalhome | `discount` | `discount_pct` (numeric extracted) |
| irshad | `discount_pct` (string `-14%`) | `discount_pct` (float `14.0`) |

Raw per-retailer CSVs are preserved in `data/` alongside the combined file.
