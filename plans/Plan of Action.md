# RetailPilot

AI-powered electronics market intelligence and shopping assistant, built for the Google AI Builder Cup (Retail & Commerce track).

RetailPilot has two user experiences on one platform:

- **Retailers** compare equivalent laptop models across online retailers, track advertised prices, find pricing gaps, calculate margins from their own cost data, and get evidence-backed recommendations.
- **Customers** search laptops by budget and needs, compare offers, and get recommendations grounded in the actual catalog, with links to the source listing.

We are not building another price-comparison site or scraper. The value is turning market data into business decisions and shopping advice.

**Scope (MVP):** laptops only.

**Data limits:** public listings show advertised prices and some availability signals. They do not show competitors' sales, margins, or warehouse stock. Profit numbers come only from the retailer's own cost inputs.

**Prototype deadline:** October 18, 2026. Feature freeze: October 15.

---

## Architecture

```
Streamlit frontend  ──►  FastAPI on Cloud Run  ──►  Google ADK + Gemini (agent)
(dashboard, compare,                │                    │
 shopping assistant)                ▼                    ▼
                          PostgreSQL catalog       Market / Shopping / Profit / Guardrail tools
                          (products, listings,            │
                           prices, costs)  ◄──────────────┘
                                ▲
                  Data pipeline (HTTPX, BeautifulSoup, Pandas)
```

- **Frontend:** Streamlit. Calls the API only, never the database.
- **Backend:** FastAPI + Pydantic. Owns endpoints, validation, and auth where needed.
- **Agent:** Google ADK + Gemini. Answers only by calling backend tools.
- **Data:** PostgreSQL with a normalized product/listing/price schema, filled by the pipeline.
- **Deployment:** Docker on Google Cloud Run. Secrets in Google Cloud Secret Manager.
- **Development tools:** Google Antigravity and GitHub. Antigravity is for building only, not a runtime component.

Keep it simple. No microservices, Kubernetes, or multi-agent setups before the deadline.

---

## Team Split

| Owner | Responsibilities |
|---|---|
| Owner | Responsibilities |
|---|---|
| **Sarthak** — Backend | FastAPI, PostgreSQL models, API contracts, search/compare/profit tools, backend deployment (Docker, Cloud Run, Cloud SQL) |
| **Gaurvit & Manpreet** — AI, data & frontend | ADK + Gemini agent and tools, assistant endpoint, data collection, normalization, product matching, Streamlit dashboard and shopping UI, price charts |
| **Gaurvit** — Testing | Automated test suites (Step 6), manual data and AI QA (Step 10), final QA sign-off |

Work independently against the shared API contracts and sample data, then integrate daily. Each deliverable has one accountable owner.

---

## Repository Layout

```
RetailPilot/
├── src/retailpilot/
│   ├── api/            # FastAPI app, routers, Pydantic schemas
│   ├── db/             # SQLAlchemy models, session, seed loader
│   ├── pipeline/       # collectors, normalizers, matcher
│   ├── agent/          # ADK agent definition and tool functions
│   ├── services/       # pure logic: search, compare, profit math
│   └── config.py       # pydantic-settings, reads env vars
├── frontend/           # Streamlit app (dashboard.py, shop.py)
├── data/sample/        # seed JSON used before real collection works
├── tests/              # pytest: matching, margin math, endpoints, agent
├── Dockerfile
├── requirements.txt
└── README.md
```

Keep business logic in `services/`, not in routers or agent tools. The API, the agent, and the tests can then all call the same functions.

---

## Core Data Model

- **`products`**: `id`, `canonical_name`, `brand`, `model_number`, `cpu`, `ram_gb`, `storage_gb`, `gpu`, `screen_in`, `weight_kg`, `fingerprint` (hash of key specs, used for matching).
- **`retailers`**: `id`, `name`, `market` (e.g., IN, SG), `currency`.
- **`listings`**: `id`, `product_id` (nullable until matched), `retailer_id`, `source_url`, `raw_title`, `match_confidence`.
- **`price_observations`**: `id`, `listing_id`, `price`, `currency`, `in_stock` (nullable), `observed_at`. Append-only, so price history comes for free.
- **`retailer_costs`**: `id`, `retailer_id`, `product_id`, `unit_cost`, `fees_pct`, `updated_at`. The only source for margin calculations.

Every price returned by the API must carry `source_url` and `observed_at`.

---

