# Source Reference — Retailer Scrapers

> 11 retailers · data collected February 2026

Each scraper lives in `scripts/<source>.py` and writes to `data/<source>.csv`.

---

## Quick Reference

| Source slug | Site | Script | Output CSV | Records | Pagination method |
|---|---|---|---|---|---|
| `birmarket` | birmarket.az | `scripts/birmarket.py` | `data/birmarket.csv` | 1,457 | GET `?page=N` |
| `telsat` | telsat.az | `scripts/telsat.py` | `data/telsat.csv` | 1,069 | GET `?page=N` |
| `soliton` | soliton.az | `scripts/soliton.py` | `data/soliton.csv` | 472 | GET `?page=N` |
| `bakuelectronics` | bakuelectronics.az | `scripts/bakuelectronics.py` | `data/bakuelectronics.csv` | 417 | Next.js `_next/data` JSON |
| `kontakt` | kontakt.az | `scripts/kontakt.py` | `data/kontakt.csv` | 269 | GET `?page=N` |
| `irshad` | irshad.az | `scripts/irshad.py` | `data/irshad.csv` | 169 | AJAX HTML fragment + CSRF |
| `digitalhome` | digitalhome.az | `scripts/digitalhome.py` | `data/digitalhome.csv` | 162 | GET `?page=N` |
| `bytelecom` | bytelecom.az | `scripts/bytelecom.py` | `data/bytelecom.csv` | 131 | GET `?page=N` |
| `elitoptimal` | elitoptimal.az | `scripts/elitoptimal.py` | `data/elitoptimal.csv` | 114 | JSON REST API |
| `almali` | almali.az | `scripts/almali.py` | `data/almali.csv` | 85 | GET `?page=N` |
| `wt` | w-t.az | `scripts/wt.py` | `data/wt.csv` | 77 | GET `?page=N` |

---

## Per-Retailer Details

### birmarket.az
- **Category URL:** `https://birmarket.az/categories/3-mobil-telefonlar-ve-smartfonlar`
- **Pagination:** `?page=N` — stop when page returns 0 products (63 pages at collection)
- **Product card selector:** `div.MPProductItem[data-product-id]`
- **Key fields:**
  - Price current: `span[data-info="item-desc-price-new"]`
  - Price original: `span[data-info="item-desc-price-old"]`
  - Discount badge: `div.MPProductItem-Discount`
  - Instalment: `div.MPInstallment span`
  - Stock: presence of `button.AddToCart`
- **Notes:** Highest coverage of discounts (82%) and deepest average cuts (26%) in the dataset.

---

### telsat.az
- **Category URL:** `https://telsat.az/az/telefonlar/mobil-telefonlar/`
- **Pagination:** `?page=N` query parameter
- **Notes:** Operates as a classified-ad marketplace (user-to-user listings). Raw schema includes `location` and `date` fields not present in other sources. Almost entirely full-price (only 2% of listings discounted). No instalment data.

---

### soliton.az
- **Category URL:** Smartphones category
- **Pagination:** `?page=N`
- **Notes:** Value-market focus (median 400 AZN). Large share of value-tier brands (Infinix, Tecno, ZTE).

---

### bakuelectronics.az
- **Listing URL:** `https://www.bakuelectronics.az/az/catalog/smartfonlar/mobil-telefonlar`
- **Endpoint type:** Next.js `_next/data/{buildId}/az/catalog/{slug1}/{slug2}.json`
- **Build ID:** Extracted dynamically at runtime from `<script id="__NEXT_DATA__">` in the HTML listing page. Must be refreshed on each run because it changes on site redeployment.
- **Data path:** `response["pageProps"]["products"]["products"]["items"]` (double `products` nesting)
- **Pagination:** `?page=N` appended to the JSON endpoint URL; `total` field in response determines page count
- **Cookie bootstrap:** A GET to the listing HTML page via the same `aiohttp.ClientSession` sets the `unAutorizedUsr` cookie automatically; subsequent JSON requests carry this cookie.
- **Installment handling:** Raw CSV has `installment_monthly` (AZN/month) and `installment_months` (integer). The combine step maps these to the appropriate `installment_Xm` column (6, 12, or 18).
- **Notes:** Records include `product_code`, `rating`, `review_count`, and `online_only` fields not present in the unified schema.

