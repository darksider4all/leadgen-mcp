"""Leadgen MCP — lead-generation & enrichment tools for AI agents.

Exposes four work-doing tools an agent cannot perform itself:
  1. lookup_business — Romanian business registry search (official ONRC data)
  2. lookup_director — find companies by director / legal representative name
  3. extract_contacts — crawl a website for emails, phones, social profiles
  4. lookup_domain    — WHOIS + DNS + SPF/DMARC security audit

Self-contained (no external orchestration), Streamable HTTP transport.
Company data loaded locally from the official ONRC data.gov.ro snapshot.
Name searches run through a SQLite FTS5 index (diacritic-insensitive) built
by load_onrc.py; ensure_fts_index() at startup covers legacy DBs.
"""
from __future__ import annotations

import os
import re
import socket
import sqlite3
import unicodedata
from urllib.parse import unquote, urljoin, urlparse

import dns.resolver
import httpx
import whois
from bs4 import BeautifulSoup
from mcp.server.mcpserver import MCPServer

from monetization import MonetizationMiddleware

mcp = MCPServer("leadgen", middleware=[MonetizationMiddleware()])

# Tool-call analytics: every invocation is appended to a JSONL file so the
# weekly traffic monitor can count REAL tool usage (vs. liveness probes,
# which only ever hit initialize/tools-list).
TOOL_CALL_LOG = "/opt/leadgen-mcp/data/tool_calls.log"


def _log_tool_call(tool: str, **args) -> None:
    """Append a JSONL line recording a genuine tool invocation."""
    import json as _json
    import time as _time
    try:
        with open(TOOL_CALL_LOG, "a") as f:
            f.write(_json.dumps({
                "ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
                "tool": tool,
                "args": args,
            }, default=str) + "\n")
    except Exception:
        pass

# Romanian company data comes from the official ONRC open-data snapshot
# (data.gov.ro), loaded into a local SQLite DB by load_onrc.py.
ONRC_DB = os.environ.get("ONRC_DB", "/opt/leadgen-mcp/data/onrc.db")

# Shared browser UA for the website-contact crawler.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# --------------------------------------------------------------------------- #
# Diacritic-insensitive text folding                                          #
# --------------------------------------------------------------------------- #
# Romanian-specific letters, including legacy cedilla forms (ş/ţ) still found
# in older registry rows. Everything else is NFKD-decomposed and stripped of
# combining marks so any remaining accents also fold to plain ASCII.
_DIACRITIC_MAP = str.maketrans({
    "Ă": "A", "Â": "A", "Î": "I", "Ș": "S", "Ş": "S", "Ț": "T", "Ţ": "T",
    "ă": "a", "â": "a", "î": "i", "ș": "s", "ş": "s", "ț": "t", "ţ": "t",
})


def fold_diacritics(text: str) -> str:
    """Fold Romanian text to plain uppercase ASCII for matching.

    'PAVĂL' -> 'PAVAL', 'ȘTEFAN' -> 'STEFAN', 'ţ' -> 'T', etc.
    """
    if not text:
        return ""
    t = text.translate(_DIACRITIC_MAP)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return t.upper()


# --------------------------------------------------------------------------- #
# FTS5 search helpers                                                         #
# --------------------------------------------------------------------------- #
def _fts_query(text: str) -> str:
    """Turn free text into an FTS5 MATCH expression (AND of prefix tokens).

    Each whitespace-separated token becomes a quoted prefix term, so
    'paval sc' matches rows whose folded tokens start with PAVAL and SC.
    Quoting protects FTS5 special characters; embedded quotes are doubled.
    """
    folded = fold_diacritics(text)
    tokens = [t for t in re.split(r"\s+", folded) if t]
    if not tokens:
        return ""
    parts = []
    for tok in tokens:
        # FTS5 string-literal escaping: double embedded quotes, then prefix-match
        parts.append(f'"{tok.replace(chr(34), chr(34) * 2)}"*')
    return " ".join(parts)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','virtual table') AND name = ?",
        (name,),
    ).fetchone() is not None


