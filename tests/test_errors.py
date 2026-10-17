"""Auth headers, refusals, input errors and network failures."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from conftest import API, call_tool, json_of
from leadgen_mcp.api import Config, _reset_note

pytestmark = pytest.mark.anyio
GET_A_KEY = "https://leadgen.adrianhomelab.com/start"


def refusal(error, message, **extra):
    body = {"error": error, "message": message, "get_a_key": GET_A_KEY, **extra}
    return {**body, "detail": dict(body)}


async def _usage_request(**config):
    with respx.mock(base_url=API) as router:
        route = router.get("/usage").mock(return_value=httpx.Response(200, json={"plan": "free"}))
        await call_tool("get_usage", **config)
    return route.calls.last.request


async def test_no_key_sends_no_auth_headers():
    request = await _usage_request()
    assert "authorization" not in request.headers
    assert "x-api-key" not in request.headers
    assert request.headers["x-leadgen-source"] == "glama"
    assert request.headers["user-agent"].startswith("leadgen-mcp/")


async def test_key_is_sent_as_bearer_and_x_api_key():
    request = await _usage_request(api_key="test-key-123")
    assert request.headers["authorization"] == "Bearer test-key-123"
    assert request.headers["x-api-key"] == "test-key-123"


def test_config_from_env():
    config = Config.from_env({"LEADGEN_API_KEY": "   ", "LEADGEN_API_URL": "http://localhost:9/",
                              "LEADGEN_SOURCE": "cursor"})
    assert config.api_key == "" and "Authorization" not in config.headers()
    assert config.api_url == "http://localhost:9" and config.source == "cursor"
    defaults = Config.from_env({})
    assert defaults.api_url == "https://leadgen.adrianhomelab.com" and defaults.source == "glama"
    assert Config.from_env({"LEADGEN_SOURCE": ""}).source == ""
    assert Config.from_env({"LEADGEN_TIMEOUT": "abc"}).timeout == 60.0


async def test_429_says_when_the_limit_resets():
    body = refusal("daily_limit_reached", "5 of 5 lookups used today.", limit=5, resets_at="2026-10-18T00:00:00Z")
    with respx.mock(base_url=API) as router:
        router.get("/lookup_business").mock(return_value=httpx.Response(
            429, json=body, headers={"RateLimit-Reset": "3600"}))
        result = await call_tool("lookup_business", {"query": "x"})
    assert result.isError
    text = result.content[0].text
    assert "HTTP 429, daily_limit_reached" in text
    assert "5 of 5 lookups used today." in text
    assert "resets at 2026-10-18T00:00:00Z (in 1 h 0 min)" in text
    assert GET_A_KEY in text
    assert '"detail"' not in text


def test_reset_note_falls_back_to_the_next_utc_midnight():
    response = httpx.Response(429, json={})
    now = datetime(2026, 10, 17, 22, 30, tzinfo=timezone.utc)
    assert _reset_note(response, {}, now) == \
        "The daily allowance resets at 2026-10-18T00:00:00Z (in 1 h 30 min) (00:00 UTC)."


@pytest.mark.parametrize("status,error,message", [
    (401, "invalid_api_key", "That API key is not recognised."),
    (403, "key_suspended", "This key is suspended."),
    (402, "paid_tool", "This tool needs a paid plan."),
    (402, "insufficient_credits", "No credits left."),
])
async def test_refusals_are_tool_errors_with_message_and_link(status, error, message):
    with respx.mock(base_url=API) as router:
        router.get("/lookup_business").mock(return_value=httpx.Response(status, json=refusal(error, message)))
        result = await call_tool("lookup_business", {"query": "x"}, api_key="test-key-123")
    assert result.isError
    text = result.content[0].text
    assert f"HTTP {status}, {error}" in text and message in text and GET_A_KEY in text
    if error == "invalid_api_key":
        assert "LEADGEN_API_KEY" in text


async def test_plan_required_is_a_tool_result_like_the_hosted_tool():
    body = refusal("plan_required", "Ratios are a Pro feature.", upgrade="https://example.test/up")
    with respx.mock(base_url=API) as router:
        router.get("/financials/2816464/ratios").mock(return_value=httpx.Response(402, json=body))
        result = await call_tool("lookup_financial_ratios", {"cui": "2816464"})
    assert not result.isError
    data = json_of(result)
    assert data["error"] == "plan_required" and "detail" not in data


async def test_input_errors_are_answers_with_error_set():
    body = {"error": "invalid_cui", "message": "Wrong control digit.",
            "detail": {"error": "invalid_cui", "message": "Wrong control digit."}}
    with respx.mock(base_url=API) as router:
        router.get("/lookup_business").mock(return_value=httpx.Response(400, json=body))
        result = await call_tool("lookup_business", {"query": "2816465"})
    assert not result.isError
    assert result.structuredContent == {"error": "invalid_cui", "message": "Wrong control digit."}


async def test_fastapi_validation_errors_are_tool_errors():
    body = {"detail": [{"loc": ["query", "min_revenue"], "msg": "Input should be greater than or equal to 0"}]}
    with respx.mock(base_url=API) as router:
        router.get("/lookup_by_category").mock(return_value=httpx.Response(422, json=body))
        result = await call_tool("lookup_by_category", {"topic": "software", "min_revenue": -1})
    assert result.isError
    assert result.content[0].text == "Invalid input: min_revenue: Input should be greater than or equal to 0"


async def test_server_errors_are_tool_errors():
    with respx.mock(base_url=API) as router:
        router.get("/usage").mock(return_value=httpx.Response(502, text="Bad Gateway"))
        result = await call_tool("get_usage")
    assert result.isError and "HTTP 502" in result.content[0].text


@pytest.mark.parametrize("exc,phrase", [
    (httpx.ConnectError("name resolution failed"), "Could not reach the Leadgen API"),
    (httpx.ReadTimeout("slow"), "did not answer within"),
])
async def test_network_failures_are_tool_errors_not_crashes(exc, phrase):
    with respx.mock(base_url=API) as router:
        router.get("/lookup_business").mock(side_effect=exc)
        result = await call_tool("lookup_business", {"query": "x"})
        assert result.isError and phrase in result.content[0].text
        # the server is still alive afterwards
        router.get("/usage").mock(return_value=httpx.Response(200, json={"plan": "free"}))
        assert not (await call_tool("get_usage")).isError


async def test_unknown_tool_is_an_error():
    result = await call_tool("no_such_tool", {})
    assert result.isError
