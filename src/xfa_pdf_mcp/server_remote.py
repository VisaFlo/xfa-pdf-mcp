"""Remote MCP server for XFA-PDF form filling (streamable-http transport)."""

import base64
import hashlib
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from xfa_pdf_mcp.engine import XfaPdfEngine

_host = os.environ.get("HOST", "0.0.0.0")
_port = int(os.environ.get("PORT", "8080"))
_base_url = os.environ.get("BASE_URL", "https://xfa-pdf-mcp.vflo.app")

# Temporary directory for storing filled PDFs for download
_tmp_dir = Path(tempfile.mkdtemp(prefix="xfa-pdf-mcp-"))

mcp = FastMCP(
    "xfa-pdf-mcp",
    host=_host,
    port=_port,
    instructions=(
        "This server fills XFA-PDF form fields (e.g. IRCC immigration forms). "
        "Workflow: upload_pdf -> list_fields -> fill_fields -> download_pdf -> close_pdf. "
        "Fields are addressed by their XFA path (e.g. form1/Page1/PersonalDetails/Name/FamilyName). "
        "When the user attaches a PDF file, pass it to upload_pdf using the file parameter "
        "(for ChatGPT file references) or pdf_url (for direct links). "
        "The download_pdf tool returns a download URL for the user to get their filled form."
    ),
)

engine = XfaPdfEngine()

# Store filled PDF bytes keyed by doc_id for download
_filled_cache: dict[str, tuple[bytes, str, float]] = {}  # doc_id -> (bytes, filename, timestamp)
_CACHE_TTL = 1800  # 30 minutes


# ---- Custom HTTP routes for file upload/download (non-MCP) ----

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response


