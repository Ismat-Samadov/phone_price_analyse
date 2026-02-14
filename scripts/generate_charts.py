"""
generate_charts.py
Generates all business insight charts from data/data.csv → charts/
"""

import csv
import collections
import re
import statistics
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT      = pathlib.Path(__file__).parent.parent
DATA_PATH = ROOT / "data" / "data.csv"
CHARTS    = ROOT / "charts"
CHARTS.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------
plt.rcParams.update({
    "figure.facecolor":  "#FAFAFA",
    "axes.facecolor":    "#FFFFFF",
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.grid":         True,
    "grid.color":        "#E5E5E5",
    "grid.linewidth":    0.7,
    "font.family":       "sans-serif",
    "font.size":         11,
    "axes.titlesize":    14,
    "axes.titleweight":  "bold",
    "axes.labelsize":    11,
})

PALETTE = [
    "#2563EB","#16A34A","#DC2626","#D97706","#7C3AED",
    "#0891B2","#DB2777","#65A30D","#EA580C","#4F46E5","#0D9488",
]

RETAILER_LABELS = {
    "almali":          "Almali.az",
    "bakuelectronics": "BakuElectronics",
    "birmarket":       "Birmarket.az",
    "bytelecom":       "Bytelecom.az",
    "digitalhome":     "DigitalHome.az",
    "elitoptimal":     "ElitOptimal.az",
    "irshad":          "Irshad.az",
    "kontakt":         "Kontakt.az",
    "soliton":         "Soliton.az",
    "telsat":          "Telsat.az",
    "wt":              "W-T.az",
}

# ---------------------------------------------------------------------------
# Data loading & preprocessing
# ---------------------------------------------------------------------------

def to_float(s: str):
    if not s:
        return None
    try:
        return float(re.sub(r"[^\d.]", "", s.replace(",", "")))
    except ValueError:
        return None


KNOWN_BRANDS = {
    "samsung", "apple", "iphone", "xiaomi", "poco", "redmi", "huawei",
    "honor", "oppo", "oneplus", "vivo", "realme", "sony", "nokia",
    "motorola", "google", "nothing", "infinix", "tecno", "zte", "lenovo",
    "asus", "oukitel", "vertu", "cubot", "oscal", "doogee", "philips",
    "itel", "razer",
}

BRAND_MERGE = {
    "iphone": "Apple",
    "redmi":  "Xiaomi",
    "poco":   "Xiaomi",
}


def extract_brand(name: str, brand_field: str) -> str:
    b = brand_field.strip().lower()
    if b:
        canonical = b.title()
        return BRAND_MERGE.get(b, canonical)
    name_lower = name.lower()
    for br in KNOWN_BRANDS:
        if re.search(r"\b" + re.escape(br) + r"\b", name_lower):
            canonical = br.title()
            return BRAND_MERGE.get(br, canonical)
    return ""


