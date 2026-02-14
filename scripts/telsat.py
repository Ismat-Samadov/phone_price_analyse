"""
Async scraper for telsat.az mobile phone listings.
Output: data/telsat.csv

API: POST https://telsat.az/era_pagination.php?t=products&l=az&c=0&p=N
Body (form-encoded): pager=N&limiter=28

Response: HTML fragment (28 cards per page).
Stop condition: button[onclick*='nextPage'] contains nextPage(0, ...) → end of data.

Card selectors (div.col-6 > a.card__product):
  product_id  : a.era_fav [data-id]
  name        : h3.product-title  text
  url         : a.card__product [href]       → prepend BASE_URL
  image       : img.img-fluid [src]          → prepend BASE_URL
  price       : p.product-price text         (strip "AZN", keep number)
  price_old   : p.product-price del text     (d-none when no discount)
  location    : span.location span.text__grey6
  date        : span.date span.text__grey6
  delivery    : span[data-bs-title="Çatdırılma"] visibility
  credit      : span[data-bs-title="Kredit"]    visibility
  barter      : span[data-bs-title="Barter"]    visibility
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

BASE_URL   = "https://telsat.az"
AJAX_URL   = BASE_URL + "/era_pagination.php"
LANG       = "az"
CATEGORY   = "0"         # 0 = all phones
TYPE       = "products"
LIMITER    = 28
CONCURRENCY = 8
MAX_PAGE   = 500         # safety cap

OUTPUT_CSV = Path(__file__).parent.parent / "data" / "telsat.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
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
    "price",
    "price_old",
    "currency",
    "location",
    "date",
    "delivery",
    "credit",
    "barter",
    "url",
    "image",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def page_url(page: int) -> str:
    return f"{AJAX_URL}?t={TYPE}&l={LANG}&c={CATEGORY}&p={page}"


def page_body(page: int) -> dict:
    return {"pager": str(page), "limiter": str(LIMITER)}


def abs_url(href: str) -> str:
    if not href or href.startswith("javascript"):
        return ""
    if href.startswith("http"):
        return href
    return BASE_URL + "/" + href.lstrip("/")


def clean_price(text: str) -> str:
    """'110 AZN' → '110'"""
    return re.sub(r"[^\d.,]", "", text).strip()


def service_visible(tag) -> str:
    """Return 'Yes' if a service icon is visible (not style=display:none)."""
    if tag is None:
        return "No"
    style = tag.get("style", "")
    return "No" if "display: none" in style or "display:none" in style else "Yes"


def parse_page(html: str) -> tuple[list[dict], bool]:
    """
    Returns (products, has_more).
    has_more=False when nextPage(0, ...) appears in button onclick.
    """
    soup = BeautifulSoup(html, "html.parser")

    # ── Stop condition ────────────────────────────────────────────────────
    btn = soup.select_one("button[onclick*='nextPage']")
    has_more = True
    if btn:
        m = re.search(r"nextPage\((\d+)", btn.get("onclick", ""))
        if m and int(m.group(1)) == 0:
            has_more = False
    else:
        has_more = False

    # ── Parse cards ───────────────────────────────────────────────────────
    products: list[dict] = []

    for col in soup.select("div.col-6"):
        card = col.select_one("a.card__product")
        if not card:
            continue

        # ── product ID ────────────────────────────────────────────────────
        fav = col.select_one("a.era_fav[data-id]")
        product_id = fav.get("data-id", "") if fav else ""

        # ── url ──────────────────────────────────────────────────────────
        url = abs_url(card.get("href", ""))

        # ── image ─────────────────────────────────────────────────────────
        img = col.select_one("img.img-fluid")
        image = abs_url(img.get("src", "")) if img else ""

        # ── price ─────────────────────────────────────────────────────────
        price_p = col.select_one("p.product-price")
        del_tag = price_p.select_one("del") if price_p else None
        price_old = ""
        price = ""

        if price_p:
            # Remove del content before reading main price
            del_text = del_tag.get_text() if del_tag else ""
            price_old_raw = clean_price(del_text) if del_tag else ""
            # Only keep price_old if not "0"
            price_old = price_old_raw if price_old_raw and price_old_raw != "0" else ""

            if del_tag:
                del_tag.decompose()
            price = clean_price(price_p.get_text())

        # ── name ──────────────────────────────────────────────────────────
        name_tag = col.select_one("h3.product-title")
        name = name_tag.get_text(strip=True) if name_tag else ""

        # ── location & date ───────────────────────────────────────────────
        loc_tag = col.select_one("span.location span.text__grey6")
        location = loc_tag.get_text(strip=True) if loc_tag else ""

        date_tag = col.select_one("span.date span.text__grey6")
        date = date_tag.get_text(strip=True) if date_tag else ""

        # ── service icons ─────────────────────────────────────────────────
        def svc(title: str) -> str:
            tag = col.select_one(f'span[data-bs-title="{title}"]')
            return service_visible(tag)

        delivery = svc("Çatdırılma")
        credit   = svc("Kredit")
        barter   = svc("Barter")

        if name or url:
            products.append(
                {
                    "product_id": product_id,
                    "name":       name,
                    "price":      price,
                    "price_old":  price_old,
                    "currency":   "AZN",
                    "location":   location,
                    "date":       date,
                    "delivery":   delivery,
                    "credit":     credit,
                    "barter":     barter,
                    "url":        url,
                    "image":      image,
                }
            )

    return products, has_more


# ---------------------------------------------------------------------------
# Async fetching
# ---------------------------------------------------------------------------

async def fetch_page(
    session: aiohttp.ClientSession,
    page: int,
    sem: asyncio.Semaphore,
) -> tuple[int, list[dict], bool]:
    """Returns (page, products, has_more)."""
    async with sem:
        try:
            async with session.post(
                page_url(page),
                data=page_body(page),
                headers=HEADERS,
                ssl=True,
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()
                products, has_more = parse_page(html)
                print(
                    f"  page {page:3d} → {len(products):3d} listings"
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

    all_products: list[dict] = []

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        page = 1
        while page <= MAX_PAGE:
            # Fetch a batch of CONCURRENCY pages concurrently
            batch = list(range(page, page + CONCURRENCY))
            tasks = [fetch_page(session, p, sem) for p in batch]
            results = await asyncio.gather(*tasks)

            done = False
            for p, products, has_more in sorted(results, key=lambda r: r[0]):
                all_products.extend(products)
                if not has_more:
                    done = True
                    break          # discard pages fetched beyond end

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
    print(f"Scraping {AJAX_URL} [t={TYPE}, l={LANG}, c={CATEGORY}] …\n")
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

    print(f"\nTotal unique listings: {len(unique)}")
    save_csv(unique, OUTPUT_CSV)


if __name__ == "__main__":
    main()
