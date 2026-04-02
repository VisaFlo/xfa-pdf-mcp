"""REST API for XFA-PDF form filling (for ChatGPT Actions and other LLMs)."""

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
