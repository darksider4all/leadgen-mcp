"""tools.json against the hosted server card: names, descriptions and inputSchema.
Network-gated: runs only with LEADGEN_DRIFT=1 (CI runs it weekly)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

CARD = os.environ.get("LEADGEN_SERVER_CARD",
                      "https://leadgen-mcp.adrianhomelab.com/.well-known/mcp/server-card.json")
TOOLS_JSON = Path(__file__).resolve().parent.parent / "leadgen_mcp" / "tools.json"

pytestmark = pytest.mark.skipif(os.environ.get("LEADGEN_DRIFT") != "1",
                                reason="set LEADGEN_DRIFT=1 to compare with the live server card")


def _card_tools() -> dict[str, dict]:
    card = httpx.get(CARD, timeout=30, headers={"User-Agent": "leadgen-mcp-drift"}).raise_for_status().json()
    tools = card.get("tools") or card.get("serverInfo", {}).get("tools") or []
    return {tool["name"]: tool for tool in tools}


def test_tools_json_matches_the_live_server_card():
    live = _card_tools()
    local = {tool["name"]: tool for tool in json.loads(TOOLS_JSON.read_text(encoding="utf-8"))["tools"]}
    assert sorted(local) == sorted(live), "tool names differ: run scripts/sync_tools.py"
    for name, tool in local.items():
        assert tool["description"] == live[name]["description"], f"{name}: description drifted"
        assert tool["inputSchema"] == live[name]["inputSchema"], f"{name}: inputSchema drifted"
