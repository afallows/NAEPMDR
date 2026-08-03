"""Parallel scan orchestration.

Extraction is CPU-bound (rendering plus several Tesseract passes per cell),
so it runs across processes. Each worker builds its own Extractor once and
reuses it, because locating Tesseract and the ODA converter is far from
free.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .config import Settings
from .extractor import Extractor, discover
from .models import DocumentRecord

# Per-process singletons, initialised on first use inside each worker.
_WORKER_EXTRACTOR: Extractor | None = None
_WORKER_SETTINGS: Settings | None = None


def _init_worker(settings: Settings) -> None:
    global _WORKER_SETTINGS
    _WORKER_SETTINGS = settings
    # Tesseract parallelises internally with OpenMP and will happily use
    # every core for a single call. Inside a worker pool that fights the
    # pool: N workers each spawning multi-threaded Tesseract oversubscribes
    # the machine and each call gets slower. One thread per worker lets the
    # pool supply the parallelism, which is the faster arrangement by a wide
    # margin on a full scan.
    os.environ["OMP_THREAD_LIMIT"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"


def _extract_one(args: tuple[str, str, str]) -> list[DocumentRecord]:
    """Worker entry point: extract one PDF into records."""
    global _WORKER_EXTRACTOR
    path, root, register_dir = args
    settings = _WORKER_SETTINGS or Settings()
    if _WORKER_EXTRACTOR is None:
        _WORKER_EXTRACTOR = Extractor(settings)
    return _WORKER_EXTRACTOR.extract(Path(path), Path(root), Path(register_dir))


@dataclass
class ScanProgress:
    done: int
    total: int
    current: str
    records: list[DocumentRecord]


class ScanCancelled(RuntimeError):
    pass


def scan(
    root: str,
    settings: Settings,
    register_dir: str | None = None,
    on_progress: Callable[[ScanProgress], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[DocumentRecord]:
    """Scan ``root`` and return register records, newest-first per folder.

    ``on_progress`` is called after each file completes; ``should_cancel``
    is polled so a GUI can stop a long scan.
    """
    root_path = Path(root)
    files = discover(root, settings)
    out_dir = Path(register_dir) if register_dir else root_path
    total = len(files)
    records: list[DocumentRecord] = []

    if not files:
        if on_progress:
            on_progress(ScanProgress(0, 0, "", []))
        return records

    workers = min(settings.worker_count(), max(1, total))

    if workers == 1:
        extractor = Extractor(settings)
        for index, path in enumerate(files, start=1):
            if should_cancel and should_cancel():
                raise ScanCancelled()
            records.extend(extractor.extract(path, root_path, out_dir))
            if on_progress:
                on_progress(ScanProgress(index, total, path.name, records))
        return _sort(records)

    payload = [(str(p), str(root_path), str(out_dir)) for p in files]
    with ProcessPoolExecutor(max_workers=workers,
                             initializer=_init_worker,
                             initargs=(settings,)) as pool:
        futures = {pool.submit(_extract_one, item): item[0] for item in payload}
        done = 0
        for future in as_completed(futures):
            name = Path(futures[future]).name
            if should_cancel and should_cancel():
                for pending in futures:
                    pending.cancel()
                raise ScanCancelled()
            try:
                records.extend(future.result())
            except Exception as exc:
                failed = DocumentRecord(
                    file_path=futures[future],
                    file_name=name,
                    error=f"Worker failed: {exc}",
                )
                failed.add_flag("Extraction failed")
                records.append(failed)
            done += 1
            if on_progress:
                on_progress(ScanProgress(done, total, name, records))
    return _sort(records)


def _sort(records: list[DocumentRecord]) -> list[DocumentRecord]:
    """Order by folder, then drawing number, then sheet - normal MDR order."""
    return sorted(
        records,
        key=lambda r: (r.folder.lower(), r.drawing_number.upper(), r.page),
    )
