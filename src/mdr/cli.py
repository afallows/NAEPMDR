"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .config import Settings
from .ocr import OcrUnavailable
from .register import write_register
from .scanner import ScanCancelled, ScanProgress, scan


def default_output(root: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    name = Path(root).name or "Documents"
    return str(Path(root) / f"Master Document Register - {name} - {stamp}.xlsx")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdr",
        description="Build a master document register from a folder of drawings.",
    )
    parser.add_argument("root", nargs="?", help="Folder to scan")
    parser.add_argument("-o", "--output", help="Output .xlsx path")
    parser.add_argument("--no-recursive", action="store_true",
                        help="Only scan the top-level folder")
    parser.add_argument("--first-page-only", action="store_true",
                        help="Register only page 1 of each PDF")
    parser.add_argument("--no-dwg", action="store_true",
                        help="Ignore companion DWG files and use OCR only")
    parser.add_argument("--dpi", type=int, help="Overview render DPI (default 300)")
    parser.add_argument("--workers", type=int, help="Parallel workers (0 = auto)")
    parser.add_argument("--absolute-links", action="store_true",
                        help="Use absolute rather than relative hyperlinks")
    parser.add_argument("--exclude", action="append", default=None,
                        metavar="NAME", help="Folder name to skip (repeatable)")
    parser.add_argument("--tesseract", help="Path to tesseract.exe")
    parser.add_argument("--oda", help="Path to ODAFileConverter.exe")
    parser.add_argument("--gui", action="store_true", help="Launch the desktop UI")
    parser.add_argument("-q", "--quiet", action="store_true")
    return parser


def settings_from_args(args) -> Settings:
    settings = Settings.load()
    if args.root:
        settings.root_folder = str(Path(args.root).resolve())
    settings.recursive = not args.no_recursive
    settings.all_pages = not args.first_page_only
    settings.prefer_dwg = not args.no_dwg
    if args.dpi:
        settings.render_dpi = args.dpi
    if args.workers is not None:
        settings.workers = args.workers
    if args.absolute_links:
        settings.link_style = "absolute"
    if args.exclude is not None:
        settings.exclude_folders = args.exclude
    if args.tesseract:
        settings.tesseract_path = args.tesseract
    if args.oda:
        settings.oda_converter_path = args.oda
    return settings


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.gui or not args.root:
        from .gui import launch

        launch()
        return 0

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: not a folder: {root}", file=sys.stderr)
        return 2

    settings = settings_from_args(args)
    output = args.output or default_output(str(root))
    settings.output_path = output

    def report(progress: ScanProgress) -> None:
        if args.quiet or not progress.total:
            return
        pct = 100 * progress.done / progress.total
        print(f"\r[{progress.done}/{progress.total}] {pct:5.1f}%  {progress.current[:60]:<60}",
              end="", file=sys.stderr, flush=True)

    try:
        records = scan(str(root), settings, str(Path(output).parent), on_progress=report)
    except OcrUnavailable as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 3
    except ScanCancelled:
        print("\ncancelled", file=sys.stderr)
        return 130

    if not args.quiet:
        print(file=sys.stderr)

    if not records:
        print("No PDFs found.", file=sys.stderr)
        return 1

    path = write_register(records, str(root), output,
                          settings.include_full_path_column)
    flagged = sum(1 for r in records if r.qc_flags)
    print(f"{len(records)} sheet(s) registered, {flagged} flagged for review")
    print(path)
    settings.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
