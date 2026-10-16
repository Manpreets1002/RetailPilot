import json
import uuid

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from web import analytics
from web.backend import BackendError, get, post


def landing(request: HttpRequest):
    return render(request, "web/landing.html")


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def retailer(request: HttpRequest):
    error = None
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
    scenarios = analytics.margin_scenarios(sel["price"], lowest, landed, gst, fees, peer_prices) if unit_cost > 0 else []

    sent = analytics.sentiment(data)
    product_sent = analytics.product_sentiment(sent, asin)

    history = analytics.price_history(data, asin, city, days)
    trend_json = None
    if history:
        import plotly.graph_objects as go

        fig = go.Figure(go.Scatter(x=[d for d, _ in history], y=[p for _, p in history], mode="lines+markers",
                                   line={"color": "#5eead4", "width": 3}, marker={"size": 7}))
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          margin={"l": 40, "r": 10, "t": 10, "b": 40}, height=320, yaxis_title="INR")
        trend_json = fig.to_json()

    heatmap_json = None
    cities_, labels, grid = analytics.gap_matrix(data, sel["category"])
    if len(cities_) > 1 and labels:
        import plotly.graph_objects as go

        fig = go.Figure(go.Heatmap(z=grid, x=cities_, y=labels, colorscale="RdBu", zmid=0,
                                   colorbar={"title": "% vs median"}))
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          margin={"l": 10, "r": 10, "t": 10, "b": 10}, height=max(280, 28 * len(labels) + 80))
        heatmap_json = fig.to_json()

    health = analytics.health(data)
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
        "trend_json": trend_json,
        "heatmap_json": heatmap_json,
        "unit_cost": unit_cost,
        "shipping": shipping,
        "duty": duty,
        "gst": gst,
        "fees": fees,
        "landed": landed,
        "scenarios": scenarios,
        "sentiment": product_sent,
        "sentiment_accuracy": sent["accuracy"] if sent else None,
        "pos_terms": sent["pos_terms"] if sent else [],
        "neg_terms": sent["neg_terms"] if sent else [],
        "basket": analytics.basket_rules(data),
        "health": health,
        "error": error,
    }
    return render(request, "web/retailer.html", context)


def customer(request: HttpRequest):
    request.session.setdefault("shopper_id", str(uuid.uuid4()))
    return render(request, "web/customer.html", {"shopper_id": request.session["shopper_id"]})


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
def api_recommendations(request: HttpRequest):
    shopper = request.session.setdefault("shopper_id", str(uuid.uuid4()))
    try:
        return JsonResponse(get("/recommendations", session_id=shopper, category=request.GET.get("category", ""),
                                city=request.GET.get("city", "delhi"), limit=5))
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