def _search_firme_fts(conn: sqlite3.Connection, query: str, limit: int) -> list[str] | None:
    """Ranked FTS5 company-name search. Returns None if the index is missing."""
    if not _table_exists(conn, "firme_fts"):
        return None
    match = _fts_query(query)
    if not match:
        return []
    rows = conn.execute(
        "SELECT cod, rank FROM firme_fts WHERE firme_fts MATCH ? ORDER BY rank LIMIT ?",
        (match, limit),
    ).fetchall()
    return [r[0] for r in rows]


def _search_firme_like(conn: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    """Fallback substring scan (original behaviour, used only when FTS is absent)."""
    like = f"%{query}%"
    return conn.execute(
        "SELECT * FROM firme WHERE denumire LIKE ? COLLATE NOCASE LIMIT ?",
        (like, limit),
    ).fetchall()


def _firme_by_cods(conn: sqlite3.Connection, cods: list[str]) -> list[sqlite3.Row]:
    """Fetch full firme rows for codes, preserving the FTS ranking order."""
    if not cods:
        return []
    rows = []
    for i in range(0, len(cods), 500):
        chunk = cods[i:i + 500]
        ph = ",".join("?" * len(chunk))
        rows.extend(conn.execute(
            f"SELECT * FROM firme WHERE cod_inmatriculare IN ({ph})", chunk
        ).fetchall())
    order = {c: i for i, c in enumerate(cods)}
    rows.sort(key=lambda r: order.get(r["cod_inmatriculare"], 10 ** 9))
    return rows


def _company_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """Enrich one firme row into the standard company result dict."""
    cod = row["cod_inmatriculare"]
    caen_codes = [r[0] for r in conn.execute(
        "SELECT cod_caen FROM caen WHERE cod_inmatriculare = ? LIMIT 10", (cod,))]
    directors = [r[0] for r in conn.execute(
        "SELECT persoana_imputernicita FROM reprezentanti WHERE cod_inmatriculare = ? LIMIT 5", (cod,))]
    status_codes = [r[0] for r in conn.execute(
        "SELECT cod FROM stare WHERE cod_inmatriculare = ?", (cod,))]

    # Decode CAEN + status via nomenclatoare tables (graceful if absent)
    caen_activities = []
    for cc in caen_codes:
        name = conn.execute(
            "SELECT denumire FROM caen_nom WHERE clasa = ?", (cc,)).fetchone()
        caen_activities.append({"code": cc, "activity": name[0] if name else None})
    status_names = []
    for sc in status_codes:
        name = conn.execute(
            "SELECT denumire FROM stare_nom WHERE cod = ?", (sc,)).fetchone()
        status_names.append({"code": sc, "name": name[0] if name else None})

    addr = ", ".join(v for v in [
        row["adr_localitate"], row["adr_judet"], row["adr_den_strada"],
        row["adr_nr_strada"], row["adr_cod_postal"], row["adr_sector"],
    ] if v)

    return {
        "companyName": row["denumire"],
        "cui": row["cui"],
        "registrationCode": cod,
        "registrationDate": row["data_inmatriculare"],
        "legalForm": row["forma_juridica"],
        "euid": row["euid"],
        "address": addr,
        "county": row["adr_judet"],
        "city": row["adr_localitate"],
        "website": row["web"],
        "caenActivities": caen_activities or None,
        "directors": directors or None,
        "status": status_names or None,
        "source": "onrc",
    }


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(ONRC_DB)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------- #
# Tool 1: Romanian business registry lookup                                   #
# --------------------------------------------------------------------------- #
@mcp.tool()
def lookup_business(query: str, max_results: int = 10) -> dict:
    """Search the Romanian business registry (official ONRC data) for companies.

    Data source: official ONRC open-data snapshot (data.gov.ro), loaded locally.
    Name queries use a ranked FTS5 index and are diacritic-insensitive
    ('paval' matches 'PAVĂL'); digit queries match the CUI exactly, falling
    back to a CUI prefix match when the exact code is unknown.

    Args:
        query: Company name or CUI (tax ID) to search for.
        max_results: Maximum number of companies to return (1-100).
    """
    _log_tool_call("lookup_business", query=query, max_results=max_results)
    max_results = max(1, min(int(max_results), 100))
    q = query.strip()
    if not os.path.exists(ONRC_DB):
        return {"error": "ONRC database not loaded yet. Run load_onrc.py first."}

    conn = _open_db()
    try:
        if q.isdigit():
            rows = conn.execute(
                "SELECT * FROM firme WHERE cui = ? LIMIT ?", (q, max_results)
            ).fetchall()
            if not rows:
                # Partial CUI: prefix-search the FTS cui column
                cods = _search_firme_fts(conn, q, max_results)
                if cods:
                    rows = _firme_by_cods(conn, cods)
        else:
            cods = _search_firme_fts(conn, q, max_results)
            if cods is None:
                rows = _search_firme_like(conn, q, max_results)
            else:
                rows = _firme_by_cods(conn, cods)

        results = [_company_dict(conn, row) for row in rows]
        return {"query": query, "total": len(results), "results": results, "source": "onrc"}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Tool 2: search by director / legal representative                           #
# --------------------------------------------------------------------------- #
@mcp.tool()
def lookup_director(name: str, max_results: int = 10) -> dict:
    """Find Romanian companies by director or legal representative name.

    Searches the ONRC reprezentanti table (persoana_imputernicita) via a
    ranked FTS5 index; diacritic-insensitive ('popescu' matches 'POPESCU').
    Returns companies with their director names and roles.

    Args:
        name: Director / representative name to search for (e.g. "Ion Popescu").
        max_results: Maximum number of companies to return (1-100).
    """
    _log_tool_call("lookup_director", name=name, max_results=max_results)
    max_results = max(1, min(int(max_results), 100))
    q = name.strip()
    if not os.path.exists(ONRC_DB):
        return {"error": "ONRC database not loaded yet. Run load_onrc.py first."}

    conn = _open_db()
    try:
        if _table_exists(conn, "reprez_fts"):
            match = _fts_query(q)
            if not match:
                return {"query": name, "total": 0, "results": []}
            cods = [r[0] for r in conn.execute(
                "SELECT cod, rank FROM reprez_fts WHERE reprez_fts MATCH ? "
                "ORDER BY rank LIMIT ?", (match, max_results * 2)).fetchall()]
        else:
            like = f"%{q}%"
            cods = [r[0] for r in conn.execute(
                "SELECT DISTINCT cod_inmatriculare FROM reprezentanti "
                "WHERE persoana_imputernicita LIKE ? COLLATE NOCASE LIMIT ?",
                (like, max_results * 2)).fetchall()]

        results = []
        for cod in cods[:max_results]:
            row = conn.execute(
                "SELECT * FROM firme WHERE cod_inmatriculare = ?", (cod,)).fetchone()
            if row is None:
                continue
            item = _company_dict(conn, row)
            roles = [{"name": r[0], "role": r[1]} for r in conn.execute(
                "SELECT persoana_imputernicita, calitate FROM reprezentanti "
                "WHERE cod_inmatriculare = ? LIMIT 10", (cod,))]
            item["directorRoles"] = roles or None
            results.append(item)
        return {"query": name, "total": len(results), "results": results, "source": "onrc"}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Tool 3: Website contact extraction                                           #
# --------------------------------------------------------------------------- #
CONTACT_HINTS = (
    "contact", "kontakt", "contacto", "contatti", "about", "despre",
    "team", "impressum", "imprint", "support", "help", "legal",
    "privacy", "company",
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
OBFUSCATED_EMAIL_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*[\[({]\s*at\s*[\])}]\s*([A-Za-z0-9.\-]+)"
    r"\s*[\[({]\s*dot\s*[\])}]\s*([A-Za-z]{2,})",
    re.IGNORECASE,
)
CF_EMAIL_RE = re.compile(r'data-cfemail="([0-9a-f]+)"', re.IGNORECASE)
EMAIL_JUNK = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js",
    "example.com", "yourdomain", "email.com", "domain.com", "sentry",
    "wixpress.com", "@2x", "@3x", "no-reply", "noreply",
)
SOCIAL_PATTERNS = {
    "facebook": r"https?://(?:www\.)?facebook\.com/[A-Za-z0-9_.\-/]+",
    "twitter": r"https?://(?:www\.)?(?:twitter|x)\.com/[A-Za-z0-9_]+",
    "instagram": r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.]+",
    "linkedin": r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|school)/[A-Za-z0-9_\-%.]+",
    "youtube": r"https?://(?:www\.)?youtube\.com/(?:@|channel/|c/|user/)[A-Za-z0-9_\-]+",
    "github": r"https?://(?:www\.)?github\.com/[A-Za-z0-9\-]+",
}
# Vendor/utility accounts that are never the target company's own social profile
# (tracking, CDN, SaaS badges often link to these on customer sites).
SOCIAL_VENDOR_DENY = (
    "newrelic", "sentry", "github.com/github", "github.com/features",
    "github.com/about", "github.com/enterprise", "github.com/marketplace",
    "github.com/topics", "github.com/collections", "cloudflare", "vercel",
    "netlify", "stripe", "auth0", "aws", "google", "shopify",
)


