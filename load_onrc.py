#!/usr/bin/env python3
"""Download and load ONRC Romanian company registry data into SQLite.

Source: data.gov.ro CKAN (ONRC organisation), monthly snapshots.
Delimiter: '^'. Join key across files: COD_INMATRICULARE.

Refresh strategy (atomic swap — a failed load can never leave an empty DB):
  1. Find the latest 'firme-*' dataset on CKAN.
  2. If the live DB already holds that exact snapshot, exit (idempotent).
  3. Download the CSVs (resumable, cached under --data-dir).
  4. Build a fresh DB at <db>.new: schema + data + nomenclatoare + FTS5
     search indexes (firme_fts, reprez_fts, diacritic-folded).
  5. Verify row counts exceed sanity thresholds.
  6. os.replace(<db>.new -> <db>) — atomic on the same filesystem. The
     running server keeps serving the old inode until restarted, so a
     mid-load failure simply leaves the old DB untouched.
  7. Restart the systemd service so it picks up the new file.

A lock file (flock, non-blocking) prevents concurrent runs.

Usage:
  python3 load_onrc.py [--data-dir DIR] [--db PATH] [--force]
                       [--no-restart] [--build-fts-only]
"""
import argparse
import csv
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
import unicodedata

CKAN_API = "https://data.gov.ro/api/3/action"
DATASET_PREFIX = "firme-"
NOMENCLATOARE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "load_nomenclatoare.py")

# The four files worth loading (name fragment → table + column mapping)
FILES = {
    "OD_FIRME": "firme",
    "OD_CAEN_AUTORIZAT": "caen",
    "OD_REPREZENTANTI_LEGALI": "reprezentanti",
    "OD_STARE_FIRMA": "stare",
}

# Sanity floors — the real snapshot has millions of rows; a truncated download
# or an upstream data change that collapses below these should NOT be swapped in.
MIN_ROWS = {"firme": 1_000_000, "caen": 5_000_000,
            "reprezentanti": 1_000_000, "stare": 1_000_000}

# Romanian diacritics (incl. legacy cedilla forms) → ASCII. Kept in sync with
# server.py's fold_diacritics so index and query folding agree.
_DIACRITIC_MAP = str.maketrans({
    "Ă": "A", "Â": "A", "Î": "I", "Ș": "S", "Ş": "S", "Ț": "T", "Ţ": "T",
    "ă": "a", "â": "a", "î": "i", "ș": "s", "ş": "s", "ț": "t", "ţ": "t",
})


def fold_diacritics(text: str) -> str:
    if not text:
        return ""
    t = text.translate(_DIACRITIC_MAP)
    t = unicodedata.normalize("NFKD", t)
    return "".join(ch for ch in t if not unicodedata.combining(ch)).upper()


def ckan_json(url, timeout=120):
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout), url],
                       capture_output=True, text=True, timeout=timeout + 10)
    return json.loads(r.stdout)


def latest_dataset():
    """Find the most recent 'firme-*' dataset + its resource URLs."""
    result = ckan_json(f"{CKAN_API}/package_search?q=organization:onrc&rows=100")
    datasets = result["result"]["results"]
    candidates = [d for d in datasets if d["name"].startswith(DATASET_PREFIX)]
    candidates.sort(key=lambda d: d.get("metadata_modified", ""), reverse=True)
    latest = candidates[0]
    pkg = ckan_json(f"{CKAN_API}/package_show?id={latest['name']}")
    resources = {}
    for r in pkg["result"]["resources"]:
        base = r["name"].replace(".CSV", "").replace(".csv", "").strip()
        resources[base] = {"url": r["url"], "size": r.get("size")}
    return latest["name"], latest.get("metadata_modified", ""), resources


def download(url, dest):
    """curl download, resumable, returns True on success."""
    r = subprocess.run(
        ["curl", "-s", "-L", "--max-time", "3600", "-C", "-", "-o", dest, url],
        capture_output=True, text=True, timeout=3700,
    )
    return os.path.exists(dest) and os.path.getsize(dest) > 0


def create_schema(conn):
    conn.executescript("""
    CREATE TABLE firme (
        denumire TEXT, cui TEXT, cod_inmatriculare TEXT PRIMARY KEY,
        data_inmatriculare TEXT, euid TEXT, forma_juridica TEXT,
        adr_tara TEXT, adr_judet TEXT, adr_localitate TEXT,
        adr_den_strada TEXT, adr_nr_strada TEXT, adr_bloc TEXT,
        adr_scara TEXT, adr_etaj TEXT, adr_apartament TEXT,
        adr_cod_postal TEXT, adr_sector TEXT, adr_completare TEXT,
        web TEXT, tara_firma_mama TEXT
    );
    CREATE INDEX idx_firme_denumire ON firme(denumire);
    CREATE INDEX idx_firme_cui ON firme(cui);
    CREATE TABLE caen (
        cod_inmatriculare TEXT, cod_caen TEXT, ver_caen TEXT
    );
    CREATE INDEX idx_caen_cod ON caen(cod_inmatriculare);
    CREATE TABLE reprezentanti (
        cod_inmatriculare TEXT, persoana_imputernicita TEXT, calitate TEXT
    );
    CREATE INDEX idx_reprez_cod ON reprezentanti(cod_inmatriculare);
    CREATE TABLE stare (
        cod_inmatriculare TEXT, cod TEXT
    );
    CREATE INDEX idx_stare_cod ON stare(cod_inmatriculare);
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY, value TEXT
    );
    """)


