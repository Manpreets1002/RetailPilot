# RetailPilot test suites

Run everything from the project root with the project virtualenv:

```powershell
.venv\Scripts\python.exe -m pytest testing_suites -v
```

Run one area:

```powershell
.venv\Scripts\python.exe -m pytest testing_suites\test_mcp_helpers.py -v
```

## What each file covers

| File | Covers | Needs |
|---|---|---|
| `test_config_and_env.py` | Settings defaults, env overrides, every key in `.env.example` is read by the code | nothing |
| `test_project_setup.py` | `requirement-*.txt` vs `pyproject.toml` vs the imports in the code, installed versions, Dockerfile COPY paths and stages, Compose targets and ports, `cities.json` | nothing |
| `test_mcp_helpers.py` | MCP helper functions: date windows, battery parsing, spec filters, card and margin maths | nothing |
| `test_mcp_tools_integration.py` | Every MCP tool (customer and retailer), run against seeded data | MongoDB |
| `test_chat_history_integration.py` | Threads, messages, shown products and interest in the chat store | MongoDB |
| `test_chatbot_and_wrapper.py` | Role routing, message helpers, waiting-thread rules, MCP wrapper | nothing |
| `test_backend_api.py` | FastAPI endpoints: validation, status codes, recommendations, categories | nothing (chat is faked) |
| `test_analytics.py` | Dashboard maths: margins, peers, price history, gap matrix, sentiment, basket, health; the load query | the load test needs MongoDB |
| `test_frontend_views.py` | Django pages and JSON endpoints, the retailer dashboard, the HTTP client to the backend | nothing (backend and data are faked) |
| `test_frontend_ui_contract.py` | Templates compile, static files exist, every ID and CSS class the scripts use is defined | nothing |
| `test_crawler_parsers.py` | Price, rating, count, ASIN and discount parsers; city selection | nothing |

Tests that need MongoDB are skipped with a reason when it is not reachable. To run them, start the
database first (`docker compose up -d mongo`).

## Safety

MongoDB tests write only to `retailpilot_test_suite` and `retailpilot_test_suite_chat`, and drop both
when they finish. Your real crawl data is never touched.

## Expected failures

None at the moment. The four bugs that used to be marked as expected failures are fixed:

- `customer_best_today` with `observed_on="any"` now returns older offers.
- Marketplace fees of 100% or more are rejected with a clear message, in the dashboard and the MCP tool.
- A failed MCP tool call raises `ToolCallFailed`, which the backend returns as a 502 with a message.
- The root compose file publishes MongoDB on host port 27019, so it can run alongside the crawler's MongoDB on 27018.

## Requirements

The tests read the per-service files at the project root: `requirement-mcp.txt`, `requirement-backend.txt`
(which includes the MCP file), `requirement-frontend.txt`, `requirement-crawler.txt` and `requirement-dev.txt`
(test tools: pytest and httpx). They check that:

- every package the code imports is listed in one of these files;
- a package listed in several files has the same version range in each;
- the installed versions satisfy the ranges.

## Known failures

`test_every_pyproject_dependency_is_in_requirements` fails. `pyproject.toml` lists 11 packages that none of
the service files include: `alembic`, `google-adk`, `google-cloud-secret-manager`, `google-genai`, `pandas`,
`psycopg[binary]`, `pytest`, `python-dotenv`, `ruff`, `sqlalchemy` and `streamlit`. Either add them to a
service file or remove them from `pyproject.toml`.
