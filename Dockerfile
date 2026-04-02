FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libqpdf-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir .

ENV PORT=8080
ENV HOST=0.0.0.0

# Default: remote MCP server (streamable-http)
# Override with: CMD ["python", "-m", "xfa_pdf_mcp.api"] for REST API
CMD ["python", "-m", "xfa_pdf_mcp.server_remote"]
