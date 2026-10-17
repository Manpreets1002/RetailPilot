# RetailPilot

**An AI retail intelligence platform for laptop retailers and their shoppers.**

RetailPilot turns live Amazon.in price and review data into two AI assistants:

- **Retailer copilot.** Compares our prices with competitors city by city, tests margin scenarios, reads review sentiment, and finds what sells together. It answers pricing questions with numbers taken from the database, not from the model's memory.
- **Customer shopping assistant.** Helps a shopper find and compare laptops in their own city, using today's prices, ratings and reviews. It asks clarifying questions when a request is vague.

Both assistants run on the same data and the same tools, so a retailer's pricing answer and a shopper's recommendation agree with each other.

---

## The problem

Laptop prices on Amazon.in change daily and differ between cities. A retailer who wants to price well has to check many listings by hand and guess at margins. A shopper has to read hundreds of reviews to choose between similar models. Neither gets a clear, current, city-specific answer.

## Our solution

1. **Collect.** A city-by-city crawler reads the Electronics catalogue on Amazon.in. For each city it records the delivery pincode, price, stock, seller and delivery date. It also records product specs, ratings, and reviews.
2. **Store.** MongoDB keeps product facts once, and city-specific offers and daily price history separately, so every price is tied to the city and the day it was seen.
3. **Expose.** An MCP server offers 20 read-only tools, split into shared, customer and retailer tools. Each tool is a small, tested function over the database.
4. **Reason.** A LangGraph agent, powered by Gemini, decides which tools to call. It keeps customer and retailer tools separate, and it pauses to ask the user when it needs more information.
5. **Show.** A Django web app offers a landing page, a retailer dashboard, and a customer page with the assistant. It talks to a FastAPI backend, which runs the agent.

```
 Amazon.in ──► Crawler (curl_cffi, per city) ──► MongoDB
                                                   ▲   │
                                                   │   ▼
 Browser ──► Django frontend ──► FastAPI backend ──► LangGraph agent (Gemini)
                 :8080               :8000               │
                                                         ▼
                                                 MCP server (FastMCP, :8001)
                                                 20 read-only tools ──► MongoDB
```

## Key features

**For retailers**
- Price position against competitors in the same city
- Competitor price list and 14-day price trend
- Price-gap heatmap across a category
- Margin scenarios, with GST and marketplace fees
- Review sentiment for a product
- Market basket analysis: which products are reviewed by the same customers
- Data health check: how fresh the data is for each city

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
| AI model | Google Gemini 2.5 Flash (default, set by `GEMINI_MODEL`), through `langchain-google-genai` |
| Agent | LangGraph and LangChain Core |
| Tool server | FastMCP (Model Context Protocol), streamable HTTP transport |
| Backend API | FastAPI, served by Uvicorn |
| Frontend | Django 6, served by Gunicorn with WhiteNoise for static files |
| Database | MongoDB 7 (PyMongo, async and sync clients) |
| Crawler | curl_cffi (browser-like TLS fingerprint), BeautifulSoup and lxml |
| Analytics helpers | NumPy, scikit-learn, Plotly |
| Config | pydantic and pydantic-settings, read from `.env` |
| Packaging and containers | `uv` project file, one Dockerfile with four build stages, Docker Compose |
| Tests | pytest, httpx |

### AI models and external services

- **Gemini 2.5 Flash** does the reasoning and tool choice. It needs a Google AI API key in `GOOGLE_API_KEY`. The model is set by `GEMINI_MODEL`, and the temperature is 0 for repeatable answers.
- **Amazon.in** is the data source. The crawler reads public pages, waits 2 to 5 seconds between requests, backs off on bot checks, and skips any URL that `robots.txt` disallows. The model has no live access to Amazon; it only reads what the crawler has stored.

## Repository layout

```
RetailPilot.md               this file
Dockerfile                   one image per service: crawler, mcp, backend, frontend
docker-compose.yml           mongo, backend, frontend, mcp (and crawler in the "tools" profile)
.env.example                 every setting the app reads, with safe defaults
pyproject.toml               project metadata and dependencies
requirement-*.txt            per-service pinned dependencies
amazon_ecomm_crawler/        city-wise Amazon crawler and its cities.json
src/retailpilot/
  agent/                     LangGraph agent, prompts, state, chat history
  backend/                   FastAPI app (agent_route.py)
  config/                    shared settings
  frontend/                  Django web app and templates
  mcp/                       MCP server and its Mongo connection
testing_suites/              pytest suites (see its README)
plans/                       planning notes and build status
```

## How to run

### Option A: Docker (recommended)

**1. Prepare the settings.** Copy the example file and add your Google AI key. The `.env` file is git-ignored, so keep your key only in it.

```powershell
copy .env.example .env
# then set GOOGLE_API_KEY in .env
```

**2. Build and start the app.** This starts MongoDB, the backend, the frontend and the MCP server.

```powershell
docker compose up -d --build
```

**3. Open the app.**

| Service | URL |
|---|---|
| Web app (landing, retailer, customer) | http://localhost:8080 |
| Backend API docs | http://localhost:8000/docs |
| MCP server | http://localhost:8001 |
| MongoDB (host port) | localhost:27018 |

**4. Load data.** The app needs crawled data to answer questions. Run the crawler for one or more cities:

```powershell
python .\amazon_ecomm_crawler\crawler.py --city delhi mumbai
```

Use `--city` with a slug, a name, or a pincode. Leave it out to crawl all ten cities.

### Option B: Local development

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

Settings are read from `.env`. Names and purposes are listed in `.env.example`; the actual values never go into the code.

| Group | Keys |
|---|---|
| AI | `GOOGLE_API_KEY`, `GEMINI_MODEL` |
| Database | `MONGO_URI`, `MONGO_DB`, `CHAT_DB` |
| Services | `BACKEND_URL` (Docker Compose sets `MCP_TRANSPORT` and `MCP_PORT` for the MCP container) |
| Crawler | `AMAZON_BASE_URL`, `AMAZON_SEARCH_INDEX`, `AMAZON_ROOT_NODE`, `CRAWL_MIN_DELAY`, `CRAWL_MAX_DELAY`, `CRAWL_MAX_RETRIES`, `CRAWL_MAX_PAGES`, `CRAWL_MAX_PRODUCTS`, `CRAWL_MAX_REVIEWS`, `STALE_HOURS` |
| Web | `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` |

## Testing

The test suite covers settings, project setup, MCP tools, the chat store, the chatbot and its routing, the backend API, analytics, the Django views, the front-end contract (IDs and CSS classes the scripts rely on), and the crawler parsers.

```powershell
.venv\Scripts\python.exe -m pytest testing_suites -v
```

MongoDB-backed tests are skipped with a reason if the database is not reachable. Start it with `docker compose up -d mongo` to run them. See [`testing_suites/README.md`](testing_suites/README.md) for details.

## Limitations and next steps

- Data covers Electronics on Amazon.in only, for ten Indian cities. Coverage differs by city, and the data-health tool reports how fresh it is.
- Laptops sit in a sub-category that the top-level crawl does not reach yet.
- Competitor prices depend on what the crawler has collected, so they can lag the live marketplace.
- Margin scenarios use the fee and tax inputs the retailer supplies. They are estimates, not accounting.