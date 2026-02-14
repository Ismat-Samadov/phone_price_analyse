"""
Async scraper for almali.az (WooCommerce / Woodmart) phone listings.
Output: data/almali.csv

Pagination (PJAX GET):
  Page 1 : GET /product-category/telefonlar/?_pjax=.main-page-wrapper
  Page N : GET /product-category/telefonlar/page/N/?_pjax=.main-page-wrapper
  Required headers: X-Pjax, X-Pjax-Container, X-Requested-With
  Last page discovered from nav.woocommerce-pagination on page 1.

Card selectors (div.product[data-id]):
  product_id     : [data-id]
  name           : h3.wd-entities-title a  text
  url            : h3.wd-entities-title a [href]
  image          : img.attachment-woocommerce_thumbnail [src]
  price_original : span.price del bdi  text  (struck-through)
  price_current  : span.price ins bdi  text  (sale price)
  currency       : span.woocommerce-Price-currencySymbol
  in_stock       : 'outofstock' NOT in card classes
  labels         : span.product-label  text  (e.g. "Endirimlər", "Mövcud deyil")
  campaign       : span.awl-inner-text text  (e.g. "Kampaniya: 1369₼")
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

BASE_URL     = "https://almali.az"
CAT_PATH     = "/product-category/telefonlar"
PJAX_PARAM   = "_pjax=.main-page-wrapper"
CONCURRENCY  = 6

OUTPUT_CSV = Path(__file__).parent.parent / "data" / "almali.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html, */*; q=0.01",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "DNT": "1",
    "Referer": f"{BASE_URL}{CAT_PATH}/",
    "X-Pjax": "true",
    "X-Pjax-Container": ".main-page-wrapper",
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
    "currency",
    "in_stock",
    "labels",
    "campaign",
    "url",
    "image",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def page_url(page: int) -> str:
    if page == 1:
        return f"{BASE_URL}{CAT_PATH}/?{PJAX_PARAM}"
    return f"{BASE_URL}{CAT_PATH}/page/{page}/?{PJAX_PARAM}"


def clean_price(tag) -> str:
    """Extract numeric price from a bdi tag, stripping currency symbol."""
    if tag is None:
        return ""
    # Remove currency symbol span
    for sym in tag.select("span.woocommerce-Price-currencySymbol"):
        sym.decompose()
    return re.sub(r"[^\d.,]", "", tag.get_text()).strip()


def parse_last_page(soup: BeautifulSoup) -> int:
    pag = soup.select_one("nav.woocommerce-pagination")
    if not pag:
        return 1
    nums: list[int] = []
    for a in pag.select("a.page-numbers[href]"):
        m = re.search(r"/page/(\d+)/", a.get("href", ""))
        if m:
            nums.append(int(m.group(1)))
    for span in pag.select("span.page-numbers"):
        t = span.get_text(strip=True)
        if t.isdigit():
            nums.append(int(t))
    return max(nums) if nums else 1


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for card in soup.select("div.product[data-id]"):
        classes = card.get("class", [])

        # ── product ID ────────────────────────────────────────────────────
        product_id = card.get("data-id", "")

        # ── name & url ────────────────────────────────────────────────────
        title_a = card.select_one("h3.wd-entities-title a")
        name = title_a.get_text(strip=True) if title_a else ""
        url  = title_a.get("href", "") if title_a else ""

        # fallback URL from image link
        if not url:
            img_a = card.select_one("a.product-image-link[href]")
            url = img_a.get("href", "") if img_a else ""

        # ── image ─────────────────────────────────────────────────────────
        img = card.select_one("img.attachment-woocommerce_thumbnail")
        image = img.get("src", "") if img else ""

        # ── prices ────────────────────────────────────────────────────────
        price_tag   = card.select_one("span.price")
        del_bdi     = price_tag.select_one("del bdi")   if price_tag else None
        ins_bdi     = price_tag.select_one("ins bdi")   if price_tag else None
        # Single price (no sale)
        only_bdi    = price_tag.select_one("bdi")       if price_tag else None

        price_original = clean_price(del_bdi)
        price_current  = clean_price(ins_bdi)

        # If no sale structure, just one price
        if not price_current and not price_original and only_bdi:
            price_current = clean_price(only_bdi)

        # ── currency ──────────────────────────────────────────────────────
        currency = "AZN"

        # ── stock status ──────────────────────────────────────────────────
        in_stock = "No" if "outofstock" in classes else "Yes"

        # ── product labels ────────────────────────────────────────────────
        label_tags = card.select("span.product-label")
        labels = " | ".join(l.get_text(strip=True) for l in label_tags if l.get_text(strip=True))

        # ── campaign / AWL label ──────────────────────────────────────────
        campaign_tags = card.select("span.awl-inner-text")
        campaign = " | ".join(c.get_text(strip=True) for c in campaign_tags if c.get_text(strip=True))

        if name:
            products.append(
                {
                    "product_id":     product_id,
                    "name":           name,
                    "price_current":  price_current,
                    "price_original": price_original,
                    "currency":       currency,
                    "in_stock":       in_stock,
                    "labels":         labels,
                    "campaign":       campaign,
                    "url":            url,
                    "image":          image,
                }
            )

    return products, parse_last_page(BeautifulSoup(html, "html.parser"))


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
    print(f"Scraping {BASE_URL}{CAT_PATH}/ …\n")
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