def load_data():
    rows = []
    with open(DATA_PATH, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            r["price_f"]      = to_float(r.get("price_current", ""))
            r["price_orig_f"] = to_float(r.get("price_original", ""))
            r["brand_norm"]   = extract_brand(r.get("name", ""), r.get("brand", ""))
            rows.append(r)
    return rows


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def save(fig, name: str):
    path = CHARTS / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {name}")


def rl(src: str) -> str:
    """Retailer label."""
    return RETAILER_LABELS.get(src, src)


# ---------------------------------------------------------------------------
# Chart 1 — Retailer Catalogue Size
# ---------------------------------------------------------------------------

def chart_catalogue_size(rows):
    counter = collections.Counter(r["source"] for r in rows)
    sources = sorted(counter, key=lambda s: counter[s])
    counts  = [counter[s] for s in sources]
    labels  = [rl(s) for s in sources]
    colors  = [PALETTE[i % len(PALETTE)] for i in range(len(sources))]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.barh(labels, counts, color=colors, height=0.6)
    ax.bar_label(bars, fmt="%d", padding=6, fontsize=10)
    ax.set_xlabel("Number of Listings")
    ax.set_title("Retailer Catalogue Size — Total Listings per Platform")
    ax.set_xlim(0, max(counts) * 1.15)
    fig.tight_layout()
    save(fig, "01_retailer_catalogue_size.png")


# ---------------------------------------------------------------------------
# Chart 2 — Median Price by Retailer
# ---------------------------------------------------------------------------

def chart_median_price(rows):
    src_prices = collections.defaultdict(list)
    for r in rows:
        if r["price_f"]:
            src_prices[r["source"]].append(r["price_f"])

    sources = sorted(src_prices, key=lambda s: statistics.median(src_prices[s]))
    medians = [statistics.median(src_prices[s]) for s in sources]
    labels  = [rl(s) for s in sources]
    colors  = [PALETTE[i % len(PALETTE)] for i in range(len(sources))]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.barh(labels, medians, color=colors, height=0.6)
    ax.bar_label(bars, fmt="%.0f AZN", padding=6, fontsize=10)
    ax.set_xlabel("Median Price (AZN)")
    ax.set_title("Price Positioning — Median Selling Price per Retailer")
    ax.set_xlim(0, max(medians) * 1.2)
    fig.tight_layout()
    save(fig, "02_median_price_by_retailer.png")


# ---------------------------------------------------------------------------
# Chart 3 — Price Segment Distribution (Stacked Bar)
# ---------------------------------------------------------------------------

SEGMENTS = [
    ("Budget\n≤200 AZN",       0,    200),
    ("Mid-Low\n200–500 AZN",  200,   500),
    ("Mid\n500–1000 AZN",     500,  1000),
    ("Premium\n1000–2000 AZN",1000, 2000),
    ("Ultra-Premium\n>2000 AZN",2000, 9e9),
]
SEG_COLORS = ["#16A34A", "#2563EB", "#D97706", "#DC2626", "#7C3AED"]

def chart_price_segments(rows):
    src_prices = collections.defaultdict(list)
    for r in rows:
        if r["price_f"]:
            src_prices[r["source"]].append(r["price_f"])

    sources = sorted(src_prices,
                     key=lambda s: statistics.median(src_prices[s]))

    seg_counts = {}
    for src in sources:
        seg_counts[src] = []
        ps = src_prices[src]
        total = len(ps)
        for _, lo, hi in SEGMENTS:
            c = sum(1 for p in ps if lo <= p < hi)
            seg_counts[src].append(c / total * 100 if total else 0)

    labels = [rl(s) for s in sources]
    x      = np.arange(len(sources))
    width  = 0.65

    fig, ax = plt.subplots(figsize=(13, 6))
    bottoms = np.zeros(len(sources))
    for i, (seg_label, _, _) in enumerate(SEGMENTS):
        vals = [seg_counts[s][i] for s in sources]
        ax.bar(x, vals, width, bottom=bottoms, color=SEG_COLORS[i],
               label=seg_label.replace("\n", " "))
        for j, (v, b) in enumerate(zip(vals, bottoms)):
            if v >= 8:
                ax.text(j, b + v / 2, f"{v:.0f}%", ha="center",
                        va="center", fontsize=8.5, color="white",
                        fontweight="bold")
        bottoms += np.array(vals)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylabel("Share of Listings (%)")
    ax.set_title("Price Segment Mix — Share of Listings by Price Band per Retailer")
    ax.legend(loc="upper left", fontsize=9, ncol=5,
              framealpha=0.9, edgecolor="#CCCCCC")
    ax.set_ylim(0, 110)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter())
    fig.tight_layout()
    save(fig, "03_price_segments_by_retailer.png")


# ---------------------------------------------------------------------------
# Chart 4 — Top Brands by Listing Count
# ---------------------------------------------------------------------------

