#!/usr/bin/env python3
"""Download + load ONRC nomenclatoare (decode tables) into the onrc.db.

Decodes: company status (stare) and CAEN activity codes.
Source: data.gov.ro CKAN dataset 'nomenclatoare-*'.

Usage:
  python3 load_nomenclatoare.py [--db PATH] [--data-dir DIR]

Normally invoked by load_onrc.py against its staging DB so the decode tables
land in the same atomic swap as the main data.
"""
import argparse
import csv
import json
import os
import sqlite3
import subprocess

CKAN_API = "https://data.gov.ro/api/3/action"


def ckan_json(url, timeout=120):
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout), url],
                       capture_output=True, text=True, timeout=timeout + 10)
    return json.loads(r.stdout)


def latest_nomenclatoare():
    result = ckan_json(f"{CKAN_API}/package_search?q=organization:onrc&rows=100")
    datasets = result["result"]["results"]
    cands = [d for d in datasets if d["name"].startswith("nomenclatoare-")]
    cands.sort(key=lambda d: d.get("metadata_modified", ""), reverse=True)
    latest = cands[0]
    pkg = ckan_json(f"{CKAN_API}/package_show?id={latest['name']}")
    resources = {}
    for r in pkg["result"]["resources"]:
        base = r["name"].replace(".CSV", "").replace(".csv", "").strip()
        resources[base] = r["url"]
    return latest["name"], resources


def download(url, dest):
    subprocess.run(["curl", "-s", "-L", "--max-time", "300", "-o", dest, url],
                   capture_output=True, timeout=320)
    return os.path.exists(dest) and os.path.getsize(dest) > 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/opt/leadgen-mcp/data/onrc.db")
    ap.add_argument("--data-dir", default="/opt/leadgen-mcp/data")
    args = ap.parse_args()

    print("[1/3] Finding latest nomenclatoare...")
    ds_name, resources = latest_nomenclatoare()
    print(f"      {ds_name}")

    os.makedirs(args.data_dir, exist_ok=True)

    conn = sqlite3.connect(args.db)
    conn.executescript("""
    DROP TABLE IF EXISTS stare_nom;
    DROP TABLE IF EXISTS caen_nom;
    CREATE TABLE stare_nom (cod TEXT PRIMARY KEY, denumire TEXT);
    CREATE TABLE caen_nom (clasa TEXT PRIMARY KEY, denumire TEXT);
    """)

    print("[2/3] Downloading nomenclatoare CSVs...")
    paths = {}
    for fname in ["N_STARE_FIRMA", "N_CAEN"]:
        if fname not in resources:
            print(f"      ! {fname} missing")
            continue
        dest = os.path.join(args.data_dir, f"{fname}.csv")
        if not os.path.exists(dest):
            download(resources[fname], dest)
        paths[fname] = dest
        print(f"      {fname}: {os.path.getsize(dest)} bytes")

    print("[3/3] Loading...")
    # N_STARE_FIRMA: COD^DENUMIRE
    if "N_STARE_FIRMA" in paths:
        n = 0
        with open(paths["N_STARE_FIRMA"], encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter="^")
            header = [h.lstrip("\ufeff").strip().upper() for h in next(reader)]
            ci, di = header.index("COD"), header.index("DENUMIRE")
            for row in reader:
                if len(row) > max(ci, di):
                    conn.execute("INSERT OR REPLACE INTO stare_nom VALUES (?,?)",
                                 (row[ci].strip(), row[di].strip()))
                    n += 1
        print(f"      stare_nom: {n} codes")

    # N_CAEN: SECTIUNEA^...^CLASA^DENUMIRE^VERSIUNE_CAEN
    if "N_CAEN" in paths:
        n = 0
        with open(paths["N_CAEN"], encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter="^")
            header = [h.lstrip("\ufeff").strip().upper() for h in next(reader)]
            ci = header.index("CLASA")
            di = header.index("DENUMIRE")
            for row in reader:
                if len(row) > max(ci, di):
                    conn.execute("INSERT OR REPLACE INTO caen_nom VALUES (?,?)",
                                 (row[ci].strip(), row[di].strip()))
                    n += 1
        print(f"      caen_nom: {n} codes")

    conn.commit()
    # verify
    s = conn.execute("SELECT COUNT(*) FROM stare_nom").fetchone()[0]
    c = conn.execute("SELECT COUNT(*) FROM caen_nom").fetchone()[0]
    conn.close()
    print(f"Done. stare_nom={s}, caen_nom={c}")


if __name__ == "__main__":
    main()
