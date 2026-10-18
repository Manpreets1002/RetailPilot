"""Django frontend tests: pages, JSON endpoints, the retailer dashboard, and the HTTP client to the backend.

The backend and MongoDB are replaced with fakes, so these run without either.
Sessions use signed cookies so nothing is written to the local SQLite file.
"""

import json

import httpx
import pytest
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse

import support  # noqa: F401
import sample_data as sd
from web import analytics, views
from web.backend import BackendError
from web import backend


@pytest.fixture
def client():
    with override_settings(SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies"):
        yield Client()


@pytest.fixture(autouse=True)
def fresh_cache():
    analytics._cache.clear()
    yield
    analytics._cache.clear()


@pytest.fixture
def dataset(monkeypatch):
    data = sd.dashboard_dataset()
    monkeypatch.setattr(analytics, "load", lambda: data)
    return data


# ------------------------------------------------------------------ pages

def test_landing_links_to_both_dashboards_without_the_chat_button(client):
    body = client.get("/").content.decode()
    assert reverse("retailer") in body
    assert reverse("customer") in body
    assert 'id="rp-toggle"' not in body


def test_customer_shop_has_city_picker_cart_and_assistant(client):
    response = client.get("/customer/")
    assert response.status_code == 200
    body = response.content.decode()
    for marker in ('id="city"', 'id="cart-btn"', 'id="categories"', 'id="rp-toggle"', 'id="assistant-config"'):
        assert marker in body, marker


def test_customer_shop_has_no_switch_to_the_retailer_dashboard(client):
    body = client.get("/customer/").content.decode()
    assert reverse("retailer") not in body
    assert 'id="shop-search-input"' in body and 'id="all-products"' in body


def test_retailer_dashboard_has_no_switch_to_the_customer_shop(client, monkeypatch):
    monkeypatch.setattr(analytics, "load", lambda: {"offers": [], "products": {}})
    body = client.get("/retailer/").content.decode()
    assert reverse("customer") not in body


def test_retailer_page_explains_when_mongodb_is_unreachable(client, monkeypatch):
    def unreachable():
        raise ConnectionError("no server")

    monkeypatch.setattr(analytics, "load", unreachable)
    body = client.get("/retailer/").content.decode()
    assert "Cannot read MongoDB" in body
    assert 'id="rp-toggle"' not in body


def test_retailer_page_asks_for_a_crawl_when_there_are_no_offers(client, monkeypatch):
    monkeypatch.setattr(analytics, "load", lambda: {"offers": [], "products": {}})
    assert "Run the crawler first" in client.get("/retailer/").content.decode()


def test_retailer_dashboard_renders_charts_insights_and_assistant(client, dataset):
    response = client.get("/retailer/", {"city": "delhi", "unit_cost": "30000"})
    assert response.status_code == 200
    body = response.content.decode()
    for chart in ("chart-category", "chart-trend", "chart-competitor", "chart-sentiment",
                  "chart-heatmap", "chart-margin"):
        assert f'id="{chart}"' in body, chart
    assert 'id="charts"' in body
    assert "Your price is 19.0% above the lowest competitor" in body
    assert 'id="rp-toggle"' in body


def test_retailer_dashboard_explains_fees_of_100_percent(client, dataset):
    response = client.get("/retailer/", {"city": "delhi", "unit_cost": "30000", "fees": "100"})
    assert response.status_code == 200
    assert "Marketplace fees must be below 100%." in response.content.decode()


def test_retailer_dashboard_tolerates_bad_number_inputs(client, dataset):
    response = client.get("/retailer/", {"city": "delhi", "days": "abc", "unit_cost": "x", "gst": "?"})
    assert response.status_code == 200


def test_retailer_dashboard_handles_an_unknown_city(client, dataset):
    response = client.get("/retailer/", {"city": "atlantis"})
    assert response.status_code == 200


# ------------------------------------------------------------------ JSON endpoints

class Recorder:
    def __init__(self, reply=None, error=None):
        self.reply = reply if reply is not None else {}
        self.error = error
        self.calls = []

    def __call__(self, path, *args, **kwargs):
        self.calls.append((path, args, kwargs))
        if self.error:
            raise self.error
        return self.reply


def test_cities_endpoint_passes_backend_data_through(client, monkeypatch):
    monkeypatch.setattr(views, "get", Recorder([{"slug": "delhi"}]))
    assert client.get("/api/cities/").json() == {"cities": [{"slug": "delhi"}]}


def test_backend_outage_becomes_a_502_with_a_message(client, monkeypatch):
    monkeypatch.setattr(views, "get", Recorder(error=BackendError("Cannot reach the RetailPilot backend")))
    response = client.get("/api/categories/")
    assert response.status_code == 502
    assert "Cannot reach" in response.json()["detail"]


def test_recommendations_forward_category_city_and_a_session(client, monkeypatch):
    recorder = Recorder({"items": []})
    monkeypatch.setattr(views, "get", recorder)
    client.get("/api/recommendations/", {"category": "Laptops", "city": "mumbai"})
    path, _, params = recorder.calls[0]
    assert path == "/recommendations"
    assert params["category"] == "Laptops" and params["city"] == "mumbai"
    assert params["limit"] == 5 and params["session_id"]


def test_customer_chat_message_includes_the_delivery_city(client, monkeypatch):
    recorder = Recorder({"reply": "ok"})
    monkeypatch.setattr(views, "post", recorder)
    client.post("/api/chat/", data=json.dumps({"message": "laptops?", "role": "customer", "city": "mumbai"}),
                content_type="application/json")
    _, (body,), _ = recorder.calls[0]
    assert body["message"].startswith("My delivery city is mumbai. ")


def test_chat_rejects_an_empty_message(client):
    response = client.post("/api/chat/", data=json.dumps({"message": "   "}), content_type="application/json")
    assert response.status_code == 400


def test_chat_while_waiting_is_a_conflict(client, monkeypatch):
    monkeypatch.setattr(views, "post", Recorder(error=BackendError("This thread is waiting for an answer.")))
    response = client.post("/api/chat/", data=json.dumps({"message": "hi"}), content_type="application/json")
    assert response.status_code == 409


def test_answer_when_not_waiting_is_a_conflict(client, monkeypatch):
    monkeypatch.setattr(views, "post", Recorder(error=BackendError("This thread is not waiting for an answer.")))
    response = client.post("/api/chat/t1/answer/", data=json.dumps({"answer": "x"}), content_type="application/json")
    assert response.status_code == 409


def test_interest_for_an_unshown_product_is_not_found(client, monkeypatch):
    monkeypatch.setattr(views, "post", Recorder(error=BackendError("That product was not shown in this session.")))
    response = client.post("/api/interest/", data=json.dumps({"asin": "B0TESTA001", "interest": "buy"}),
                           content_type="application/json")
    assert response.status_code == 404


def test_products_endpoint_forwards_city_category_query_and_paging(client, monkeypatch):
    recorder = Recorder({"items": [], "total": 0})
    monkeypatch.setattr(views, "get", recorder)
    client.get("/api/products/", {"city": "mumbai", "category": "Laptops", "offset": "12", "limit": "12"})
    path, _, params = recorder.calls[-1]
    assert path == "/products"
    assert params == {"city": "mumbai", "category": "Laptops", "q": None, "limit": 12, "offset": 12}


def test_products_search_trims_the_query_and_ignores_bad_numbers(client, monkeypatch):
    recorder = Recorder({"items": [], "total": 0})
    monkeypatch.setattr(views, "get", recorder)
    client.get("/api/products/", {"q": "  headphones  ", "limit": "lots", "offset": ""})
    _, _, params = recorder.calls[-1]
    assert params["q"] == "headphones"
    assert params["category"] is None
    assert params["limit"] == 12 and params["offset"] == 0


def test_json_endpoints_reject_the_wrong_http_method(client):
    assert client.get("/api/chat/").status_code == 405
    assert client.post("/api/cities/").status_code == 405


# ------------------------------------------------------------------ backend HTTP client

@pytest.fixture
def backend_transport(monkeypatch):
    real_client = httpx.Client
    holder = {}

    def use(handler):
        holder["handler"] = handler
        monkeypatch.setattr(backend.httpx, "Client",
                            lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    return use


def test_backend_get_returns_json_and_drops_empty_params(backend_transport):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[{"slug": "delhi"}])

    backend_transport(handler)
    assert backend.get("/cities") == [{"slug": "delhi"}]
    assert backend.get("/recommendations", category="Laptops", city=None) == [{"slug": "delhi"}]
    assert "city=" not in seen["url"]
    assert "category=Laptops" in seen["url"]


def test_backend_error_detail_is_surfaced(backend_transport):
    backend_transport(lambda request: httpx.Response(404, json={"detail": "nope"}))
    with pytest.raises(BackendError, match="nope"):
        backend.get("/threads/x/messages")


def test_backend_non_json_error_uses_the_body_text(backend_transport):
    backend_transport(lambda request: httpx.Response(500, text="Internal failure"))
    with pytest.raises(BackendError, match="Internal failure"):
        backend.get("/cities")


def test_unreachable_backend_is_a_backend_error(backend_transport):
    def refused(request):
        raise httpx.ConnectError("connection refused", request=request)

    backend_transport(refused)
    with pytest.raises(BackendError, match="Cannot reach the RetailPilot backend"):
        backend.get("/health")
