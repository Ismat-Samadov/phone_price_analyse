"""
Async scraper for irshad.az smartphone listings (Laravel + load-more AJAX).
Output: data/irshad.csv

Bootstrap:
  GET /az/telefon-ve-aksesuarlar/mobil-telefonlar
    → sets irsad_session + XSRF-TOKEN cookies
    → extracts <meta name="csrf-token"> value for X-CSRF-TOKEN header

AJAX endpoint (HTML fragment, 9 cards/page):
  GET /az/list-products/telefon-ve-aksesuarlar/mobil-telefonlar
      ?q=&sort=first_pinned&page=N
  Headers: X-CSRF-TOKEN, X-Requested-With: XMLHttpRequest

Stop condition: <button id="loadMore"> absent → no more pages.

Card: div.product[class*="product-"]

Fields:
  product_id   : first non-d-none div.product__tools[data-selected-id]
  product_code : a.to-compare[data-product-code] inside selected tools div
  name         : img[alt] (first image in card)
  url          : a[href*="/az/mehsullar/"]
  image        : img[src]
  price_original : span.old-price  text  ("2599.99 AZN" → "2599.99")
  price_current  : p.new-price     text
  discount_pct   : div[class*="discount-badge"] or [class*="label-discount"]
  installment_6m / 12m / 18m : input.ppl-input[data-monthly-payment] matched
                                to label "6 ay" / "12 ay" / "18 ay"
  in_stock       : a.product-add-to-cart.btn-green present
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

BASE_URL    = "https://irshad.az"
CAT_SLUG    = "telefon-ve-aksesuarlar/mobil-telefonlar"
LISTING_URL = f"{BASE_URL}/az/{CAT_SLUG}"
AJAX_URL    = f"{BASE_URL}/az/list-products/{CAT_SLUG}"

CONCURRENCY = 6
MAX_PAGE    = 200   # safety cap

OUTPUT_CSV = Path(__file__).parent.parent / "data" / "irshad.csv"

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "az",
    "DNT": "1",
}

FIELDNAMES = [
    "product_id",
    "product_code",
    "name",
    "price_current",
    "price_original",
    "discount_pct",
    "currency",
    "installment_6m",
    "installment_12m",
    "installment_18m",
    "in_stock",
    "url",
    "image",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_price(text: str) -> str:
    """'2229.99 AZN' → '2229.99'"""
    return re.sub(r"[^\d.,]", "", text).strip()


def parse_cards(html: str) -> tuple[list[dict], bool]:
    """Returns (products, has_more)."""
    soup = BeautifulSoup(html, "html.parser")

    # ── Stop condition ────────────────────────────────────────────────────
    load_more_btn = soup.select_one("#loadMore")
    has_more = load_more_btn is not None

    products: list[dict] = []

    for card in soup.select("div.product[class*='product-']"):
        # ── Active variant: first tools div NOT hidden ────────────────────
        tools = card.select_one("div.product__tools:not(.d-none)")
        if not tools:
            tools = card.select_one("div.product__tools")

        product_id   = tools.get("data-selected-id", "") if tools else ""
        compare_a    = tools.select_one("a.to-compare[data-product-code]") if tools else None
        product_code = compare_a.get("data-product-code", "") if compare_a else ""

        # ── name (img alt) ────────────────────────────────────────────────
        img_tag = card.select_one("img[src][alt]")
        name    = img_tag.get("alt", "").strip() if img_tag else ""
        image   = img_tag.get("src", "") if img_tag else ""

        # ── url ──────────────────────────────────────────────────────────
        url_a = card.select_one("a[href*='/az/mehsullar/']")
        url   = url_a.get("href", "") if url_a else ""

        # ── prices ────────────────────────────────────────────────────────
        price_div = card.select_one("div.product__price__current")
        old_tag   = price_div.select_one("span.old-price") if price_div else None
        new_tag   = price_div.select_one("p.new-price")    if price_div else None

        price_original = clean_price(old_tag.get_text()) if old_tag else ""
        price_current  = clean_price(new_tag.get_text()) if new_tag else ""

        # If no sale structure, fall back to any price element
        if not price_current and price_div:
            price_current = clean_price(price_div.get_text())

        # ── discount badge ────────────────────────────────────────────────
        disc_tag = card.select_one(
            "[class*='discount-badge'], [class*='label-discount'], "
            "[class*='sale-badge'], div.product__img [class*='discount']"
        )
        discount_pct = disc_tag.get_text(strip=True) if disc_tag else ""

        # ── installments ──────────────────────────────────────────────────
        install_map: dict[str, str] = {}
        for inp in card.select("input.ppl-input[data-monthly-payment]"):
            inp_id = inp.get("id", "")
            lbl = card.select_one(f"label[for='{inp_id}']")
            if lbl:
                months_text = lbl.get_text(strip=True)   # "6 ay", "12 ay", "18 ay"
                monthly = inp.get("data-monthly-payment", "")
                install_map[months_text] = monthly

        installment_6m  = install_map.get("6 ay",  "")
        installment_12m = install_map.get("12 ay", "")
        installment_18m = install_map.get("18 ay", "")

        # ── stock ─────────────────────────────────────────────────────────
        atc = card.select_one("a.product-add-to-cart.btn-green, button.product-add-to-cart.btn-green")
        in_stock = "Yes" if atc else "No"

        if name or product_id:
            products.append(
                {
                    "product_id":    product_id,
                    "product_code":  product_code,
                    "name":          name,
                    "price_current": price_current,
                    "price_original":price_original,
                    "discount_pct":  discount_pct,
                    "currency":      "AZN",
                    "installment_6m": installment_6m,
                    "installment_12m":installment_12m,
                    "installment_18m":installment_18m,
                    "in_stock":      in_stock,
                    "url":           url,
                    "image":         image,
                }
            )

    return products, has_more


# ---------------------------------------------------------------------------
# Bootstrap: extract CSRF token from main page
# ---------------------------------------------------------------------------

async def bootstrap(session: aiohttp.ClientSession) -> str:
    """GET listing page → returns CSRF token string, warms session cookies."""
    headers = {
        **_BASE_HEADERS,
        "accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
    }
    async with session.get(LISTING_URL, headers=headers, ssl=True) as resp:
        resp.raise_for_status()
        html = await resp.text()

    m = re.search(
        r'<meta\s+name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)["\']', html
    )
    if not m:
        raise RuntimeError("Could not find <meta name='csrf-token'> on listing page")
    csrf = m.group(1)
    print(f"  csrf token: {csrf[:20]}…")
    return csrf


# ---------------------------------------------------------------------------
# Async fetching
# ---------------------------------------------------------------------------

async def fetch_page(
    session: aiohttp.ClientSession,
    csrf: str,
    page: int,
    sem: asyncio.Semaphore,
) -> tuple[int, list[dict], bool]:
    """Returns (page, products, has_more)."""
    async with sem:
        try:
            headers = {
                **_BASE_HEADERS,
                "accept": "*/*",
                "X-CSRF-TOKEN": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "referer": LISTING_URL,
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
            }
            url = f"{AJAX_URL}?q=&sort=first_pinned&page={page}"
            async with session.get(url, headers=headers, ssl=True) as resp:
                resp.raise_for_status()
                html = await resp.text()
                products, has_more = parse_cards(html)
                print(
                    f"  page {page:3d} → {len(products):2d} products"
                    + ("  [end]" if not has_more else ""),
                    flush=True,
                )
                return page, products, has_more

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
        # ── Bootstrap: get CSRF token + session cookies ───────────────────
        print("Bootstrapping session …")
        csrf = await bootstrap(session)

        # ── Page 1 sequentially (discover has_more) ───────────────────────
        print("\nFetching page 1 …")
        _, first_products, has_more = await fetch_page(session, csrf, 1, sem)

        all_products: list[dict] = list(first_products)

        if not has_more:
            return all_products

        # ── Batch-fetch remaining pages ───────────────────────────────────
        page = 2
        while page <= MAX_PAGE:
            batch = list(range(page, page + CONCURRENCY))
            tasks = [fetch_page(session, csrf, p, sem) for p in batch]
            results = await asyncio.gather(*tasks)

            done = False
            for p, products, hm in sorted(results, key=lambda r: r[0]):
                all_products.extend(products)
                if not hm:
                    done = True
                    break   # discard pages fetched beyond end

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
