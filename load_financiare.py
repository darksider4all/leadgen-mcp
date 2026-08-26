#!/usr/bin/env python3
"""Download and load Romanian Ministry of Finance financial statements into SQLite.

Source: data.gov.ro CKAN (MFP organisation), annual 'situatii_financiare_*'
datasets. Two complementary files per year (identical column layout, disjoint
company populations — verified zero CUI overlap in 2025):

  WEB_UU_AN{year}.txt        ~80MB — main company financial indicators
  WEB_BL_BS_SL_AN{year}.txt  ~9MB  — balance sheet / larger filers

Both files share one header: CUI,CAEN,I1..I20. The I* meaning is published in
the sibling WEB_UU_AN{year}.csv / WEB_BL_BS_SL_AN{year}.csv spec files (one
'label;I#' pair per line). This loader derives the DB column names from that
spec at runtime, so upstream renames never silently break the mapping.

Design:
  - Adds a new 'financiare' table to the EXISTING onrc.db (does NOT rebuild
    the 3.4 GB registry DB — the live firme/FTS tables stay untouched).
  - Keyed (cui, an, sursa) with INSERT OR REPLACE → idempotent per year.
  - One transaction per file; rollback on failure leaves prior data intact.
  - busy_timeout guards the write against the live server's short reads.
  - flock (non-blocking) prevents concurrent refreshes.
  - Tracks snapshot metadata in the existing 'meta' table.

Usage:
  python3 load_financiare.py [--data-dir DIR] [--db PATH] [--force]
                             [--no-restart] [--year 2025]
"""
import argparse
import csv
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

CKAN_API = "https://data.gov.ro/api/3/action"
DATASET_RE = re.compile(r"situatii[_-]?financiare[_-]?(\d{4})")

# Spec (label;I#) files give the canonical column meaning. If a file appears in
# the dataset but is absent locally, we fall back to this verified 2025 map.
FALLBACK_COLUMNS = {
    "i1": "active_imobilizate",
    "i2": "active_circulante",
    "i3": "stocuri",
    "i4": "creante",
    "i5": "casa_si_conturi_banci",
    "i6": "cheltuieli_in_avans",
    "i7": "datorii",
    "i8": "venituri_in_avans",
    "i9": "provizioane",
    "i10": "capitaluri_totale",
    "i11": "capital_subscris_varsat",
    "i12": "patrimoniul_regiei",
    "i13": "cifra_de_afaceri_neta",
    "i14": "venituri_totale",
    "i15": "cheltuieli_totale",
    "i16": "profit_brut",
    "i17": "pierdere_bruta",
    "i18": "profit_net",
    "i19": "pierdere_neta",
    "i20": "numar_mediu_salariati",
}

# The two data files worth loading (name prefix → 'sursa' tag)
DATA_FILES = {
    "WEB_UU_AN": "UU",
    "WEB_BL_BS_SL_AN": "BL_BS_SL",
}

# Sanity floors per source — a truncated download below these is not trusted.
MIN_ROWS = {"UU": 500_000, "BL_BS_SL": 50_000}


def ckan_json(url, timeout=120):
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout), url],
                       capture_output=True, text=True, timeout=timeout + 10)
    return json.loads(r.stdout)


def latest_dataset(prefer_year=None):
    """Find the most recent 'situatii_financiare_*' dataset by fiscal year.

    Searches MFP datasets, parses the year from the package name and returns
    the package with the HIGHEST year (2025 beats the June-2026 re-publish of
    the 2024 data). Among same-year candidates (e.g. ..._2024 vs
    ..._2024_actualizat) the most recently modified wins.
    """
    result = ckan_json(f"{CKAN_API}/package_search?q=organization:mfp&rows=100")
    candidates = []
    for d in result["result"]["results"]:
        m = DATASET_RE.search(d["name"])
        if m:
            candidates.append((int(m.group(1)), d))
    if not candidates:
        raise RuntimeError("No situatii_financiare dataset found on CKAN")
    best_year = max(c for c, _ in candidates)
    if prefer_year and prefer_year in {c for c, _ in candidates}:
        best_year = prefer_year
    same_year = [d for c, d in candidates if c == best_year]
    same_year.sort(key=lambda d: d.get("metadata_modified", ""), reverse=True)
    latest = same_year[0]
    pkg = ckan_json(f"{CKAN_API}/package_show?id={latest['name']}")
    resources = {}
    for r in pkg["result"]["resources"]:
        base = r["name"].replace(".txt", "").replace(".csv", "").strip()
        resources.setdefault(base, []).append({"url": r["url"], "size": r.get("size")})
    return latest["name"], best_year, latest.get("metadata_modified", ""), resources


def download(url, dest, timeout=3600):
    """curl download, resumable, returns True on success."""
    r = subprocess.run(
        ["curl", "-s", "-L", "--max-time", str(timeout), "-C", "-", "-o", dest, url],
        capture_output=True, text=True, timeout=timeout + 10,
    )
    return os.path.exists(dest) and os.path.getsize(dest) > 0


