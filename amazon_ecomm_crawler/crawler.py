"""Amazon Electronics category crawler (POC), city-wise.

Flow:
  1. Load the cities from cities.json (10 tier-1 cities, each with a pincode).
  2. Discover Electronics subcategories from the search page's Department filter.
  3. For each city: open a session, set that city's delivery pincode, then for each
     subcategory walk the search result pages and open each product page.
  4. Save to MongoDB: city-independent data in `products`, city-specific price,
     stock, seller and delivery in `city_offers`, and a city-tagged `price_history` row.

Usage:
  python crawler.py --list-cities
  python crawler.py --list-categories
  python crawler.py                                         # every city in cities.json
  python crawler.py --city mumbai                           # one city (slug, name or pincode)
  python crawler.py --city delhi bengaluru 600001 --categories 1389401031 --pages 3 --products 30
  python crawler.py --city mumbai --no-details              # listing data only (fast)
  python crawler.py --enqueue --city mumbai delhi           # queue jobs for worker.py instead of crawling here
  python crawler.py --enqueue --if-idle                     # same, but skip if a run is still queued/running
"""

import argparse
import logging
import sys
from urllib.parse import quote

from config import load_cities, select_cities, settings
from http_client import Fetcher, LocationError
from parsers import parse_departments, parse_product_page, parse_search_page
from storage import Store

log = logging.getLogger("crawler")


# The Electronics subcategories (amazon.in browse nodes) every run crawls. Left out on purpose:
# Accessories, Car & Vehicle Electronics, Telephones & Accessories, Warranties,
# eBook Readers & Accessories, General Purpose Batteries & Battery Chargers.
# `--list-categories` shows Amazon's live list; `--categories NODE...` crawls others for one run.
SUBCATEGORIES = [
    {"name": "Cameras & Photography", "node": "1388977031"},
    {"name": "Computers & Accessories", "node": "1458204031"},
    {"name": "GPS & Accessories", "node": "1389315031"},
    {"name": "Home Audio", "node": "1389335031"},
    {"name": "Home Theater, TV & Video", "node": "1389375031"},
    {"name": "Mobiles & Accessories", "node": "1389401031"},
    {"name": "Portable Media Players", "node": "1389433031"},

    {"name": "Tablets", "node": "1375458031"},
    {"name": "Wearable Technology", "node": "11599648031"},
    {"name": "Headphones, Earbuds & Accessories", "node": "29561348031"},
    {"name": "Power Accessories", "node": "1388973031"},
]


def discover_categories(fetcher: Fetcher) -> list[dict]:
    """Amazon's live Electronics subcategories (the search page's "Department" filter)."""
    html = fetcher.get(f"/s?k={settings.search_index}&i={settings.search_index}")
    cats = parse_departments(html) if html else []
    # Drop the root node itself if it shows up as a filter entry.
    return [c for c in cats if c["node"] != settings.root_node]


def search_url(node: str, page: int) -> str:
    rh = quote(f"n:{node}", safe="")
    # fs=true forces the product grid; without it page 1 is a browse storefront.
    return f"/s?i={settings.search_index}&rh={rh}&fs=true&page={page}"


def new_stats() -> dict:
    return {"listings": 0, "new_products": 0, "details": 0, "detail_failures": 0, "reviews": 0,
            "cities_done": [], "cities_failed": []}


def crawl_listing_pages(fetcher: Fetcher, store: Store, city: dict, cat: dict, args, run_id, stats: dict,
                        first_page: int = 1, last_page: int = 0, already_seen: int = 0) -> tuple[list[str], bool]:
    """Save search pages first_page..last_page (0 = until the end) for one city and category.

    Returns (ASINs saved, whether the category has more pages after the last one fetched).
    `already_seen` counts products saved by earlier chunks, for the --products limit.
    """
    seen: list[str] = []
    page = first_page
    while True:
        if args.pages and page > args.pages:
            return seen, False
        if last_page and page > last_page:
            return seen, True
        html = fetcher.get(search_url(cat["node"], page))
        if not html:
            return seen, False
        result = parse_search_page(html)
        items = [p for p in result["products"] if p["asin"] not in seen]
        if not items:
            log.info("No products on page %d, stopping category", page)
            return seen, False

        if args.products:
            items = items[: max(0, args.products - already_seen - len(seen))]
        new = store.upsert_listings(items, cat, city, run_id)
        seen.extend(p["asin"] for p in items)
        stats["listings"] += len(items)
        stats["new_products"] += new
        log.info("[%s] %s page %d/%s: %d products (%d new)", city["slug"], cat["name"], page,
                 result["total_pages"] or "?", len(items), new)

        if args.products and already_seen + len(seen) >= args.products:
            return seen, False
        if not result["next_page"]:
            return seen, False
        page += 1


def detail_todo(store: Store, asins: list[str], city: dict, args) -> list[str]:
    """Which product pages to fetch: per city, or once per product (see CRAWL_DETAIL_MODE)."""
    if settings.crawl_detail_mode == "once":
        return store.needs_detail_any(asins, args.refresh_hours)
    return store.needs_detail(asins, city["slug"], args.refresh_hours)


