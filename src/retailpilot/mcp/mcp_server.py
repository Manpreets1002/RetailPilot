"""Read-only MCP server over the Amazon India crawl in MongoDB, for the RetailPilot agent.

Run: python src/retailpilot/mcp/mcp_server.py   (stdio transport)

Every tool reads the last crawl stored in MongoDB. city_offers keeps one latest row per product and city,
so there is no date filter: data stays current until the next crawl replaces it, whatever the date is.
"""

import difflib
import math
import os
import random
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import combinations
from statistics import median
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from mcp_connection import AMAZON_BASE_URL, db, load_cities

READ_ONLY = ToolAnnotations(readOnlyHint=True)
MAX_LIMIT = 50
CANDIDATE_CAP = 1000
SNIPPET_CHARS = 300

mcp = FastMCP(
    "amazon-retail",
    instructions=(
        "Answer product questions from Amazon India data for one city at a time. "
        "Call resolve_city first to get the city slug. Prices are in INR and reflect the city's "
        "delivery pincode, from the last crawl stored in the database. Each offer shows when it was observed."
    ),
)


def _clean(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in doc.items()}


def _observed_at(offer: dict | None) -> datetime | None:
    stamps = [t for t in ((offer or {}).get("listing_scraped_at"), (offer or {}).get("detail_scraped_at")) if t]
    return max(stamps) if stamps else None


def _battery_hours(product: dict) -> float | None:
    for key, value in (product.get("specifications") or {}).items():
        if "battery" in key.lower() and re.search(r"life|hour", key, re.IGNORECASE):
            m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hour|hr)", str(value), re.IGNORECASE)
            if m:
                return float(m.group(1))
    for bullet in product.get("feature_bullets") or []:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hour|hr)s?\b[^.]{0,40}battery|battery[^.]{0,40}?(\d+(?:\.\d+)?)\s*(?:hour|hr)", bullet, re.IGNORECASE)
        if m:
            return float(m.group(1) or m.group(2))
    return None


def _searchable_text(product: dict) -> str:
    parts = [
        product.get("title"),
        product.get("brand"),
        product.get("byline"),
        " ".join(product.get("feature_bullets") or []),
        product.get("description"),
        " ".join(f"{k} {v}" for k, v in (product.get("specifications") or {}).items()),
        " ".join(f"{k} {v}" for k, v in (product.get("overview") or {}).items()),
    ]
    return " ".join(p for p in parts if p).lower()


def _spec_matches(product: dict, spec_filters: dict[str, str]) -> bool:
    specs = product.get("specifications") or {}
    for want_key, want_value in spec_filters.items():
        if not any(
            want_key.lower() in key.lower() and want_value.lower() in str(value).lower()
            for key, value in specs.items()
        ):
            return False
    return True


def _card(offer: dict, product: dict) -> dict:
    seen = _observed_at(offer)
    image_url = next(
        (image for image in (product.get("images") or []) if isinstance(image, str) and image.startswith("https://")),
        None,
    )
    return {
        "asin": offer["asin"],
        "title": product.get("title"),
        "brand": product.get("brand"),
        "image_url": image_url,
        "url": f"{AMAZON_BASE_URL}/dp/{offer['asin']}",
        "price": offer.get("price"),
        "mrp": offer.get("mrp"),
        "discount_pct": offer.get("discount_pct"),
        "in_stock": offer.get("availability"),
        "delivery_date": offer.get("delivery_date"),
        "seller": offer.get("seller"),
        "observed_at": seen.isoformat() if seen else None,
        "rating": product.get("rating"),
        "rating_count": product.get("rating_count"),
        "battery_hours": _battery_hours(product),
        "key_features": [b[:SNIPPET_CHARS] for b in (product.get("feature_bullets") or [])[:3]],
    }


def _review_view(r: dict) -> dict:
    return {
        "asin": r.get("asin"),
        "rating": r.get("rating"),
        "title": r.get("title"),
        "body": (r.get("body") or "")[: SNIPPET_CHARS * 2],
        "date": r.get("date"),
        "verified_purchase": r.get("verified_purchase"),
        "helpful_votes": r.get("helpful_votes"),
    }


def _offer_summary(offer: dict | None) -> dict | None:
    if offer is None:
        return None
    seen = _observed_at(offer)
    return {
        "price": offer.get("price"),
        "mrp": offer.get("mrp"),
        "discount_pct": offer.get("discount_pct"),
        "delivery_date": offer.get("delivery_date"),
        "availability": offer.get("availability"),
        "seller": offer.get("seller"),
        "observed_at": seen.isoformat() if seen else None,
    }


@mcp.tool(
    "list_categories",
    description="List crawled Amazon categories with product counts. Use to see what is available.",
    annotations=READ_ONLY,
)
async def list_categories() -> list[dict]:
    categories = await db.categories.find({}, {"_id": 0, "name": 1, "node": 1}).sort("name", 1).to_list(None)
    return [
        {"name": c["name"], "node": c["node"], "products": await db.products.count_documents({"category_nodes": c["node"]})}
        for c in categories
    ]


