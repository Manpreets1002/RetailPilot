"""Unit tests for the chatbot's routing and state rules, and the MCP wrapper. No API key, database or model needed."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import support  # noqa: F401
from retailpilot.agent import chatbot
from retailpilot.agent.chatbot import (
    ChatNotConfigured,
    RetailPilotChat,
    ThreadNotWaiting,
    ThreadWaiting,
    ToolCallFailed,
    _last_user_text,
    _text,
    _turn_transcript,
    tools_for_role,
)
from retailpilot.agent.mcp_wrapper import MCPToolkit, _unwrap


class FakeHistory:
    def __init__(self, pending=None):
        self.pending = pending

    async def get_pending(self, thread_id):
        return self.pending

    async def close(self):
        pass


class FakeToolkit:
    def __init__(self, raw):
        self.raw = raw

    async def call(self, name, args):
        return self.raw


class FakeClient:
    def __init__(self, result):
        self.result = result

    async def call_tool(self, name, args, raise_on_error=False):
        return self.result


def _tool(name):
    return SimpleNamespace(name=name)


# ------------------------------------------------------------------ role routing

def test_retailer_assistant_cannot_see_customer_tools():
    tools = [_tool(n) for n in ["customer_best_today", "retailer_price_position", "list_cities", "search_products"]]
    names = {t.name for t in tools_for_role(tools, "retailer")}
    assert names == {"retailer_price_position", "list_cities", "search_products"}


def test_customer_assistant_cannot_see_retailer_tools():
    tools = [_tool(n) for n in ["customer_best_today", "retailer_price_position", "list_cities", "search_products"]]
    names = {t.name for t in tools_for_role(tools, "customer")}
    assert names == {"customer_best_today", "list_cities", "search_products"}


# ------------------------------------------------------------------ message helpers

def test_text_reads_plain_and_multi_part_content():
    assert _text(AIMessage(content="hi")) == "hi"
    assert _text(AIMessage(content=[{"type": "text", "text": "a"}, "b"])) == "ab"


def test_last_user_text_skips_review_feedback():
    messages = [
        HumanMessage(content="first question"),
        AIMessage(content="draft"),
        HumanMessage(content="Your answer is missing a price", name="review_feedback"),
    ]
    assert _last_user_text(messages) == "first question"


def test_turn_transcript_lists_tool_calls_and_truncates_results():
    big = "x" * (chatbot.TOOL_RESULT_CHARS + 500)
    messages = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{"name": "search_products", "args": {"city": "delhi"}, "id": "1"}]),
        ToolMessage(content=big, tool_call_id="1"),
    ]
    transcript = _turn_transcript(messages)
    assert "Tool calls: search_products" in transcript
    assert "x" * (chatbot.TOOL_RESULT_CHARS + 1) not in transcript


def test_turn_transcript_without_tool_use():
    assert _turn_transcript([HumanMessage(content="q")]) == "(no tool results)"


# ------------------------------------------------------------------ chat state rules

def test_chat_without_api_key_reports_not_configured():
    chat = RetailPilotChat()
    chat.history = FakeHistory()
    with pytest.raises(ChatNotConfigured):
        asyncio.run(chat.send("t1", "hi"))
    with pytest.raises(ChatNotConfigured):
        asyncio.run(chat.answer("t1", "yes"))


def test_new_message_is_refused_while_a_question_is_open():
    chat = RetailPilotChat()
    chat.history = FakeHistory(pending="Which city?")
    chat.graph = object()
    with pytest.raises(ThreadWaiting):
        asyncio.run(chat.send("t1", "hi"))


def test_answer_is_refused_when_no_question_is_open():
    chat = RetailPilotChat()
    chat.history = FakeHistory(pending=None)
    chat.graph = object()
    with pytest.raises(ThreadNotWaiting):
        asyncio.run(chat.answer("t1", "yes"))


def test_unknown_role_is_rejected_before_anything_is_stored():
    chat = RetailPilotChat()
    chat.history = FakeHistory(pending=None)
    chat.graph = object()
    with pytest.raises(ValueError):
        asyncio.run(chat.send("t1", "hi", role="admin"))


def test_failed_tool_call_raises_tool_call_failed():
    chat = RetailPilotChat()
    chat.toolkit = FakeToolkit("Tool get_product failed: {'error': 'boom'}")
    with pytest.raises(ToolCallFailed, match="get_product failed"):
        asyncio.run(chat.call_tool("get_product", {}))


# ------------------------------------------------------------------ MCP wrapper

def test_unwrap_prefers_the_structured_result_key():
    assert _unwrap(SimpleNamespace(structured_content={"result": [1, 2]}, content=[])) == [1, 2]


def test_unwrap_returns_plain_structured_payloads_unchanged():
    assert _unwrap(SimpleNamespace(structured_content={"a": 1}, content=[])) == {"a": 1}


def test_unwrap_falls_back_to_json_text_then_raw_text():
    as_json = SimpleNamespace(structured_content=None, content=[SimpleNamespace(text='{"ok": true}')])
    assert _unwrap(as_json) == {"ok": True}
    as_text = SimpleNamespace(structured_content=None, content=[SimpleNamespace(text="not json")])
    assert _unwrap(as_text) == "not json"


def test_tool_success_is_returned_as_json_text():
    kit = MCPToolkit()
    kit.client = FakeClient(SimpleNamespace(is_error=False, structured_content={"result": {"x": 1}}, content=[]))
    assert json.loads(asyncio.run(kit.call("list_cities", {}))) == {"x": 1}


def test_tool_error_is_returned_as_text_starting_with_failed():
    kit = MCPToolkit()
    kit.client = FakeClient(SimpleNamespace(is_error=True, structured_content={"error": "boom"}, content=[]))
    out = asyncio.run(kit.call("get_product", {"asin": "x"}))
    assert out.startswith("Tool get_product failed")
