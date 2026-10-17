"""Crawl jobs for the worker system (Redis + RQ).

`crawler.py --enqueue` splits a run into one job per (city, category) and puts
them on the Redis queue; `worker.py` processes run them. A final job, which runs
after all the others whether they passed or failed, marks the run finished.
A job that fails (e.g. the pincode can't be set even with the browser) is
retried twice, a few minutes apart, before it counts as failed.

Each worker process keeps one Fetcher per city, so the delivery pincode (and any
browser fallback) is set up once per city per worker, not once per job.
"""

import logging
from types import SimpleNamespace

from bson import ObjectId
from redis import Redis
from rq import Queue, Retry
from rq.job import Dependency

from config import settings
from crawler import crawl_category, new_stats
from http_client import Fetcher, LocationError
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


def crawl_category_job(city: dict, cat: dict, opts: dict, run_id: str) -> dict:
    """Crawl one category for one city and add the counters to the run."""
    store, run = _get_store(), ObjectId(run_id)
    try:
        fetcher = _get_fetcher(city)
    except LocationError:
        store.add_run_stats(run, {"setup_failures": 1})
        raise

    stats = new_stats()
    before = dict(fetcher.stats)
    try:
        crawl_category(fetcher, store, city, cat, SimpleNamespace(**opts), run, stats)
    finally:
        counters = {k: v for k, v in stats.items() if isinstance(v, int)}
        counters |= {k: v - before.get(k, 0) for k, v in fetcher.stats.items()}
        store.add_run_stats(run, counters)
    store.add_run_stats(run, {"jobs_done": 1}, cities_ok=[city["slug"]])
    return counters


def finish_run_job(run_id: str) -> str:
    """Runs after every crawl job of the run has finished or failed for good; sets the final status."""
    store, run = _get_store(), ObjectId(run_id)
    doc = store.get_run(run) or {}
    stats = doc.get("stats", {})
    params = doc.get("params", {})
    total = len(params.get("cities", [])) * len(params.get("categories", []))
    failed_jobs = total - stats.get("jobs_done", 0)
    ok = set(stats.get("cities_ok", []))
    done = [c for c in params.get("cities", []) if c in ok]
    failed = [c for c in params.get("cities", []) if c not in ok]
    status = "partial" if failed_jobs else "completed"
    store.db.crawl_runs.update_one({"_id": run}, {"$set": {
        "status": status, "finished_at": now(), "stats.jobs_failed": failed_jobs,
        "stats.cities_done": done, "stats.cities_failed": failed,
    }})
    log.info("Run %s %s: %s", run_id, status, stats)
    return status


def enqueue_run(cities: list[dict], cats: list[dict], opts: dict, run_id) -> int:
    """Queue one job per (city, category) plus the closing job. Returns the number of crawl jobs."""
    q = get_queue()
    jobs = [
        q.enqueue(
            crawl_category_job, city, cat, opts, str(run_id),
            job_timeout=settings.crawl_job_timeout,
            description=f"{city['slug']} / {cat['name']}",
            result_ttl=7 * 24 * 3600, failure_ttl=7 * 24 * 3600,
            retry=Retry(max=2, interval=[60, 300]),
        )
        # Category-major order, so parallel workers spread across cities.
        for cat in cats
        for city in cities
    ]
    q.enqueue(finish_run_job, str(run_id), depends_on=Dependency(jobs=jobs, allow_failure=True),
              description=f"finish run {run_id}")
    return len(jobs)
