"""Integration tests against real IRCC immigration forms."""

import pytest
from pathlib import Path
from xfa_pdf_mcp.engine import XfaPdfEngine

DOWNLOADS = Path("/Users/Yulbin/Downloads")

FORMS = {
    "imm5257e": DOWNLOADS / "imm5257e.pdf",
    "imm1294e": DOWNLOADS / "imm1294e (1).pdf",
    "imm5707e": DOWNLOADS / "imm5707e.pdf",
    "imm1295e": DOWNLOADS / "imm1295e.pdf",
    "imm5476e": DOWNLOADS / "imm5476e.pdf",
}

AVAILABLE_FORMS = {k: v for k, v in FORMS.items() if v.exists()}


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
    assert len(fields) > 10, f"{form_name} should have many fields, got {len(fields)}"
    for f in fields:
        assert "path" in f
        assert "type" in f


@pytest.mark.parametrize("form_name,form_path", AVAILABLE_FORMS.items())
def test_fill_and_save(engine, form_name, form_path, tmp_path):
    """Fill a field and save -- output should be a valid PDF."""
    doc_id = engine.open(form_path)
    fields = engine.list_fields(doc_id)

    text_fields = [f for f in fields if f["type"] == "textEdit" and "Name" in f["path"]]
    if not text_fields:
        text_fields = [f for f in fields if f["type"] == "textEdit"]

    if text_fields:
        test_field = text_fields[0]["path"]
        engine.fill_fields(doc_id, {test_field: "MPCTEST"})

        output = tmp_path / f"{form_name}_filled.pdf"
        engine.save(doc_id, output)
        assert output.stat().st_size > 0

        doc_id2 = engine.open(output)
        val = engine.get_field_values(doc_id2, [test_field])
        assert val[test_field] == "MPCTEST"
