"""Turn files on disk into DocumentRecords."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import fitz

from .config import Settings
from .dwg_source import locate_oda, read_dwg_titleblock
from .models import (
    DocumentRecord,
    RevisionEntry,
    SOURCE_DWG,
    SOURCE_FILENAME,
    SOURCE_OCR,
)
from .ocr import OcrEngine
from .parse import (
    clean_drawing_number,
    compare_numbers,
    infer_discipline,
    normalise_date,
    normalise_revision,
    normalise_status,
    parse_filename,
)
from .titleblock import TITLEBLOCK_REGION, TitleBlockReader, render_region


def discover(root: str, settings: Settings) -> list[Path]:
    """Find candidate PDFs beneath ``root``."""
    root_path = Path(root)
    if not root_path.is_dir():
        raise NotADirectoryError(f"Not a folder: {root}")
    excluded = {e.strip().lower() for e in settings.exclude_folders if e.strip()}
    pattern = "**/*" if settings.recursive else "*"
    out: list[Path] = []
    for path in sorted(root_path.glob(pattern)):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        rel_parts = {p.lower() for p in path.relative_to(root_path).parts[:-1]}
        if rel_parts & excluded:
            continue
        out.append(path)
    return out


def _companion_dwg(pdf_path: Path) -> Path | None:
    """Return a .dwg sitting beside the PDF with the same stem."""
    for suffix in (".dwg", ".DWG"):
        candidate = pdf_path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None




class Extractor:
    """Reads one PDF (and its DWG twin) into register records."""

    def __init__(self, settings: Settings, ocr: OcrEngine | None = None):
        self.settings = settings
        self.ocr = ocr or OcrEngine(settings.tesseract_path or None, settings.ocr_language)
        self.reader = TitleBlockReader(self.ocr)
        self.oda_path = (
            locate_oda(settings.oda_converter_path or None)
            if settings.prefer_dwg else None
        )

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _merge(record: DocumentRecord, data: dict, source: str) -> None:
        """Apply extracted fields onto a record without clobbering good data."""
        simple = [
            "drawing_number", "revision", "project_number", "scale",
            "drawn_by", "design_engineer", "drawing_check", "design_check",
            "project_approval", "facility",
        ]
        for key in simple:
            value = (data.get(key) or "").strip()
            if value and not getattr(record, key, ""):
                setattr(record, key, value)

        if data.get("drawing_date") and not record.drawing_date:
            raw_date = data["drawing_date"]
            record.drawing_date = normalise_date(raw_date) or raw_date

        lines = [l for l in (data.get("title_lines") or []) if l.strip()]
        if lines and not record.title_lines:
            record.title_lines = lines

        history = data.get("revision_history") or []
        if history and not record.revision_history:
            record.revision_history = [
                RevisionEntry(revision=normalise_revision(r), date=normalise_date(d) or d,
                              description=desc)
                for r, d, desc in history
            ]

        confidences = [v for v in (data.get("confidences") or {}).values() if v]
        if confidences:
            record.confidence = max(record.confidence, round(sum(confidences) / len(confidences), 1))
        if source == SOURCE_DWG:
            record.source = SOURCE_DWG
        elif record.source != SOURCE_DWG:
            record.source = source

    def _finalise(self, record: DocumentRecord, root: Path, register_dir: Path) -> None:
        """Derive computed fields, resolve conflicts and raise QC flags."""
        path = Path(record.file_path)

        # Filename-derived values, used for cross-checking only.
        fn_number, fn_rev = parse_filename(path.stem)
        record.filename_drawing_number = fn_number
        record.filename_revision = fn_rev

        self._reconcile_revision(record)

        # Title block is authoritative; fall back to the filename when the
        # title block could not be read at all.
        if not record.drawing_number and fn_number:
            record.drawing_number = fn_number
            record.source = SOURCE_FILENAME
            record.add_flag("Drawing number taken from filename (title block unreadable)")
        elif fn_number and not compare_numbers(record.drawing_number, fn_number):
            record.add_flag(
                f"Drawing number mismatch - title block '{record.drawing_number}' "
                f"vs filename '{fn_number}'"
            )

        if not record.revision and fn_rev:
            record.revision = fn_rev
            record.add_flag("Revision taken from filename (title block unreadable)")
        elif fn_rev and record.revision and record.revision.upper() != fn_rev.upper():
            record.add_flag(
                f"Revision mismatch - title block '{record.revision}' vs filename '{fn_rev}'"
            )

        # Status from the newest revision history row.
        if record.revision_history:
            latest = self._latest_revision(record)
            if latest:
                record.status_description = latest.description
                record.revision_date = latest.date
                record.status = normalise_status(latest.description)
        if not record.status and record.status_description:
            record.status = normalise_status(record.status_description)
        if not record.status:
            record.add_flag("Status could not be determined from revision description")

        if record.drawing_date and not normalise_date(record.drawing_date):
            record.add_flag(f"Drawing date not parseable - OCR read '{record.drawing_date}'")

        record.title = " / ".join(record.title_lines)
        if not record.title:
            record.add_flag("Title not readable")
        record.discipline = infer_discipline(record.drawing_number)

        if not record.drawing_number:
            record.add_flag("Drawing number not readable")
        if record.confidence and record.confidence < 60:
            record.add_flag(f"Low OCR confidence ({record.confidence:.0f}%)")

        # File metadata and links.
        try:
            stat = path.stat()
            record.file_size_bytes = stat.st_size
            record.file_modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            pass
        record.file_name = path.name
        record.file_type = path.suffix.lstrip(".").upper()
        try:
            record.relative_path = str(path.relative_to(root))
            record.folder = str(path.parent.relative_to(root)) or "."
        except ValueError:
            record.relative_path = str(path)
            record.folder = str(path.parent)

        if self.settings.link_style == "absolute":
            record.link_target = str(path)
        else:
            try:
                record.link_target = os.path.relpath(path, register_dir)
            except ValueError:
                # Different drive on Windows - a relative link is impossible.
                record.link_target = str(path)
                record.add_flag("Absolute link used (file is on a different drive)")

    @staticmethod
    def _reconcile_revision(record: DocumentRecord) -> None:
        """Cross-check the revision triangle against the revision history.

        The revision is printed inside a triangle, whose strokes routinely
        contaminate the OCR. The newest row of the revision history carries
        the same value in plain ruled text and is markedly more reliable,
        so it wins whenever the two disagree.
        """
        if not record.revision_history:
            return
        dated = [e for e in record.revision_history if e.date and e.revision]
        newest = (max(dated, key=lambda e: e.date) if dated
                  else next((e for e in reversed(record.revision_history) if e.revision), None))
        if not newest or not newest.revision:
            return

        if not record.revision:
            record.revision = newest.revision
        elif record.revision.upper() != newest.revision.upper():
            record.add_flag(
                f"Revision triangle read '{record.revision}' but the revision history "
                f"ends at '{newest.revision}' - history used"
            )
            record.revision = newest.revision

    @staticmethod
    def _latest_revision(record: DocumentRecord) -> RevisionEntry | None:
        """Pick the revision history row matching the title block revision.

        Falls back to the newest row by date, then to the last row read.
        """
        if not record.revision_history:
            return None
        if record.revision:
            target = record.revision.upper()
            for entry in record.revision_history:
                if entry.revision.upper() == target:
                    return entry
        dated = [e for e in record.revision_history if e.date]
        if dated:
            return max(dated, key=lambda e: e.date)
        return record.revision_history[-1]

    # -- main ------------------------------------------------------------

    def extract(self, pdf_path: Path, root: Path, register_dir: Path) -> list[DocumentRecord]:
        """Extract every sheet of one PDF."""
        records: list[DocumentRecord] = []
        try:
            doc = fitz.open(str(pdf_path))
        except Exception as exc:
            record = DocumentRecord(file_path=str(pdf_path), error=f"Cannot open PDF: {exc}")
            self._finalise(record, root, register_dir)
            return [record]

        dwg = _companion_dwg(pdf_path)
        dwg_data: dict = {}
        if dwg and self.settings.prefer_dwg and self.oda_path:
            try:
                dwg_data = read_dwg_titleblock(str(dwg), self.oda_path)
            except Exception:
                dwg_data = {}

        page_count = len(doc)
        limit = page_count if self.settings.all_pages else 1
        limit = min(limit, self.settings.max_pages_per_file)

        for index in range(limit):
            record = DocumentRecord(
                file_path=str(pdf_path),
                page=index + 1,
                page_count=page_count,
                companion_dwg=dwg.name if dwg else "",
            )
            try:
                page = doc[index]
                tb_image, region = render_region(
                    page, TITLEBLOCK_REGION, self.settings.render_dpi)
                self._merge(record, self.reader.read(tb_image, region), SOURCE_OCR)
            except Exception as exc:
                record.error = f"Extraction failed: {exc}"

            # DWG attributes are exact, so they take precedence. Applied
            # after OCR so mismatches can be detected first.
            if dwg_data:
                self._compare_dwg(record, dwg_data)

            self._finalise(record, root, register_dir)
            records.append(record)

        doc.close()

        if self.settings.collapse_identical_sheets and len(records) > 1:
            numbers = {r.drawing_number for r in records if r.drawing_number}
            if len(numbers) <= 1:
                head = records[0]
                head.page_count = page_count
                return [head]
        return records

    def _compare_dwg(self, record: DocumentRecord, dwg_data: dict) -> None:
        """Overlay DWG attribute values, flagging any disagreement with OCR."""
        if self.settings.flag_dwg_pdf_mismatch:
            dwg_number = clean_drawing_number(dwg_data.get("drawing_number", ""))
            if dwg_number and record.drawing_number and not compare_numbers(
                dwg_number, record.drawing_number
            ):
                record.add_flag(
                    f"DWG/PDF drawing number differ - DWG '{dwg_number}' "
                    f"vs PDF '{record.drawing_number}'"
                )
            dwg_rev = normalise_revision(dwg_data.get("revision", ""))
            if dwg_rev and record.revision and dwg_rev.upper() != record.revision.upper():
                record.add_flag(
                    f"DWG/PDF revision differ - DWG '{dwg_rev}' vs PDF '{record.revision}' "
                    "(PDF may be published from a stale DWG)"
                )

        # DWG wins on the fields it supplies.
        for key in ("drawing_number", "revision", "project_number", "scale",
                    "drawn_by", "design_engineer", "drawing_check", "design_check",
                    "project_approval", "facility"):
            value = (dwg_data.get(key) or "").strip()
            if value:
                setattr(record, key, value)
        if dwg_data.get("title_lines"):
            record.title_lines = dwg_data["title_lines"]
        if dwg_data.get("drawing_date"):
            record.drawing_date = normalise_date(dwg_data["drawing_date"]) or dwg_data["drawing_date"]
        record.source = SOURCE_DWG
        record.confidence = 100.0
