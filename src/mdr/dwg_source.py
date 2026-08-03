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


def inspect_dwg(dwg_path: str, oda_path: str | None) -> dict:
    """Return every block-attribute tag/value pair found in a DWG.

    Diagnostic aid: the ATTRIBUTE_MAP above has to match the ATTRIB tags a
    particular title block template actually uses, and those vary between
    drawing offices. Running this against one real drawing shows exactly
    what to map, instead of guessing.
    """
    if ezdxf is None:
        return {"error": "ezdxf is not installed."}
    if not oda_path:
        return {"error": "ODA File Converter not found - cannot read DWG."}
    dxf_path = dwg_to_dxf(dwg_path, oda_path)
    if not dxf_path:
        return {"error": "Conversion to DXF failed."}
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as exc:
        return {"error": f"Could not read the converted DXF: {exc}"}
    finally:
        shutil.rmtree(Path(dxf_path).parent.parent, ignore_errors=True)

    raw: dict[str, str] = {}
    blocks: list[str] = []
    for layout in list(doc.layouts) + [doc.modelspace()]:
        for entity in layout:
            if entity.dxftype() != "INSERT":
                continue
            try:
                attribs = list(entity.attribs)
            except AttributeError:
                continue
            if not attribs:
                continue
            name = getattr(entity.dxf, "name", "?")
            if name not in blocks:
                blocks.append(name)
            for att in attribs:
                tag = (att.dxf.tag or "").strip()
                value = (att.dxf.text or "").strip()
                if tag:
                    raw.setdefault(f"{name}.{tag}", value)

    mapped = {}
    normalised = {_norm_tag(k.split(".")[-1]): v for k, v in raw.items()}
    for field_name, tags in ATTRIBUTE_MAP.items():
        for tag in tags:
            if _norm_tag(tag) in normalised:
                mapped[field_name] = normalised[_norm_tag(tag)]
                break
    return {"blocks": blocks, "attributes": raw, "mapped": mapped,
            "unmapped": sorted(set(raw) - {
                k for k in raw
                if _norm_tag(k.split(".")[-1]) in {
                    _norm_tag(t) for tags in ATTRIBUTE_MAP.values() for t in tags
                } | {_norm_tag(t) for t in TITLE_TAGS + REVISION_DESC_TAGS}
            })}


def fields_from_attributes(attrs: dict[str, str]) -> dict:
    """Map raw ATTRIB tag/value pairs onto register fields.

    Split out from the DWG path so it can be tested without AutoCAD or the
    ODA converter - the tag matching is where template differences bite.
    """
    if not attrs:
        return {}
    # Strip before testing: an attribute holding only spaces is empty, and
    # letting it through would overwrite a good OCR read with whitespace.
    normalised = {}
    for key, value in attrs.items():
        cleaned = (value or "").strip()
        if cleaned:
            normalised[_norm_tag(key)] = cleaned

    out: dict = {"confidences": {}}
    for field_name, tags in ATTRIBUTE_MAP.items():
        for tag in tags:
            key = _norm_tag(tag)
            if key in normalised:
                out[field_name] = normalised[key]
                out["confidences"][field_name] = 100.0
                break

    titles = [normalised[_norm_tag(t)] for t in TITLE_TAGS if _norm_tag(t) in normalised]
    seen: set[str] = set()
    out["title_lines"] = [t for t in titles if not (t in seen or seen.add(t))]

    for tag in REVISION_DESC_TAGS:
        key = _norm_tag(tag)
        if key in normalised:
            out["revision_history"] = [(out.get("revision", ""),
                                        out.get("drawing_date", ""),
                                        normalised[key])]
            break
    return out


def read_dxf_titleblock(dxf_path: str) -> dict:
    """Extract title block fields from a converted DXF."""
    if ezdxf is None:
        return {}
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception:
        return {}
    return fields_from_attributes(_collect_attributes(doc))


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
        return read_dxf_titleblock(dxf_path)
    finally:
        shutil.rmtree(Path(dxf_path).parent.parent, ignore_errors=True)
