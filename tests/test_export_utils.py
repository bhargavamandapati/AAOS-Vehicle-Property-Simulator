import csv
import json
import xml.etree.ElementTree as ET

import pytest

from app.export_utils import export_rows

SAMPLE_ROWS = [
    {"id": "0x11600207", "name": "PERF_VEHICLE_SPEED", "areas": {"0": "12.5"}},
    {"id": "0x11400400", "name": "GEAR_SELECTION", "areas": {"0": "8"}},
]


def test_export_csv(tmp_path):
    path = tmp_path / "out.csv"
    export_rows(SAMPLE_ROWS, str(path))
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["id", "name", "areas"]
    assert rows[1][0] == "0x11600207"
    assert rows[1][1] == "PERF_VEHICLE_SPEED"


def test_export_csv_flattens_nested_values(tmp_path):
    path = tmp_path / "out.csv"
    export_rows(SAMPLE_ROWS, str(path))
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    # Nested dict values are JSON-encoded into a single cell, not dropped.
    assert json.loads(rows[1][2]) == {"0": "12.5"}


def test_export_json_round_trips_nested_values(tmp_path):
    path = tmp_path / "out.json"
    export_rows(SAMPLE_ROWS, str(path))
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data == SAMPLE_ROWS


def test_export_xml_structure(tmp_path):
    path = tmp_path / "out.xml"
    export_rows(SAMPLE_ROWS, str(path), root_tag="properties", item_tag="property")
    tree = ET.parse(path)
    root = tree.getroot()
    assert root.tag == "properties"
    items = root.findall("property")
    assert len(items) == 2
    assert items[0].find("name").text == "PERF_VEHICLE_SPEED"
    assert items[0].find("id").text == "0x11600207"


def test_export_xml_sanitizes_field_names_for_tags(tmp_path):
    path = tmp_path / "out.xml"
    rows = [{"weird key!": "value", "1starts_with_digit": "x"}]
    export_rows(rows, str(path))
    text = path.read_text(encoding="utf-8")
    # Must not contain the raw invalid characters as element tag names.
    assert "<weird key!>" not in text
    assert "<1starts_with_digit>" not in text


def test_export_html_contains_table_and_escapes_values(tmp_path):
    path = tmp_path / "out.html"
    rows = [{"name": "<script>alert(1)</script>", "value": "A & B"}]
    export_rows(rows, str(path), title="My Export")
    html = path.read_text(encoding="utf-8")
    assert "<table>" in html
    assert "My Export" in html
    assert "<script>alert(1)</script>" not in html  # must be escaped
    assert "&lt;script&gt;" in html
    assert "A &amp; B" in html


def test_export_xlsx_creates_valid_workbook(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "out.xlsx"
    export_rows(SAMPLE_ROWS, str(path), title="Vehicle Properties")
    workbook = openpyxl.load_workbook(path)
    sheet = workbook.active
    assert sheet.title == "Vehicle Properties"
    header = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    assert header == ["id", "name", "areas"]
    first_data_row = [cell.value for cell in next(sheet.iter_rows(min_row=2, max_row=2))]
    assert first_data_row[0] == "0x11600207"


def test_export_defaults_to_csv_for_unknown_extension(tmp_path):
    path = tmp_path / "out.unknownext"
    export_rows(SAMPLE_ROWS, str(path))
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["id", "name", "areas"]


def test_export_empty_rows_does_not_crash(tmp_path):
    for ext in (".csv", ".json", ".xml", ".html"):
        path = tmp_path / f"empty{ext}"
        export_rows([], str(path))
        assert path.exists()
