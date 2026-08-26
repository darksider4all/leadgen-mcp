"""Monetization scaffolding for the leadgen MCP — held behind a single flag.

Freemium model (agreed 2026-08-24, Adrian):
  - Free tier: registry-identity tools (lookup_business, lookup_director, ...)
  - Paid tier: financial statements (lookup_financials)

Everything is dormant while ``MONETIZATION_ENABLED`` is false — the middleware
only records telemetry and never blocks a request. Flipping the flag activates:

  1. Paid-tier gating  — tools named in ``PAID_TOOLS`` require
     ``Authorization: Bearer <key>`` where ``<key>`` is in ``API_KEYS``.
  2. Free-tier rate limit — unauthenticated clients are limited to
     ``FREE_TIER_CALLS`` tool calls per ``FREE_TIER_WINDOW_SECONDS`` per IP.
     Authenticated (paid) clients are unlimited.

The rate limiter is intentionally in-memory: the homelab runs a single
process. A multi-instance deployment must move ``_RateLimiter`` state to Redis.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

from mcp.shared.exceptions import MCPError


# --------------------------------------------------------------------------- #
# Config                                                                       #
# --------------------------------------------------------------------------- #
def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, "true" if default else "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


# The master switch. False = held (telemetry only, nothing blocked).
MONETIZATION_ENABLED = _env_bool("MONETIZATION_ENABLED", default=False)

# Free tier: this many tool calls per client IP per window.
FREE_TIER_CALLS = int(os.environ.get("FREE_TIER_CALLS", "100"))
FREE_TIER_WINDOW_SECONDS = int(os.environ.get("FREE_TIER_WINDOW_SECONDS", "86400"))  # 24 h

# Paid-tier API keys. Comma-separated; empty means no paid clients yet.
API_KEYS = frozenset(
    k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()
)

# Tools that require a paid key once monetization is enabled. Env-overridable so
# the set can change without a code edit (e.g. gate extract_contacts later).
PAID_TOOLS = frozenset(
    t.strip()
    for t in os.environ.get("PAID_TOOLS", "lookup_financials").split(",")
    if t.strip()
)

# Telemetry log — always written, even while held, so the free-tier limit can be
# sized from real data before the flag flips.
MONETIZATION_LOG = os.environ.get(
    "MONETIZATION_LOG", "/opt/leadgen-mcp/data/monetization.log"
)

# Client-IP header names in precedence order (Cloudflare -> Caddy proxy chain).
_IP_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "x-real-ip")


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
def client_ip(request) -> str:
    """Best-effort client IP from proxied headers, falling back to the peer."""
    headers = getattr(request, "headers", None)
    if headers is not None:
        for name in _IP_HEADERS:
            value = headers.get(name)
            if value:
                return value.split(",")[0].strip()
    client = getattr(request, "client", None)
    if client is not None and getattr(client, "host", None):
        return client.host
    return "unknown"


def valid_api_key(auth_header: str | None) -> bool:
    """True if the Authorization header carries a known paid API key."""
    if not auth_header or not API_KEYS:
        return False
    token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else auth_header.strip()
    return token in API_KEYS


class _RateLimiter:
    """Thread-safe sliding-window counter keyed by client IP."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


_rate_limiter = _RateLimiter(FREE_TIER_CALLS, FREE_TIER_WINDOW_SECONDS)


def _telemetry(ip: str, tool: str, authed: bool, action: str, args: dict | None = None) -> None:
    """Append a monetization sample to JSONL. Never raises."""
    import json

    try:
        os.makedirs(os.path.dirname(MONETIZATION_LOG), exist_ok=True)
        with open(MONETIZATION_LOG, "a") as f:
            rec = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "ip": ip,
                "tool": tool,
                "authed": authed,
                "action": action,
                "enabled": MONETIZATION_ENABLED,
            }
            if args:
                rec["args"] = {k: (str(v)[:80] if not isinstance(v, (list, dict)) else v) for k, v in args.items()}
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Middleware                                                                    #
# --------------------------------------------------------------------------- #
class MonetizationMiddleware:
    """Rate-limit the free tier + gate paid tools. No-op while held.

    Registered on ``MCPServer(middleware=[...])``, so it wraps every inbound
    message. It only acts on ``tools/call`` — initialize, tools/list and
    notifications pass straight through.
    """

    async def __call__(self, ctx, call_next):
        if ctx.method != "tools/call":
            return await call_next(ctx)

        params = ctx.params or {}
        tool = params.get("name", "")
        tool_args = params.get("arguments") or {}
        request = getattr(ctx, "request", None)

        ip = client_ip(request) if request is not None else "unknown"
        auth_header = None
        headers = getattr(request, "headers", None) if request is not None else None
        if headers is not None:
            auth_header = headers.get("authorization")
        authed = valid_api_key(auth_header)

        if MONETIZATION_ENABLED:
            if tool in PAID_TOOLS and not authed:
                _telemetry(ip, tool, authed, "paid_denied", tool_args)
                raise MCPError(
                    -32001,
                    f"'{tool}' requires a paid subscription. Pass "
                    "'Authorization: Bearer <key>' or upgrade at "
                    "https://hermes.adrianhomelab.com/mcp.",
                )
            if not authed and not _rate_limiter.allow(ip):
                _telemetry(ip, tool, authed, "rate_limited", tool_args)
                raise MCPError(
                    -32002,
                    f"Free-tier rate limit reached ({FREE_TIER_CALLS} calls per "
                    f"{FREE_TIER_WINDOW_SECONDS // 3600}h). Add an API key to continue.",
                )
            _telemetry(ip, tool, authed, "allowed", tool_args)
        else:
            _telemetry(ip, tool, authed, "telemetry_only", tool_args)

        return await call_next(ctx)
