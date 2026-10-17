# Amazon E-commerce Crawler (POC)

Crawls the **Electronics** category on amazon.in **city by city** and stores
products, prices, ratings, delivery and reviews in MongoDB.

## How it works

1. **Load cities.** Reads the cities from [`cities.json`](cities.json): 10 tier-1
   cities, each with a delivery pincode.
2. **Discover subcategories.** Reads the "Department" filter on the Electronics
   search page and finds 18 subcategories (Mobiles, Computers, Headphones, TV, Cameras, …).
3. **Set the city.** For each city it opens a new session and sets that city's
   delivery pincode, the same way the "Deliver to" popup does. Amazon confirms the
   city, and every page is checked to make sure it is still delivering to that pincode.
4. **Walk listing pages.** For each subcategory it opens
   `/s?i=electronics&rh=n:<node>&fs=true&page=N` and saves every product card.
5. **Open product pages.** It fetches `/dp/<ASIN>` for full details, specs, images,
   reviews and that city's delivery date.
6. **Save to MongoDB.** Data that is the same everywhere goes to `products`.
   City-specific price, stock, seller and delivery go to `city_offers`.

Plain `requests`/`httpx` get HTTP 503 from Amazon, so the crawler uses
[`curl_cffi`](https://github.com/lexiforest/curl_cffi), which uses the same TLS
fingerprint as Chrome. It waits 2–5 s between requests at random. If Amazon shows
a bot-check page, it backs off and starts a fresh session, then sets the city
again. It skips any URL that robots.txt (`User-agent: *`) disallows.

## Browser fallback

Sometimes plain HTTP can't get a working session: Amazon serves its AWS WAF
challenge or a bot-check page, or the "Deliver to" API won't set the pincode.
Before this fallback, that city (often Mumbai) was skipped. Now the crawler:

1. Tries the HTTP warm-up and pincode call twice.
2. If both fail, opens Chromium with Playwright ([`browser_session.py`](browser_session.py)).
   The browser solves the JS challenge, clicks "Continue shopping" on a bot check,
   and sets the pincode through the real "Deliver to" popup, waiting for Amazon's
   `address-change` reply.
3. Loads the browser's cookies into the `curl_cffi` session, checks that HTTP pages
   now show the right pincode, and carries on scraping over fast HTTP.

The same thing happens mid-crawl: if a page is blocked twice in a row, the next
session reset uses the browser instead of fresh HTTP cookies. If the browser fails,
it isn't retried for 5 minutes.

| Env var | Default | Meaning |
|---|---|---|
| `BROWSER_FALLBACK` | `auto` | `auto` = only when HTTP fails, `always` = every new session, `off` = never |
| `BROWSER_HEADLESS` | `true` | `false` opens a visible window. If Amazon shows an image captcha, solve it there and the crawl continues |
| `BROWSER_MANUAL_WAIT` | `180` | Seconds to wait for a manual captcha solve |
| `BROWSER_EXECUTABLE` | (Playwright Chromium) | Use an installed browser instead, e.g. `/usr/bin/google-chrome` |

Headless mode handles the challenge and button checks, but not image captchas. If
one appears, the job fails and is retried later.

## Workers (Redis + RQ)

For parallel crawling, split a run into jobs and let workers process them:

```bash
python crawler.py --enqueue --city mumbai delhi chennai   # one job per (city, category)
python worker.py --processes 4                           # 4 workers (default: CRAWL_WORKERS)
python worker.py --burst                                 # or: exit when the queue is empty
rq info -u redis://localhost:6380/0                      # queue / worker status
```

- Each job crawls one category for one city. Workers keep one session per city, so
  each pincode is set once per worker, not once per job.
- A failed job is retried twice (after 1 and 5 minutes). Workers run RQ's
  scheduler for this, so use long-running workers, not `--burst`, if you want retries.
- Every job adds its counters to the run's `crawl_runs` doc. A final job marks the
  run `completed` or `partial` and fills `cities_done`/`cities_failed`.
- At most `BROWSER_MAX_CONCURRENT` (default 2) Chromium instances run at once across
  all workers. The rest wait for a free slot.
- All workers share one IP, so more workers mean more bot checks (see sizing below).

### Sizing

Measured in the container:

| | CPU | Memory |
|---|---|---|
| Worker crawling over HTTP | ~5% of a core on average (mostly sleeping between requests) | 60–100 MB |
| Chromium during a fallback (~30 s) | 1–2.3 cores | ~600–700 MB |
| 4 workers, 2 browsers at once (worst case) | capped at 2 cores | 1.3 GB peak |

With the container's 2 CPU / 12 GB limit, the hardware could run about 30 workers.
The real limit is Amazon: each worker makes ~17 requests/min, all from one IP.
Use **4 workers** (the default), and at most 6–8. Past that, blocks rise until the
browser fallback runs all the time. More throughput needs proxies, not more workers.

`REDIS_URL` (default `redis://localhost:6380/0`), `CRAWL_QUEUE` (`crawl`) and
`CRAWL_JOB_TIMEOUT` (seconds, default 3 h) configure the queue.

## Docker

The crawler image is the `crawler` target of the repo-root [`Dockerfile`](../Dockerfile),
and it includes headless Chromium. Run these from the repo root (compose needs a
`.env` there; copy `.env.example`):

One command starts everything and begins crawling:

```bash
docker compose --profile crawl up -d --build
```

That starts `mongo`, `redis`, and `crawler-worker` (`CRAWL_WORKERS` workers). It also runs
`crawler-start` once, which finds the 18 Electronics categories, queues one job per
(city, category), and exits. By default that is all 10 cities in `cities.json`,
so 180 jobs. With the default limits (2 pages, 20 products per category) and 4
workers, that takes about 1–1.5 h. Set `CRAWL_CITIES=mumbai,delhi` in `.env` to crawl fewer.
Running `up` again starts a new run only if the last one has finished.

```bash
docker compose logs -f crawler-start crawler-worker                     # watch progress
docker compose start crawler-start                                      # start another run later
docker compose run --rm crawler crawler.py --enqueue --city mumbai pune # queue a run for specific cities
docker compose run --rm crawler crawler.py --city mumbai --pages 1      # or crawl directly, no queue
docker compose run --rm crawler crawler.py --list-cities
```

Inside compose, `MONGO_URI` and `REDIS_URL` point at the `mongo` and `redis`
services. From the host, Redis is on port 6380.

The `crawler`, `crawler-start` and `crawler-worker` containers are limited to **2 CPUs and 12 GB RAM**
each. All worker processes run in the single `crawler-worker` container, so the
limit covers them together. Set the count with `CRAWL_WORKERS` in `.env`, not
`--scale`, because each scaled copy would get its own 2 CPU / 12 GB.

## Cities config

`cities.json` holds an array of cities. To add or remove one, edit the array; no
code changes are needed.

```json
{
  "cities": [
    {"slug": "delhi",  "name": "New Delhi", "state": "Delhi",       "pincode": "110001"},
    {"slug": "mumbai", "name": "Mumbai",    "state": "Maharashtra", "pincode": "400001"}
  ]
}
```

| Slug | City | Pincode |
|---|---|---|
| delhi | New Delhi | 110001 |
| mumbai | Mumbai | 400001 |
| bengaluru | Bengaluru | 560001 |
| hyderabad | Hyderabad | 500001 |
| chennai | Chennai | 600001 |
| kolkata | Kolkata | 700001 |
| pune | Pune | 411001 |
| ahmedabad | Ahmedabad | 380001 |
| gurugram | Gurugram | 122001 |
| noida | Noida | 201301 |

The first eight are India's official tier-1 (X-class) cities. Gurugram and Noida
were added from the NCR to make ten. Set `CITIES_FILE` to use a different file.

## Quick start

```bash
cd amazon_ecomm_crawler
docker compose up -d                     # MongoDB on localhost:27018
python -m venv ../.venv && ../.venv/bin/pip install -r requirements.txt
../.venv/bin/playwright install chromium # for the browser fallback
cp ../.env.example ../.env               # optional, defaults work

../.venv/bin/python crawler.py --list-cities
../.venv/bin/python crawler.py --list-categories
../.venv/bin/python crawler.py --city mumbai                      # one city
../.venv/bin/python crawler.py --city delhi bengaluru 600001      # several (slug, name or pincode)
../.venv/bin/python crawler.py --city mumbai --categories 29561348031 --pages 1 --products 5
../.venv/bin/python crawler.py                                    # all 10 cities
../.venv/bin/python -m pytest -q tests
```

| Flag | Meaning |
|---|---|
| `--city C [C…]` | Cities from `cities.json`, by slug, name or pincode (default: all) |
| `--list-cities` | Print the configured cities and exit |
| `--categories N [N…]` | Browse node IDs to crawl (default: all discovered) |
| `--pages N` | Max listing pages per category (`0` = all, Amazon caps around 7–20) |
| `--products N` | Max products per category (`0` = all) |
| `--no-details` | Listing data only, skips product pages (much faster) |
| `--refresh-hours H` | Skip product pages already scraped for that city within H hours (`0` = always fetch) |
| `--enqueue` | Queue one job per (city, category) for `worker.py` instead of crawling here |

Each city is a full pass, so crawl time grows with the number of cities. To run
cities in parallel, use `--enqueue` and workers (see below).

## Data model (`amazon_ecomm` database)

**`products`**: one doc per ASIN, holding data that is the same in every city

| Field | Example |
|---|---|
| `asin`, `title`, `brand`, `byline` | `B0CCZ26B5V`, "Bose NEW QuietComfort…", `Bose` |
| `rating`, `rating_count`, `rating_histogram` | `4.4`, `21605`, `{"5": 71, "4": 13, …}` |
| `bought_past_month`, `is_amazons_choice`, `best_sellers_rank` | "500+ bought in past month" |
| `breadcrumbs`, `category_nodes`, `category_names` | `["Electronics","Headphones…","Over-Ear"]` |
| `feature_bullets`, `description`, `overview`, `specifications` | "About this item" bullets, spec table (model, weight, warranty, …) |
| `images`, `variations` | hi-res image URLs, sibling ASINs |
| `first_seen_at`, `last_seen_at`, `detail_scraped_at` | UTC timestamps |

**`city_offers`**: one doc per (ASIN, city)

| Field | Example |
|---|---|
| `asin`, `city`, `city_name`, `pincode` | `B0CY1PL36Q`, `chennai`, `Chennai`, `600001` |
| `price`, `mrp`, `discount_pct`, `currency` | `1699.0`, `5999.0`, `72`, `INR` |
| `price_raw`, `mrp_raw` | `₹1,699.00`, `₹5,999` |
| `availability`, `deliverable` | "In stock", `true` |
| `delivery`, `delivery_date`, `fastest_delivery` | "FREE delivery Monday, 26 October" |
| `seller`, `ships_from`, `deal_badge` | "Clicktech Retail Private Ltd", "Amazon" |
| `listing` | Search card in that city: position, sponsored, prime, coupon, delivery, offers |

**`price_history`**: `asin`, `city`, `pincode`, `price`, `mrp`, `discount_pct`, `source` (listing/detail), `scraped_at`

**`reviews`**: `review_id`, `asin`, `author`, `rating`, `title`, `body`,
`date`, `country`, `variant`, `verified_purchase`, `helpful_votes`, `image_count`

**`cities`**: the configured cities plus the location Amazon confirmed.
**`categories`**, **`crawl_runs`**: subcategory list and per-run counters (cities done or failed, requests, blocks).

### Example queries

```js
// mongosh mongodb://localhost:27018/amazon_ecomm

// One product across all cities: price and delivery date
db.city_offers.find({asin: "B0CY1PL36Q"}, {city: 1, price: 1, delivery_date: 1, seller: 1, _id: 0})

// Products whose price differs between cities
db.city_offers.aggregate([
  {$group: {_id: "$asin", min: {$min: "$price"}, max: {$max: "$price"}, cities: {$addToSet: "$city"}}},
  {$match: {$expr: {$gt: ["$max", "$min"]}}}
])

// Best-rated deals in Mumbai
db.city_offers.aggregate([
  {$match: {city: "mumbai"}},
  {$lookup: {from: "products", localField: "asin", foreignField: "asin", as: "p"}}, {$unwind: "$p"},
  {$match: {"p.rating": {$gte: 4.3}, "p.rating_count": {$gte: 1000}}},
  {$sort: {discount_pct: -1}}, {$limit: 10},
  {$project: {_id: 0, asin: 1, title: "$p.title", price: 1, mrp: 1, discount_pct: 1, rating: "$p.rating"}}
])

db.price_history.find({asin: "B0CCZ26B5V", city: "delhi"}).sort({scraped_at: -1})
```

## Limits of the POC

- **What varies by city:** in testing, price and MRP were mostly the same across
  cities, while delivery date, stock and sometimes the seller changed. The schema
  stores all of them per city either way.
- **Bot checks:** Amazon often shows a "Continue shopping" bot-check page on product
  pages. The crawler backs off and retries with a fresh session, then falls back to the
  browser, so each check costs about 15–40 s. Raising `CRAWL_MIN_DELAY`/`CRAWL_MAX_DELAY`
  reduces them.
- **Reviews:** only the ~8–10 reviews shown on the product page. Amazon's full
  review pages (`/product-reviews/`) need a signed-in session.
- **Volume:** a full crawl is 10 cities × 18 categories × up to ~400 pages. For
  production you would add proxies so workers don't share one IP, and schedule
  incremental price re-crawls. The official
  [Product Advertising API](https://webservices.amazon.in/paapi5/documentation/)
  is the approved way to get this data.
- **Markup drift:** Amazon changes its HTML often. Every field is optional, and the
  unit tests in `tests/` pin the selectors.
- **Terms:** robots.txt is respected, but Amazon's Conditions of Use restrict
  automated access. Keep this to research or POC scale.

## Files

| File | Purpose |
|---|---|
| `cities.json` | The 10 cities and pincodes to crawl |
| `crawler.py` | CLI and city → category → product crawl loop |
| `http_client.py` | Chrome-fingerprint session, pincode/location setting, delays, retries, bot-check detection, robots.txt, browser fallback trigger |
| `browser_session.py` | Playwright browser: passes the bot check, sets the pincode, returns cookies for HTTP |
| `jobs.py` | Queue jobs: one per (city, category), plus the closing job for the run |
| `worker.py` | RQ worker that runs the queued jobs |
| `parsers.py` | Search page, product page (incl. delivery), department parsers; city/common field split |
| `storage.py` | MongoDB upserts, indexes, city offers, price history, run tracking |
| `config.py` | City loading and selection; settings come from `retailpilot.config` (env / root `.env`) |
| `docker-compose.yml` | Local MongoDB 7 on port 27018 (the full stack with Redis and workers is the repo-root compose) |
