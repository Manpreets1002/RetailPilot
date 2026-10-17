"""FastAPI backend for the RetailPilot agent (customer and retailer assistants).

Run from the repo root:
  .venv\\Scripts\\python.exe -m uvicorn retailpilot.backend.agent_route:app --reload
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from retailpilot.agent.chatbot import (
    ChatNotConfigured,
    RetailPilotChat,
    ThreadNotWaiting,
    ThreadWaiting,
    ToolCallFailed,
)

state: dict[str, RetailPilotChat] = {}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = None
    role: Literal["customer", "retailer"] | None = Field(
        default=None, description="Skip the router and use this assistant. Omit to let the router decide."
    )


class AnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    thread_id: str
    role: str | None = None
    title: str | None = None
    reply: str | None = None
    question: str | None = None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with RetailPilotChat() as chat:
        state["chat"] = chat
        yield


app = FastAPI(title="RetailPilot Chat Backend", lifespan=lifespan)


@app.exception_handler(ToolCallFailed)
async def tool_call_failed(_: Request, exc: ToolCallFailed) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": f"A data tool failed: {exc}"})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    thread_id = body.thread_id or str(uuid.uuid4())
    try:
        out = await state["chat"].send(thread_id, body.message, role=body.role)
    except ThreadWaiting:
        raise HTTPException(status_code=409, detail="This thread is waiting for an answer to a question.")
    except ChatNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return ChatResponse(thread_id=thread_id, **out)


@app.post("/chat/{thread_id}/answer", response_model=ChatResponse)
async def answer(thread_id: str, body: AnswerRequest) -> ChatResponse:
    try:
        out = await state["chat"].answer(thread_id, body.answer)
    except ThreadNotWaiting:
        raise HTTPException(status_code=409, detail="This thread is not waiting for an answer.")
    except ChatNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return ChatResponse(thread_id=thread_id, **out)


async def _sse(events):
    async for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _event_stream(events):
    return StreamingResponse(
        _sse(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/chat/stream")
async def chat_stream(body: ChatRequest):
    thread_id = body.thread_id or str(uuid.uuid4())
    try:
        events = await state["chat"].start_send(thread_id, body.message, role=body.role)
    except ThreadWaiting:
        raise HTTPException(status_code=409, detail="This thread is waiting for an answer to a question.")
    except ChatNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return _event_stream(events)


@app.post("/chat/{thread_id}/answer/stream")
async def answer_stream(thread_id: str, body: AnswerRequest):
    try:
        events = await state["chat"].start_answer(thread_id, body.answer)
    except ThreadNotWaiting:
        raise HTTPException(status_code=409, detail="This thread is not waiting for an answer.")
    except ChatNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return _event_stream(events)


class InterestRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    asin: str = Field(min_length=10, max_length=10)
    interest: Literal["buy", "skip"] | None = None


@app.get("/cities")
async def cities() -> list[dict]:
    return await state["chat"].call_tool("list_cities", {})


@app.get("/categories")
async def categories() -> list[dict]:
    rows = await state["chat"].call_tool("list_categories", {})
    return [r for r in rows if r["products"] > 0]


@app.get("/category-search")
async def category_search(q: str, limit: int = 5) -> dict:
    return await state["chat"].call_tool("match_category", {"query": q, "limit": max(1, min(limit, 10))})


@app.get("/recommendations")
async def recommendations(session_id: str, category: str, city: str = "delhi", limit: int = 5) -> dict:
    chat = state["chat"]
    exclude = await chat.history.shown_asins(session_id)
    result = await chat.call_tool(
        "customer_best_today",
        {"category": category, "city": city, "limit": max(1, min(limit, 10)), "exclude_asins": exclude},
    )
    await chat.history.record_shown(session_id, [{**i, "city": city, "category": category} for i in result.get("items", [])])
    return result


@app.get("/recommendations/shown")
async def shown(session_id: str) -> list[dict]:
    return await state["chat"].history.shown(session_id)


@app.post("/recommendations/interest")
async def interest(body: InterestRequest) -> dict[str, bool]:
    if not await state["chat"].history.set_interest(body.session_id, body.asin, body.interest):
        raise HTTPException(status_code=404, detail="That product was not shown in this session.")
    return {"ok": True}


@app.get("/threads")
async def threads(limit: int = 50) -> list[dict]:
    return await state["chat"].threads(max(1, min(limit, 200)))


@app.get("/threads/{thread_id}/messages")
async def thread_messages(thread_id: str) -> list[dict]:
    return await state["chat"].messages(thread_id)