---

### kontakt.az
- **Category URL:** Smartphones category
- **Pagination:** `?page=N`
- **Notes:** Premium positioning (median 1,200 AZN). Heavy Samsung + Apple focus. Highest concentration of outliers above 5,000 AZN.

---

### irshad.az
- **Listing URL:** `https://irshad.az/az/telefon-ve-aksesuarlar/mobil-telefonlar`
- **AJAX endpoint:** `https://irshad.az/az/list-products/telefon-ve-aksesuarlar/mobil-telefonlar?page=N`
- **Authentication:** Laravel CSRF token required.
  1. GET listing page → extract `<meta name="csrf-token" content="...">`.
  2. Pass token as `X-CSRF-TOKEN` header on all AJAX requests.
  3. Same `aiohttp.ClientSession` carries the `irsad_session` cookie.
- **Request headers required:** `X-CSRF-TOKEN`, `X-Requested-With: XMLHttpRequest`
- **Response format:** HTML fragment (not JSON). Parsed with BeautifulSoup.
- **Pagination stop condition:** Absence of `<button id="loadMore">` in the response fragment (19 pages at collection).
- **Product card parsing:**
  - Product ID: `div.product__tools[data-selected-id]` (class name includes `product-{ID}_{UUID}`)
  - Product code: `a.to-compare[data-product-code]`
  - Name: `img[alt]`
  - Price original: `span.old-price`
  - Price current: `p.new-price`
  - Instalments: `input.ppl-input[data-monthly-payment]` matched to labels by `for`/`id` attributes

---

### digitalhome.az
- **Category URL:** Smartphones category
- **Pagination:** `?page=N`
- **Notes:** No Ultra-Premium listings (caps at ~1,000 AZN). Near-100% discount coverage. Installment data covers 6m and 12m plans only (no 18m).

---

### bytelecom.az
- **Category URL:** Smartphones category
- **Pagination:** `?page=N`
- **Notes:** Near-100% discount coverage. Mid-market positioning.

---

### elitoptimal.az
- **API endpoint:** `https://api.elitoptimal.az/v1/Products/v3`
- **Parameters:** `CategoryId=132`, `Limit=24`, `Page=N`
- **Authentication:** Bearer JWT in `Authorization` header. Token is a long-lived public token (expiry 2036); no user credentials required.
- **Response format:** Pure JSON. No HTML parsing needed.
  - Products array: `response["products"]["items"]`
  - Total count: `response["productsCount"]`
  - Pages: `ceil(productsCount / Limit)` (5 pages at collection)
- **Raw schema extras:** `barcode`, `stock_qty`, `label`, `category` fields not in unified schema.
- **Notes:** Only retailer with explicit `brand` field in the API response. `previousPrice == price` indicates no discount; `price_original` is left blank in that case.

---

### almali.az
- **Category URL:** Smartphones category
- **Pagination:** `?page=N`
- **Notes:** Ultra-premium specialist. 65%+ Apple catalogue share. Only 11% of products discounted (selective, deep markdowns rather than blanket promotion).

---

### wt.az (W-T.az)
- **Category URL:** Smartphones category
- **Pagination:** `?page=N`
- **Notes:** Curated niche store (77 listings). Zero discount coverage — every product listed at a single price. No instalment data.

---

## Running a Scraper

All scrapers use the same interface:

```bash
python3 scripts/<source>.py
# Output: data/<source>.csv
```

Requirements: `aiohttp`, `beautifulsoup4`, `lxml` (or `html.parser`).

After running individual scrapers, regenerate the combined dataset:

```bash
python3 scripts/combine.py       # if a combine script exists
# or re-run the inline combine logic
```

Then regenerate charts:

```bash
python3 scripts/generate_charts.py
# Output: charts/01_*.png … charts/11_*.png
```
