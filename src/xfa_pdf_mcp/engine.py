"""Core XFA-PDF manipulation engine using pikepdf + lxml."""

import io
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
    excl_group_peers: list[str] = field(default_factory=list)  # paths of mutually exclusive peers


@dataclass
class RepeatingSection:
    """Metadata for a repeating subform in the template."""
    path: str  # template path e.g. "form1/Page1/dependants"
    data_name: str  # the subform name used in datasets e.g. "dependants"
    parent_data_path: str  # parent path in datasets e.g. "form1/Page1"
    max_occur: int  # -1 = unlimited
    field_names: list[str]  # field names within the subform


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
    lov_data: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    repeating_sections: list[RepeatingSection] = field(default_factory=list)


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

        return self._init_document(pdf, path.name)

    def open_bytes(self, pdf_bytes: bytes, filename: str = "upload.pdf") -> str:
        """Open an XFA-PDF from raw bytes and return a document ID."""
        try:
            pdf = pikepdf.Pdf.open(io.BytesIO(pdf_bytes))
        except Exception as e:
            raise ValueError(f"Cannot open PDF: {e}")

        return self._init_document(pdf, filename)

    def _init_document(self, pdf: pikepdf.Pdf, filename: str) -> str:
        """Parse XFA structure from an already-opened pikepdf.Pdf and register it.

        Shared logic used by both open() and open_bytes().
        Returns the new document ID.
        """
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
            source_path=Path(filename),
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
        # Extract repeating section definitions from template
        doc.repeating_sections = self._extract_repeating_sections(template_root, detected_ns)
        self.documents[doc_id] = doc
        return doc_id

    def _extract_repeating_sections(self, template_root, ns_t: str) -> list[RepeatingSection]:
        """Find all repeating subforms (max > 1 or max = -1) in the template."""
        sections = []
        for subform in template_root.iter(f"{{{ns_t}}}subform"):
            occur = subform.find(f"{{{ns_t}}}occur")
            if occur is None:
                continue
            max_val = occur.get("max", "1")
            if max_val in ("0", "1", ""):
                continue

            name = subform.get("name", "")
            if not name:
                continue

            max_int = int(max_val) if max_val != "-1" else -1

            # Build path
            path_parts = []
            parent = subform.getparent()
            while parent is not None:
                pname = parent.get("name", "")
                if pname:
                    path_parts.insert(0, pname)
                parent = parent.getparent()
            full_path = "/".join(path_parts) + "/" + name
            parent_path = "/".join(path_parts)

            # Get field names within this subform
            field_names = []
            for f in subform.findall(f".//{{{ns_t}}}field"):
                fname = f.get("name", "")
                if fname and fname not in ("RemoveButton", "AddButton"):
                    field_names.append(fname)

            sections.append(RepeatingSection(
                path=full_path,
                data_name=name,
                parent_data_path=parent_path,
                max_occur=max_int,
                field_names=field_names,
            ))
        return sections

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

        # Second pass: detect exclusion groups and wire up mutual exclusion
        self._wire_exclusion_groups(template_root, ns_t, meta)
        return meta

    def _wire_exclusion_groups(self, template_root, ns_t: str, meta: dict) -> None:
        """Detect exclGroup elements and paired checkboxes, wire mutual exclusion."""
        # 1. Handle explicit exclGroup elements
        for eg in template_root.iter(f"{{{ns_t}}}exclGroup"):
            member_paths = []
            for child in eg:
                child_name = child.get("name", "")
                if not child_name:
                    continue
                tag = etree.QName(child.tag).localname
                if tag == "field":
                    # Build full path
                    path_parts = []
                    parent = eg.getparent()
                    while parent is not None:
                        pname = parent.get("name", "")
                        if pname:
                            path_parts.insert(0, pname)
                        parent = parent.getparent()
                    eg_name = eg.get("name", "")
                    if eg_name:
                        path_parts.append(eg_name)
                    full_path = "/".join(path_parts) + "/" + child_name
                    if full_path in meta:
                        member_paths.append(full_path)

            # Wire each member to know about its peers
            for path in member_paths:
                meta[path].excl_group_peers = [p for p in member_paths if p != path]

        # 2. Handle CanadaUS/Other pattern (JS-driven mutual exclusion)
        # These are sibling checkButton fields in the same subform that act as radio buttons
        canada_us_pairs = [
            ("CanadaUS", "Other"),
        ]
        for path, fm in meta.items():
            if fm.field_type != "checkButton":
                continue
            field_name = path.split("/")[-1]
            parent_path = "/".join(path.split("/")[:-1])
            for name_a, name_b in canada_us_pairs:
                if field_name == name_a:
                    peer_path = parent_path + "/" + name_b
                    if peer_path in meta and not fm.excl_group_peers:
                        fm.excl_group_peers = [peer_path]
                        meta[peer_path].excl_group_peers = [path]

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
            # Fields discovered from testing 95+ IMM forms
            "servicein": "OfficialLanguageList",
            "status": "ImmigrationStatusList",
            "statusincan": "SponsorStatusInCanadaList",
            "type": "PhoneTypeTRVList",
            "purposeofvisit": "VisitPurposeList",
            "purpose": "VisitPurposeList",
            "program": "ApplyingProgramList",
            "level": "LevelOfStudyList",
            "leveledu": "EducationLevelList",
            "expensespaidby": "ExpensesPaidBySPList",
            "exppaidby": "ExpensesPaidBySPList",
            "relationship": "RelationshipToPAList",
            "rel2sponsor": "RelationshipToSponsorList",
            "relationshiptoapplicant": "RelationshipToPAList",
            "maritalstatusnew": "MaritalStatusList",
            "langpref": "PreferenceLanguageList",
            "communicatelang": "AbleCommunicateEnglishOrFrenchList",
            "communicationlang": "AbleCommunicateEnglishOrFrenchList",
            "freqlang": "ContactLanguageList",
            "correspondencelang": "OfficialLanguageList",
            "interviewlang": "InterviewLanguageList",
            "citizenship1": "CountryOfCitizenshipList",
            "citizenship2": "CountryOfCitizenshipList",
            "citizencountry": "CountryOfCitizenshipList",
            "countryterritory": "CountryList",
            "countrywithcanada": "CountryList",
            "countrywithoutcanada": "CountryList",
            "gender": "GenderMelList",
            "eyecolour": "EyeColorList",
            "typeofdepchildren": "DependantTypeList",
            "category": "ApplyingCategoryList",
            "permittype": "WorkPermitTypeInLandList",
        }

        # Province/State fields are cascade-dependent on country.
        # Merge all province/state LOV lists so any region can be resolved.
        if fn in ("provincestate", "provstate", "prov", "currentprovince"):
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

            # Enforce mutual exclusion for checkboxes in exclusion groups
            fm = doc.field_meta.get(path)
            if fm and fm.field_type == "checkButton" and fm.excl_group_peers:
                # If this checkbox is being turned ON, turn OFF its peers
                v_lower = value.strip().lower()
                is_on = v_lower in ("true", "checked", "on", "yes", "1", "y")
                if is_on or (fm.items and resolved == fm.items[0]):
                    for peer_path in fm.excl_group_peers:
                        peer_fm = doc.field_meta.get(peer_path)
                        if peer_fm and peer_fm.items:
                            off_val = peer_fm.items[1] if len(peer_fm.items) >= 2 else ""
                            self._set_value_at_path(doc, peer_path, off_val)

            # Enforce phone field consistency when CanadaUS/Other is toggled
            field_name = path.split("/")[-1]
            if field_name in ("CanadaUS", "Other") and fm and fm.field_type == "checkButton":
                parent_path = "/".join(path.split("/")[:-1])
                v_lower = value.strip().lower()
                is_on = v_lower in ("true", "checked", "on", "yes", "1", "y")
                if is_on:
                    if field_name == "CanadaUS":
                        # Switching to Canada/US: clear international fields
                        self._set_value_at_path(doc, parent_path + "/IntlNumber/IntlNumber", "")
                        self._set_value_at_path(doc, parent_path + "/NumberCountry", "1")
                    elif field_name == "Other":
                        # Switching to International: clear NA fields
                        self._set_value_at_path(doc, parent_path + "/NANumber/AreaCode", "")
                        self._set_value_at_path(doc, parent_path + "/NANumber/FirstThree", "")
                        self._set_value_at_path(doc, parent_path + "/NANumber/LastFive", "")

        # Post-processing: compute ActualNumber for phone fields
        # Adobe's JS computes this from parts; we must do it manually
        self._sync_phone_actual_numbers(doc, field_values)

        return results

    def _sync_phone_actual_numbers(self, doc: "OpenDocument", field_values: dict[str, str]) -> None:
        """Compute ActualNumber from phone number parts after filling.

        Adobe JS concatenates AreaCode+FirstThree+LastFive (for Canada/US)
        or CountryCode+IntlNumber (for International) into ActualNumber.
        """
        # Find all phone parent paths that were touched
        phone_parents = set()
        for path in field_values:
            parts = path.split("/")
            for i, part in enumerate(parts):
                if part in ("Phone", "AltPhone"):
                    phone_parents.add("/".join(parts[:i + 1]))
                    break

        for parent in phone_parents:
            canada_us = self._get_value_at_path(doc, parent + "/CanadaUS")
            if canada_us == "1":
                # Canada/US: ActualNumber = AreaCode + FirstThree + LastFive
                area = self._get_value_at_path(doc, parent + "/NANumber/AreaCode") or ""
                first = self._get_value_at_path(doc, parent + "/NANumber/FirstThree") or ""
                last = self._get_value_at_path(doc, parent + "/NANumber/LastFive") or ""
                actual = area + first + last
                if actual:
                    self._set_value_at_path(doc, parent + "/ActualNumber", actual)
                    self._set_value_at_path(doc, parent + "/NumberCountry", "1")
            else:
                # International: ActualNumber = IntlNumber (with country code prefix)
                country = self._get_value_at_path(doc, parent + "/NumberCountry") or ""
                intl = self._get_value_at_path(doc, parent + "/IntlNumber/IntlNumber") or ""
                if intl:
                    actual = f"+{country}{intl}" if country else intl
                    self._set_value_at_path(doc, parent + "/ActualNumber", actual)

    def list_repeating_sections(self, doc_id: str) -> list[dict]:
        """List all repeating sections (dynamic rows) in the form."""
        doc = self._get_doc(doc_id)
        result = []
        for rs in doc.repeating_sections:
            # Count existing data rows
            existing = self._count_data_rows(doc, rs)
            result.append({
                "path": rs.path,
                "name": rs.data_name,
                "parent_path": rs.parent_data_path,
                "max": rs.max_occur,
                "current_count": existing,
                "field_names": rs.field_names,
            })
        return result

    def _count_data_rows(self, doc: OpenDocument, rs: RepeatingSection) -> int:
        """Count existing data nodes for a repeating section."""
        parent = self._navigate_to_path(doc, rs.parent_data_path)
        if parent is None:
            return 0
        count = 0
        for child in parent:
            tag = etree.QName(child.tag).localname if "}" in child.tag else child.tag
            if tag == rs.data_name:
                count += 1
        return count

    def _navigate_to_path(self, doc: OpenDocument, path: str):
        """Navigate the data XML tree to a path, returning the element."""
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
        return node

    def add_row(self, doc_id: str, section_path: str, field_values: dict[str, str]) -> dict:
        """Add a new row to a repeating section.

        Args:
            doc_id: Document ID.
            section_path: Path of the repeating section (from list_repeating_sections).
            field_values: Dict mapping field names to values (just the field name,
                         not the full path — e.g. {"FamilyName": "SMITH"}).

        Returns:
            Dict with row index and resolved values.
        """
        doc = self._get_doc(doc_id)

        # Find the matching repeating section
        rs = None
        for s in doc.repeating_sections:
            if s.path == section_path:
                rs = s
                break
        if rs is None:
            raise ValueError(f"Repeating section not found: {section_path}")

        # Check max
        current = self._count_data_rows(doc, rs)
        if rs.max_occur != -1 and current >= rs.max_occur:
            raise ValueError(f"Cannot add row: max {rs.max_occur} reached ({current} existing)")

        # Navigate to parent
        parent = self._navigate_to_path(doc, rs.parent_data_path)
        if parent is None:
            raise ValueError(f"Parent path not found in datasets: {rs.parent_data_path}")

        # Create new data node
        new_row = etree.SubElement(parent, rs.data_name)

        # Fill field values, applying the same resolvers
        resolved = {}
        for field_name, value in field_values.items():
            # Build a pseudo full path for resolver lookup
            full_path = f"{section_path}/{field_name}"
            val = self._resolve_checkbox_value(doc, full_path, value)
            val = self._resolve_choicelist_value(doc, full_path, val)
            val = self._normalize_date(doc, full_path, val)
            etree.SubElement(new_row, field_name).text = val
            resolved[field_name] = val

        return {
            "row_index": current,
            "section": section_path,
            "values": resolved,
        }

    def _strip_signature_fields(self, fields) -> None:
        """Recursively remove /V from signature fields."""
        for field in fields:
            ft = str(field.get("/FT", ""))
            if ft == "/Sig" and "/V" in field:
                del field["/V"]
            kids = field.get("/Kids", [])
            if kids:
                self._strip_signature_fields(kids)

    def _prepare_for_save(self, doc: OpenDocument) -> None:
        """Serialize modified datasets XML back into the PDF and strip signatures.

        Shared logic used by both save() and save_bytes().
        """
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

    def save(self, doc_id: str, output_path: Path) -> Path:
        """Write modified datasets back to the PDF and save."""
        doc = self._get_doc(doc_id)
        output_path = Path(output_path)

        self._prepare_for_save(doc)

        doc.pdf.save(output_path)
        return output_path

    def save_bytes(self, doc_id: str) -> bytes:
        """Write modified datasets back to the PDF and return as bytes."""
        doc = self._get_doc(doc_id)

        self._prepare_for_save(doc)

        buf = io.BytesIO()
        doc.pdf.save(buf)
        return buf.getvalue()

    def close(self, doc_id: str) -> None:
        """Close document and free resources."""
        if doc_id in self.documents:
            doc = self.documents[doc_id]
            doc.pdf.close()
            del self.documents[doc_id]
