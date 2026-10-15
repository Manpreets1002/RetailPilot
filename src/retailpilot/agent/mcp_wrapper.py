"""Connects the RetailPilot agent to the Amazon MCP server and exposes its tools as LangChain tools."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from langchain_core.tools import StructuredTool

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp" / "mcp_server.py"
IST = timezone(timedelta(hours=5, minutes=30))
DATE_AWARE_TOOLS = {"search_products"}


def today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def _unwrap(result: Any) -> Any:
    payload = result.structured_content
    if payload is None:
        text = "\n".join(block.text for block in result.content if hasattr(block, "text"))
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return payload["result"] if set(payload) == {"result"} else payload


class MCPToolkit:
    def __init__(self, server_path: Path = SERVER_PATH):
        env = {**os.environ, "PYTHONPATH": str(server_path.parents[2])}
        self.client = Client(StdioTransport(command=sys.executable, args=[str(server_path)], env=env))
        self.tools: list[StructuredTool] = []

    async def __aenter__(self) -> "MCPToolkit":
        await self.client.__aenter__()
        self.tools = [self._as_langchain_tool(t) for t in await self.client.list_tools()]
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.client.__aexit__(*exc_info)

    def _with_today(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name in DATE_AWARE_TOOLS and not args.get("observed_on"):
            return {**args, "observed_on": today_ist()}
        return args

    async def call(self, name: str, args: dict[str, Any]) -> str:
        result = await self.client.call_tool(name, self._with_today(name, args), raise_on_error=False)
        if result.is_error:
            return f"Tool {name} failed: {_unwrap(result)}"
        return json.dumps(_unwrap(result), default=str, ensure_ascii=False)

    def _as_langchain_tool(self, tool) -> StructuredTool:
        name = tool.name

        async def run(**kwargs: Any) -> str:
            return await self.call(name, kwargs)

        return StructuredTool(
            name=name,
            description=tool.description or "",
            args_schema=tool.inputSchema,
            coroutine=run,
        )
