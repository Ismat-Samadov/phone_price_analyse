"""
Async scraper for birmarket.az smartphone listings.
Output: data/birmarket.csv

Pagination (plain GET):
  Page 1 : GET /categories/3-mobil-telefonlar-ve-smartfonlar?page=1
  Page N : GET /categories/3-mobil-telefonlar-ve-smartfonlar?page=N
  Last page : max page number in div.MPProductPaginationWrapper a[href*="page="]

Card: div.MPProductItem[data-product-id][data-product-index]

Fields:
  product_id   : [data-product-id]
  name         : span.MPTitle  text
  url          : a[href] inside .MPProduct-Content  → prepend BASE_URL if relative
  image        : img[src] inside div.MPProductItem-Logo
  price_current: span[data-info="item-desc-price-new"]  text  ("399.00 ₼" → "399.00")
  price_old    : span[data-info="item-desc-price-old"]  text
  discount_pct : div.MPProductItem-Discount  text  ("-39 %" → "-39")
  installment  : div.MPInstallment span  text  ("16.63 ₼ x 24 ay")
  in_stock     : button.AddToCart presence
"""

import asyncio
import csv
import re
import sys
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL    = "https://birmarket.az"
CAT_PATH    = "/categories/3-mobil-telefonlar-ve-smartfonlar"
CONCURRENCY = 6

OUTPUT_CSV = Path(__file__).parent.parent / "data" / "birmarket.csv"

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
    "name",
    "price_current",
    "price_old",
    "discount_pct",
    "currency",
    "installment",
    "in_stock",
    "url",
    "image",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def page_url(page: int) -> str:
    return f"{BASE_URL}{CAT_PATH}?page={page}"


def clean_price(text: str) -> str:
    """
    '399.00 ₼'  →  '399.00'
    '1,299.00 ₼' → '1299.00'
    """
    t = text.strip()
    # Remove currency symbol and surrounding whitespace
    t = re.sub(r"[₼\s]", "", t)
    # Remove thousands commas (e.g. 1,299.00 → 1299.00)
    t = t.replace(",", "")
    return t


def clean_discount(text: str) -> str:
    """'-39 %' → '-39'"""
    return re.sub(r"[^\d\-]", "", text.strip())


def parse_last_page(soup: BeautifulSoup) -> int:
    nums: list[int] = []
    for a in soup.select("div.MPProductPaginationWrapper a[href]"):
        m = re.search(r"[?&]page=(\d+)", a.get("href", ""))
        if m:
            nums.append(int(m.group(1)))
    # Also check span/button elements that may show page numbers
    for el in soup.select("div.MPProductPaginationWrapper [class*='Page']"):
        t = el.get_text(strip=True)
        if t.isdigit():
            nums.append(int(t))
    return max(nums) if nums else 1


def parse_cards(html: str) -> tuple[list[dict], int]:
    soup = BeautifulSoup(html, "html.parser")
    products: list[dict] = []

    for card in soup.select("div.MPProductItem[data-product-id]"):
        # ── product ID ────────────────────────────────────────────────────
        product_id = card.get("data-product-id", "")

        # ── name ──────────────────────────────────────────────────────────
        name_tag = card.select_one("span.MPTitle")
        name = name_tag.get_text(strip=True) if name_tag else ""

        # ── url ──────────────────────────────────────────────────────────
        url = ""
        content_div = card.select_one(".MPProduct-Content")
        if content_div:
            a_tag = content_div.select_one("a[href]")
            url = a_tag.get("href", "") if a_tag else ""
        if not url:
            a_tag = card.select_one("a[href]")
            url = a_tag.get("href", "") if a_tag else ""
        if url and not url.startswith("http"):
            url = BASE_URL + "/" + url.lstrip("/")

        # ── image ─────────────────────────────────────────────────────────
        image = ""
        logo_div = card.select_one("div.MPProductItem-Logo")
        if logo_div:
            img = logo_div.select_one("img[src]")
            image = img.get("src", "") if img else ""
        if not image:
            img = card.select_one("img[src]")
            image = img.get("src", "") if img else ""
        if image and not image.startswith("http"):
            image = BASE_URL + "/" + image.lstrip("/")

        # ── prices ────────────────────────────────────────────────────────
        price_new_tag = card.select_one('span[data-info="item-desc-price-new"]')
        price_old_tag = card.select_one('span[data-info="item-desc-price-old"]')

        price_current = clean_price(price_new_tag.get_text()) if price_new_tag else ""
        price_old     = clean_price(price_old_tag.get_text()) if price_old_tag else ""

        # ── discount % ────────────────────────────────────────────────────
        disc_tag = card.select_one("div.MPProductItem-Discount")
        discount_pct = clean_discount(disc_tag.get_text()) if disc_tag else ""

        # ── installment ───────────────────────────────────────────────────
        install_tag = card.select_one("div.MPInstallment span")
        installment = install_tag.get_text(strip=True) if install_tag else ""

        # ── stock status ──────────────────────────────────────────────────
        atc = card.select_one("button.AddToCart")
        in_stock = "Yes" if atc else "No"

        if name or product_id:
            products.append(
                {
                    "product_id":   product_id,
                    "name":         name,
                    "price_current":price_current,
                    "price_old":    price_old,
                    "discount_pct": discount_pct,
                    "currency":     "AZN",
                    "installment":  installment,
                    "in_stock":     in_stock,
                    "url":          url,
                    "image":        image,
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
    print(f"Scraping {BASE_URL}{CAT_PATH} …\n")
    products = asyncio.run(scrape_all())

    if not products:
        print("No products scraped.", file=sys.stderr)
        sys.exit(1)

    # Deduplicate by product_id (fallback: url, then name)
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
