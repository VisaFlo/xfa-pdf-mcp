# xfa-pdf-mcp

MCP server for filling XFA-PDF form fields. Built for IRCC immigration forms (IMM series) but works with any XFA-PDF.

## How It Works

XFA-PDFs embed form data as XML inside the PDF. This server:

1. Opens the PDF with `pikepdf`
2. Extracts the `datasets` XML stream
3. Parses it with `lxml` to discover and modify field values
4. Writes the modified XML back and saves

No Adobe Acrobat needed for filling. The output PDF must be opened in Adobe Reader/Acrobat to render correctly (browsers don't support XFA).

## Installation

```bash
cd /path/to/xfa-pdf-mcp
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Claude Desktop Configuration

Add to your `claude_desktop_config.json`:

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

## Claude Code Configuration

Add to your `.claude/settings.json` or project's `.mcp.json`:

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

## Available Tools

| Tool | Description |
|------|-------------|
| `open_pdf` | Open an XFA-PDF, returns doc_id and field count |
| `list_fields` | List all fillable fields with paths, types, current values |
| `get_field_values` | Get values for specific field paths |
| `fill_fields` | Batch-fill fields by path-to-value mapping |
| `save_pdf` | Save the filled PDF to a new file |
| `close_pdf` | Close and free resources |

## Workflow

```
open_pdf → list_fields → fill_fields → save_pdf → close_pdf
```

## Field Paths

Fields use XFA template paths. Examples from IMM5257 (TRV Application):

```
form1/Page1/PersonalDetails/Name/FamilyName
form1/Page1/PersonalDetails/Name/GivenName
form1/Page1/PersonalDetails/Sex/Sex
form1/Page1/PersonalDetails/DOBYear
form1/Page1/PersonalDetails/DOBMonth
form1/Page1/PersonalDetails/DOBDay
form1/Page1/PersonalDetails/PlaceBirthCity
form1/Page1/PersonalDetails/PlaceBirthCountry
form1/Page1/PersonalDetails/Citizenship/Citizenship
```

## Field Types

- `textEdit` — Free text input
- `choiceList` — Dropdown (use LOV code values)
- `checkButton` — Checkbox
- `dateTimeEdit` — Date picker
- `numericEdit` — Number input

## Example Usage (via Claude)

> "Open the IMM5257 form at ~/Downloads/imm5257e.pdf, list all the personal details fields, then fill in: FamilyName=KIM, GivenName=YULBIN, DOBYear=1990, DOBMonth=01, DOBDay=15. Save to ~/Desktop/imm5257_filled.pdf"

## Testing

```bash
.venv/bin/pytest tests/ -v
```

## Tested Forms

- IMM5257e — TRV Application
- IMM1294e — Study Permit
- IMM5707e — Family Information
- IMM1295e — Work Permit
- IMM5476e — Use of Representative

## Limitations

- Output PDFs must be opened in Adobe Reader/Acrobat (not Chrome, Preview, or Firefox)
- Fields with `bind="none"` in the template cannot be filled via datasets
- Embedded JavaScript validation only runs in Adobe Reader
- Reader Extensions signatures may be invalidated on save
