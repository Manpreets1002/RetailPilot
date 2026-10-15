"""MongoDB storage.

Collections:
  cities          the cities from cities.json, with the location Amazon confirmed
  categories      one doc per crawled subcategory (browse node)
  products        one doc per ASIN: data that is the same in every city
                  (title, brand, specs, images, rating, reviews summary)
  city_offers     one doc per (asin, city): price, MRP, discount, stock, seller,
                  delivery, search position... as seen from that city's pincode
  reviews         one doc per review, upserted by review_id
  price_history   one doc per price observation, tagged with city + pincode
  crawl_runs      one doc per crawler run with counters
"""

from datetime import UTC, datetime

from parsers import split_city_fields
from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne

# Search-card fields that are the same everywhere; the rest go to city_offers.
LISTING_COMMON = ("title", "title_differentiators", "image", "url", "rating", "rating_count")


def now() -> datetime:
    return datetime.now(UTC)


class Store:
    def __init__(self, uri: str, db_name: str):
        self.client = MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
        self.db = self.client[db_name]
        self.client.admin.command("ping")
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        self.db.products.create_index("asin", unique=True)
        self.db.products.create_index([("category_nodes", ASCENDING)])
        self.db.products.create_index([("brand", ASCENDING)])
        self.db.products.create_index([("rating", DESCENDING)])
        self.db.city_offers.create_index([("asin", ASCENDING), ("city", ASCENDING)], unique=True)
        self.db.city_offers.create_index([("city", ASCENDING), ("price", ASCENDING)])
        self.db.city_offers.create_index([("city", ASCENDING), ("category_nodes", ASCENDING)])
        self.db.reviews.create_index("review_id", unique=True)
        self.db.reviews.create_index("asin")
        self.db.price_history.create_index([("asin", ASCENDING), ("city", ASCENDING), ("scraped_at", DESCENDING)])
        self.db.categories.create_index("node", unique=True)
        self.db.cities.create_index("slug", unique=True)

    # ---------------------------------------------------------------- writes

    def upsert_city(self, city: dict, confirmed: dict | None) -> None:
        self.db.cities.update_one(
            {"slug": city["slug"]},
            {"$set": {**city, "amazon_location": confirmed, "updated_at": now()}},
            upsert=True,
        )

    def upsert_category(self, node: str, name: str, parent: str | None) -> None:
        self.db.categories.update_one(
            {"node": node},
            {"$set": {"name": name, "parent_node": parent, "updated_at": now()}},
            upsert=True,
        )

    def upsert_listings(self, items: list[dict], category: dict, city: dict, run_id) -> int:
        """Save search-card data for one city. Returns how many ASINs were new to the products collection."""
        if not items:
            return 0
        ts = now()
        product_ops, offer_ops = [], []
        for it in items:
            asin = it["asin"]
            common = {k: it.get(k) for k in LISTING_COMMON if it.get(k) is not None}
            product_ops.append(
                UpdateOne(
                    {"asin": asin},
                    {
                        "$set": {**common, "last_seen_at": ts, "last_run_id": run_id},
                        "$addToSet": {"category_nodes": category["node"], "category_names": category["name"]},
                        "$setOnInsert": {"asin": asin, "first_seen_at": ts, "detail_scraped_at": None},
                    },
                    upsert=True,
                )
            )
            listing = {k: v for k, v in it.items() if k not in LISTING_COMMON and k != "asin"}
            offer_ops.append(
                UpdateOne(
                    {"asin": asin, "city": city["slug"]},
                    {
                        "$set": {
                            "pincode": city["pincode"],
                            "city_name": city["name"],
                            "price": it.get("price"),
                            "mrp": it.get("mrp"),
                            "discount_pct": it.get("discount_pct"),
                            "listing": listing,
                            "listing_scraped_at": ts,
                            "last_run_id": run_id,
                        },
                        "$addToSet": {"category_nodes": category["node"]},
                        "$setOnInsert": {"first_seen_at": ts, "detail_scraped_at": None},
                    },
                    upsert=True,
                )
            )
        res = self.db.products.bulk_write(product_ops, ordered=False)
        self.db.city_offers.bulk_write(offer_ops, ordered=False)
        self._record_prices(items, city, source="listing", run_id=run_id)
        return res.upserted_count

    def save_product_detail(self, detail: dict, city: dict, run_id) -> None:
        ts = now()
        reviews = detail.pop("reviews", [])
        common, city_part = split_city_fields(detail)

        doc = {k: v for k, v in common.items() if v not in (None, [], {})}
        doc |= {"detail_scraped_at": ts, "last_run_id": run_id, "review_sample_count": len(reviews)}
        self.db.products.update_one({"asin": detail["asin"]}, {"$set": doc}, upsert=True)

        self.db.city_offers.update_one(
            {"asin": detail["asin"], "city": city["slug"]},
            {
                "$set": {
                    **city_part,
                    "pincode": city["pincode"],
                    "city_name": city["name"],
                    "detail_scraped_at": ts,
                    "last_run_id": run_id,
                },
                "$setOnInsert": {"first_seen_at": ts},
            },
            upsert=True,
        )
        self._record_prices([detail], city, source="detail", run_id=run_id)

        if reviews:
            self.db.reviews.bulk_write(
                [
                    UpdateOne(
                        {"review_id": r["review_id"]},
                        {"$set": {**r, "asin": detail["asin"], "scraped_at": ts}},
                        upsert=True,
                    )
                    for r in reviews
                ],
                ordered=False,
            )

    def _record_prices(self, items: list[dict], city: dict, source: str, run_id) -> None:
        ts = now()
        rows = [
            {
                "asin": it["asin"],
                "city": city["slug"],
                "pincode": city["pincode"],
                "price": it.get("price"),
                "mrp": it.get("mrp"),
                "discount_pct": it.get("discount_pct"),
                "source": source,
                "run_id": run_id,
                "scraped_at": ts,
            }
            for it in items
            if it.get("price") is not None
        ]
        if rows:
            self.db.price_history.insert_many(rows)

    # ---------------------------------------------------------------- runs

    def start_run(self, params: dict):
        return self.db.crawl_runs.insert_one({"started_at": now(), "status": "running", "params": params}).inserted_id

    def finish_run(self, run_id, status: str, stats: dict) -> None:
        self.db.crawl_runs.update_one(
            {"_id": run_id}, {"$set": {"finished_at": now(), "status": status, "stats": stats}}
        )

    # ---------------------------------------------------------------- reads

    def needs_detail(self, asins: list[str], city: str, max_age_hours: float) -> list[str]:
        """ASINs whose product page was never scraped for this city, or is older than max_age_hours."""
        if max_age_hours <= 0:
            return asins
        cutoff = now().timestamp() - max_age_hours * 3600
        fresh = {
            d["asin"]
            for d in self.db.city_offers.find(
                {"asin": {"$in": asins}, "city": city, "detail_scraped_at": {"$ne": None}},
                {"asin": 1, "detail_scraped_at": 1},
            )
            if d["detail_scraped_at"].timestamp() >= cutoff
        }
        return [a for a in asins if a not in fresh]
