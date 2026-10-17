# Leadgen MCP tools: parameters

`*` = required. Every company-bearing tool also takes `fields` (comma-separated field names or
dotted paths such as `risk.level`, to trim each record) and `compact: true` (a short preset: name,
CUI, county, city, legal form, risk level, phone, website, latest revenue and headcount).

| Tool | Parameters | Notes |
|---|---|---|
| `lookup_business` | `query`*, `county`, `max_results` (1–100, default 10) | Name or CUI. Live firms rank first; an exact name beats a partial one; closed firms follow with their status. |
| `lookup_companies` | `cuis`* (list or comma-separated, ≤100) | One result per input, in order, `found` true/false. Counts one lookup per CUI. |
| `match_companies` | `rows`* (≤500 of `{name, county?, domain?, cui?}`) | `match {cui, companyName, confidence, matchedOn}` or `no_match` with up to 3 `candidates`; flags `ambiguous`, `nameDiffers`, `countyMismatch`. One lookup per row. |
| `find_company_by_domain` | `domain`* | The firm behind a site (registry website field, then the site's own CUI). |
| `lookup_by_category` | `topic`*, `county`, `max_results` (1–100), `legal_form` (`"SRL,SA"`), `active_only` (default true), `has_website`, `min_revenue`/`max_revenue` (RON, latest filing), `min_employees`/`max_employees`, `vat_payer`, `main_activity_only` | `topic`: a curated topic, plain words, a CAEN class title, or codes (`"4321,4322"`). Rev.2↔Rev.3 codes are crossed automatically; `4781r3` / `4781r2` pins one revision's meaning. Newest registrations first. |
| `lookup_director` | `name`*, `max_results`, `prefix` | People names are not unique: group by county and say so. |
| `extract_contacts` | `website`*, `max_pages` | Emails, phones and social profiles found on the site. |
| `enrich_company` | `cui`*, `website`, `max_pages`, `refresh` | Registry record plus site contacts, each with a confidence and source. |
| `lookup_domain` | `domain`*, `include_dns`, `include_security` | WHOIS, DNS (A, MX, NS, TXT), SPF/DMARC audit. |
| `lookup_financials` | `cui`* | Every fiscal year since 2008: revenue, profit, headcount, balance sheet (RON), yearly ratios, a summary with CAGR and a health score. |
| `lookup_financial_ratios` | `cui`* | Trend (YoY, 3/5/10-year CAGR), margins, solvency, liquidity, a 0–100 health score with its rules, red flags, peer percentiles in the same CAEN and county. |
| `watch_company` | `cui`* | Needs a key. |
| `watch_caen` | `code`*, `county` | New registrations in a code (and county). Needs a key. |
| `poll_watchlist` | — | Drains every change for the caller's watches, most severe first. |
| `get_usage` | — | Plan, daily limit, used today, left, reset time. Free. |
| `report_issue` | `message`*, `kind` (`gap`, `wrong_record`, `bug`, `idea`), `cui`, `contact` | Free; reaches the founder. |

## Refusals

| Situation | MCP | REST |
|---|---|---|
| Daily limit reached | JSON-RPC error -32002 `daily_limit_reached` | 429 |
| Key suspended | -32004 `key_suspended` | 403 |
| Invalid key | HTTP 401 `invalid_api_key` | 401 |
| Feature needs a paid plan | tool result `error: plan_required` with `upgrade` | 402 |

Every refusal body carries `get_a_key`. Answers with an `error` field (bad input, `plan_required`,
`ambiguous_topic`) are not counted against the day.
