"""HTML parsers for Amazon search result pages and product detail pages.

Amazon's markup changes often, so every field is optional: a missing selector
gives None instead of an exception, and the raw text is kept where useful.
"""

import json
import re
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup, Tag

ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")


# --------------------------------------------------------------------------- helpers


def text(el: Tag | None) -> str | None:
    if el is None:
        return None
    t = el.get_text(" ", strip=True)
    return t or None


def first_text(root: Tag, *selectors: str) -> str | None:
    for sel in selectors:
        t = text(root.select_one(sel))
        if t:
            return t
    return None


def parse_price(raw: str | None) -> float | None:
    """'₹1,099.00' -> 1099.0"""
    if not raw:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", raw)
    return float(m.group().replace(",", "")) if m else None


def parse_rating(raw: str | None) -> float | None:
    """'4.1 out of 5 stars' -> 4.1"""
    if not raw:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*out of\s*5", raw) or re.search(r"\d+(?:\.\d+)?", raw)
    return float(m.group(1) if m.groups() else m.group()) if m else None


def parse_count(raw: str | None) -> int | None:
    """'(27,065)' -> 27065, '1.2K' -> 1200"""
    if not raw:
        return None
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([KkMm])?", raw)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    mult = {"k": 1_000, "m": 1_000_000}.get((m.group(2) or "").lower(), 1)
    return int(n * mult)


def parse_percent(raw: str | None) -> int | None:
    """'-84%' or '(79% off)' -> 84 / 79"""
    if not raw:
        return None
    m = re.search(r"(\d+)\s*%", raw)
    return int(m.group(1)) if m else None


def discount_pct(price: float | None, mrp: float | None) -> int | None:
    if price and mrp and mrp > price:
        return round((mrp - price) / mrp * 100)
    return None


def asin_from_url(url: str | None) -> str | None:
    if not url:
        return None
    # Sponsored links wrap the real URL: /sspa/click?...&url=%2FName%2Fdp%2FASIN...
    if "/sspa/click" in url:
        inner = parse_qs(urlparse(url).query).get("url", [""])[0]
        url = unquote(inner)
    m = ASIN_RE.search(url)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- search pages


def parse_search_page(html: str) -> dict:
    """Return {'products': [...], 'next_page': url|None, 'total_pages': int|None}."""
    soup = BeautifulSoup(html, "lxml")
    products = []
    for pos, card in enumerate(soup.select('div[data-component-type="s-search-result"]'), 1):
        item = parse_search_card(card)
        if item:
            item["search_position"] = pos
            products.append(item)

    nxt = soup.select_one("a.s-pagination-next")
    pages = [parse_count(text(x)) for x in soup.select(".s-pagination-item")]
    pages = [p for p in pages if p]
    return {
        "products": products,
        "next_page": nxt.get("href") if nxt else None,
        "total_pages": max(pages) if pages else None,
    }


def parse_search_card(card: Tag) -> dict | None:
    asin = card.get("data-asin")
    if not asin:
        return None

    classes = " ".join(card.get("class", []))
    sponsored = "AdHolder" in classes or bool(card.select_one(".puis-sponsored-label-text"))

    price_raw = first_text(card, ".a-price:not(.a-text-price) .a-offscreen")
    mrp_raw = first_text(card, ".a-price.a-text-price .a-offscreen")
    price, mrp = parse_price(price_raw), parse_price(mrp_raw)

    discount_raw = None
    for span in card.select("span"):
        t = span.get_text(strip=True)
        if re.fullmatch(r"\(?\d+% off\)?", t):
            discount_raw = t
            break

    rating_raw = first_text(card, "i[class*='a-star'] .a-icon-alt", "span.a-icon-alt")
    count_el = card.select_one("a[href*='customerReviews'] span, span.s-underline-text, [aria-label$='ratings']")

    img = card.select_one("img.s-image")

    return {
        "asin": asin,
        "title": first_text(card, "h2 span", "h2"),
        "title_differentiators": first_text(card, ".title-differentiators"),
        "url": f"/dp/{asin}",
        "image": img.get("src") if img else None,
        "image_srcset": img.get("srcset") if img else None,
        "price": price,
        "price_raw": price_raw,
        "mrp": mrp,
        "mrp_raw": mrp_raw,
        "discount_pct": parse_percent(discount_raw) or discount_pct(price, mrp),
        "rating": parse_rating(rating_raw),
        "rating_count": parse_count(text(count_el)),
        "bought_past_month": first_text(card, "span.a-size-base.a-color-secondary:-soup-contains('bought in past month')"),
        "is_sponsored": sponsored,
        "is_prime": bool(card.select_one("i.a-icon-prime, [aria-label='Amazon Prime']")),
        "badge": first_text(card, ".a-badge-text", "[id$='-best-seller-label']"),
        "coupon": first_text(card, ".s-coupon-unclipped", "[class*='coupon']"),
        "delivery": first_text(card, "[data-cy='delivery-recipe']", ".udm-primary-delivery-message"),
        "offers": first_text(card, ".a-truncate-full"),
    }


# --------------------------------------------------------------------------- product pages


