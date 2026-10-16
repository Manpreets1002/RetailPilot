"""Retailer dashboard calculations, ported from the gaurvit-retailer-dashboard-base branch (Streamlit) to plain Python.

Reads the crawl data in MongoDB directly, as the branch did.
"""

import math
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import combinations
from statistics import median

import numpy as np
from django.conf import settings
from pymongo import MongoClient
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import make_pipeline

PEER_COUNT = 12
THIN_MARGIN_PCT = 10.0
RAISE_PCT = 5.0
DEFAULT_GST_PCT = 18.0
POS_T, NEG_T = 0.65, 0.35
GENERIC_AUTHORS = {"", "amazon customer", "customer"}
HISTORY_DAYS = 90
CACHE_SECONDS = 600

_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, build):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = build()
    _cache[key] = (time.time(), value)
    return value


def subcategory(names) -> str:
    for name in names or []:
        if name and name.lower() != "electronics":
            return name
    return "Other"


def is_stale(ts) -> bool:
    return ts is not None and ts < datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=settings.STALE_HOURS)


def observed_at(offer: dict):
    stamps = [t for t in (offer.get("listing_scraped_at"), offer.get("detail_scraped_at")) if t]
    return max(stamps) if stamps else None


def margin_at(price: float, landed: float, gst_pct: float, fees_pct: float) -> dict:
    net = price / (1 + gst_pct / 100)
    fees = net * fees_pct / 100
    contribution = net - landed - fees
    return {
        "price": price,
        "net_revenue": net,
        "contribution": contribution,
        "margin_pct": contribution / net * 100 if net else 0.0,
        "breakeven_price": landed / (1 - fees_pct / 100) * (1 + gst_pct / 100),
    }


def margin_scenarios(current: float, lowest_peer, landed: float, gst_pct: float, fees_pct: float, peer_prices: list[float]) -> list[dict]:
    options = [
        ("Match lowest peer", lowest_peer or current),
        ("Hold price", current),
        (f"Raise {RAISE_PCT:g}%", current * (1 + RAISE_PCT / 100)),
    ]
    rows = []
    for name, price in options:
        m = margin_at(price, landed, gst_pct, fees_pct)
        if price < m["breakeven_price"]:
            risk = "Below breakeven"
        elif m["margin_pct"] < THIN_MARGIN_PCT:
            risk = f"Thin margin (<{THIN_MARGIN_PCT:g}%)"
        else:
            risk = "OK"
        rows.append({"scenario": name, **m, "peers_cheaper": sum(p < price for p in peer_prices), "risk": risk})
    return rows


def _load_raw() -> dict:
    client = MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=3000, tz_aware=False)
    try:
        db = client[settings.MONGO_DB]
        products = {p["asin"]: p for p in db.products.find({}, {"_id": 0, "images": 0, "description": 0})}
        offers = list(db.city_offers.find({"price": {"$ne": None}}, {"_id": 0}))
        since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=HISTORY_DAYS)
        history = list(db.price_history.find({"scraped_at": {"$gte": since}}, {"_id": 0, "asin": 1, "city": 1, "price": 1, "scraped_at": 1}))
        reviews = list(db.reviews.find({}, {"_id": 0, "asin": 1, "author": 1, "rating": 1, "title": 1, "body": 1, "date": 1}))
        runs = list(db.crawl_runs.find({}, {"_id": 0}).sort("started_at", -1).limit(5))
    finally:
        client.close()

    rows = []
    for o in offers:
        p = products.get(o["asin"], {})
        rows.append({
            **o,
            "title": p.get("title") or o["asin"],
            "brand": p.get("brand"),
            "category": subcategory(p.get("category_names")),
            "rating": p.get("rating"),
            "rating_count": p.get("rating_count"),
            "url": p.get("url"),
            "observed_at": observed_at(o),
        })
    return {"products": products, "offers": rows, "history": history, "reviews": reviews, "runs": runs}


def load() -> dict:
    return _cached("raw", _load_raw)


def cities(data: dict) -> list[dict]:
    seen = {}
    for o in data["offers"]:
        seen.setdefault(o["city"], o.get("city_name") or o["city"])
    return [{"slug": slug, "name": name} for slug, name in sorted(seen.items(), key=lambda kv: kv[1])]


def categories_in(data: dict, city: str) -> list[str]:
    return sorted({o["category"] for o in data["offers"] if o["city"] == city})


def products_in(data: dict, city: str, category: str | None) -> list[dict]:
    return sorted(
        [o for o in data["offers"] if o["city"] == city and (category in (None, "All categories") or o["category"] == category)],
        key=lambda o: (o["title"] or "").lower(),
    )


def peers_of(data: dict, city: str, sel: dict, n: int = PEER_COUNT) -> list[dict]:
    peers = [o for o in data["offers"] if o["city"] == city and o["category"] == sel["category"] and o["asin"] != sel["asin"]]
    return sorted(peers, key=lambda o: abs(o["price"] - sel["price"]))[:n]


def price_history(data: dict, asin: str, city: str, days: int) -> list[tuple[str, float]]:
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    per_day: dict[str, float] = {}
    for h in sorted((h for h in data["history"] if h["asin"] == asin and h["city"] == city and h["scraped_at"] >= since), key=lambda h: h["scraped_at"]):
        if h.get("price") is not None:
            per_day[h["scraped_at"].date().isoformat()] = h["price"]
    return sorted(per_day.items())


