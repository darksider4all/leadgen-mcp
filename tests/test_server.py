"""tools/list equals tools.json, and the server starts with no key and no network."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from leadgen_mcp.handlers import HANDLERS
from leadgen_mcp.server import build_server, snapshot

ROOT = Path(__file__).resolve().parent.parent
TOOLS_JSON = ROOT / "leadgen_mcp" / "tools.json"


def test_every_snapshot_tool_has_a_handler():
    names = [tool["name"] for tool in snapshot()["tools"]]
    assert len(names) == 17
    assert sorted(names) == sorted(HANDLERS)


@pytest.mark.anyio
async def test_tools_list_equals_tools_json_in_memory():
    async with create_connected_server_and_client_session(build_server()) as session:
        listed = await session.list_tools()
    wire = [tool.model_dump(by_alias=True, exclude_none=True, mode="json") for tool in listed.tools]
    assert wire == json.loads(TOOLS_JSON.read_text(encoding="utf-8"))["tools"]


def _rpc(proc, message):
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


def test_stdio_answers_initialize_and_tools_list_without_key_or_network():
    env = {k: v for k, v in os.environ.items() if not k.startswith("LEADGEN_")}
    # an unroutable API URL: initialize and tools/list must not touch it
    env["LEADGEN_API_URL"] = "http://127.0.0.1:9"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen([sys.executable, "-m", "leadgen_mcp"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        _rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"}}})
        init = json.loads(proc.stdout.readline())
        _rpc(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listed = json.loads(proc.stdout.readline())
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
    assert init["result"]["serverInfo"]["name"] == "leadgen"
    assert init["result"]["instructions"] == snapshot()["instructions"]
    assert listed["result"]["tools"] == json.loads(TOOLS_JSON.read_text(encoding="utf-8"))["tools"]