def parse_product_page(html: str, asin: str, max_reviews: int = 10) -> dict:
    soup = BeautifulSoup(html, "lxml")

    price_raw = first_text(
        soup,
        "#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen",
        "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
        "#corePrice_feature_div .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        ".a-price .a-offscreen",
    )
    if not parse_price(price_raw):
        whole = first_text(soup, "#corePriceDisplay_desktop_feature_div .a-price-whole")
        price_raw = whole or price_raw
    mrp_raw = first_text(
        soup,
        "#corePriceDisplay_desktop_feature_div .basisPrice .a-offscreen",
        ".basisPrice .a-offscreen",
        "#corePrice_desktop .a-text-price .a-offscreen",
    )
    price, mrp = parse_price(price_raw), parse_price(mrp_raw)

    details = parse_detail_tables(soup)
    rating_count_raw = first_text(soup, "#acrCustomerReviewText")

    return {
        "asin": asin,
        "title": first_text(soup, "#productTitle", "#title"),
        "brand": details.get("Brand") or details.get("Brand Name") or _brand_from_byline(soup),
        "byline": first_text(soup, "#bylineInfo"),
        "price": price,
        "price_raw": price_raw,
        "mrp": mrp,
        "mrp_raw": mrp_raw,
        "discount_pct": parse_percent(first_text(soup, ".savingsPercentage")) or discount_pct(price, mrp),
        "currency": "INR" if (price_raw or "").startswith("₹") else None,
        "rating": parse_rating(first_text(soup, "#acrPopover .a-icon-alt", "#acrPopover")),
        "rating_count": parse_count(rating_count_raw),
        "rating_histogram": parse_histogram(soup),
        "bought_past_month": first_text(soup, "#social-proofing-faceout-title-tk_bought"),
        "availability": first_text(soup, "#availability"),
        "seller": first_text(soup, "#sellerProfileTriggerId", "#merchantInfoFeature_feature_div .offer-display-feature-text"),
        "ships_from": first_text(soup, "#fulfillerInfoFeature_feature_div .offer-display-feature-text-message", "#fulfillerInfoFeature_feature_div .offer-display-feature-text"),
        "deal_badge": clean_deal_badge(first_text(soup, "#dealBadge_feature_div", "#dealsAccessibility")),
        **parse_delivery(soup),
        "is_amazons_choice": bool(soup.select_one("#acBadge_feature_div .ac-badge-wrapper")),
        "breadcrumbs": [t for a in soup.select("#wayfinding-breadcrumbs_feature_div a") if (t := text(a))],
        "feature_bullets": [
            t for li in soup.select("#feature-bullets li span.a-list-item") if (t := text(li))
        ],
        "description": first_text(soup, "#productDescription"),
        "overview": parse_overview(soup),
        "specifications": details,
        "best_sellers_rank": details.get("Best Sellers Rank"),
        "images": parse_images(html, soup),
        "variations": parse_variations(soup),
        "reviews": parse_reviews(soup, max_reviews),
    }


# Fields that depend on the delivery pincode. Everything else on a product page
# (title, specs, ratings, reviews, images) is the same in every city.
CITY_FIELDS = (
    "price", "price_raw", "mrp", "mrp_raw", "discount_pct", "currency",
    "availability", "seller", "ships_from", "deal_badge",
    "delivery", "delivery_date", "fastest_delivery", "deliverable",
)


def split_city_fields(detail: dict) -> tuple[dict, dict]:
    """Split a parsed product page into (city-independent, city-specific) dicts."""
    city = {k: detail[k] for k in CITY_FIELDS if k in detail}
    common = {k: v for k, v in detail.items() if k not in CITY_FIELDS}
    return common, city


def parse_delivery(soup: BeautifulSoup) -> dict:
    primary = soup.select_one("#mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE")
    dated = soup.select_one("[data-csa-c-delivery-time]")
    message = text(primary) or first_text(soup, "#deliveryBlockMessage")
    blob = " ".join(filter(None, [message, first_text(soup, "#availability")])).lower()
    return {
        "delivery": _strip_details(message),
        "delivery_date": dated.get("data-csa-c-delivery-time") if dated else None,
        "fastest_delivery": _strip_details(
            first_text(soup, "#mir-layout-DELIVERY_BLOCK-slot-SECONDARY_DELIVERY_MESSAGE_LARGE")
        ),
        "deliverable": not any(
            s in blob for s in ("cannot be delivered", "not deliverable", "currently unavailable", "does not deliver")
        ),
    }


def _strip_details(raw: str | None) -> str | None:
    return re.sub(r"\s*\.?\s*Details\s*$", "", raw).replace(" .", ".") if raw else None


def clean_deal_badge(raw: str | None) -> str | None:
    """Drop countdown-timer placeholders ('NO_OF_HOURS hours') and repeated labels."""
    if not raw:
        return None
    t = re.sub(r"NO_OF_\w+\s*(?:hours|minutes|seconds)?", "|", raw)
    parts = list(dict.fromkeys(p.strip() for p in t.split("|") if p.strip()))
    return " ".join(parts) or None


