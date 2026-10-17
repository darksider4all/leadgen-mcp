"""HTTP client for Leadgen's public REST API, with refusals turned into clear tool errors."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from leadgen_mcp import __version__

DEFAULT_API_URL = "https://leadgen.adrianhomelab.com"
DEFAULT_SOURCE = "glama"
DEFAULT_TIMEOUT = 60.0
GET_A_KEY = "https://leadgen.adrianhomelab.com/start"

# HTTP statuses whose body is a refusal (no data): they become tool errors.
REFUSALS = {401, 402, 403, 429}


class ToolError(Exception):
    """A call that produced no answer; its text is shown to the model as an error result."""


@dataclass(frozen=True)
class Config:
    api_url: str = DEFAULT_API_URL
    api_key: str = ""
    source: str = DEFAULT_SOURCE
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        url = (env.get("LEADGEN_API_URL") or "").strip() or DEFAULT_API_URL
        source = env.get("LEADGEN_SOURCE")
        source = DEFAULT_SOURCE if source is None else source.strip()
        try:
            timeout = float(env.get("LEADGEN_TIMEOUT") or DEFAULT_TIMEOUT)
        except ValueError:
            timeout = DEFAULT_TIMEOUT
        return cls(
            api_url=url.rstrip("/"),
            api_key=(env.get("LEADGEN_API_KEY") or "").strip(),
            source=source,
            timeout=timeout,
        )

    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": f"leadgen-mcp/{__version__} (+https://github.com/darksider4all/leadgen-mcp)",
        }
        if self.api_key:
            # X-API-Key is the header the REST docs name; it is never sent empty.
            headers["X-API-Key"] = self.api_key
        if self.source:
            headers["X-Leadgen-Source"] = self.source
        return headers


def _strip_mirror(body: dict) -> dict:
    """Drop the legacy `detail` copy the REST API adds next to a top-level error body."""
    detail = body.get("detail")
    if isinstance(detail, dict) and "error" in body and detail.get("error") == body.get("error"):
        body = {k: v for k, v in body.items() if k != "detail"}
    return body


def _reset_note(response: httpx.Response, body: dict, now: datetime | None = None) -> str:
    """When the daily allowance resets, from the body, the RateLimit headers or the UTC day."""
    now = now or datetime.now(timezone.utc)
    resets_at = body.get("resets_at") or body.get("resetsAt")
    seconds: float | None = None
    for name in ("resets_in_seconds", "retry_after"):
        if isinstance(body.get(name), (int, float)):
            seconds = float(body[name])
            break
    if seconds is None:
        for header in ("RateLimit-Reset", "Retry-After", "X-RateLimit-Reset"):
            value = response.headers.get(header)
            if value and value.strip().isdigit():
                seconds = float(value.strip())
                break
    if not resets_at:
        if seconds is not None:
            when = now + timedelta(seconds=seconds)
        else:
            when = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            seconds = (when - now).total_seconds()
        resets_at = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    note = f"The daily allowance resets at {resets_at}"
    if seconds is not None:
        minutes = max(0, int(seconds // 60))
        note += f" (in {minutes // 60} h {minutes % 60} min)"
    return note + " (00:00 UTC)."


def refusal_text(response: httpx.Response, body: dict, has_key: bool) -> str:
    """A clear sentence for a 401/402/403/429, then the server's own body as JSON."""
    status = response.status_code
    code = body.get("error") or {401: "unauthorized", 402: "payment_required",
                                 403: "forbidden", 429: "daily_limit_reached"}[status]
    message = body.get("message") or (body.get("detail") if isinstance(body.get("detail"), str) else "")
    parts = [f"Leadgen refused the call (HTTP {status}, {code})."]
    if message:
        parts.append(str(message))
    if status == 429:
        parts.append(_reset_note(response, body))
    if status == 401 and code == "invalid_api_key" and has_key:
        parts.append("Check the LEADGEN_API_KEY environment variable.")
    get_a_key = body.get("get_a_key") or GET_A_KEY
    if status in (401, 429) or code in ("api_key_required", "key_suspended"):
        parts.append(f"Get a free key: {get_a_key}")
    elif get_a_key and get_a_key not in " ".join(parts):
        parts.append(f"Keys and plans: {get_a_key}")
    text = " ".join(parts)
    return text + "\n\n" + json.dumps(body, indent=2, ensure_ascii=False)


def _validation_text(body: dict) -> str:
    detail = body.get("detail")
    if isinstance(detail, list):
        bits = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(p) for p in item.get("loc", []) if p not in ("query", "body", "path"))
                bits.append(f"{loc}: {item.get('msg')}" if loc else str(item.get("msg")))
        if bits:
            return "Invalid input: " + "; ".join(bits)
    return "Invalid input: " + json.dumps(body, ensure_ascii=False)


@dataclass
class LeadgenAPI:
    config: Config = field(default_factory=Config.from_env)
    transport: httpx.AsyncBaseTransport | None = None
    _client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.api_url,
                headers=self.config.headers(),
                timeout=self.config.timeout,
                transport=self.transport,
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                      json_body: Any = None) -> dict:
        """One REST call. Returns the JSON answer (including answers whose `error` is set);
        raises ToolError for refusals, server errors and network failures."""
        query = {k: _wire(v) for k, v in (params or {}).items() if v is not None}
        if self.config.source:
            query.setdefault("src", self.config.source)
        try:
            response = await self._http().request(method, path, params=query, json=json_body)
        except httpx.TimeoutException:
            raise ToolError(
                f"The Leadgen API at {self.config.api_url} did not answer within "
                f"{self.config.timeout:g} s. Try again, or raise LEADGEN_TIMEOUT.") from None
        except httpx.HTTPError as exc:
            raise ToolError(
                f"Could not reach the Leadgen API at {self.config.api_url}: "
                f"{exc.__class__.__name__}: {exc}. Check the network connection "
                f"or LEADGEN_API_URL.") from None

        try:
            body = response.json()
        except ValueError:
            body = None

        status = response.status_code
        if 200 <= status < 300:
            if isinstance(body, dict):
                return body
            if isinstance(body, list):
                return {"items": body}
            raise ToolError(f"The Leadgen API answered HTTP {status} without a JSON object.")
        if status in REFUSALS and isinstance(body, dict):
            body = _strip_mirror(body)
            if status == 402 and body.get("error") == "plan_required":
                return body  # the hosted MCP tools hand this back as a tool result
            raise ToolError(refusal_text(response, body, bool(self.config.api_key)))
        if status in REFUSALS:
            raise ToolError(refusal_text(response, {}, bool(self.config.api_key)))
        if status == 422 and isinstance(body, dict) and "error" not in body:
            raise ToolError(_validation_text(body))
        if 400 <= status < 500 and isinstance(body, dict) and ("error" in body or "found" in body):
            return _strip_mirror(body)  # input errors and not-found answers, as the MCP tools give them
        snippet = response.text[:300].strip()
        raise ToolError(f"The Leadgen API answered HTTP {status} for {method} {path}"
                        + (f": {snippet}" if snippet else "."))


def _wire(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value
