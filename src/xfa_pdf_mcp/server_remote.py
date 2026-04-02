"""Remote MCP server for XFA-PDF form filling (streamable-http transport)."""

import base64
import os
from mcp.server.fastmcp import FastMCP
from xfa_pdf_mcp.engine import XfaPdfEngine

_host = os.environ.get("HOST", "0.0.0.0")
_port = int(os.environ.get("PORT", "8080"))

mcp = FastMCP(
    "xfa-pdf-mcp",
    host=_host,
    port=_port,
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
    return {"message": f"Document {doc_id} closed."}


def main():
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