def chart_top_brands(rows):
    bc = collections.Counter(
        r["brand_norm"] for r in rows if r["brand_norm"] and r["price_f"]
    )
    # Remove generic/non-brand entries
    skip = {"Mobil", "Smartfon", "Telefon", "", "Corn"}
    top = [(b, c) for b, c in bc.most_common(30) if b not in skip][:12]
    brands, counts = zip(*top)
    colors = [PALETTE[i % len(PALETTE)] for i in range(len(brands))]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.barh(brands, counts, color=colors, height=0.6)
    ax.bar_label(bars, fmt="%d", padding=5, fontsize=10)
    ax.set_xlabel("Number of Listings")
    ax.set_title("Brand Dominance — Top 12 Brands by Total Listings Across All Retailers")
    ax.set_xlim(0, max(counts) * 1.18)
    fig.tight_layout()
    save(fig, "04_top_brands_listing_count.png")


# ---------------------------------------------------------------------------
# Chart 5 — Average Price by Brand (top brands, ≥30 listings)
# ---------------------------------------------------------------------------

def chart_brand_avg_price(rows):
    skip = {"Mobil", "Smartfon", "Telefon", "", "Corn"}
    bc = collections.Counter(
        r["brand_norm"] for r in rows if r["brand_norm"] and r["price_f"]
    )
    eligible = [b for b, c in bc.most_common(30)
                if c >= 30 and b not in skip][:10]

    avg_prices = []
    med_prices = []
    for b in eligible:
        ps = [r["price_f"] for r in rows if r["brand_norm"] == b and r["price_f"]]
        avg_prices.append(statistics.mean(ps))
        med_prices.append(statistics.median(ps))

    order = sorted(range(len(eligible)), key=lambda i: med_prices[i])
    eligible   = [eligible[i] for i in order]
    avg_prices = [avg_prices[i] for i in order]
    med_prices = [med_prices[i] for i in order]

    x     = np.arange(len(eligible))
    width = 0.38

    fig, ax = plt.subplots(figsize=(12, 5))
    b1 = ax.bar(x - width/2, avg_prices, width, label="Average Price",
                color="#2563EB", alpha=0.85)
    b2 = ax.bar(x + width/2, med_prices, width, label="Median Price",
                color="#16A34A", alpha=0.85)
    ax.bar_label(b1, fmt="%.0f AZN", padding=3, fontsize=8.5, rotation=90)
    ax.bar_label(b2, fmt="%.0f AZN", padding=3, fontsize=8.5, rotation=90)
    ax.set_xticks(x)
    ax.set_xticklabels(eligible, rotation=15, ha="right")
    ax.set_ylabel("Price (AZN)")
    ax.set_title("Brand Price Positioning — Average vs Median Selling Price (min 30 listings)")
    ax.legend(framealpha=0.9)
    fig.tight_layout()
    save(fig, "05_brand_price_positioning.png")


# ---------------------------------------------------------------------------
# Chart 6 — Discount Aggressiveness by Retailer
# ---------------------------------------------------------------------------

