import asyncio
import json

import uuid
from datetime import datetime
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field
from pymongo import MongoClient

from retailpilot.agent.history import ChatHistory
from retailpilot.config.config import settings
from retailpilot.agent.mcp_wrapper import IST, MCPToolkit
from retailpilot.agent.prompt import (
    REVIEW_PROMPT,
    ROUTER_PROMPT,
    SYSTEM_CUSTOMER_PROMPT,
    SYSTEM_RETAILER_PROMPT,
    THREAD_TITLE_PROMPT,
)
from retailpilot.agent.state import RetailPilot, ThreadGeneration

CHANNEL = "amazon"
ROLES = ("customer", "retailer")
SYSTEM_PROMPTS = {"customer": SYSTEM_CUSTOMER_PROMPT, "retailer": SYSTEM_RETAILER_PROMPT}
MAX_REVIEW_RETRIES = 1
TOOL_RESULT_CHARS = 4000


class RouteDecision(BaseModel):
    role: Literal["customer", "retailer"]
    reason: str


class ReviewResult(BaseModel):
    passed: bool
    coverage: float = Field(ge=0, le=1)
    missing: list[str] = Field(default_factory=list)


class ThreadWaiting(Exception):
    """The thread is waiting for an answer to a question, so it cannot take a new message."""


class ChatNotConfigured(Exception):
    """The chat needs a setting that is missing, such as GOOGLE_API_KEY."""


class ThreadNotWaiting(Exception):
    """The thread has no open question to answer."""


class ToolCallFailed(Exception):
    """An MCP tool call failed or returned something that is not JSON."""


@tool
async def ask_user(question: str) -> str:
    """Ask the user one short question when information needed to answer is missing. Pauses until they reply."""
    return ""


def tools_for_role(mcp_tools: list, role: str) -> list:
    """Shared tools (no prefix) plus the tools prefixed with this role."""
    other_prefixes = tuple(f"{r}_" for r in ROLES if r != role)
    return [t for t in mcp_tools if not t.name.startswith(other_prefixes)]


def _text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content)


def _is_user_turn(message: BaseMessage) -> bool:
    return isinstance(message, HumanMessage) and message.name != "review_feedback"


def _last_user_text(messages: list[BaseMessage]) -> str:
    return next((_text(m) for m in reversed(messages) if _is_user_turn(m)), "")


def _turn_transcript(messages: list[BaseMessage]) -> str:
    start = max((i for i, m in enumerate(messages) if _is_user_turn(m)), default=0)
    lines = []
    for m in messages[start:]:
        if isinstance(m, ToolMessage):
            lines.append(f"Tool result: {_text(m)[:TOOL_RESULT_CHARS]}")
        elif isinstance(m, AIMessage) and m.tool_calls:
            lines.append("Tool calls: " + ", ".join(f"{c['name']}({c['args']})" for c in m.tool_calls))
    return "\n".join(lines) or "(no tool results)"


