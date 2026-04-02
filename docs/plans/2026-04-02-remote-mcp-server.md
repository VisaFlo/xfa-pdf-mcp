# Remote MCP Server + REST API Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deploy xfa-pdf-mcp as a hosted cloud service so users can connect with just a URL — no Python, no installation, no config files. Also expose a REST API for ChatGPT and other LLMs.

**Architecture:** Add a remote server module (`server_remote.py`) that runs the same `XfaPdfEngine` over streamable-http MCP transport + a FastAPI REST wrapper. File handling switches from local paths to base64 upload/download. Deploy as a Docker container on Google Cloud Run. Keep the local stdio server unchanged.

**Tech Stack:** Python 3.11, FastMCP `streamable-http`, FastAPI, Docker, Google Cloud Run, pikepdf, lxml

**Project Location:** `/Users/Yulbin/Documents/Dev/2minEasy/xfa/xfa-pdf-mcp/`

---

### Task 1: Add base64 file upload/download to engine

The current engine uses local file paths (`engine.open(Path(...))` and `engine.save(doc_id, Path(...))`). The remote server needs to accept raw PDF bytes instead. Add two new methods without changing existing ones.

**Files:**
- Modify: `src/xfa_pdf_mcp/engine.py`
- Create: `tests/test_engine_bytes.py`

**Step 1: Write the failing test**

```python
# tests/test_engine_bytes.py
"""Tests for byte-based open/save (for remote server)."""

import pytest
from pathlib import Path
from xfa_pdf_mcp.engine import XfaPdfEngine
from tests.conftest import get_test_pdf


@pytest.fixture
def engine():
    e = XfaPdfEngine()
    yield e
    for doc_id in list(e.documents.keys()):
        e.close(doc_id)


def test_open_from_bytes(engine):
    pdf_bytes = get_test_pdf().read_bytes()
    doc_id = engine.open_bytes(pdf_bytes, "test.pdf")
    assert doc_id in engine.documents
    fields = engine.list_fields(doc_id)
    assert len(fields) > 0


def test_save_to_bytes(engine):
    pdf_bytes = get_test_pdf().read_bytes()
    doc_id = engine.open_bytes(pdf_bytes, "test.pdf")
    engine.fill_fields(doc_id, {
        "form1/Page1/PersonalDetails/Name/FamilyName": "BYTESTEST",
    })
    result_bytes = engine.save_bytes(doc_id)
    assert len(result_bytes) > 0
    assert result_bytes[:5] == b"%PDF-"

    # Roundtrip: reopen from saved bytes
    doc_id2 = engine.open_bytes(result_bytes, "roundtrip.pdf")
    vals = engine.get_field_values(doc_id2, [
        "form1/Page1/PersonalDetails/Name/FamilyName",
    ])
    assert vals["form1/Page1/PersonalDetails/Name/FamilyName"] == "BYTESTEST"
```

**Step 2: Run test to verify it fails**

```bash
cd /Users/Yulbin/Documents/Dev/2minEasy/xfa/xfa-pdf-mcp
.venv/bin/pytest tests/test_engine_bytes.py -v
```

Expected: AttributeError `open_bytes` not found.

**Step 3: Implement open_bytes and save_bytes**

Add to `engine.py` after the `open()` method:

