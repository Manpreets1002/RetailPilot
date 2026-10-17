#!/bin/sh
# Start a crawl run. Used by cron at 5 AM and 5 PM (see COMMANDS.md).
# Makes sure the workers are up, then queues a new run. If the previous run is
# still going, crawler-start skips (--if-idle), so runs never pile up.
cd "$(dirname "$0")/.." || exit 1
echo "=== $(date '+%F %T %Z') starting crawl"
docker compose --profile crawl up -d crawler-worker
docker compose --profile crawl run --rm crawler-start
echo "=== $(date '+%F %T %Z') done"
