"""Tests for the parts of the title block reader that are pure logic.

Grid detection and OCR need a real PDF and a Tesseract binary, so they are
not covered here. Revision-row parsing is where the OCR noise actually gets
interpreted, and it is testable directly against strings observed coming out
of Tesseract on the sample drawings.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mdr.titleblock import Grid, Segment, TitleBlockReader  # noqa: E402

parse_row = TitleBlockReader._parse_history_row


class TestRevisionRowParsing:
    @pytest.mark.parametrize("text,expected", [
        # Exactly as Tesseract returned them from the sample corpus.
        ("|   A 2026/04/14 | ISSUED FOR BID (30006386 NORTH AITKEN EXPANSION)",
         ("A", "2026/04/14", "ISSUED FOR BID (30006386 NORTH AITKEN EXPANSION)")),
        ("} B | 2026/05/08 | RE-ISSUED FOR BID (30006386 NORTH AITKEN EXPANSION)",
         ("B", "2026/05/08", "RE-ISSUED FOR BID (30006386 NORTH AITKEN EXPANSION)")),
        ("| 3 | 2026/01/07 | IFC (30006386 - NORTH AITKEN CREEK EXPANSION)",
         ("3", "2026/01/07", "IFC (30006386 - NORTH AITKEN CREEK EXPANSION)")),
        ("| = 1 | 2022/05/20 | AS-BUILT",
         ("1", "2022/05/20", "AS-BUILT")),
    ])
    def test_observed_rows(self, text, expected):
        assert parse_row(text) == expected

    def test_letter_o_is_read_as_revision_zero(self):
        row = parse_row("O    2022/01/04 | ISSUED FOR CONSTRUCTION")
        assert row is not None
        assert row[0] == "0"
        assert row[2] == "ISSUED FOR CONSTRUCTION"

    def test_trailing_initials_columns_are_stripped(self):
        # The drawn/checked/approved columns bleed into the description crop.
        row = parse_row("| 2 | 2025/08/01 | REVISED TITLEBLOCK RT GH BB")
        assert row is not None
        assert row[2] == "REVISED TITLEBLOCK"

    def test_row_without_a_date_is_rejected(self):
        # Blank ruled rows above the populated ones must not become entries.
        assert parse_row("|  |  |   |   |") is None
        assert parse_row("ee ee") is None

    def test_too_short_is_rejected(self):
        assert parse_row("") is None
        assert parse_row("| 1 |") is None

    def test_description_must_have_content(self):
        assert parse_row("| 1 | 2022/05/20 | x") is None

    def test_date_recovered_when_revision_is_unreadable(self):
        row = parse_row("~~~ 2026/03/13 ISSUED FOR CONSTRUCTION")
        assert row is not None
        assert row[1] == "2026/03/13"
        assert "ISSUED FOR CONSTRUCTION" in row[2]


class TestGridQueries:
    """The grid is the geometry the whole reader depends on."""

    def _grid(self) -> Grid:
        # A 2x2 table: verticals at x=0,50,100; horizontals at y=0,20,40.
        verticals = [Segment(pos=x, start=0, end=40) for x in (0, 50, 100)]
        horizontals = [Segment(pos=y, start=0, end=100) for y in (0, 20, 40)]
        return Grid(horizontals=horizontals, verticals=verticals, width=100, height=40)

    def test_column_bounds_bracket_the_point(self):
        assert self._grid().column_bounds(25, 10) == (0, 50)
        assert self._grid().column_bounds(75, 10) == (50, 100)

    def test_cell_below_a_label(self):
        # A label occupying x 10-40, y 2-12 sits in the top-left cell; the
        # cell below it is bounded by the same verticals and the y=20 rule.
        x0, y0, x1, y1 = self._grid().cell_below(10, 2, 40, 12)
        assert (x0, x1) == (3, 47)
        assert y0 == 15 and y1 == 17

    def test_rules_above_are_nearest_first(self):
        assert self._grid().rules_above(25, 45) == [40, 20, 0]

    def test_segment_span_test_is_inclusive_with_tolerance(self):
        segment = Segment(pos=10, start=100, end=200)
        assert segment.spans(150)
        assert segment.spans(100)
        assert segment.spans(98)          # within tolerance
        assert not segment.spans(50)

    def test_missing_rules_fall_back_to_the_image_edges(self):
        empty = Grid(horizontals=[], verticals=[], width=100, height=40)
        assert empty.column_bounds(50, 10) == (0, 100)
        assert empty.rules_above(50, 30) == []