```python
import io
import tempfile

def open_bytes(self, pdf_bytes: bytes, filename: str = "upload.pdf") -> str:
    """Open an XFA-PDF from raw bytes. Returns a document ID."""
    # pikepdf needs a file-like object or path
    pdf_stream = io.BytesIO(pdf_bytes)
    try:
        pdf = pikepdf.Pdf.open(pdf_stream)
    except Exception as e:
        raise ValueError(f"Cannot open PDF: {e}")

    # Reuse the same XFA extraction logic
    acroform = pdf.Root.get("/AcroForm")
    if not acroform:
        raise ValueError("No XFA: PDF has no AcroForm")
    xfa = acroform.get("/XFA")
    if not xfa:
        raise ValueError("No XFA: PDF has AcroForm but no XFA data")
    if not isinstance(xfa, pikepdf.Array):
        raise ValueError("No XFA: unexpected XFA format (not an array)")

    datasets_index = None
    datasets_root = None
    template_root = None
    template_ns = None

    for i in range(0, len(xfa), 2):
        key = str(xfa[i])
        if key == "datasets":
            datasets_index = i + 1
            xml_bytes = bytes(xfa[i + 1].read_bytes())
            datasets_root = etree.fromstring(xml_bytes)
        elif key == "template":
            tmpl_bytes = bytes(xfa[i + 1].read_bytes())
            template_root = etree.fromstring(tmpl_bytes)
            root_ns = template_root.tag.split("}")[0].lstrip("{") if "}" in template_root.tag else ""
            if root_ns:
                template_ns = root_ns
            else:
                for ns in TEMPLATE_NS_PREFIXES:
                    if template_root.findall(f".//{{{ns}}}field"):
                        template_ns = ns
                        break

    if datasets_index is None or datasets_root is None:
        raise ValueError("No XFA: datasets section not found")

    ns = {"xfa": XFA_DATA_NS}
    data_node = datasets_root.find(".//xfa:data", ns)
    if data_node is None:
        raise ValueError("No XFA: xfa:data node not found in datasets")

    doc_id = str(uuid.uuid4())[:8]
    detected_ns = template_ns or TEMPLATE_NS_PREFIXES[0]
    doc = OpenDocument(
        pdf=pdf,
        source_path=Path(filename),
        xfa_array=xfa,
        datasets_index=datasets_index,
        datasets_root=datasets_root,
        data_node=data_node,
        template_ns=detected_ns,
        template_root=template_root,
    )
    doc.lov_data = self._extract_lov(datasets_root)
    doc.field_meta = self._build_field_meta(template_root, detected_ns, doc.lov_data)
    doc.repeating_sections = self._extract_repeating_sections(template_root, detected_ns)
    self.documents[doc_id] = doc
    return doc_id

def save_bytes(self, doc_id: str) -> bytes:
    """Save the filled PDF and return as bytes."""
    doc = self._get_doc(doc_id)

    modified_xml = etree.tostring(
        doc.datasets_root, xml_declaration=False, encoding="unicode"
    ).encode("utf-8")
    doc.xfa_array[doc.datasets_index].write(modified_xml)

    if "/Perms" in doc.pdf.Root:
        del doc.pdf.Root["/Perms"]
    if "/DSS" in doc.pdf.Root:
        del doc.pdf.Root["/DSS"]
    acroform = doc.pdf.Root.get("/AcroForm")
    if acroform:
        if "/SigFlags" in acroform:
            del acroform["/SigFlags"]
        self._strip_signature_fields(acroform.get("/Fields", []))

    output = io.BytesIO()
    doc.pdf.save(output)
    return output.getvalue()
```

Note: There's code duplication between `open()` and `open_bytes()`. Refactor later — extract shared XFA parsing into a private method `_init_document(pdf, filename)`. For now, keep it simple.

**Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_engine_bytes.py tests/test_engine.py -v
```

Expected: All pass.

**Step 5: Commit**

```bash
git add src/xfa_pdf_mcp/engine.py tests/test_engine_bytes.py
git commit -m "feat: add open_bytes/save_bytes for remote server support"
```

---

### Task 2: Remote MCP server (streamable-http)

Create a new server module that uses the same tools but with base64 file handling instead of local paths.

**Files:**
- Create: `src/xfa_pdf_mcp/server_remote.py`

**Step 1: Create the remote server**

```python
# src/xfa_pdf_mcp/server_remote.py
"""Remote MCP server for XFA-PDF form filling (streamable-http transport)."""

import base64
import os
from mcp.server.fastmcp import FastMCP
from xfa_pdf_mcp.engine import XfaPdfEngine

mcp = FastMCP(
    "xfa-pdf-mcp",
    instructions=(
        "This server fills XFA-PDF form fields (e.g. IRCC immigration forms). "
        "Workflow: upload_pdf -> list_fields -> fill_fields -> download_pdf -> close_pdf. "
        "Fields are addressed by their XFA path (e.g. form1/Page1/PersonalDetails/Name/FamilyName). "
        "Upload PDFs as base64-encoded bytes. Download filled PDFs as base64."
    ),
)

engine = XfaPdfEngine()


