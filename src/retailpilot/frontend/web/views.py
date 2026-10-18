import json
import uuid

from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
import plotly.graph_objects as go

from web import analytics
from web.backend import BackendError, get, open_stream, post

GREEN = "#186E43"
TEAL = "#139F83"
AMBER = "#E9A33E"
RED = "#D84F5F"
PALETTE = [GREEN, TEAL, AMBER, RED, "#647CE8", "#8E73D2"]

CUSTOMER_ASSISTANT = {
    "role": "customer",
    "citySelect": "city",
    "greeting": "Hi! Ask me about a product's price, its specs, pros and cons, or how past prices looked.",
    "history": True,
}


def landing(request: HttpRequest):
    return render(request, "web/landing.html")


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


# ----------------------------------------------------------------- charts (Plotly figures as JSON for the browser)

def _chart(fig: go.Figure, height: int, margin: dict | None = None) -> dict:
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        colorway=PALETTE,
        height=height,
        margin=margin or {"l": 50, "r": 20, "t": 20, "b": 40},
        font={"family": "Inter, Segoe UI, system-ui, sans-serif", "color": "#353B50", "size": 12},
        hoverlabel={"bgcolor": "#191D39", "font": {"color": "#FFFFFF", "size": 12}},
    )
    fig.update_xaxes(gridcolor="rgba(104, 112, 134, .12)", zerolinecolor="rgba(104, 112, 134, .18)")
    fig.update_yaxes(gridcolor="rgba(104, 112, 134, .12)", zerolinecolor="rgba(104, 112, 134, .18)")
    return json.loads(fig.to_json())


MUTED_BAR = "#B9D9CB"
MIXED = "#B8B5AA"


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _trend_chart(history: list[tuple[str, float]]) -> dict:
    dates = [d for d, _ in history]
    prices = [p for _, p in history]
    fig = go.Figure(go.Scatter(
        x=dates, y=prices, mode="lines+markers",
        line={"color": GREEN, "width": 3}, marker={"size": 8, "color": GREEN, "line": {"color": "#FFFFFF", "width": 2}},
        hovertemplate="%{x}<br>Price ₹%{y:,.0f}<extra></extra>",
    ))
    # Label the newest price so the reader does not have to hunt for "where are we now".
    fig.add_annotation(x=dates[-1], y=prices[-1], text=f"<b>Latest ₹{prices[-1]:,.0f}</b>", showarrow=False,
                       xanchor="left", xshift=10, font={"color": GREEN, "size": 13})
    # Dates are labels, not times, and the price axis is padded so small moves are visible without repeated ticks.
    low, high = min(prices), max(prices)
    pad = max((high - low) * 0.25, 2)
    fig.update_xaxes(type="category")
    fig.update_yaxes(title_text="Price (INR)", tickprefix="₹", tickformat=",.0f", nticks=5,
                     range=[low - pad, high + pad])
    return _chart(fig, 300, {"l": 70, "r": 110, "t": 20, "b": 50})


