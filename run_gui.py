"""Desktop entry point - also the PyInstaller target."""

import multiprocessing
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    # Required so the worker pool does not re-launch the GUI on Windows.
    multiprocessing.freeze_support()
    from mdr.gui import launch

    launch()