@mcp.tool()
def upload_pdf(pdf_base64: str, filename: str = "form.pdf") -> dict:
    """Upload an XFA-PDF form as base64 and get a document ID.

    Args:
        pdf_base64: Base64-encoded PDF file bytes.
        filename: Original filename (for reference).

    Returns:
        Document ID, form name, and field count.
    """
    pdf_bytes = base64.b64decode(pdf_base64)
    doc_id = engine.open_bytes(pdf_bytes, filename)
    fields = engine.list_fields(doc_id)
    return {
        "doc_id": doc_id,
        "file": filename,
        "field_count": len(fields),
        "message": f"Uploaded {filename} with {len(fields)} fields.",
    }


@mcp.tool()
def list_fields(doc_id: str, filter_type: str = "") -> list[dict]:
    """List all fillable fields in an open XFA-PDF.

    Args:
        doc_id: Document ID from upload_pdf.
        filter_type: Optional filter by field type.

    Returns:
        List of fields with path, type, current value, and options.
    """
    fields = engine.list_fields(doc_id)
    if filter_type:
        fields = [f for f in fields if f["type"] == filter_type]
    return fields


@mcp.tool()
def get_field_values(doc_id: str, paths: list[str]) -> dict:
    """Get current values for specific fields.

    Args:
        doc_id: Document ID from upload_pdf.
        paths: List of field paths.

    Returns:
        Dict mapping each path to its current value.
    """
    return engine.get_field_values(doc_id, paths)


@mcp.tool()
def fill_fields(doc_id: str, field_values: dict[str, str]) -> dict:
    """Fill form fields with values (auto-resolves labels, checkboxes, dates).

    Args:
        doc_id: Document ID from upload_pdf.
        field_values: Dict mapping field paths to values.

    Returns:
        Dict mapping each path to success status.
    """
    results = engine.fill_fields(doc_id, field_values)
    filled_count = sum(1 for v in results.values() if v)
    return {
        "results": results,
        "message": f"Filled {filled_count}/{len(field_values)} fields.",
    }


@mcp.tool()
def download_pdf(doc_id: str) -> dict:
    """Download the filled PDF as base64.

    Args:
        doc_id: Document ID from upload_pdf.

    Returns:
        Base64-encoded PDF bytes and filename.
    """
    doc = engine._get_doc(doc_id)
    pdf_bytes = engine.save_bytes(doc_id)
    return {
        "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        "filename": f"filled_{doc.source_path.name}",
        "size_bytes": len(pdf_bytes),
    }


@mcp.tool()
def list_repeating_sections(doc_id: str) -> list[dict]:
    """List all repeating sections (dynamic rows) in the form.

    Args:
        doc_id: Document ID from upload_pdf.

    Returns:
        List of repeating sections with path, name, max rows, current count.
    """
    return engine.list_repeating_sections(doc_id)


@mcp.tool()
def add_row(doc_id: str, section_path: str, field_values: dict[str, str]) -> dict:
    """Add a new row to a repeating section.

    Args:
        doc_id: Document ID from upload_pdf.
        section_path: Path from list_repeating_sections.
        field_values: Dict mapping field names to values.

    Returns:
        Row index and resolved values.
    """
    return engine.add_row(doc_id, section_path, field_values)


@mcp.tool()
def close_pdf(doc_id: str) -> dict:
    """Close an open PDF and free resources.

    Args:
        doc_id: Document ID from upload_pdf.

    Returns:
        Confirmation message.
    """
    engine.close(doc_id)
    return {"message": f"Document {doc_id} closed."}


