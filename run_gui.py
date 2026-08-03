"""Desktop entry point - also the PyInstaller target."""

import multiprocessing
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def _ensure_streams() -> None:
    """Give the process real stdout/stderr handles.

    A PyInstaller windowed build has none, and the multiprocessing workers
    the scanner starts fail on the first write to a missing stream. Pointing
    them at the null device costs nothing and removes the failure mode.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w"))


if __name__ == "__main__":
    # Required so the worker pool does not re-launch the GUI on Windows.
    multiprocessing.freeze_support()
    _ensure_streams()

    from mdr.gui import launch

    launch()
