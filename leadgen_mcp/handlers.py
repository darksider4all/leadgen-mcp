"""One handler per MCP tool: the arguments of the hosted tool in, its REST twin called,
the hosted tool's answer shape out."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

from leadgen_mcp.api import LeadgenAPI, ToolError

Handler = Callable[[LeadgenAPI, dict[str, Any]], Awaitable[dict]]


def _pick(args: dict[str, Any], *names: str) -> dict[str, Any]:
    """The named arguments the caller passed (None means 'not given', as in the schemas)."""
    return {name: args[name] for name in names if args.get(name) is not None}


def _cui_path(args: dict[str, Any]) -> str:
    cui = str(args.get("cui") or "").strip()
    if not cui:
        raise ToolError("Pass a CUI (Romanian tax code), e.g. '2816464'.")
    return quote(cui, safe="")


def _get(path: str, *names: str) -> Handler:
    async def handler(api: LeadgenAPI, args: dict[str, Any]) -> dict:
        return await api.request("GET", path, params=_pick(args, *names))
    return handler


def _post(path: str, *names: str) -> Handler:
    async def handler(api: LeadgenAPI, args: dict[str, Any]) -> dict:
        return await api.request("POST", path, json_body=_pick(args, *names))
    return handler


COMPANY_VIEW = ("fields", "compact")


async def lookup_financials(api: LeadgenAPI, args: dict[str, Any]) -> dict:
    return await api.request("GET", f"/financials/{_cui_path(args)}")


async def lookup_financial_ratios(api: LeadgenAPI, args: dict[str, Any]) -> dict:
    return await api.request("GET", f"/financials/{_cui_path(args)}/ratios")


async def report_issue(api: LeadgenAPI, args: dict[str, Any]) -> dict:
    body = _pick(args, "message", "kind", "cui")
    if args.get("contact") is not None:
        body["email"] = args["contact"]  # the REST body calls it `email`
    if api.config.source:
        body["ref"] = api.config.source
    return await api.request("POST", "/feedback", json_body=body)


# ---- watchlist ---------------------------------------------------------------------------

_ITEM_LISTS = ("companies", "caen", "items", "results", "watches")
_DONE = {"added": False, "already": True}
_ERRORS = {"not_found": "not_found", "cap": "watchlist_full"}


def _first_item(body: dict) -> tuple[dict | None, list[dict]]:
    for key in _ITEM_LISTS:
        items = body.get(key)
        if isinstance(items, list):
            items = [item for item in items if isinstance(item, dict)]
            if items:
                return items[0], items[1:]
    return None, []


def _watch_answer(body: dict, invalid_error: str) -> dict:
    """POST /watches answers per item (status added/already/not_found/invalid/cap);
    watch_company and watch_caen answer {ok, alreadyWatching, watch, warnings, asOf}."""
    if "watch" in body and ("ok" in body or "error" in body):
        return body  # already in the MCP tool's shape
    if body.get("error"):
        return body
    item, rest = _first_item(body)
    if item is None:
        raise ToolError("The Leadgen API accepted the watch but returned no item for it.")
    status = str(item.get("status") or "").lower()
    warnings = [w for w in (item.get("warnings") or []) + (body.get("warnings") or []) if w]
    if item.get("warning"):
        warnings.append(item["warning"])
    for extra in rest:
        target = extra.get("target") or extra.get("code") or extra.get("cui")
        warnings.append(f"also watched: {target} ({extra.get('status', 'added')})")
    watch = item.get("watch")
    if not isinstance(watch, dict):
        watch = {k: v for k, v in item.items()
                 if k not in ("status", "message", "error", "warnings", "warning", "asOf")}
    answer: dict[str, Any] = {}
    if status in _DONE:
        answer.update(ok=True, alreadyWatching=_DONE[status], watch=watch)
    else:
        error = item.get("error")
        if not error:
            error = invalid_error if status == "invalid" else _ERRORS.get(status, status or "not_added")
        answer.update(ok=False, alreadyWatching=False, watch=None, error=error)
    message = item.get("message") or body.get("message")
    if message:
        answer["message"] = message
    if warnings:
        answer["warnings"] = warnings
    as_of = body.get("asOf") or item.get("asOf")
    if as_of is not None:
        answer["asOf"] = as_of
    return answer


async def watch_company(api: LeadgenAPI, args: dict[str, Any]) -> dict:
    cui = str(args.get("cui") or "").strip()
    if not cui:
        raise ToolError("Pass the CUI (Romanian tax code) of the firm to watch, e.g. '2816464'.")
    body = await api.request("POST", "/watches", json_body={"companies": [cui]})
    return _watch_answer(body, "invalid_cui")


async def watch_caen(api: LeadgenAPI, args: dict[str, Any]) -> dict:
    code = str(args.get("code") or "").strip()
    if not code:
        raise ToolError("Pass a CAEN activity code to watch, e.g. '6210'.")
    item: dict[str, Any] = {"code": code}
    if args.get("county") is not None:
        item["county"] = args["county"]
    body = await api.request("POST", "/watches", json_body={"caen": [item]})
    return _watch_answer(body, "invalid_code")


async def poll_watchlist(api: LeadgenAPI, args: dict[str, Any]) -> dict:
    """POST /watches/seen drains the changes and returns them: the MCP tool's answer plus
    `ok` and `cleared`, which the REST route adds and are dropped here."""
    body = await api.request("POST", "/watches/seen")
    return {k: v for k, v in body.items() if k not in ("ok", "cleared")}


async def unwatch(api: LeadgenAPI, args: dict[str, Any]) -> dict:
    """DELETE /watches removes one watch; reshaped to the hosted unwatch answer. A watch the
    key does not hold is ok with removed 0, as the hosted tool answers it."""
    cui = str(args.get("cui") or "").strip()
    code = str(args.get("code") or "").strip()
    if bool(cui) == bool(code):
        return {"error": "give_cui_or_code",
                "message": "Pass cui (a watched firm) or code (a watched CAEN code, with its county), not both."}
    county = str(args.get("county") or "").strip() or None
    kind, target = ("company", cui) if cui else ("caen", code)
    params = {"kind": kind, "target": target}
    if kind == "caen" and county:
        params["county"] = county
    body = await api.request("DELETE", "/watches", params=params)
    if body.get("error") not in (None, "not_watching"):
        return body
    removed = 0 if body.get("error") else int(body.get("removed") or 0)
    return {"ok": True, "removed": removed, "kind": kind, "target": target,
            "county": county if kind == "caen" else None,
            "message": ("Watch removed." if removed else
                        "This key does not watch that; nothing changed. See poll_watchlist's active list.")}


HANDLERS: dict[str, Handler] = {
    "lookup_business": _get("/lookup_business", "query", "max_results", "county", *COMPANY_VIEW),
    "lookup_companies": _post("/lookup_companies", "cuis", *COMPANY_VIEW),
    "find_company_by_domain": _get("/find_company_by_domain", "domain", *COMPANY_VIEW),
    "match_companies": _post("/match_companies", "rows", *COMPANY_VIEW),
    "lookup_by_category": _get(
        "/lookup_by_category", "topic", "county", "max_results", "legal_form", "active_only",
        "has_website", "min_revenue", "max_revenue", "min_employees", "max_employees",
        "vat_payer", "main_activity_only", "sort", *COMPANY_VIEW),
    "lookup_director": _get("/lookup_director", "name", "max_results", "prefix", *COMPANY_VIEW),
    "extract_contacts": _get("/extract_contacts", "website", "max_pages"),
    "enrich_company": _get("/enrich_company", "cui", "website", "max_pages", "refresh"),
    "lookup_domain": _get("/lookup_domain", "domain", "include_dns", "include_security"),
    "lookup_financials": lookup_financials,
    "lookup_financial_ratios": lookup_financial_ratios,
    "watch_company": watch_company,
    "watch_caen": watch_caen,
    "poll_watchlist": poll_watchlist,
    "unwatch": unwatch,
    "get_usage": _get("/usage"),
    "report_issue": report_issue,
}
