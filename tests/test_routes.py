"""Every tool calls its REST twin with the right method, path, query and body."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from conftest import API, call_tool, json_of

pytestmark = pytest.mark.anyio

CASES = [
    ("lookup_business", {"query": "DIGI ROMANIA", "max_results": 3, "county": "Bucuresti", "compact": True},
     "GET", "/lookup_business", {"query": "DIGI ROMANIA", "max_results": "3", "county": "Bucuresti", "compact": "true"}, None),
    ("lookup_business", {"query": "2816464", "fields": "companyName,cui"},
     "GET", "/lookup_business", {"query": "2816464", "fields": "companyName,cui"}, None),
    ("lookup_director", {"name": "Ion Popescu", "prefix": True, "max_results": 5},
     "GET", "/lookup_director", {"name": "Ion Popescu", "prefix": "true", "max_results": "5"}, None),
    ("lookup_by_category", {"topic": "software", "county": "cluj", "active_only": False, "legal_form": "SRL,SA",
                            "has_website": True, "min_revenue": 1000000, "max_revenue": 5000000,
                            "min_employees": 5, "max_employees": 50, "vat_payer": True,
                            "main_activity_only": True, "max_results": 20},
     "GET", "/lookup_by_category", {"topic": "software", "county": "cluj", "active_only": "false",
                                    "legal_form": "SRL,SA", "has_website": "true", "min_revenue": "1000000",
                                    "max_revenue": "5000000", "min_employees": "5", "max_employees": "50",
                                    "vat_payer": "true", "main_activity_only": "true", "max_results": "20"}, None),
    ("find_company_by_domain", {"domain": "dedeman.ro", "compact": True},
     "GET", "/find_company_by_domain", {"domain": "dedeman.ro", "compact": "true"}, None),
    ("extract_contacts", {"website": "example.com", "max_pages": 2},
     "GET", "/extract_contacts", {"website": "example.com", "max_pages": "2"}, None),
    ("enrich_company", {"cui": "2816464", "website": "dedeman.ro", "max_pages": 3, "refresh": True},
     "GET", "/enrich_company", {"cui": "2816464", "website": "dedeman.ro", "max_pages": "3", "refresh": "true"}, None),
    ("lookup_domain", {"domain": "example.com", "include_dns": False, "include_security": True},
     "GET", "/lookup_domain", {"domain": "example.com", "include_dns": "false", "include_security": "true"}, None),
    ("lookup_financials", {"cui": "RO2816464"}, "GET", "/financials/RO2816464", {}, None),
    ("lookup_financial_ratios", {"cui": "2816464"}, "GET", "/financials/2816464/ratios", {}, None),
    ("get_usage", {}, "GET", "/usage", {}, None),
    ("lookup_companies", {"cuis": ["2816464", "RO14399840"], "compact": True},
     "POST", "/lookup_companies", {}, {"cuis": ["2816464", "RO14399840"], "compact": True}),
    ("lookup_companies", {"cuis": "2816464,14399840", "fields": "cui"},
     "POST", "/lookup_companies", {}, {"cuis": "2816464,14399840", "fields": "cui"}),
    ("match_companies", {"rows": [{"name": "Dedeman", "county": "Bacau"}, "emag.ro"], "compact": True},
     "POST", "/match_companies", {}, {"rows": [{"name": "Dedeman", "county": "Bacau"}, "emag.ro"], "compact": True}),
    ("report_issue", {"message": "Missing phone", "kind": "wrong_record", "cui": "2816464", "contact": "a@example.com"},
     "POST", "/feedback", {}, {"message": "Missing phone", "kind": "wrong_record", "cui": "2816464",
                               "email": "a@example.com", "ref": "glama"}),
]


@pytest.mark.parametrize("tool,args,method,path,query,body", CASES,
                         ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
async def test_tool_maps_to_rest_route(tool, args, method, path, query, body):
    answer = {"ok": True, "echo": tool}
    with respx.mock(base_url=API, assert_all_called=True) as router:
        route = router.route(method=method, path=path).mock(return_value=httpx.Response(200, json=answer))
        result = await call_tool(tool, args)
    assert not result.isError, result.content
    request = route.calls.last.request
    params = dict(request.url.params)
    assert params.pop("src") == "glama"
    assert params == query
    if body is None:
        assert request.content in (b"", None)
    else:
        assert json.loads(request.content) == body
    assert json_of(result) == answer
    assert result.structuredContent == answer


async def test_defaults_are_left_to_the_server():
    with respx.mock(base_url=API) as router:
        route = router.get("/lookup_business").mock(return_value=httpx.Response(200, json={"total": 0}))
        await call_tool("lookup_business", {"query": "x", "county": None})
    assert dict(route.calls.last.request.url.params) == {"query": "x", "src": "glama"}


async def test_cui_is_path_escaped():
    with respx.mock(base_url=API) as router:
        route = router.get(url__regex=r"/financials/.*").mock(return_value=httpx.Response(200, json={"found": False}))
        await call_tool("lookup_financials", {"cui": "12/34 5"})
    assert route.calls.last.request.url.raw_path.startswith(b"/financials/12%2F34%205")


async def test_empty_cui_is_a_tool_error_without_a_request():
    with respx.mock(base_url=API, assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(200, json={}))
        result = await call_tool("lookup_financials", {"cui": "  "})
        assert not router.calls
    assert result.isError


async def test_missing_required_argument_is_refused_locally():
    with respx.mock(base_url=API, assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(200, json={}))
        result = await call_tool("lookup_business", {})
        assert not router.calls
    assert result.isError
    assert "query" in result.content[0].text


async def test_unicode_is_kept_in_the_text():
    answer = {"results": [{"companyName": "PAVĂL SRL"}], "total": 1}
    with respx.mock(base_url=API) as router:
        router.get("/lookup_business").mock(return_value=httpx.Response(200, json=answer))
        result = await call_tool("lookup_business", {"query": "paval"})
    assert "PAVĂL" in result.content[0].text
    assert result.content[0].text == json.dumps(answer, indent=2, ensure_ascii=False)


async def test_no_source_means_no_src_and_no_header():
    with respx.mock(base_url=API) as router:
        route = router.get("/usage").mock(return_value=httpx.Response(200, json={"plan": "free"}))
        await call_tool("get_usage", source="")
    request = route.calls.last.request
    assert "src" not in request.url.params
    assert "x-leadgen-source" not in request.headers


async def test_report_issue_without_source_has_no_ref():
    with respx.mock(base_url=API) as router:
        route = router.post("/feedback").mock(return_value=httpx.Response(200, json={"status": "received", "id": 1}))
        await call_tool("report_issue", {"message": "hi"}, source="")
    assert json.loads(route.calls.last.request.content) == {"message": "hi"}
