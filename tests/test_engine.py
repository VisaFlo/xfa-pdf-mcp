"""Unit tests for the XFA-PDF engine."""

import pytest
from xfa_pdf_mcp.engine import XfaPdfEngine
from pathlib import Path
from tests.conftest import get_test_pdf


@pytest.fixture
def engine():
    e = XfaPdfEngine()
    yield e
    for doc_id in list(e.documents.keys()):
        e.close(doc_id)


@pytest.fixture
def test_pdf():
    return get_test_pdf()


def test_open_pdf(engine, test_pdf):
    doc_id = engine.open(test_pdf)
    assert doc_id is not None
    assert doc_id in engine.documents


def test_open_non_xfa_raises(engine):
    with pytest.raises(ValueError, match="(No XFA|Cannot open PDF)"):
        engine.open(Path("/dev/null"))


def test_list_fields(engine, test_pdf):
    doc_id = engine.open(test_pdf)
    fields = engine.list_fields(doc_id)
    assert len(fields) > 0
    assert "path" in fields[0]
    assert "type" in fields[0]


def test_list_fields_has_known_paths(engine, test_pdf):
    """IMM5257 should have standard personal details fields."""
    doc_id = engine.open(test_pdf)
    fields = engine.list_fields(doc_id)
    paths = [f["path"] for f in fields]
    assert "form1/Page1/PersonalDetails/Name/FamilyName" in paths
    assert "form1/Page1/PersonalDetails/Name/GivenName" in paths


def test_get_field_values(engine, test_pdf):
    doc_id = engine.open(test_pdf)
    values = engine.get_field_values(doc_id, [
        "form1/Page1/PersonalDetails/Name/FamilyName",
        "form1/Page1/PersonalDetails/Name/GivenName",
    ])
    assert "form1/Page1/PersonalDetails/Name/FamilyName" in values
    assert "form1/Page1/PersonalDetails/Name/GivenName" in values


def test_fill_and_read_back(engine, test_pdf):
    doc_id = engine.open(test_pdf)
    engine.fill_fields(doc_id, {
        "form1/Page1/PersonalDetails/Name/FamilyName": "TESTFAMILY",
        "form1/Page1/PersonalDetails/Name/GivenName": "TESTGIVEN",
    })
    values = engine.get_field_values(doc_id, [
        "form1/Page1/PersonalDetails/Name/FamilyName",
        "form1/Page1/PersonalDetails/Name/GivenName",
    ])
    assert values["form1/Page1/PersonalDetails/Name/FamilyName"] == "TESTFAMILY"
    assert values["form1/Page1/PersonalDetails/Name/GivenName"] == "TESTGIVEN"


def test_checkbox_auto_resolve(engine, test_pdf):
    """Checkboxes should auto-resolve boolean-like values."""
    doc_id = engine.open(test_pdf)
    engine.fill_fields(doc_id, {
        "form1/Page1/PersonalDetails/PCRIndicator/No": "true",
    })
    values = engine.get_field_values(doc_id, [
        "form1/Page1/PersonalDetails/PCRIndicator/No",
    ])
    # "true" should resolve to the template's on value (e.g. "N" for a "No" checkbox)
    assert values["form1/Page1/PersonalDetails/PCRIndicator/No"] != "true"
    assert values["form1/Page1/PersonalDetails/PCRIndicator/No"] != ""


def test_choicelist_label_resolve(engine, test_pdf):
    """Dropdowns should resolve display labels to codes."""
    doc_id = engine.open(test_pdf)
    engine.fill_fields(doc_id, {
        "form1/Page1/MaritalStatus/SectionA/MaritalStatus": "Married",
    })
    values = engine.get_field_values(doc_id, [
        "form1/Page1/MaritalStatus/SectionA/MaritalStatus",
    ])
    assert values["form1/Page1/MaritalStatus/SectionA/MaritalStatus"] == "01"


def test_date_normalization(engine, test_pdf):
    """Date fields should normalize various formats to YYYY-MM-DD."""
    doc_id = engine.open(test_pdf)
    engine.fill_fields(doc_id, {
        "form1/Page1/PersonalDetails/CurrentCOR/Row2/FromDate": "01/15/2025",
    })
    values = engine.get_field_values(doc_id, [
        "form1/Page1/PersonalDetails/CurrentCOR/Row2/FromDate",
    ])
    assert values["form1/Page1/PersonalDetails/CurrentCOR/Row2/FromDate"] == "2025-01-15"


def test_save_and_reopen(engine, test_pdf, tmp_path):
    """Saving produces a valid PDF with filled values that survive roundtrip."""
    doc_id = engine.open(test_pdf)
    engine.fill_fields(doc_id, {
        "form1/Page1/PersonalDetails/Name/FamilyName": "SAVEFAMILY",
    })
    output = tmp_path / "filled.pdf"
    engine.save(doc_id, output)
    assert output.exists()
    assert output.stat().st_size > 0

    doc_id2 = engine.open(output)
    values = engine.get_field_values(doc_id2, [
        "form1/Page1/PersonalDetails/Name/FamilyName",
    ])
    assert values["form1/Page1/PersonalDetails/Name/FamilyName"] == "SAVEFAMILY"


def test_close(engine, test_pdf):
    doc_id = engine.open(test_pdf)
    engine.close(doc_id)
    assert doc_id not in engine.documents
