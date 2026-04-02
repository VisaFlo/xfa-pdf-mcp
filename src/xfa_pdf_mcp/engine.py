"""Core XFA-PDF manipulation engine using pikepdf + lxml."""

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
    items: list[str]  # for checkButton: [on_value] or [on, off, neutral]; for choiceList: option values


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
        # Build field metadata cache from template
        doc.field_meta = self._build_field_meta(template_root, detected_ns)
        self.documents[doc_id] = doc
        return doc_id

    def _build_field_meta(self, template_root, ns_t: str) -> dict[str, FieldMeta]:
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

            # Extract items (checkbox on/off values, choiceList options)
            items = []
            for items_elem in field_elem.findall(f"{{{ns_t}}}items"):
                for item in items_elem:
                    if item.text:
                        items.append(item.text)

            meta[full_path] = FieldMeta(
                path=full_path,
                field_type=field_type,
                items=items,
            )
        return meta

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

    def fill_fields(self, doc_id: str, field_values: dict[str, str]) -> dict[str, bool]:
        """Fill multiple fields. Returns dict of path -> success.

        For checkButton fields, accepts boolean-like values (true/false, yes/no,
        checked/unchecked, on/off, 1/0) and auto-resolves to the correct template
        item value.
        """
        doc = self._get_doc(doc_id)
        results = {}
        for path, value in field_values.items():
            resolved = self._resolve_checkbox_value(doc, path, value)
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