# Shopper words that mean a different electronics term in the crawled category names.
SEARCH_SYNONYMS = {
    "phone": ["mobile"], "smartphone": ["mobile"], "earphone": ["earbud", "headphone"],
    "headset": ["headphone"], "laptop": ["computer"], "pc": ["computer"], "watch": ["wearable"],
    "smartwatch": ["wearable"], "kindle": ["ebook"], "projector": ["theater"], "tv": ["television"],
}


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower())]


def _term_score(term: str, name_tokens: list[str]) -> float:
    if term in name_tokens:
        return 1.0
    if len(term) >= 4 and any(t.startswith(term) for t in name_tokens):
        return 0.8
    # Typo tolerance: "headphons" still finds "headphone".
    if len(term) >= 4 and difflib.get_close_matches(term, name_tokens, n=1, cutoff=0.8):
        return 0.7
    return 0.0


@mcp.tool(
    "match_category",
    description=(
        "Find the category for any shopper search. Searches products by title, brand, features and specs, then "
        "ranks the categories those products belong to. Returns best, the one category to open when a single "
        "category clearly fits. Returns matches, several categories to choose from, when more than one fits. "
        "Returns neither when no product in the data matches."
    ),
    annotations=READ_ONLY,
)
async def match_category(query: str, limit: int = 5) -> dict:
    words = re.findall(r"[a-z0-9]+", (query or "").lower())
    query_terms = [_stem(w) for w in words]
    counts: Counter = Counter()
    products = await db.products.find({}, {"_id": 0}).to_list(None)
    category_names = {}
    for product in products:
        names = {n for n in (product.get("breadcrumbs") or []) + (product.get("category_names") or []) if n and n != "Electronics"}
        category_names[product["asin"]] = names
        counts.update(names)
    top_level = await db.categories.find({}, {"_id": 0, "name": 1}).sort("name", 1).to_list(None)
    examples = [c["name"] for c in top_level][:6]
    if not words:
        return {"query": query, "best": None, "matches": [], "matched": 0, "examples": examples}

    # Products match when every word appears in their text; if none do, accept products that match at least half the words.
    texts = {p["asin"]: _searchable_text(p) for p in products}
    word_hits = {asin: sum(1 for w in set(words) if w in text) for asin, text in texts.items()}
    need = len(set(words))
    hits = [asin for asin, n in word_hits.items() if n == need]
    if not hits:
        hits = [asin for asin, n in word_hits.items() if n and n >= math.ceil(need / 2)]

    hit_counts: Counter = Counter()
    for asin in hits:
        hit_counts.update(category_names.get(asin, set()))

    # A category name can also match the words directly, e.g. "headphone" matches "Headphones".
    # A category also qualifies when it holds a fair share of the matched products, e.g. a brand spread across categories.
    top_hit = max(hit_counts.values(), default=0)
    candidates = []
    for name in set(counts) | set(hit_counts):
        name_tokens = _tokens(name)
        name_score = 0.0
        if name_tokens and query_terms:
            per_term = []
            for term in query_terms:
                options = [term] + [s for syn in SEARCH_SYNONYMS.get(term, []) for s in _tokens(syn)]
                per_term.append(max(_term_score(option, name_tokens) for option in options))
            name_score = sum(per_term) / len(query_terms)
        share = hit_counts[name] / len(hits) if hits else 0.0
        combined = 0.6 * name_score + 0.4 * share
        if combined >= 0.3 or (hit_counts[name] and hit_counts[name] >= max(1, math.ceil(0.25 * top_hit))):
            candidates.append((combined, name_score, len(name_tokens), counts[name], name))

    candidates.sort(key=lambda row: (-row[0], row[2], -row[3], row[4]))
    matches = [
        {"name": name, "products": counts[name], "matched": hit_counts[name], "score": round(combined, 2)}
        for combined, _, _, _, name in candidates[:limit]
    ]
    # Route straight to one category only when it is the single fit, or the only exact name match.
    best = None
    if candidates:
        top = candidates[0]
        if len(candidates) == 1 or (top[1] >= 0.9 and candidates[1][1] < 0.9):
            best = top[4]
    return {"query": query, "best": best, "matches": matches, "matched": len(hits), "examples": examples}


@mcp.tool(
    "resolve_city",
    description="Map the user's city, state or pincode to a supported city slug. Call before searching.",
    annotations=READ_ONLY,
)
async def resolve_city(location: str) -> dict:
    key = location.strip().lower()
    cities = load_cities()
    match = next(
        (c for c in cities if key in (c["slug"], c["name"].lower(), c["pincode"], c.get("state", "").lower())),
        None,
    ) or next((c for c in cities if key and key in c["name"].lower()), None)
    if match is None:
        return {"slug": None, "covered": False, "supported_cities": [c["slug"] for c in cities]}
    covered = await db.city_offers.find_one({"city": match["slug"]}, {"_id": 1}) is not None
    return {"slug": match["slug"], "name": match["name"], "covered": covered}


