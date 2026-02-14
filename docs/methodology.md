# Methodology

> How data was collected, combined, normalised, and visualised.

---

## 1. Data Collection

### Approach
Data was collected by scraping each retailer's public smartphone category pages in February 2026. All scrapers are written in Python using `asyncio` and `aiohttp` for concurrent HTTP requests. A `Semaphore` limits concurrency per scraper (typically 5 simultaneous requests) to avoid rate-limiting.

### What was collected
Only smartphone and mobile phone listings were targeted. Accessories, tablets, and smart watches were excluded. The category URL or API filter for each retailer is documented in [sources.md](sources.md).

### What was not collected
- Seller ratings or reviews (collected for BakuElectronics only; excluded from unified schema)
- Historical price data (single point-in-time snapshot)
- Delivery or warranty terms
- User-submitted classified listings beyond what the retailer surfaces in its catalogue view

### Session and cookie handling
Several retailers require session cookies or CSRF tokens before returning data:
- **BakuElectronics** sets an `unAutorizedUsr` cookie on first page load. The scraper performs one HTML GET to warm the cookie before making any JSON API requests.
- **Irshad.az** uses a Laravel CSRF token. The scraper extracts the token from the HTML `<meta name="csrf-token">` tag and sends it as an `X-CSRF-TOKEN` header on all AJAX requests.

### Dynamic endpoints
- **BakuElectronics** uses a Next.js build ID in its `_next/data` API URLs. The build ID changes on every site redeployment. The scraper extracts the current build ID from `__NEXT_DATA__` JSON embedded in the listing HTML at startup.

---

## 2. Combining Sources

The 11 raw CSVs (`data/<source>.csv`) are combined into `data/data.csv`. The combine step:

1. Reads each source CSV with `csv.DictReader`.
2. Applies column aliases to map source-specific column names to unified names.
3. Applies per-source transformations (see below).
4. Writes all rows to `data/data.csv` with a consistent 15-column schema.

### Column aliases applied

| Raw column | Unified column | Sources affected |
|---|---|---|
| `price` | `price_current` | telsat |
| `price_old` | `price_original` | telsat, birmarket |
| `available` | `in_stock` | elitoptimal |

### Per-source transformations

**elitoptimal** — stock status mapping:
```
InStock          → Yes
LimitedQuantity  → Yes
OutOfStock       → No
```

**bakuelectronics** — instalment pivoting:
The raw CSV has `installment_monthly` (AZN/month) and `installment_months` (integer). Each row is mapped to the matching `installment_Xm` column:
```
installment_months == 6  → installment_6m  = installment_monthly
installment_months == 12 → installment_12m = installment_monthly
installment_months == 18 → installment_18m = installment_monthly
```

**digitalhome** — discount amount extraction:
The raw `discount` column contains strings like `"100.00 ₼ Endirim"`. The numeric value is extracted with regex and written to `discount_pct` as a float (this column actually holds the absolute AZN amount for DigitalHome; labelled `discount_pct` in the unified schema for consistency with other sources that do store a percentage here).

**irshad** — discount sign correction:
The raw `discount_pct` column contains strings like `"-14%"`. The leading minus and trailing percent sign are stripped; the absolute value is stored.

**DigitalHome price strings** — commas removed:
Prices like `"1,199.99"` are cleaned to `"1199.99"` before casting to float.

---

## 3. Brand Normalisation

`brand` is the most inconsistent field across retailers. The normalisation pipeline:

1. **Use the retailer's own brand field** if present and non-empty (elitoptimal provides this).
2. **Extract from the product name** by scanning `name` (lowercased) for any token matching a curated `KNOWN_BRANDS` set:
   ```
   Samsung, Apple, Xiaomi, Honor, Motorola, Vivo, Oppo, Realme,
   OnePlus, Nokia, Infinix, Tecno, Huawei, Sony, Asus, Lenovo,
   ZTE, Alcatel, CAT, Doogee, Oukitel, Blackview, Ulefone, Cubot, ...
   ```
