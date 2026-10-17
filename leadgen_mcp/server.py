"""The local stdio MCP server: the hosted Leadgen tools, answered through the public REST API."""

from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache
from importlib import resources
from typing import Any

import anyio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from leadgen_mcp import __version__
from leadgen_mcp.api import Config, LeadgenAPI, ToolError
from leadgen_mcp.handlers import HANDLERS

WEBSITE = "https://leadgen.adrianhomelab.com/"


@lru_cache(maxsize=1)
def snapshot() -> dict[str, Any]:
    """tools.json: the hosted server's tool definitions and instructions (scripts/sync_tools.py)."""
    text = resources.files("leadgen_mcp").joinpath("tools.json").read_text(encoding="utf-8")
    return json.loads(text)


def tool_definitions() -> list[dict[str, Any]]:
    return snapshot()["tools"]


def _result(data: dict) -> types.CallToolResult:
    """The hosted tools' answer: the JSON as text, and the same object as structuredContent."""
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=data,
    )


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=True)


def build_server(api: LeadgenAPI | None = None) -> Server:
    """The MCP server. No network is touched until a tool is called."""
    api = api or LeadgenAPI(Config.from_env())
    snap = snapshot()
    server: Server = Server(
        snap.get("server", {}).get("name", "leadgen"),
        version=__version__,
        instructions=snap.get("instructions"),
        website_url=snap.get("server", {}).get("websiteUrl", WEBSITE),
    )
    tools = [types.Tool.model_validate(tool) for tool in tool_definitions()]

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        handler = HANDLERS.get(name)
        if handler is None:
            return _error(f"Unknown tool: {name}")
        try:
            return _result(await handler(api, arguments or {}))
        except ToolError as exc:
            return _error(str(exc))
        except Exception as exc:  # never let one call take the server down
            return _error(f"leadgen-mcp could not complete {name}: {exc.__class__.__name__}: {exc}")

    return server


async def serve_stdio(api: LeadgenAPI) -> None:
    server = build_server(api)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(NotificationOptions(), {}),
            )
    finally:
        await api.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="leadgen-mcp",
        description="Local MCP server (stdio) for Leadgen's Romanian company data. "
                    "Environment: LEADGEN_API_KEY (optional), LEADGEN_SOURCE, LEADGEN_API_URL, "
                    "LEADGEN_TIMEOUT.",
    )
    parser.add_argument("--version", action="version", version=f"leadgen-mcp {__version__}")
    parser.add_argument("--transport", choices=["stdio"], default="stdio",
                        help="MCP transport (stdio; for HTTP use the hosted server)")
    parser.parse_args(argv)
    try:
        anyio.run(serve_stdio, LeadgenAPI(Config.from_env()))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