@mcp.tool(
    "search_products",
    description=(
        "Search products in one city, using the last crawl stored for each product. Prices are in INR. "
        "query: every word must appear in the title, brand, features, description or specs. category: partial "
        "match on category or breadcrumb names. spec_filters: spec name contains and value contains, e.g. "
        "{\"colour\": \"black\"}. sort_by: rating, price_asc or price_desc."
    ),
    annotations=READ_ONLY,
)
async def search_products(
    city: str,
    query: str | None = None,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    brand: str | None = None,
    spec_filters: dict[str, str] | None = None,
    min_battery_hours: float | None = None,
    min_rating: float | None = None,
    min_ratings_count: int | None = None,
    sort_by: str = "rating",
    limit: int = 10,
) -> dict:
    product_filter: dict[str, Any] = {}
    if category:
        pattern = {"$regex": re.escape(category), "$options": "i"}
        product_filter["$or"] = [{"breadcrumbs": pattern}, {"category_names": pattern}]
    products = {p["asin"]: p for p in await db.products.find(product_filter).limit(CANDIDATE_CAP).to_list(None)}
    if not products:
        return {"city": city, "matched": 0, "results": [], "note": "No crawled products match the category."}

    query_filter: dict[str, Any] = {"city": city, "asin": {"$in": list(products)}}
    price = {k: v for k, v in (("$gte", min_price), ("$lte", max_price)) if v is not None}
    if price:
        query_filter["price"] = price
    offers = await db.city_offers.find(query_filter).limit(CANDIDATE_CAP).to_list(None)

    terms = query.lower().split() if query else []
    rows = []
    for offer in offers:
        product = products[offer["asin"]]
        if terms and not all(t in _searchable_text(product) for t in terms):
            continue
        if brand and brand.lower() not in (product.get("brand") or "").lower():
            continue
        if spec_filters and not _spec_matches(product, spec_filters):
            continue
        row = _card(offer, product)
        if min_battery_hours is not None and (row["battery_hours"] or 0) < min_battery_hours:
            continue
        if min_rating is not None and (row["rating"] or 0) < min_rating:
            continue
        if min_ratings_count is not None and (row["rating_count"] or 0) < min_ratings_count:
            continue
        rows.append(row)

    if sort_by == "price_asc":
        rows.sort(key=lambda r: r["price"] if r["price"] is not None else float("inf"))
    elif sort_by == "price_desc":
        rows.sort(key=lambda r: -(r["price"] or 0))
    else:
        rows.sort(key=lambda r: (r["rating"] or 0, r["rating_count"] or 0), reverse=True)

    limit = max(1, min(limit, MAX_LIMIT))
    return {"city": city, "matched": len(rows), "results": rows[:limit]}


@mcp.tool(
    "get_product",
    description="Full details for one ASIN: all specs, features, description, and the city's latest price and delivery.",
    annotations=READ_ONLY,
)
async def get_product(asin: str, city: str) -> dict:
    product = await db.products.find_one({"asin": asin}, {"_id": 0, "images": 0})
    if product is None:
        return {"error": f"ASIN {asin} not found"}
    offer = await db.city_offers.find_one({"asin": asin, "city": city}, {"_id": 0, "listing": 0})
    return {
        "product": _clean(product),
        "offer": _offer_summary(offer),
        "note": None if offer else f"Not crawled for city '{city}'; price and delivery unknown.",
    }


@mcp.tool(
    "customer_product_insights",
    description=(
        "Material for pros and cons: features, key specs, rating breakdown, and positive and negative "
        "reviews for one ASIN in a city. Use before answering quality questions."
    ),
    annotations=READ_ONLY,
)
async def customer_product_insights(asin: str, city: str, max_reviews_per_side: int = 5) -> dict:
    product = await db.products.find_one({"asin": asin}, {"_id": 0, "images": 0})
    if product is None:
        return {"error": f"ASIN {asin} not found"}
    offer = await db.city_offers.find_one(
        {"asin": asin, "city": city},
        {"_id": 0, "price": 1, "mrp": 1, "discount_pct": 1, "delivery_date": 1, "availability": 1, "seller": 1, "listing_scraped_at": 1, "detail_scraped_at": 1},
    )
    reviews = await (
        db.reviews.find({"asin": asin}, {"_id": 0, "asin": 1, "title": 1, "body": 1, "rating": 1, "date": 1, "verified_purchase": 1, "helpful_votes": 1})
        .sort("helpful_votes", -1)
        .to_list(None)
    )
    cap = max(1, min(max_reviews_per_side, MAX_LIMIT))
    positive = [_review_view(r) for r in reviews if (r.get("rating") or 0) >= 4][:cap]
    negative = [_review_view(r) for r in reviews if r.get("rating") is not None and r["rating"] <= 2][:cap]
    return {
        "asin": asin,
        "title": product.get("title"),
        "offer": _offer_summary(offer),
        "rating": product.get("rating"),
        "rating_count": product.get("rating_count"),
        "rating_histogram": product.get("rating_histogram"),
        "feature_bullets": product.get("feature_bullets") or [],
        "key_specs": dict(list((product.get("specifications") or {}).items())[:25]),
        "positive_reviews": positive,
        "negative_reviews": negative,
        "reviews_stored": len(reviews),
        "note": "Only the reviews shown on the product page are stored (about 8-10 per product). Reviews are a sample, not the full picture.",
    }


