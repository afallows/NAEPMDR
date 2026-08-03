"""Tests for the DWG/DXF title block path.

The ODA File Converter is Windows-only, so DWG conversion itself cannot be
exercised here. Everything after it can: a DXF carrying Enbridge-style block
attributes is built with ezdxf and read back through the real code path.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ezdxf = pytest.importorskip("ezdxf")

from mdr.dwg_source import (  # noqa: E402
    fields_from_attributes,
    locate_oda,
    read_dxf_titleblock,
)

# Values taken from the sample corpus so the test reflects real content.
TITLE_BLOCK = {
    "DWG_NO": "PID-22064-050-010-04",
    "REV": "3",
    "PROJECT_NO": "30006386",
    "SCALE": "NTS",
    "TITLE1": "PIPING AND INSTRUMENTATION DIAGRAM",
    "TITLE2": "INLET HEADER BUILDING (BU-50)",
    "FACILITY": "PP-1 AITKEN CREEK GAS STORAGE FACILITY",
    "DRAWN": "CAW",
    "DESIGN_ENGINEER": "MC",
    "CHECKED": "JW",
    "REV_DESC": "ISSUED FOR CONSTRUCTION (30006386 NORTH AITKEN EXPANSION)",
}


def _build_dxf(tmp_path: Path, attrs: dict[str, str]) -> str:
    """Write a DXF containing one block reference carrying ``attrs``."""
    doc = ezdxf.new("R2018", setup=True)
    block = doc.blocks.new(name="TITLEBLOCK")
    for index, tag in enumerate(attrs):
        block.add_attdef(tag=tag, insert=(0, -index))
    msp = doc.modelspace()
    ref = msp.add_blockref("TITLEBLOCK", (0, 0))
    ref.add_auto_attribs(attrs)
    path = tmp_path / "sample.dxf"
    doc.saveas(str(path))
    return str(path)


class TestAttributeMapping:
    def test_maps_enbridge_tags(self):
        fields = fields_from_attributes(TITLE_BLOCK)
        assert fields["drawing_number"] == "PID-22064-050-010-04"
        assert fields["revision"] == "3"
        assert fields["project_number"] == "30006386"
        assert fields["facility"] == "PP-1 AITKEN CREEK GAS STORAGE FACILITY"

    def test_tag_matching_ignores_separators_and_case(self):
        # DWG_NO, DWGNO and dwg-no must all resolve to the same field.
        for tag in ("DWG_NO", "DWGNO", "dwg-no", "Dwg No"):
            assert fields_from_attributes({tag: "X-1"})["drawing_number"] == "X-1"

    def test_title_lines_are_ordered_and_deduplicated(self):
        fields = fields_from_attributes(TITLE_BLOCK)
        assert fields["title_lines"] == [
            "PIPING AND INSTRUMENTATION DIAGRAM",
            "INLET HEADER BUILDING (BU-50)",
        ]

    def test_duplicate_title_text_collapses(self):
        fields = fields_from_attributes({"TITLE1": "SAME", "TITLE2": "SAME"})
        assert fields["title_lines"] == ["SAME"]

    def test_revision_description_becomes_history(self):
        fields = fields_from_attributes(TITLE_BLOCK)
        assert fields["revision_history"] == [
            ("3", "", "ISSUED FOR CONSTRUCTION (30006386 NORTH AITKEN EXPANSION)")
        ]

    def test_dwg_values_are_full_confidence(self):
        # DWG attributes are exact; nothing was guessed.
        fields = fields_from_attributes(TITLE_BLOCK)
        assert set(fields["confidences"].values()) == {100.0}

    def test_blank_values_are_ignored(self):
        assert "revision" not in fields_from_attributes({"REV": "   "})

    def test_empty_input(self):
        assert fields_from_attributes({}) == {}


class TestDxfRoundTrip:
    def test_reads_attributes_from_a_real_dxf(self, tmp_path):
        fields = read_dxf_titleblock(_build_dxf(tmp_path, TITLE_BLOCK))
        assert fields["drawing_number"] == "PID-22064-050-010-04"
        assert fields["revision"] == "3"
        assert fields["title_lines"][0] == "PIPING AND INSTRUMENTATION DIAGRAM"

    def test_unreadable_file_returns_empty_rather_than_raising(self, tmp_path):
        broken = tmp_path / "broken.dxf"
        broken.write_text("this is not a DXF")
        assert read_dxf_titleblock(str(broken)) == {}

    def test_missing_file_returns_empty(self, tmp_path):
        assert read_dxf_titleblock(str(tmp_path / "nope.dxf")) == {}


class TestConverterDiscovery:
    def test_absent_converter_returns_none_not_an_exception(self):
        # The DWG path is optional; a missing converter must degrade to OCR.
        assert locate_oda("/definitely/not/here.exe") is None
