"""
Async scraper for digitalhome.az smartphone listings.
Output: data/digitalhome.csv

Response envelope:
  {
    "error": false,
    "data": "<html fragment>",   # product cards
    "message": "164 məhsul",     # total count string
    "additional": {...}
  }

Card selectors:
  container  : div.tpproduct
  name       : h3.tpproduct__title a  (title attr or text)
  url        : h3.tpproduct__title a[href]
  image      : div.tpproduct__thumb a img:first-of-type [src]
  price_new  : span.price-new
  price_old  : span.price-old
  in_stock   : div.stock-status-badge span
  product_id : a.add-to-cart [data-id]
  discount   : span.product__badge-item
  install_6m : div.installment-option[data-month="6"] [data-amount]
  install_12m: div.installment-option[data-month="12"] [data-amount]
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

BASE_URL = "https://digitalhome.az"
LISTING_PATH = "/product-categories/smartfonlar"

CATEGORIES = [4, 3, 7, 8, 9, 10, 146, 163, 11, 144]
PER_PAGE = 12
SORT_BY = "default_sorting"
LAYOUT = "grid"
CONCURRENCY = 5

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

FIELDNAMES = [
    "product_id",
    "name",
    "price_current",
    "price_original",
    "discount",
    "currency",
    "in_stock",
    "installment_6m",
    "installment_12m",
    "url",
    "image",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_params(page: int) -> list[tuple]:
    params = [
        ("page", page),
        ("per-page", PER_PAGE),
        ("sort-by", SORT_BY),
        ("layout", LAYOUT),
    ]
    for cat in CATEGORIES:
        params.append(("categories[]", cat))
    return params


def clean_price(text: str) -> str:
    """Strip currency symbols, spaces → keep digits, comma, dot."""
    return re.sub(r"[^\d,.]", "", text).strip()


def parse_total(message: str) -> int:
    """'164 məhsul' → 164"""
    m = re.search(r"\d+", message or "")
    return int(m.group()) if m else 0


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for card in soup.select("div.tpproduct"):
        # ── name & url ───────────────────────────────────────────────────
        title_a = card.select_one("h3.tpproduct__title a")
        name = ""
        url = ""
        if title_a:
            name = title_a.get("title") or title_a.get_text(strip=True)
            url = title_a.get("href", "")

        # ── image ────────────────────────────────────────────────────────
        thumb_a = card.select_one("div.tpproduct__thumb > a")
        image = ""
        if thumb_a:
            img = thumb_a.select_one("img:not(.product-thumb-secondary)")
            if not img:
                img = thumb_a.find("img")
            if img:
                image = img.get("src") or img.get("data-src", "")

        # ── prices ───────────────────────────────────────────────────────
        price_new_tag = card.select_one("span.price-new")
        price_old_tag = card.select_one("span.price-old")
        price_current = clean_price(price_new_tag.get_text()) if price_new_tag else ""
        price_original = clean_price(price_old_tag.get_text()) if price_old_tag else ""

        # If no sale price, there may only be one price element
        if not price_current and not price_original:
            any_price = card.select_one(".product-price-section span")
            if any_price:
                price_current = clean_price(any_price.get_text())

        # ── discount badge ───────────────────────────────────────────────
        badge = card.select_one("span.product__badge-item")
        discount = badge.get_text(strip=True) if badge else ""

        # ── stock status ─────────────────────────────────────────────────
        stock_tag = card.select_one("div.stock-status-badge span")
        in_stock = stock_tag.get_text(strip=True) if stock_tag else ""

        # ── product id ───────────────────────────────────────────────────
        cart_a = card.select_one("a.add-to-cart")
        product_id = cart_a.get("data-id", "") if cart_a else ""

        # ── installments ─────────────────────────────────────────────────
        def get_installment(months: int) -> str:
            opt = card.select_one(
                f'div.installment-option[data-month="{months}"]'
            )
            return opt.get("data-amount", "").strip() if opt else ""

        install_6m = get_installment(6)
        install_12m = get_installment(12)

        if name:
            products.append(
                {
                    "product_id": product_id,
                    "name": name,
                    "price_current": price_current,
                    "price_original": price_original,
                    "discount": discount,
                    "currency": "AZN",
                    "in_stock": in_stock,
                    "installment_6m": install_6m,
                    "installment_12m": install_12m,
                    "url": url,
                    "image": image,
                }
            )

    return products


# ---------------------------------------------------------------------------
# Async fetching
# ---------------------------------------------------------------------------

async def fetch_page(
    session: aiohttp.ClientSession,
    page: int,
    sem: asyncio.Semaphore,
) -> tuple[int, list[dict], int]:
    """Returns (page, products, total_items)."""
    url = BASE_URL + LISTING_PATH
    params = build_params(page)

    async with sem:
        try:
            async with session.get(
                url, params=params, headers=HEADERS, ssl=True
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)

                html_fragment = data.get("data", "")
                message = data.get("message", "")
                total_items = parse_total(message)

                products = parse_cards(html_fragment)
                print(f"  page {page:3d} → {len(products):3d} products", flush=True)
                return page, products, total_items

        except aiohttp.ClientResponseError as exc:
            print(f"  page {page:3d} → HTTP {exc.status}", file=sys.stderr)
        except Exception as exc:
            print(f"  page {page:3d} → ERROR: {exc}", file=sys.stderr)

    return page, [], 0


async def scrape_all() -> list[dict]:
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=True)
    timeout = aiohttp.ClientTimeout(total=60)
    sem = asyncio.Semaphore(CONCURRENCY)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # ── Page 1: discover total ────────────────────────────────────────
        print("Fetching page 1 to determine total …")
        _, first_products, total_items = await fetch_page(session, 1, sem)

        total_pages = max(1, math.ceil(total_items / PER_PAGE))
        print(f"Total items: {total_items}  |  Total pages: {total_pages}\n")

        all_products: list[dict] = list(first_products)

        if total_pages > 1:
            tasks = [
                fetch_page(session, p, sem)
                for p in range(2, total_pages + 1)
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
        writer = csv.DictWriter(
            fh, fieldnames=FIELDNAMES, extrasaction="ignore"
        )
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