def category_overview(data: dict, city: str) -> list[dict]:
    groups: dict[str, list[float]] = {}
    for o in data["offers"]:
        if o["city"] == city:
            groups.setdefault(o["category"], []).append(o["price"])
    return [
        {"category": name, "products": len(prices), "avg_price": round(sum(prices) / len(prices))}
        for name, prices in sorted(groups.items())
    ]


def gap_matrix(data: dict, category: str) -> tuple[list[str], list[str], list[list[float | None]]]:
    by_product: dict[str, dict[str, float]] = {}
    titles: dict[str, str] = {}
    for o in data["offers"]:
        if o["category"] != category:
            continue
        by_product.setdefault(o["asin"], {})[o["city_name"] or o["city"]] = o["price"]
        titles[o["asin"]] = o["title"]
    cities_ = sorted({c for prices in by_product.values() for c in prices})
    labels, grid = [], []
    for asin, prices in by_product.items():
        mid = median(prices.values())
        labels.append(f"{(titles[asin] or asin)[:40]} · {asin}")
        grid.append([round((prices[c] / mid - 1) * 100, 1) if c in prices else None for c in cities_])
    return cities_, labels, grid


# ----------------------------------------------------------------- sentiment (ML, same approach as the branch)

def _review_text(r: dict) -> str:
    return f"{r.get('title') or ''} {r.get('body') or ''}".strip()


def _build_sentiment(reviews: list[dict]) -> dict | None:
    rated = [r for r in reviews if r.get("rating") is not None and _review_text(r)]
    if not rated:
        return None
    train = [r for r in rated if r["rating"] != 3]
    texts = [_review_text(r) for r in train]
    y = np.array([1 if r["rating"] >= 4 else 0 for r in train], dtype=int)
    model = make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, stop_words="english"),
        LogisticRegression(max_iter=1000, class_weight="balanced"),
    )
    accuracy = None
    counts = np.bincount(y, minlength=2) if len(y) else np.zeros(2, dtype=int)
    folds = int(min(5, counts.min())) if (counts > 0).all() else 0
    if folds >= 2 and len(texts) >= 50:
        accuracy = float(cross_val_score(model, texts, y, cv=folds).mean())
    if len(set(y.tolist())) < 2:
        return None
    model.fit(texts, y)
    p_pos = model.predict_proba([_review_text(r) for r in rated])[:, 1]
    scored = []
    for r, p in zip(rated, p_pos):
        label = "Positive" if p >= POS_T else "Negative" if p <= NEG_T else "Mixed"
        scored.append({**r, "p_positive": float(p), "sentiment": label})
    names = np.array(model.steps[0][1].get_feature_names_out())
    order = np.argsort(model.steps[1][1].coef_[0])
    return {
        "reviews": scored,
        "accuracy": accuracy,
        "pos_terms": names[order[::-1][:12]].tolist(),
        "neg_terms": names[order[:12]].tolist(),
    }


def sentiment(data: dict) -> dict | None:
    return _cached("sentiment", lambda: _build_sentiment(data["reviews"]))


def product_sentiment(sent: dict | None, asin: str) -> dict | None:
    if not sent:
        return None
    mine = [r for r in sent["reviews"] if r["asin"] == asin]
    if not mine:
        return None
    total = len(mine)
    return {
        "reviews": total,
        "positive_pct": round(sum(r["sentiment"] == "Positive" for r in mine) / total * 100, 1),
        "negative_pct": round(sum(r["sentiment"] == "Negative" for r in mine) / total * 100, 1),
        "avg_rating": round(sum(r.get("rating") or 0 for r in mine) / total, 2),
        "samples": sorted(mine, key=lambda r: r.get("date") or "", reverse=True)[:6],
    }


# ----------------------------------------------------------------- market basket (research)

def basket_rules(data: dict, min_count: int = 2, top: int = 30) -> list[dict]:
    def build():
        baskets: dict[str, set[str]] = {}
        for r in data["reviews"]:
            author = (r.get("author") or "").strip().lower()
            if author in GENERIC_AUTHORS or not r.get("asin"):
                continue
            baskets.setdefault(author, set()).add(r["asin"])
        sets = [frozenset(s) for s in baskets.values() if len(s) >= 2]
        n = len(sets)
        if n == 0:
            return []
        item_counts = Counter(i for s in sets for i in s)
        pair_counts = Counter(p for s in sets for p in combinations(sorted(s), 2))
        rows = []
        for (a, b), c in pair_counts.items():
            if c < min_count:
                continue
            for x, y in ((a, b), (b, a)):
                conf = c / item_counts[x]
                rows.append({
                    "antecedent": data["products"].get(x, {}).get("title") or x,
                    "consequent": data["products"].get(y, {}).get("title") or y,
                    "pair_count": c,
                    "support": round(c / n, 4),
                    "confidence": round(conf, 3),
                    "lift": round(conf / (item_counts[y] / n), 3),
                })
        return sorted(rows, key=lambda r: (r["lift"], r["support"]), reverse=True)[:top]

    return _cached(f"basket:{min_count}:{top}", build)


def health(data: dict) -> dict:
    total = len(data["offers"])
    stale = sum(is_stale(o["observed_at"]) for o in data["offers"])
    return {
        "products": len(data["products"]),
        "offers": total,
        "history_points": len(data["history"]),
        "reviews": len(data["reviews"]),
        "stale": stale,
        "stale_hours": settings.STALE_HOURS,
        "runs": data["runs"],
    }
