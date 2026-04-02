import pytest
from pathlib import Path
from xfa_pdf_mcp.engine import XfaPdfEngine

TEST_PDF = Path("/Users/Yulbin/Downloads/imm5257e.pdf")

@pytest.fixture
def engine():
    return XfaPdfEngine()

def test_open_pdf(engine):
    doc_id = engine.open(TEST_PDF)
    assert doc_id is not None
    assert doc_id in engine.documents

def test_open_non_xfa_raises(engine):
    with pytest.raises(ValueError, match="(No XFA|Cannot open PDF)"):
        engine.open(Path("/dev/null"))

def test_list_fields(engine):
    doc_id = engine.open(TEST_PDF)
    fields = engine.list_fields(doc_id)
    assert len(fields) > 0
    paths = [f["path"] for f in fields]
    assert "form1/Page1/PersonalDetails/Name/FamilyName" in paths
    assert "form1/Page1/PersonalDetails/Name/GivenName" in paths
    assert "type" in fields[0]

def test_get_field_values(engine):
    doc_id = engine.open(TEST_PDF)
    values = engine.get_field_values(doc_id, [
        "form1/Page1/PersonalDetails/Name/FamilyName",
        "form1/Page1/PersonalDetails/Name/GivenName",
    ])
    assert "form1/Page1/PersonalDetails/Name/FamilyName" in values
    assert "form1/Page1/PersonalDetails/Name/GivenName" in values

def test_fill_and_read_back(engine):
    doc_id = engine.open(TEST_PDF)
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

def test_save_pdf(engine, tmp_path):
    doc_id = engine.open(TEST_PDF)
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

def test_close(engine):
    doc_id = engine.open(TEST_PDF)
    engine.close(doc_id)
    assert doc_id not in engine.documents
