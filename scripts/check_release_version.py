#!/usr/bin/env python3
"""Refuse a release whose tag does not match the package version.

    python scripts/check_release_version.py v1.2.3

The tag must be vX.Y.Z, and X.Y.Z must equal the version in pyproject.toml,
__version__ in the Python package. The release workflow runs this
before it builds anything, so a mistyped tag publishes nothing.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "leadgen_mcp/__init__.py"


def dunder_version(path: Path) -> str | None:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_release_version.py vX.Y.Z", file=sys.stderr)
        return 2
    tag = argv[1]
    match = re.fullmatch(r"v(\d+\.\d+\.\d+)", tag)
    if not match:
        print(f"release tag {tag!r} is not vX.Y.Z: refusing to publish", file=sys.stderr)
        return 1
    wanted = match.group(1)
    found = {
        "pyproject.toml": tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        str(INIT.relative_to(ROOT)): dunder_version(INIT),
    }
    package_json = ROOT / "package.json"
    if package_json.exists():
        found["package.json"] = json.loads(package_json.read_text(encoding="utf-8"))["version"]
    wrong = {where: version for where, version in found.items() if version != wanted}
    for where, version in found.items():
        print(f"{where}: {version}{'' if version == wanted else '  <- does not match ' + tag}")
    if wrong:
        print(f"release tag {tag} does not match the package version: refusing to publish", file=sys.stderr)
        return 1
    print(f"release tag {tag} matches the package version")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