def _category_chart(rows: list[dict], selected: str | None) -> dict:
    # Cheapest on top, so the eye reads down from low to high. The category of the selected product is highlighted.
    rows = sorted(rows, key=lambda r: r["avg_price"])
    fig = go.Figure(go.Bar(
        x=[r["avg_price"] for r in rows],
        y=[_short(r["category"], 30) for r in rows],
        orientation="h",
        marker_color=[GREEN if r["category"] == selected else MUTED_BAR for r in rows],
        text=[f"₹{r['avg_price']:,.0f}" for r in rows],
        textposition="outside", cliponaxis=False,
        customdata=[[r["category"], r["products"]] for r in rows],
        hovertemplate="%{customdata[0]}<br>Average ₹%{x:,.0f}<br>%{customdata[1]} products<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed", automargin=True)
    fig.update_xaxes(title_text="Average price (INR)", tickprefix="₹", tickformat=",.0f",
                     range=[0, max(r["avg_price"] for r in rows) * 1.18])
    fig.update_traces(width=0.62)
    return _chart(fig, max(260, 30 * len(rows) + 70), {"l": 10, "r": 10, "t": 10, "b": 50})


def _competitor_chart(sel: dict, peers: list[dict]) -> dict:
    rows = [(sel["price"], sel["title"], True)] + [(p["price"], p["title"], False) for p in peers]
    rows.sort(key=lambda r: r[0])
    fig = go.Figure(go.Bar(
        x=[r[0] for r in rows],
        y=[_short(r[1] or "Unnamed product", 30) for r in rows],
        orientation="h",
        marker_color=[GREEN if r[2] else MUTED_BAR for r in rows],
        text=[f"₹{r[0]:,.0f}" + (" · yours" if r[2] else "") for r in rows],
        textposition="outside", cliponaxis=False,
        hovertemplate="%{customdata}<br>₹%{x:,.0f}<extra></extra>",
        customdata=[r[1] or "Unnamed product" for r in rows],
    ))
    fig.update_yaxes(autorange="reversed", automargin=True)
    fig.update_xaxes(title_text="Price (INR)", tickprefix="₹", tickformat=",.0f",
                     range=[0, max(r[0] for r in rows) * 1.3])
    fig.update_traces(width=0.62)
    return _chart(fig, max(240, 30 * len(rows) + 70), {"l": 10, "r": 10, "t": 10, "b": 50})


def _margin_chart(scenarios: list[dict]) -> dict:
    fig = go.Figure(go.Bar(
        x=[s["scenario"] for s in scenarios],
        y=[s["contribution"] for s in scenarios],
        marker_color=[TEAL if s["contribution"] >= 0 else RED for s in scenarios],
        hovertemplate="%{x}<br>₹%{y:,.0f} per unit<extra></extra>",
    ))
    fig.update_yaxes(title_text="Contribution per unit (INR)")
    return _chart(fig, 300)


def _sentiment_chart(product_sent: dict) -> dict:
    positive = product_sent["positive_pct"]
    negative = product_sent["negative_pct"]
    mixed = max(0.0, round(100 - positive - negative, 1))
    # One 100% bar, split into three labelled parts. Small parts drop their inside label; the hover and legend still name them.
    fig = go.Figure()
    for name, value, color, text_color in (
        ("Positive", positive, GREEN, "#FFFFFF"),
        ("Mixed", mixed, MIXED, "#1F2235"),
        ("Negative", negative, RED, "#FFFFFF"),
    ):
        fig.add_trace(go.Bar(
            name=name, x=[value], y=[""], orientation="h", marker_color=color,
            text=[f"{name} {value:.0f}%" if value >= 12 else ""], textposition="inside", insidetextanchor="middle",
            textfont={"color": text_color, "size": 13},
            hovertemplate=f"{name}: %{{x:.0f}}% of reviews<extra></extra>",
        ))
    fig.update_layout(barmode="stack", bargap=0.2, showlegend=True,
                      legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0})
    fig.update_xaxes(range=[0, 100], ticksuffix="%", title_text="Share of stored reviews")
    fig.update_yaxes(visible=False)
    return _chart(fig, 170, {"l": 10, "r": 20, "t": 50, "b": 50})


def _heatmap_chart(cities: list[str], labels: list[str], grid: list[list[float | None]]) -> dict:
    fig = go.Figure(go.Heatmap(z=grid, x=cities, y=labels, colorscale="RdBu", zmid=0,
                               colorbar={"title": "% vs median"}))
    return _chart(fig, max(280, 28 * len(labels) + 80), {"l": 10, "r": 10, "t": 10, "b": 10})


def _insights(sel, peers, gap_pct, product_sent, scenarios, health) -> list[str]:
    tips = []
    if gap_pct is not None:
        if gap_pct > 0:
            tips.append(f"Your price is {gap_pct}% above the lowest competitor in this city.")
        else:
            tips.append(f"You have the lowest price among {len(peers)} nearby competing products.")
    if peers:
        cheaper = sum(p["price"] < sel["price"] for p in peers)
        tips.append(f"{cheaper} of {len(peers)} competing products in this city are cheaper than yours.")
    if product_sent:
        tips.append(f"{product_sent['positive_pct']:.0f}% of stored reviews are positive and "
                    f"{product_sent['negative_pct']:.0f}% are negative.")
    if scenarios:
        best = max(scenarios, key=lambda s: s["contribution"])
        tips.append(f"Best contribution option: {best['scenario']} (₹{best['contribution']:,.0f} per unit).")
        risky = [f"{s['scenario']} ({s['risk']})" for s in scenarios if s["risk"] != "OK"]
        if risky:
            tips.append("Watch out: " + "; ".join(risky) + ".")
    if health["stale"]:
        tips.append(f"{health['stale']} offers are older than {health['stale_hours']:g} hours. "
                    "Run the crawler again to refresh them.")
    return tips


# ----------------------------------------------------------------- pages

