"""
Async scraper for w-t.az (World Telecom) smartphone listings.
Output: data/wt.csv

Flow:
  1. GET /k1+smartfonlar-ve-aksessuarlar?kateqoriya=2
       → warms session cookies + extracts CSRF token
       → yields first 20 product cards from the full HTML
  2. POST /k1+smartfonlar-ve-aksessuarlar/load-more?kateqoriya=2
       body: page=N  (2, 3, 4 …)
       header: X-CSRF-TOKEN: <token>
       → {"html": "<cards>"}   empty html  ⟹  stop

Card selectors (div.item > div.productCard):
  product_id     : button.add-favorite [data-id]
  name           : div.productName  text
  url            : a.productUrl [href]
  image          : img.productImage-img [src]
  price_current  : span.realPrice  (text stripped of ₼ / sup)
  installment_6m : label[for*="-6"]  [data-price]
  installment_12m: label[for*="-12"] [data-price]
  installment_18m: label[for*="-18"] [data-price]
  labels         : div.labels * p  text
  color_variants : span.color_item [data-color]  count
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

BASE_URL     = "https://www.w-t.az"
LISTING_PATH = "/k1+smartfonlar-ve-aksessuarlar"
CATEGORY_QS  = "kateqoriya=2"
LOAD_MORE_URL = f"{BASE_URL}{LISTING_PATH}/load-more?{CATEGORY_QS}"
LISTING_URL   = f"{BASE_URL}{LISTING_PATH}?{CATEGORY_QS}"
CONCURRENCY  = 6
MAX_PAGE     = 200          # safety cap

OUTPUT_CSV = Path(__file__).parent.parent / "data" / "wt.csv"

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "az,en;q=0.9,en-US;q=0.8",
    "DNT": "1",
}

FIELDNAMES = [
    "product_id",
    "name",
    "price_current",
    "currency",
    "installment_6m",
    "installment_12m",
    "installment_18m",
    "labels",
    "color_count",
    "url",
    "image",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_csrf(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("meta", {"name": "csrf-token"})
    return tag.get("content", "") if tag else ""


def clean_price(text: str) -> str:
    """'1549\n.00\n₼' → '1549.00'"""
    return re.sub(r"[^\d.]", "", text).strip()


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for item in soup.select("div.item"):
        card = item.select_one("div.productCard") or item

        # ── product ID ────────────────────────────────────────────────────
        fav_btn = card.select_one("button.add-favorite[data-id]")
        product_id = fav_btn.get("data-id", "") if fav_btn else ""

        # fallback: cart button
        if not product_id:
            cart_btn = card.select_one("button.addToCart[data-id]")
            product_id = cart_btn.get("data-id", "") if cart_btn else ""

        # ── name ─────────────────────────────────────────────────────────
        name_div = card.select_one("div.productName")
        name = name_div.get_text(strip=True) if name_div else ""

        # ── url ──────────────────────────────────────────────────────────
        url_a = card.select_one("a.productUrl[href]")
        url = url_a.get("href", "") if url_a else ""
        if url and not url.startswith("http"):
            url = BASE_URL + "/" + url.lstrip("/")

        # ── image ────────────────────────────────────────────────────────
        img = card.select_one("img.productImage-img")
        image = img.get("src", "") if img else ""
        if image and not image.startswith("http"):
            image = BASE_URL + "/" + image.lstrip("/")

        # ── price ─────────────────────────────────────────────────────────
        real_price = card.select_one("span.realPrice")
        if real_price:
            # remove <sup> tag content first
            for sup in real_price.select("sup"):
                sup.decompose()
            price_current = clean_price(real_price.get_text())
        else:
            price_current = ""

        # ── installments ─────────────────────────────────────────────────
        def get_install(months: int) -> str:
            lbl = card.select_one(f'label[for$="-{months}"][data-price]')
            return lbl.get("data-price", "") if lbl else ""

        install_6  = get_install(6)
        install_12 = get_install(12)
        install_18 = get_install(18)

        # ── labels / badges ───────────────────────────────────────────────
        label_tags = card.select("div.labels p")
        labels = " | ".join(l.get_text(strip=True) for l in label_tags if l.get_text(strip=True))

        # ── color variants ────────────────────────────────────────────────
        colors = card.select("span.color_item[data-color]")
        color_count = len(colors)

        if name:
            products.append(
                {
                    "product_id":     product_id,
                    "name":           name,
                    "price_current":  price_current,
                    "currency":       "AZN",
                    "installment_6m": install_6,
                    "installment_12m":install_12,
                    "installment_18m":install_18,
                    "labels":         labels,
                    "color_count":    color_count,
                    "url":            url,
                    "image":          image,
                }
            )

    return products


# ---------------------------------------------------------------------------
# Async fetching
# ---------------------------------------------------------------------------

async def bootstrap(session: aiohttp.ClientSession) -> tuple[list[dict], str]:
    """GET the listing page → (first_products, csrf_token)."""
    async with session.get(LISTING_URL, headers=_BASE_HEADERS, ssl=True) as r:
        r.raise_for_status()
        html = await r.text()
    csrf = extract_csrf(html)
    products = parse_cards(html)
    print(f"  page   1 → {len(products):3d} products  (CSRF: {csrf[:16]}…)")
    return products, csrf


async def fetch_page(
    session: aiohttp.ClientSession,
    page: int,
    csrf: str,
    sem: asyncio.Semaphore,
) -> tuple[int, list[dict], bool]:
    """
    POST load-more page N.
    Returns (page, products, has_more).
    has_more=False when html is empty.
    """
    post_headers = {
        **_BASE_HEADERS,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": BASE_URL,
        "Referer": LISTING_URL,
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRF-TOKEN": csrf,
    }

    async with sem:
        try:
            async with session.post(
                LOAD_MORE_URL,
                data={"page": str(page)},
                headers=post_headers,
                ssl=True,
            ) as resp:
                resp.raise_for_status()
                j = await resp.json(content_type=None)
                html = j.get("html", "") if isinstance(j, dict) else ""

                if not html or not html.strip():
                    print(f"  page {page:3d} →   0 products (end)")
                    return page, [], False

                products = parse_cards(html)
                print(f"  page {page:3d} → {len(products):3d} products", flush=True)
                return page, products, True

        except aiohttp.ClientResponseError as exc:
            print(f"  page {page:3d} → HTTP {exc.status}", file=sys.stderr)
        except Exception as exc:
            print(f"  page {page:3d} → ERROR: {exc}", file=sys.stderr)

    return page, [], True   # keep going on transient errors


async def scrape_all() -> list[dict]:
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=True)
    timeout   = aiohttp.ClientTimeout(total=60)
    sem       = asyncio.Semaphore(CONCURRENCY)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # Page 1 — bootstrap (must run first for cookies + CSRF)
        first_products, csrf = await bootstrap(session)
        all_products = list(first_products)

        # Pages 2+ — fetch in rolling batches; stop when any batch item is empty
        page = 2
        while page <= MAX_PAGE:
            batch_pages = list(range(page, page + CONCURRENCY))
            tasks = [fetch_page(session, p, csrf, sem) for p in batch_pages]
            results = await asyncio.gather(*tasks)

            done = False
            for p, products, has_more in sorted(results, key=lambda r: r[0]):
                all_products.extend(products)
                if not has_more:
                    done = True

            page += CONCURRENCY
            if done:
                break

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
