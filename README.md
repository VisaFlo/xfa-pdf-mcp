# xfa-pdf-mcp

An MCP (Model Context Protocol) server for reading and filling XFA-PDF form fields. Built for Canadian immigration forms (IRCC IMM series) but works with any XFA-PDF.

Tested against **95+ IRCC immigration forms** with 100% roundtrip success.

## How It Works

XFA-PDFs embed form definitions and data as XML inside a PDF container. This server:

1. Opens the PDF with `pikepdf`
2. Extracts the XFA `datasets` XML (form data) and `template` XML (field definitions)
3. Builds a metadata cache of all fields, including LOV (List of Values) dropdown options
4. Fills fields by modifying the datasets XML, auto-resolving labels to codes
5. Writes the modified XML back and saves a new PDF

No Adobe Acrobat needed for filling. Output PDFs must be opened in Adobe Reader/Acrobat to render correctly (browsers don't support XFA).

## Installation

```bash
git clone https://github.com/VisaFlo/xfa-pdf-mcp.git
cd xfa-pdf-mcp
python3 -m venv .venv
.venv/bin/pip install -e .
```

Requires Python 3.11+.

## Configuration

### Claude Code

```bash
claude mcp add --transport stdio --scope user xfa-pdf-mcp \
  /path/to/xfa-pdf-mcp/.venv/bin/python -- -m xfa_pdf_mcp.server
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "xfa-pdf-mcp": {
      "command": "/path/to/xfa-pdf-mcp/.venv/bin/python",
      "args": ["-m", "xfa_pdf_mcp.server"]
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `open_pdf` | Open an XFA-PDF, returns doc_id and field count |
| `list_fields` | List all fillable fields with paths, types, values, and dropdown options |
| `get_field_values` | Get current values for specific field paths |
| `fill_fields` | Batch-fill fields with auto-resolution of labels, checkboxes, and dates |
| `save_pdf` | Save the filled PDF to a new file |
| `close_pdf` | Close and free resources |
| `list_repeating_sections` | List dynamic row sections (dependants, children, employment, etc.) |
| `add_row` | Add a new row to a repeating section |

## Workflow

```
open_pdf -> list_fields -> fill_fields -> save_pdf -> close_pdf
```

For forms with dynamic rows:

```
open_pdf -> list_repeating_sections -> add_row (repeat) -> save_pdf -> close_pdf
```

## Smart Value Resolution

The server automatically resolves human-readable values to the correct form codes:

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
# -> [{"path": "IMM_5707/page1/SectionB/Child", "max": -1, "current_count": 4, "field_names": [...]}]

# Add a new row
add_row(doc_id, "IMM_5707/page1/SectionB/Child", {
    "FamilyName": "SIMPSON",
    "GivenName": "BART",
    "Relationship": "Son",
    "DOBYear": "2005",
})
```

## Field Paths

Fields use XFA template paths. Examples from IMM5257 (TRV Application):

```
form1/Page1/PersonalDetails/Name/FamilyName
form1/Page1/PersonalDetails/Name/GivenName
form1/Page1/PersonalDetails/Sex/Sex
form1/Page1/PersonalDetails/DOBYear
form1/Page1/PersonalDetails/PlaceBirthCountry
form1/Page1/PersonalDetails/Citizenship/Citizenship
```

## Field Types

| Type | Description |
|------|-------------|
| `textEdit` | Free text input |
| `choiceList` | Dropdown with LOV options |
| `checkButton` | Checkbox with template-defined on/off values |
| `dateTimeEdit` | Date picker (YYYY-MM-DD) |
| `numericEdit` | Number input |
| `picture` | Masked input (dates, postal codes) |
| `barcode` | Auto-generated barcode (read-only) |
| `signature` | Signature field |

## Example

```
"Open the IMM5257 form at ~/Downloads/imm5257e.pdf, list the personal details fields,
fill in: FamilyName=KIM, GivenName=YULBIN, Sex=Male, PlaceBirthCountry=Korea South,
Citizenship=Korea South, DOBYear=1990, DOBMonth=01, DOBDay=15.
Save to ~/Desktop/imm5257_filled.pdf"
```

## Testing

```bash
# Unit tests (require imm5257e.pdf in tests/fixtures/)
.venv/bin/pytest tests/test_engine.py -v

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
pikepdf (PDF I/O) + lxml (XML parsing)
    |
    v
XfaPdfEngine (stateful, keeps docs open in memory)
    |-- open(): extract XFA streams, build field/LOV metadata
    |-- list_fields(): return fields with types, values, options
    |-- fill_fields(): set values with auto-resolution
    |-- add_row(): add repeating section instances
    |-- save(): write XML back, strip signatures
    |-- close(): free resources
    |
    v
FastMCP server (8 tools over stdio)
```

## Limitations

- Output PDFs must be opened in **Adobe Reader/Acrobat** (not Chrome, Preview, or Firefox)
- Fields with `bind="none"` in the template cannot be filled via datasets
- Embedded JavaScript validation only runs in Adobe Reader
- Digital signatures are stripped on save (required to avoid "certification invalid" warnings)
- A small number of forms (e.g. IMM 5444) have no LOV data; dropdowns in those forms require raw code values
- Non-XFA PDFs (AcroForm only) are not supported

## License

MIT