def retailer(request: HttpRequest):
    try:
        data = analytics.load()
    except Exception as exc:  # pymongo raises several types for "cannot connect"
        return render(request, "web/retailer.html", {"error": f"Cannot read MongoDB ({exc.__class__.__name__})."})
    if not data["offers"]:
        return render(request, "web/retailer.html", {"error": "No priced offers in the database yet. Run the crawler first."})

    city_list = analytics.cities(data)
    city = request.GET.get("city") or city_list[0]["slug"]
    category_list = analytics.categories_in(data, city)
    category = request.GET.get("category") or "All categories"
    rows = analytics.products_in(data, city, category)
    if not rows:
        return render(request, "web/retailer.html", {"error": "No priced products for this selection.", "cities": city_list, "city": city})
    asin = request.GET.get("asin") or rows[0]["asin"]
    sel = next((o for o in rows if o["asin"] == asin), rows[0])
    asin = sel["asin"]

    days = int(_float(request.GET.get("days"), 14))
    days = max(7, min(days, analytics.HISTORY_DAYS))
    peers = [
        {**p, "gap": round((p["price"] / sel["price"] - 1) * 100, 1)}
        for p in analytics.peers_of(data, city, sel)
    ]
    peer_prices = [p["price"] for p in peers]
    lowest = min(peer_prices) if peer_prices else None
    gap_pct = round((sel["price"] / lowest - 1) * 100, 1) if lowest else None

    unit_cost = _float(request.GET.get("unit_cost"), 0.0)
    shipping = _float(request.GET.get("shipping"), 0.0)
    duty = _float(request.GET.get("duty"), 0.0)
    gst = _float(request.GET.get("gst"), analytics.DEFAULT_GST_PCT)
    fees = _float(request.GET.get("fees"), 0.0)
    landed = (unit_cost + shipping) * (1 + duty / 100)
    scenarios, fees_error = [], None
    if unit_cost > 0:
        try:
            scenarios = analytics.margin_scenarios(sel["price"], lowest, landed, gst, fees, peer_prices)
        except ValueError as exc:
            fees_error = str(exc)

    sent = analytics.sentiment(data)
    product_sent = analytics.product_sentiment(sent, asin)
    health = analytics.health(data)

    charts = {"category": _category_chart(analytics.category_overview(data, city), sel["category"])}
    history = analytics.price_history(data, asin, city, days)
    if len(history) >= 2:
        charts["trend"] = _trend_chart(history)
    if peers:
        charts["competitor"] = _competitor_chart(sel, peers)
    cities_, labels, grid = analytics.gap_matrix(data, sel["category"])
    if len(cities_) > 1 and labels:
        charts["heatmap"] = _heatmap_chart(cities_, labels, grid)
    if scenarios:
        charts["margin"] = _margin_chart(scenarios)
    if product_sent:
        charts["sentiment"] = _sentiment_chart(product_sent)

    context = {
        "cities": city_list,
        "city": city,
        "categories": ["All categories"] + category_list,
        "category": category,
        "products": rows,
        "sel": sel,
        "asin": asin,
        "peers": peers,
        "lowest": lowest,
        "gap_pct": gap_pct,
        "days": days,
        "unit_cost": unit_cost,
        "shipping": shipping,
        "duty": duty,
        "gst": gst,
        "fees": fees,
        "landed": landed,
        "scenarios": scenarios,
        "fees_error": fees_error,
        "sentiment": product_sent,
        "sentiment_accuracy": sent["accuracy"] if sent else None,
        "pos_terms": sent["pos_terms"] if sent else [],
        "neg_terms": sent["neg_terms"] if sent else [],
        "basket": analytics.basket_rules(data),
        "health": health,
        "charts": charts,
        "insights": _insights(sel, peers, gap_pct, product_sent, scenarios, health),
        "assistant": {
            "role": "retailer",
            "context": f"I am looking at {sel['title']} (ASIN {asin}) in {city}.",
            "greeting": "Hi! Ask about this product, a price, or your margin. I answer from the same data as this dashboard.",
        },
    }
    return render(request, "web/retailer.html", context)


def crawler_health(request: HttpRequest):
    try:
        data = analytics.load()
    except Exception as exc:  # pymongo raises several types for "cannot connect"
        return render(request, "web/crawler_health.html", {"error": f"Cannot read MongoDB ({exc.__class__.__name__})."})
    return render(request, "web/crawler_health.html", {"health": analytics.health(data)})


def customer(request: HttpRequest):
    request.session.setdefault("shopper_id", str(uuid.uuid4()))
    return render(request, "web/customer.html", {"assistant": CUSTOMER_ASSISTANT})


# ----------------------------------------------------------------- JSON endpoints used by the pages

def _json_error(exc: Exception, status: int = 502) -> JsonResponse:
    return JsonResponse({"detail": str(exc)}, status=status)


@require_GET
def api_cities(request: HttpRequest):
    try:
        return JsonResponse({"cities": get("/cities")})
    except BackendError as exc:
        return _json_error(exc)


@require_GET
def api_categories(request: HttpRequest):
    try:
        return JsonResponse({"categories": get("/categories")})
    except BackendError as exc:
        return _json_error(exc)


