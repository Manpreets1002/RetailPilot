"""Crawl jobs for the worker system (Redis + RQ).

`crawler.py --enqueue` starts a run by queuing one listing job per (city, category).
Jobs are small, so a full crawl (every page, every product) never hits a timeout:

  listing_job  saves CRAWL_PAGES_PER_JOB search pages (default 10) for one city and
               category, queues detail jobs for the products it found, then queues
               itself again for the next pages until the category runs out.
  detail_job   fetches CRAWL_DETAILS_PER_JOB product pages (default 20): specs,
               images, reviews, and that city's price and delivery.

Detail jobs go to the front of the queue so products are filled in soon after they
are listed. A failed job is retried twice, a few minutes apart. The run keeps a count
of unfinished jobs; the job that brings it to zero marks the run finished.

Each worker process keeps one Fetcher per city, so the delivery pincode (and any
browser fallback) is set up once per city per worker, not once per job.
"""

import logging
from collections.abc import Callable
from types import SimpleNamespace

from bson import ObjectId
from redis import Redis
from rq import Queue, Retry, get_current_job

from config import settings
from crawler import crawl_details, crawl_listing_pages, detail_todo, new_stats
from http_client import Fetcher
from storage import Store, now

log = logging.getLogger("jobs")

_store: Store | None = None
_fetchers: dict[str, Fetcher] = {}


def get_queue() -> Queue:
    return Queue(settings.crawl_queue, connection=Redis.from_url(settings.redis_url))


def pending_jobs() -> int:
    """Jobs still waiting, running or scheduled for a retry."""
    q = get_queue()
    return len(q) + q.started_job_registry.count + q.scheduled_job_registry.count + q.deferred_job_registry.count


def _get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(settings.mongo_uri, settings.mongo_db)
    return _store


def _get_fetcher(city: dict) -> Fetcher:
    fetcher = _fetchers.get(city["slug"])
    if fetcher is None:
        fetcher = Fetcher(settings, city=city)
        fetcher.warm_up()  # LocationError if neither HTTP nor the browser can set the pincode
        _get_store().upsert_city(city, fetcher.location)
        _fetchers[city["slug"]] = fetcher
    return fetcher


# ---------------------------------------------------------------- run bookkeeping

def _enqueue(store: Store, run: ObjectId, func: Callable, *args, description: str, at_front: bool = False) -> None:
    store.run_pending(run, +1)  # count it before it can possibly finish
    get_queue().enqueue(
        func, *args,
        job_timeout=settings.crawl_job_timeout,
        description=description,
        result_ttl=3600, failure_ttl=7 * 24 * 3600,
        retry=Retry(max=2, interval=[60, 300]),
        at_front=at_front,
    )


def _tracked(run: ObjectId, city: dict, body: Callable[[], dict]) -> dict:
    """Run a job body; when it succeeds or fails for the last time, count it and maybe close the run."""
    store = _get_store()
    try:
        result = body()
    except Exception:
        job = get_current_job()
        if job is None or not job.retries_left:
            store.add_run_stats(run, {"jobs_failed": 1})
            _job_finished(store, run)
        raise
    store.add_run_stats(run, {"jobs_done": 1}, cities_ok=[city["slug"]])
    _job_finished(store, run)
    return result


def _job_finished(store: Store, run: ObjectId) -> None:
    if store.run_pending(run, -1) > 0:
        return
    doc = store.get_run(run) or {}
    stats = doc.get("stats", {})
    cities = doc.get("params", {}).get("cities", [])
    ok = set(stats.get("cities_ok", []))
    status = "partial" if stats.get("jobs_failed") else "completed"
    store.db.crawl_runs.update_one({"_id": run}, {"$set": {
        "status": status, "finished_at": now(),
        "stats.cities_done": [c for c in cities if c in ok],
        "stats.cities_failed": [c for c in cities if c not in ok],
    }})
    log.info("Run %s %s: %s", run, status, stats)


def _save_counters(store: Store, run: ObjectId, stats: dict, fetcher: Fetcher, before: dict) -> dict:
    counters = {k: v for k, v in stats.items() if isinstance(v, int)}
    counters |= {k: v - before.get(k, 0) for k, v in fetcher.stats.items()}
    store.add_run_stats(run, counters)
    return counters


# ---------------------------------------------------------------- jobs

def listing_job(city: dict, cat: dict, first_page: int, already_seen: int, opts: dict, run_id: str) -> dict:
    """Save a chunk of search pages, queue their product pages, then queue the next chunk."""
    run = ObjectId(run_id)

    def body() -> dict:
        store, fetcher, args = _get_store(), _get_fetcher(city), SimpleNamespace(**opts)
        if first_page == 1:
            store.upsert_category(cat["node"], cat["name"], settings.root_node)
        last_page = first_page + settings.crawl_pages_per_job - 1
        stats, before = new_stats(), dict(fetcher.stats)
        try:
            seen, more = crawl_listing_pages(fetcher, store, city, cat, args, run, stats,
                                             first_page, last_page, already_seen)
        finally:
            counters = _save_counters(store, run, stats, fetcher, before)

        if not args.no_details:
            todo = detail_todo(store, seen, city, args)
            n = settings.crawl_details_per_job
            for i in range(0, len(todo), n):
                _enqueue(store, run, detail_job, city, todo[i:i + n], opts, run_id, at_front=True,
                         description=f"{city['slug']} / {cat['name']} / {len(todo[i:i + n])} products")
        if more:
            _enqueue(store, run, listing_job, city, cat, last_page + 1, already_seen + len(seen), opts, run_id,
                     description=f"{city['slug']} / {cat['name']} / pages {last_page + 1}+")
        return counters

    return _tracked(run, city, body)


def detail_job(city: dict, asins: list[str], opts: dict, run_id: str) -> dict:
    """Fetch product pages (skips any that another job filled in meanwhile)."""
    run = ObjectId(run_id)

    def body() -> dict:
        store, fetcher = _get_store(), _get_fetcher(city)
        stats, before = new_stats(), dict(fetcher.stats)
        try:
            crawl_details(fetcher, store, city, asins, SimpleNamespace(**opts), run, stats)
        finally:
            counters = _save_counters(store, run, stats, fetcher, before)
        return counters

    return _tracked(run, city, body)


def enqueue_run(cities: list[dict], cats: list[dict], opts: dict, run_id) -> int:
    """Queue the first listing job of every (city, category). Returns how many were queued."""
    store = _get_store()
    count = 0
    # Hold one extra count while queuing, so a fast first job can't close the run early.
    store.run_pending(run_id, +1)
    try:
        # Category-major order, so parallel workers spread across cities.
        for cat in cats:
            for city in cities:
                _enqueue(store, run_id, listing_job, city, cat, 1, 0, opts, str(run_id),
                         description=f"{city['slug']} / {cat['name']} / pages 1+")
                count += 1
    finally:
        _job_finished(store, run_id)
    return count
