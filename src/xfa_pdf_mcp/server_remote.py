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
        "Upload PDFs via URL (preferred for large files) or base64. "
        "Download filled PDFs as base64 (small files) or chunked."
    ),
)

engine = XfaPdfEngine()

# Store filled PDF bytes keyed by doc_id for download
_filled_cache: dict[str, tuple[bytes, str, float]] = {}  # doc_id -> (bytes, filename, timestamp)
_CACHE_TTL = 1800  # 30 minutes


def _cleanup_cache():
    """Remove expired entries from the filled PDF cache."""
    now = time.time()
    expired = [k for k, (_, _, ts) in _filled_cache.items() if now - ts > _CACHE_TTL]
    for k in expired:
        del _filled_cache[k]


@mcp.tool()
def upload_pdf(
    pdf_url: str = "",
    pdf_base64: str = "",
    filename: str = "form.pdf",
) -> dict:
    """Upload an XFA-PDF form and get a document ID.

    Provide EITHER a URL to download the PDF from OR base64-encoded bytes.
    URL is preferred for large files (avoids payload size limits).

    Args:
        pdf_url: URL to download the PDF from (preferred). Supports http/https.
        pdf_base64: Base64-encoded PDF bytes (alternative for small files).
        filename: Original filename (for reference).

    Returns:
        Document ID, form name, and field count.
    """
    if pdf_url:
        parsed = urlparse(pdf_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Invalid URL scheme: {parsed.scheme}. Use http or https.")
        try:
            resp = httpx.get(pdf_url, follow_redirects=True, timeout=30)
            resp.raise_for_status()
            pdf_bytes = resp.content
        except httpx.HTTPError as e:
            raise ValueError(f"Failed to download PDF from URL: {e}")
        if not filename or filename == "form.pdf":
            filename = Path(parsed.path).name or "form.pdf"
    elif pdf_base64:
        try:
            pdf_bytes = base64.b64decode(pdf_base64)
        except Exception:
            raise ValueError("Invalid base64 encoding")
    else:
        raise ValueError("Provide either pdf_url or pdf_base64")

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
    return {
        "results": results,
        "message": f"Filled {filled_count}/{len(field_values)} fields.",
    }


@mcp.tool()
def download_pdf(doc_id: str) -> dict:
    """Get the filled PDF as base64.

    Args:
        doc_id: Document ID from upload_pdf.

    Returns:
        Base64-encoded PDF bytes, filename, and size.
    """
    _cleanup_cache()
    doc = engine._get_doc(doc_id)
    pdf_bytes = engine.save_bytes(doc_id)
    filename = f"filled_{doc.source_path.name}"

    return {
        "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        "filename": filename,
        "size_bytes": len(pdf_bytes),
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
