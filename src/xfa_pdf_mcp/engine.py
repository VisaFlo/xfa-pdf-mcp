"""Core XFA-PDF manipulation engine using pikepdf + lxml."""

import re
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

import pikepdf
from lxml import etree

XFA_DATA_NS = "http://www.xfa.org/schema/xfa-data/1.0/"
XFA_TEMPLATE_NS = "http://www.xfa.org/schema/xfa-template/2.8/"
TEMPLATE_NS_PREFIXES = [
    "http://www.xfa.org/schema/xfa-template/2.8/",
    "http://www.xfa.org/schema/xfa-template/3.0/",
    "http://www.xfa.org/schema/xfa-template/3.3/",
]


@dataclass
class FieldMeta:
    """Metadata for a single form field, extracted from the template."""
    path: str
    field_type: str
    items: list[str]  # for checkButton: [on_value] or [on, off, neutral]
    options: list[tuple[str, str]] = field(default_factory=list)  # for choiceList: [(code, label), ...]
    format_pattern: str = ""  # for dateTimeEdit/picture: the expected format pattern


@dataclass
class OpenDocument:
    """Represents an open XFA-PDF in memory."""
    pdf: pikepdf.Pdf
    source_path: Path
    xfa_array: Any
    datasets_index: int
    datasets_root: Any
    data_node: Any
    template_ns: str
    template_root: Any
    field_meta: dict[str, FieldMeta] = field(default_factory=dict)
    lov_data: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # LOV name -> [(code, label), ...]


