"""Data model for a single register row."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# Sources, in descending order of trust.
SOURCE_DWG = "DWG Attributes"
SOURCE_PDF_TEXT = "PDF Text Layer"
SOURCE_OCR = "Title Block OCR"
SOURCE_FILENAME = "Filename"


@dataclass
class RevisionEntry:
    """One row of the drawing's revision history table."""

    revision: str = ""
    date: str = ""
    description: str = ""

    def as_text(self) -> str:
        bits = [b for b in (self.revision, self.date, self.description) if b]
        return " | ".join(bits)


@dataclass
class DocumentRecord:
    """A single sheet in the master document register.

    One record per sheet, not per file: a multi-sheet PDF produces several
    records that share ``file_path`` but differ in ``page``.
    """

    # --- Identity (title block is authoritative) ---
    drawing_number: str = ""
    revision: str = ""
    status: str = ""                      # normalised code, e.g. "IFC"
    status_description: str = ""          # verbatim latest revision description

    # --- Descriptive ---
    title: str = ""                       # combined title lines
    title_lines: list[str] = field(default_factory=list)
    facility: str = ""
    discipline: str = ""

    # --- Title block metadata ---
    project_number: str = ""
    scale: str = ""
    drawing_date: str = ""
    revision_date: str = ""
    drawn_by: str = ""
    design_engineer: str = ""
    drawing_check: str = ""
    design_check: str = ""
    project_approval: str = ""

    # --- Revision history ---
    revision_history: list[RevisionEntry] = field(default_factory=list)

    # --- File / location ---
    file_path: str = ""                   # absolute
    relative_path: str = ""               # relative to scan root
    link_target: str = ""                 # relative to the register workbook
    file_name: str = ""
    folder: str = ""
    file_type: str = ""                   # PDF / DWG
    file_size_bytes: int = 0
    file_modified: str = ""
    companion_dwg: str = ""               # matching .dwg if one exists

    # --- Sheet ---
    page: int = 1
    page_count: int = 1

    # --- Provenance and QC ---
    source: str = SOURCE_OCR
    confidence: float = 0.0               # 0-100
    filename_drawing_number: str = ""
    filename_revision: str = ""
    qc_flags: list[str] = field(default_factory=list)
    error: str = ""

    def add_flag(self, flag: str) -> None:
        if flag and flag not in self.qc_flags:
            self.qc_flags.append(flag)

    @property
    def qc_text(self) -> str:
        return "; ".join(self.qc_flags)

    @property
    def has_error(self) -> bool:
        return bool(self.error)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
