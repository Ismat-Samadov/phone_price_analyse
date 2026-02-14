"""
Async scraper for bakuelectronics.az smartphone listings via Next.js JSON API.
Output: data/bakuelectronics.csv

Pagination (Next.js _next/data):
  Endpoint : GET /_next/data/{buildId}/az/catalog/{SLUG1}/{SLUG2}.json
             ?slug={SLUG1}&slug={SLUG2}&page=N
  Build ID  : discovered dynamically from __NEXT_DATA__ on the listing page
              (changes with every site deployment)
  Page size : 18 products per page
  Total     : products.total (e.g. 417) → ceil(total/18) pages

Response envelope: pageProps.products
  items  → list[dict]   (18 per page)
  total  → int          (total product count)
  page   → int          (current page number)
  size   → int          (page size, 18)

Product fields (JSON):
  id               → product_id
  product_code     → product_code
  name             → name
  slug             → url slug   → BASE_URL/az/product/{slug}
  price            → price_original   (full price, AZN)
  discount         → discount_amt     (string, e.g. "150")
  discounted_price → price_current    (sale price; equals price when no discount)
  perMonth.price   → installment_monthly
  perMonth.month   → installment_months
  quantity         → stock_qty
  rate             → rating
  reviewCount      → review_count
  is_online        → online_only
  image            → image URL
"""

import asyncio
import csv
import math
import re
import sys
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://www.bakuelectronics.az"
SLUG1    = "telefonlar-qadcetler"
SLUG2    = "smartfonlar-mobil-telefonlar"
LANG     = "az"

LISTING_URL = f"{BASE_URL}/{LANG}/catalog/{SLUG1}/{SLUG2}"

CONCURRENCY = 6

OUTPUT_CSV = Path(__file__).parent.parent / "data" / "bakuelectronics.csv"

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "az",
    "DNT": "1",
    "sec-fetch-site": "same-origin",
}

FIELDNAMES = [
    "product_id",
    "product_code",
    "name",
    "price_current",
    "price_original",
    "discount_amt",
    "currency",
    "installment_monthly",
    "installment_months",
    "stock_qty",
    "rating",
    "review_count",
    "online_only",
    "url",
    "image",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def api_url(build_id: str, page: int) -> str:
    return (
        f"{BASE_URL}/_next/data/{build_id}/{LANG}/catalog/{SLUG1}/{SLUG2}.json"
        f"?slug={SLUG1}&slug={SLUG2}&page={page}"
    )


def product_url(slug: str) -> str:
    if not slug:
        return ""
    return f"{BASE_URL}/{LANG}/product/{slug}"


def parse_product(item: dict) -> dict:
    price_original = item.get("price", "")
    price_current  = item.get("discounted_price", "")

    # If discounted_price == price → no actual discount
    try:
        if float(price_current) >= float(price_original):
            price_original = ""
    except (TypeError, ValueError):
        pass

    discount_amt = str(item.get("discount", "")).strip()
    if discount_amt in ("0", "None", ""):
        discount_amt = ""

    per_month = item.get("perMonth") or {}
    installment_monthly = per_month.get("price", "")
    installment_months  = per_month.get("month", "")

    return {
        "product_id":          str(item.get("id", "")),
        "product_code":        str(item.get("product_code", "")),
        "name":                item.get("name", ""),
        "price_current":       price_current,
        "price_original":      price_original,
        "discount_amt":        discount_amt,
        "currency":            "AZN",
        "installment_monthly": installment_monthly,
        "installment_months":  installment_months,
        "stock_qty":           item.get("quantity", ""),
        "rating":              item.get("rate", ""),
        "review_count":        item.get("reviewCount", ""),
        "online_only":         "Yes" if item.get("is_online") else "No",
        "url":                 product_url(item.get("slug", "")),
        "image":               item.get("image", ""),
    }


# ---------------------------------------------------------------------------
# Build-ID discovery
# ---------------------------------------------------------------------------

async def get_build_id(session: aiohttp.ClientSession) -> str:
    """Fetch the listing page and extract the Next.js buildId."""
    headers = {**_BASE_HEADERS,
        "accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
    }
    async with session.get(LISTING_URL, headers=headers, ssl=True) as resp:
        resp.raise_for_status()
        html = await resp.text()

    # __NEXT_DATA__ JSON block
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        import json as _json
        nd = _json.loads(m.group(1))
        build_id = nd.get("buildId", "")
        if build_id:
            print(f"  build_id: {build_id}")
            return build_id

    # Fallback: bare regex
    m2 = re.search(r'"buildId"\s*:\s*"([^"]+)"', html)
    if m2:
        print(f"  build_id (fallback): {m2.group(1)}")
        return m2.group(1)

    raise RuntimeError("Could not extract Next.js buildId from listing page")


# ---------------------------------------------------------------------------
# Async fetching
# ---------------------------------------------------------------------------

async def fetch_page(
    session: aiohttp.ClientSession,
    build_id: str,
    page: int,
    sem: asyncio.Semaphore,
) -> tuple[int, list[dict], int]:
    """Returns (page, products, total)."""
    async with sem:
        try:
            headers = {**_BASE_HEADERS,
                "accept": "*/*",
                "x-nextjs-data": "1",
                "referer": LISTING_URL,
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
            }
            async with session.get(
                api_url(build_id, page), headers=headers, ssl=True
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)

                pdata    = data["pageProps"]["products"]["products"]
                items    = pdata.get("items", [])
                total    = pdata.get("total", 0)
                products = [parse_product(p) for p in items]

                print(
                    f"  page {page:3d} → {len(products):3d} products"
                    + (f"  (total: {total})" if page == 1 else ""),
                    flush=True,
                )
                return page, products, total

        except aiohttp.ClientResponseError as exc:
            print(f"  page {page:3d} → HTTP {exc.status}", file=sys.stderr)
        except Exception as exc:
            print(f"  page {page:3d} → ERROR: {exc}", file=sys.stderr)

    return page, [], 0


async def scrape_all() -> list[dict]:
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=True)
    timeout   = aiohttp.ClientTimeout(total=60)
    sem       = asyncio.Semaphore(CONCURRENCY)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # ── Discover build ID ─────────────────────────────────────────────
        print("Fetching listing page to discover Next.js buildId …")
        build_id = await get_build_id(session)

        # ── Page 1: discover total ────────────────────────────────────────
        print("\nFetching page 1 to determine total products …")
        _, first_products, total = await fetch_page(session, build_id, 1, sem)

        if not total:
            return first_products

        last_page = math.ceil(total / 18)
        print(f"Total pages: {last_page}  ({total} products)\n")

        all_products: list[dict] = list(first_products)

        if last_page > 1:
            tasks = [
                fetch_page(session, build_id, p, sem)
                for p in range(2, last_page + 1)
            ]
            results = await asyncio.gather(*tasks)
            for _, products, _ in sorted(results, key=lambda r: r[0]):
                all_products.extend(products)

    return all_products


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

def save_csv(products: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(products)
    print(f"\nSaved {len(products)} rows → {path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Scraping {LISTING_URL} …\n")
    products = asyncio.run(scrape_all())

    if not products:
        print("No products scraped.", file=sys.stderr)
        sys.exit(1)

    # Deduplicate by product_id (fallback: url)
    seen: set[str] = set()
    unique: list[dict] = []
    for p in products:
        key = p.get("product_id") or p.get("url") or p.get("name")
        if key and key not in seen:
            seen.add(key)
            unique.append(p)

    print(f"\nTotal unique products: {len(unique)}")
    save_csv(unique, OUTPUT_CSV)


if __name__ == "__main__":
    main()