@require_GET
def api_category_search(request: HttpRequest):
    query = request.GET.get("q", "").strip()[:80]
    if not query:
        return JsonResponse({"query": "", "best": None, "matches": [], "examples": []})
    try:
        return JsonResponse(get("/category-search", q=query, limit=5))
    except BackendError as exc:
        return _json_error(exc)


@require_GET
def api_recommendations(request: HttpRequest):
    shopper = request.session.setdefault("shopper_id", str(uuid.uuid4()))
    try:
        return JsonResponse(get("/recommendations", session_id=shopper, category=request.GET.get("category", ""),
                                city=request.GET.get("city", "delhi"), limit=5))
    except BackendError as exc:
        return _json_error(exc)


@require_GET
def api_products(request: HttpRequest):
    query = request.GET.get("q", "").strip()[:80]
    try:
        return JsonResponse(get(
            "/products",
            city=request.GET.get("city", "delhi"),
            category=request.GET.get("category") or None,
            q=query or None,
            limit=_int(request.GET.get("limit"), 12),
            offset=_int(request.GET.get("offset"), 0),
        ))
    except BackendError as exc:
        return _json_error(exc)


@require_GET
def api_shown(request: HttpRequest):
    shopper = request.session.setdefault("shopper_id", str(uuid.uuid4()))
    try:
        return JsonResponse({"shown": get("/recommendations/shown", session_id=shopper)})
    except BackendError as exc:
        return _json_error(exc)


@require_POST
def api_interest(request: HttpRequest):
    shopper = request.session.setdefault("shopper_id", str(uuid.uuid4()))
    body = json.loads(request.body or "{}")
    try:
        return JsonResponse(post("/recommendations/interest", {"session_id": shopper, "asin": body.get("asin", ""),
                                                             "interest": body.get("interest")}))
    except BackendError as exc:
        return _json_error(exc, 404 if "not shown" in str(exc) else 502)


@require_GET
def api_threads(request: HttpRequest):
    try:
        return JsonResponse({"threads": get("/threads", limit=30)})
    except BackendError as exc:
        return _json_error(exc)


@require_GET
def api_thread_messages(request: HttpRequest, thread_id: str):
    try:
        return JsonResponse({"messages": get(f"/threads/{thread_id}/messages")})
    except BackendError as exc:
        return _json_error(exc)


@require_POST
def api_chat(request: HttpRequest):
    body = json.loads(request.body or "{}")
    message = (body.get("message") or "").strip()
    if not message:
        return JsonResponse({"detail": "Write a message first."}, status=400)
    role = body.get("role") if body.get("role") in ("customer", "retailer") else None
    payload = {"message": message, "thread_id": body.get("thread_id") or None, "role": role}
    if role == "customer":
        payload["message"] = f"My delivery city is {body.get('city', 'delhi')}. {message}"
    try:
        return JsonResponse(post("/chat", payload))
    except BackendError as exc:
        return _json_error(exc, 409 if "waiting" in str(exc) else 502)


@require_POST
def api_answer(request: HttpRequest, thread_id: str):
    body = json.loads(request.body or "{}")
    try:
        return JsonResponse(post(f"/chat/{thread_id}/answer", {"answer": body.get("answer", "")}))
    except BackendError as exc:
        return _json_error(exc, 409 if "not waiting" in str(exc) else 502)


def _stream_response(path: str, payload: dict):
    try:
        client, upstream = open_stream(path, payload)
    except BackendError as exc:
        return _json_error(exc)
    if upstream.status_code >= 400:
        upstream.read()
        try:
            detail = upstream.json().get("detail", upstream.text)
        except ValueError:
            detail = upstream.text
        upstream.close()
        client.close()
        return JsonResponse({"detail": str(detail)}, status=upstream.status_code)

    def chunks():
        try:
            yield from upstream.iter_bytes()
        finally:
            upstream.close()
            client.close()

    response = StreamingHttpResponse(chunks(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@require_POST
def api_chat_stream(request: HttpRequest):
    body = json.loads(request.body or "{}")
    message = (body.get("message") or "").strip()
    if not message:
        return JsonResponse({"detail": "Write a message first."}, status=400)
    role = body.get("role") if body.get("role") in ("customer", "retailer") else None
    payload = {"message": message, "thread_id": body.get("thread_id") or None, "role": role}
    if role == "customer":
        payload["message"] = f"My delivery city is {body.get('city', 'delhi')}. {message}"
    return _stream_response("/chat/stream", payload)


@require_POST
def api_answer_stream(request: HttpRequest, thread_id: str):
    body = json.loads(request.body or "{}")
    return _stream_response(f"/chat/{thread_id}/answer/stream", {"answer": body.get("answer", "")})