class XfaPdfEngine:
    """Stateful engine for opening, reading, filling, and saving XFA-PDFs."""

    def __init__(self):
        self.documents: dict[str, OpenDocument] = {}

    def open(self, path: Path) -> str:
        """Open an XFA-PDF and return a document ID."""
        path = Path(path)
        try:
            pdf = pikepdf.Pdf.open(path)
        except Exception as e:
            raise ValueError(f"Cannot open PDF: {e}")

        acroform = pdf.Root.get("/AcroForm")
        if not acroform:
            raise ValueError("No XFA: PDF has no AcroForm")

        xfa = acroform.get("/XFA")
        if not xfa:
            raise ValueError("No XFA: PDF has AcroForm but no XFA data")

        if not isinstance(xfa, pikepdf.Array):
            raise ValueError("No XFA: unexpected XFA format (not an array)")

        datasets_index = None
        datasets_root = None
        template_root = None
        template_ns = None

        for i in range(0, len(xfa), 2):
            key = str(xfa[i])
            if key == "datasets":
                datasets_index = i + 1
                xml_bytes = bytes(xfa[i + 1].read_bytes())
                datasets_root = etree.fromstring(xml_bytes)
            elif key == "template":
                tmpl_bytes = bytes(xfa[i + 1].read_bytes())
                template_root = etree.fromstring(tmpl_bytes)
                root_ns = template_root.tag.split("}")[0].lstrip("{") if "}" in template_root.tag else ""
                if root_ns:
                    template_ns = root_ns
                else:
                    for ns in TEMPLATE_NS_PREFIXES:
                        if template_root.findall(f".//{{{ns}}}field"):
                            template_ns = ns
                            break

        if datasets_index is None or datasets_root is None:
            raise ValueError("No XFA: datasets section not found")

        ns = {"xfa": XFA_DATA_NS}
        data_node = datasets_root.find(".//xfa:data", ns)
        if data_node is None:
            raise ValueError("No XFA: xfa:data node not found in datasets")

        doc_id = str(uuid.uuid4())[:8]
        detected_ns = template_ns or TEMPLATE_NS_PREFIXES[0]
        doc = OpenDocument(
            pdf=pdf,
            source_path=path,
            xfa_array=xfa,
            datasets_index=datasets_index,
            datasets_root=datasets_root,
            data_node=data_node,
            template_ns=detected_ns,
            template_root=template_root,
        )
        # Extract LOV (List of Values) from datasets for dropdown lookups
        doc.lov_data = self._extract_lov(datasets_root)
        # Build field metadata cache from template
        doc.field_meta = self._build_field_meta(template_root, detected_ns, doc.lov_data)
        self.documents[doc_id] = doc
        return doc_id

    def _extract_lov(self, datasets_root) -> dict[str, list[tuple[str, str]]]:
        """Extract LOV (List of Values) data from datasets XML.

        Returns dict mapping LOV list name to [(code, label), ...] pairs.
        """
        lov_data = {}
        lov_file = datasets_root.find("LOVFile")
        if lov_file is None:
            return lov_data
        lov_elem = lov_file.find("LOV")
        if lov_elem is None:
            return lov_data

        for lov_list in lov_elem:
            list_name = lov_list.tag
            options = []
            for item in lov_list:
                code = item.get("lic", "")
                label = item.text or ""
                if code:  # skip empty entries
                    options.append((code, label))
                # Handle nested LOVs (e.g. CityList has cities nested under provinces)
                if len(list(item)) > 0:
                    for nested in item:
                        n_code = nested.get("lic", "")
                        n_label = nested.text or ""
                        if n_code:
                            options.append((n_code, n_label))
            if options:
                lov_data[list_name] = options
        return lov_data

    def _build_field_meta(self, template_root, ns_t: str, lov_data: dict) -> dict[str, FieldMeta]:
        """Extract field metadata (type, items/options) from the XFA template."""
        meta = {}
        for field_elem in template_root.iter(f"{{{ns_t}}}field"):
            name = field_elem.get("name")
            if not name:
                continue

            path_parts = []
            parent = field_elem.getparent()
            while parent is not None:
                pname = parent.get("name", "")
                if pname:
                    path_parts.insert(0, pname)
                parent = parent.getparent()
            full_path = "/".join(path_parts) + "/" + name

            ui = field_elem.find(f"{{{ns_t}}}ui")
            field_type = "textEdit"
            if ui is not None:
                for child in ui:
                    field_type = etree.QName(child.tag).localname
                    break

            # Extract items for checkButtons
            items = []
            # Extract options for choiceLists
            options = []

            items_elems = field_elem.findall(f"{{{ns_t}}}items")

            if field_type == "checkButton":
                for items_elem in items_elems:
                    for item in items_elem:
                        if item.text:
                            items.append(item.text)
            elif field_type == "choiceList":
                # choiceList can have inline items or be LOV-driven
                labels = []
                codes = []
                for items_elem in items_elems:
                    save = items_elem.get("save", "")
                    item_texts = [item.text or "" for item in items_elem]
                    if save == "1":
                        codes = item_texts  # save=1 items are the stored codes
                    else:
                        labels = item_texts  # display labels

                if codes and labels and len(codes) == len(labels):
                    # Inline items with both labels and codes
                    options = list(zip(codes, labels))
                elif not codes and not labels:
                    # LOV-driven — try to match field name to a LOV list
                    options = self._match_lov(name, lov_data)

            # Extract format pattern for date/picture fields
            format_pattern = ""
            if field_type in ("dateTimeEdit", "picture"):
                pics = field_elem.findall(f".//{{{ns_t}}}picture")
                for pic in pics:
                    if pic.text and "date{" in pic.text:
                        format_pattern = "YYYY-MM-DD"
                        break
                    elif pic.text and "num{" in pic.text:
                        format_pattern = pic.text
                        break
                    elif pic.text and "text{" in pic.text:
                        format_pattern = pic.text
                        break

            meta[full_path] = FieldMeta(
                path=full_path,
                field_type=field_type,
                items=items,
                options=options,
                format_pattern=format_pattern,
            )
        return meta

    def _match_lov(self, field_name: str, lov_data: dict) -> list[tuple[str, str]]:
        """Try to match a choiceList field name to a LOV list by convention.

        IRCC forms use naming conventions like:
        - Field "Country" -> LOV "CountryList"
        - Field "PlaceBirthCountry" -> LOV "CountryOfBirthList"
        - Field "Citizenship" -> LOV "CountryOfCitizenshipList"
        - Field "MaritalStatus" -> LOV "MaritalStatusList"
        - Field "Sex" -> LOV "GenderMelList"
        """
        fn = field_name.lower()

        # Direct match: FieldName + "List"
        for lov_name, opts in lov_data.items():
            if lov_name.lower() == fn + "list":
                return opts

        # Common IRCC field-to-LOV mappings
        mappings = {
            "placebirthcountry": "CountryOfBirthList",
            "citizenship": "CountryOfCitizenshipList",
            "countryofissue": "CountryOfIssueList",
            "country": "CountryList",
            "sex": "GenderMelList",
            "maritalstatus": "MaritalStatusList",
            "typeofrelationship": "MaritalStatusHistoryList",
            "nativelang": "ContactLanguageList",
            "abletocommunicate": "AbleCommunicateEnglishOrFrenchList",
            "workpermittype": "WorkPermitTypeList",
            "lov": "PreferenceLanguageList",
            # Unresolved fields from form analysis
            "servicein": "OfficialLanguageList",
            "status": "ImmigrationStatusList",
            "type": "PhoneTypeTRVList",
            "purposeofvisit": "VisitPurposeList",
            "program": "ApplyingProgramList",
            "level": "LevelOfStudyList",
            "expensespaidby": "ExpensesPaidBySPList",
        }

        # Province/State fields are cascade-dependent on country.
        # Merge all province/state LOV lists so any region can be resolved.
        if fn in ("provincestate", "provstate", "prov"):
            combined = []
            seen_codes = set()
            for lov_name in ("ProvinceAbbrevList", "StateAbbrevList"):
                for code, label in lov_data.get(lov_name, []):
                    if code not in seen_codes:
                        combined.append((code, label))
                        seen_codes.add(code)
            return combined

        # City fields are cascade-dependent on province.
        # CityList is already flattened by _extract_lov (nested items merged).
        if fn == "citytown" and "CityList" in lov_data:
            return lov_data["CityList"]

        mapped_lov = mappings.get(fn)
        if mapped_lov and mapped_lov in lov_data:
            return lov_data[mapped_lov]

        return []

    def _get_doc(self, doc_id: str) -> OpenDocument:
        if doc_id not in self.documents:
            raise ValueError(f"Document {doc_id} not found. Open it first.")
        return self.documents[doc_id]

    def list_fields(self, doc_id: str) -> list[dict]:
        """List all fillable fields with their XFA paths, types, and valid values."""
        doc = self._get_doc(doc_id)
        fields = []

        for path, fm in doc.field_meta.items():
            current_value = self._get_value_at_path(doc, path)
            entry = {
                "path": path,
                "type": fm.field_type,
                "value": current_value or "",
            }
            if fm.items:
                entry["items"] = fm.items
            if fm.options:
                # Show options as code=label pairs (limit to 20 for large LOVs)
                if len(fm.options) <= 20:
                    entry["options"] = [{"code": c, "label": l} for c, l in fm.options]
                else:
                    entry["options_count"] = len(fm.options)
                    entry["options_sample"] = [{"code": c, "label": l} for c, l in fm.options[:10]]
                    entry["options_hint"] = "Use label or code. Call get_field_values for full list."
            if fm.format_pattern:
                entry["format"] = fm.format_pattern
            fields.append(entry)

        return fields

    def _get_value_at_path(self, doc: OpenDocument, path: str) -> str | None:
        """Navigate the data XML tree to find a value at the given path."""
        parts = path.split("/")
        node = doc.data_node
        for part in parts:
            found = None
            for child in node:
                tag = etree.QName(child.tag).localname if "}" in child.tag else child.tag
                if tag == part:
                    found = child
                    break
            if found is None:
                return None
            node = found
        return node.text if node is not None else None

    def _set_value_at_path(self, doc: OpenDocument, path: str, value: str) -> bool:
        """Set a value in the data XML tree, creating nodes as needed."""
        parts = path.split("/")
        node = doc.data_node
        for i, part in enumerate(parts):
            found = None
            for child in node:
                tag = etree.QName(child.tag).localname if "}" in child.tag else child.tag
                if tag == part:
                    found = child
                    break
            if found is None:
                found = etree.SubElement(node, part)
            node = found

        node.text = value
        return True

    def get_field_values(self, doc_id: str, paths: list[str]) -> dict[str, str | None]:
        """Get current values for specified field paths."""
        doc = self._get_doc(doc_id)
        result = {}
        for path in paths:
            result[path] = self._get_value_at_path(doc, path)
        return result

    def _resolve_checkbox_value(self, doc: OpenDocument, path: str, value: str) -> str:
        """Resolve a checkbox value to the correct template item value.

        Accepts: true/false/checked/unchecked/on/off/yes/no/1/0
        Returns: the actual item value from the template (e.g. "Y", "N", "1", "0")
        """
        fm = doc.field_meta.get(path)
        if not fm or fm.field_type != "checkButton" or not fm.items:
            return value

        # Normalize input
        v = value.strip().lower()
        is_on = v in ("true", "checked", "on", "yes", "1", "y")
        is_off = v in ("false", "unchecked", "off", "no", "0", "n")

        if not is_on and not is_off:
            # Not a boolean-like value — pass through as-is (might be the actual item value)
            return value

        # items layout: [on_value] or [on_value, off_value] or [on, off, neutral]
        if is_on:
            return fm.items[0]  # first item is always the "on" value
        else:
            if len(fm.items) >= 2:
                return fm.items[1]  # second item is "off"
            return ""  # no off value defined — clear it

    def _resolve_choicelist_value(self, doc: OpenDocument, path: str, value: str) -> str:
        """Resolve a choiceList value — if a label is given, convert to the code.

        Accepts: code directly (e.g. "511"), label (e.g. "Canada"), or
                 case-insensitive partial match (e.g. "canada").
        Returns: the code value to store in the XML.
        """
        fm = doc.field_meta.get(path)
        if not fm or fm.field_type != "choiceList" or not fm.options:
            return value

        # Check if value is already a valid code
        for code, label in fm.options:
            if code == value:
                return value

        # Try exact label match (case-insensitive)
        v_lower = value.strip().lower()
        for code, label in fm.options:
            if label.strip().lower() == v_lower:
                return code

        # Try partial/contains match
        for code, label in fm.options:
            if v_lower in label.strip().lower():
                return code

        # No match — return as-is (might be a valid code the LOV doesn't have)
        return value

    def _normalize_date(self, doc: OpenDocument, path: str, value: str) -> str:
        """Normalize date values to YYYY-MM-DD format for dateTimeEdit/picture fields.

        Accepts: YYYY-MM-DD, MM/DD/YYYY, DD/MM/YYYY, YYYY/MM/DD, YYYYMMDD,
                 Month DD YYYY, etc.
        Returns: YYYY-MM-DD formatted string, or original value if not a date field.
        """
        fm = doc.field_meta.get(path)
        if not fm or fm.format_pattern != "YYYY-MM-DD":
            return value

        # Already in correct format
        if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            return value

        # YYYYMMDD
        if re.match(r"^\d{8}$", value):
            return f"{value[:4]}-{value[4:6]}-{value[6:8]}"

        # YYYY/MM/DD
        m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", value)
        if m:
            return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"

        # MM/DD/YYYY or DD/MM/YYYY — assume MM/DD/YYYY (North American convention)
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", value)
        if m:
            return f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"

        # Month DD, YYYY (e.g. "January 15, 2025")
        months = {
            "january": "01", "february": "02", "march": "03", "april": "04",
            "may": "05", "june": "06", "july": "07", "august": "08",
            "september": "09", "october": "10", "november": "11", "december": "12",
        }
        m = re.match(r"^(\w+)\s+(\d{1,2}),?\s+(\d{4})$", value.strip())
        if m and m.group(1).lower() in months:
            return f"{m.group(3)}-{months[m.group(1).lower()]}-{m.group(2).zfill(2)}"

        return value

    def fill_fields(self, doc_id: str, field_values: dict[str, str]) -> dict[str, bool]:
        """Fill multiple fields. Returns dict of path -> success.

        Auto-resolves values based on field type:
        - checkButton: true/false/yes/no -> correct template item value
        - choiceList: display labels -> LOV code values
        - dateTimeEdit/picture: various date formats -> YYYY-MM-DD
        """
        doc = self._get_doc(doc_id)
        results = {}
        for path, value in field_values.items():
            resolved = self._resolve_checkbox_value(doc, path, value)
            resolved = self._resolve_choicelist_value(doc, path, resolved)
            resolved = self._normalize_date(doc, path, resolved)
            results[path] = self._set_value_at_path(doc, path, resolved)
        return results

    def _strip_signature_fields(self, fields) -> None:
        """Recursively remove /V from signature fields."""
        for field in fields:
            ft = str(field.get("/FT", ""))
            if ft == "/Sig" and "/V" in field:
                del field["/V"]
            kids = field.get("/Kids", [])
            if kids:
                self._strip_signature_fields(kids)

    def save(self, doc_id: str, output_path: Path) -> Path:
        """Write modified datasets back to the PDF and save."""
        doc = self._get_doc(doc_id)
        output_path = Path(output_path)

        modified_xml = etree.tostring(
            doc.datasets_root, xml_declaration=False, encoding="unicode"
        ).encode("utf-8")
        doc.xfa_array[doc.datasets_index].write(modified_xml)

        # Remove all certification/signature data to avoid
        # "certification is invalid" warnings in Adobe Reader.
        if "/Perms" in doc.pdf.Root:
            del doc.pdf.Root["/Perms"]
        if "/DSS" in doc.pdf.Root:
            del doc.pdf.Root["/DSS"]
        acroform = doc.pdf.Root.get("/AcroForm")
        if acroform:
            if "/SigFlags" in acroform:
                del acroform["/SigFlags"]
            # Remove signature field values from form fields
            self._strip_signature_fields(acroform.get("/Fields", []))

        doc.pdf.save(output_path)
        return output_path

    def close(self, doc_id: str) -> None:
        """Close document and free resources."""
        if doc_id in self.documents:
            doc = self.documents[doc_id]
            doc.pdf.close()
            del self.documents[doc_id]
