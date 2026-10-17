"""Crawl worker: runs jobs from the Redis queue that `crawler.py --enqueue` fills.

  python worker.py                 # one worker, keeps running and waits for jobs
  python worker.py --processes 4   # four workers in one process group (what Docker runs)
  python worker.py --burst         # exit when the queue is empty

Uses RQ's SimpleWorker (no fork per job), so each worker keeps its per-city
sessions between jobs and sets each pincode only once. The built-in scheduler
re-queues failed jobs for their delayed retries.

Running all workers in one container lets one CPU/memory limit cover them all.
"""

import argparse
import logging
import multiprocessing as mp
import os
import signal
import sys

from redis import Redis
from rq import Queue, SimpleWorker

from config import settings


def run_worker(burst: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(processName)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    conn = Redis.from_url(settings.redis_url)
    worker = SimpleWorker([Queue(settings.crawl_queue, connection=conn)], connection=conn)
    worker.work(burst=burst, with_scheduler=True, logging_level="INFO")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run crawl jobs from the Redis queue")
    ap.add_argument("--burst", action="store_true", help="exit when the queue is empty")
    ap.add_argument("--processes", type=int, default=settings.crawl_workers,
                    help="worker processes to run (default: CRAWL_WORKERS)")
    args = ap.parse_args()

    if args.processes <= 1:
        run_worker(args.burst)
        return

    procs = [mp.Process(target=run_worker, args=(args.burst,), name=f"worker-{i}") for i in range(1, args.processes + 1)]
    for p in procs:
        p.start()

    def stop(signum, _frame):
        # docker stop sends SIGTERM to PID 1 only; pass it on so each RQ worker
        # finishes its current job and exits (warm shutdown).
        for p in procs:
            if p.is_alive():
                os.kill(p.pid, signum)

    signal.signal(signal.SIGTERM, stop)
    # Ctrl+C already reaches every worker through the terminal's process group.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    for p in procs:
        p.join()
    sys.exit(max((p.exitcode or 0) for p in procs))


if __name__ == "__main__":
    main()
