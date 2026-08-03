"""Tests for conflict resolution and QC flagging.

These are the rules that decide what actually lands in the register when the
title block, the filename and the companion DWG disagree, so they are tested
directly rather than inferred from a full scan.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mdr.config import Settings  # noqa: E402
from mdr.extractor import Extractor  # noqa: E402
from mdr.models import (  # noqa: E402
    DocumentRecord,
    RevisionEntry,
    SOURCE_DWG,
    SOURCE_FILENAME,
)


@pytest.fixture
def extractor():
    """An Extractor with OCR and DWG discovery bypassed.

    Only the pure decision logic is under test, so no Tesseract binary or
    ODA converter is needed.
    """
    instance = Extractor.__new__(Extractor)
    instance.settings = Settings(prefer_dwg=True, flag_dwg_pdf_mismatch=True)
    instance.oda_path = None
    return instance


def _record(name: str, **kw) -> DocumentRecord:
    root = Path("/root")
    record = DocumentRecord(file_path=str(root / "EHT" / name), **kw)
    return record


def _finalise(extractor, record):
    extractor._finalise(record, Path("/root"), Path("/root"))
    return record


class TestTitleBlockWins:
    def test_title_block_beats_the_filename(self, extractor):
        # The drawing itself is authoritative, per the agreed rule.
        record = _record("PP-1-94000-5530-01_RB.pdf",
                         drawing_number="PP-1-94000-5599-01", revision="B")
        _finalise(extractor, record)
        assert record.drawing_number == "PP-1-94000-5599-01"
        assert any("mismatch" in f for f in record.qc_flags)

    def test_agreement_raises_no_flag(self, extractor):
        record = _record("PP-1-94000-5530-01_RB.pdf",
                         drawing_number="PP-1-94000-5530-01", revision="B",
                         title="X", status="IFB", confidence=90)
        _finalise(extractor, record)
        assert not any("mismatch" in f for f in record.qc_flags)

    def test_separator_differences_are_not_mismatches(self, extractor):
        # The P&ID title block prints hyphens the filename omits.
        record = _record("PID2206405001004_R3.pdf",
                         drawing_number="PID-22064-050-010-04", revision="3")
        _finalise(extractor, record)
        assert not any("mismatch" in f for f in record.qc_flags)

    def test_falls_back_to_the_filename_when_unreadable(self, extractor):
        record = _record("ACGS-EL-6001-06_R3.pdf")
        _finalise(extractor, record)
        assert record.drawing_number == "ACGS-EL-6001-06"
        assert record.revision == "3"
        assert record.source == SOURCE_FILENAME
        assert any("filename" in f for f in record.qc_flags)


class TestRevisionReconciliation:
    def test_history_overrules_a_misread_triangle(self, extractor):
        # The triangle strokes corrupt OCR; the ruled table does not.
        record = _record("ACGS-EL-6001-06_R3.pdf", revision="J",
                         revision_history=[
                             RevisionEntry("2", "2025-08-01", "IFI"),
                             RevisionEntry("3", "2026-01-07", "IFC (30006386)"),
                         ])
        _finalise(extractor, record)
        assert record.revision == "3"
        assert any("revision history" in f for f in record.qc_flags)

    def test_history_fills_an_unread_triangle_silently(self, extractor):
        record = _record("X_R3.pdf", revision="",
                         revision_history=[RevisionEntry("3", "2026-01-07", "IFC")])
        _finalise(extractor, record)
        assert record.revision == "3"
        assert not any("revision history" in f for f in record.qc_flags)

    def test_agreement_raises_no_flag(self, extractor):
        record = _record("X_R3.pdf", revision="3",
                         revision_history=[RevisionEntry("3", "2026-01-07", "IFC")])
        _finalise(extractor, record)
        assert record.revision == "3"
        assert not any("revision history" in f for f in record.qc_flags)

    def test_newest_row_is_chosen_by_date(self, extractor):
        record = _record("X_RB.pdf", revision="",
                         revision_history=[
                             RevisionEntry("B", "2026-05-08", "RE-ISSUED FOR BID"),
                             RevisionEntry("A", "2026-04-14", "ISSUED FOR BID"),
                         ])
        _finalise(extractor, record)
        assert record.revision == "B"
        assert record.status_description == "RE-ISSUED FOR BID"


class TestStatus:
    def test_status_comes_from_the_matching_revision_row(self, extractor):
        record = _record("X_R3.pdf", revision="3", revision_history=[
            RevisionEntry("1", "2022-05-20", "AS-BUILT"),
            RevisionEntry("3", "2026-01-07", "IFC (30006386)"),
        ])
        _finalise(extractor, record)
        assert record.status == "IFC"
        assert record.status_description == "IFC (30006386)"
        assert record.revision_date == "2026-01-07"

    def test_unrecognised_description_is_flagged_not_guessed(self, extractor):
        record = _record("X_R1.pdf", revision="1", revision_history=[
            RevisionEntry("1", "2026-01-07", "UPDATED PER MARKUP")])
        _finalise(extractor, record)
        assert record.status == ""
        assert any("Status could not be determined" in f for f in record.qc_flags)


class TestQcFlags:
    def test_low_confidence_is_flagged(self, extractor):
        record = _record("X_R1.pdf", drawing_number="X", revision="1",
                         title="T", status="IFC", confidence=42.0)
        _finalise(extractor, record)
        assert any("Low OCR confidence" in f for f in record.qc_flags)

    def test_unparseable_date_is_flagged_and_kept_verbatim(self, extractor):
        record = _record("X_R1.pdf", drawing_number="X", revision="1",
                         drawing_date="2025/12/222")
        _finalise(extractor, record)
        assert record.drawing_date == "2025/12/222"
        assert any("not parseable" in f for f in record.qc_flags)

    def test_missing_title_is_flagged(self, extractor):
        record = _record("X_R1.pdf", drawing_number="X", revision="1")
        _finalise(extractor, record)
        assert any("Title not readable" in f for f in record.qc_flags)

    def test_hash_in_path_is_flagged(self, extractor):
        record = DocumentRecord(file_path="/root/rev#2/X_R1.pdf",
                                drawing_number="X", revision="1", title="T")
        _finalise(extractor, record)
        assert any("#" in f for f in record.qc_flags)


class TestDerivedFields:
    def test_discipline_inferred_from_the_drawing_number(self, extractor):
        record = _record("ACGS-EL-6001-06_R3.pdf")
        _finalise(extractor, record)
        assert record.discipline == "Electrical"

    def test_relative_link_is_computed_from_the_register_location(self, extractor):
        record = _record("X_R1.pdf", drawing_number="X")
        extractor._finalise(record, Path("/root"), Path("/root"))
        assert record.link_target == str(Path("EHT/X_R1.pdf"))
        assert record.folder == "EHT"

    def test_absolute_link_when_requested(self, extractor):
        extractor.settings.link_style = "absolute"
        record = _record("X_R1.pdf", drawing_number="X")
        _finalise(extractor, record)
        assert record.link_target == str(Path("/root/EHT/X_R1.pdf"))


class TestDwgComparison:
    def test_dwg_values_override_and_mismatches_are_flagged(self, extractor):
        record = _record("X_RB.pdf", drawing_number="PP-1-94000-5550-01",
                         revision="B", title="T")
        extractor._compare_dwg(record, {
            "drawing_number": "PP-1-94000-5530-01",
            "revision": "C",
        })
        assert record.drawing_number == "PP-1-94000-5530-01"
        assert record.revision == "C"
        assert record.source == SOURCE_DWG
        assert record.confidence == 100.0
        assert any("DWG/PDF drawing number differ" in f for f in record.qc_flags)
        assert any("stale DWG" in f for f in record.qc_flags)

    def test_agreement_produces_no_flags(self, extractor):
        record = _record("X_RB.pdf", drawing_number="PP-1-94000-5530-01",
                         revision="B")
        extractor._compare_dwg(record, {
            "drawing_number": "PP-1-94000-5530-01", "revision": "B"})
        assert not any("DWG/PDF" in f for f in record.qc_flags)

    def test_comparison_can_be_disabled(self, extractor):
        extractor.settings.flag_dwg_pdf_mismatch = False
        record = _record("X_RB.pdf", drawing_number="A", revision="B")
        extractor._compare_dwg(record, {"drawing_number": "Z", "revision": "C"})
        assert not any("DWG/PDF" in f for f in record.qc_flags)
        assert record.drawing_number == "Z"
