#!/usr/bin/env bash
# git init + commit + create public repo + push for leadgen-mcp.
# Uses the gh auth already established for darksider4all.
set -euo pipefail
cd /opt/data/leadgen-mcp
GH=/opt/data/bin/gh

git init -q
git config user.name "Adrian Oaida"
git config user.email "oaida.adrian@gmail.com"

git add -A
# safety: never stage secrets
git rm --cached --ignore-unmatch .github-identity _read_gh_identity.py _gh_auth.sh >/dev/null 2>&1 || true

git commit -q -m "Initial public release of Leadgen MCP (4 tools, streamable HTTP)" || echo "nothing to commit"

# create public repo if absent
if ! "$GH" repo view darksider4all/leadgen-mcp >/dev/null 2>&1; then
  "$GH" repo create darksider4all/leadgen-mcp --public --source=. --push --description "Lead-generation & enrichment MCP server (Romanian ONRC registry, contact extraction, WHOIS/DNS audit)"
else
  git remote remove origin >/dev/null 2>&1 || true
  git remote add origin https://github.com/darksider4all/leadgen-mcp.git
  git push -u origin main 2>&1 || git push -u origin master 2>&1
fi

echo "--- remote ---"
git remote -v
echo "--- default branch ---"
git branch --show-current
