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

    # Roundtrip
    doc_id2 = engine.open_bytes(result_bytes, "roundtrip.pdf")
    vals = engine.get_field_values(doc_id2, [
        "form1/Page1/PersonalDetails/Name/FamilyName",
    ])
    assert vals["form1/Page1/PersonalDetails/Name/FamilyName"] == "BYTESTEST"


def test_open_bytes_non_xfa_raises(engine):
    with pytest.raises(ValueError):
        engine.open_bytes(b"not a pdf", "bad.pdf")
