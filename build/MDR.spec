# PyInstaller spec - build with:  pyinstaller build\MDR.spec
#
# Produces a one-folder distribution in dist\EnbridgeMDR. One-folder rather
# than one-file: a single .exe re-extracts ~80 MB of OpenCV and PyMuPDF to a
# temp directory on every launch, which is slow and trips some corporate
# endpoint protection.
#
# Tesseract is bundled if a copy is placed in vendor\tesseract before
# building, so the result needs nothing pre-installed on the target machine.

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

PROJECT = Path(SPECPATH).resolve().parent
VENDOR = PROJECT / "vendor" / "tesseract"

binaries = []
datas = []

if VENDOR.is_dir():
    for path in VENDOR.rglob("*"):
        if path.is_file():
            datas.append((str(path), str(Path("tesseract") / path.relative_to(VENDOR).parent)))

hidden = [
    "PIL._tkinter_finder",
    "openpyxl.cell._writer",
    "ezdxf.addons",
]

analysis = Analysis(
    [str(PROJECT / "run_gui.py")],
    pathex=[str(PROJECT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "pandas", "PyQt5", "PySide2", "IPython"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="EnbridgeMDR",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(PROJECT / "build" / "app.ico") if (PROJECT / "build" / "app.ico").is_file() else None,
)

cli = EXE(
    PYZ(Analysis(
        [str(PROJECT / "run_cli.py")],
        pathex=[str(PROJECT / "src")],
        hiddenimports=hidden,
        excludes=["matplotlib", "scipy", "pandas", "PyQt5", "PySide2", "IPython"],
    ).pure),
    Analysis(
        [str(PROJECT / "run_cli.py")],
        pathex=[str(PROJECT / "src")],
        hiddenimports=hidden,
        excludes=["matplotlib", "scipy", "pandas", "PyQt5", "PySide2", "IPython"],
    ).scripts,
    [],
    exclude_binaries=True,
    name="EnbridgeMDR-cli",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

COLLECT(
    exe,
    cli,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="EnbridgeMDR",
)