def main():
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
```

**Step 2: Test locally**

```bash
cd /Users/Yulbin/Documents/Dev/2minEasy/xfa/xfa-pdf-mcp
.venv/bin/python -m xfa_pdf_mcp.server_remote &
# In another terminal:
curl -s http://localhost:8080/mcp -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
# Should return MCP initialize response
kill %1
```

**Step 3: Add entry point to pyproject.toml**

Add to `[project.scripts]`:
```toml
xfa-pdf-mcp-remote = "xfa_pdf_mcp.server_remote:main"
```

**Step 4: Commit**

```bash
git add src/xfa_pdf_mcp/server_remote.py pyproject.toml
git commit -m "feat: add remote MCP server with streamable-http transport"
```

---

### Task 3: REST API for ChatGPT and other LLMs

Add a FastAPI wrapper that exposes the same engine as REST endpoints with OpenAPI spec.

**Files:**
- Create: `src/xfa_pdf_mcp/api.py`
- Modify: `pyproject.toml` (add `fastapi`, `uvicorn` deps)

**Step 1: Add FastAPI dependency**

In `pyproject.toml`, add to `dependencies`:
```toml
"fastapi>=0.100.0",
"uvicorn>=0.20.0",
"python-multipart>=0.0.6",
```

Install:
```bash
.venv/bin/pip install -e .
```

**Step 2: Create the REST API**

```python
# src/xfa_pdf_mcp/api.py
"""REST API for XFA-PDF form filling (for ChatGPT Actions and other LLMs)."""

import base64
import os
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from xfa_pdf_mcp.engine import XfaPdfEngine

app = FastAPI(
    title="XFA-PDF Form Filler API",
    description="Fill XFA-PDF form fields programmatically. Supports IRCC immigration forms.",
    version="0.1.0",
)

engine = XfaPdfEngine()


class UploadResponse(BaseModel):
    doc_id: str
    filename: str
    field_count: int


class FillRequest(BaseModel):
    field_values: dict[str, str]


class FillResponse(BaseModel):
    results: dict[str, bool]
    message: str


class AddRowRequest(BaseModel):
    section_path: str
    field_values: dict[str, str]


@app.post("/upload", response_model=UploadResponse)
async def upload_pdf(file: UploadFile = File(...)):
    """Upload an XFA-PDF form file."""
    pdf_bytes = await file.read()
    try:
        doc_id = engine.open_bytes(pdf_bytes, file.filename or "form.pdf")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    fields = engine.list_fields(doc_id)
    return UploadResponse(
        doc_id=doc_id,
        filename=file.filename or "form.pdf",
        field_count=len(fields),
    )


