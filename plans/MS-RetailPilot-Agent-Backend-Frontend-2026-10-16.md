# MS: RetailPilot agent, backend, frontend and Docker, 2026-10-16

Status of today's build against `Plan of Action.md`. Written from what is in the repo and what was run.

---

## What we built today

### 1. Amazon crawler (run and extended)
- Ran the existing crawler against Delhi. Full runs were stopped and restarted as requested. Current data: 18 top-level Electronics categories, 5 products each, plus smaller test runs.
- Found that laptops sit under a sub-node that the top-level crawl does not reach (node `1375424031`). Not crawled yet.
- Crawler settings now come from the shared `Settings` class (see section 6).

### 2. MCP server (`src/retailpilot/mcp/`)
- FastMCP server, fully async (`AsyncMongoClient`), read-only tools.
- **20 tools**, named by role:
  - Shared: `resolve_city`, `list_categories`, `list_cities`, `search_products`, `get_product`, `get_reviews`, `search_reviews`, `get_price_history`.
  - Customer: `customer_product_insights`, `customer_compare_products`, `customer_best_today`.
  - Retailer: `retailer_price_position`, `retailer_competitor_prices`, `retailer_price_trend`, `retailer_price_gap_heatmap`, `retailer_margin_scenarios`, `retailer_review_sentiment`, `retailer_market_basket`, `retailer_category_overview`, `retailer_data_health`.
- Every offer and price is tied to the IST day it was observed. Search defaults to today; older days need an explicit `observed_on`.
- `customer_best_today` scores products (rating, review count, price) and returns a random pick from the top candidates. It excludes products already shown in that session.

### 3. Agent (`src/retailpilot/agent/`)
- LangGraph graph: a **router** picks customer or retailer each turn (the API can override it), and each role gets its own system prompt and tool set.
- Tools are limited by name prefix: a customer cannot call a retailer tool, and a call that breaks the rule is refused.
- `ask_user` pauses the graph with `interrupt()`. The answer resumes it.
- Review step checks the answer against the question (pricing weighted highest, 80% coverage). One retry at most.
- Storage in MongoDB database `chatbot`:
  - `threads` (title, role, pending question, timestamps), `messages` (every user and assistant message, with the routed role).
  - `shown_products` (what each shopper was shown, and buy or skip).
  - `checkpoints` and `checkpoint_writes` (LangGraph state, via `langgraph-checkpoint-mongodb`).
- The Gemini call is written, but **not yet run with a real key** (see Open items).

### 4. FastAPI backend (`src/retailpilot/backend/agent_route.py`)
- `POST /chat`, `POST /chat/{thread_id}/answer`, `GET /threads`, `GET /threads/{id}/messages`.
- `GET /cities`, `GET /categories`, `GET /recommendations`, `GET /recommendations/shown`, `POST /recommendations/interest`, `GET /health`.
- Conflicts return 409. Unknown shown products return 404.

### 5. Django frontend (`src/retailpilot/frontend/`)
- Replaced the Streamlit plan with Django, as requested.
- **Landing page:** white and dark blue, scrollable, explains RetailPilot, asks retailer or customer, footer "Made by Wi-Fight Club".
- **Retailer dashboard:** the five analyses from the `gaurvit-retailer-dashboard-base` branch, ported to plain Python (`web/analytics.py`), plus an "Ask RetailPilot" tab. Slate and teal theme.
- **Customer shop:** ChatGPT-style layout. Threads on the left, "New chat", category picks (5 at a time, new ones on request), an ⓘ panel listing what was shown with buy or skip, a 5-minute idle prompt, light and dark toggle.
- Django proxies call the FastAPI backend, so the browser never talks to it directly.

### 6. Configuration and Docker
- **One `Settings` class** in `src/retailpilot/config/config.py` (pydantic-settings). It reads the repo-root `.env` and the environment. Credentials default to `None`. Other values have local-development defaults.
- The `load_dotenv` and `os.getenv` calls in the MCP code, agent, Django settings, and crawler were replaced with `Settings`.
- Requirements split: `requirement-mcp.txt`, `requirement-backend.txt` (includes MCP), `requirement-frontend.txt`, `requirement-crawler.txt`.
- One `Dockerfile` with four targets (`crawler`, `mcp`, `backend`, `frontend`), a `.dockerignore`, `docker-compose.yml` (MongoDB, backend, frontend, plus crawler and MCP under the `tools` profile), and one root `.env.example`.
- Images built and smoke-tested earlier today. Image sizes: backend 567 MB, frontend 702 MB (scikit-learn and numpy), crawler 267 MB, MCP 247 MB.

