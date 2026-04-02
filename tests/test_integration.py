"""Integration tests against real IRCC immigration forms.

These tests require IMM PDF forms to be present in ~/Downloads/ (or the
directory specified by the XFA_TEST_DOWNLOADS environment variable).
Tests are skipped automatically if the required PDFs are not found.
"""

import pytest
from pathlib import Path
from xfa_pdf_mcp.engine import XfaPdfEngine
from tests.conftest import get_downloads_dir

DOWNLOADS = get_downloads_dir()

FORMS = {
    "imm5257e": DOWNLOADS / "imm5257e.pdf",
    "imm1294e": DOWNLOADS / "imm1294e.pdf",
    "imm5707e": DOWNLOADS / "imm5707e.pdf",
    "imm1295e": DOWNLOADS / "imm1295e.pdf",
    "imm5476e": DOWNLOADS / "imm5476e.pdf",
    "imm0008e_2d": DOWNLOADS / "imm0008e_2d.pdf",
    "imm5490e": DOWNLOADS / "imm5490e.pdf",
    "imm5532e": DOWNLOADS / "imm5532e.pdf",
    "imm5645e": DOWNLOADS / "imm5645e.pdf",
    "imm5406e": DOWNLOADS / "imm5406e-1.pdf",
    "imm5409e": DOWNLOADS / "imm5409e.pdf",
    "imm5562e": DOWNLOADS / "imm5562e.pdf",
    "imm5768e": DOWNLOADS / "imm5768e.pdf",
    "imm5710e": DOWNLOADS / "imm5710e.pdf",
}

# Also try alternate filenames (some downloads have different names)
for key, path in list(FORMS.items()):
    if not path.exists():
        # Try with (1) suffix
        alt = path.parent / f"{path.stem} (1){path.suffix}"
        if alt.exists():
            FORMS[key] = alt

AVAILABLE_FORMS = {k: v for k, v in FORMS.items() if v.exists()}

if not AVAILABLE_FORMS:
    pytest.skip("No IMM PDF forms found in downloads directory", allow_module_level=True)


@pytest.fixture
def engine():
    e = XfaPdfEngine()
    yield e
    for doc_id in list(e.documents.keys()):
        e.close(doc_id)


@pytest.mark.parametrize("form_name,form_path", AVAILABLE_FORMS.items())
def test_open_and_list_fields(engine, form_name, form_path):
    """Each form opens and has discoverable fields."""
    doc_id = engine.open(form_path)
    fields = engine.list_fields(doc_id)
    assert len(fields) > 5, f"{form_name} should have many fields, got {len(fields)}"
    for f in fields:
        assert "path" in f
        assert "type" in f


@pytest.mark.parametrize("form_name,form_path", AVAILABLE_FORMS.items())
def test_fill_and_save_roundtrip(engine, form_name, form_path, tmp_path):
    """Fill a text field, save, reopen, and verify the value persists."""
    doc_id = engine.open(form_path)
    fields = engine.list_fields(doc_id)

    text_fields = [f for f in fields if f["type"] == "textEdit" and "Name" in f["path"]]
    if not text_fields:
        text_fields = [f for f in fields if f["type"] == "textEdit"]

    assert text_fields, f"{form_name} has no text fields"

    test_field = text_fields[0]["path"]
    engine.fill_fields(doc_id, {test_field: "ROUNDTRIP"})

    output = tmp_path / f"{form_name}_filled.pdf"
    engine.save(doc_id, output)
    assert output.stat().st_size > 0

    doc_id2 = engine.open(output)
    val = engine.get_field_values(doc_id2, [test_field])
    assert val[test_field] == "ROUNDTRIP"
