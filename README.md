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
| **Teammate A** — Backend, AI & deployment | FastAPI, PostgreSQL models, API contracts, ADK + Gemini agent, search/compare/profit tools, Cloud Run deployment |
| **Teammate B** — Data & frontend | Permitted data collection, normalization, product matching, Streamlit dashboard and shopping UI, price charts |

Work independently against the shared API contracts and sample data, then integrate daily. Swap ownership if one person is clearly stronger in an area, but keep one owner per deliverable.

---

## API Contracts

Agree on these first so both sides can build in parallel.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/products` | Search and filter laptops |
| GET | `/api/products/{id}/compare` | Compare equivalent listings |
| GET | `/api/products/{id}/history` | Timestamped price observations |
| POST | `/api/profit/calculate` | Contribution and margin scenarios |
| POST | `/api/assistant/chat` | Ask the ADK agent a question |

---

## Roadmap

### Step 0 — Freeze scope and contracts (Oct 14)
Confirm the feature list, the API contracts above, and the normalized schema. Share sample JSON so both teammates can start.

### Step 1 — Verify access (Oct 14)
Confirm Gemini/ADK access and Google Cloud deployment permissions. Find an accessible dataset or permitted source. If a source blocks collection, switch to an authorized dataset or clearly labeled sample observations.

### Step 2 — Backend core (Teammate A, Oct 14)
Set up FastAPI, Pydantic models, and PostgreSQL. Implement the product search, compare, and profit endpoints against sample data.

### Step 3 — Catalog and matching (Teammate B, Oct 14)
Normalize product names, model numbers, specs, and prices into one schema. Match equivalent models across sources. Each listing keeps its source URL and observation timestamp.

### Step 4 — Agent with tools (Teammate A, Oct 14)
Build the ADK + Gemini agent with at least two tools (for example, product search and profit calculation). The agent must call tools for any price or product fact.

### Step 5 — Dashboard and shopping UI (Teammate B, Oct 14)
Build the Streamlit retailer dashboard with a working comparison table and price chart, plus the customer shopping view. Use sample responses until the backend is ready.

**Checkpoint (end of Oct 14):** both workstreams run locally with shared sample records.

### Step 6 — Integrate (Oct 15)
Connect the dashboard to the API and the agent to the catalog and calculation tools. Show source URLs, observation timestamps, and price differences.

### Step 7 — Complete the two core flows (Oct 15)
- **Customer flow:** budget and requirements → suitable laptops → comparison → explanation with source links.
- **Retailer flow:** enter cost → see price gaps → calculate margin → AI explains the recommendation.

### Step 8 — Deploy (Oct 15)
Dockerize the app, store secrets in Secret Manager, deploy to Cloud Run, and verify the public URL reaches the backend and database.

**Checkpoint (end of Oct 15):** a complete prototype runs at a public URL. **Feature freeze.**

### Step 9 — Test data and AI behavior (Oct 16)
- Check that matched products are truly equivalent.
- Check margin math by hand.
- Test no-result queries, missing specs, stale prices, and API failures.
- Confirm the agent uses tools and does not invent prices or specs.

### Step 10 — Fix and stabilize (Oct 16–17)
Fix deployment, dependency, and config errors. Fix critical bugs only. No new features.

### Step 11 — Submission materials (Oct 17)
- Architecture diagram and solution proposal.
- README with setup and deployment steps (this file, updated).
- Demo video of three minutes or less, if the rules require it.
- Test every public link and artifact.

### Step 12 — Submit (Oct 18)
Check the official submission checklist, verify the live app once more, and submit early. Avoid last-minute changes.

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
- [ ] Demo and proposal describe only implemented features

---

## Metrics We Report

- Product-match correctness
- Calculation accuracy
- Agent tool-use reliability

We do not claim sales or profit uplift.

---

## Setup

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run retailpilot
```

Setup and run instructions will be expanded as the backend and frontend land.
