"""One real anonymous call to the public REST API. Runs only with LEADGEN_LIVE=1 (it counts
as one of the five anonymous lookups a day)."""

from __future__ import annotations

import os

import pytest

from leadgen_mcp.api import Config, LeadgenAPI
from leadgen_mcp.server import build_server

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("LEADGEN_LIVE") != "1", reason="set LEADGEN_LIVE=1 to call the live API"),
]


async def test_lookup_business_digi_romania_anonymous():
    from mcp.shared.memory import create_connected_server_and_client_session

    config = Config.from_env({"LEADGEN_SOURCE": os.environ.get("LEADGEN_SOURCE", "ci-live"),
                              "LEADGEN_API_URL": os.environ.get("LEADGEN_API_URL", "")})
    api = LeadgenAPI(config)
    try:
        async with create_connected_server_and_client_session(build_server(api)) as session:
            result = await session.call_tool("lookup_business", {"query": "DIGI ROMANIA", "max_results": 3})
    finally:
        await api.aclose()
    assert not result.isError, result.content[0].text
    data = result.structuredContent
    assert data["total"] >= 1
    names = [row.get("companyName", "") for row in data["results"]]
    assert any("DIGI" in name.upper() for name in names), names
    assert data.get("asOf")