@mcp.tool(
    "customer_compare_products",
    description="Compare up to 5 ASINs in a city: latest price, rating, and every spec that at least two of them share.",
    annotations=READ_ONLY,
)
async def customer_compare_products(asins: list[str], city: str) -> dict:
    found = []
    for asin in asins[:5]:
        product = await db.products.find_one({"asin": asin}, {"_id": 0, "images": 0})
        if product is None:
            found.append({"asin": asin, "error": "not found"})
            continue
        offer = await db.city_offers.find_one(
            {"asin": asin, "city": city},
            {"_id": 0, "price": 1, "mrp": 1, "delivery_date": 1, "availability": 1, "seller": 1, "listing_scraped_at": 1, "detail_scraped_at": 1},
        )
        found.append({
            "asin": asin,
            "title": product.get("title"),
            "offer": _offer_summary(offer),
            "rating": product.get("rating"),
            "rating_count": product.get("rating_count"),
            "battery_hours": _battery_hours(product),
            "specs": product.get("specifications") or {},
        })

    key_counts: dict[str, int] = {}
    for f in found:
        for key in f.get("specs", {}):
            key_counts[key] = key_counts.get(key, 0) + 1
    shared = [k for k, n in key_counts.items() if n >= 2]
    rows = []
    for f in found:
        if "specs" not in f:
            rows.append(f)
            continue
        rows.append({k: v for k, v in f.items() if k != "specs"} | {"shared_specs": {k: f["specs"].get(k) for k in shared}})
    return {"city": city, "products": rows, "shared_spec_names": shared}


@mcp.tool(
    "get_reviews",
    description="Stored reviews for one ASIN, filtered by rating range, verified purchase, or a keyword in the text.",
    annotations=READ_ONLY,
)
async def get_reviews(
    asin: str,
    limit: int = 10,
    min_rating: float | None = None,
    max_rating: float | None = None,
    verified_only: bool = False,
    keyword: str | None = None,
) -> dict:
    product = await db.products.find_one({"asin": asin}, {"_id": 0, "rating": 1, "rating_count": 1, "rating_histogram": 1})
    query: dict[str, Any] = {"asin": asin}
    rating = {k: v for k, v in (("$gte", min_rating), ("$lte", max_rating)) if v is not None}
    if rating:
        query["rating"] = rating
    if verified_only:
        query["verified_purchase"] = True
    reviews = await db.reviews.find(query, {"_id": 0, "asin": 1, "title": 1, "body": 1, "rating": 1, "date": 1, "verified_purchase": 1, "helpful_votes": 1}).sort("helpful_votes", -1).to_list(None)
    if keyword:
        reviews = [r for r in reviews if keyword.lower() in f"{r.get('title') or ''} {r.get('body') or ''}".lower()]
    return {
        "asin": asin,
        "rating": product.get("rating") if product else None,
        "rating_count": product.get("rating_count") if product else None,
        "rating_histogram": product.get("rating_histogram") if product else None,
        "reviews": [_review_view(r) for r in reviews[: max(1, min(limit, MAX_LIMIT))]],
        "note": "Only the reviews shown on the product page are stored (about 8-10 per product).",
    }


@mcp.tool(
    "search_reviews",
    description="Find reviews across all products that mention a keyword (e.g. battery, heating, sound), optionally by rating range.",
    annotations=READ_ONLY,
)
async def search_reviews(keyword: str, min_rating: float | None = None, max_rating: float | None = None, limit: int = 10) -> dict:
    query: dict[str, Any] = {}
    rating = {k: v for k, v in (("$gte", min_rating), ("$lte", max_rating)) if v is not None}
    if rating:
        query["rating"] = rating
    needle = keyword.lower()
    hits = []
    cursor = db.reviews.find(query, {"_id": 0, "asin": 1, "title": 1, "body": 1, "rating": 1, "date": 1, "verified_purchase": 1, "helpful_votes": 1}).sort("helpful_votes", -1)
    async for r in cursor:
        if needle in f"{r.get('title') or ''} {r.get('body') or ''}".lower():
            hits.append(_review_view(r))
            if len(hits) >= max(1, min(limit, MAX_LIMIT)):
                break
    return {"keyword": keyword, "matched": len(hits), "reviews": hits}


@mcp.tool(
    "get_price_history",
    description="Recorded price observations for one ASIN in a city, newest first. Use only when the user asks about past prices.",
    annotations=READ_ONLY,
)
async def get_price_history(asin: str, city: str, limit: int = 20) -> dict:
    rows = await (
        db.price_history.find({"asin": asin, "city": city}, {"_id": 0, "price": 1, "mrp": 1, "discount_pct": 1, "scraped_at": 1, "source": 1})
        .sort("scraped_at", -1)
        .limit(max(1, min(limit, MAX_LIMIT)))
        .to_list(None)
    )
    return {"asin": asin, "city": city, "observations": [_clean(r) for r in rows]}