def crawl_details(fetcher: Fetcher, store: Store, city: dict, asins: list[str], args, run_id, stats: dict) -> None:
    todo = detail_todo(store, asins, city, args)
    log.info("[%s] Fetching %d product pages (%d skipped as fresh)", city["slug"], len(todo), len(asins) - len(todo))
    for i, asin in enumerate(todo, 1):
        html = fetcher.get(f"/dp/{asin}")
        if not html:
            stats["detail_failures"] += 1
            continue
        try:
            detail = parse_product_page(html, asin, settings.max_reviews_per_product)
        except Exception:
            log.exception("Parse failed for %s", asin)
            stats["detail_failures"] += 1
            continue
        stats["reviews"] += len(detail.get("reviews", []))
        store.save_product_detail(detail, city, run_id)
        stats["details"] += 1
        log.info(
            "[%s %d/%d] %s | %s | ₹%s (MRP ₹%s, %s%% off) | %s★ (%s) | %s",
            city["slug"], i, len(todo), asin, (detail.get("title") or "")[:40],
            detail.get("price"), detail.get("mrp"), detail.get("discount_pct"),
            detail.get("rating"), detail.get("rating_count"), detail.get("delivery_date") or detail.get("delivery"),
        )


def crawl_category(fetcher: Fetcher, store: Store, city: dict, cat: dict, args, run_id, stats: dict) -> None:
    log.info("=== [%s] Category %s (%s)", city["slug"], cat["name"], cat["node"])
    store.upsert_category(cat["node"], cat["name"], settings.root_node)
    seen, _ = crawl_listing_pages(fetcher, store, city, cat, args, run_id, stats)
    if not args.no_details:
        crawl_details(fetcher, store, city, seen, args, run_id, stats)


def crawl_city(city: dict, cats: list[dict], store: Store, args, run_id, stats: dict) -> None:
    log.info("##### City %s (%s, pincode %s)", city["name"], city.get("state", ""), city["pincode"])
    fetcher = Fetcher(settings, city=city)
    try:
        fetcher.warm_up()
    except LocationError as exc:
        log.error("Skipping %s: %s", city["name"], exc)
        stats["cities_failed"].append(city["slug"])
        return
    store.upsert_city(city, fetcher.location)
    try:
        for cat in cats:
            crawl_category(fetcher, store, city, cat, args, run_id, stats)
    finally:
        for k, v in fetcher.stats.items():
            stats[k] = stats.get(k, 0) + v
    stats["cities_done"].append(city["slug"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Crawl Amazon Electronics into MongoDB, city by city")
    ap.add_argument("--city", nargs="*", metavar="CITY", default=settings.crawl_cities.replace(",", " ").split(),
                    help="cities to crawl, by slug/name/pincode from cities.json (default: CRAWL_CITIES, else all)")
    ap.add_argument("--list-cities", action="store_true", help="print the cities in cities.json and exit")
    ap.add_argument("--list-categories", action="store_true", help="print subcategories and exit")
    ap.add_argument("--categories", nargs="*",
                    help="browse node IDs to crawl (default: the 12 in SUBCATEGORIES)")
    ap.add_argument("--pages", type=int, default=settings.max_pages_per_category, help="max pages per category, 0 = all")
    ap.add_argument("--products", type=int, default=settings.max_products_per_category, help="max products per category, 0 = all")
    ap.add_argument("--no-details", action="store_true", help="skip product detail pages")
    ap.add_argument("--refresh-hours", type=float, default=settings.crawl_refresh_hours,
                    help="re-scrape details older than this (default: CRAWL_REFRESH_HOURS); 0 = always")
    ap.add_argument("--enqueue", action="store_true",
                    help="put one job per (city, category) on the Redis queue for worker.py instead of crawling here")
    ap.add_argument("--if-idle", action="store_true",
                    help="with --enqueue: do nothing if the queue still has jobs waiting or running")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    all_cities = load_cities()
    if args.list_cities:
        for c in all_cities:
            print(f"{c['slug']:<12} {c['pincode']}  {c['name']}, {c.get('state', '')}")
        return 0
    cities = select_cities(args.city, all_cities)

    if args.enqueue and args.if_idle:
        from jobs import pending_jobs

        if n := pending_jobs():
            log.info("Queue still has %d jobs from an earlier run; not queuing a new one", n)
            return 0

    if args.list_categories:
        # Live list from Amazon, to look up node IDs; marks the ones every run crawls.
        discovery = Fetcher(settings)
        discovery.warm_up()
        crawled = {c["node"] for c in SUBCATEGORIES}
        for c in discover_categories(discovery):
            print(f"{c['node']:>14}  {'*' if c['node'] in crawled else ' '} {c['name']}")
        print("\n* = crawled every run (SUBCATEGORIES in crawler.py)")
        return 0

    cats = list(SUBCATEGORIES)
    if args.categories:
        by_node = {c["node"]: c for c in SUBCATEGORIES}
        cats = [by_node.get(n, {"name": n, "node": n}) for n in args.categories]
    log.info("Crawling %d subcategories: %s", len(cats), ", ".join(c["name"] for c in cats))

    store = Store(settings.mongo_uri, settings.mongo_db)
    params = vars(args) | {"cities": [c["slug"] for c in cities], "categories": [c["node"] for c in cats]}
    run_id = store.start_run(params)

    if args.enqueue:
        from jobs import enqueue_run  # needs redis/rq, only imported for queued runs

        opts = {k: getattr(args, k) for k in ("pages", "products", "no_details", "refresh_hours")}
        n = enqueue_run(cities, cats, opts, run_id)
        log.info("Run %s: queued %d jobs (%d cities x %d categories) on '%s'; start worker.py to process them",
                 run_id, n, len(cities), len(cats), settings.crawl_queue)
        return 0

    stats = new_stats()
    log.info("Run %s: %d cities x %d categories", run_id, len(cities), len(cats))
    status = "completed"
    try:
        for city in cities:
            crawl_city(city, cats, store, args, run_id, stats)
    except KeyboardInterrupt:
        status = "interrupted"
        log.warning("Interrupted, saving run status")
    finally:
        if stats["cities_failed"] and status == "completed":
            status = "partial"
        store.finish_run(run_id, status, stats)
        log.info("Run %s %s: %s", run_id, status, stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
