"""Multi-format export for tabular app data (vehicle properties, process
lists, ...): JSON, CSV, XML, HTML, and Excel (.xlsx). One shared
implementation so every "Export" button in the app behaves the same way
and supports the same format list.
"""
from __future__ import annotations

import csv
import json
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Passed straight to tkinter.filedialog's `filetypes=`.
EXPORT_FILETYPES: List[Tuple[str, str]] = [
    ("CSV", "*.csv"),
    ("JSON", "*.json"),
    ("XML", "*.xml"),
    ("HTML", "*.html"),
    ("Excel Workbook", "*.xlsx"),
]

SUPPORTED_SUFFIXES = {".csv", ".json", ".xml", ".html", ".htm", ".xlsx"}


def export_rows(
    rows: List[Dict[str, Any]],
    path: str,
    root_tag: str = "items",
    item_tag: str = "item",
    title: str = "Export",
) -> None:
    """Write `rows` (a list of flat-ish dicts, one per record) to `path`.
    Format is chosen by file extension. Values that are already dict/list
    (nested data) get JSON-encoded for formats that can't represent them
    natively (CSV/XML/HTML/XLSX) - JSON is the only format that keeps
    full structure.
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        _export_json(rows, path)
    elif suffix == ".xml":
        _export_xml(rows, path, root_tag, item_tag)
    elif suffix in (".html", ".htm"):
        _export_html(rows, path, title)
    elif suffix == ".xlsx":
        _export_xlsx(rows, path, title)
    else:
        _export_csv(rows, path)


def _flatten(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return "" if value is None else str(value)


def _columns(rows: List[Dict[str, Any]]) -> List[str]:
    columns: List[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return columns


def _export_json(rows: List[Dict[str, Any]], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2)


def _export_csv(rows: List[Dict[str, Any]], path: str) -> None:
    columns = _columns(rows)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_flatten(row.get(c)) for c in columns])


def _xml_safe_tag(name: str) -> str:
    safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in str(name)) or "field"
    return safe if not safe[0].isdigit() else f"_{safe}"


def _export_xml(rows: List[Dict[str, Any]], path: str, root_tag: str, item_tag: str) -> None:
    root = ET.Element(_xml_safe_tag(root_tag))
    for row in rows:
        item = ET.SubElement(root, _xml_safe_tag(item_tag))
        for key, value in row.items():
            child = ET.SubElement(item, _xml_safe_tag(key))
            child.text = _flatten(value)
    raw = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(pretty)


def _html_escape(value: Any) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _export_html(rows: List[Dict[str, Any]], path: str, title: str) -> None:
    columns = _columns(rows)
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{_html_escape(title)}</title>",
        "<style>"
        "body{font-family:'Segoe UI',Arial,sans-serif;background:#111417;color:#e8e8e8;padding:16px}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #3a3f45;padding:6px 10px;text-align:left;font-size:13px;"
        "max-width:480px;overflow-wrap:break-word}"
        "th{background:#20242a;position:sticky;top:0}"
        "tr:nth-child(even){background:#181b1f}"
        "h2{margin-bottom:4px}"
        "</style></head><body>",
        f"<h2>{_html_escape(title)}</h2>",
        f"<p>{len(rows)} rows exported.</p>",
        "<table><thead><tr>" + "".join(f"<th>{_html_escape(c)}</th>" for c in columns) + "</tr></thead><tbody>",
    ]
    for row in rows:
        cells = "".join(f"<td>{_html_escape(_flatten(row.get(c)))}</td>" for c in columns)
        parts.append(f"<tr>{cells}</tr>")
    parts.append("</tbody></table></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))


def _export_xlsx(rows: List[Dict[str, Any]], path: str, title: str) -> None:
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise RuntimeError(
            "Excel export requires the 'openpyxl' package (listed in requirements.txt). "
            "Re-run run.bat/run.sh to install it, or `pip install openpyxl` in your venv."
        ) from exc

    columns = _columns(rows)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = (title or "Export")[:31]
    sheet.append(columns)
    for row in rows:
        sheet.append([_flatten(row.get(c)) for c in columns])
    for index, col in enumerate(columns, start=1):
        width = max(10, min(60, len(col) + 4))
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    workbook.save(path)
