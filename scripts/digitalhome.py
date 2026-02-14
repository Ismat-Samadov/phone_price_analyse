"""
Async scraper for digitalhome.az smartphone listings.
Output: data/digitalhome.csv
"""

import asyncio
import csv
import json
import re
import sys
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://digitalhome.az"
LISTING_PATH = "/product-categories/smartfonlar"

CATEGORIES = [4, 3, 7, 8, 9, 10, 146, 163, 11, 144]
PER_PAGE = 24          # bump from 12 to reduce round-trips
SORT_BY = "default_sorting"
LAYOUT = "grid"
CONCURRENCY = 5        # max simultaneous page requests

OUTPUT_CSV = Path(__file__).parent.parent / "data" / "digitalhome.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "DNT": "1",
    "Referer": f"{BASE_URL}{LISTING_PATH}",
    "X-Requested-With": "XMLHttpRequest",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_params(page: int) -> dict:
    params = [
        ("page", page),
        ("per-page", PER_PAGE),
        ("sort-by", SORT_BY),
        ("layout", LAYOUT),
    ]
    for cat in CATEGORIES:
        params.append(("categories[]", cat))
    return params


def extract_csrf(html: str) -> str | None:
    """Pull _token / csrf-token from the page HTML."""
    soup = BeautifulSoup(html, "html.parser")

    # <meta name="csrf-token" content="...">
    tag = soup.find("meta", {"name": "csrf-token"})
    if tag and tag.get("content"):
        return tag["content"]

    # <input name="_token" value="...">
    tag = soup.find("input", {"name": "_token"})
    if tag and tag.get("value"):
        return tag["value"]

    # inline JS: window.csrf_token = "..."
    match = re.search(r'csrf[_-]token["\s]*[:=]["\s]*([A-Za-z0-9+/=]{20,})', html)
    if match:
        return match.group(1)

    return None


def parse_products(data: dict | list) -> list[dict]:
    """
    Normalise the JSON payload into a flat list of product dicts.
    digitalhome.az returns either:
      • {"data": [...], "total": N, ...}
      • {"products": {"data": [...], ...}}
      • raw HTML string under "data" key  (HTML fragment)
    """
    products: list[dict] = []

    # ── JSON list directly ──────────────────────────────────────────────────
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Try common envelope keys
        for key in ("data", "products", "items", "result"):
            val = data.get(key)
            if isinstance(val, list):
                items = val
                break
            if isinstance(val, dict):
                inner = val.get("data") or val.get("items") or []
                if isinstance(inner, list):
                    items = inner
                    break
        else:
            # Fall back: if "data" is an HTML string, parse it
            raw = data.get("data", "")
            if isinstance(raw, str) and "<" in raw:
                return parse_html_fragment(raw)
            items = []
    else:
        return products

    for item in items:
        if not isinstance(item, dict):
            continue
        products.append(normalise(item))

    return products