def build_fts_tables(conn):
    """Create + populate the firme_fts and reprez_fts FTS5 search indexes."""
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


def read_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def write_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(value)))


def load_csv_to_table(conn, path, table, columns):
    """Stream a ^-delimited CSV into a table. `columns` maps header→db column."""
    inserted = 0
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter="^")
        header = next(reader)
        header = [h.lstrip("\ufeff").strip().upper() for h in header]
        idx_to_col = []
        for h in header:
            idx_to_col.append(columns.get(h))
        placeholders = ",".join(["?"] * len(columns))
        colnames = ",".join(columns[c] for c in columns)
        sql = f"INSERT OR IGNORE INTO {table} ({colnames}) VALUES ({placeholders})"
        batch = []
        for row in reader:
            vals = []
            for i, col in enumerate(idx_to_col):
                if col is None:
                    continue
                vals.append(row[i].strip() if i < len(row) else "")
            if len(vals) != len(columns):
                continue
            batch.append(vals)
            if len(batch) >= 5000:
                conn.executemany(sql, batch)
                inserted += len(batch)
                batch = []
        if batch:
            conn.executemany(sql, batch)
            inserted += len(batch)
    return inserted


def acquire_lock(lock_path):
    """Non-blocking flock. Returns the open file or None if already locked."""
    lock = open(lock_path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    lock.write(f"{os.getpid()}\n")
    lock.flush()
    return lock


def colmaps_for():
    return {
        "firme": {
            "DENUMIRE": "denumire", "CUI": "cui", "COD_INMATRICULARE": "cod_inmatriculare",
            "DATA_INMATRICULARE": "data_inmatriculare", "EUID": "euid",
            "FORMA_JURIDICA": "forma_juridica", "ADR_TARA": "adr_tara",
            "ADR_JUDET": "adr_judet", "ADR_LOCALITATE": "adr_localitate",
            "ADR_DEN_STRADA": "adr_den_strada", "ADR_NR_STRADA": "adr_nr_strada",
            "ADR_BLOC": "adr_bloc", "ADR_SCARA": "adr_scara", "ADR_ETAJ": "adr_etaj",
            "ADR_APARTAMENT": "adr_apartament", "ADR_COD_POSTAL": "adr_cod_postal",
            "ADR_SECTOR": "adr_sector", "ADR_COMPLETARE": "adr_completare",
            "WEB": "web", "TARA_FIRMA_MAMA": "tara_firma_mama",
        },
        "caen": {"COD_INMATRICULARE": "cod_inmatriculare",
                 "COD_CAEN_AUTORIZAT": "cod_caen", "VER_CAEN_AUTORIZAT": "ver_caen"},
        "reprezentanti": {"COD_INMATRICULARE": "cod_inmatriculare",
                          "PERSOANA_IMPUTERNICITA": "persoana_imputernicita",
                          "CALITATE": "calitate"},
        "stare": {"COD_INMATRICULARE": "cod_inmatriculare", "COD": "cod"},
    }


def snapshot_matches_local(resources, data_dir):
    """Heuristic: do the local CSV sizes match the latest snapshot's resources?

    Used by --build-fts-only to stamp the real snapshot name onto a DB that
    was loaded before the meta table existed (old loader recorded nothing).
    Only returns True when ALL four resource sizes are present and equal.
    """
    for fname in FILES:
        res = resources.get(fname)
        local = os.path.join(data_dir, f"{fname}.csv")
        if not res or not res.get("size") or not os.path.exists(local):
            return False
        if abs(int(res["size"]) - os.path.getsize(local)) > 1024:
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/opt/leadgen-mcp/data")
    ap.add_argument("--db", default="/opt/leadgen-mcp/data/onrc.db")
    ap.add_argument("--force", action="store_true",
                    help="Rebuild even if the live DB already holds the latest snapshot")
    ap.add_argument("--no-restart", action="store_true",
                    help="Do not restart the leadgen-mcp service after a swap")
    ap.add_argument("--build-fts-only", action="store_true",
                    help="Only build FTS5 indexes on the existing DB (upgrade path), "
                         "then exit — no download, no swap")
    args = ap.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)
    lock_path = os.path.join(args.data_dir, ".refresh.lock")

    # --- FTS-only upgrade path (no download, in place) ---------------------- #
    if args.build_fts_only:
        if not os.path.exists(args.db):
            print("DB not found; nothing to index.")
            return
        lock = acquire_lock(lock_path)
        if lock is None:
            print("Another refresh is running; exiting.")
            return
        try:
            conn = sqlite3.connect(args.db)
            try:
                conn.executescript("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);")
                ds_name, modified, resources = latest_dataset()
                if snapshot_matches_local(resources, args.data_dir):
                    write_meta(conn, "snapshot", ds_name)
                    write_meta(conn, "snapshot_modified", modified)
                    print(f"Local CSVs match latest snapshot {ds_name} — stamped into meta.")
                elif read_meta(conn, "snapshot") is None:
                    write_meta(conn, "snapshot", "unknown-pre-fts")
                    print("Local CSVs do not match the latest CKAN snapshot; "
                          "meta.snapshot left as 'unknown-pre-fts' (next cron run will refresh).")
                n_firme = conn.execute("SELECT COUNT(*) FROM firme").fetchone()[0]
                print(f"firme rows: {n_firme:,}")
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='firme_fts'").fetchone():
                    t0 = time.time()
                    build_fts_tables(conn)
                    print(f"Built FTS5 indexes in {time.time() - t0:.1f}s")
                else:
                    print("FTS5 indexes already present; nothing to do.")
                write_meta(conn, "fts_built_at", time.strftime("%Y-%m-%d %H:%M:%S"))
                conn.commit()
            finally:
                conn.close()
        finally:
            lock.close()
        return

    # --- Full refresh path -------------------------------------------------- #
    lock = acquire_lock(lock_path)
    if lock is None:
        print("Another refresh is running; exiting.")
        return
    try:
        print("[1/4] Finding latest ONRC dataset...")
        ds_name, modified, resources = latest_dataset()
        print(f"      Latest: {ds_name} (modified {modified})")

        # Idempotency: if the live DB already holds this snapshot, skip.
        if os.path.exists(args.db) and not args.force:
            conn = sqlite3.connect(args.db)
            try:
                prev = read_meta(conn, "snapshot")
                n = conn.execute("SELECT COUNT(*) FROM firme").fetchone()[0]
            finally:
                conn.close()
            if prev == ds_name and n > 0:
                print(f"      DB is already on snapshot {ds_name} ({n:,} firms). Nothing to do.")
                return
            print(f"      DB on '{prev or 'unknown'}' ({n:,} firms) — refreshing to {ds_name}")

        # Download CSVs (cached; only missing/empty files are fetched)
        print("[2/4] Downloading CSVs...")
        for fname in FILES:
            if fname not in resources:
                print(f"      ! {fname} not found in resources, skipping")
                continue
            dest = os.path.join(args.data_dir, f"{fname}.csv")
            if not os.path.exists(dest) or os.path.getsize(dest) < 1000:
                print(f"      downloading {fname}...")
                download(resources[fname]["url"], dest)
            sz = os.path.getsize(dest) / 1e6
            print(f"      {fname}: {sz:.1f} MB")

        # Build the NEW database in a staging file (never touch the live one)
        staging = f"{args.db}.new"
        if os.path.exists(staging):
            os.remove(staging)
        conn = sqlite3.connect(staging)
        try:
            create_schema(conn)
            colmaps = colmaps_for()
            print("[3/4] Loading into staging DB...")
            for fname, table in FILES.items():
                path = os.path.join(args.data_dir, f"{fname}.csv")
                if not os.path.exists(path):
                    continue
                t0 = time.time()
                n = load_csv_to_table(conn, path, table, colmaps[table])
                print(f"      {table}: {n:,} rows in {time.time() - t0:.1f}s")

            # Nomenclatoare (decode tables) into the SAME staging DB
            if os.path.exists(NOMENCLATOARE_SCRIPT):
                r = subprocess.run(
                    [sys.executable, NOMENCLATOARE_SCRIPT,
                     "--db", staging, "--data-dir", args.data_dir],
                    capture_output=True, text=True, timeout=1800)
                print("      nomenclatoare:", (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else "?")
                if r.returncode != 0:
                    print(f"      ! nomenclatoare failed: {r.stderr[-500:]}")
            else:
                print("      ! load_nomenclatoare.py missing; decode tables skipped")

            # FTS5 indexes (folded, diacritic-insensitive)
            t0 = time.time()
            build_fts_tables(conn)
            print(f"      FTS5 indexes built in {time.time() - t0:.1f}s")

            # Snapshot metadata
            write_meta(conn, "snapshot", ds_name)
            write_meta(conn, "snapshot_modified", modified)
            write_meta(conn, "loaded_at", time.strftime("%Y-%m-%d %H:%M:%S"))

            counts = {}
            for table in FILES.values():
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            conn.commit()

            # Verify before swap — abort on any table below its sanity floor
            for table, floor in MIN_ROWS.items():
                if counts.get(table, 0) < floor:
                    print(f"ABORT: {table} has only {counts.get(table, 0):,} rows "
                          f"(floor {floor:,}). Not swapping; live DB untouched.")
                    conn.close()
                    os.remove(staging)
                    return
        except Exception:
            conn.close()
            if os.path.exists(staging):
                os.remove(staging)
            raise

        print("[4/4] Summary:")
        for table in FILES.values():
            print(f"      {table}: {counts[table]:,} rows")

        # Atomic swap: new file becomes the live DB
        os.replace(staging, args.db)
        print(f"      Swapped: {args.db} is now snapshot {ds_name}")

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