## API Contracts

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/products` | Search and filter laptops |
| GET | `/api/products/{id}/compare` | Compare equivalent listings |
| GET | `/api/products/{id}/history` | Timestamped price observations |
| POST | `/api/profit/calculate` | Contribution and margin scenarios |
| POST | `/api/assistant/chat` | Ask the ADK agent a question |

---

## Roadmap

### Step 0: Freeze scope and contracts (Oct 14)

**Build:**
- The OpenAPI spec generated by FastAPI, plus sample JSON for each endpoint in `data/sample/`.

**Contract shapes:**
- `GET /api/products?q=&max_price=&min_ram=&use_case=` returns `[{id, name, specs, best_price, currency, sources:[{retailer, price, source_url, observed_at}]}]`.
- `GET /api/products/{id}/compare` returns the listings side by side with a `price_gap` field.
- `GET /api/products/{id}/history?days=30` returns `[{observed_at, retailer, price}]`.
- `POST /api/profit/calculate` takes `{product_id, retailer_id, unit_cost, target_price, units}` and returns `{contribution, margin_pct, breakeven_price}`.
- `POST /api/assistant/chat` takes `{message, session_id}` and returns `{answer, tool_calls:[...], sources:[...]}`.

**Done when:** both teammates can build against the sample JSON without waiting on each other.

### Step 1: Verify access (Oct 14)

**Build:**
- A script that calls the Gemini API with one prompt, confirming the key works.
- An ADK hello-world agent that calls one trivial Python function.
- A "hello" container deployed once to Cloud Run, confirming project permissions.

**Done when:** the Gemini call, the ADK tool call, and the Cloud Run deploy have each succeeded once. Any blocker is found now.

### Step 2: Backend core (Sarthak, Oct 14)

**Build:**
- `db/models.py`: SQLAlchemy models for the tables above.
- `db/seed.py`: loads `data/sample/*.json` into PostgreSQL.
- `services/search.py`: filters by budget, RAM, storage, and use case.
- `services/compare.py`: for one product, returns each listing's latest price and the gap from the lowest price.
- `services/profit.py`: computes margin.
- `api/routers/products.py` and `api/routers/profit.py`: thin wrappers around the services.

**Margin formulas:**
- `contribution = target_price - unit_cost - (target_price × fees_pct)`
- `margin_pct = contribution / target_price × 100`
- `breakeven_price = unit_cost / (1 - fees_pct)`

**Done when:** endpoints return the sample data, and pytest covers the margin formulas with hand-computed values.

### Step 3: Catalog and matching (Gaurvit & Manpreet, Oct 14)

**Build:**
- `pipeline/normalize.py`: cleans raw titles (lowercase, strip marketing words) and extracts brand, model number, CPU, RAM, storage, and screen size with regexes.
- `pipeline/matcher.py`, which assigns each listing to a product:
  1. Exact match on normalized model number, if present.
  2. Otherwise, compare the spec fingerprint (CPU + RAM + storage + GPU + screen size).
  3. Score the candidate. Above a threshold, match. Below it, leave the listing unmatched and flag it for review.
- Store `match_confidence` so the UI and evaluation can show it.

**Done when:** the matcher produces the expected matches on sample titles and leaves ambiguous listings unmatched. These are pytest cases.

### Step 4: Agent with tools (Gaurvit & Manpreet, Oct 14)

**Build:**
- `agent/tools.py`: plain Python functions with type hints and docstrings, which ADK uses as tool schemas:
  - `search_laptops(query, max_price, min_ram_gb, use_case)`
  - `compare_prices(product_id)`
  - `calculate_margin(product_id, retailer_id, unit_cost, target_price)`
- `agent/agent.py`: an ADK `LlmAgent` on a Gemini model (name set via environment variable). Instructions require a tool call for every price or spec and forbid invented numbers.
- `api/routers/assistant.py`: `POST /api/assistant/chat` runs the agent and returns the answer plus the recorded `tool_calls`.

**Done when:** a test sends "laptop under $1000 with 16 GB RAM," the logged tool calls include `search_laptops`, and the answer's prices match the tool output.

### Step 5: Dashboard and shopping UI (Gaurvit & Manpreet, Oct 14)

**Build:**
- `frontend/dashboard.py` (retailer view): product table, price comparison for the selected product, a history chart with one line per retailer, and a form for unit cost and target price that calls `/api/profit/calculate`.
- `frontend/shop.py` (customer view): filters for budget, RAM, storage, and use case; result cards with price, source link, and observation time; and a chat box that calls `/api/assistant/chat`.
- `api_client.py`: a small `httpx` wrapper with the base URL read from an environment variable.

**Done when:** both pages render from sample JSON in a mock mode, and the API base URL can be switched without code changes.

**Checkpoint (end of Oct 14):** matching, the agent, and the UI all run locally with the shared sample records.

### Step 6: Automated test suites (Gaurvit, Oct 14–15)

Write tests alongside each component, not at the end. Every step after this one runs the suite before merging.

**Build:**
- `tests/unit/`: pure-logic tests with no database or network.
  - `normalize.py`: title cleaning and spec extraction on sample titles.
  - `matcher.py`: exact model-number match, fingerprint match, and unmatched ambiguous listings.
  - `profit.py`: margin formulas against hand-computed values.
  - `search.py`: budget, RAM, storage, and use-case filters.
- `tests/integration/`: FastAPI endpoints through `TestClient`, backed by a PostgreSQL test database (the same engine as production) loaded from `data/sample/`. Cover success, empty results, invalid input (422), and missing products (404).
- `tests/agent/`: tool-calling tests with a mocked Gemini model, so results are deterministic. Check that the right tool is called with the right arguments, and that tool errors are handled.
- `tests/frontend/`: smoke tests with Streamlit's `AppTest`, confirming both pages render and the form submits.
- `tests/live/` (marked `@pytest.mark.live`): a few end-to-end tests against the real Gemini API. Skipped unless the API key is set, so the default run needs no network.
- Shared fixtures in `tests/conftest.py`: database session, sample data loader, and the mocked agent model.
- Add `pytest` and `pytest-cov` to `requirements.txt`.

**Commands:**
- `uv run pytest`: full suite (live tests skipped without a key).
- `uv run pytest -m "not live"`: fast local run.
- `uv run pytest --cov=retailpilot --cov-report=term-missing`: coverage report. Aim for at least 80% on `services/` and `pipeline/`.

**Done when:** the suite runs in one command, passes on a clean clone, and covers every item above. A failing test blocks the step from being marked done.

### Step 7: Integrate (Oct 15)

**Build:**
- Point the Streamlit `api_client` at the real FastAPI server.
- Keep mock mode for tests only.
- Show the source link and `observed_at` on every price. Add a "stale" flag when `observed_at` is older than a configurable threshold.

**Done when:** a search in the UI returns the same numbers as a direct API call.

### Step 8: Complete the two core flows (Oct 15)

- **Customer flow:** budget and needs → `search_laptops` → comparison card → assistant explanation with source links.
- **Retailer flow:** choose product → enter cost → `calculate_margin` → assistant explains the result using the returned numbers.

**Build:** make each flow work end to end from the UI, and add an error state for each call (timeout, no results, missing cost).

**Done when:** each flow runs without manual steps, and the assistant's explanation contains only numbers that appear in the tool output.

### Step 9: Deploy (Sarthak, Oct 15)

**Build:**
- A `Dockerfile` that runs FastAPI and Streamlit (one container if possible).
- Secrets (`GEMINI_API_KEY`, `DATABASE_URL`) read from environment variables, filled by Cloud Run from Secret Manager.
- PostgreSQL (Cloud SQL, or an external Postgres if Cloud SQL is slow to set up), with the seed script run once.
- `gcloud run deploy` with the environment configured.

**Done when:** the public URL loads the UI, a search returns data, the assistant responds, and no keys or `.env` files are in the repo (check with `git grep`).

**Checkpoint (end of Oct 15):** the public prototype works. **Feature freeze.**

### Step 10: Test data and AI behavior (Gaurvit, Oct 16)

Builds on the automated suite from Step 6. These checks need human judgment and real data.

- **Matching:** label 30 to 50 listings by hand and compute precision and recall.
- **Math:** check five margin scenarios against a spreadsheet.
- **Failures:** empty search, product with no specs, price older than the threshold, database down, Gemini API error.
- **Agent grounding:** for 10 assistant queries, check that every number in the answer appears in a tool result.

**Done when:** each test has a recorded result. Report the numbers, even if imperfect.

### Step 11: Fix and stabilize (Oct 16–17)

- Fix deployment, dependency, and config errors first.
- Fix critical bugs only. Anything else waits.
- Add a regression test for every bug fixed, and run the full suite before each deploy.
- Pin dependency versions in `requirements.txt` after the last working install (`pip freeze`), and confirm `uv.lock` matches.

**Done when:** a fresh clone runs setup from this README on a clean machine.

### Step 12: Submission materials (Oct 17)

- Architecture diagram (Streamlit → FastAPI → ADK/Gemini → PostgreSQL).
- Solution proposal, drawing on the Metrics section below and the Step 10 numbers.
- Test results from the suite (pass count and coverage) for the proposal.
- Setup and deploy steps in this README, filled in from what was actually run.
- Demo video of three minutes or less, following the customer and retailer flows.
- Click every public link and open every submission artifact.

### Step 13: Submit (Oct 18)

- Check the official submission checklist line by line.
- Load the live URL once and run both flows.
- Submit early. After submitting, change nothing that affects the running app.

---

## Release Checklist

- [ ] Product matching compares equivalent laptop configurations
- [ ] Every displayed market price has a source and observation time
- [ ] Price differences and margin calculations pass manual tests
- [ ] Gemini/ADK invokes the intended backend tools
- [ ] Assistant answers are grounded in catalog records
- [ ] Missing data and source failures are handled gracefully
- [ ] Deployed frontend reaches the backend and database
- [ ] No secrets or API keys in the repository
- [ ] Public URL and submission links work
- [ ] Automated test suite passes on a clean clone
- [ ] Demo and proposal describe only implemented features

---

## Metrics We Report

- Product-match correctness
- Calculation accuracy
- Agent tool-use reliability

We do not claim sales or profit uplift.

---

## Setup

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/). Dependencies for the prototype are listed in `requirements.txt`.

```bash
uv sync
uv run retailpilot
```

Setup and run instructions will be expanded as the backend and frontend land.