def _registrable_host(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def _email_matches_domain(email: str, host: str) -> bool:
    """True if the email's domain is the target domain or a subdomain of it."""
    dom = email.split("@")[-1].lower()
    return dom == host or dom.endswith("." + host)


def _clean_email(email: str) -> str | None:
    low = email.lower().strip().rstrip(".")
    if any(j in low for j in EMAIL_JUNK) or len(low) > 60:
        return None
    return low


def _decode_cf_email(hex_str: str) -> str | None:
    try:
        data = bytes.fromhex(hex_str)
        key = data[0]
        return bytes(b ^ key for b in data[1:]).decode("utf-8")
    except Exception:
        return None


def _extract_emails(html: str, soup: BeautifulSoup) -> set:
    found = set()
    for a in soup.select('a[href^="mailto:"]'):
        addr = unquote(a["href"][7:]).split("?")[0].strip()
        if EMAIL_RE.fullmatch(addr):
            e = _clean_email(addr)
            if e:
                found.add(e)
    for email in EMAIL_RE.findall(html):
        e = _clean_email(email)
        if e:
            found.add(e)
    for m in OBFUSCATED_EMAIL_RE.findall(html):
        e = _clean_email(f"{m[0]}@{m[1]}.{m[2]}")
        if e:
            found.add(e)
    for hex_str in CF_EMAIL_RE.findall(html):
        decoded = _decode_cf_email(hex_str)
        if decoded and EMAIL_RE.fullmatch(decoded):
            e = _clean_email(decoded)
            if e:
                found.add(e)
    return found


@mcp.tool()
def extract_contacts(website: str, max_pages: int = 5) -> dict:
    """Crawl a website and extract emails, phone numbers, and social profiles.

    Args:
        website: The website URL to crawl (e.g. "example.com" or "https://example.com").
        max_pages: Maximum number of pages to crawl (1-20).
    """
    _log_tool_call("extract_contacts", website=website, max_pages=max_pages)
    url = website.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    max_pages = max(1, min(int(max_pages), 20))
    host = _registrable_host(url)
    domain_token = host.split(".")[0]
    emails: set = set()
    phones: set = set()
    socials = {p: {} for p in SOCIAL_PATTERNS}
    queue = [url]
    visited: set = set()
    final_url = None
    error = None

    with httpx.Client(follow_redirects=True, timeout=30, headers={"User-Agent": USER_AGENT}) as client:
        while queue and len(visited) < max_pages:
            page_url = queue.pop(0)
            key = page_url.rstrip("/")
            if key in visited:
                continue
            visited.add(key)
            try:
                resp = client.get(page_url)
            except Exception as e:
                if not final_url:
                    error = str(e)
                continue
            if resp.status_code >= 400 or "html" not in resp.headers.get("content-type", "html"):
                continue
            if not final_url:
                final_url = str(resp.url)
                error = None
            html = resp.text or ""
            soup = BeautifulSoup(html, "html.parser")
            emails |= _extract_emails(html, soup)
            # phones — stricter: reject barcode/ID digit runs, strip punctuation
            text = soup.get_text(" ", strip=True)
            for m in re.findall(r"\+?[0-9][0-9\s().\-]{6,18}", text):
                candidate = m.strip().rstrip(".,;:!")
                digits = re.sub(r"\D", "", candidate)
                if not (8 <= len(digits) <= 11):
                    continue  # barcodes (EAN-13) and IDs are longer / reject
                if re.fullmatch(r"\d{4}\s*[-–—]\s*\d{4}", candidate):
                    continue  # year range like "2001-2026", not a phone
                has_structure = (" " in candidate or "-" in candidate or "." in candidate
                                 or "(" in candidate or candidate.startswith("+"))
                if candidate.startswith("+") or candidate.startswith("0") or has_structure:
                    phones.add(re.sub(r"\s+", " ", candidate))
            # socials — anchor tags only (not raw HTML), with vendor denylist
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if href.startswith("//"):
                    href = "https:" + href
                for platform, pattern in SOCIAL_PATTERNS.items():
                    m = re.match(pattern, href)
                    if not m:
                        continue
                    link = m.group(0).rstrip("/.\"'")
                    if (any(v in link.lower() for v in SOCIAL_VENDOR_DENY)
                            or "/sharer" in link.lower() or "/share?" in link.lower()):
                        continue
                    socials[platform][link] = socials[platform].get(link, 0) + 1
            # enqueue internal links
            for a in soup.find_all("a", href=True):
                href = urljoin(page_url, a["href"].strip())
                parsed = urlparse(href)
                if parsed.scheme not in ("http", "https"):
                    continue
                if _registrable_host(href) != host:
                    continue
                href = href.split("#")[0]
                if href.rstrip("/") not in visited and href not in queue:
                    queue.append(href)

    ranked = {p: sorted(counts, key=lambda l: (domain_token not in l.lower(), -counts[l], l))
              for p, counts in socials.items()}
    on_domain = sorted(e for e in emails if _email_matches_domain(e, host))
    other_emails = sorted(e for e in emails if not _email_matches_domain(e, host))
    return {
        "url": url,
        "domain": host,
        "pagesCrawled": len(visited),
        "emails": on_domain[:50] or None,
        "otherEmails": other_emails[:50] or None,
        "phones": sorted(phones)[:25] or None,
        "facebook": (ranked["facebook"] or [None])[0],
        "twitter": (ranked["twitter"] or [None])[0],
        "instagram": (ranked["instagram"] or [None])[0],
        "linkedin": (ranked["linkedin"] or [None])[0],
        "youtube": (ranked["youtube"] or [None])[0],
        "github": (ranked["github"] or [None])[0],
        "socialLinks": [l for p in SOCIAL_PATTERNS for l in ranked[p][:5]][:25] or None,
        "error": error,
    }


# --------------------------------------------------------------------------- #
# Tool 4: WHOIS + DNS lookup                                                   #
# --------------------------------------------------------------------------- #
@mcp.tool()
def lookup_domain(domain: str, include_dns: bool = True, include_security: bool = True) -> dict:
    """Perform WHOIS + DNS + SPF/DMARC lookup for a domain.

    Args:
        domain: Domain name to look up (e.g. "example.com").
        include_dns: Include DNS records (A, MX, NS, TXT, etc.).
        include_security: Include SPF/DMARC email-security check.
    """
    _log_tool_call("lookup_domain", domain=domain)
    domain = domain.strip().lower()
    if domain.startswith("http"):
        domain = urlparse(domain).netloc
    domain = domain.split(":")[0].split("/")[0]

    result: dict = {"domain": domain}

    # WHOIS
    try:
        w = whois.whois(domain)
        result["whois"] = {
            "registrar": w.registrar,
            "creationDate": _date_str(w.creation_date),
            "expirationDate": _date_str(w.expiration_date),
            "updatedDate": _date_str(w.updated_date),
            "nameServers": [ns.lower() for ns in w.name_servers] if w.name_servers else None,
            "registrantOrg": w.org,
            "registrantCountry": w.country,
        }
    except Exception as e:
        result["whois"] = {"error": str(e)}

    # DNS
    if include_dns:
        records = {}
        for rt in ["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA", "CAA"]:
            vals = _dns(domain, rt)
            if vals:
                records[rt] = vals
        result["dns"] = records

    # Security (SPF/DMARC)
    if include_security:
        spf = None
        dmarc = None
        for txt in _dns(domain, "TXT"):
            if "v=spf1" in txt.lower():
                spf = txt
        for txt in _dns(f"_dmarc.{domain}", "TXT"):
            if "v=DMARC1" in txt:
                dmarc = txt
        result["security"] = {"hasSPF": bool(spf), "spf": spf, "hasDMARC": bool(dmarc), "dmarc": dmarc}

    # Resolved IP
    try:
        result["resolvedIP"] = socket.gethostbyname(domain)
    except Exception:
        result["resolvedIP"] = None

    return result


def _date_str(d):
    if isinstance(d, list):
        return str(d[0]) if d else None
    return str(d) if d else None


def _dns(domain: str, record_type: str) -> list:
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = 10
        resolver.lifetime = 15
        return [str(r) for r in resolver.resolve(domain, record_type)]
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Tool 5: Romanian financial statements (MFP situatii_financiare)            #
# --------------------------------------------------------------------------- #
def _ron(value: int | None) -> str | None:
    """Format a RON integer with Romanian-style thousands separators."""
    if value is None:
        return None
    return f"{value:,}".replace(",", ".") + " RON"


def _fin_year_dict(row: sqlite3.Row) -> dict:
    """One financiare row → a clean, human-readable annual result dict."""
    revenue = row["cifra_de_afaceri_neta"]
    profit_net = row["profit_net"]
    loss_net = row["pierdere_neta"]
    total_assets = None
    if row["active_imobilizate"] is not None or row["active_circulante"] is not None:
        total_assets = (row["active_imobilizate"] or 0) + (row["active_circulante"] or 0)
        if row["cheltuieli_in_avans"]:
            total_assets += row["cheltuieli_in_avans"]
    d = {
        "year": row["an"],
        "source": row["sursa"],
        "caen": row["caen"],
        "revenue": revenue,
        "revenueFormatted": _ron(revenue),
        "netProfit": profit_net,
        "netLoss": loss_net,
        "netResult": (profit_net or 0) - (loss_net or 0)
        if profit_net is not None or loss_net is not None else None,
        "employees": row["numar_mediu_salariati"],
        "totalAssets": total_assets,
        "totalAssetsFormatted": _ron(total_assets),
        "totalDebt": row["datorii"],
        "totalDebtFormatted": _ron(row["datorii"]),
        "equity": row["capitaluri_totale"],
        "equityFormatted": _ron(row["capitaluri_totale"]),
        "indicators": {
            "fixedAssets": row["active_imobilizate"],
            "currentAssets": row["active_circulante"],
            "inventories": row["stocuri"],
            "receivables": row["creante"],
            "cash": row["casa_si_conturi_banci"],
            "prepaidExpenses": row["cheltuieli_in_avans"],
            "deferredIncome": row["venituri_in_avans"],
            "provisions": row["provizioane"],
            "subscribedCapital": row["capital_subscris_varsat"],
            "totalRevenue": row["venituri_totale"],
            "totalExpenses": row["cheltuieli_totale"],
            "grossProfit": row["profit_brut"],
            "grossLoss": row["pierdere_bruta"],
        },
    }
    return d


@mcp.tool()
def lookup_financials(cui: str) -> dict:
    """Return a Romanian company's annual financial statements (MFP data).

    Data source: official Ministry of Finance 'situatii financiare' open data
    (data.gov.ro), loaded locally by load_financiare.py. Values are RON
    integers from the company's latest annual filing(s). Free tier returns
    registry identity; this is the paid-tier financial enrichment.

    Args:
        cui: Romanian tax identification number (CUI), e.g. "2816464".
    """
    _log_tool_call("lookup_financials", cui=cui)
    cui = (cui or "").strip()
    if not cui:
        return {"error": "cui is required."}
    if not os.path.exists(ONRC_DB):
        return {"error": "ONRC database not loaded yet. Run load_onrc.py first."}

    conn = _open_db()
    try:
        if not _table_exists(conn, "financiare"):
            return {"cui": cui, "found": False, "total": 0, "years": [],
                    "message": "Financial data not loaded yet. Run load_financiare.py first.",
                    "source": "mfp"}
        rows = conn.execute(
            "SELECT * FROM financiare WHERE cui = ? ORDER BY an DESC, sursa",
            (cui,)).fetchall()
        if not rows:
            return {"cui": cui, "found": False, "total": 0, "years": [],
                    "source": "mfp"}
        years = [_fin_year_dict(r) for r in rows]
        return {"cui": cui, "found": True, "total": len(years),
                "years": years, "source": "mfp"}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# FTS index bootstrap (legacy DBs loaded before FTS existed)                  #
# --------------------------------------------------------------------------- #
def build_fts_tables(conn: sqlite3.Connection) -> None:
    """Create + populate the firme_fts and reprez_fts FTS5 indexes."""
    conn.create_function("fold", 1, fold_diacritics, deterministic=True)
    conn.execute("DROP TABLE IF EXISTS firme_fts")
    conn.execute("DROP TABLE IF EXISTS reprez_fts")
    conn.execute(
        "CREATE VIRTUAL TABLE firme_fts USING fts5("
        "denumire_norm, cui, cod UNINDEXED)")
    conn.execute(
        "CREATE VIRTUAL TABLE reprez_fts USING fts5("
        "persoana_norm, calitate, cod UNINDEXED)")
    conn.execute(
        "INSERT INTO firme_fts(denumire_norm, cui, cod) "
        "SELECT fold(denumire), cui, cod_inmatriculare FROM firme")
    conn.execute(
        "INSERT INTO reprez_fts(persoana_norm, calitate, cod) "
        "SELECT fold(persoana_imputernicita), calitate, cod_inmatriculare "
        "FROM reprezentanti")
    conn.commit()


def ensure_fts_index() -> None:
    """Build FTS indexes at startup if missing (one-time upgrade path).

    Fresh snapshots loaded by load_onrc.py already include the indexes, so
    this is normally a fast no-op. Only an old DB (loaded before FTS existed)
    triggers an actual build here — a few minutes, once.
    """
    if not os.path.exists(ONRC_DB):
        return
    conn = sqlite3.connect(ONRC_DB)
    try:
        if not _table_exists(conn, "firme") or _table_exists(conn, "firme_fts"):
            return
        import time
        t0 = time.time()
        build_fts_tables(conn)
        print(f"[leadgen] built FTS5 indexes on legacy DB in {time.time() - t0:.1f}s", flush=True)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Entrypoint                                                                   #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    ensure_fts_index()
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8766)
