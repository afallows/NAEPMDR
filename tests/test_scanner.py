"""Tests for file discovery, ordering and scan control."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mdr.config import Settings  # noqa: E402
from mdr.extractor import discover  # noqa: E402
from mdr.models import DocumentRecord  # noqa: E402
from mdr.scanner import ScanCancelled, _sort, scan  # noqa: E402


@pytest.fixture
def tree(tmp_path):
    """A folder tree resembling a real project drawing store."""
    layout = [
        "EHT/PP-1-94000-5530-01_RB.pdf",
        "EHT/PP-1-94000-5535-05_RB.pdf",
        "EHT/PP-1-94000-5530-01_RB.dwg",
        "PID/PID2206405001004_R3.pdf",
        "PID/Superseded/PID2206405001004_R1.pdf",
        "Archive/old-drawing.pdf",
        "notes.txt",
        "EHT/thumbnail.png",
    ]
    for item in layout:
        path = tmp_path / item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return tmp_path


class TestDiscovery:
    def test_finds_only_pdfs(self, tree):
        found = discover(str(tree), Settings(exclude_folders=[]))
        assert all(p.suffix == ".pdf" for p in found)
        assert not any("thumbnail" in p.name for p in found)
        assert not any(p.suffix == ".dwg" for p in found)

    def test_recurses_by_default(self, tree):
        # Five PDFs across EHT, PID, PID/Superseded and Archive.
        found = discover(str(tree), Settings(exclude_folders=[]))
        assert len(found) == 5

    def test_excluded_folders_are_skipped(self, tree):
        found = discover(str(tree), Settings(
            exclude_folders=["superseded", "archive"]))
        names = {p.name for p in found}
        assert "PID2206405001004_R1.pdf" not in names
        assert "old-drawing.pdf" not in names
        assert len(found) == 3

    def test_exclusion_is_case_insensitive(self, tree):
        # 'Superseded' on disk, 'SUPERSEDED' in the setting.
        found = discover(str(tree), Settings(exclude_folders=["SUPERSEDED"]))
        assert not any("_R1" in p.name for p in found)

    def test_non_recursive_stays_at_the_top(self, tree):
        assert discover(str(tree), Settings(recursive=False)) == []

    def test_missing_folder_raises(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            discover(str(tmp_path / "nope"), Settings())


class TestOrdering:
    def test_sorted_by_folder_then_number_then_sheet(self):
        records = [
            DocumentRecord(folder="PID", drawing_number="PID-2", page=1),
            DocumentRecord(folder="EHT", drawing_number="PP-2", page=1),
            DocumentRecord(folder="EHT", drawing_number="PP-1", page=2),
            DocumentRecord(folder="EHT", drawing_number="PP-1", page=1),
        ]
        ordered = [(r.folder, r.drawing_number, r.page) for r in _sort(records)]
        assert ordered == [
            ("EHT", "PP-1", 1), ("EHT", "PP-1", 2),
            ("EHT", "PP-2", 1), ("PID", "PID-2", 1),
        ]


class TestScanControl:
    """The single-worker path, with extraction stubbed out."""

    @pytest.fixture(autouse=True)
    def _stub_extractor(self, monkeypatch):
        class Stub:
            def __init__(self, settings):
                pass

            def extract(self, path, root, register_dir):
                return [DocumentRecord(
                    file_path=str(path), file_name=path.name,
                    folder=path.parent.name, drawing_number=path.stem)]

        monkeypatch.setattr("mdr.scanner.Extractor", Stub)

    def test_reports_progress_for_every_file(self, tree):
        seen = []
        settings = Settings(workers=1, exclude_folders=[])
        records = scan(str(tree), settings, str(tree),
                       on_progress=lambda p: seen.append((p.done, p.total)))
        assert len(records) == 5
        assert seen[-1] == (5, 5)

    def test_cancellation_stops_the_scan(self, tree):
        settings = Settings(workers=1, exclude_folders=[])
        with pytest.raises(ScanCancelled):
            scan(str(tree), settings, str(tree), should_cancel=lambda: True)

    def test_empty_folder_returns_nothing_without_error(self, tmp_path):
        calls = []
        records = scan(str(tmp_path), Settings(workers=1), str(tmp_path),
                       on_progress=calls.append)
        assert records == []
        assert calls and calls[0].total == 0
