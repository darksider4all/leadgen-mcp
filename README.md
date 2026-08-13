# Leadgen MCP

Lead-generation & enrichment tools for AI agents — a self-hosted MCP server over
Streamable HTTP, publicly reachable at `https://hermes.adrianhomelab.com/mcp`
(no auth — validation phase).

## Tools

| Tool | What it does | Data source |
|---|---|---|
| `lookup_business(query)` | Romanian business registry search + detail enrichment | official ONRC data (data.gov.ro CKAN), local SQLite + FTS5 |
| `lookup_director(name)` | Find companies by director / legal-representative name | ONRC reprezentanti table (FTS5, diacritic-insensitive) |
| `extract_contacts(website)` | Crawl a site for emails, phones, socials | live crawl |
| `lookup_domain(domain)` | WHOIS + DNS + SPF/DMARC security audit | live WHOIS/DNS |

## Connect

Streamable HTTP — point any MCP client at:

```
https://hermes.adrianhomelab.com/mcp
```

## Run locally

```bash
uv venv venv
uv pip install --python venv/bin/python -r requirements.txt
venv/bin/python server.py --transport streamable-http --host 0.0.0.0 --port 8766
```

Or via the mcp CLI:

```bash
venv/bin/mcp run server.py --transport streamable-http --port 8766
```

## Data

The ONRC database is built from public data.gov.ro CKAN snapshots:

```bash
python3 load_onrc.py          # download + load the monthly snapshot
python3 load_nomenclatoare.py # decode tables
```

The default DB path is `/opt/leadgen-mcp/data/onrc.db`.

## Deploy (homelab)

See the `homelab-mcp-deploy` skill: uv venv on the target LXC, systemd unit,
SSH tunnel, Hermes MCP HTTP config.

## Validation status

Free tier, no billing yet. Validate the wedge (do agents call it?) before
wiring metering.
