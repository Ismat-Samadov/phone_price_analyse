"""
Async scraper for kontakt.az smartphone listings.
Output: data/kontakt.csv

Pagination (plain GET):
  Page 1 : GET /telefoniya/smartfonlar?p=1
  Page N : GET /telefoniya/smartfonlar?p=N
  Last page : <a class="page last" href="...?p=14"> → 14 pages

Card: div.prodItem.product-item[data-gtm][data-sku][id]

data-gtm JSON (primary source):
  item_name      → name
  item_id        → sku
  item_brand     → brand
  price          → price_current  (float)
  discount       → discount_amt   (float, 0 = no discount)
  item_category  → category

HTML fields:
  product_id     : [id]  (internal numeric id)
  url            : a.prodItem__img [href]
  image          : picture source [srcset] (first URL)   or  img.product-image [src]
  price_original : div.prodItem__prices i  text  ("2.859,99 ₼" → "2859.99")
  price_current  : div.prodItem__prices b  text
  installment    : div.prodItem__prices span text  ("0% 6 ay")
  in_stock       : absence of "out-stock" class on all color swatches
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

BASE_URL    = "https://kontakt.az"
CAT_URL     = BASE_URL + "/telefoniya/smartfonlar"
CONCURRENCY = 6

OUTPUT_CSV = Path(__file__).parent.parent / "data" / "kontakt.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "az,en;q=0.9,en-US;q=0.8",
    "DNT": "1",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
}

FIELDNAMES = [
    "product_id",
    "sku",
    "name",
    "brand",
    "price_current",
    "price_original",
    "discount_amt",
    "currency",
    "installment",
    "in_stock",
    "category",
    "url",
    "image",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def page_url(page: int) -> str:
    return f"{CAT_URL}?p={page}"


def az_price(text: str) -> str:
    """
    Convert Azerbaijani price format to plain decimal.
    '2.859,99 ₼'  →  '2859.99'
    '2.499,99 ₼'  →  '2499.99'
    '1.299 ₼'     →  '1299'
    """
    t = text.strip()
    # Remove currency symbol and whitespace
    t = re.sub(r"[₼\s]", "", t)
    # If comma present → decimal separator; dots are thousand separators
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(".", "")
    return t


def parse_last_page(soup: BeautifulSoup) -> int:
    # <a class="page last" href="...?p=14">
    last_a = soup.select_one("a.page.last[href]")
    if last_a:
        m = re.search(r"[?&]p=(\d+)", last_a.get("href", ""))
        if m:
            return int(m.group(1))
    # Fallback: highest page number in pagination links
    nums = []
    for a in soup.select("a.page[href]"):
        m = re.search(r"[?&]p=(\d+)", a.get("href", ""))
        if m:
            nums.append(int(m.group(1)))
    return max(nums) if nums else 1


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for card in soup.select("div.product-item[data-gtm]"):
        # ── GTM JSON (fast lane) ─────────────────────────────────────────
        try:
            gtm = json.loads(card.get("data-gtm", "{}"))
        except json.JSONDecodeError:
            gtm = {}

        name         = gtm.get("item_name", "")
        sku          = gtm.get("item_id", "") or card.get("data-sku", "")
        brand        = gtm.get("item_brand", "")
        gtm_price    = gtm.get("price", "")
        gtm_discount = gtm.get("discount", 0)
        category     = gtm.get("item_category", "")

        # ── product ID ────────────────────────────────────────────────────
        product_id = card.get("id", "")

        # ── url ──────────────────────────────────────────────────────────
        img_a = card.select_one("a.prodItem__img[href]")
        url = img_a.get("href", "") if img_a else ""
        if url and not url.startswith("http"):
            url = BASE_URL + "/" + url.lstrip("/")

        # ── image ─────────────────────────────────────────────────────────
        image = ""
        src_tag = card.select_one("picture source[srcset]")
        if src_tag:
            # srcset may contain multiple URLs; take first
            image = src_tag.get("srcset", "").split(",")[0].split()[0]
        if not image:
            img_tag = card.select_one("img.product-image[src]")
            image = img_tag.get("src", "") if img_tag else ""

        # ── prices from HTML ──────────────────────────────────────────────
        prices_div = card.select_one("div.prodItem__prices")
        price_original = ""
        price_current  = ""
        installment    = ""

        if prices_div:
            i_tag = prices_div.select_one("i")    # original (struck-through)
            b_tag = prices_div.select_one("b")    # current sale price
            s_tag = prices_div.select_one("span") # instalment info

            if i_tag:
                price_original = az_price(i_tag.get_text())
            if b_tag:
                price_current = az_price(b_tag.get_text())
            if s_tag:
                installment = s_tag.get_text(strip=True)

        # Fallback to GTM price if HTML price not found
        if not price_current and gtm_price:
            price_current = str(gtm_price)
        if not price_original and gtm_price and gtm_discount:
            price_original = str(round(float(gtm_price) + float(gtm_discount), 2))

        discount_amt = str(gtm_discount) if gtm_discount else ""

        # ── stock status ──────────────────────────────────────────────────
        # If ANY non-out-stock swatch exists → in stock
        all_swatches = card.select("a[class*='out-stock'], .out-stock")
        non_out = card.select(
            "a.swatch-option:not(.out-stock), div.swatch-option:not(.out-stock)"
        )
        if non_out:
            in_stock = "Yes"
        elif all_swatches:
            in_stock = "No"
        else:
            # No swatches — check for add-to-cart button
            atc = card.select_one("[class*='addToCart'], button[title*='Səbətə']")
            in_stock = "Yes" if atc else "Unknown"

        if name or sku:
            products.append(
                {
                    "product_id":    product_id,
                    "sku":           sku,
                    "name":          name,
                    "brand":         brand,
                    "price_current": price_current,
                    "price_original":price_original,
                    "discount_amt":  discount_amt,
                    "currency":      "AZN",
                    "installment":   installment,
                    "in_stock":      in_stock,
                    "category":      category,
                    "url":           url,
                    "image":         image,
                }
            )

    return products, parse_last_page(soup)


# ---------------------------------------------------------------------------
# Async fetching
# ---------------------------------------------------------------------------

async def fetch_page(
    session: aiohttp.ClientSession,
    page: int,
    sem: asyncio.Semaphore,
) -> tuple[int, list[dict], int]:
    """Returns (page, products, last_page)."""
    async with sem:
        try:
            async with session.get(
                page_url(page), headers=HEADERS, ssl=True
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()
                products, last_page = parse_cards(html)
                print(f"  page {page:3d} → {len(products):3d} products", flush=True)
                return page, products, last_page

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
        # ── Page 1: discover last page ────────────────────────────────────
        print("Fetching page 1 to determine total pages …")
        _, first_products, last_page = await fetch_page(session, 1, sem)

        last_page = max(1, last_page)
        print(f"Total pages: {last_page}\n")

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
    print(f"Scraping {CAT_URL} …\n")
    products = asyncio.run(scrape_all())

    if not products:
        print("No products scraped.", file=sys.stderr)
        sys.exit(1)

    # Deduplicate by product_id / sku (fallback: url)
    seen: set[str] = set()
    unique: list[dict] = []
    for p in products:
        key = p.get("product_id") or p.get("sku") or p.get("url")
        if key and key not in seen:
            seen.add(key)
            unique.append(p)

    print(f"\nTotal unique products: {len(unique)}")
    save_csv(unique, OUTPUT_CSV)


if __name__ == "__main__":
    main()