3. **Apply brand merge rules** for common aliases:
   ```
   iphone  → Apple
   redmi   → Xiaomi
   poco    → Xiaomi
   ```
4. **Skip generic words** that appear in product names but are not brands:
   `Mobil`, `Smartfon`, `Telefon`, `Corn`, `Pro`, `Max`, `Plus`

Where no brand can be extracted, `brand` is left blank. Blank-brand rows are excluded from brand-level charts but included in all price and discount analyses.

---

## 4. Price Segment Definitions

Used in Chart 03 and related analyses:

| Segment | Price range (AZN) |
|---|---|
| Budget | < 200 |
| Mid-range | 200 – 499 |
| Upper-mid | 500 – 999 |
| Premium | 1,000 – 1,999 |
| Ultra-Premium | ≥ 2,000 |

---

## 5. Metric Definitions

**Median price** — the 50th percentile of `price_current` for all in-scope rows. Used as the primary price position metric because it is robust to outliers (e.g. a single very expensive device skewing the mean).

**Average price** — arithmetic mean of `price_current`. Shown alongside median in Charts 07 and 08 as a diamond marker; interpreted as an indicator of catalogue skew toward high-end models.

**Discount depth** — `discount_pct` where non-null. Average computed over discounted listings only (zero-discount rows excluded).

**Discount coverage** — percentage of a retailer's listings that have a non-null, non-zero `discount_pct`.

**Instalment coverage** — percentage of a retailer's listings with a non-null value in the `installment_6m`, `installment_12m`, or `installment_18m` column respectively.

---

## 6. Charting Decisions

All charts are generated by `scripts/generate_charts.py` using `matplotlib` with the `Agg` (non-interactive) backend.

### Design principles
- **No pie charts.** Pie charts are avoided for catalogue-share comparisons; stacked horizontal bars are used instead (Chart 03, Chart 11).
- **Median over mean for price bars.** Where a bar represents a price level, it uses median to avoid distortion from flagship outliers.
- **Diamond markers for averages.** Averages are shown as overlaid scatter markers (Charts 07, 08) rather than separate bars, keeping the scale anchored to the median while still surfacing skew.
- **x-axis cap at `max(medians) × 1.45`** on Charts 07 and 08. This keeps labels readable without cutting off the diamond markers for retailers with high averages; extreme average values (e.g. Telsat Apple average 7,970 AZN) are allowed to fall off the visible axis.
- **Side-by-side subplots** for Chart 06 (discount depth vs coverage). The original single dual-axis chart was illegible because 100%-coverage bars dominated. Two independent horizontal bar charts with their own scales are far easier to read.
- **Colour-coded coverage bars** in Chart 06 right panel: blue ≥ 90%, amber 50–89%, red < 50%.
- **Currency symbol** — the Manat symbol ₼ is not in all system fonts. All axis labels and annotations use the ASCII string `AZN` to avoid missing-glyph warnings.

### Output
- Directory: `charts/`
- Format: PNG, 150 DPI
- Filenames: zero-padded two-digit prefix matching section numbers in `README.md` (`01_` through `11_`)

---

## 7. Limitations

- **Point-in-time snapshot.** All data reflects a single collection date (February 2026). Prices, discounts, and stock status change frequently on Azerbaijani retail sites.
- **Display discounts ≠ real markdowns.** Several retailers (BakuElectronics, DigitalHome, Bytelecom, Soliton) show crossed-out original prices on virtually every listing. These are display conventions; the "original price" may never have been the actual selling price.
- **Telsat.az is a classified marketplace.** Some Telsat listings are from private sellers, not retailers. Prices on Telsat are not directly comparable to other retailers' new-device prices.
- **Brand extraction accuracy.** For retailers without a brand field, brand is inferred from the product name. Edge cases (bundle listings, accessory mislabelled as a phone, non-Latin scripts) may produce incorrect or blank brand values.
- **Instalment terms not verified.** Monthly instalment figures are taken directly from listing data; actual credit costs, eligibility, and bank partners are not captured.
