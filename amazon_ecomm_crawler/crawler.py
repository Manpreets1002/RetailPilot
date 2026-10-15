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


def discover_categories(fetcher: Fetcher) -> list[dict]:
    html = fetcher.get(f"/s?k={settings.search_index}&i={settings.search_index}")
    cats = parse_departments(html) if html else []
    # Drop the root node itself if it shows up as a filter entry.
    cats = [c for c in cats if c["node"] != settings.root_node]
    if not cats:
        log.warning("No subcategories found; falling back to the root Electronics node")
        cats = [{"name": "Electronics", "node": settings.root_node}]
    return cats


def search_url(node: str, page: int) -> str:
    rh = quote(f"n:{node}", safe="")
    # fs=true forces the product grid; without it page 1 is a browse storefront.
    return f"/s?i={settings.search_index}&rh={rh}&fs=true&page={page}"


def crawl_category(fetcher: Fetcher, store: Store, city: dict, cat: dict, args, run_id, stats: dict) -> None:
    log.info("=== [%s] Category %s (%s)", city["slug"], cat["name"], cat["node"])
    store.upsert_category(cat["node"], cat["name"], settings.root_node)

    seen: list[str] = []
    page = 1
    while True:
        if args.pages and page > args.pages:
            break
        html = fetcher.get(search_url(cat["node"], page))
        if not html:
            break
        result = parse_search_page(html)
        items = [p for p in result["products"] if p["asin"] not in seen]
        if not items:
            log.info("No products on page %d, stopping category", page)
            break

        if args.products:
            items = items[: max(0, args.products - len(seen))]
        new = store.upsert_listings(items, cat, city, run_id)
        seen.extend(p["asin"] for p in items)
        stats["listings"] += len(items)
        stats["new_products"] += new
        log.info("[%s] Page %d/%s: %d products (%d new)", city["slug"], page, result["total_pages"] or "?", len(items), new)

        if args.products and len(seen) >= args.products:
            break
        if not result["next_page"]:
            break
        page += 1

    if args.no_details:
        return

    todo = store.needs_detail(seen, city["slug"], args.refresh_hours)
    log.info("[%s] Fetching %d product pages (%d skipped as fresh)", city["slug"], len(todo), len(seen) - len(todo))
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
    ap.add_argument("--city", nargs="*", metavar="CITY",
                    help="cities to crawl, by slug/name/pincode from cities.json (default: all)")
    ap.add_argument("--list-cities", action="store_true", help="print the cities in cities.json and exit")
    ap.add_argument("--list-categories", action="store_true", help="print subcategories and exit")
    ap.add_argument("--categories", nargs="*", help="browse node IDs to crawl (default: all discovered)")
    ap.add_argument("--pages", type=int, default=settings.max_pages_per_category, help="max pages per category, 0 = all")
    ap.add_argument("--products", type=int, default=settings.max_products_per_category, help="max products per category, 0 = all")
    ap.add_argument("--no-details", action="store_true", help="skip product detail pages")
    ap.add_argument("--refresh-hours", type=float, default=24, help="re-scrape details older than this; 0 = always")
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

    # Subcategories are the same in every city, so discover them once without a location.
    discovery = Fetcher(settings)
    discovery.warm_up()
    cats = discover_categories(discovery)

    if args.list_categories:
        for c in cats:
            print(f"{c['node']:>14}  {c['name']}")
        return 0

    if args.categories:
        by_node = {c["node"]: c for c in cats}
        cats = [by_node.get(n, {"name": n, "node": n}) for n in args.categories]

    store = Store(settings.mongo_uri, settings.mongo_db)
    params = vars(args) | {"cities": [c["slug"] for c in cities], "categories": [c["node"] for c in cats]}
    run_id = store.start_run(params)
    stats = {"listings": 0, "new_products": 0, "details": 0, "detail_failures": 0, "reviews": 0,
             "cities_done": [], "cities_failed": []}
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