THIN_MARGIN_PCT = 10.0
RAISE_PCT = 5.0
STALE_HOURS = 48
GENERIC_AUTHORS = {"", "amazon customer", "customer"}
STOPWORDS = {
    "the", "and", "for", "this", "that", "with", "was", "but", "not", "are", "have", "has", "its", "very",
    "you", "your", "they", "them", "from", "had", "just", "use", "used", "get", "got", "one", "its", "it's",
    "product", "laptop", "phone", "buy", "bought", "good", "great", "nice", "also", "after", "more", "than",
}


def _subcategory(product: dict) -> str:
    for name in product.get("category_names") or []:
        if name and name.lower() != "electronics":
            return name
    return "Other"


def _margin_at(price: float, landed: float, gst_pct: float, fees_pct: float) -> dict:
    net = price / (1 + gst_pct / 100)
    fees = net * fees_pct / 100
    contribution = net - landed - fees
    return {
        "net_revenue": round(net, 2),
        "contribution": round(contribution, 2),
        "margin_pct": round(contribution / net * 100, 2) if net else 0.0,
        "breakeven_price": round(landed / (1 - fees_pct / 100) * (1 + gst_pct / 100), 2),
    }


async def _peer_offers(product: dict, asin: str, city: str) -> list[dict]:
    peer_asins = [
        p["asin"]
        for p in await db.products.find({"category_names": _subcategory(product), "asin": {"$ne": asin}}, {"_id": 0, "asin": 1})
        .limit(CANDIDATE_CAP)
        .to_list(None)
    ]
    return await db.city_offers.find(
        {"city": city, "asin": {"$in": peer_asins}, "price": {"$ne": None}},
        {"_id": 0, "asin": 1, "price": 1, "mrp": 1, "seller": 1, "delivery_date": 1, "listing_scraped_at": 1, "detail_scraped_at": 1},
    ).to_list(None)


async def _own_offer(asin: str, city: str) -> tuple[dict | None, dict | None]:
    product = await db.products.find_one({"asin": asin}, {"_id": 0, "images": 0})
    offer = await db.city_offers.find_one({"asin": asin, "city": city}, {"_id": 0, "listing": 0})
    return product, offer


@mcp.tool(
    "retailer_price_position",
    description=(
        "Retailer view: your price for one ASIN in a city against the cheapest and closest competitors in the same "
        "subcategory, with the gap in percent and how many are cheaper."
    ),
    annotations=READ_ONLY,
)
async def retailer_price_position(asin: str, city: str) -> dict:
    product, offer = await _own_offer(asin, city)
    if product is None or offer is None or offer.get("price") is None:
        return {"error": f"No priced offer for {asin} in {city}"}
    price = offer["price"]
    peers = sorted(await _peer_offers(product, asin, city), key=lambda o: abs(o["price"] - price))[:12]
    lowest = min((o["price"] for o in peers), default=None)
    cheaper = sum(o["price"] < price for o in peers)
    return {
        "asin": asin,
        "title": product.get("title"),
        "subcategory": _subcategory(product),
        "your_price": price,
        "your_mrp": offer.get("mrp"),
        "observed_at": _observed_at(offer).isoformat() if _observed_at(offer) else None,
        "lowest_competitor_price": lowest,
        "gap_vs_lowest_pct": round((price / lowest - 1) * 100, 2) if lowest else None,
        "competitors_cheaper": cheaper,
        "competitors_compared": len(peers),
        "position": "cheapest" if not peers or cheaper == 0 else ("priciest" if cheaper == len(peers) else "mid-range"),
    }


@mcp.tool(
    "retailer_competitor_prices",
    description="Retailer view: competitor offers in one city for the same subcategory as an ASIN, nearest in price first.",
    annotations=READ_ONLY,
)
async def retailer_competitor_prices(asin: str, city: str, limit: int = 12) -> dict:
    product, offer = await _own_offer(asin, city)
    if product is None or offer is None or offer.get("price") is None:
        return {"error": f"No priced offer for {asin} in {city}"}
    price = offer["price"]
    peers = sorted(await _peer_offers(product, asin, city), key=lambda o: abs(o["price"] - price))
    rows = [
        {
            "asin": o["asin"],
            "price": o["price"],
            "mrp": o.get("mrp"),
            "gap_vs_you_pct": round((o["price"] / price - 1) * 100, 2),
            "seller": o.get("seller"),
            "delivery_date": o.get("delivery_date"),
            "observed_at": _observed_at(o).isoformat() if _observed_at(o) else None,
        }
        for o in peers[: max(1, min(limit, MAX_LIMIT))]
    ]
    return {"asin": asin, "city": city, "your_price": price, "competitors": rows}


@mcp.tool(
    "retailer_price_trend",
    description="Retailer view: daily last recorded price for one ASIN in a city over the last N days (default 14).",
    annotations=READ_ONLY,
)
async def retailer_price_trend(asin: str, city: str, days: int = 14) -> dict:
    days = max(1, min(days, 90))
    last = await db.price_history.find_one(
        {"asin": asin, "city": city}, {"_id": 0, "scraped_at": 1}, sort=[("scraped_at", -1)]
    )
    if last is None:
        return {"asin": asin, "city": city, "days": days, "daily_last_price": {}}
    # Count the window back from the last stored observation, so the trend still shows after a gap in crawling.
    since = last["scraped_at"] - timedelta(days=days)
    rows = await db.price_history.find(
        {"asin": asin, "city": city, "scraped_at": {"$gte": since}},
        {"_id": 0, "price": 1, "scraped_at": 1},
    ).sort("scraped_at", 1).to_list(None)
    last_per_day: dict[str, float] = {}
    for r in rows:
        if r.get("price") is not None:
            last_per_day[r["scraped_at"].date().isoformat()] = r["price"]
    return {"asin": asin, "city": city, "days": days, "daily_last_price": last_per_day}


