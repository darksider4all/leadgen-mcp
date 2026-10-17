"""watch_company, watch_caen and poll_watchlist over the /watches routes."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from conftest import API, call_tool

pytestmark = pytest.mark.anyio
KEY = {"api_key": "test-key-123"}


async def test_watch_company_posts_one_company_and_reshapes_the_item():
    body = {"companies": [{"target": "2816464", "status": "added", "watch": {"kind": "company", "target": "2816464"}}],
            "caen": [], "asOf": {"registry": "2026-09-02"}}
    with respx.mock(base_url=API) as router:
        route = router.post("/watches").mock(return_value=httpx.Response(200, json=body))
        result = await call_tool("watch_company", {"cui": "2816464"}, **KEY)
    assert json.loads(route.calls.last.request.content) == {"companies": ["2816464"]}
    assert result.structuredContent == {"ok": True, "alreadyWatching": False,
                                        "watch": {"kind": "company", "target": "2816464"},
                                        "asOf": {"registry": "2026-09-02"}}


async def test_watch_company_already_held():
    body = {"companies": [{"target": "2816464", "status": "already", "kind": "company"}]}
    with respx.mock(base_url=API) as router:
        router.post("/watches").mock(return_value=httpx.Response(200, json=body))
        result = await call_tool("watch_company", {"cui": "2816464"}, **KEY)
    data = result.structuredContent
    assert data["ok"] is True and data["alreadyWatching"] is True
    assert data["watch"] == {"target": "2816464", "kind": "company"}


@pytest.mark.parametrize("status,error", [("not_found", "not_found"), ("invalid", "invalid_cui"), ("cap", "watchlist_full")])
async def test_watch_company_refused_items(status, error):
    body = {"companies": [{"target": "1", "status": status, "message": "no"}]}
    with respx.mock(base_url=API) as router:
        router.post("/watches").mock(return_value=httpx.Response(200, json=body))
        result = await call_tool("watch_company", {"cui": "1"}, **KEY)
    data = result.structuredContent
    assert data["ok"] is False and data["error"] == error and data["message"] == "no"


async def test_watch_company_passes_through_an_mcp_shaped_answer():
    body = {"ok": True, "alreadyWatching": False, "watch": {"kind": "company"}, "asOf": {}}
    with respx.mock(base_url=API) as router:
        router.post("/watches").mock(return_value=httpx.Response(200, json=body))
        result = await call_tool("watch_company", {"cui": "2816464"}, **KEY)
    assert result.structuredContent == body


async def test_watch_caen_posts_code_and_county_and_keeps_warnings():
    body = {"caen": [{"target": "6210", "county": "CLUJ", "status": "added",
                      "warnings": ["6201 is CAEN Rev.2; also watching its Rev.3 successor 6210"]}]}
    with respx.mock(base_url=API) as router:
        route = router.post("/watches").mock(return_value=httpx.Response(200, json=body))
        result = await call_tool("watch_caen", {"code": "6201", "county": "Cluj"}, **KEY)
    assert json.loads(route.calls.last.request.content) == {"caen": [{"code": "6201", "county": "Cluj"}]}
    data = result.structuredContent
    assert data["ok"] is True and data["watch"] == {"target": "6210", "county": "CLUJ"}
    assert data["warnings"] == ["6201 is CAEN Rev.2; also watching its Rev.3 successor 6210"]


async def test_watch_caen_nationwide_sends_no_county():
    with respx.mock(base_url=API) as router:
        route = router.post("/watches").mock(return_value=httpx.Response(200, json={"caen": [{"status": "added"}]}))
        await call_tool("watch_caen", {"code": "4321"}, **KEY)
    assert json.loads(route.calls.last.request.content) == {"caen": [{"code": "4321"}]}


async def test_watch_without_a_key_is_a_clear_error():
    body = {"error": "api_key_required", "message": "A watchlist belongs to an API key: send it as X-API-Key.",
            "get_a_key": "https://leadgen.adrianhomelab.com/start",
            "detail": {"error": "api_key_required", "message": "...", "get_a_key": "..."}}
    with respx.mock(base_url=API) as router:
        router.post("/watches").mock(return_value=httpx.Response(401, json=body))
        result = await call_tool("watch_company", {"cui": "2816464"})
    assert result.isError
    text = result.content[0].text
    assert "api_key_required" in text and "https://leadgen.adrianhomelab.com/start" in text
    assert '"detail"' not in text


async def test_poll_is_one_drain_call_without_ok_and_cleared():
    drained = {"total": 1, "owner": "key abc", "email_alerts": "POST /alerts/email",
               "asOf": {"registry": "2026-09-02"},
               "watches": [{"kind": "caen", "target": "4321", "new": [{"cui": "1"}]}]}
    with respx.mock(base_url=API, assert_all_called=False) as router:
        seen = router.post("/watches/seen").mock(
            return_value=httpx.Response(200, json={"ok": True, "cleared": 1, **drained}))
        get = router.get("/watches").mock(return_value=httpx.Response(200, json={}))
        result = await call_tool("poll_watchlist", {}, **KEY)
        assert len(router.calls) == 1 and seen.called and not get.called
        request = seen.calls.last.request
        assert request.headers["x-api-key"] == "test-key-123"
        assert "authorization" not in request.headers
    assert not result.isError
    assert result.structuredContent == drained


async def test_poll_without_a_key_is_a_clear_error():
    body = {"error": "api_key_required", "message": "A watchlist belongs to an API key: send it as X-API-Key.",
            "get_a_key": "https://leadgen.adrianhomelab.com/start"}
    with respx.mock(base_url=API) as router:
        router.post("/watches/seen").mock(return_value=httpx.Response(401, json=body))
        result = await call_tool("poll_watchlist", {})
    assert result.isError and "api_key_required" in result.content[0].text


async def test_unwatch_company_deletes_and_reshapes():
    with respx.mock(base_url=API) as router:
        route = router.delete("/watches").mock(return_value=httpx.Response(200, json={"ok": True, "removed": 1, "limits": {}}))
        result = await call_tool("unwatch", {"cui": "2816464"}, **KEY)
    assert dict(route.calls.last.request.url.params) == {"kind": "company", "target": "2816464", "src": "glama"}
    data = result.structuredContent
    assert data["ok"] is True and data["removed"] == 1 and data["kind"] == "company" and data["county"] is None


async def test_unwatch_caen_passes_county_and_not_held_is_removed_zero():
    body = {"error": "not_watching", "message": "This key does not watch that."}
    with respx.mock(base_url=API) as router:
        route = router.delete("/watches").mock(return_value=httpx.Response(404, json=body))
        result = await call_tool("unwatch", {"code": "4321", "county": "Cluj"}, **KEY)
    assert dict(route.calls.last.request.url.params) == {"kind": "caen", "target": "4321", "county": "Cluj", "src": "glama"}
    data = result.structuredContent
    assert data["ok"] is True and data["removed"] == 0 and data["county"] == "Cluj"


@pytest.mark.parametrize("args", [{}, {"cui": "2816464", "code": "4321"}])
async def test_unwatch_needs_exactly_one_target(args):
    with respx.mock(base_url=API, assert_all_called=False):
        result = await call_tool("unwatch", args, **KEY)
    assert result.structuredContent["error"] == "give_cui_or_code"
