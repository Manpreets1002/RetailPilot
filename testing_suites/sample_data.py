"""Sample data for the test suites.

seed_mongo() fills a MongoDB database with a small, known catalogue.
dashboard_dataset() returns the same data in the shape analytics.load() produces, so the dashboard
can be tested without a running database.
"""

from datetime import UTC, datetime, timedelta

LAPTOP_NODE = "1375424031"
PHONE_NODE = "1788888031"
LAPTOP_A = "B0TESTA001"  # Acer laptop: priced in Delhi, Mumbai and Chennai (the Chennai offer is stale)
LAPTOP_B = "B0TESTA002"  # Dell laptop: priced in Delhi, unpriced in Mumbai
CASE = "B0TESTA003"      # Phone case: another subcategory, priced in Delhi


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def ago(days: float = 0, hours: float = 0) -> datetime:
    return utc_now() - timedelta(days=days, hours=hours)


def products() -> list[dict]:
    return [
        {"asin": LAPTOP_A, "title": "Acer Aspire 5 Laptop 15.6 inch", "brand": "Acer", "rating": 4.5,
         "rating_count": 2000, "rating_histogram": {"5": 60, "4": 20, "3": 8, "2": 5, "1": 7},
         "feature_bullets": ["Up to 10 hours battery life", "Full HD display"],
         "specifications": {"Battery Life": "Up to 10 hours", "Colour": "Black"}, "overview": {"Brand": "Acer"},
         "description": "Everyday laptop with a long battery", "category_names": ["Electronics", "Laptops"],
         "breadcrumbs": ["Electronics", "Laptops"], "category_nodes": [LAPTOP_NODE]},
        {"asin": LAPTOP_B, "title": "Dell Inspiron 3520 Laptop", "brand": "Dell", "rating": 3.8, "rating_count": 150,
         "rating_histogram": {"5": 30, "4": 40, "3": 40, "2": 20, "1": 20}, "feature_bullets": ["Intel Core i5 processor"],
         "specifications": {"Colour": "Silver"}, "overview": {}, "description": "Office laptop",
         "category_names": ["Electronics", "Laptops"], "breadcrumbs": ["Electronics", "Laptops"],
         "category_nodes": [LAPTOP_NODE]},
        {"asin": CASE, "title": "Slim Phone Case", "brand": "Generic", "rating": 4.0, "rating_count": 50,
         "rating_histogram": {}, "feature_bullets": ["Shockproof"], "specifications": {}, "overview": {},
         "description": "Case", "category_names": ["Electronics", "Mobile Accessories"],
         "breadcrumbs": ["Electronics", "Mobile Accessories"], "category_nodes": [PHONE_NODE]},
    ]


def offers() -> list[dict]:
    return [
        {"asin": LAPTOP_A, "city": "delhi", "city_name": "New Delhi", "pincode": "110001", "price": 50000.0,
         "mrp": 60000.0, "discount_pct": 17, "availability": "In stock", "delivery_date": "Tomorrow",
         "seller": "Acer Store", "listing_scraped_at": utc_now(), "detail_scraped_at": utc_now()},
        {"asin": LAPTOP_B, "city": "delhi", "city_name": "New Delhi", "pincode": "110001", "price": 42000.0,
         "mrp": 45000.0, "discount_pct": 7, "availability": "In stock", "delivery_date": "Friday",
         "seller": "Dell Retail", "listing_scraped_at": utc_now(), "detail_scraped_at": None},
        {"asin": CASE, "city": "delhi", "city_name": "New Delhi", "pincode": "110001", "price": 800.0,
         "mrp": 999.0, "discount_pct": 20, "availability": "In stock", "delivery_date": "Tomorrow",
         "seller": "Generic Shop", "listing_scraped_at": utc_now(), "detail_scraped_at": None},
        {"asin": LAPTOP_A, "city": "mumbai", "city_name": "Mumbai", "pincode": "400001", "price": 52000.0,
         "mrp": 60000.0, "discount_pct": 13, "availability": "In stock", "delivery_date": "Tomorrow",
         "seller": "Acer Store", "listing_scraped_at": utc_now(), "detail_scraped_at": None},
        {"asin": LAPTOP_B, "city": "mumbai", "city_name": "Mumbai", "pincode": "400001", "price": None,
         "mrp": None, "discount_pct": None, "availability": "Unavailable", "delivery_date": None,
         "seller": None, "listing_scraped_at": utc_now(), "detail_scraped_at": None},
        {"asin": LAPTOP_A, "city": "chennai", "city_name": "Chennai", "pincode": "600001", "price": 49000.0,
         "mrp": 60000.0, "discount_pct": 18, "availability": "In stock", "delivery_date": "Friday",
         "seller": "Acer Store", "listing_scraped_at": ago(days=5), "detail_scraped_at": ago(days=5)},
    ]


