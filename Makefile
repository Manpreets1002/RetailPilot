# Shortcuts for docker-compose.yml. Run `make` (or `make help`) to list them.
# Data in Mongo and Redis lives in Docker volumes, so stop/down/restart keep it.

COMPOSE := docker compose
# Also covers the profiled crawler services, so stop/down/ps include them
ALL     := $(COMPOSE) --profile crawl --profile tools
MONGO_VOLUME := amazon_ecomm_crawler_amazon_crawler_mongo

.DEFAULT_GOAL := help
.PHONY: help volume up down stop restart build ps logs \
        mongo redis backend frontend mcp crawler crawl-run \
        stop-mongo stop-redis stop-backend stop-frontend stop-mcp stop-crawler \
        mongo-shell redis-cli queue backup

help: ## List the commands
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-14s %s\n", $$1, $$2}'

volume: ## Create the Mongo data volume (first run on a new server; safe to repeat)
	@docker volume inspect $(MONGO_VOLUME) >/dev/null 2>&1 || docker volume create $(MONGO_VOLUME)

# ---- everything ----
up: volume ## Start everything: mongo, redis, backend, frontend, mcp and the crawler
	$(COMPOSE) --profile crawl up -d --build

stop: ## Stop everything (containers are kept)
	$(ALL) stop

down: ## Stop and remove all containers (volume data is kept)
	$(ALL) down

restart: ## Restart everything
	$(ALL) restart

build: ## Rebuild all images
	$(COMPOSE) --profile crawl build

ps: ## Show what is running
	$(ALL) ps

logs: ## Follow logs: `make logs` for all, `make logs s=backend` for one service
	$(ALL) logs -f --tail=100 $(s)

# ---- single services (each also starts the services it depends on) ----
mongo: volume ## Start MongoDB
	$(COMPOSE) up -d mongo

redis: ## Start Redis
	$(COMPOSE) up -d redis

backend: volume ## Start the backend (+ mongo)
	$(COMPOSE) up -d --build backend

frontend: volume ## Start the frontend (+ backend, mongo)
	$(COMPOSE) up -d --build frontend

mcp: volume ## Start the MCP server (+ mongo)
	$(COMPOSE) up -d --build mcp

crawler: volume ## Start the scraper: queue a run and start the workers (+ mongo, redis)
	$(COMPOSE) --profile crawl up -d --build crawler-start crawler-worker

crawl-run: ## Queue another crawl run (skipped if one is still pending)
	$(COMPOSE) --profile crawl start crawler-start

stop-mongo: ## Stop MongoDB
	$(COMPOSE) stop mongo

stop-redis: ## Stop Redis
	$(COMPOSE) stop redis

stop-backend: ## Stop the backend
	$(COMPOSE) stop backend

stop-frontend: ## Stop the frontend
	$(COMPOSE) stop frontend

stop-mcp: ## Stop the MCP server
	$(COMPOSE) stop mcp

stop-crawler: ## Stop the scraper (queued jobs stay in Redis)
	$(COMPOSE) --profile crawl stop crawler-start crawler-worker

# ---- tools ----
mongo-shell: ## Open mongosh on the amazon_ecomm database
	$(COMPOSE) exec mongo mongosh amazon_ecomm

redis-cli: ## Open redis-cli
	$(COMPOSE) exec redis redis-cli

queue: ## Show crawl queue, workers and job counts
	$(COMPOSE) --profile crawl exec crawler-worker rq info -u redis://redis:6379/0

backup: ## Dump the Mongo database to backup-<date>.archive.gz
	$(COMPOSE) exec -T mongo mongodump --db amazon_ecomm --archive --gzip > backup-$$(date +%F).archive.gz