@mcp.tool(
    "retailer_price_gap_heatmap",
    description="Retailer view: for each product in a category, how far each city's price sits from its median across cities.",
    annotations=READ_ONLY,
)
async def retailer_price_gap_heatmap(category: str) -> dict:
    pattern = {"$regex": f"^{re.escape(category)}$", "$options": "i"}
    products = await db.products.find({"category_names": pattern}, {"_id": 0, "asin": 1, "title": 1}).to_list(None)
    titles = {p["asin"]: p.get("title") for p in products}
    offers = await db.city_offers.find(
        {"asin": {"$in": list(titles)}, "price": {"$ne": None}}, {"_id": 0, "asin": 1, "city": 1, "price": 1}
    ).to_list(None)
    by_asin: dict[str, dict[str, float]] = {}
    for o in offers:
        by_asin.setdefault(o["asin"], {})[o["city"]] = o["price"]
    rows = []
    for asin, prices in by_asin.items():
        if len(prices) < 2:
            continue
        mid = median(prices.values())
        rows.append({"asin": asin, "title": titles.get(asin), "median_price": mid,
                     "gap_pct_by_city": {c: round((p / mid - 1) * 100, 2) for c, p in prices.items()}})
    result = {"category": category, "products": rows}
    if not rows:
        result["note"] = "Needs the same product priced in at least two cities. Only one city is crawled so far."
    return result


@mcp.tool(
    "retailer_margin_scenarios",
    description=(
        "Retailer view: margin at match-lowest-competitor, hold, and raise-5% prices, from your landed cost. "
        "Needs unit_cost. Optional shipping, duty_pct, gst_pct (default 18) and marketplace fees_pct."
    ),
    annotations=READ_ONLY,
)
async def retailer_margin_scenarios(
    asin: str,
    city: str,
    unit_cost: float,
    shipping: float = 0.0,
    duty_pct: float = 0.0,
    gst_pct: float = 18.0,
    fees_pct: float = 0.0,
) -> dict:
    if fees_pct >= 100:
        return {"error": "Marketplace fees must be below 100%."}
    product, offer = await _own_offer(asin, city)
    if product is None or offer is None or offer.get("price") is None:
        return {"error": f"No priced offer for {asin} in {city}"}
    current = offer["price"]
    peer_prices = [o["price"] for o in await _peer_offers(product, asin, city)]
    lowest = min(peer_prices, default=None)
    landed = (unit_cost + shipping) * (1 + duty_pct / 100)
    options = [
        ("match_lowest_competitor", lowest or current),
        ("hold", current),
        (f"raise_{RAISE_PCT:g}_pct", current * (1 + RAISE_PCT / 100)),
    ]
    scenarios = []
    for name, price in options:
        m = _margin_at(price, landed, gst_pct, fees_pct)
        if price < m["breakeven_price"]:
            risk = "below_breakeven"
        elif m["margin_pct"] < THIN_MARGIN_PCT:
            risk = f"thin_margin_under_{THIN_MARGIN_PCT:g}_pct"
        else:
            risk = "ok"
        scenarios.append({"scenario": name, "price": round(price, 2), **m,
                          "competitors_cheaper": sum(p < price for p in peer_prices), "risk": risk})
    return {
        "asin": asin,
        "city": city,
        "landed_cost": round(landed, 2),
        "current_price": current,
        "scenarios": scenarios,
        "assumptions": {"gst_pct": gst_pct, "fees_pct": fees_pct, "duty_pct": duty_pct, "shipping": shipping},
        "note": "Price includes GST. Marketplace fees are a share of net revenue. Costs come from the user, not the crawl.",
    }


@mcp.tool(
    "retailer_review_sentiment",
    description=(
        "Retailer view: share of positive, negative and mixed reviews for one ASIN from star ratings, average rating, "
        "and the words most often used in positive and negative reviews."
    ),
    annotations=READ_ONLY,
)
async def retailer_review_sentiment(asin: str) -> dict:
    reviews = await db.reviews.find({"asin": asin}, {"_id": 0, "rating": 1, "title": 1, "body": 1}).to_list(None)
    rated = [r for r in reviews if r.get("rating") is not None]
    if not rated:
        return {"asin": asin, "reviews": 0, "note": "No stored reviews for this product."}

    def words(rows: list[dict]) -> list[str]:
        tokens = re.findall(r"[a-z]{3,}", " ".join(f"{r.get('title') or ''} {r.get('body') or ''}" for r in rows).lower())
        return [t for t in tokens if t not in STOPWORDS]

    positive = [r for r in rated if r["rating"] >= 4]
    negative = [r for r in rated if r["rating"] <= 2]
    total = len(rated)
    return {
        "asin": asin,
        "reviews": total,
        "positive_pct": round(len(positive) / total * 100, 1),
        "negative_pct": round(len(negative) / total * 100, 1),
        "mixed_pct": round((total - len(positive) - len(negative)) / total * 100, 1),
        "average_rating": round(sum(r["rating"] for r in rated) / total, 2),
        "top_words_positive": [w for w, _ in Counter(words(positive)).most_common(8)],
        "top_words_negative": [w for w, _ in Counter(words(negative)).most_common(8)],
        "note": "Labels come from star ratings (4-5 positive, 1-2 negative). Stored reviews are a sample of about 10 per product.",
    }


