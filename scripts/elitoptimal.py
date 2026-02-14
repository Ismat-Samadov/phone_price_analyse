"""
Async scraper for elitoptimal.az smartphone listings via JSON API.
Output: data/elitoptimal.csv

API: GET https://api.elitoptimal.az/v1/Products/v3
     ?CategoryId=132&Limit=24&Page=N

Response envelope:
  productsCount  → total products (int)
  products       → list[dict]  (24 per page)

Product fields (JSON):
  id                      → product_id
  name                    → name
  brandName               → brand
  barCode                 → barcode
  price                   → price_current   (float, AZN)
  previousPrice           → price_original  (float; equals price when no discount)
  discountAmount          → discount_amt    (float)
  discountPercent         → discount_pct    (float, 0 when none)
  installmentMonthlyPayment → installment_monthly  (float, monthly AZN)
  available               → available       ("InStock" / "LimitedQuantity" / "OutOfStock" …)
  storageQuantity         → stock_qty       (int)
  labelText               → label           (promo label text, nullable)
  categoryName            → category
  route                   → url slug        → BASE_URL + "/" + route
  imageUrl                → image
"""

import asyncio
import csv
import math
import sys
from pathlib import Path

import aiohttp

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL    = "https://elitoptimal.az"
API_URL     = "https://api.elitoptimal.az/v1/Products/v3"
CATEGORY_ID = 132
LIMIT       = 24
CONCURRENCY = 6

# Long-lived public JWT embedded in the site's JS bundle
BEARER_TOKEN = (
    "eyJhbGciOiJSUzI1NiIsImtpZCI6IkZBNjEwNzA1NDFDODNFQjNFMTQzODVDODA1Q0Mw"
    "NjcyNEY1RjkyMjZSUzI1NiIsInR5cCI6ImF0K2p3dCIsIng1dCI6Ii1tRUhCVUhJUHJQ"
    "aFE0WElCY3dHY2s5ZmtpWSJ9.eyJuYmYiOjE3NzEwNTY1MjcsImV4cCI6MjA4NjQxNjUy"
    "NywiaXNzIjoiaHR0cHM6Ly9hcGkuRWxpdE9wdGltYWwuYXovIiwiYXVkIjoiQXBpIiwiY"
    "2xpZW50X2lkIjoiRWxpdE9wdGltYWxXZWIiLCJqdGkiOiJBNDZENkVDQzY0NkVGOTQwRD"
    "c5NjI3RUJBODUzRDc2MiIsImlhdCI6MTc3MTA1NjUyNywic2NvcGUiOlsiRWxpdE9wdGlt"
    "YWxBcGkiXX0.AesOvgbwYUb0R4_KWJqmiFllsjFf1WDYy01kNxgK3BnVmWPsUpx9sjB3d"
    "CV7iUsnoY4byOv8fvK6P9PcrhYA4BOm1OacqJj8wuY_0QVR3Xdiq_15IRi9s8rNDEjf6r"
    "NJscLjNy0XGiTggqKAxLx1onYFILPO9hLKITXWJwbyNmJAT1TWTUcm5xNUj_KTh4ZThWy"
    "ECOkzaqhL_fzmihCY1uSe3m6IzX42HpTuyo1XHv8yz7jWK393lIHQy-R3KmQaND9NNiqR"
    "YjbUJ9Gx0c2-ZJHe949IUMMmwwaCV4XwRMlpJSfo425GPW7koQqb5clMsMf8UeqooLLOj"
    "TzvnZgD5A"
)

OUTPUT_CSV = Path(__file__).parent.parent / "data" / "elitoptimal.csv"

HEADERS = {
    "Authorization":  f"Bearer {BEARER_TOKEN}",
    "accept":         "application/json, text/plain, */*",
    "accept-language": "az",
    "origin":         "https://elitoptimal.az",
    "os":             "web",
    "referer":        "https://elitoptimal.az/",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "dnt": "1",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}

FIELDNAMES = [
    "product_id",
    "name",
    "brand",
    "barcode",
    "price_current",
    "price_original",
    "discount_amt",
    "discount_pct",
    "currency",
    "installment_monthly",
    "available",
    "stock_qty",
    "label",
    "category",
    "url",
    "image",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def api_url(page: int) -> str:
    return f"{API_URL}?CategoryId={CATEGORY_ID}&Limit={LIMIT}&Page={page}"


def product_url(route: str) -> str:
    if not route:
        return ""
    return BASE_URL + "/" + route.lstrip("/")


def parse_product(item: dict) -> dict:
    price_current  = item.get("price", "")
    price_original = item.get("previousPrice", "")

    # previousPrice == price means no actual discount
    if price_original == price_current:
        price_original = ""

    discount_amt = item.get("discountAmount") or ""
    discount_pct = item.get("discountPercent") or ""

    # Compute from prices if API fields report 0 but prices differ
    if not discount_amt and price_original and price_current:
        try:
            diff = float(price_original) - float(price_current)
            if diff > 0:
                discount_amt = round(diff, 2)
        except (ValueError, TypeError):
            pass

    return {
        "product_id":          str(item.get("id", "")),
        "name":                item.get("name", ""),
        "brand":               item.get("brandName", ""),
        "barcode":             item.get("barCode", ""),
        "price_current":       price_current,
        "price_original":      price_original,
        "discount_amt":        discount_amt,
        "discount_pct":        discount_pct,
        "currency":            "AZN",
        "installment_monthly": item.get("installmentMonthlyPayment", ""),
        "available":           item.get("available", ""),
        "stock_qty":           item.get("storageQuantity", ""),
        "label":               item.get("labelText", "") or "",
        "category":            item.get("categoryName", ""),
        "url":                 product_url(item.get("route", "")),
        "image":               item.get("imageUrl", ""),
    }


# ---------------------------------------------------------------------------
# Async fetching
# ---------------------------------------------------------------------------

async def fetch_page(
    session: aiohttp.ClientSession,
    page: int,
    sem: asyncio.Semaphore,
) -> tuple[int, list[dict], int]:
    """Returns (page, products, total_count)."""
    async with sem:
        try:
            async with session.get(
                api_url(page), headers=HEADERS, ssl=True
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)

                total_count = data.get("productsCount", 0)
                raw_products = data.get("products", [])
                products = [parse_product(p) for p in raw_products]

                print(
                    f"  page {page:3d} → {len(products):3d} products"
                    + (f"  (total: {total_count})" if page == 1 else ""),
                    flush=True,
                )
                return page, products, total_count

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
        # ── Page 1: discover total count ──────────────────────────────────
        print("Fetching page 1 to determine total products …")
        _, first_products, total_count = await fetch_page(session, 1, sem)

        if not total_count:
            return first_products

        last_page = math.ceil(total_count / LIMIT)
        print(f"Total pages: {last_page}  ({total_count} products)\n")

        all_products: list[dict] = list(first_products)

        if last_page > 1:
            tasks = [
                fetch_page(session, p, sem)
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
    print(f"Scraping {API_URL} [CategoryId={CATEGORY_ID}] …\n")
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
