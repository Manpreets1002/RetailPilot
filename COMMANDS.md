# Server commands: MongoDB, Redis, crawler

Run everything from the repo folder (`cd ~/RetailPilot`). Service names (`mongo`, `redis`,
`crawler-worker`) come from `docker-compose.yml`, so these work whatever Docker names the containers.

## 1. Start / stop

```bash
# First time on a new server only: create the Mongo data volume
docker volume create amazon_ecomm_crawler_amazon_crawler_mongo

docker compose up -d mongo redis              # start only MongoDB + Redis
docker compose ps                             # what is running
docker compose logs -f mongo                  # Mongo logs (Ctrl+C to leave)
docker compose logs -f redis                  # Redis logs
docker compose restart mongo redis            # restart
docker compose stop mongo redis               # stop (data is kept)
```

## 2. Crawler

```bash
# Only the crawler (+ mongo, redis). Without the service names, `up` also builds and starts
# backend, frontend and mcp, because services with no profile always start.
docker compose --profile crawl up -d --build crawler-start crawler-worker
docker compose --profile crawl up -d --build              # everything: website, backend, mcp AND the crawler
docker compose stop frontend backend mcp                  # stop the website stack, keep crawling
docker compose logs -f crawler-start crawler-worker       # watch the crawl
docker compose stop crawler-worker                        # pause crawling (jobs stay queued)
docker compose start crawler-worker                       # resume
docker compose start crawler-start                        # start another run (skips if one is still pending)
docker compose up -d --build                              # website, backend, MCP (+ mongo)
```

### Scheduled runs (cron, 5 AM and 5 PM)

```bash
date && timedatectl | grep "Time zone"                    # check server time and zone
sudo timedatectl set-timezone Asia/Kolkata && sudo systemctl restart cron   # if it isn't IST
crontab -e                                                # add the line below
# 0 5,17 * * * /home/beast/RetailPilot/scripts/start-crawl.sh >> /home/beast/crawl-cron.log 2>&1
crontab -l                                                # confirm it's saved
tail -f ~/crawl-cron.log                                  # see what cron did
./scripts/start-crawl.sh                                  # run it by hand once to test
```

## 3. MongoDB

Open the shell (type `exit` to leave):

```bash
docker compose exec mongo mongosh amazon_ecomm
```

Inside `mongosh`:

```js
show collections
db.stats(1024*1024)                                   // database size in MB

// Counts
db.products.countDocuments()
db.products.countDocuments({detail_scraped_at: {$ne: null}})   // products with full details
db.city_offers.countDocuments()
db.reviews.countDocuments()
db.city_offers.aggregate([{$group: {_id: "$city", products: {$sum: 1}}}])

// Crawl progress: latest run (pending_jobs 0 = finished)
db.crawl_runs.find({}, {status: 1, pending_jobs: 1, started_at: 1, finished_at: 1, stats: 1}).sort({started_at: -1}).limit(1)

// One product, its price in every city, its reviews, its price history
db.products.findOne({asin: "B0DTJ9JMN8"})
db.city_offers.find({asin: "B0DTJ9JMN8"}, {city: 1, price: 1, mrp: 1, availability: 1, delivery: 1, _id: 0})
db.reviews.find({asin: "B0DTJ9JMN8"}, {rating: 1, title: 1, date: 1, _id: 0})
db.price_history.find({asin: "B0DTJ9JMN8"}).sort({scraped_at: -1}).limit(10)

// Search by title / brand
db.products.find({title: /iphone/i}, {asin: 1, title: 1, rating: 1, _id: 0}).limit(10)
db.products.find({brand: "Samsung"}).count()

// Products per category
db.categories.find({}, {node: 1, name: 1, _id: 0})
db.products.aggregate([{$unwind: "$category_names"}, {$group: {_id: "$category_names", n: {$sum: 1}}}, {$sort: {n: -1}}])
```

One-liners without opening the shell:

```bash
docker compose exec mongo mongosh amazon_ecomm --quiet --eval 'db.products.countDocuments()'
docker compose exec mongo mongosh amazon_ecomm --quiet --eval 'db.crawl_runs.find({}, {status:1, pending_jobs:1, "stats.listings":1, "stats.details":1, "stats.reviews":1}).sort({started_at:-1}).limit(1)'
```

Backup and restore:

```bash
docker compose exec -T mongo mongodump --db amazon_ecomm --archive --gzip > backup-$(date +%F).archive.gz
docker compose exec -T mongo mongorestore --archive --gzip < backup-2026-10-17.archive.gz
```

## 4. Redis (job queue)

```bash
docker compose exec redis redis-cli ping                       # PONG = running
docker compose exec redis redis-cli llen rq:queue:crawl        # jobs waiting in the queue
docker compose exec redis redis-cli info memory | grep used_memory_human
docker compose exec redis redis-cli                            # interactive shell (exit to leave)
```

Queue status and failed jobs (runs the `rq` tool inside the worker container):

```bash
docker compose exec crawler-worker rq info -u redis://redis:6379/0                        # queues, workers, job counts
docker compose exec crawler-worker rq requeue --queue crawl --all -u redis://redis:6379/0 # retry every failed job
docker compose exec crawler-worker rq empty crawl -u redis://redis:6379/0                 # drop all waiting jobs
docker compose exec crawler-worker rq suspend -u redis://redis:6379/0                     # workers stop taking new jobs
docker compose exec crawler-worker rq resume -u redis://redis:6379/0                      # and start again
```

Start over with an empty queue (Mongo data is kept):

```bash
docker compose exec redis redis-cli flushall
docker compose --profile crawl up -d --force-recreate crawler-start crawler-worker
```

## 5. Notes

- MongoDB (port 27018) and Redis (port 6380) have **no password**. Keep those ports closed in the
  VM firewall; only the website port 8080 should be open.
- After changing `.env`, recreate the containers so they pick it up:
  `docker compose --profile crawl up -d --force-recreate`