def _brand_from_byline(soup: BeautifulSoup) -> str | None:
    byline = first_text(soup, "#bylineInfo")
    if not byline:
        return None
    m = re.match(r"(?:Visit the|Brand:)\s*(.+?)(?:\s+Store)?$", byline)
    return m.group(1).strip() if m else byline


def parse_detail_tables(soup: BeautifulSoup) -> dict:
    """Key/value pairs from 'Product information', tech specs and detail bullets."""
    out: dict[str, str] = {}
    for tr in soup.select("#prodDetails tr, [id^=productDetails_techSpec] tr, [id^=productDetails_detailBullets] tr"):
        k, v = text(tr.select_one("th")), text(tr.select_one("td"))
        if k and v:
            out.setdefault(_clean_key(k), _clean_val(v))
    for li in soup.select("#detailBullets_feature_div li"):
        spans = li.select("span > span")
        if len(spans) >= 2:
            k, v = text(spans[0]), text(spans[1])
            if k and v:
                out.setdefault(_clean_key(k), _clean_val(v))
    out.pop("Customer Reviews", None)
    return out


def _clean_key(k: str) -> str:
    return re.sub(r"[\s:\u200e\u200f]+$", "", re.sub(r"^[\s\u200e\u200f]+", "", k)).replace(".", "_")


def _clean_val(v: str) -> str:
    return re.sub(r"[\u200e\u200f]", "", v).strip()


def parse_overview(soup: BeautifulSoup) -> dict:
    out = {}
    for tr in soup.select("#productOverview_feature_div tr"):
        cells = tr.select("td")
        if len(cells) >= 2:
            k, v = text(cells[0]), text(cells[1])
            if k and v:
                out[_clean_key(k)] = v
    return out


def parse_histogram(soup: BeautifulSoup) -> dict:
    """{'5': 55, '4': 25, ...} as percentages."""
    out = {}
    for el in soup.select("#histogramTable [aria-label]"):
        m = re.match(r"(\d+) percent of reviews have (\d) stars?", el.get("aria-label", ""))
        if m:
            out[m.group(2)] = int(m.group(1))
    return out


def parse_images(html: str, soup: BeautifulSoup) -> list[str]:
    hires = re.findall(r'"hiRes":"(https://[^"]+)"', html)
    if not hires:
        landing = soup.select_one("#landingImage")
        if landing and landing.get("data-a-dynamic-image"):
            try:
                hires = list(json.loads(landing["data-a-dynamic-image"]).keys())
            except ValueError:
                pass
        elif landing and landing.get("src"):
            hires = [landing["src"]]
    return list(dict.fromkeys(hires))  # dedupe, keep order


def parse_variations(soup: BeautifulSoup) -> list[dict]:
    out = []
    for li in soup.select("#twister li[data-asin], #inline-twister-expander-content li[data-asin]"):
        a = li.get("data-asin")
        if a:
            out.append({"asin": a, "label": li.get("title") or text(li)})
    return out


def parse_reviews(soup: BeautifulSoup, limit: int) -> list[dict]:
    reviews = []
    seen = set()
    for r in soup.select("[data-hook=review]"):
        rid = r.get("id")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        body = r.select_one("[data-hook=reviewRichContentContainer]") or r.select_one("[data-hook=review-body]")
        date_raw = first_text(r, "[data-hook=review-date]")
        country, date = None, None
        if date_raw:
            m = re.match(r"Reviewed in (.+?) on (.+)", date_raw)
            if m:
                country, date = m.group(1), m.group(2)
        reviews.append(
            {
                "review_id": rid,
                "author": first_text(r, ".a-profile-name", "[data-hook=genome-widget]"),
                "rating": parse_rating(first_text(r, "[data-hook=review-star-rating] .a-icon-alt", "[data-hook=review-star-rating]", "[data-hook=cmps-review-star-rating]")),
                "title": _review_title(r),
                "body": text(body),
                "date": date,
                "country": country,
                "variant": first_text(r, "[data-hook=format-strip]"),
                "verified_purchase": bool(r.select_one("[data-hook=avp-badge]")),
                "helpful_votes": parse_count(first_text(r, "[data-hook=helpful-vote-statement]")),
                "image_count": len(r.select("[data-hook=review-image-tile]")),
            }
        )
        if len(reviews) >= limit:
            break
    return reviews


def _review_title(r: Tag) -> str | None:
    el = r.select_one("[data-hook=review-title]") or r.select_one("[data-hook=reviewTitle]")
    if el is None:
        return None
    # The title link contains the star rating text first; keep only the last span.
    spans = [s for s in el.select("span") if not s.select_one("span") and "out of 5" not in s.get_text()]
    return text(spans[-1]) if spans else text(el)


# --------------------------------------------------------------------------- category discovery


def parse_departments(html: str) -> list[dict]:
    """Subcategories from the 'Department' filter: [{'name': ..., 'node': ...}]."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select("#departments a[href]"):
        nodes = re.findall(r"n(?:%3A|:)(\d+)", unquote(a["href"]))
        name = text(a)
        if nodes and name:
            out.append({"name": name, "node": nodes[-1]})
    return out
