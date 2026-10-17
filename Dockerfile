# Leadgen MCP: the open-source local MCP server (stdio) over Leadgen's public REST API.
#   docker build -t leadgen-mcp .
#   docker run -i --rm -e LEADGEN_API_KEY=<your key> leadgen-mcp
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY leadgen_mcp ./leadgen_mcp
RUN pip install . && useradd --create-home --uid 10001 leadgen

USER leadgen
CMD ["leadgen-mcp"]
