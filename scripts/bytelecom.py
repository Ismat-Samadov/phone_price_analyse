"""
Async scraper for bytelecom.az smartphone listings.
Output: data/bytelecom.csv

Page structure (full HTML, Livewire-rendered):
  URL pattern : /az/category/smartfonlar-1?page=N

Card selectors:
  container      : div.product
  name           : a.product-name  (text)
  url            : a.product-name [href]
  image          : div.product-img img [src]
  price_original : h6.discount-price   (higher, crossed-out)
  price_current  : h5.price            (sale price)
  product_id     : wire:click="toggleWishlist(ID)"
  badges         : div.badge-item p    (e.g. "İlkin ödənişsiz")
  specs          : div.product-info ul li

Pagination:
  ul.pagination  → last page-item with a page number in wire:key
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

BASE_URL = "https://bytelecom.az"
LISTING_PATH = "/az/category/smartfonlar-1"
CONCURRENCY = 5

OUTPUT_CSV = Path(__file__).parent.parent / "data" / "bytelecom.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "az,en;q=0.9,en-US;q=0.8",
    "DNT": "1",
    "Referer": BASE_URL + LISTING_PATH,
}

FIELDNAMES = [
    "product_id",
    "name",
    "price_current",
    "price_original",
    "currency",
    "badges",
    "specs",
    "url",
    "image",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_price(text: str) -> str:
    """'₼ 699.00' → '699.00'"""
    return re.sub(r"[^\d.]", "", text).strip()


def parse_last_page(soup: BeautifulSoup) -> int:
    """
    Extract last page number from ul.pagination.
    Buttons have wire:key="paginator-page-1-page-N".
    """
    pag = soup.select_one("ul.pagination")
    if not pag:
        return 1
    # All page-link items with a numeric label
    page_nums: list[int] = []
    for item in pag.select("li.page-item"):
        key = item.get("wire:key", "")
        m = re.search(r"-page-(\d+)$", key)
        if m:
            page_nums.append(int(m.group(1)))
        else:
            # fallback: text content of the button/span
            txt = item.get_text(strip=True)
            if txt.isdigit():
                page_nums.append(int(txt))
    return max(page_nums) if page_nums else 1


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for card in soup.select("div.product"):
        # ── name & url ───────────────────────────────────────────────────
        name_a = card.select_one("a.product-name")
        name = name_a.get_text(strip=True) if name_a else ""
        url = name_a.get("href", "") if name_a else ""
        # fallback url from first <a> pointing to /products/
        if not url:
            for a in card.find_all("a", href=True):
                if "/products/" in a["href"]:
                    url = a["href"]
                    break

        # ── image ────────────────────────────────────────────────────────
        img_tag = card.select_one("div.product-img img")
        image = img_tag.get("src", "") if img_tag else ""

        # ── prices ───────────────────────────────────────────────────────
        # h6.discount-price = original (higher, struck-through)
        # h5.price          = current sale price
        orig_tag = card.select_one("h6.discount-price")
        curr_tag = card.select_one("h5.price")
        price_original = clean_price(orig_tag.get_text()) if orig_tag else ""
        price_current  = clean_price(curr_tag.get_text()) if curr_tag else ""

        # If only one price element exists
        if price_original and not price_current:
            price_current, price_original = price_original, ""

        # ── product ID from Livewire click handler ────────────────────────
        product_id = ""
        wc = card.find(attrs={"wire:click": re.compile(r"toggleWishlist\(\d+\)")})
        if wc:
            m = re.search(r"toggleWishlist\((\d+)\)", wc.get("wire:click", ""))
            if m:
                product_id = m.group(1)

        # ── badges ───────────────────────────────────────────────────────
        badge_tags = card.select("div.badge-item p")
        badges = " | ".join(b.get_text(strip=True) for b in badge_tags)

        # ── specs ────────────────────────────────────────────────────────
        spec_tags = card.select("div.product-info ul li")
        specs = " | ".join(s.get_text(strip=True) for s in spec_tags)

        if name:
            products.append(
                {
                    "product_id": product_id,
                    "name": name,
                    "price_current": price_current,
                    "price_original": price_original,
                    "currency": "AZN",
                    "badges": badges,
                    "specs": specs,
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
    """Returns (page, products, last_page)."""
    url = BASE_URL + LISTING_PATH
    params = {"page": page}

    async with sem:
        try:
            async with session.get(
                url, params=params, headers=HEADERS, ssl=True
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()

                soup = BeautifulSoup(html, "html.parser")
                last_page = parse_last_page(soup) if page == 1 else 0
                products = parse_cards(html)

                print(f"  page {page:3d} → {len(products):3d} products", flush=True)
                return page, products, last_page

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
        # ── Page 1: discover total pages ──────────────────────────────────
        print("Fetching page 1 to determine pagination …")
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
