"""DWG title block extraction via the ODA File Converter.

No pure-Python library reads DWG. The Open Design Alliance ships a free
"ODA File Converter" for Windows that converts DWG to DXF, which ezdxf can
then read. When the converter is absent every call degrades to "no data"
and the caller falls back to OCR - the app never hard-fails on a missing
optional dependency.

Reading the DWG gives exact title block values with no OCR risk at all,
because Enbridge title blocks are block attributes (ATTRIB entities).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    import ezdxf
except ImportError:  # pragma: no cover
    ezdxf = None


# Attribute tags seen in Enbridge / Gas Liquids Engineering title blocks,
# mapped onto our record fields. Matching is case-insensitive and ignores
# separators, so DWG_NO / DWGNO / DRAWING_NUMBER all resolve.
ATTRIBUTE_MAP = {
    "drawing_number": ["DRAWINGNUMBER", "DWGNO", "DWGNUM", "DWGNUMBER", "DRAWINGNO",
                       "DRAWING", "NUMBER", "DRGNO", "SHEETNO"],
    "revision": ["REVISION", "REV", "REVNO", "REVISIONNO"],
    "project_number": ["PROJECTNUMBER", "PROJECTNO", "PROJNO", "JOBNO", "JOBNUMBER"],
    "scale": ["SCALE"],
    "drawing_date": ["DATE", "DWGDATE", "DRAWINGDATE", "ISSUEDATE"],
    "drawn_by": ["DRAWN", "DRAWNBY", "DRAWNDESIGN", "DESIGNEDBY", "DESIGNER"],
    "design_engineer": ["DESIGNENGINEER", "ENGINEER", "ENG"],
    "drawing_check": ["DRAWINGCHECK", "CHECKED", "CHECKEDBY", "CHK", "DWGCHK"],
    "design_check": ["DESIGNCHECK", "DSGNCHK"],
    "project_approval": ["PROJECTAPPROVAL", "APPROVED", "APPROVEDBY", "APPR"],
    "facility": ["FACILITY", "LOCATION", "SITE", "PLANT"],
}

TITLE_TAGS = ["TITLE", "TITLE1", "TITLE2", "TITLE3", "TITLE4",
              "DWGTITLE", "DRAWINGTITLE", "SUBTITLE", "DESC", "DESCRIPTION"]

REVISION_DESC_TAGS = ["REVDESC", "REVISIONDESCRIPTION", "REVDESCRIPTION",
                      "REVISIONDESC", "DESCRIPTION1", "REVTEXT"]


def _norm_tag(tag: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (tag or "").upper())


class OdaConverterUnavailable(RuntimeError):
    pass


def locate_oda(explicit: str | None = None) -> str | None:
    """Find ODAFileConverter.exe, or return None if it is not installed."""
    if explicit and Path(explicit).is_file():
        return explicit
    found = shutil.which("ODAFileConverter") or shutil.which("ODAFileConverter.exe")
    if found:
        return found
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for child in root.iterdir():
                if child.is_dir() and child.name.upper().startswith("ODA"):
                    for exe in child.rglob("ODAFileConverter*.exe"):
                        return str(exe)
        except OSError:
            continue
    return None


def dwg_to_dxf(dwg_path: str, oda_path: str, timeout: int = 120) -> str | None:
    """Convert one DWG to DXF in a temp folder; return the DXF path."""
    src = Path(dwg_path).resolve()
    workdir = Path(tempfile.mkdtemp(prefix="mdr_dwg_"))
    indir = workdir / "in"
    outdir = workdir / "out"
    indir.mkdir()
    outdir.mkdir()
    shutil.copy2(src, indir / src.name)

    # ODAFileConverter <in> <out> <version> <type> <recurse> <audit> [filter]
    cmd = [oda_path, str(indir), str(outdir), "ACAD2018", "DXF", "0", "1", "*.DWG"]
    try:
        subprocess.run(
            cmd, timeout=timeout, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError):
        shutil.rmtree(workdir, ignore_errors=True)
        return None

    for candidate in outdir.glob("*.dxf"):
        return str(candidate)
    shutil.rmtree(workdir, ignore_errors=True)
    return None


def _collect_attributes(doc) -> dict[str, str]:
    """Flatten every block-reference attribute in the layouts into a dict."""
    attrs: dict[str, str] = {}

    def absorb(insert) -> None:
        try:
            for att in insert.attribs:
                tag = _norm_tag(att.dxf.tag)
                value = (att.dxf.text or "").strip()
                if tag and value and tag not in attrs:
                    attrs[tag] = value
        except AttributeError:
            pass

    for layout in doc.layouts:
        for entity in layout:
            if entity.dxftype() == "INSERT":
                absorb(entity)
    for entity in doc.modelspace().query("INSERT"):
        absorb(entity)
    return attrs


def read_dwg_titleblock(dwg_path: str, oda_path: str | None) -> dict:
    """Return title block fields from a DWG, or {} if unreadable.

    The returned dict uses the same keys as the OCR reader so the two are
    interchangeable at the call site.
    """
    if ezdxf is None or not oda_path:
        return {}
    dxf_path = dwg_to_dxf(dwg_path, oda_path)
    if not dxf_path:
        return {}
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception:
        return {}
    finally:
        parent = Path(dxf_path).parent.parent
        shutil.rmtree(parent, ignore_errors=True)

    attrs = _collect_attributes(doc)
    if not attrs:
        return {}

    out: dict = {"confidences": {}}
    for field_name, tags in ATTRIBUTE_MAP.items():
        for tag in tags:
            key = _norm_tag(tag)
            if key in attrs:
                out[field_name] = attrs[key]
                out["confidences"][field_name] = 100.0
                break

    titles = [attrs[_norm_tag(t)] for t in TITLE_TAGS if _norm_tag(t) in attrs]
    # Preserve title line order without duplicates.
    seen: set[str] = set()
    out["title_lines"] = [t for t in titles if not (t in seen or seen.add(t))]

    for tag in REVISION_DESC_TAGS:
        key = _norm_tag(tag)
        if key in attrs:
            out["revision_history"] = [(out.get("revision", ""),
                                        out.get("drawing_date", ""),
                                        attrs[key])]
            break
    return out
