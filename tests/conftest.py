from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from leadgen_mcp.api import Config, LeadgenAPI
from leadgen_mcp.server import build_server

API = "https://api.test"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_config(**overrides: Any) -> Config:
    values = {"api_url": API, "api_key": "", "source": "glama", "timeout": 5.0}
    values.update(overrides)
    return Config(**values)


async def call_tool(name: str, arguments: dict[str, Any] | None = None, **config: Any):
    """Call one tool through a real MCP client session (in memory) against the local server."""
    api = LeadgenAPI(make_config(**config))
    server = build_server(api)
    try:
        async with create_connected_server_and_client_session(server) as session:
            return await session.call_tool(name, arguments or {})
    finally:
        await api.aclose()


def text_of(result) -> str:
    return "".join(block.text for block in result.content if getattr(block, "type", "") == "text")


def json_of(result) -> Any:
    return json.loads(text_of(result))
