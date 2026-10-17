#!/usr/bin/env python3
"""Snapshot the hosted Leadgen MCP server's tool definitions into leadgen_mcp/tools.json.

The local client registers its tools from that file, so names, titles, descriptions,
input and output schemas and annotations always match the hosted server. Run it after
the hosted server changes a tool:

    python scripts/sync_tools.py            # rewrite leadgen_mcp/tools.json
    python scripts/sync_tools.py --check    # exit 1 if the snapshot is out of date

initialize and tools/list are free: they never count against the daily allowance.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

DEFAULT_ENDPOINT = "https://leadgen-mcp.adrianhomelab.com/mcp"
TOOLS_JSON = Path(__file__).resolve().parent.parent / "leadgen_mcp" / "tools.json"
PROTOCOL_VERSION = "2025-06-18"


def _parse(response: httpx.Response) -> dict:
    """A JSON-RPC message from a JSON or an SSE (text/event-stream) response."""
    response.raise_for_status()
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                message = json.loads(line[5:].strip())
                if "result" in message or "error" in message:
                    return message
        raise RuntimeError("no JSON-RPC message in the event stream")
    return response.json()


def fetch(endpoint: str = DEFAULT_ENDPOINT, timeout: float = 30.0) -> dict:
    """initialize + tools/list against the hosted server; the snapshot to store."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "leadgen-mcp-sync",
    }
    with httpx.Client(timeout=timeout, headers=headers) as http:
        init = http.post(endpoint, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "leadgen-mcp-sync", "version": "1"},
            },
        })
        result = _parse(init)
        if "error" in result:
            raise RuntimeError(f"initialize failed: {result['error']}")
        info = result["result"]
        session = init.headers.get("mcp-session-id")
        extra = {"MCP-Protocol-Version": info.get("protocolVersion", PROTOCOL_VERSION)}
        if session:
            extra["Mcp-Session-Id"] = session
        http.post(endpoint, headers=extra,
                  json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools: list = []
        cursor = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            listed = _parse(http.post(endpoint, headers=extra, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": params}))
            if "error" in listed:
                raise RuntimeError(f"tools/list failed: {listed['error']}")
            tools.extend(listed["result"]["tools"])
            cursor = listed["result"].get("nextCursor")
            if not cursor:
                break
        if session:
            try:
                http.delete(endpoint, headers=extra)
            except httpx.HTTPError:
                pass
    server = info.get("serverInfo", {})
    return {
        "source": endpoint,
        "server": {k: server[k] for k in ("name", "title", "version", "websiteUrl") if k in server},
        "instructions": info.get("instructions"),
        "tools": tools,
    }


def render(snapshot: dict) -> str:
    return json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endpoint", default=os.environ.get("LEADGEN_MCP_URL", DEFAULT_ENDPOINT))
    parser.add_argument("--check", action="store_true", help="exit 1 if tools.json is out of date")
    args = parser.parse_args(argv)
    text = render(fetch(args.endpoint))
    current = TOOLS_JSON.read_text(encoding="utf-8") if TOOLS_JSON.exists() else ""
    if args.check:
        if text != current:
            print(f"{TOOLS_JSON.name} is out of date: run scripts/sync_tools.py", file=sys.stderr)
            return 1
        print(f"{TOOLS_JSON.name} is up to date")
        return 0
    TOOLS_JSON.write_text(text, encoding="utf-8")
    count = len(json.loads(text)["tools"])
    print(f"wrote {count} tools to {TOOLS_JSON.relative_to(Path.cwd()) if TOOLS_JSON.is_relative_to(Path.cwd()) else TOOLS_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