def build_graph(llm: ChatGoogleGenerativeAI, mcp_tools: list, checkpointer: Any):
    tools_by_name = {t.name: t for t in mcp_tools}
    role_tools = {role: [ask_user, *tools_for_role(mcp_tools, role)] for role in ROLES}
    tool_names = {role: ", ".join(t.name for t in role_tools[role]) for role in ROLES}
    chains = {
        role: ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPTS[role]), MessagesPlaceholder("messages")])
        | llm.bind_tools(role_tools[role])
        for role in ROLES
    }
    router = llm.with_structured_output(RouteDecision)
    reviewer = llm.with_structured_output(ReviewResult)

    async def route(state: RetailPilot, config: RunnableConfig) -> dict:
        override = (config.get("configurable") or {}).get("role_override")
        if override:
            return {"role": override}
        decision = await router.ainvoke([
            SystemMessage(content=ROUTER_PROMPT.format(previous_role=state.get("role") or "customer")),
            HumanMessage(content=_last_user_text(state["messages"])),
        ])
        return {"role": decision.role}

    async def agent(state: RetailPilot) -> dict:
        role = state["role"]
        reply = await chains[role].ainvoke({
            "messages": state["messages"],
            "day": state["day"],
            "date": state["date"],
            "tool_names": tool_names[role],
        })
        return {"messages": [reply]}

    def route_after_agent(state: RetailPilot) -> str:
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return "review"
        if any(c["name"] == "ask_user" for c in last.tool_calls):
            return "ask_user"
        return "tools"

    async def run_tools(state: RetailPilot) -> dict:
        allowed = {t.name for t in role_tools[state["role"]]}
        outputs = []
        for call in state["messages"][-1].tool_calls:
            if call["name"] not in allowed:
                content = f"Tool {call['name']} is not available to the {state['role']} assistant."
            else:
                try:
                    content = await tools_by_name[call["name"]].ainvoke(call["args"])
                except Exception as exc:
                    content = f"Tool {call['name']} raised an error: {exc}"
            outputs.append(ToolMessage(content=str(content), tool_call_id=call["id"]))
        return {"messages": outputs}

    async def ask_user_node(state: RetailPilot) -> dict:
        outputs = []
        asked = False
        for call in state["messages"][-1].tool_calls:
            if call["name"] != "ask_user":
                outputs.append(ToolMessage(content="Not run: ask one question at a time, then call this tool again after the user replies.", tool_call_id=call["id"]))
            elif asked:
                outputs.append(ToolMessage(content="Only one question can be asked per turn.", tool_call_id=call["id"]))
            else:
                asked = True
                answer = interrupt({"question": call["args"]["question"]})
                outputs.append(ToolMessage(content=str(answer), tool_call_id=call["id"]))
        return {"messages": outputs}

    async def review(state: RetailPilot) -> Command:
        messages = state["messages"]
        answer = _text(messages[-1])
        result: ReviewResult = await reviewer.ainvoke([
            SystemMessage(content=REVIEW_PROMPT),
            HumanMessage(content=(
                f"User question:\n{_last_user_text(messages)}\n\n"
                f"Conversation tool results:\n{_turn_transcript(messages)}\n\n"
                f"Assistant answer:\n{answer}"
            )),
        ])
        attempts = state["review_attempts"] + 1
        if result.passed or attempts > MAX_REVIEW_RETRIES:
            return Command(goto=END, update={"review_attempts": attempts})
        feedback = HumanMessage(
            name="review_feedback",
            content=(
                "Your answer does not yet cover everything the user asked. Missing: "
                f"{'; '.join(result.missing) or 'unspecified'}. Use the tools to fill these gaps, "
                "or call ask_user if you need information from the user, then answer again."
            ),
        )
        return Command(goto="agent", update={"review_attempts": attempts, "messages": [feedback]})

    builder = StateGraph(RetailPilot)
    builder.add_node("route", route)
    builder.add_node("agent", agent)
    builder.add_node("tools", run_tools)
    builder.add_node("ask_user", ask_user_node)
    builder.add_node("review", review)
    builder.add_edge(START, "route")
    builder.add_edge("route", "agent")
    builder.add_conditional_edges("agent", route_after_agent, {"review": "review", "tools": "tools", "ask_user": "ask_user"})
    builder.add_edge("tools", "agent")
    builder.add_edge("ask_user", "agent")
    return builder.compile(checkpointer=checkpointer)