@mcp.custom_route("/upload", methods=["GET"])
async def upload_page(request: Request) -> HTMLResponse:
    """Serve a simple upload page where users can drop their PDF."""
    html = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>XFA-PDF Upload</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #f5f5f5; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
  .container { background: white; border-radius: 12px; padding: 40px; max-width: 500px; width: 90%; box-shadow: 0 2px 12px rgba(0,0,0,0.1); }
  h1 { font-size: 1.5rem; margin-bottom: 8px; }
  p { color: #666; margin-bottom: 24px; font-size: 0.9rem; }
  .dropzone { border: 2px dashed #ccc; border-radius: 8px; padding: 40px 20px; text-align: center; cursor: pointer; transition: all 0.2s; }
  .dropzone:hover, .dropzone.dragover { border-color: #2563eb; background: #eff6ff; }
  .dropzone input { display: none; }
  .result { margin-top: 20px; padding: 16px; border-radius: 8px; display: none; }
  .result.success { background: #f0fdf4; border: 1px solid #86efac; display: block; }
  .result.error { background: #fef2f2; border: 1px solid #fca5a5; display: block; }
  .doc-id { font-family: monospace; font-size: 1.2rem; font-weight: bold; color: #2563eb; user-select: all; }
  .loading { display: none; margin-top: 20px; text-align: center; color: #666; }
  .copy-btn { margin-top: 8px; padding: 6px 16px; background: #2563eb; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 0.85rem; }
  .copy-btn:hover { background: #1d4ed8; }
</style>
</head><body>
<div class="container">
  <h1>Upload XFA-PDF Form</h1>
  <p>Upload your IRCC immigration form (IMM series) to get a document ID for use with ChatGPT, Claude, or any AI assistant.</p>
  <div class="dropzone" id="dropzone" onclick="document.getElementById('fileInput').click()">
    <input type="file" id="fileInput" accept=".pdf">
    <p style="margin:0;color:#888;">Drop your PDF here or click to browse</p>
  </div>
  <div class="loading" id="loading">Uploading and parsing form...</div>
  <div class="result" id="result"></div>
</div>
<script>
const dz = document.getElementById('dropzone');
const fi = document.getElementById('fileInput');
const loading = document.getElementById('loading');
const result = document.getElementById('result');

dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('dragover'); });
dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('dragover'); handleFile(e.dataTransfer.files[0]); });
fi.addEventListener('change', () => { if (fi.files[0]) handleFile(fi.files[0]); });

async function handleFile(file) {
  if (!file.name.toLowerCase().endsWith('.pdf')) { showError('Please upload a PDF file.'); return; }
  loading.style.display = 'block';
  result.className = 'result';
  result.style.display = 'none';
  const formData = new FormData();
  formData.append('file', file);
  try {
    const resp = await fetch('/upload-file', { method: 'POST', body: formData });
    const data = await resp.json();
    if (data.error) { showError(data.error); return; }
    result.className = 'result success';
    result.innerHTML = '<p>Your document ID:</p><p class="doc-id">' + data.doc_id + '</p>' +
      '<p style="margin-top:8px;color:#666;font-size:0.85rem;">' + data.field_count + ' fields found in ' + data.filename + '</p>' +
      '<button class="copy-btn" onclick="navigator.clipboard.writeText(\\'' + data.doc_id + '\\')">Copy doc_id</button>' +
      '<p style="margin-top:12px;color:#666;font-size:0.85rem;">Give this doc_id to your AI assistant to fill the form.</p>';
    result.style.display = 'block';
  } catch (e) { showError('Upload failed: ' + e.message); }
  loading.style.display = 'none';
}

function showError(msg) {
  result.className = 'result error';
  result.innerHTML = '<p style="color:#dc2626;">' + msg + '</p>';
  result.style.display = 'block';
  loading.style.display = 'none';
}
</script>
</body></html>"""
    return HTMLResponse(html)


@mcp.custom_route("/upload-file", methods=["POST"])
async def upload_file_endpoint(request: Request) -> JSONResponse:
    """Accept multipart file upload, return doc_id."""
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        return JSONResponse({"error": "Expected multipart/form-data"}, status_code=400)

    form = await request.form()
    file = form.get("file")
    if not file:
        return JSONResponse({"error": "No file uploaded"}, status_code=400)

    pdf_bytes = await file.read()
    filename = getattr(file, "filename", "form.pdf") or "form.pdf"

    try:
        doc_id = engine.open_bytes(pdf_bytes, filename)
        fields = engine.list_fields(doc_id)
        return JSONResponse({
            "doc_id": doc_id,
            "filename": filename,
            "field_count": len(fields),
        })
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@mcp.custom_route("/download-file/{doc_id}", methods=["GET"])
async def download_file_endpoint(request: Request) -> Response:
    """Download a filled PDF by doc_id."""
    doc_id = request.path_params["doc_id"]

    # Check cache first (from download_pdf tool)
    if doc_id in _filled_cache:
        pdf_bytes, filename, _ = _filled_cache[doc_id]
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # Fall back to generating on the fly
    try:
        doc = engine._get_doc(doc_id)
        pdf_bytes = engine.save_bytes(doc_id)
        filename = f"filled_{doc.source_path.name}"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError:
        return JSONResponse({"error": f"Document {doc_id} not found"}, status_code=404)


def _cleanup_cache():
    """Remove expired entries from the filled PDF cache."""
    now = time.time()
    expired = [k for k, (_, _, ts) in _filled_cache.items() if now - ts > _CACHE_TTL]
    for k in expired:
        del _filled_cache[k]


@mcp.tool()
def upload_pdf(
    doc_id: str = "",
    pdf_url: str = "",
    pdf_base64: str = "",
    file: str = "",
    filename: str = "form.pdf",
) -> dict:
    """Open an XFA-PDF form for filling.

    When the user drops/attaches a PDF file in the chat, pass it using the file
    parameter or pdf_url. The server will download and parse it.

    Args:
        doc_id: Document ID if the PDF was already uploaded (reuse existing).
        pdf_url: Direct HTTP/HTTPS URL to the PDF file.
        pdf_base64: Base64-encoded PDF bytes.
        file: File reference (URL or JSON object with download_url from ChatGPT).
        filename: Original filename for reference.

    Returns:
        Document ID, form name, and field count.
    """
    # If doc_id provided, just verify it exists
    if doc_id:
        doc = engine._get_doc(doc_id)
        fields = engine.list_fields(doc_id)
        return {
            "doc_id": doc_id,
            "file": str(doc.source_path.name),
            "field_count": len(fields),
            "message": f"Document {doc_id} is ready with {len(fields)} fields.",
        }

    # Resolve the PDF bytes from whichever source is provided
    pdf_bytes = None

    # Handle ChatGPT file reference (JSON object with download_url)
    if file:
        url = None
        if isinstance(file, dict):
            url = file.get("download_url") or file.get("url")
        elif isinstance(file, str):
            # Could be a URL string or a JSON string
            if file.startswith("http"):
                url = file
            elif file.startswith("{"):
                import json as _json
                try:
                    obj = _json.loads(file)
                    url = obj.get("download_url") or obj.get("url")
                except _json.JSONDecodeError:
                    pass
            if not url:
                url = file  # treat as URL
        if url:
            pdf_url = url

    if pdf_url:
        parsed = urlparse(pdf_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Invalid URL scheme: {parsed.scheme}. Use http or https.")
        try:
            resp = httpx.get(pdf_url, follow_redirects=True, timeout=60)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "html" in content_type:
                raise ValueError(
                    "URL returned HTML, not a PDF. "
                    "Make sure the URL points directly to a .pdf file."
                )
            pdf_bytes = resp.content
            if len(pdf_bytes) < 100 or pdf_bytes[:5] != b"%PDF-":
                raise ValueError("Downloaded content is not a valid PDF file.")
        except httpx.HTTPError as e:
            raise ValueError(f"Failed to download PDF from URL: {e}")
        if not filename or filename == "form.pdf":
            filename = Path(parsed.path).name or "form.pdf"

    elif pdf_base64:
        try:
            pdf_bytes = base64.b64decode(pdf_base64)
        except Exception:
            raise ValueError("Invalid base64 encoding")

    if pdf_bytes is None:
        raise ValueError(
            "No PDF provided. Attach the PDF file in the chat, or provide a pdf_url."
        )

    doc_id = engine.open_bytes(pdf_bytes, filename)
    fields = engine.list_fields(doc_id)
    return {
        "doc_id": doc_id,
        "file": filename,
        "field_count": len(fields),
        "message": f"Uploaded {filename} with {len(fields)} fields. Use list_fields to see them.",
    }


@mcp.tool()
def list_fields(doc_id: str, filter_type: str = "") -> list[dict]:
    """List all fillable fields in an open XFA-PDF.

    Args:
        doc_id: Document ID from upload_pdf.
        filter_type: Optional filter by field type (e.g. 'textEdit', 'choiceList', 'checkButton').
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
    """
    return engine.get_field_values(doc_id, paths)


@mcp.tool()
def fill_fields(doc_id: str, field_values: dict[str, str]) -> dict:
    """Fill form fields with values (auto-resolves labels, checkboxes, dates).

    Args:
        doc_id: Document ID from upload_pdf.
        field_values: Dict mapping field paths to values.
            Labels are auto-resolved: "Canada" -> "511", "true" -> "Y", "01/15/2025" -> "2025-01-15"
    """
    results = engine.fill_fields(doc_id, field_values)
    filled_count = sum(1 for v in results.values() if v)

    # Check if phone fields were filled — they need a user action to display
    phone_filled = any(
        "NANumber" in p or "IntlNumber" in p or "AreaCode" in p or
        "FirstThree" in p or "LastFive" in p
        for p in field_values
    )
    note = ""
    if phone_filled:
        note = (
            " IMPORTANT: Phone number data is saved but appears hidden until activated. "
            "Tell the user: 'After opening in Adobe Reader, click the Canada/US (or Other) "
            "checkbox in the phone section to reveal the pre-filled phone number.'"
        )

    return {
        "results": results,
        "message": f"Filled {filled_count}/{len(field_values)} fields.{note}",
    }


@mcp.tool()
def download_pdf(doc_id: str) -> dict:
    """Save the filled PDF and get a download link.

    The user can download the filled PDF from the returned URL.
    Give the download_url to the user so they can download their filled form.

    Args:
        doc_id: Document ID from upload_pdf.

    Returns:
        Download URL, filename, and size.
    """
    _cleanup_cache()
    doc = engine._get_doc(doc_id)
    pdf_bytes = engine.save_bytes(doc_id)
    filename = f"filled_{doc.source_path.name}"

    # Cache for download via HTTP endpoint
    _filled_cache[doc_id] = (pdf_bytes, filename, time.time())

    download_url = f"{_base_url}/download-file/{doc_id}"

    return {
        "download_url": download_url,
        "filename": filename,
        "size_bytes": len(pdf_bytes),
        "message": f"PDF ready. Give this link to the user to download: {download_url}",
    }


@mcp.tool()
def list_repeating_sections(doc_id: str) -> list[dict]:
    """List all repeating sections (dynamic rows) in the form.

    Args:
        doc_id: Document ID from upload_pdf.
    """
    return engine.list_repeating_sections(doc_id)


@mcp.tool()
def add_row(doc_id: str, section_path: str, field_values: dict[str, str]) -> dict:
    """Add a new row to a repeating section.

    Args:
        doc_id: Document ID from upload_pdf.
        section_path: Path from list_repeating_sections.
        field_values: Dict mapping field names to values.
    """
    return engine.add_row(doc_id, section_path, field_values)


@mcp.tool()
def close_pdf(doc_id: str) -> dict:
    """Close an open PDF and free resources.

    Args:
        doc_id: Document ID from upload_pdf.
    """
    engine.close(doc_id)
    if doc_id in _filled_cache:
        del _filled_cache[doc_id]
    return {"message": f"Document {doc_id} closed."}


def main():
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
