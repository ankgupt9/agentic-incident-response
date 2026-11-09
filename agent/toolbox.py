"""Tool access for the loop. The loop sees a Toolbox; production backs it with the MCP server."""

import os
import sys
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Protocol

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client
from mcp.types import TextContent

MAX_RESULT_CHARS = 8000
# The MCP SDK starts the server with a minimal environment, so the settings it needs are passed on.
PASSTHROUGH_ENV = ("LOKI_URL", "TEMPO_URL", "PROMETHEUS_URL", "DATABASE_URL", "POSTGRES_PASSWORD")


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False


class Toolbox(Protocol):
    def specs(self) -> list[dict]:
        """Tool definitions in the Anthropic shape: {name, description, input_schema}."""
        ...

    async def call(self, name: str, args: dict) -> ToolResult: ...


class FakeToolbox:
    """Scripted tools for tests. results maps tool name -> text, or a callable(args) -> text."""

    def __init__(self, results: dict[str, str | Callable[[dict], str]], errors: set[str] | None = None):
        self._results, self._errors = results, errors or set()
        self.calls: list[tuple[str, dict]] = []

    def specs(self) -> list[dict]:
        return [{"name": n, "description": f"fake {n}", "input_schema": {"type": "object", "properties": {}}} for n in self._results]

    async def call(self, name: str, args: dict) -> ToolResult:
        self.calls.append((name, args))
        if name not in self._results:
            return ToolResult(f"unknown tool {name!r}", is_error=True)
        out = self._results[name]
        return ToolResult(out(args) if callable(out) else out, is_error=name in self._errors)


class McpToolbox:
    """Talks to mcp_server/server.py over stdio. Use as an async context manager."""

    def __init__(self, command: str = sys.executable, args: list[str] | None = None, env: dict[str, str] | None = None):
        server_env = {**get_default_environment(), **{k: os.environ[k] for k in PASSTHROUGH_ENV if k in os.environ}, **(env or {})}
        self._params = StdioServerParameters(command=command, args=args or ["-m", "mcp_server.server"], env=server_env)
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self._specs: list[dict] = []

    async def __aenter__(self) -> "McpToolbox":
        read, write = await self._stack.enter_async_context(stdio_client(self._params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        listed = await self._session.list_tools()
        self._specs = [{"name": t.name, "description": t.description or "", "input_schema": t.inputSchema} for t in listed.tools]
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    def specs(self) -> list[dict]:
        return self._specs

    async def call(self, name: str, args: dict) -> ToolResult:
        assert self._session is not None, "use 'async with McpToolbox()'"
        try:
            result = await self._session.call_tool(name, args)
        except Exception as exc:
            return ToolResult(f"tool call failed: {type(exc).__name__}", is_error=True)
        text = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n... output truncated"
        return ToolResult(text, is_error=bool(result.isError))
