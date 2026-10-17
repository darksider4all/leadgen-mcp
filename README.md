# Leadgen MCP — Romanian company data (ONRC, ANAF, financial statements)

<!-- mcp-name: com.adrianhomelab/leadgen-onrc -->

A remote [Model Context Protocol](https://modelcontextprotocol.io) server for Romania's official business
registry: **4.2M firms** from ONRC, the ANAF fiscal layer (VAT, e-Factura, fiscal inactivity, phone)
and **18 years of financial statements** (Ministry of Finance, 2008–2025) with ratios and peer benchmarks.

- **Endpoint:** `https://leadgen-mcp.adrianhomelab.com/mcp` (Streamable HTTP)
- **Free:** 5 lookups a day without a key, **25 a day with a free key** from
  <https://leadgen.adrianhomelab.com/start> — send it as `Authorization: Bearer <key>` (or `X-API-Key`).
- **REST API and docs:** <https://leadgen.adrianhomelab.com/docs> · **for LLMs:** <https://leadgen.adrianhomelab.com/llms.txt>
- **Server card:** <https://leadgen-mcp.adrianhomelab.com/.well-known/mcp/server-card.json> ·
  official MCP registry: `com.adrianhomelab/leadgen-onrc`

This repository holds the public description of the server and an open-source local MCP client
([below](#run-locally-open-client)). The hosted server's source is not public.

## Connect

**Claude Code**

```sh
claude mcp add --transport http leadgen https://leadgen-mcp.adrianhomelab.com/mcp --header "Authorization: Bearer <your key>"
```

or in `.mcp.json`:

```json
{
  "mcpServers": {
    "leadgen": {
      "type": "http",
      "url": "https://leadgen-mcp.adrianhomelab.com/mcp",
      "headers": { "Authorization": "Bearer <your key>" }
    }
  }
}
```

**Claude (claude.ai and Claude Desktop):** Customize → Connectors → Add custom connector →
URL `https://leadgen-mcp.adrianhomelab.com/mcp`, no sign-in; add the `Authorization` header with your key if your plan allows custom headers.

**Cursor** (`~/.cursor/mcp.json` or `.cursor/mcp.json`):

```json
{ "mcpServers": { "leadgen": { "url": "https://leadgen-mcp.adrianhomelab.com/mcp", "headers": { "Authorization": "Bearer <your key>" } } } }
```

**VS Code** (`.vscode/mcp.json`):

```json
{ "servers": { "leadgen": { "type": "http", "url": "https://leadgen-mcp.adrianhomelab.com/mcp", "headers": { "Authorization": "Bearer <your key>" } } } }
```

Without a key every client works for 5 calls a day; `get_usage` tells you what is left.

## Run locally (open client)

The hosted endpoint above is the main product and the simplest way in. If your client only speaks
stdio, or you want to run the MCP server yourself, this repository also ships `leadgen-mcp`: a small
open-source (MIT) local MCP server with the same 16 tools. It holds no data: every tool calls Leadgen's
public REST API (<https://leadgen.adrianhomelab.com/docs>), so answers, limits and keys are the same as
on the hosted server. Tool names, descriptions and schemas are a snapshot of the hosted server's
`tools/list` (`leadgen_mcp/tools.json`, refreshed with `scripts/sync_tools.py`).

It is on PyPI as [`leadgen-mcp`](https://pypi.org/project/leadgen-mcp/) (Python 3.10 or newer):

```sh
uvx leadgen-mcp
```

or install it:

```sh
pip install leadgen-mcp
leadgen-mcp
```

To run the latest commit on `main` instead of the release:
`uvx --from git+https://github.com/darksider4all/leadgen-mcp leadgen-mcp`.

or run it in Docker: `docker build -t leadgen-mcp .` then `docker run -i --rm -e LEADGEN_API_KEY leadgen-mcp`.

| Variable | Default | What it does |
|---|---|---|
| `LEADGEN_API_KEY` | none | Your free key from <https://leadgen.adrianhomelab.com/start> (25 lookups a day instead of 5). Optional; sent only when set. |
| `LEADGEN_SOURCE` | `glama` | A channel tag sent with each request (`X-Leadgen-Source` header and `src` parameter), so Leadgen knows where its users come from. |
| `LEADGEN_API_URL` | `https://leadgen.adrianhomelab.com` | REST API base URL. |
| `LEADGEN_TIMEOUT` | `60` | Seconds to wait for an answer (website crawls can take a while). |

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "leadgen": {
      "command": "uvx",
      "args": ["leadgen-mcp"],
      "env": { "LEADGEN_API_KEY": "<your key>", "LEADGEN_SOURCE": "claude-desktop" }
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json` or `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "leadgen": {
      "command": "uvx",
      "args": ["leadgen-mcp"],
      "env": { "LEADGEN_API_KEY": "<your key>", "LEADGEN_SOURCE": "cursor" }
    }
  }
}
```

Leave `LEADGEN_API_KEY` out to use the 5 anonymous lookups a day.

How it relates to the hosted server: same tools, same data, and the same daily allowance
(one allowance per key, or per IP without one, whichever door the call comes through). A refusal (no key, daily limit, suspended key) comes back as a tool
error that keeps the server's message and the link to a free key; for the daily limit it says when it
resets (00:00 UTC). The key is sent as the `X-API-Key` header, only when it is set. Watches need a key.

Development: `pip install -e ".[test]" && pytest` (no network); `LEADGEN_LIVE=1 pytest tests/test_live.py`
makes one real anonymous lookup; `LEADGEN_DRIFT=1 pytest tests/test_drift.py` compares `tools.json`
with the live server card; `python scripts/check_public.py` checks that nothing private is committed.

## Tools

| Tool | What it does |
|---|---|
| `lookup_business` | Look up a Romanian company by name or CUI (tax code) in the official ONRC registry of 4.2M firms. |
| `lookup_companies` | Look up many Romanian companies by CUI in one call (up to 100): one result per input, in input order, each with found true/false and the company record. |
| `find_company_by_domain` | Find the Romanian firm behind a website domain (dedeman.ro). |
| `match_companies` | Match a list of up to 500 records (name, optional county, domain or CUI) to Romanian firms: each row gets match {cui, companyName, confidence, matchedOn} and the record, or no_match with up to 3 candidates. |
| `lookup_by_category` | Find companies by what they do, not by name: a curated topic ('software', 'retail'), plain words ('dentists', a CAEN class title; see matched) or CAEN codes ('4321,4322'; 6201 also finds Rev.3 6210). |
| `lookup_director` | Find Romanian companies by the name of a director or legal representative (ONRC). |
| `extract_contacts` | Crawl a company website and extract emails, phone numbers and social profiles. |
| `enrich_company` | A whole-firm profile in one call: the ONRC registry record plus the contacts discovered on that firm's website, each labelled with a confidence and a source. |
| `lookup_domain` | WHOIS plus DNS records (A, MX, NS, TXT) and an SPF/DMARC email-security audit for a domain. |
| `lookup_financials` | Annual financial statements for a Romanian company from Ministry of Finance filings, every fiscal year on record since 2008: revenue, profit, headcount and balance-sheet lines in RON with each year's ratios (growth, margins, equity ratio, liquidity), plus a summary (3/5/10-year CAGR, loss years, health score). |
| `lookup_financial_ratios` | Derived financial intelligence for a Romanian company: multi-year revenue trend (YoY, 3/5/10-year CAGR), margins, headcount, solvency and liquidity ratios, a 0-100 health score with its rule-by-rule breakdown, red flags, and peer percentiles within the same CAEN activity and county. |
| `watch_company` | Register a company (by CUI) to monitor. |
| `watch_caen` | Watch a CAEN activity code, optionally scoped to one county, for newly registered firms. |
| `poll_watchlist` | Collect everything the watch tools have found: call it to drain the changeset for every watch owned by your API key, most severe first. |
| `get_usage` | Show your own allowance: plan, daily limit, lookups used today, how many remain, when it resets (00:00 UTC) and any credit balance. |
| `report_issue` | Tell the Leadgen founder what is missing or wrong: a missing field, firm or source (kind 'gap'), a wrong company record ('wrong_record', pass its cui), a bug, an idea. |


Every company-bearing answer carries `asOf` (registry snapshot, financial year, ANAF load date) — cite it.

## Data and credit

Registry and financial statements: ONRC and the Ministry of Finance via data.gov.ro, licensed CC BY 4.0;
fiscal data: ANAF. When you publish data obtained through Leadgen, credit it as:
"Sursa: ONRC și Ministerul Finanțelor (data.gov.ro, CC BY 4.0) și ANAF, prin Leadgen".

## Romanian

Server MCP pentru registrul oficial al firmelor din România (ONRC, ANAF, bilanțuri 2008–2025).
Gratuit: 5 interogări pe zi fără cheie, 25 de interogări pe zi cu o cheie gratuită de la
<https://leadgen.adrianhomelab.com/start>. Documentație: <https://leadgen.adrianhomelab.com/docs>.

## Contact

Prices: <https://leadgen-checkout.adrianhomelab.com/> · questions: through the website's contact link.
