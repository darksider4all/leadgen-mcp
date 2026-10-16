# Leadgen MCP — Romanian company data (ONRC, ANAF, financial statements)

A remote [Model Context Protocol](https://modelcontextprotocol.io) server for Romania's official business
registry: **4.2M firms** from ONRC, the ANAF fiscal layer (VAT, e-Factura, fiscal inactivity, phone)
and **18 years of financial statements** (Ministry of Finance, 2008–2025) with ratios and peer benchmarks.

- **Endpoint:** `https://leadgen-mcp.adrianhomelab.com/mcp` (Streamable HTTP)
- **Free:** 5 lookups a day without a key, **25 a day with a free key** from
  <https://leadgen.adrianhomelab.com/start> — send it as `Authorization: Bearer <key>` (or `X-API-Key`).
- **REST API and docs:** <https://leadgen.adrianhomelab.com/docs> · **for LLMs:** <https://leadgen.adrianhomelab.com/llms.txt>
- **Server card:** <https://leadgen-mcp.adrianhomelab.com/.well-known/mcp/server-card.json> ·
  official MCP registry: `com.adrianhomelab/leadgen-onrc`

This repository holds the public description of the server. The server's source is not public.

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

## Tools

| Tool | What it does |
|---|---|
| `lookup_business` | Look up a Romanian company by name or CUI (tax code) in the official ONRC registry of 4.2M firms. |
| `lookup_by_category` | Find companies by what they do, not by name: a curated topic ('software', 'cybersecurity', 'artificial intelligence', 'retail') expands to the CAEN codes that define it, or pass a raw CAEN code or list ('4321', '4321,4322'); a raw code also finds its Rev.2/Rev.3 counterpart (6201 and 6210), said in warnings. |
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