---

## Plan of Action: status

| Step | Plan item | Status | Notes |
|---|---|---|---|
| 0 | Freeze scope and contracts | Partial | Endpoints exist, but the contracts differ from the plan (MongoDB and chat/recommendation endpoints). No `data/sample/` JSON yet. |
| 1 | Verify access (Gemini, ADK, Cloud Run) | Not done | Gemini key is not set in the Python environment, so no live call yet. No ADK, no Cloud Run deploy. |
| 2 | Backend core (SQLAlchemy, PostgreSQL, services) | Changed | Data is in MongoDB (crawler). Services exist as MCP tools instead of `services/`. No PostgreSQL. |
| 3 | Catalog and matching | Not done | Matching by model number or fingerprint is not built. Listings are not matched across retailers. |
| 4 | Agent with tools | Partial | LangGraph agent with tools via MCP, not ADK. Ask-user, router, review retry are built. Not run with a real LLM. |
| 5 | Dashboard and shopping UI | Done (Django, not Streamlit) | Pages render and pass system checks. Not tested in a browser. |
| 6 | Automated test suites | Partial | Crawler tests pass (10). No pytest suite for MCP, agent, backend, or frontend yet. |
| 7 | Integrate | Partial | Frontend calls the backend through proxies. Data is shown from Mongo for the retailer page. |
| 8 | Complete the two core flows | Partial | Customer flow needs today's crawl data and a Gemini key. Retailer margin flow works on the data. |
| 9 | Deploy (Docker, Cloud Run) | Partial | Dockerfile and compose written, images built. Not deployed. |
| 10 | Data and AI behaviour checks | Not done | No labelled matching set, no grounding checks. |
| 11 | Fix and stabilize | Not done | |
| 12 | Submission materials | Not done | |
| 13 | Submit | Not done | |

---

## Open items

1. **Gemini key:** add `GOOGLE_API_KEY` to the root `.env`. Until then the chat fails, and the title and router calls with it.
2. **Live crawl for customer picks:** `customer_best_today` only uses offers observed today. Run the crawler on the current day for picks to appear.
3. **Laptops:** crawl node `1375424031` so the laptop questions have data.
4. **Docker rebuild:** the `Settings` change needs one rebuild of all four images before the containers are used.
5. **Browser check:** open the landing, retailer and customer pages in a browser. The customer nudge timer and the light and dark toggle have not been seen on screen.
6. **Tests:** add pytest coverage for the MCP tools, agent routing and tool restriction, backend routes, and the Django views (plan Step 6).
7. **Deviations from the plan:** Django instead of Streamlit, MongoDB instead of PostgreSQL, and LangGraph instead of ADK. Update the Plan of Action or decide whether to switch back before submission.

---

## Commands

```powershell
# backend (FastAPI)
.venv\Scripts\python.exe -m uvicorn retailpilot.backend.agent_route:app --reload

# frontend (Django)
.venv\Scripts\python.exe src\retailpilot\frontend\manage.py runserver

# crawler (example)
cd amazon_ecomm_crawler
..\.venv\Scripts\python.exe crawler.py --city delhi --pages 1 --products 5

# docker (after the rebuild)
docker compose up -d mongo backend frontend
docker compose --profile tools up -d mcp      # MCP server over HTTP on port 8001
```

---

## Health checks (curl)

Run these after `docker compose up`. Expected results are in the comments.

```bash
# Backend (FastAPI, port 8000)
curl -s http://127.0.0.1:8000/health                       # {"status":"ok"}
curl -s http://127.0.0.1:8000/cities                       # city list, delhi covered:true
curl -s http://127.0.0.1:8000/categories                   # categories with product counts
curl -s "http://127.0.0.1:8000/threads?limit=3"            # [] on a fresh database
curl -s -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" \
     -d '{"message":"hi","role":"customer"}'               # 503 until GOOGLE_API_KEY is set
curl -s "http://127.0.0.1:8000/recommendations?session_id=test&category=Accessories"   # empty items if no crawl today

# Frontend (Django, port 8080)
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/      # 200

# MCP server (streamable HTTP, port 8001)
curl -s -X POST http://127.0.0.1:8001/mcp -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
                                                             # event: message / data: {...result...}
```

A plain `GET /mcp` returns 406. That is expected, because the MCP endpoint needs the `Accept` header above.
