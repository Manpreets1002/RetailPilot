# RetailPilot

**An AI retail intelligence platform for laptop retailers and their shoppers.**

**Live app:** https://retailpilot-frontend-ktnjbom56q-el.a.run.app

RetailPilot turns live Amazon.in price and review data into two AI assistants:

- **Retailer copilot.** Compares our prices with competitors city by city, tests margin scenarios, reads review sentiment, and finds what sells together. It answers pricing questions with numbers taken from the database, not from the model's memory.
- **Customer shopping assistant.** Helps a shopper find and compare laptops in their own city, using today's prices, ratings and reviews. It asks clarifying questions when a request is vague.

Both assistants run on the same data and the same tools, so a retailer's pricing answer and a shopper's recommendation agree with each other.

| Link | What it is |
|---|---|
| [Live app](https://retailpilot-frontend-ktnjbom56q-el.a.run.app) | Landing page, retailer dashboard and customer shop, with the assistant on both |
| [Retailer dashboard](https://retailpilot-frontend-ktnjbom56q-el.a.run.app/retailer/) | Prices, competitors, margins, reviews and market basket for one product |
| [Customer shop](https://retailpilot-frontend-ktnjbom56q-el.a.run.app/customer/) | City-aware shop, compare and "best today" picks |
| [Crawler health](https://retailpilot-frontend-ktnjbom56q-el.a.run.app/crawler/health/) | How much data is stored and how fresh it is |
| [CI/CD runs](https://github.com/Manpreets1002/RetailPilot/actions) | Tests, builds and Cloud Run deploys for every push |
| [Crawler guide](amazon_ecomm_crawler/README.md) | How the crawler, its browser fallback and its workers work |
| [Server commands](COMMANDS.md) | Day-to-day commands for the VM: Mongo, Redis, crawler, cron, deploys |
| [Test suites](testing_suites/README.md) | What the tests cover and how to run them |

---

## The problem

Laptop prices on Amazon.in change daily and differ between cities. A retailer who wants to price well has to check many listings by hand and guess at margins. A shopper has to read hundreds of reviews to choose between similar models. Neither gets a clear, current, city-specific answer.

## Our solution

1. **Collect.** A city-by-city crawler reads the Electronics catalogue on Amazon.in. For each city it records the delivery pincode, price, stock, seller and delivery date. It also records product specs, ratings, and reviews. Worker processes share the work through a Redis queue, and a real browser steps in when Amazon blocks plain requests.
2. **Store.** MongoDB keeps product facts once, and city-specific offers and daily price history separately, so every price is tied to the city and the day it was seen.
3. **Expose.** An MCP server offers 20 read-only tools, split into shared, customer and retailer tools. Each tool is a small, tested function over the database.
4. **Reason.** A LangGraph agent, powered by Gemini, decides which tools to call. It keeps customer and retailer tools separate, and it pauses to ask the user when it needs more information.
5. **Show.** A Django web app offers a landing page, a retailer dashboard and a customer page, each dashboard with the assistant. It talks to a FastAPI backend, which runs the agent.

## Architecture and deployment

```
                    Google Cloud · asia-south1 (Mumbai)
 ┌──────────────────────────────── Cloud Run ─────────────────────────────────┐
 │                                                                            │
 │  Browser ──► Frontend (Django) ──► Backend (FastAPI) ──► LangGraph agent ──┼──► Gemini (Vertex AI)
 │              public HTTPS          private: VPC only     │                 │
 │                    │                    │                ▼                 │
 │                    │                    │          MCP server (FastMCP)    │
 │                    │                    │          20 read-only tools      │
 └────────────────────┼────────────────────┼────────────────┼─────────────────┘
                      │   private network (VPC), 10.160.0.2:27018
 ┌────────────────────▼──── retailpilot-vm (Compute Engine) ▼─────────────────┐
 │  MongoDB 7 ◄── Crawler workers (4) ◄── Redis queue ◄── cron 5 AM / 5 PM IST │
 │                    │                                                       │
 │                    └──► Amazon.in (curl_cffi; Playwright Chromium fallback) │
 └────────────────────────────────────────────────────────────────────────────┘
```

| Part | Runs on | Notes |
|---|---|---|
| Frontend (Django) | Cloud Run `retailpilot-frontend` | Public. Scales to zero when idle, so the first visit after a quiet spell takes a few seconds. |
| Backend (FastAPI + agent + MCP) | Cloud Run `retailpilot-backend` | Private (internal ingress). Only the frontend can call it, over the VPC. Starts the MCP server itself as a subprocess. |
| MongoDB | VM, Docker | Reached by Cloud Run over the private network only; port 27018 is not open to the internet. |
| Redis + crawler workers | VM, Docker | 4 workers in one container, limited to 2 CPUs and 12 GB RAM. |
| Secrets | Secret Manager | `GOOGLE_API_KEY` and `DJANGO_SECRET_KEY`; never baked into images. |
| Images | Artifact Registry `asia-south1-docker.pkg.dev/retailpilot-gemini-10162213/retailpilot` | Tagged with the git commit. |

## Key features

**For retailers**
- Price position against competitors in the same city
- Competitor price list and 14-day price trend
- Price-gap heatmap across a category
- Margin scenarios, with GST and marketplace fees
- Review sentiment for a product
- Market basket analysis: which products are reviewed by the same customers
- Data health check: how fresh the data is for each city
- The assistant on the dashboard, answering from the same data

**For shoppers**
- City-aware search and product insights
- Side-by-side comparison of products
- "Best today" picks, scored on rating, review count and price, with items already shown in the session removed
- Clarifying questions when the request is unclear, and a reply that waits for the answer
- Saved chat threads, with the products shown and the shopper's choices

**Across the platform**
- Times are in IST. Every offer is tied to the day it was observed.
- Each agent turn is routed to the customer or retailer role, and each role can only call its own tools.
- Crawler runs are logged, with per-run statistics.

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.13 |
| AI model | Google Gemini through Vertex AI (`langchain-google-genai`, `GEMINI_VERTEXAI=true`). Model set by `GEMINI_MODEL`; the code default is `gemini-3.1-flash-lite` |
| Agent | LangGraph and LangChain Core |
| Tool server | FastMCP (Model Context Protocol) |
| Backend API | FastAPI, served by Uvicorn |
| Frontend | Django 6, served by Gunicorn (threaded workers) with WhiteNoise for static files |
| Database | MongoDB 7 (PyMongo, async and sync clients) |
| Crawler | curl_cffi (browser-like TLS fingerprint), BeautifulSoup and lxml, Playwright Chromium as a fallback |
| Job queue | Redis 7 and RQ |
| Analytics helpers | NumPy, scikit-learn, Plotly |
| Config | pydantic and pydantic-settings, read from `.env` |
| Containers | One Dockerfile with four build targets (crawler, mcp, backend, frontend), Docker Compose, Makefile shortcuts |
| Cloud | Google Cloud Run, Artifact Registry, Secret Manager, Compute Engine, Workload Identity Federation |
| CI/CD | GitHub Actions |
| Tests | pytest, httpx |

### AI models and external services

- **Gemini** does the reasoning and tool choice, called through Vertex AI with the Google Cloud API key in `GOOGLE_API_KEY`. The temperature is 0 for repeatable answers.
- **Amazon.in** is the data source. The crawler reads public pages, waits 2 to 5 seconds between requests, backs off on bot checks, and skips any URL that `robots.txt` disallows. The model has no live access to Amazon; it only reads what the crawler has stored.

## CI/CD

Every push and pull request runs [`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml):

1. **Test:** the pytest suites against a real MongoDB.
2. **Build:** the backend, frontend and MCP images.
3. **Deploy (pushes to `main` only, after tests and builds pass):** only the services whose files changed are rebuilt, pushed and deployed to Cloud Run.

| Files changed | Deployed |
|---|---|
| `src/retailpilot/frontend/**`, `requirement-frontend.txt` | Frontend |
| `src/retailpilot/agent/**`, `backend/**`, `mcp/**`, `requirement-backend.txt`, `requirement-mcp.txt`, `amazon_ecomm_crawler/cities.json` | Backend |
| `src/retailpilot/config/**`, `Dockerfile` | Both |
| Crawler, docs, tests | Nothing |

- **Deploy both by hand:** Actions → CI/CD → **Run workflow** on `main`.
- **No secrets in GitHub.** The deploy job signs in with Workload Identity Federation as the `github-deployer` service account, which can only push images and deploy Cloud Run. Google only trusts pushes to `main` of `Manpreets1002/RetailPilot`.
- **Settings stay put.** A deploy only swaps the image; env vars, secrets, network and scaling stay as they are. To change those, run [`scripts/deploy-cloudrun.sh`](scripts/deploy-cloudrun.sh).
- **The crawler is not in CI/CD.** It runs on the VM; update it there with `git pull && make crawler`.

## The crawler

Full details are in the [crawler guide](amazon_ecomm_crawler/README.md). In short:

- **Scope:** the Electronics department on Amazon.in, split into its 18 subcategories. The server is set to 3 cities (Delhi, Mumbai, Bengaluru) and 50 search pages per subcategory, with every product and every review shown on each product page.
- **Workers:** a run is split into small jobs on a Redis queue: 10 search pages per job, then product pages in batches of 20. Each product page is fetched once; each city's price, stock and delivery come from that city's search results.
- **Browser fallback:** when Amazon blocks plain HTTP or won't set the delivery pincode, a headless Chromium passes the check, sets the pincode through the real "Deliver to" popup, and hands its cookies back to the fast HTTP session.
- **Schedule:** cron on the VM starts a run at 5 AM and 5 PM IST. A start is skipped if the previous run is still going. A full run of the current settings takes about 10–12 hours; the second run of the day mostly refreshes prices and finishes faster.

## How to run

### On the server (VM)

```bash
cp .env.example .env              # then fill in GOOGLE_API_KEY, DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS
make crawler                      # mongo + redis + crawler workers, and queue a crawl run
make help                         # every shortcut: logs, queue, mongo-shell, backup, ...
```

Day-to-day commands for Mongo, Redis, the crawler, cron and deploys are in [COMMANDS.md](COMMANDS.md).

### Deploy to Cloud Run

Pushing to `main` deploys automatically (see [CI/CD](#cicd)). For a first deploy, or to change env vars and secrets:

```bash
./scripts/deploy-cloudrun.sh      # needs gcloud signed in to retailpilot-gemini-10162213, plus Docker
```

### Option A: everything locally with Docker

**1. Prepare the settings.** Copy the example file and add your Google Cloud API key. The `.env` file is git-ignored, so keep your key only in it.

```powershell
copy .env.example .env
# then set GOOGLE_API_KEY in .env
```

**2. Build and start the app.** This starts MongoDB, Redis, the backend, the frontend and the MCP server. Add `--profile crawl` to also start the crawler.

```powershell
docker volume create amazon_ecomm_crawler_amazon_crawler_mongo   # first time only
docker compose up -d --build
```

**3. Open the app.**

| Service | URL |
|---|---|
| Web app (landing, retailer, customer) | http://localhost:8080 |
| Backend API docs | http://localhost:8000/docs |
| MCP server | http://localhost:8001 |
| MongoDB (host port) | localhost:27018 |
| Redis (host port) | localhost:6380 |

**4. Load data.** The app needs crawled data to answer questions:

```powershell
docker compose --profile crawl up -d --build crawler-start crawler-worker
```

Set `CRAWL_CITIES` in `.env` to choose cities (slug, name or pincode); leave it empty to crawl all ten.

### Option B: local development

Requires Python 3.13 and a running MongoDB (for example `docker compose up -d mongo`).

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirement-backend.txt -r requirement-frontend.txt -r requirement-crawler.txt -r requirement-dev.txt
```

Set `PYTHONPATH=src` so the `retailpilot` package can be imported, then start each part in its own terminal:

```powershell
uvicorn retailpilot.backend.agent_route:app --port 8000
python src/retailpilot/mcp/mcp_server.py        # set MCP_TRANSPORT=http for HTTP on port 8001
cd src/retailpilot/frontend; python manage.py runserver 8080
```

## Configuration

Settings are read from `.env`. Every key, with safe defaults and comments, is in [`.env.example`](.env.example); the actual values never go into the code. On Cloud Run the same keys are set on the services, with the two secrets in Secret Manager.

| Group | Keys |
|---|---|
| AI | `GOOGLE_API_KEY`, `GEMINI_MODEL` |
| Database | `MONGO_URI`, `MONGO_DB`, `CHAT_DB` |
| Services | `BACKEND_URL` (Docker Compose sets `MCP_TRANSPORT` and `MCP_PORT` for the MCP container) |
| Crawler: source | `AMAZON_BASE_URL`, `AMAZON_SEARCH_INDEX`, `AMAZON_ROOT_NODE` |
| Crawler: scope | `CRAWL_CITIES`, `CRAWL_MAX_PAGES`, `CRAWL_MAX_PRODUCTS`, `CRAWL_MAX_REVIEWS`, `CRAWL_DETAIL_MODE`, `CRAWL_REFRESH_HOURS` |
| Crawler: pace | `CRAWL_MIN_DELAY`, `CRAWL_MAX_DELAY`, `CRAWL_MAX_RETRIES`, `STALE_HOURS` |
| Crawler: browser fallback | `BROWSER_FALLBACK`, `BROWSER_HEADLESS`, `BROWSER_EXECUTABLE`, `BROWSER_MANUAL_WAIT`, `BROWSER_MAX_CONCURRENT` |
| Crawler: workers | `REDIS_URL`, `CRAWL_QUEUE`, `CRAWL_WORKERS`, `CRAWL_PAGES_PER_JOB`, `CRAWL_DETAILS_PER_JOB`, `CRAWL_JOB_TIMEOUT` |
| Web | `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` |

## Repository layout

```
README.md                    this file
COMMANDS.md                  server commands: Mongo, Redis, crawler, cron, deploys
Dockerfile                   one image per service: crawler, mcp, backend, frontend
docker-compose.yml           mongo, redis, backend, frontend, mcp; crawler services in the "crawl" profile
Makefile                     shortcuts for docker compose (`make help`)
.env.example                 every setting the app reads, with safe defaults
.github/workflows/ci-cd.yml  tests, image builds and Cloud Run deploys
scripts/
  deploy-cloudrun.sh         full Cloud Run deploy (first deploy, env vars, secrets)
  start-crawl.sh             starts a crawl run; used by cron
pyproject.toml               project metadata and dependencies
requirement-*.txt            per-service dependencies
amazon_ecomm_crawler/        city-wise Amazon crawler, browser fallback, queue jobs and workers
src/retailpilot/
  agent/                     LangGraph agent, prompts, state, chat history
  backend/                   FastAPI app (agent_route.py)
  config/                    shared settings
  frontend/                  Django web app and templates
  mcp/                       MCP server and its Mongo connection
testing_suites/              pytest suites (see its README)
plans/                       planning notes and build status
```

## Testing

The test suite covers settings, project setup, MCP tools, the chat store, the chatbot and its routing, the backend API, analytics, the Django views, the front-end contract (IDs and CSS classes the scripts rely on), and the crawler parsers. The crawler has its own tests in `amazon_ecomm_crawler/tests`.

```powershell
.venv\Scripts\python.exe -m pytest testing_suites -v
```

MongoDB-backed tests are skipped with a reason if the database is not reachable. Start it with `docker compose up -d mongo` to run them. They only write to throwaway test databases. See [`testing_suites/README.md`](testing_suites/README.md) for details.

## Limitations and next steps

- Data covers Electronics on Amazon.in only. The server crawls 3 of the 10 configured cities, and the data-health page reports how fresh each city is.
- Laptops are reached only through the "Computers & Accessories" subcategory, not as their own category yet.
- Competitor prices depend on what the crawler has collected, so they can lag the live marketplace by up to one crawl run.
- All crawler workers share one IP, so speed is limited by Amazon's bot checks, not by the server. Faster crawling needs proxies.
- Full review pages need a signed-in Amazon account, so only the reviews shown on each product page are stored.
- Margin scenarios use the fee and tax inputs the retailer supplies. They are estimates, not accounting.