def chart_discounts(rows):
    src_disc  = collections.defaultdict(list)
    src_cover = collections.defaultdict(lambda: [0, 0])  # [discounted, total]

    for r in rows:
        src = r["source"]
        src_cover[src][1] += 1
        if (r["price_f"] and r["price_orig_f"]
                and r["price_orig_f"] > r["price_f"] > 0):
            pct = (r["price_orig_f"] - r["price_f"]) / r["price_orig_f"] * 100
            src_disc[src].append(pct)
            src_cover[src][0] += 1

    sources_with_disc = [s for s in src_disc if src_disc[s]]
    # Sort by avg discount depth descending so biggest discounters are on top
    sources_with_disc.sort(key=lambda s: statistics.mean(src_disc[s]), reverse=True)

    avg_disc   = [statistics.mean(src_disc[s]) for s in sources_with_disc]
    cover_pcts = [src_cover[s][0] / src_cover[s][1] * 100
                  for s in sources_with_disc]
    labels = [rl(s) for s in sources_with_disc]

    # Two side-by-side horizontal bar charts — one scale each, fully readable
    fig, (ax_left, ax_right) = plt.subplots(
        1, 2, figsize=(14, 5),
        gridspec_kw={"wspace": 0.45}
    )
    fig.suptitle("Promotional Strategy — Discount Depth vs Coverage per Retailer",
                 fontsize=14, fontweight="bold", y=1.01)

    # ── Left: average discount depth ──────────────────────────────────────
    bars_l = ax_left.barh(labels, avg_disc, color="#DC2626", height=0.6, alpha=0.85)
    ax_left.bar_label(bars_l, fmt="%.1f%%", padding=5, fontsize=10)
    ax_left.set_xlabel("Average Discount Depth (%)")
    ax_left.set_title("How deep are discounts?", fontsize=12, fontweight="normal")
    ax_left.set_xlim(0, max(avg_disc) * 1.25)
    ax_left.invert_yaxis()

    # ── Right: coverage — % of listings with a discount ───────────────────
    bar_colors = ["#2563EB" if c >= 90 else
                  "#D97706" if c >= 50 else
                  "#DC2626" for c in cover_pcts]
    bars_r = ax_right.barh(labels, cover_pcts, color=bar_colors, height=0.6, alpha=0.85)
    ax_right.bar_label(bars_r, fmt="%.0f%%", padding=5, fontsize=10)
    ax_right.set_xlabel("Share of Listings on Promotion (%)")
    ax_right.set_title("How many products are discounted?", fontsize=12, fontweight="normal")
    ax_right.set_xlim(0, 125)
    ax_right.xaxis.set_major_formatter(mticker.PercentFormatter())
    ax_right.invert_yaxis()

    # Colour-key annotation
    ax_right.text(108, len(labels) - 0.3, "Blue = 90%+\nAmber = 50–89%\nRed = <50%",
                  fontsize=8, va="top", color="#555555",
                  bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#CCCCCC"))

    fig.tight_layout()
    save(fig, "06_discount_aggressiveness.png")


# ---------------------------------------------------------------------------
# Chart 7 — Samsung Price Across Retailers
# ---------------------------------------------------------------------------

def chart_samsung_prices(rows):
    src_ps = collections.defaultdict(list)
    for r in rows:
        if r["brand_norm"] == "Samsung" and r["price_f"]:
            src_ps[r["source"]].append(r["price_f"])

    sources = sorted(src_ps, key=lambda s: statistics.median(src_ps[s]))
    meds    = [statistics.median(src_ps[s]) for s in sources]
    avgs    = [statistics.mean(src_ps[s])   for s in sources]
    counts  = [len(src_ps[s])               for s in sources]
    labels  = [f"{rl(s)}  (n={counts[i]})" for i, s in enumerate(sources)]

    fig, ax = plt.subplots(figsize=(10, 6))

    # Horizontal bars — median price
    ax.barh(labels, meds, color="#2563EB", height=0.55, alpha=0.85,
            label="Median Price")

    # Average as a diamond marker
    ax.scatter(avgs, labels, marker="D", color="#7C3AED", s=60, zorder=5,
               label="Average Price")

    # Value labels
    for i, med in enumerate(meds):
        ax.text(med + 25, i, f"{med:,.0f} AZN", va="center",
                fontsize=9.5, color="#111111")

    ax.set_xlabel("Price (AZN)")
    ax.set_title("Samsung Price Variance — Median Price per Retailer\n"
                 "(diamond = average; gap reflects different model tier focus)")
    ax.legend(framealpha=0.9, loc="lower right")
    ax.set_xlim(0, max(meds) * 1.35)
    ax.invert_yaxis()
    fig.tight_layout()
    save(fig, "07_samsung_price_by_retailer.png")


# ---------------------------------------------------------------------------
# Chart 8 — Apple Price Across Retailers
# ---------------------------------------------------------------------------

def chart_apple_prices(rows):
    src_ps = collections.defaultdict(list)
    for r in rows:
        if r["brand_norm"] == "Apple" and r["price_f"]:
            src_ps[r["source"]].append(r["price_f"])

    # Sort by median ascending
    sources = sorted(src_ps, key=lambda s: statistics.median(src_ps[s]))
    meds    = [statistics.median(src_ps[s]) for s in sources]
    avgs    = [statistics.mean(src_ps[s])   for s in sources]
    counts  = [len(src_ps[s])               for s in sources]
    labels  = [f"{rl(s)}  (n={counts[i]})" for i, s in enumerate(sources)]

    fig, ax = plt.subplots(figsize=(10, 6))

    # Horizontal bars — median price
    bars = ax.barh(labels, meds, color="#DC2626", height=0.55, alpha=0.85,
                   label="Median Price")

    # Average price as a diamond marker — won't blow up the bar scale
    ax.scatter(avgs, labels, marker="D", color="#EA580C", s=60, zorder=5,
               label="Average Price")

    # Value labels on bars
    for i, (med, avg) in enumerate(zip(meds, avgs)):
        ax.text(med + 60, i, f"{med:,.0f} AZN", va="center",
                fontsize=9.5, color="#111111")

    ax.set_xlabel("Price (AZN)")
    ax.set_title("Apple Price Variance — Median Price per Retailer\n"
                 "(diamond = average; high averages reflect premium flagship mix)")
    ax.legend(framealpha=0.9, loc="upper left")
    ax.set_xlim(0, max(meds) * 1.45)
    ax.invert_yaxis()
    fig.tight_layout()
    save(fig, "08_apple_price_by_retailer.png")


# ---------------------------------------------------------------------------
# Chart 9 — Price Distribution (Box Plot)
# ---------------------------------------------------------------------------

def chart_price_distribution(rows):
    src_prices = collections.defaultdict(list)
    for r in rows:
        if r["price_f"] and r["price_f"] < 6000:   # exclude extreme outliers
            src_prices[r["source"]].append(r["price_f"])

    # Order by median
    sources = sorted(src_prices,
                     key=lambda s: statistics.median(src_prices[s]))
    data    = [src_prices[s] for s in sources]
    labels  = [rl(s) for s in sources]

    fig, ax = plt.subplots(figsize=(13, 6))
    bp = ax.boxplot(data, vert=True, patch_artist=True, notch=False,
                    widths=0.55, medianprops=dict(color="black", linewidth=2))
    for patch, color in zip(bp["boxes"], PALETTE):
        patch.set_facecolor(color)
        patch.set_alpha(0.75)
    for flier in bp["fliers"]:
        flier.set(marker=".", markersize=3, alpha=0.4, color="#666666")

    ax.set_xticklabels(labels, rotation=20, ha="right")
    ax.set_ylabel("Price (AZN)")
    ax.set_title("Price Distribution — Spread and Concentration of Prices per Retailer\n"
                 "(box = 25th–75th percentile, line = median, dots = outliers)")
    fig.tight_layout()
    save(fig, "09_price_distribution_boxplot.png")


# ---------------------------------------------------------------------------
# Chart 10 — Installment Plan Coverage
# ---------------------------------------------------------------------------

def chart_installments(rows):
    src_total = collections.Counter(r["source"] for r in rows)

    fields = [
        ("installment_6m",  "6-Month Plan",  "#16A34A"),
        ("installment_12m", "12-Month Plan", "#2563EB"),
        ("installment_18m", "18-Month Plan", "#7C3AED"),
    ]

    # Collect coverage per source
    coverage = {}
    for src in src_total:
        src_rows = [r for r in rows if r["source"] == src]
        total    = len(src_rows)
        coverage[src] = [
            sum(1 for r in src_rows if r.get(f, "").strip()) / total * 100
            for f, _, _ in fields
        ]

    # Only show sources with any installment data
    sources_with = [s for s in coverage
                    if any(v > 0 for v in coverage[s])]
    sources_with.sort(key=lambda s: max(coverage[s]))

    x     = np.arange(len(sources_with))
    width = 0.25
    labels = [rl(s) for s in sources_with]

    fig, ax = plt.subplots(figsize=(12, 5))
    for i, (field, label, color) in enumerate(fields):
        vals = [coverage[s][i] for s in sources_with]
        bars = ax.bar(x + (i - 1) * width, vals, width,
                      label=label, color=color, alpha=0.85)
        ax.bar_label(bars, fmt="%.0f%%", padding=2, fontsize=8.5)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.set_ylabel("% of Listings with Installment Option")
    ax.set_title("Installment Plan Coverage — % of Products Offering Credit Plans per Retailer")
    ax.legend(framealpha=0.9)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter())
    fig.tight_layout()
    save(fig, "10_installment_coverage.png")


