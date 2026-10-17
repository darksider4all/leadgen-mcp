#!/usr/bin/env python3
"""Fail if any file in the repository holds something that must never be public:
private IPs, server paths, env files, API key or payment secrets, internal hostnames.

    python scripts/check_public.py

Checks every tracked file (plus new, not-ignored files, so it also works before a commit).
The patterns are written so that this file does not match itself.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERNS = [
    (r"192\.168\.", "private IPv4 (192.168/16)"),
    (r"\b10\.\d+\.\d+\.\d+", "private IPv4 (10/8)"),
    (r"/r[o]ot/", "server path (/root)"),
    (r"/op[t]/leadgen", "server path (/opt)"),
    (r"\.en[v]\b", "env file"),
    (r"lg_[0-9a-f]{8}", "Leadgen API key"),
    (r"sk_(live|test)_", "Stripe secret key"),
    (r"rk_liv[e]_", "Stripe restricted key"),
    (r"whse[c]_", "Stripe webhook secret"),
    (r"hermes\.adrianhomelab", "internal hostname"),
    (r"onrc-api\.adrianhomelab", "retired hostname"),
]
# The public hosts may appear anywhere; they are removed before matching.
ALLOWED = ["leadgen.adrianhomelab.com", "leadgen-mcp.adrianhomelab.com"]

COMBINED = re.compile("|".join(f"(?:{p})" for p, _ in PATTERNS))


def files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout.decode()
    return [ROOT / name for name in out.split("\0") if name and (ROOT / name).is_file()]


def scan(path: Path) -> list[tuple[int, str, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        clean = line
        for host in ALLOWED:
            clean = clean.replace(host, "")
        if not COMBINED.search(clean):
            continue
        for pattern, why in PATTERNS:
            match = re.search(pattern, clean)
            if match:
                hits.append((number, why, match.group(0)))
    return hits


def main() -> int:
    failures = 0
    checked = files()
    for path in checked:
        for number, why, found in scan(path):
            failures += 1
            print(f"{path.relative_to(ROOT)}:{number}: {why}: {found!r}")
    if failures:
        print(f"check_public: FAIL, {failures} finding(s) in {len(checked)} files", file=sys.stderr)
        return 1
    print(f"check_public: OK, {len(checked)} files clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