@mcp.tool(
    "retailer_market_basket",
    description=(
        "Retailer research: which products are often reviewed by the same customer (association rules with support, "
        "confidence and lift). Reviews are not purchases, so treat results as signals only."
    ),
    annotations=READ_ONLY,
)
async def retailer_market_basket(min_count: int = 2, top: int = 20) -> dict:
    reviews = await db.reviews.find({}, {"_id": 0, "asin": 1, "author": 1}).to_list(None)
    baskets: dict[str, set[str]] = {}
    for r in reviews:
        author = (r.get("author") or "").strip().lower()
        if author in GENERIC_AUTHORS or not r.get("asin"):
            continue
        baskets.setdefault(author, set()).add(r["asin"])
    sets = [frozenset(s) for s in baskets.values() if len(s) >= 2]
    n = len(sets)
    if n == 0:
        return {"baskets": 0, "rules": [], "note": "Not enough named reviewers who reviewed two or more products."}
    item_counts = Counter(i for s in sets for i in s)
    pair_counts = Counter(p for s in sets for p in combinations(sorted(s), 2))
    rules = []
    for (a, b), c in pair_counts.items():
        if c < min_count:
            continue
        for x, y in ((a, b), (b, a)):
            confidence = c / item_counts[x]
            rules.append({"if_reviewed": x, "also_reviewed": y, "pair_count": c, "support": round(c / n, 4),
                          "confidence": round(confidence, 4), "lift": round(confidence / (item_counts[y] / n), 3)})
    rules.sort(key=lambda r: (r["lift"], r["support"]), reverse=True)
    return {"baskets": n, "rules": rules[: max(1, min(top, MAX_LIMIT))],
            "note": "Research only: reviewer names are not unique customer IDs."}


@mcp.tool(
    "retailer_category_overview",
    description="Retailer view: product count, lowest, median, highest and average price, and average rating for a category in one city.",
    annotations=READ_ONLY,
)
async def retailer_category_overview(category: str, city: str) -> dict:
    pattern = {"$regex": f"^{re.escape(category)}$", "$options": "i"}
    products = {p["asin"]: p for p in await db.products.find({"category_names": pattern}, {"_id": 0, "asin": 1, "rating": 1}).to_list(None)}
    offers = await db.city_offers.find(
        {"city": city, "asin": {"$in": list(products)}, "price": {"$ne": None}}, {"_id": 0, "asin": 1, "price": 1}
    ).to_list(None)
    if not offers:
        return {"category": category, "city": city, "products": 0, "note": "No priced products in this category and city."}
    prices = [o["price"] for o in offers]
    ratings = [products[o["asin"]]["rating"] for o in offers if products[o["asin"]].get("rating") is not None]
    return {
        "category": category,
        "city": city,
        "products": len(prices),
        "lowest_price": min(prices),
        "median_price": median(prices),
        "highest_price": max(prices),
        "average_price": round(sum(prices) / len(prices), 2),
        "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
    }


@mcp.tool(
    "retailer_data_health",
    description="Retailer view: how much data exists, how many offers are stale or unpriced, and the last crawl runs.",
    annotations=READ_ONLY,
)
async def retailer_data_health() -> dict:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=STALE_HOURS)
    offers = await db.city_offers.find({}, {"_id": 0, "price": 1, "listing_scraped_at": 1, "detail_scraped_at": 1}).to_list(None)
    stale = sum(1 for o in offers if (_observed_at(o) is None or _observed_at(o) < cutoff))
    unpriced = sum(1 for o in offers if o.get("price") is None)
    runs = await db.crawl_runs.find({}, {"_id": 0, "started_at": 1, "finished_at": 1, "status": 1, "stats": 1}).sort("started_at", -1).limit(5).to_list(None)
    return {
        "products": await db.products.count_documents({}),
        "city_offers": len(offers),
        "price_points": await db.price_history.count_documents({}),
        "reviews": await db.reviews.count_documents({}),
        "stale_offers": stale,
        "stale_after_hours": STALE_HOURS,
        "unpriced_offers": unpriced,
        "recent_crawl_runs": [_clean(r) for r in runs],
    }


@mcp.tool(
    "list_cities",
    description="List the cities we support, with whether each one has priced data in the last crawl.",
    annotations=READ_ONLY,
)
async def list_cities() -> list[dict]:
    rows = []
    for c in load_cities():
        covered = await db.city_offers.find_one({"city": c["slug"]}, {"_id": 1}) is not None
        rows.append({"slug": c["slug"], "name": c["name"], "state": c.get("state"), "covered": covered})
    return rows


