# xfa-pdf-mcp

An MCP (Model Context Protocol) server for reading and filling XFA-PDF form fields. Built for Canadian immigration forms (IRCC IMM series) but works with any XFA-PDF.

Tested against **95+ IRCC immigration forms** with 100% roundtrip success.

## Quick Start (Hosted)

No installation required. Connect to the hosted MCP server with one step.

### ChatGPT

1. Go to **Settings** > **Developer Mode** (enable if needed)
2. Go to **Connectors** > **Add MCP Server**
3. Enter URL: `https://xfa-pdf-mcp.vflo.app/mcp`
4. Name it "XFA PDF Filler" and save

Then ask ChatGPT: *"Upload imm5257e.pdf and fill in FamilyName=KIM, GivenName=YULBIN"*

### Claude Code

```bash
claude mcp add --transport http xfa-pdf https://xfa-pdf-mcp.vflo.app/mcp
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "xfa-pdf": {
      "type": "streamable-http",
      "url": "https://xfa-pdf-mcp.vflo.app/mcp"
    }
  }
}
```

### OpenAI Agents SDK / Responses API

```python
# Connect as an MCP tool source
tool = {"type": "mcp", "server_url": "https://xfa-pdf-mcp.vflo.app/mcp"}
```

## Self-Hosted Setup

### Option 1: Docker (Recommended)

```bash
docker run -p 8080:8080 ghcr.io/visaflo/xfa-pdf-mcp
```

Then connect Claude:

```bash
claude mcp add --transport http xfa-pdf http://localhost:8080/mcp
```

### Option 2: Local (stdio)

Requires Python 3.11+.

```bash
git clone https://github.com/VisaFlo/xfa-pdf-mcp.git
cd xfa-pdf-mcp
python3 -m venv .venv
.venv/bin/pip install -e .
```

```bash
claude mcp add --transport stdio --scope user xfa-pdf-mcp \
  /path/to/xfa-pdf-mcp/.venv/bin/python -- -m xfa_pdf_mcp.server
```

## How It Works

XFA-PDFs embed form definitions and data as XML inside a PDF container. This server:

1. Opens the PDF with `pikepdf`
2. Extracts the XFA `datasets` XML (form data) and `template` XML (field definitions)
3. Builds a metadata cache of all fields, including LOV (List of Values) dropdown options
4. Fills fields by modifying the datasets XML, auto-resolving labels to codes
5. Writes the modified XML back and saves a new PDF

