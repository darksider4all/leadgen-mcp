"""Leadgen Data Wrapper — read-only HTTP API for the ONRC registry.

Thin REST layer over the Leadgen MCP's DB-backed lookups so the Apify
store actor (ro-business-data-mcp) can query the official ONRC snapshot
without bundling the 3.6 GB SQLite. Runs as a FastAPI/uvicorn service behind
the Cloudflare tunnel (onrc-api.adrianhomelab.com).

Only the DB-backed tools are exposed here (lookup_business, lookup_director).
extract_contacts and lookup_domain are stateless web tools that run inside
the actor itself — no registry data needed.

Read-only, no auth (public open data). Optional shared secret via env
WRAPPER_API_KEY; the actor sends it as X-API-Key when configured.

2026-08-26: added anonymous query/IP/UA telemetry (rest_telemetry rows into
the shared ``monetization.log`` JSONL — same stream as the MCP middleware),
a light in-memory per-IP rate limit (generous; protects the SQLite FTS), and
a public landing page at "/". The service stays FREE and open.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from server import lookup_business as _lookup_business
from server import lookup_director as _lookup_director
from monetization import client_ip, MONETIZATION_ENABLED, _RateLimiter


def _log_rest(ip: str, tool: str, **args) -> None:
    """Append a REST-call telemetry row (same JSONL as the MCP middleware)."""
    import json
    import time

    try:
        path = os.environ.get(
            "MONETIZATION_LOG", "/opt/leadgen-mcp/data/monetization.log"
        )
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ip": ip,
            "tool": tool,
            "authed": False,
            "action": "rest_telemetry",
            "enabled": MONETIZATION_ENABLED,
        }
        if args:
            rec["args"] = {
                k: (str(v)[:80] if not isinstance(v, (list, dict)) else v)
                for k, v in args.items()
            }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


app = FastAPI(
    title="Leadgen ONRC Data Wrapper",
    version="1.1.0",
    description="Free read-only API over Romania's official business registry (ONRC).",
)

API_KEY = os.environ.get("WRAPPER_API_KEY", "")

# Generous anonymous rate limit (protects the SQLite FTS from scrapers).
_limiter = _RateLimiter(
    int(os.environ.get("WRAPPER_RATE_LIMIT", "100")),
    int(os.environ.get("WRAPPER_RATE_WINDOW_SECONDS", "60")),
)


def _gate(request: Request, tool: str, **args) -> None:
    ip = client_ip(request)
    if not _limiter.allow(ip):
        raise HTTPException(
            status_code=429,
            detail="rate-limit reached — slow down or get in touch for higher limits.",
        )
    _log_rest(ip, tool, **args)


def _check_key(x_api_key: str | None) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid-api-key")


@app.get("/health")
def health():
    return {"status": "ok", "service": "leadgen-onrc-wrapper"}


@app.get("/lookup_business")
def lookup_business(
    query: str = Query(..., description="Company name or CUI"),
    max_results: int = Query(10, ge=1, le=100),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    request: Request = None,
):
    _check_key(x_api_key)
    if request is not None:
        _gate(request, "lookup_business", query=query, max_results=max_results)
    return _lookup_business(query, max_results)


@app.get("/lookup_director")
def lookup_director(
    name: str = Query(..., description="Director / legal representative name"),
    max_results: int = Query(10, ge=1, le=100),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    request: Request = None,
):
    _check_key(x_api_key)
    if request is not None:
        _gate(request, "lookup_director", name=name, max_results=max_results)
    return _lookup_director(name, max_results)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing():
    return _LANDING


_LANDING = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Leadgen — Romanian Business Registry API (ONRC)</title>
<meta name="description" content="Free, read-only JSON API over Romania's official ONRC business registry: 4.2M companies, 19.3M CAEN activities, 3.68M representatives, 2025 financials."/>
<style>
:root{--ink:#1a1a2e;--mut:#5a5a72;--acc:#2563eb;--bg:#f7f8fb;--card:#ffffff;--code:#0f172a;}
*{box-sizing:border-box}
body{margin:0;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}
.wrap{max-width:920px;margin:0 auto;padding:40px 24px}
h1{font-size:2.1rem;margin:0 0 6px;letter-spacing:-.02em}
h2{font-size:1.25rem;margin:36px 0 12px}
.lede{color:var(--mut);font-size:1.08rem;max-width:640px}
.badge{display:inline-block;background:#ecfdf5;color:#047857;border:1px solid #a7f3d0;border-radius:999px;padding:3px 12px;font-size:.82rem;font-weight:600;margin:8px 6px 0 0}
code,pre{font-family:"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;font-size:.9rem}
pre{background:var(--code);color:#e2e8f0;padding:16px 18px;border-radius:10px;overflow-x:auto;line-height:1.5}
pre .c{color:#7dd3fc}
table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid #e5e7eb;border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:10px 14px;border-bottom:1px solid #eef0f4;vertical-align:top}
th{background:#fafbfd;font-size:.85rem;text-transform:uppercase;letter-spacing:.03em;color:var(--mut)}
td code{color:var(--acc)}
a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}
.foot{color:var(--mut);font-size:.88rem;margin-top:40px;border-top:1px solid #e5e7eb;padding-top:16px}
</style>
</head>
<body>
<div class="wrap">
<h1>Leadgen — ONRC Business Registry API</h1>
<p class="lede">Free, read-only JSON access to Romania's official business registry: every registered company, its CAEN activities, legal representatives, status, and 2025 financial statements.</p>
<div>
<span class="badge">Free · no auth</span>
<span class="badge">Read-only</span>
<span class="badge">Official ONRC data</span>
</div>

<h2>What's inside</h2>
<table>
<tr><th>Dataset</th><th>Records</th></tr>
<tr><td>Companies (<code>firme</code>)</td><td>4,196,860</td></tr>
<tr><td>CAEN activity records</td><td>19.3M</td></tr>
<tr><td>Legal representatives</td><td>3.68M</td></tr>
<tr><td>Company statuses</td><td>4.64M</td></tr>
<tr><td>2025 financial statements</td><td>loaded</td></tr>
</table>

<h2>Endpoints</h2>
<table>
<tr><th>Endpoint</th><th>Description</th></tr>
<tr><td><code>GET /lookup_business?query=&lt;name|CUI&gt;&amp;max_results=N</code></td><td>Search companies by name or tax code (CUI).</td></tr>
<tr><td><code>GET /lookup_director?name=&lt;name&gt;&amp;max_results=N</code></td><td>Find companies by director / legal-representative name.</td></tr>
<tr><td><code>GET /docs</code></td><td>Interactive OpenAPI docs (Swagger UI).</td></tr>
<tr><td><code>GET /health</code></td><td>Liveness check.</td></tr>
</table>

<h2>Try it</h2>
<pre><span class="c"># search by company name</span>
curl "https://onrc-api.adrianhomelab.com/lookup_business?query=DEDEMAN&max_results=3"

<span class="c"># search by CUI (tax code)</span>
curl "https://onrc-api.adrianhomelab.com/lookup_business?query=2816464"

<span class="c"># find companies by director name</span>
curl "https://onrc-api.adrianhomelab.com/lookup_director?name=popescu"</pre>

<h2>Sample response</h2>
<pre>{
  "query": "DEDEMAN",
  "total": 2,
  "results": [
    {
      "companyName": "DEDEMAN SRL",
      "cui": "2816464",
      "registrationCode": "J1992002621040",
      "legalForm": "SRL",
      "address": "Municipiul Bacău, Bacău, ...",
      "county": "Bacău",
      "website": "www.dedeman.ro",
      "caenActivities": [ { "code": "0125", "activity": "..." } ],
      "directors": [ "PAVĂL I. DRAGOŞ", "PAVĂL ADRIAN" ],
      "status": [ { "code": "1048", "name": "funcțiune" } ]
    }
  ]
}</pre>

<h2>Where the data comes from</h2>
<p class="lede">The registry is built from the official ONRC snapshot published on <a href="https://data.gov.ro">data.gov.ro</a>, indexed locally with SQLite FTS5 for fast, diacritic-insensitive full-text search. No scraping, no third-party vendor — just the official open data, made queryable.</p>

<h2>Use it from an MCP client</h2>
<p>There's also a full MCP server (business lookup, contact extraction, domain/WHOIS audit) at <code>https://hermes.adrianhomelab.com/mcp</code>, plus an <a href="https://apify.com/darksider4all/ro-business-data-mcp">Apify actor</a> for no-code access.</p>

<p class="foot">Leadgen · open data, open API. Questions or higher rate limits — get in touch via the project's GitHub and dev.to links.</p>
</div>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8767, log_level="info")