@app.get("/documents/{doc_id}/fields")
async def list_fields(doc_id: str, filter_type: str = ""):
    """List all fillable fields in an uploaded form."""
    try:
        fields = engine.list_fields(doc_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if filter_type:
        fields = [f for f in fields if f["type"] == filter_type]
    return fields


@app.post("/documents/{doc_id}/fill", response_model=FillResponse)
async def fill_fields(doc_id: str, request: FillRequest):
    """Fill form fields with values."""
    try:
        results = engine.fill_fields(doc_id, request.field_values)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    filled = sum(1 for v in results.values() if v)
    return FillResponse(
        results=results,
        message=f"Filled {filled}/{len(request.field_values)} fields.",
    )


@app.get("/documents/{doc_id}/download")
async def download_pdf(doc_id: str):
    """Download the filled PDF."""
    try:
        pdf_bytes = engine.save_bytes(doc_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    doc = engine._get_doc(doc_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="filled_{doc.source_path.name}"'},
    )


@app.get("/documents/{doc_id}/repeating-sections")
async def list_repeating_sections(doc_id: str):
    """List dynamic row sections."""
    try:
        return engine.list_repeating_sections(doc_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/documents/{doc_id}/add-row")
async def add_row(doc_id: str, request: AddRowRequest):
    """Add a row to a repeating section."""
    try:
        return engine.add_row(doc_id, request.section_path, request.field_values)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/documents/{doc_id}")
async def close_pdf(doc_id: str):
    """Close and free an uploaded document."""
    try:
        engine.close(doc_id)
    except ValueError:
        pass
    return {"message": f"Document {doc_id} closed."}


def main():
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
```

**Step 3: Test locally**

```bash
.venv/bin/python -m xfa_pdf_mcp.api &
# Upload a PDF
curl -s -F "file=@/Users/Yulbin/Downloads/imm5257e.pdf" http://localhost:8080/upload
# List fields
curl -s http://localhost:8080/documents/{doc_id}/fields | head -20
# Fill fields
curl -s -X POST http://localhost:8080/documents/{doc_id}/fill \
  -H "Content-Type: application/json" \
  -d '{"field_values": {"form1/Page1/PersonalDetails/Name/FamilyName": "TEST"}}'
# Download
curl -s http://localhost:8080/documents/{doc_id}/download -o filled.pdf
kill %1
```

**Step 4: Commit**

```bash
git add src/xfa_pdf_mcp/api.py pyproject.toml
git commit -m "feat: add REST API for ChatGPT Actions and other LLMs"
```

---

### Task 4: Dockerfile and Cloud Run deployment

**Files:**
- Create: `Dockerfile`
- Create: `docker/cloudbuild.yaml` (optional, for CI/CD)

**Step 1: Create Dockerfile**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system deps for pikepdf (needs libqpdf)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libqpdf-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

# Default to remote MCP server; override with CMD for API
ENV PORT=8080
ENV HOST=0.0.0.0

# Run both MCP and REST API on the same port using a combined entrypoint
COPY src/xfa_pdf_mcp/server_combined.py src/xfa_pdf_mcp/server_combined.py

CMD ["python", "-m", "xfa_pdf_mcp.server_combined"]
```

**Step 2: Create combined server entrypoint**

```python
# src/xfa_pdf_mcp/server_combined.py
"""Combined server: MCP (streamable-http) + REST API on one port."""

import os
from xfa_pdf_mcp.server_remote import mcp as mcp_app
from xfa_pdf_mcp.api import app as rest_app

# Mount REST API alongside MCP
# FastMCP's streamable-http uses /mcp path by default
# REST API uses /api/* paths
# Use Starlette mount to combine them

from starlette.applications import Starlette
from starlette.routing import Mount

combined = Starlette(routes=[
    Mount("/api", app=rest_app),
])

# For now, just run the MCP server which handles /mcp
# REST API runs separately or we use a reverse proxy
# Simplest: run MCP server_remote (it handles /mcp endpoint)
# and expose REST API on /api via the same FastAPI app

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))

    # The MCP SDK's streamable-http creates its own ASGI app
    # We'll run the REST API with MCP mounted
    from xfa_pdf_mcp.api import app
    # Mount MCP's SSE/streamable-http handler
    mcp_app_instance = mcp_app._mcp_server

    # Simplest approach: run REST API, let users choose MCP or REST
    uvicorn.run(app, host=host, port=port)
```

Actually, this is getting complex. Simpler approach: **run MCP and REST API as separate processes in the container, or just run the MCP server** since that's the primary use case. REST API can be a separate `CMD` option.

Revised Dockerfile:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libqpdf-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

ENV PORT=8080
ENV HOST=0.0.0.0

# Default: MCP server (streamable-http)
# Override with: CMD ["python", "-m", "xfa_pdf_mcp.api"] for REST API
CMD ["python", "-m", "xfa_pdf_mcp.server_remote"]
```

**Step 3: Build and test locally**

```bash
cd /Users/Yulbin/Documents/Dev/2minEasy/xfa/xfa-pdf-mcp
docker build -t xfa-pdf-mcp .
docker run -p 8080:8080 xfa-pdf-mcp
# Test MCP endpoint
curl -s http://localhost:8080/mcp -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

**Step 4: Deploy to Cloud Run**

```bash
# Deploy MCP server
gcloud run deploy xfa-pdf-mcp \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --max-instances 10

# Deploy REST API (same image, different CMD)
gcloud run deploy xfa-pdf-api \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --command "python,-m,xfa_pdf_mcp.api"
```

**Step 5: Commit**

```bash
git add Dockerfile
git commit -m "feat: add Dockerfile for Cloud Run deployment"
```

---

### Task 5: Update README and docs

**Files:**
- Modify: `README.md`

**Step 1: Add remote setup instructions**

Add sections for:
- **Quick Start (Hosted)** — one-line setup for Claude Desktop/Code
- **REST API** — endpoints for ChatGPT Actions
- **Self-Hosted** — Docker instructions for users who want to run their own
- **Local Development** — existing stdio setup for developers

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add remote server, REST API, and Docker setup instructions"
```

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | Byte-based open/save for remote use | engine.py, test_engine_bytes.py |
| 2 | Remote MCP server (streamable-http) | server_remote.py, pyproject.toml |
| 3 | REST API for ChatGPT/other LLMs | api.py, pyproject.toml |
| 4 | Dockerfile + Cloud Run deployment | Dockerfile |
| 5 | Updated README | README.md |

**Total: 5 tasks**
