"""
Async scraper for soliton.az smartphone listings.
Output: data/soliton.csv

API: POST https://soliton.az/ajax-requests.php
Payload (form-encoded):
  action=loadProducts&sectionID=96&brandID=0&offset=N&limit=15&sorting=

Response JSON:
  {
    "html"            : "<product cards>",
    "hasMore"         : "True"/"False",
    "totalCount"      : "478",
    "loadedCount"     : "15",
    "availableFilters": [...]
  }

Card selectors (div.product-item):
  product_id     : span.compare [data-item-id]
  name           : [data-title]  or  a.prodTitle text
  url            : a.prodTitle [href]           → prepend BASE_URL
  image          : div.pic img [src]            → prepend BASE_URL
  price_current  : div.prodPrice span:first-child
  price_original : div.prodPrice span.creditPrice
  discount_pct   : div.saleStar span.percent
  discount_amt   : div.saleStar span.moneydif span.amount
  offers         : div.specialOffers div.offer span.label
  installment_6m : div.monthlyPayment[data-month="6"] span.amount
  installment_12m: div.monthlyPayment[data-month="12"] span.amount
  installment_18m: div.monthlyPayment[data-month="18"] span.amount
  brand_id       : [data-brandid]
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

BASE_URL    = "https://soliton.az"
AJAX_URL    = BASE_URL + "/ajax-requests.php"
SECTION_ID  = "96"          # smartfonlar
BRAND_ID    = "0"           # all brands
LIMIT       = 15
CONCURRENCY = 8             # POST endpoint – slightly higher concurrency ok

OUTPUT_CSV = Path(__file__).parent.parent / "data" / "soliton.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
    "X-Requested-With": "XMLHttpRequest",
    "DNT": "1",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

FIELDNAMES = [
    "product_id",
    "name",
    "price_current",
    "price_original",
    "discount_pct",
    "discount_amt",
    "currency",
    "offers",
    "installment_6m",
    "installment_12m",
    "installment_18m",
    "brand_id",
    "url",
    "image",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_payload(offset: int) -> dict:
    return {
        "action":    "loadProducts",
        "sectionID": SECTION_ID,
        "brandID":   BRAND_ID,
        "offset":    str(offset),
        "limit":     str(LIMIT),
        "sorting":   "",
    }


def clean_price(text: str) -> str:
    """'1159.99 AZN' → '1159.99'"""
    return re.sub(r"[^\d.]", "", text).strip()


def abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    return BASE_URL + "/" + href.lstrip("/")


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for card in soup.select("div.product-item"):
        # ── product ID ────────────────────────────────────────────────────
        compare_span = card.select_one("span.compare[data-item-id]")
        product_id = compare_span.get("data-item-id", "") if compare_span else ""

        # fallback: extract from basket URL
        if not product_id:
            basket = card.select_one("a.buybt[href]")
            if basket:
                m = re.search(r"productID=(\w+)", basket.get("href", ""))
                if m:
                    product_id = m.group(1)

        # ── name ─────────────────────────────────────────────────────────
        name = card.get("data-title", "").strip()
        if not name:
            title_a = card.select_one("a.prodTitle")
            name = title_a.get_text(strip=True) if title_a else ""

        # ── url ──────────────────────────────────────────────────────────
        title_a = card.select_one("a.prodTitle") or card.select_one("a.thumbHolder")
        url = abs_url(title_a.get("href", "")) if title_a else ""

        # ── image ────────────────────────────────────────────────────────
        img = card.select_one("div.pic img")
        image = abs_url(img.get("src", "")) if img else ""

        # ── prices ───────────────────────────────────────────────────────
        price_spans = card.select("div.prodPrice span")
        price_current  = ""
        price_original = ""
        for span in price_spans:
            if "creditPrice" in (span.get("class") or []):
                price_original = clean_price(span.get_text())
            elif not price_current:
                price_current = clean_price(span.get_text())

        # ── discount ─────────────────────────────────────────────────────
        pct_tag = card.select_one("div.saleStar span.percent")
        amt_tag = card.select_one("div.saleStar span.moneydif span.amount")
        discount_pct = pct_tag.get_text(strip=True) if pct_tag else ""
        discount_amt = amt_tag.get_text(strip=True) if amt_tag else ""

        # ── special offers ────────────────────────────────────────────────
        offer_tags = card.select("div.specialOffers div.offer span.label")
        offers = " | ".join(o.get_text(strip=True) for o in offer_tags)

        # ── installments ─────────────────────────────────────────────────
        def get_installment(months: int) -> str:
            tag = card.select_one(
                f'div.monthlyPayment[data-month="{months}"] span.amount'
            )
            return tag.get_text(strip=True) if tag else ""

        install_6  = get_installment(6)
        install_12 = get_installment(12)
        install_18 = get_installment(18)

        # ── brand id ─────────────────────────────────────────────────────
        brand_id = card.get("data-brandid", "")

        if name:
            products.append(
                {
                    "product_id":     product_id,
                    "name":           name,
                    "price_current":  price_current,
                    "price_original": price_original,
                    "discount_pct":   discount_pct,
                    "discount_amt":   discount_amt,
                    "currency":       "AZN",
                    "offers":         offers,
                    "installment_6m": install_6,
                    "installment_12m":install_12,
                    "installment_18m":install_18,
                    "brand_id":       brand_id,
                    "url":            url,
                    "image":          image,
                }
            )

    return products


# ---------------------------------------------------------------------------
# Async fetching
# ---------------------------------------------------------------------------

async def fetch_batch(
    session: aiohttp.ClientSession,
    offset: int,
    sem: asyncio.Semaphore,
) -> tuple[int, list[dict], int]:
    """Returns (offset, products, total_count)."""
    async with sem:
        try:
            async with session.post(
                AJAX_URL,
                data=build_payload(offset),
                headers=HEADERS,
                ssl=True,
            ) as resp:
                resp.raise_for_status()
                j = await resp.json(content_type=None)

                total_count = int(j.get("totalCount", 0))
                html        = j.get("html", "")
                products    = parse_cards(html)

                print(
                    f"  offset {offset:4d} → {len(products):3d} products"
                    + (f"  (total={total_count})" if offset == 0 else ""),
                    flush=True,
                )
                return offset, products, total_count

        except aiohttp.ClientResponseError as exc:
            print(f"  offset {offset:4d} → HTTP {exc.status}", file=sys.stderr)
        except Exception as exc:
            print(f"  offset {offset:4d} → ERROR: {exc}", file=sys.stderr)

    return offset, [], 0


async def scrape_all() -> list[dict]:
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=True)
    timeout   = aiohttp.ClientTimeout(total=60)
    sem       = asyncio.Semaphore(CONCURRENCY)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # ── Batch 0: discover total count ─────────────────────────────────
        print("Fetching offset=0 to determine total …")
        _, first_products, total_count = await fetch_batch(session, 0, sem)

        total_batches = max(1, math.ceil(total_count / LIMIT))
        print(f"Total products: {total_count}  |  Total batches: {total_batches}\n")

        all_products: list[dict] = list(first_products)

        if total_batches > 1:
            tasks = [
                fetch_batch(session, offset, sem)
                for offset in range(LIMIT, total_count, LIMIT)
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
    print(f"Scraping {AJAX_URL}  [sectionID={SECTION_ID}] …\n")
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