def parse_html_fragment(html: str) -> list[dict]:
    """Parse product cards from an HTML fragment response."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for card in soup.select(".product-item, .item, [class*='product']"):
        name_tag = card.select_one(
            ".product-name, .name, h3, h2, [class*='title']"
        )
        price_tag = card.select_one(
            ".product-price, .price, [class*='price']"
        )
        link_tag = card.select_one("a[href]")
        img_tag = card.select_one("img")

        name = name_tag.get_text(strip=True) if name_tag else ""
        price_raw = price_tag.get_text(strip=True) if price_tag else ""
        price = re.sub(r"[^\d.,]", "", price_raw)
        url = BASE_URL + link_tag["href"] if link_tag else ""
        image = img_tag.get("src", "") or img_tag.get("data-src", "") if img_tag else ""

        if name:
            products.append(
                {
                    "name": name,
                    "price": price,
                    "currency": "AZN",
                    "url": url,
                    "image": image,
                    "brand": "",
                    "sku": "",
                    "in_stock": "",
                }
            )

    return products


def normalise(item: dict) -> dict:
    """Map various field names to a canonical schema."""

    def get(*keys):
        for k in keys:
            v = item.get(k)
            if v not in (None, "", []):
                return str(v)
        return ""

    name = get("name", "title", "product_name")
    sku = get("sku", "id", "product_id")
    brand = get("brand", "brand_name", "manufacturer")
    url_slug = get("url", "slug", "permalink", "link")
    if url_slug and not url_slug.startswith("http"):
        url_slug = BASE_URL + "/" + url_slug.lstrip("/")
    image = get("image", "thumbnail", "image_url", "photo")

    # Price can be nested or flat
    price = ""
    for key in ("price", "sale_price", "original_price", "current_price"):
        raw = item.get(key)
        if isinstance(raw, (int, float)):
            price = str(raw)
            break
        if isinstance(raw, str) and raw:
            price = re.sub(r"[^\d.,]", "", raw)
            break
        if isinstance(raw, dict):
            price = str(raw.get("amount") or raw.get("value") or "")
            break

    in_stock = get("in_stock", "available", "stock_status")

    return {
        "name": name,
        "price": price,
        "currency": "AZN",
        "url": url_slug,
        "image": image,
        "brand": brand,
        "sku": sku,
        "in_stock": in_stock,
    }


# ---------------------------------------------------------------------------
# Async scraping logic
# ---------------------------------------------------------------------------

async def bootstrap_session(session: aiohttp.ClientSession) -> str | None:
    """
    Load the listing page once to warm up cookies and grab the CSRF token.
    Returns the CSRF token string (or None if not found).
    """
    url = BASE_URL + LISTING_PATH
    try:
        async with session.get(url, headers=HEADERS, ssl=True) as resp:
            html = await resp.text()
            csrf = extract_csrf(html)
            return csrf
    except Exception as exc:
        print(f"[bootstrap] ERROR: {exc}", file=sys.stderr)
        return None


async def fetch_page(
    session: aiohttp.ClientSession,
    page: int,
    csrf: str | None,
    sem: asyncio.Semaphore,
) -> tuple[int, list[dict], int]:
    """
    Fetch a single listing page.
    Returns (page_number, products, total_pages).
    """
    url = BASE_URL + LISTING_PATH
    params = build_params(page)
    extra_headers = dict(HEADERS)
    if csrf:
        extra_headers["X-CSRF-TOKEN"] = csrf

    async with sem:
        try:
            async with session.get(
                url, params=params, headers=extra_headers, ssl=True
            ) as resp:
                resp.raise_for_status()
                content_type = resp.content_type or ""
                raw = await resp.text()

                if "json" in content_type:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        data = {}
                elif raw.lstrip().startswith("{") or raw.lstrip().startswith("["):
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        data = {}
                else:
                    # HTML response — wrap for parse_products
                    data = {"data": raw}

                products = parse_products(data)

                # Try to determine total pages from pagination metadata
                total_pages = 1
                if isinstance(data, dict):
                    for meta_key in (
                        "last_page", "total_pages", "pageCount", "pages"
                    ):
                        v = data.get(meta_key)
                        if isinstance(v, int) and v > 0:
                            total_pages = v
                            break
                    # Calculate from total + per_page
                    total_items = None
                    for tot_key in ("total", "count", "total_count"):
                        v = data.get(tot_key)
                        if isinstance(v, int):
                            total_items = v
                            break
                    if total_items and total_pages == 1:
                        import math
                        total_pages = math.ceil(total_items / PER_PAGE)

                print(
                    f"  page {page:3d} → {len(products):3d} products"
                    + (f"  (total_pages={total_pages})" if page == 1 else "")
                )
                return page, products, total_pages

        except aiohttp.ClientResponseError as exc:
            print(f"  page {page:3d} → HTTP {exc.status}", file=sys.stderr)
        except Exception as exc:
            print(f"  page {page:3d} → ERROR: {exc}", file=sys.stderr)

    return page, [], 1


async def scrape_all() -> list[dict]:
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=True)
    timeout = aiohttp.ClientTimeout(total=60)
    sem = asyncio.Semaphore(CONCURRENCY)

    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout
    ) as session:
        print("Bootstrapping session …")
        csrf = await bootstrap_session(session)
        print(f"CSRF token: {csrf[:20]}…" if csrf else "CSRF token: not found")

        # Fetch page 1 first to learn total page count
        print("\nFetching page 1 to determine pagination …")
        _, first_products, total_pages = await fetch_page(session, 1, csrf, sem)

        if total_pages < 1:
            total_pages = 1

        print(f"Total pages detected: {total_pages}\n")

        all_products: list[dict] = list(first_products)

        if total_pages > 1:
            tasks = [
                fetch_page(session, p, csrf, sem)
                for p in range(2, total_pages + 1)
            ]
            results = await asyncio.gather(*tasks)
            for _, products, _ in sorted(results, key=lambda r: r[0]):
                all_products.extend(products)

    return all_products


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

FIELDNAMES = ["name", "price", "currency", "url", "image", "brand", "sku", "in_stock"]


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
    print(f"Scraping {BASE_URL}{LISTING_PATH} …\n")
    products = asyncio.run(scrape_all())

    if not products:
        print("No products scraped — check the response structure.", file=sys.stderr)
        sys.exit(1)

    # Deduplicate by URL (keep first occurrence)
    seen: set[str] = set()
    unique: list[dict] = []
    for p in products:
        key = p.get("url") or p.get("name") or str(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)

    print(f"\nTotal unique products: {len(unique)}")
    save_csv(unique, OUTPUT_CSV)


if __name__ == "__main__":
    main()
