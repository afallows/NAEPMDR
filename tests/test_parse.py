"""Unit tests for the pure text-normalisation layer."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mdr.parse import (  # noqa: E402
    clean_drawing_number,
    compare_numbers,
    infer_discipline,
    normalise_date,
    normalise_revision,
    normalise_status,
    parse_filename,
)


class TestStatus:
    @pytest.mark.parametrize("description,expected", [
        # Verbatim strings read off the sample corpus.
        ("IFC (30006386 - NORTH AITKEN CREEK EXPANSION)", "IFC"),
        ("ISSUED FOR CONSTRUCTION", "IFC"),
        ("RE-ISSUED FOR BID (30006386 NORTH AITKEN EXPANSION)", "IFB"),
        ("ISSUED FOR BID (30006386 NORTH AITKEN EXPANSION)", "IFB"),
        ("IFDM (30006386 - NORTH AITKEN CREEK EXPANSION)", "IFDM"),
        ("AS-BUILT", "AS-BUILT"),
        ("AS BUILT PER FIELD TRIP - 2022 (GLE 22064)", "AS-BUILT"),
        ("ISSUED FOR AS-BUILT (WO#00003441 ICS/MECH AS-BUILTS)", "AS-BUILT"),
        ("IFI, REVISED TITLEBLOCK (19722154 - ELECTRICAL BACKDRAFTING)", "IFI"),
        ("ISSUED FOR REVIEW", "IFR"),
        ("ISSUED FOR APPROVAL", "IFA"),
        ("PRELIMINARY", "PRELIMINARY"),
        ("VOIDED", "VOID"),
    ])
    def test_recognised(self, description, expected):
        assert normalise_status(description) == expected

    def test_em_dash_is_tolerated(self):
        # OCR renders hyphens as em dashes constantly.
        assert normalise_status("IFC (30006386 — NORTH AITKEN)") == "IFC"

    def test_unknown_returns_empty_rather_than_guessing(self):
        assert normalise_status("REVISED PER MARKUP") == ""
        assert normalise_status("") == ""

    def test_demolition_wins_over_design(self):
        # 'IFDM' contains 'IFD'; the more specific code must be preferred.
        assert normalise_status("IFDM (123)") == "IFDM"


class TestRevision:
    @pytest.mark.parametrize("raw,expected", [
        ("B", "B"), ("3", "3"), ("0", "0"), ("10", "10"),
        ("  A  ", "A"), ("/B\\", "B"),
    ])
    def test_clean_values(self, raw, expected):
        assert normalise_revision(raw) == expected

    def test_letter_o_and_i_are_misread_digits(self):
        # Revision sequences omit I/O/Q by drafting convention.
        assert normalise_revision("O") == "0"
        assert normalise_revision("I") == "1"

    def test_long_reads_keep_the_trailing_token(self):
        # When the triangle bleeds into the read, the revision is what the
        # enclosure was drawn around - i.e. the tail of the string.
        assert normalise_revision("LATEST REVISION B") == "NB"
        # A leading letter is kept, because 'A1'-style revisions are real.
        assert normalise_revision("NOISEA1") == "A1"

    def test_empty(self):
        assert normalise_revision("") == ""
        assert normalise_revision("///") == ""


class TestDrawingNumber:
    def test_em_dashes_collapse_to_hyphen(self):
        assert clean_drawing_number("PP—1—94000—5535—05") == "PP-1-94000-5535-05"

    def test_stray_punctuation_is_trimmed(self):
        assert clean_drawing_number("--PP-1-94000-5535-05") == "PP-1-94000-5535-05"
        assert clean_drawing_number("ACGS-EL-5100-03.") == "ACGS-EL-5100-03"

    def test_internal_spaces_removed(self):
        assert clean_drawing_number("PID 22064 050") == "PID22064050"

    def test_comparison_ignores_separators(self):
        # The P&ID title block prints hyphens the filename omits.
        assert compare_numbers("PID-22064-050-010-04", "PID2206405001004")
        assert compare_numbers("acgs-el-6001-06", "ACGS-EL-6001-06")

    def test_comparison_rejects_real_differences(self):
        assert not compare_numbers("PP-1-94000-5530-01", "PP-1-94000-5550-01")
        assert not compare_numbers("", "ANYTHING")


class TestFilename:
    @pytest.mark.parametrize("stem,number,revision", [
        ("PP-1-94000-5535-05_RB", "PP-1-94000-5535-05", "B"),
        ("ACGS-EL-6001-06_R3", "ACGS-EL-6001-06", "3"),
        ("PID2206405001004_R3", "PID2206405001004", "3"),
        ("ACGS-EL-6001-06D-30006386_R0", "ACGS-EL-6001-06D-30006386", "0"),
        ("PID2206405001007_R5", "PID2206405001007", "5"),
    ])
    def test_known_conventions(self, stem, number, revision):
        assert parse_filename(stem) == (number, revision)

    def test_without_revision_suffix(self):
        number, revision = parse_filename("SOME-DRAWING-123")
        assert number == "SOME-DRAWING-123"
        assert revision == ""

    def test_empty(self):
        assert parse_filename("") == ("", "")


class TestDate:
    @pytest.mark.parametrize("raw,expected", [
        ("2026/01/07", "2026-01-07"),
        ("2026-05-08", "2026-05-08"),
        ("2022/05/20", "2022-05-20"),
        ("1989/08/14", "1989-08-14"),
        ("20260107", "2026-01-07"),
    ])
    def test_formats(self, raw, expected):
        assert normalise_date(raw) == expected

    def test_garbage_returns_empty_so_it_can_be_flagged(self):
        assert normalise_date("2025/12/222") == ""
        assert normalise_date("~") == ""
        assert normalise_date("") == ""


class TestDiscipline:
    @pytest.mark.parametrize("number,expected", [
        ("ACGS-EL-6001-06", "Electrical"),
        ("ACGS-ME-1000-01", "Mechanical"),
        ("ACGS-CIV-2000", "Civil"),
    ])
    def test_from_token(self, number, expected):
        assert infer_discipline(number) == expected

    def test_no_discipline_token(self):
        assert infer_discipline("PP-1-94000-5535-05") == ""
        assert infer_discipline("") == ""
