"""HTTP tests for the FastAPI agent backend, with the chat and MCP layers replaced by fakes."""

import pytest
from fastapi.testclient import TestClient

import support  # noqa: F401
from retailpilot.agent.chatbot import ChatNotConfigured, ThreadNotWaiting, ThreadWaiting, ToolCallFailed
from retailpilot.backend import agent_route


class FakeHistory:
    def __init__(self):
        self.interest_ok = True
        self.recorded = []

    async def shown_asins(self, session_id):
        return []

    async def record_shown(self, session_id, items):
        self.recorded.append(items)

    async def shown(self, session_id, limit=100):
        return []

    async def set_interest(self, session_id, asin, interest):
        return self.interest_ok


class FakeChat:
    def __init__(self):
        self.history = FakeHistory()
        self.send_error = None
        self.answer_error = None
        self.calls = []
        self.tool_results = {}

    async def send(self, thread_id, message, role=None):
        if self.send_error:
            raise self.send_error
        self.calls.append((thread_id, message, role))
        return {"question": None, "reply": f"echo: {message}", "role": role or "customer", "title": "T"}

    async def answer(self, thread_id, text):
        if self.answer_error:
            raise self.answer_error
        return {"question": None, "reply": f"answered: {text}", "role": "customer", "title": None}

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return self.tool_results.get(name, {})

    async def threads(self, limit):
        return [{"thread_id": "t1"}]

    async def messages(self, thread_id):
        return []


@pytest.fixture
def api(monkeypatch):
    fake = FakeChat()
    monkeypatch.setitem(agent_route.state, "chat", fake)
    return TestClient(agent_route.app), fake  # no `with` block, so the real lifespan (which needs MongoDB) is skipped


def test_health(api):
    client, _ = api
    assert client.get("/health").json() == {"status": "ok"}


def test_chat_creates_a_thread_id_when_none_is_given(api):
    client, _ = api
    body = client.post("/chat", json={"message": "hi"}).json()
    assert body["thread_id"]
    assert body["reply"] == "echo: hi"


def test_chat_keeps_the_thread_and_role_it_is_given(api):
    client, fake = api
    client.post("/chat", json={"message": "hi", "thread_id": "abc", "role": "retailer"})
    assert fake.calls[-1] == ("abc", "hi", "retailer")


@pytest.mark.parametrize("payload", [
    {"message": ""},
    {"message": "x" * 2001},
    {"message": "hi", "role": "admin"},
])
def test_chat_rejects_invalid_bodies(api, payload):
    client, _ = api
    assert client.post("/chat", json=payload).status_code == 422


def test_chat_while_waiting_for_an_answer_is_a_conflict(api):
    client, fake = api
    fake.send_error = ThreadWaiting("t1")
    assert client.post("/chat", json={"message": "hi", "thread_id": "t1"}).status_code == 409


def test_chat_without_api_key_is_service_unavailable(api):
    client, fake = api
    fake.send_error = ChatNotConfigured("GOOGLE_API_KEY is not set")
    response = client.post("/chat", json={"message": "hi"})
    assert response.status_code == 503
    assert "GOOGLE_API_KEY" in response.json()["detail"]


def test_answer_returns_the_next_reply(api):
    client, _ = api
    response = client.post("/chat/t1/answer", json={"answer": "Delhi"})
    assert response.status_code == 200
    assert response.json()["reply"] == "answered: Delhi"


def test_answer_when_nothing_is_pending_is_a_conflict(api):
    client, fake = api
    fake.answer_error = ThreadNotWaiting("t1")
    assert client.post("/chat/t1/answer", json={"answer": "Delhi"}).status_code == 409


def test_recommendations_exclude_shown_items_and_record_new_ones(api):
    client, fake = api
    fake.tool_results["customer_best_today"] = {"items": [{"asin": "B0TESTA001", "title": "Laptop", "price": 1}]}
    body = client.get("/recommendations", params={"session_id": "s1", "category": "Laptops", "city": "delhi"}).json()
    assert body["items"][0]["asin"] == "B0TESTA001"
    tool_name, args = fake.calls[-1]
    assert tool_name == "customer_best_today"
    assert args["category"] == "Laptops" and args["city"] == "delhi"
    assert fake.history.recorded[-1][0]["city"] == "delhi"


def test_recommendations_clamp_the_limit(api):
    client, fake = api
    client.get("/recommendations", params={"session_id": "s1", "category": "Laptops", "limit": 500})
    assert fake.calls[-1][1]["limit"] == 10


def test_products_endpoint_calls_the_customer_products_tool(api):
    client, fake = api
    fake.tool_results["customer_products"] = {"total": 1, "items": [{"asin": "B0TESTA001", "title": "Laptop"}]}
    body = client.get("/products", params={"city": "delhi", "q": "laptop", "offset": 12, "limit": 500}).json()
    assert body["items"][0]["asin"] == "B0TESTA001"
    tool_name, args = fake.calls[-1]
    assert tool_name == "customer_products"
    assert args == {"city": "delhi", "category": None, "query": "laptop", "limit": 50, "offset": 12}


def test_interest_for_a_product_not_shown_is_not_found(api):
    client, fake = api
    fake.history.interest_ok = False
    response = client.post("/recommendations/interest", json={"session_id": "s1", "asin": "B0TESTA001", "interest": "buy"})
    assert response.status_code == 404


def test_interest_accepts_buy_and_skip_only(api):
    client, _ = api
    ok = client.post("/recommendations/interest", json={"session_id": "s1", "asin": "B0TESTA001", "interest": "buy"})
    assert ok.json() == {"ok": True}
    bad = client.post("/recommendations/interest", json={"session_id": "s1", "asin": "B0TESTA001", "interest": "maybe"})
    assert bad.status_code == 422


def test_interest_rejects_a_malformed_asin(api):
    client, _ = api
    response = client.post("/recommendations/interest", json={"session_id": "s1", "asin": "SHORT", "interest": "buy"})
    assert response.status_code == 422


def test_categories_hide_empty_ones(api):
    client, fake = api
    fake.tool_results["list_categories"] = [{"name": "Laptops", "products": 2}, {"name": "Empty", "products": 0}]
    assert client.get("/categories").json() == [{"name": "Laptops", "products": 2}]


def test_a_failed_tool_call_is_a_502_with_a_message(api):
    client, fake = api

    async def broken(name, args):
        raise ToolCallFailed("Tool list_categories failed: boom")

    fake.call_tool = broken
    response = client.get("/categories")
    assert response.status_code == 502
    assert "list_categories failed" in response.json()["detail"]


def test_threads_and_messages_endpoints(api):
    client, _ = api
    assert client.get("/threads").json() == [{"thread_id": "t1"}]
    assert client.get("/threads/t1/messages").json() == []