@mcp.tool(
    "customer_best_today",
    description=(
        "Customer picks: best-value products in a category for one city, from the last crawl, scored from rating, "
        "review count and price. Returns a random selection from the top candidates. Pass exclude_asins with products "
        "already shown to get new ones."
    ),
    annotations=READ_ONLY,
)
async def customer_best_today(
    category: str,
    city: str,
    limit: int = 5,
    exclude_asins: list[str] | None = None,
    pool_size: int = 30,
) -> dict:
    pattern = {"$regex": f"^{re.escape(category)}$", "$options": "i"}
    products = {p["asin"]: p for p in await db.products.find(
        {"$or": [{"breadcrumbs": pattern}, {"category_names": pattern}]}
    ).limit(CANDIDATE_CAP).to_list(None)}
    offers = await db.city_offers.find({
        "city": city,
        "asin": {"$in": list(products), "$nin": exclude_asins or []},
        "price": {"$ne": None},
    }).limit(CANDIDATE_CAP).to_list(None)
    if not offers:
        latest = await db.city_offers.find_one({"city": city}, {"_id": 0, "listing_scraped_at": 1}, sort=[("listing_scraped_at", -1)])
        return {
            "category": category,
            "city": city,
            "items": [],
            "latest_observed_at": latest["listing_scraped_at"].isoformat() if latest and latest.get("listing_scraped_at") else None,
            "note": "No priced products in this category and city in the last crawl.",
        }

    prices = [o["price"] for o in offers]
    low, high = min(prices), max(prices)
    scored = []
    for o in offers:
        p = products[o["asin"]]
        rating = p.get("rating") or 0
        count = p.get("rating_count") or 0
        value = 1 - (o["price"] - low) / (high - low) if high > low else 1.0
        score = 0.5 * (rating / 5) + 0.3 * min(math.log10(count + 1) / 4, 1) + 0.2 * value
        scored.append((score, o, p))
    scored.sort(key=lambda t: t[0], reverse=True)
    picks = random.sample(scored[: max(1, min(pool_size, MAX_LIMIT * 2))], k=min(max(1, min(limit, MAX_LIMIT)), len(scored)))
    items = []
    for score, o, p in picks:
        card = _card(o, p)
        card["score"] = round(score, 3)
        card["discount_pct"] = o.get("discount_pct")
        items.append(card)
    return {"category": category, "city": city, "items": items}


@mcp.tool(
    "customer_products",
    description=(
        "Every priced product in one city from the last crawl, for the shop. Pass category to list one category "
        "(All products), or query to search by title, brand, features and specs, most relevant first. "
        "Returns total and one page of items; use offset to page through."
    ),
    annotations=READ_ONLY,
)
async def customer_products(
    city: str,
    category: str | None = None,
    query: str | None = None,
    limit: int = 12,
    offset: int = 0,
) -> dict:
    product_filter: dict[str, Any] = {}
    if category:
        pattern = {"$regex": f"^{re.escape(category)}$", "$options": "i"}
        product_filter["$or"] = [{"breadcrumbs": pattern}, {"category_names": pattern}]
    products = {p["asin"]: p for p in await db.products.find(product_filter).limit(CANDIDATE_CAP).to_list(None)}
    offers = await db.city_offers.find({
        "city": city,
        "asin": {"$in": list(products)},
        "price": {"$ne": None},
    }).limit(CANDIDATE_CAP).to_list(None)

    # Every word must appear somewhere in the product text; when none does, accept products that match half the words.
    words = set(re.findall(r"[a-z0-9]+", (query or "").lower()))
    scored = []
    for o in offers:
        product = products[o["asin"]]
        title = (product.get("title") or "").lower()
        text = _searchable_text(product)
        hits = sum(1 for w in words if w in text)
        title_hits = sum(1 for w in words if w in title)
        scored.append((hits, title_hits, o, product))
    if words:
        strict = [row for row in scored if row[0] == len(words)]
        loose = [row for row in scored if row[0] and row[0] >= math.ceil(len(words) / 2)]
        scored = strict or loose

    scored.sort(key=lambda row: (row[1], row[0], row[3].get("rating") or 0, row[3].get("rating_count") or 0), reverse=True)
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    if not scored:
        latest = await db.city_offers.find_one({"city": city}, {"_id": 0, "listing_scraped_at": 1}, sort=[("listing_scraped_at", -1)])
        return {
            "city": city,
            "category": category,
            "query": query,
            "total": 0,
            "items": [],
            "latest_observed_at": latest["listing_scraped_at"].isoformat() if latest and latest.get("listing_scraped_at") else None,
            "note": "No priced products match in the last crawl for this city.",
        }
    page = [_card(o, p) for _, _, o, p in scored[offset: offset + limit]]
    return {"city": city, "category": category, "query": query, "total": len(scored), "offset": offset, "items": page}


if __name__ == "__main__":
    if os.getenv("MCP_TRANSPORT") == "http":
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = int(os.getenv("MCP_PORT", "8001"))
        mcp.run("streamable-http")
    else:
        mcp.run()