No Adobe Acrobat needed for filling. Output PDFs must be opened in Adobe Reader/Acrobat to render correctly (browsers don't support XFA).

## Tools

| Tool | Description |
|------|-------------|
| `upload_pdf` | Upload a PDF via URL or base64. URL is preferred for large files. |
| `list_fields` | List all fillable fields with paths, types, values, and dropdown options |
| `get_field_values` | Get current values for specific field paths |
| `fill_fields` | Batch-fill fields with auto-resolution of labels, checkboxes, and dates |
| `download_pdf` | Download the filled PDF as base64 |
| `close_pdf` | Close and free resources |
| `list_repeating_sections` | List dynamic row sections (dependants, children, employment, etc.) |
| `add_row` | Add a new row to a repeating section |

> **Upload options:** `upload_pdf` accepts either `pdf_url` (HTTP/HTTPS link to the PDF) or `pdf_base64` (inline base64). URL is recommended for large files to avoid payload size limits.
>
> For local stdio mode, `upload_pdf`/`download_pdf` are replaced by `open_pdf` (file path) / `save_pdf` (file path).

## Workflow

```
upload_pdf -> list_fields -> fill_fields -> download_pdf -> close_pdf
```

For forms with dynamic rows:

```
upload_pdf -> list_repeating_sections -> add_row (repeat) -> download_pdf -> close_pdf
```

## Smart Value Resolution

The server automatically resolves human-readable values to the correct form codes.

### Dropdowns (choiceList)

Pass display labels instead of codes. The engine extracts LOV (List of Values) from the form's datasets XML and resolves automatically.

```
"Canada"       -> "511"
"Married"      -> "01"
"Korea, South" -> "258"
"BC"           -> "11"
"Vancouver"    -> "8634"
```

Cascade-dependent dropdowns (province depends on country, city depends on province) are handled by merging all LOV lists.

### Checkboxes (checkButton)

Pass boolean-like values. The engine reads the correct on/off values from the template.

```
"true"  -> "Y" (or "N", "1", etc. depending on the field)
"false" -> "" (or "0", etc.)
```

Accepted inputs: `true`/`false`, `yes`/`no`, `checked`/`unchecked`, `on`/`off`, `1`/`0`

### Dates (dateTimeEdit / picture)

Pass dates in any common format. The engine normalizes to `YYYY-MM-DD`.

```
"01/15/2025"         -> "2025-01-15"
"20250115"           -> "2025-01-15"
"January 15, 2025"   -> "2025-01-15"
"2025/01/15"         -> "2025-01-15"
```

## Dynamic Rows

Some forms have repeating sections (dependants, children, employment history, etc.) where rows can be dynamically added:

```python
# List available repeating sections
sections = list_repeating_sections(doc_id)
# -> [{"path": "IMM_5707/page1/SectionB/Child", "max": -1, "current_count": 4, ...}]

# Add a new row
add_row(doc_id, "IMM_5707/page1/SectionB/Child", {
    "FamilyName": "SIMPSON",
    "GivenName": "BART",
    "Relationship": "Son",
    "DOBYear": "2005",
})
```

## Example

> "Open the IMM5257 form at ~/Downloads/imm5257e.pdf, list the personal details fields, fill in: FamilyName=KIM, GivenName=YULBIN, Sex=Male, PlaceBirthCountry=Korea South, Citizenship=Korea South, DOBYear=1990, DOBMonth=01, DOBDay=15. Save to ~/Desktop/imm5257_filled.pdf"

## Field Types

| Type | Description |
|------|-------------|
| `textEdit` | Free text input |
| `choiceList` | Dropdown with LOV options (auto-resolved) |
| `checkButton` | Checkbox (auto-resolved from true/false) |
| `dateTimeEdit` | Date picker (auto-normalized to YYYY-MM-DD) |
| `numericEdit` | Number input |
| `picture` | Masked input (dates, postal codes) |
| `barcode` | Auto-generated barcode (read-only) |
| `signature` | Signature field |

## Testing

```bash
# All tests (42 total)
.venv/bin/pytest tests/ -v

# Unit tests only
.venv/bin/pytest tests/test_engine.py tests/test_engine_bytes.py -v

# Integration tests (require IMM PDFs in ~/Downloads/)
.venv/bin/pytest tests/test_integration.py -v
```

## Tested Forms

Verified with 95+ IRCC immigration forms including:

| Form | Description |
|------|-------------|
| IMM 0008 | Generic Application Form for Canada |
| IMM 1283 | Financial Evaluation |
| IMM 1294 | Application for Study Permit |
| IMM 1295 | Application for Work Permit |
| IMM 1344 | Application for Sponsorship |
| IMM 5257 | Application for Temporary Resident Visa |
| IMM 5406 | Additional Family Information |
| IMM 5409 | Statutory Declaration of Common-law Union |
| IMM 5476 | Use of a Representative |
| IMM 5490 | Sponsorship Agreement and Undertaking |
| IMM 5532 | Relationship Information |
| IMM 5562 | Supplementary Information - Your Travels |
| IMM 5645 | Family Information |
| IMM 5707 | Family Information (IMM 5707) |
| IMM 5708 | Application for Visitor Permit |
| IMM 5709 | Application for Study Permit (in-Canada) |
| IMM 5710 | Application for Work Permit (in-Canada) |
| IMM 5768 | Financial Evaluation for Super Visa |

## Architecture

```
                     +------------------+
                     |   XfaPdfEngine   |
                     |  (pikepdf+lxml)  |
                     +--------+---------+
                              |
              +---------------+---------------+
              |                               |
    +---------v----------+        +-----------v-----------+
    |  Local MCP Server  |        |  Remote MCP Server    |
    |  (stdio)           |        |  (streamable-http)    |
    |  server.py         |        |  server_remote.py     |
    +--------------------+        +-----------------------+
              |                               |
    Claude Code/Desktop           ChatGPT, Claude Desktop,
    (local install)               Claude Code, OpenAI Agents
                                  (hosted — no install)
```

## Deployment

### Google Cloud Run

```bash
gcloud run deploy xfa-pdf-mcp --source . --region us-central1 --allow-unauthenticated
```

### Docker

```bash
docker build -t xfa-pdf-mcp .
docker run -p 8080:8080 xfa-pdf-mcp
```

## Limitations

- Output PDFs must be opened in **Adobe Reader/Acrobat** (not Chrome, Preview, or Firefox)
- Fields with `bind="none"` in the template cannot be filled via datasets
- Embedded JavaScript validation only runs in Adobe Reader
- Digital signatures are stripped on save (required to avoid "certification invalid" warnings)
- A small number of forms (e.g. IMM 5444) have no LOV data; dropdowns require raw code values
- Non-XFA PDFs (AcroForm only) are not supported

## License

MIT
