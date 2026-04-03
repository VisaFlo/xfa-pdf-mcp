FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libqpdf-dev \
    default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/
COPY lib/ lib/

RUN pip install --no-cache-dir .

ENV PORT=8080
ENV HOST=0.0.0.0

CMD ["python", "-m", "xfa_pdf_mcp.server_remote"]