def acquire_lock(lock_path):
    lock = open(lock_path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    lock.write(f"{os.getpid()}\n")
    lock.flush()
    return lock


def parse_spec(path):
    """Parse a 'label;I#' spec CSV into {iN: snake_case_column}."""
    mapping = {}
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        for line in f:
            line = line.strip()
            if not line or ";" not in line:
                continue
            label, iname = line.rsplit(";", 1)
            iname = iname.strip().lower()
            if not re.fullmatch(r"i\d+", iname):
                continue
            col = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
            if col:
                mapping[iname] = col
    return mapping or None


def build_columns(spec_paths):
    """Canonical indicator columns (verified against the published spec).

    The 2025 spec files are the source of truth for the I# labels; this loader
    ships a verified canonical name per indicator (FALLBACK_COLUMNS) so the
    schema and server.py stay stable. Spec files are still parsed to catch any
    NEW indicator beyond I1..I20 in future years — unknown I# get a
    spec-derived snake_case name appended.
    """
    merged = dict(FALLBACK_COLUMNS)
    for p in spec_paths:
        spec = parse_spec(p)
        if not spec:
            continue
        for iname, col in spec.items():
            if iname not in merged:
                print(f"      + new indicator {iname} ({col}) from {p}")
                merged[iname] = col
    return merged


def create_table(conn, columns):
    """Create the financiare table if missing (columns from the spec)."""
    col_sql = ",\n    ".join(f"{name} INTEGER" for name in columns.values())
    conn.execute(f"""
    CREATE TABLE IF NOT EXISTS financiare (
        cui TEXT NOT NULL,
        an INTEGER NOT NULL,
        caen TEXT,
        sursa TEXT NOT NULL,
        {col_sql},
        PRIMARY KEY (cui, an, sursa)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_financiare_cui ON financiare(cui)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_financiare_an ON financiare(an)")
    conn.commit()


def load_file(conn, path, sursa, an, columns):
    """Stream a comma-delimited data file into the financiare table.

    Header: CUI,CAEN,I1..I20. Numeric cells are parsed to int or NULL.
    Returns the number of rows inserted.
    """
    expected = ["CUI", "CAEN"] + [f"I{i}" for i in range(1, 21)]
    inserted = 0
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter=",")
        header = next(reader)
        header = [h.lstrip("\ufeff").strip().upper() for h in header]
        if header[:2] != ["CUI", "CAEN"] or not all(h.startswith("I") for h in header[2:]):
            raise ValueError(f"Unexpected header in {path}: {header[:6]}...")
        idx_cui = header.index("CUI")
        idx_caen = header.index("CAEN")
        # Header cells are 'I1'..'I20'; the column map keys are lowercase 'i1'..'i20'
        idx_indicators = {header[i].lower(): i for i in range(2, len(header))}

        sql = f"INSERT OR REPLACE INTO financiare ({', '.join(['cui', 'an', 'caen', 'sursa'] + list(columns.values()))}) VALUES ({', '.join(['?'] * (4 + len(columns)))})"
        batch = []
        for row in reader:
            if len(row) < 2:
                continue
            cui = row[idx_cui].strip()
            if not cui:
                continue
            vals = [cui, an, row[idx_caen].strip() or None, sursa]
            for iname in columns:
                col = iname  # 'i1'..'i20', matches idx_indicators keys
                cell = row[idx_indicators[col]].strip() if col in idx_indicators and idx_indicators[col] < len(row) else ""
                vals.append(_to_int(cell))
            batch.append(vals)
            if len(batch) >= 5000:
                conn.executemany(sql, batch)
                inserted += len(batch)
                batch = []
        if batch:
            conn.executemany(sql, batch)
            inserted += len(batch)
    return inserted


def _to_int(cell):
    """Parse a RON integer cell; '' or junk → None. Handles spaces/thousands dots."""
    if cell is None:
        return None
    cell = cell.strip().replace(" ", "").replace(".", "").replace(",", "")
    if not cell:
        return None
    try:
        return int(cell)
    except ValueError:
        return None


def read_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def write_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(value)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/opt/leadgen-mcp/data")
    ap.add_argument("--db", default="/opt/leadgen-mcp/data/onrc.db")
    ap.add_argument("--force", action="store_true",
                    help="Reload even if the live DB already holds this snapshot/year")
    ap.add_argument("--no-restart", action="store_true",
                    help="Do not restart the leadgen-mcp service after loading")
    ap.add_argument("--year", type=int, default=None,
                    help="Pin a fiscal year (default: highest available on CKAN)")
    args = ap.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)
    lock = acquire_lock(os.path.join(args.data_dir, ".financiare.lock"))
    if lock is None:
        print("Another financiare refresh is running; exiting.")
        return

    try:
        print("[1/5] Finding latest situatii_financiare dataset...")
        ds_name, year, modified, resources = latest_dataset(prefer_year=args.year)
        print(f"      Latest: {ds_name} (year {year}, modified {modified})")

        # Resolve download URLs for the two data files + their spec files
        urls = {}
        for prefix, sursa in DATA_FILES.items():
            key = f"{prefix}{year}"
            if key not in resources:
                print(f"      ! {key} not found in resources, skipping")
                continue
            urls[sursa] = resources[key][0]["url"]
        if not urls:
            print("No data files found; nothing to do.")
            return
        spec_keys = [f"WEB_UU_AN{year}", f"WEB_BL_BS_SL_AN{year}"]
        spec_urls = [resources[k][0]["url"] for k in spec_keys if k in resources]

        # Idempotency: already on this snapshot+year?
        if os.path.exists(args.db) and not args.force:
            conn = sqlite3.connect(args.db)
            try:
                prev = read_meta(conn, "financiare_snapshot")
                has_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='financiare'"
                ).fetchone() is not None
                n = conn.execute(
                    "SELECT COUNT(*) FROM financiare WHERE an = ?", (year,)
                ).fetchone()[0] if has_table else 0
            finally:
                conn.close()
            if prev == ds_name and n > 0:
                print(f"      DB already on {ds_name} ({n:,} rows for {year}). Nothing to do.")
                return
            print(f"      DB on '{prev or 'unknown'}' ({n:,} rows for {year}) — loading {ds_name}")

        # Download (cached; only missing/empty files are fetched)
        print("[2/5] Downloading data files...")
        local = {}
        for sursa, url in urls.items():
            fname = f"WEB_{sursa}_AN{year}.txt"
            dest = os.path.join(args.data_dir, fname)
            if not os.path.exists(dest) or os.path.getsize(dest) < 1000:
                print(f"      downloading {fname}...")
                ok = download(url, dest)
                if not ok:
                    print(f"      ! download failed for {fname}")
                    continue
            local[sursa] = dest
            print(f"      {fname}: {os.path.getsize(dest) / 1e6:.1f} MB")

        spec_paths = []
        for k, u in zip(spec_keys, spec_urls):
            dest = os.path.join(args.data_dir, f"{k}.csv")
            if not os.path.exists(dest) or os.path.getsize(dest) < 10:
                download(u, dest, timeout=300)
            if os.path.exists(dest):
                spec_paths.append(dest)

        columns = build_columns(spec_paths)
        print(f"      columns resolved: {len(columns)} indicators "
              f"(spec files: {len(spec_paths)})")

        # Load into the EXISTING db (never touch the registry tables)
        print("[3/5] Loading into onrc.db...")
        conn = sqlite3.connect(args.db, timeout=60)
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            conn.executescript("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);")
            create_table(conn, columns)
            for sursa, path in local.items():
                conn.execute("BEGIN")
                try:
                    t0 = time.time()
                    n = load_file(conn, path, sursa, year, columns)
                    conn.commit()
                    print(f"      {sursa}: {n:,} rows in {time.time() - t0:.1f}s")
                except Exception:
                    conn.rollback()
                    raise
            write_meta(conn, "financiare_snapshot", ds_name)
            write_meta(conn, "financiare_year", year)
            write_meta(conn, "financiare_loaded_at", time.strftime("%Y-%m-%d %H:%M:%S"))
            conn.commit()
        finally:
            conn.close()

        print("[4/5] Verify...")
        conn = sqlite3.connect(args.db)
        try:
            total = conn.execute("SELECT COUNT(*) FROM financiare").fetchone()[0]
            per_year = conn.execute(
                "SELECT an, sursa, COUNT(*) FROM financiare GROUP BY an, sursa").fetchall()
            sample = conn.execute(
                "SELECT cui, cifra_de_afaceri_neta, profit_net, numar_mediu_salariati "
                "FROM financiare WHERE cui = '2816464'").fetchone()
            print(f"      total financiare rows: {total:,}")
            for an, sursa, c in per_year:
                print(f"      {an} / {sursa}: {c:,}")
            print(f"      DEDEMAN (2816464): {sample}")
            # Sanity floors per source
            ok = True
            for sursa, floor in MIN_ROWS.items():
                c = conn.execute(
                    "SELECT COUNT(*) FROM financiare WHERE sursa = ? AND an = ?",
                    (sursa, year)).fetchone()[0]
                if c < floor:
                    print(f"ABORT: {sursa} has only {c:,} rows (floor {floor:,})")
                    ok = False
            if not ok:
                print("Sanity check failed — NOT swapping in metadata. Run again after fixing.")
                return
        finally:
            conn.close()

        print("[5/5] Restart service...")
        if not args.no_restart:
            r = subprocess.run(["systemctl", "try-restart", "leadgen-mcp"],
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                print("      leadgen-mcp restarted.")
            else:
                print(f"      ! systemctl try-restart: {r.stderr.strip()[:200]}")
        print("Done.")
    finally:
        lock.close()


if __name__ == "__main__":
    main()
