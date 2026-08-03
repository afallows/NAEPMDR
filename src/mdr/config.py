"""Persisted user settings."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path


def _settings_path() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return Path(base) / "EnbridgeMDR" / "settings.json"


@dataclass
class Settings:
    """Scan and output options."""

    root_folder: str = ""
    output_path: str = ""

    # Discovery
    recursive: bool = True
    include_pdf: bool = True
    prefer_dwg: bool = True                 # use DWG attributes when available
    flag_dwg_pdf_mismatch: bool = True
    exclude_folders: list[str] = field(
        default_factory=lambda: ["superseded", "archive", "old", "void", "_backup"]
    )

    # Sheets
    all_pages: bool = True                  # one row per sheet
    collapse_identical_sheets: bool = False
    max_pages_per_file: int = 200

    # OCR
    render_dpi: int = 300
    ocr_language: str = "eng"
    tesseract_path: str = ""
    oda_converter_path: str = ""
    workers: int = 0                        # 0 = auto (cpu_count - 1)

    # Output
    open_when_done: bool = True
    link_style: str = "relative"            # relative | absolute
    include_full_path_column: bool = True

    @classmethod
    def load(cls) -> "Settings":
        path = _settings_path()
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                known = {f for f in cls.__dataclass_fields__}
                return cls(**{k: v for k, v in data.items() if k in known})
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def worker_count(self) -> int:
        if self.workers > 0:
            return self.workers
        return max(1, (os.cpu_count() or 2) - 1)
