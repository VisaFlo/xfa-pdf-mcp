"""MCP server for XFA-PDF form filling."""

from pathlib import Path
from mcp.server.fastmcp import FastMCP
from xfa_pdf_mcp.engine import XfaPdfEngine

mcp = FastMCP(
    "xfa-pdf-mcp",
    instructions=(
        "This server fills XFA-PDF form fields (e.g. IRCC immigration forms). "
        "Workflow: open_pdf -> list_fields -> fill_fields -> save_pdf -> close_pdf. "
        "Fields are addressed by their XFA path (e.g. form1/Page1/PersonalDetails/Name/FamilyName)."
    ),
)

engine = XfaPdfEngine()


@mcp.tool()
def open_pdf(file_path: str) -> dict:
    """Open an XFA-PDF form and return its document ID and basic info.

    Args:
        file_path: Absolute path to the PDF file.

    Returns:
        Document ID, form name, and field count.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    doc_id = engine.open(path)
    fields = engine.list_fields(doc_id)
    return {
        "doc_id": doc_id,
        "file": str(path.name),
        "field_count": len(fields),
        "message": f"Opened {path.name} with {len(fields)} fields. Use list_fields to see them.",
    }


@mcp.tool()
def list_fields(doc_id: str, filter_type: str = "") -> list[dict]:
    """List all fillable fields in an open XFA-PDF.

    Args:
        doc_id: Document ID from open_pdf.
        filter_type: Optional filter by field type (e.g. 'textEdit', 'choiceList', 'checkButton', 'dateTimeEdit').

    Returns:
        List of fields with path, type, and current value.
    """
    fields = engine.list_fields(doc_id)
    if filter_type:
        fields = [f for f in fields if f["type"] == filter_type]
    return fields


@mcp.tool()
def get_field_values(doc_id: str, paths: list[str]) -> dict:
    """Get current values for specific fields.

    Args:
        doc_id: Document ID from open_pdf.
        paths: List of field paths (e.g. ["form1/Page1/PersonalDetails/Name/FamilyName"]).

    Returns:
        Dict mapping each path to its current value (or null if empty).
    """
    return engine.get_field_values(doc_id, paths)


@mcp.tool()
def fill_fields(doc_id: str, field_values: dict[str, str]) -> dict:
    """Fill form fields with values.

    Args:
        doc_id: Document ID from open_pdf.
        field_values: Dict mapping field paths to values.
            Example: {"form1/Page1/PersonalDetails/Name/FamilyName": "SMITH"}

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
def save_pdf(doc_id: str, output_path: str) -> dict:
    """Save the filled PDF to a new file.

    Args:
        doc_id: Document ID from open_pdf.
        output_path: Absolute path for the output PDF.

    Returns:
        Path to the saved file.
    """
    path = engine.save(doc_id, Path(output_path))
    return {
        "saved_to": str(path),
        "message": f"Saved filled PDF to {path}",
    }


@mcp.tool()
def close_pdf(doc_id: str) -> dict:
    """Close an open PDF and free resources.

    Args:
        doc_id: Document ID from open_pdf.

    Returns:
        Confirmation message.
    """
    engine.close(doc_id)
    return {"message": f"Document {doc_id} closed."}


def main():
    mcp.run()


if __name__ == "__main__":
    main()