class RetailPilotChat:
    def __init__(self, model: str | None = None, mongo_uri: str | None = None):
        self.model = model or settings.gemini_model
        self.mongo_uri = mongo_uri or settings.mongo_uri
        self.toolkit = MCPToolkit()
        self.llm: ChatGoogleGenerativeAI | None = None
        self.history: ChatHistory | None = None
        self.mongo: MongoClient | None = None
        self.graph = None

    async def __aenter__(self) -> "RetailPilotChat":
        await self.toolkit.__aenter__()
        self.history = ChatHistory(self.mongo_uri)
        await self.history.ensure_indexes()
        self.mongo = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
        if settings.google_api_key:
            self.llm = ChatGoogleGenerativeAI(model=self.model, api_key=settings.google_api_key, temperature=0)
            self.graph = build_graph(self.llm, self.toolkit.tools, MongoDBSaver(self.mongo, db_name=settings.chat_db))
        return self

    def _require_graph(self) -> None:
        if self.graph is None:
            raise ChatNotConfigured("GOOGLE_API_KEY is not set in .env, so the assistant cannot answer yet.")

    async def __aexit__(self, *exc_info) -> None:
        await self.history.close()
        self.mongo.close()
        await self.toolkit.__aexit__(*exc_info)

    async def _title(self, message: str) -> str:
        generated = await self.llm.with_structured_output(ThreadGeneration).ainvoke(
            THREAD_TITLE_PROMPT.format(message=message)
        )
        return generated.title

    @staticmethod
    def _config(thread_id: str, role_override: str | None = None) -> RunnableConfig:
        configurable = {"thread_id": thread_id}
        if role_override:
            configurable["role_override"] = role_override
        return {"configurable": configurable}

    async def _finish(self, thread_id: str, output: Any, config: RunnableConfig) -> dict:
        role = (await self.graph.aget_state(config)).values.get("role")
        interrupts = output.get("__interrupt__") if isinstance(output, dict) else None
        if interrupts:
            question = interrupts[0].value["question"]
            await self.history.set_pending(thread_id, question)
            await self.history.add_message(thread_id, "assistant", question, kind="question", role=role)
            return {"question": question, "reply": None, "role": role}
        reply = _text(output["messages"][-1])
        await self.history.set_pending(thread_id, None)
        await self.history.add_message(thread_id, "assistant", reply, kind="answer", role=role)
        return {"question": None, "reply": reply, "role": role}

    async def send(self, thread_id: str, text: str, role: str | None = None) -> dict:
        self._require_graph()
        if await self.history.get_pending(thread_id):
            raise ThreadWaiting(thread_id)
        if role is not None and role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        now = datetime.now(IST)
        title = None
        if not await self.history.thread_exists(thread_id):
            title = await self._title(text)
            await self.history.create_thread(thread_id, title, CHANNEL)
        await self.history.add_message(thread_id, "user", text, kind="message", role=role)
        graph_input = {
            "messages": [HumanMessage(content=text)],
            "timestamp": now.isoformat(),
            "thread_id": thread_id,
            "channel": CHANNEL,
            "role": role or "customer",
            "date": now.date().isoformat(),
            "day": now.strftime("%A"),
            "review_attempts": 0,
        }
        config = self._config(thread_id, role)
        output = await self.graph.ainvoke(graph_input, config)
        return (await self._finish(thread_id, output, config)) | {"title": title}

    async def answer(self, thread_id: str, text: str) -> dict:
        self._require_graph()
        if not await self.history.get_pending(thread_id):
            raise ThreadNotWaiting(thread_id)
        await self.history.add_message(thread_id, "user", text, kind="answer", role=None)
        config = self._config(thread_id)
        output = await self.graph.ainvoke(Command(resume=text), config)
        return (await self._finish(thread_id, output, config)) | {"title": None}

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        raw = await self.toolkit.call(name, args)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ToolCallFailed(raw) from exc

    async def threads(self, limit: int = 50) -> list[dict]:
        return await self.history.list_threads(limit)

    async def messages(self, thread_id: str) -> list[dict]:
        return await self.history.messages(thread_id)


async def _cli() -> None:
    thread_id = str(uuid.uuid4())
    async with RetailPilotChat() as chat:
        while True:
            text = (await asyncio.to_thread(input, "you> ")).strip()
            if text in ("exit", "quit"):
                break
            if not text:
                continue
            out = await chat.send(thread_id, text)
            if out["title"]:
                print(f"[thread: {out['title']}]")
            while out["question"]:
                out = await chat.answer(thread_id, await asyncio.to_thread(input, f"retailpilot ({out['role']}) asks: {out['question']}\nyou> "))
            print(f"retailpilot ({out['role']})> {out['reply']}\n")


if __name__ == "__main__":
    asyncio.run(_cli())