def price_history() -> list[dict]:
    return [
        {"asin": LAPTOP_A, "city": "delhi", "pincode": "110001", "price": 49500.0, "mrp": 60000.0,
         "discount_pct": 17, "source": "listing", "scraped_at": ago(days=2)},
        {"asin": LAPTOP_A, "city": "delhi", "pincode": "110001", "price": 50500.0, "mrp": 60000.0,
         "discount_pct": 16, "source": "listing", "scraped_at": ago(days=1)},
        {"asin": LAPTOP_A, "city": "delhi", "pincode": "110001", "price": 50000.0, "mrp": 60000.0,
         "discount_pct": 17, "source": "listing", "scraped_at": utc_now()},
        {"asin": LAPTOP_A, "city": "mumbai", "pincode": "400001", "price": 52000.0, "mrp": 60000.0,
         "discount_pct": 13, "source": "listing", "scraped_at": utc_now()},
    ]


def reviews() -> list[dict]:
    rows = [
        (LAPTOP_A, "Ravi", 5, "Excellent battery", "Battery lasts a full day, great display"),
        (LAPTOP_A, "Asha", 5, "Love it", "Excellent build and great battery"),
        (LAPTOP_A, "Amazon Customer", 4, "Good value", "Good laptop for office work"),
        (LAPTOP_A, "Priya", 2, "Heats up", "Gets hot and slow after updates, poor fan"),
        (LAPTOP_A, "Karan", 1, "Disappointed", "Bad screen and poor battery"),
        (LAPTOP_B, "Ravi", 4, "Solid", "Good performance for the price"),
        (LAPTOP_B, "Asha", 3, "Average", "Okay laptop, average speaker"),
    ]
    return [
        {"review_id": f"R{i:03d}", "asin": asin, "author": author, "rating": rating, "title": title,
         "body": body, "date": "2026-10-01", "verified_purchase": True, "helpful_votes": 5 - i % 5}
        for i, (asin, author, rating, title, body) in enumerate(rows)
    ]


def synthetic_reviews(count: int) -> list[dict]:
    """Reviews with clear positive and negative wording, enough to train the sentiment model."""
    bodies = {
        5: "excellent battery great display love it",
        4: "good performance and great value",
        3: "okay laptop average speaker",
        2: "slow and heats up poor fan",
        1: "bad screen poor battery disappointed",
    }
    ratings = [5, 4, 1, 2, 3]
    return [
        {"review_id": f"S{i:04d}", "asin": LAPTOP_B, "author": f"Reviewer{i}", "rating": ratings[i % 5],
         "title": "", "body": bodies[ratings[i % 5]], "date": "2026-10-02", "verified_purchase": False,
         "helpful_votes": 0}
        for i in range(count)
    ]


CATEGORIES = [
    {"name": "Laptops", "node": LAPTOP_NODE},
    {"name": "Mobile Accessories", "node": PHONE_NODE},
]

CRAWL_RUNS = [
    {"started_at": ago(hours=1), "finished_at": ago(hours=0.5), "status": "finished",
     "stats": {"cities": 3, "products": 3}},
]


def seed_mongo(db) -> None:
    db.products.insert_many([dict(p) for p in products()])
    db.city_offers.insert_many([dict(o) for o in offers()])
    db.price_history.insert_many([dict(h) for h in price_history()])
    db.reviews.insert_many([dict(r) for r in reviews()])
    db.categories.insert_many([dict(c) for c in CATEGORIES])
    db.crawl_runs.insert_many([dict(r) for r in CRAWL_RUNS])


def dashboard_dataset(extra_reviews: int = 60) -> dict:
    """The same shape analytics._load_raw() returns, built in memory."""
    by_asin = {p["asin"]: p for p in products()}
    rows = []
    for o in offers():
        if o["price"] is None:
            continue
        p = by_asin[o["asin"]]
        observed = max(o["listing_scraped_at"], o["detail_scraped_at"]) if o["detail_scraped_at"] else o["listing_scraped_at"]
        rows.append({
            **o,
            "title": p["title"],
            "brand": p["brand"],
            "category": p["category_names"][1],
            "rating": p["rating"],
            "rating_count": p["rating_count"],
            "url": None,
            "observed_at": observed,
        })
    history = [{"asin": h["asin"], "city": h["city"], "price": h["price"], "scraped_at": h["scraped_at"]}
               for h in price_history()]
    return {
        "products": by_asin,
        "offers": rows,
        "history": history,
        "reviews": reviews() + synthetic_reviews(extra_reviews),
        "runs": CRAWL_RUNS,
    }