# ---------------------------------------------------------------------------
# Chart 11 — Brand Share per Retailer (top 6 brands, stacked bar)
# ---------------------------------------------------------------------------

def chart_brand_mix_per_retailer(rows):
    skip = {"Mobil", "Smartfon", "Telefon", "", "Corn", "Itel"}
    # Global top-6 brands (excluding skips)
    bc = collections.Counter(
        r["brand_norm"] for r in rows
        if r["brand_norm"] not in skip and r["price_f"]
    )
    top_brands = [b for b, _ in bc.most_common(6)]

    sources = sorted(set(r["source"] for r in rows))

    brand_colors = {
        "Samsung":  "#2563EB",
        "Apple":    "#DC2626",
        "Xiaomi":   "#EA580C",
        "Honor":    "#16A34A",
        "Motorola": "#7C3AED",
        "Nokia":    "#0891B2",
    }
    other_color = "#AAAAAA"

    fig, ax = plt.subplots(figsize=(13, 6))
    bottoms = np.zeros(len(sources))
    x       = np.arange(len(sources))

    for brand in top_brands:
        vals = []
        for src in sources:
            src_rows = [r for r in rows if r["source"] == src and r["price_f"]]
            total = len(src_rows) or 1
            cnt   = sum(1 for r in src_rows if r["brand_norm"] == brand)
            vals.append(cnt / total * 100)
        color = brand_colors.get(brand, "#888888")
        ax.bar(x, vals, 0.65, bottom=bottoms, label=brand, color=color, alpha=0.88)
        for j, (v, b) in enumerate(zip(vals, bottoms)):
            if v >= 7:
                ax.text(j, b + v / 2, f"{v:.0f}%", ha="center",
                        va="center", fontsize=8.5, color="white",
                        fontweight="bold")
        bottoms += np.array(vals)

    # "Other" bar segment
    other_vals = [100 - bottoms[i] for i in range(len(sources))]
    other_vals = [max(0, v) for v in other_vals]
    ax.bar(x, other_vals, 0.65, bottom=bottoms, label="Other",
           color=other_color, alpha=0.6)

    ax.set_xticks(x)
    ax.set_xticklabels([rl(s) for s in sources], rotation=20, ha="right")
    ax.set_ylabel("Share of Priced Listings (%)")
    ax.set_title("Brand Mix per Retailer — Share of Top 6 Brands in Each Retailer's Catalogue")
    ax.legend(loc="upper right", ncol=4, fontsize=9,
              framealpha=0.9, edgecolor="#CCCCCC")
    ax.yaxis.set_major_formatter(mticker.PercentFormatter())
    ax.set_ylim(0, 115)
    fig.tight_layout()
    save(fig, "11_brand_mix_per_retailer.png")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading data …")
    rows = load_data()
    print(f"  {len(rows)} rows loaded\n")

    print("Generating charts …")
    chart_catalogue_size(rows)
    chart_median_price(rows)
    chart_price_segments(rows)
    chart_top_brands(rows)
    chart_brand_avg_price(rows)
    chart_discounts(rows)
    chart_samsung_prices(rows)
    chart_apple_prices(rows)
    chart_price_distribution(rows)
    chart_installments(rows)
    chart_brand_mix_per_retailer(rows)

    print(f"\nAll charts saved to {CHARTS}/")


if __name__ == "__main__":
    main()
