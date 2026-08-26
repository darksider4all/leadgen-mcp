# Leadgen — Romanian ONRC Business Registry (API + MCP)

Free, read-only access to Romania's official business registry (**ONRC**):
every registered company, its CAEN activities, legal representatives, status,
and 2025 financial statements — built from the state's own open-data dump and
exposed over a tiny HTTP API **and** an MCP server.

- **4,196,860** companies
- **19.3M** CAEN activity records
- **3.68M** legal representatives
- **4.64M** status entries
- **2025 financial statements** loaded

## Live endpoints (free, no auth)

**REST API** — `https://onrc-api.adrianhomelab.com`

| Endpoint | Description |
|---|---|
| `GET /lookup_business?query=<name\|CUI>&max_results=N` | Search companies by name or tax code (CUI). |
| `GET /lookup_director?name=<name>&max_results=N` | Find companies by director / legal-representative name. |
| `GET /docs` | Interactive OpenAPI docs (Swagger UI). |
| `GET /health` | Liveness check. |

The landing page at `https://onrc-api.adrianhomelab.com/` documents the schema
with a worked example.

**MCP server** — `https://hermes.adrianhomelab.com/mcp` (Streamable HTTP). Point
any MCP client at that URL. Tools: `lookup_business`, `lookup_director`,
`lookup_financials`, `extract_contacts`, `lookup_domain`.

## Try it

```bash
# search by company name
curl "https://onrc-api.adrianhomelab.com/lookup_business?query=DEDEMAN&max_results=3"

# search by CUI (tax code)
curl "https://onrc-api.adrianhomelab.com/lookup_business?query=2816464"

# find companies by director name
curl "https://onrc-api.adrianhomelab.com/lookup_director?name=popescu"
```

```json
{
  "query": "DEDEMAN",
  "total": 2,
  "results": [
    {
      "companyName": "DEDEMAN SRL",
      "cui": "2816464",
      "registrationCode": "J1992002621040",
      "legalForm": "SRL",
      "county": "Bacău",
      "website": "www.dedeman.ro",
      "caenActivities": [ { "code": "0125", "activity": "…" } ],
      "directors": [ "PAVĂL I. DRAGOŞ", "PAVĂL ADRIAN" ],
      "status": [ { "code": "1048", "name": "funcțiune" } ]
    }
  ]
}
```

## Where the data comes from

The registry is built from the official ONRC snapshot published on
[data.gov.ro](https://data.gov.ro) — no scraping, no third-party vendor. The
loaders stream the CKAN CSVs into SQLite and build an **FTS5** full-text index
with diacritic-insensitive folding, so `popescu` matches `Popéscu` and `PAVĂL`
matches `paval`.

## Repository layout

| File | Purpose |
|---|---|
| `api.py` | FastAPI wrapper — the read-only public REST surface (+ landing page). |
| `server.py` | MCP server (Streamable HTTP) with all five tools. |
| `monetization.py` | Telemetry + a dormant freemium/rate-limit scaffold. |
| `load_onrc.py` | Download + load the ONRC CSV snapshot into SQLite. |
| `load_nomenclatoare.py` | Decode CAEN / status code tables. |
| `load_financiare.py` | Load the 2025 financial statements. |
| `server.json` | MCP Registry metadata (published as `io.github.darksider4all/leadgen-mcp`). |
| `smithery.yaml` | Smithery deployment config. |

## Run locally

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt

# load the data (downloads the official snapshots — several GB)
venv/bin/python load_onrc.py --data-dir ./data --db ./data/onrc.db

# start the REST API
ONRC_DB=./data/onrc.db venv/bin/python api.py

# start the MCP server
ONRC_DB=./data/onrc.db venv/bin/python server.py --transport streamable-http --port 8766
```

Then hit `http://127.0.0.1:8767/docs`.

## Notes on the data

- **Diacritic-insensitive search** — Romanian `ă â î ș ț` are folded to their
  ASCII forms on both the index and the query.
- **CUI vs registration code** — a numeric `query` is matched against the CUI
  (tax code) first, then the registration code.
- **Read-only** — the public surface only exposes lookups; there are no write
  paths.

## License

MIT — see [LICENSE](LICENSE).
