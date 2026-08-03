"""Title block location and field extraction.

The approach, in order:

1. Render the bottom strip of the sheet at high DPI - on every drawing in
   the corpus both the title block and the revision history live there.
2. Recover the ruled table structure with morphological line detection.
   The rules are *segments*, not full-width lines, so they are collected as
   connected components and queried by the span they cover.
3. OCR the strip at word level under several page-segmentation modes and
   merge the results, then locate anchor labels ("DRAWING NUMBER",
   "REVISION DESCRIPTION", "PROJECT NUMBER", ...).
4. For each anchor, resolve the adjacent cell from the grid and OCR just
   that cell, with a per-field character whitelist and multi-PSM consensus.
   The revision history is read one ruled row at a time, which turns a
   dense multi-column table into a series of clean single-line reads.

Reading each value from the cell beneath its own printed label is what lets
this survive different sheet sizes and title block scales - no field
position is hardcoded.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

from .ocr import (
    OcrEngine,
    WHITELIST_DATE,
    WHITELIST_DRAWING_NO,
    WHITELIST_REVISION,
)
from .parse import clean_drawing_number, normalise_revision

# Fraction of the page treated as the title block search window. Covers the
# revision history table on the left through to the right sheet edge.
TITLEBLOCK_REGION = (0.24, 0.82, 1.00, 1.00)

# Word-level OCR passes. psm 4 reads the ruled label grid, psm 11 catches
# isolated large text, psm 6 fills gaps.
ANCHOR_PSMS = (4, 11)


@dataclass
class RegionMap:
    """Maps pixel coordinates in a rendered strip back to PDF page space.

    Lets a cell located on the low-DPI overview be re-rendered from the
    vector page at a much higher DPI. Re-rendering recovers real detail,
    where upscaling the overview crop only interpolates what is already
    there - this is what fixes digit confusion in drawing numbers.
    """

    page: object
    origin_x: float
    origin_y: float
    scale: float          # PDF points per pixel

    def to_rect(self, box: tuple[int, int, int, int]):
        import fitz

        x0, y0, x1, y1 = box
        return fitz.Rect(
            self.origin_x + x0 * self.scale,
            self.origin_y + y0 * self.scale,
            self.origin_x + x1 * self.scale,
            self.origin_y + y1 * self.scale,
        )

    def render(self, box: tuple[int, int, int, int], dpi: int,
               inset: float = 0.0) -> Image.Image | None:
        """Render one cell straight from the page at ``dpi``."""
        x0, y0, x1, y1 = box
        if x1 - x0 < 6 or y1 - y0 < 5:
            return None
        if inset:
            dx, dy = (x1 - x0) * inset, (y1 - y0) * inset
            x0, y0, x1, y1 = x0 + dx, y0 + dy, x1 - dx, y1 - dy
            if x1 - x0 < 6 or y1 - y0 < 5:
                return None
        rect = self.to_rect((x0, y0, x1, y1))
        try:
            pix = self.page.get_pixmap(clip=rect, dpi=dpi)
        except Exception:
            return None
        return Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")


@dataclass
class Segment:
    """A detected rule: horizontal (``pos`` is y) or vertical (``pos`` is x)."""

    pos: int
    start: int
    end: int

    def spans(self, value: int, tol: int = 4) -> bool:
        return self.start - tol <= value <= self.end + tol


@dataclass
class Grid:
    horizontals: list[Segment] = field(default_factory=list)
    verticals: list[Segment] = field(default_factory=list)
    width: int = 0
    height: int = 0

    # -- neighbourhood queries ------------------------------------------

    def column_bounds(self, cx: int, y: int) -> tuple[int, int]:
        """Left and right rules bracketing ``cx`` at height ``y``."""
        left = max((s.pos for s in self.verticals if s.pos < cx - 4 and s.spans(y)), default=0)
        right = min((s.pos for s in self.verticals if s.pos > cx + 4 and s.spans(y)), default=self.width)
        return left, right

    def cell_below(self, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
        """The cell rectangle directly beneath a label box."""
        cx = (x0 + x1) // 2
        probe = y1 + max(6, (y1 - y0) // 2)
        left, right = self.column_bounds(cx, probe)
        bottom = min(
            (s.pos for s in self.horizontals if s.pos > y1 + 4 and s.spans(cx)),
            default=self.height,
        )
        pad = 3
        return (max(0, left + pad), max(0, y1 + pad),
                min(self.width, right - pad), min(self.height, bottom - pad))

    def rules_above(self, cx: int, y: int) -> list[int]:
        """Horizontal rules above ``y`` crossing ``cx``, nearest first."""
        return sorted({s.pos for s in self.horizontals if s.pos < y - 2 and s.spans(cx)}, reverse=True)


def detect_grid(gray: np.ndarray) -> Grid:
    """Find ruled lines using directional morphology."""
    h, w = gray.shape
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]

    def collect(kernel, horizontal: bool, min_len: int) -> list[Segment]:
        mask = cv2.dilate(cv2.erode(binary, kernel), kernel)
        count, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
        out: list[Segment] = []
        for i in range(1, count):
            x, y, cw, ch, _area = stats[i]
            if horizontal and cw >= min_len and ch <= max(6, h // 100):
                out.append(Segment(pos=y + ch // 2, start=x, end=x + cw))
            elif not horizontal and ch >= min_len and cw <= max(6, w // 400):
                out.append(Segment(pos=x + cw // 2, start=y, end=y + ch))
        return out

    hor_k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, w // 120), 1))
    ver_k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, h // 70)))
    return Grid(
        horizontals=collect(hor_k, True, max(40, w // 90)),
        verticals=collect(ver_k, False, max(18, h // 55)),
        width=w,
        height=h,
    )


def _prep_cell(image: Image.Image, box: tuple[int, int, int, int], scale: int = 3,
               inset: float = 0.0) -> Image.Image | None:
    """Crop, optionally inset, and upscale a cell for OCR."""
    x0, y0, x1, y1 = box
    if x1 - x0 < 8 or y1 - y0 < 6:
        return None
    crop = image.crop((x0, y0, x1, y1))
    if inset:
        w, h = crop.size
        dx, dy = int(w * inset), int(h * inset)
        if w - 2 * dx > 6 and h - 2 * dy > 6:
            crop = crop.crop((dx, dy, w - dx, h - dy))
    if crop.width * scale > 4200:
        scale = max(1, 4200 // max(1, crop.width))
    return crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)


# A revision history row reads as '<rev> | <date> | <description>' once the
# ruled separators are OCR'd into pipes and stray strokes.
_ROW_RE = re.compile(
    r"^[^A-Z0-9]*(?P<rev>[A-Z]|\d{1,2})\s*[|\]\)\s]+\s*"
    r"(?P<date>\d{4}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{1,2})"
    r"\s*[|\]\)\s]*\s*(?P<desc>.+)$",
    re.I,
)
_DATE_ANYWHERE = re.compile(r"\d{4}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{1,2}")


class TitleBlockReader:
    """Extracts title block fields from a rendered bottom strip."""

    ANCHORS = {
        "drawing_number": ("DRAWING", "NUMBER"),
        "revision_description": ("REVISION", "DESCRIPTION"),
        "project_number": ("PROJECT", "NUMBER"),
        "design_engineer": ("DESIGN", "ENGINEER"),
        "drawing_check": ("DRAWING", "CHECK"),
        "design_check": ("DESIGN", "CHECK"),
        "project_approval": ("PROJECT", "APPROVAL"),
        "drawn_by": ("DRAWN/DESIGN",),
        "revision": ("REVISION",),
        "scale": ("SCALE",),
        "date": ("DATE",),
    }

    # Anchors are resolved in this order so that two-word labels claim their
    # words before the bare single-word labels are matched.
    ANCHOR_ORDER = (
        "revision_description", "drawing_number", "project_number",
        "design_engineer", "drawing_check", "design_check",
        "project_approval", "drawn_by", "revision", "scale", "date",
    )

    def __init__(self, ocr: OcrEngine):
        self.ocr = ocr

    # -- anchors ---------------------------------------------------------

    @staticmethod
    def _match_sequence(words: list[dict], start: int, sequence: tuple[str, ...],
                        used: set[int]) -> tuple[tuple[int, int, int, int], list[int]] | None:
        """Try to match ``sequence`` beginning at ``words[start]``."""
        first = words[start]
        box = [first["x0"], first["y0"], first["x1"], first["y1"]]
        indices = [start]
        for token in sequence[1:]:
            match = None
            for j, cand in enumerate(words):
                if j in used or j in indices:
                    continue
                if cand["text"].upper().strip(":.") != token:
                    continue
                same_line = abs(cand["y0"] - first["y0"]) <= max(8, first["h"])
                adjacent = 0 < cand["x0"] - box[2] < max(60, 6 * first["h"])
                if same_line and adjacent:
                    match = (j, cand)
                    break
            if not match:
                return None
            j, cand = match
            indices.append(j)
            box[2] = cand["x1"]
            box[3] = max(box[3], cand["y1"])
        return (box[0], box[1], box[2], box[3]), indices

    @classmethod
    def _find_all_anchors(cls, words: list[dict], sequence: tuple[str, ...],
                          used: set[int]) -> list[tuple[tuple[int, int, int, int], list[int]]]:
        """Every place ``sequence`` appears, so the caller can disambiguate.

        Engineering sheets repeat words like DATE and REVISION - in the
        permit stamp, the revision table header and the title block - so a
        first-match-wins search picks the wrong one. Returning all
        candidates lets the caller choose by position.
        """
        head = sequence[0]
        out = []
        for i, word in enumerate(words):
            if i in used or word["text"].upper().strip(":.") != head:
                continue
            match = cls._match_sequence(words, i, sequence, used)
            if match:
                out.append(match)
        return out

    @classmethod
    def _find_anchor(cls, words: list[dict], sequence: tuple[str, ...],
                     used: set[int] | None = None,
                     near: tuple[int, int] | None = None,
                     min_x: int | None = None) -> tuple[int, int, int, int] | None:
        """Locate a label, optionally preferring one near a reference point."""
        if used is None:
            used = set()
        candidates = cls._find_all_anchors(words, sequence, used)
        if min_x is not None:
            filtered = [c for c in candidates if c[0][0] >= min_x]
            candidates = filtered or candidates
        if not candidates:
            return None
        if near is not None:
            nx, ny = near
            # Row alignment dominates: labels on the same ruled row matter
            # far more than horizontal proximity.
            candidates.sort(key=lambda c: (abs(c[0][1] - ny) * 4 + abs(c[0][0] - nx)))
        box, indices = candidates[0]
        used.update(indices)
        return box

    # -- individual fields -----------------------------------------------

    # Cells are re-rendered from the page at this DPI before OCR. Well above
    # the overview DPI, because character-level accuracy on drawing numbers
    # depends on real detail rather than interpolation.
    CELL_DPI = 600

    def _cell_image(self, image, region, box, scale=3, inset=0.0, dpi=None):
        """Prefer a high-DPI re-render of the cell; fall back to upscaling."""
        if region is not None:
            rendered = region.render(box, dpi or self.CELL_DPI, inset=inset)
            if rendered is not None and rendered.width >= 12 and rendered.height >= 10:
                return rendered
        return _prep_cell(image, box, scale=scale, inset=inset)

    def _read_drawing_number(self, image, grid, anchor, region=None) -> tuple[str, float]:
        crop = self._cell_image(image, region, grid.cell_below(*anchor))
        if crop is None:
            return "", 0.0
        result = self.ocr.consensus(
            crop, psms=(13, 7, 6, 11), whitelist=WHITELIST_DRAWING_NO,
            cleaner=clean_drawing_number,
        )
        return result.text, result.confidence

    def _read_revision(self, image, grid, anchor, region=None) -> tuple[str, float]:
        """Read the revision, which is printed inside a triangle.

        The triangle strokes swamp OCR, so the glyph is first isolated by
        discarding the connected components that form the enclosure. Only if
        that fails does this fall back to insetting the crop.
        """
        box = grid.cell_below(*anchor)
        crop = self._cell_image(image, region, box, scale=5)
        if crop is not None:
            glyph = self.ocr.isolate_glyphs(crop)
            if glyph is not None:
                result = self.ocr.consensus(
                    glyph, psms=(10, 8, 7, 13), whitelist=WHITELIST_REVISION,
                    cleaner=normalise_revision, heights=(40, 56, 80),
                )
                if result.text:
                    return result.text, result.confidence

        best, best_conf = "", 0.0
        for inset in (0.26, 0.34, 0.18):
            fallback = self._cell_image(image, region, box, scale=5, inset=inset)
            if fallback is None:
                continue
            result = self.ocr.consensus(
                fallback, psms=(10, 8, 7), whitelist=WHITELIST_REVISION,
                cleaner=normalise_revision, heights=(40, 56, 80),
            )
            if result.text and result.confidence > best_conf:
                best, best_conf = result.text, result.confidence
            if best_conf >= 100.0:
                break
        return best, best_conf

    def _read_simple(self, image, grid, anchor, whitelist=None,
                     psms=(7, 6, 13), region=None) -> tuple[str, float]:
        """Read a plain text cell.

        ``whitelist`` is left unset for free text: Tesseract's config string
        is shell-split by pytesseract, so a whitelist containing a space is
        not safely expressible, and without the space the OCR would run the
        words together.
        """
        crop = self._cell_image(image, region, grid.cell_below(*anchor))
        if crop is None:
            return "", 0.0
        result = self.ocr.consensus(
            crop, psms=psms, whitelist=whitelist,
            cleaner=lambda s: re.sub(r"\s+", " ", s).strip(" |_-"),
        )
        return result.text, result.confidence

    # -- title -----------------------------------------------------------

    def _title_band(self, grid: Grid, anchors: dict) -> tuple[int, int, int] | None:
        """Return (left, top, bottom) of the title cell, or None.

        Anchored on the metadata header row rather than on font size, so a
        stamp elsewhere on the sheet cannot be mistaken for the title.
        """
        metadata = [a for k, a in anchors.items()
                    if a and k in ("drawn_by", "design_engineer", "project_number",
                                   "scale", "date", "drawing_check", "design_check")]
        if not metadata:
            return None
        header_y = min(a[1] for a in metadata)
        leftmost_x = min(a[0] for a in metadata)
        cx = sum((a[0] + a[2]) // 2 for a in metadata) // len(metadata)

        rules = grid.rules_above(cx, header_y)
        if not rules:
            return None
        bottom = rules[0]
        # Walk up to the outermost rule that keeps the cell a plausible height.
        top = bottom
        for rule in rules[1:]:
            if bottom - rule > grid.height * 0.45:
                break
            top = rule
        if bottom - top < 20:
            return None

        # Left edge: the nearest rule left of the metadata columns that spans
        # the full title band - i.e. the divider against the logo/notice pane.
        candidates = [s.pos for s in grid.verticals
                      if s.start <= top + 10 and s.end >= bottom - 10 and s.pos < leftmost_x - 10]
        left = max(candidates) if candidates else 0
        return left, top, bottom

    def _read_title(self, image, grid, anchors, region=None) -> tuple[list[str], str]:
        band = self._title_band(grid, anchors)
        if band is None:
            return [], ""
        left, top, bottom = band
        box = (left + 5, top + 4, grid.width - 4, bottom - 4)
        crop = self._cell_image(image, region, box, scale=2, dpi=400)
        if crop is None:
            return [], ""

        raw = self.ocr.text(crop, psm=6)
        # Sheet border grid letters and rule fragments land in the crop as
        # isolated bracket-like characters; drop them before assembling.
        lines = [re.sub(r"(?<=\s)[\[\]|{}<>](?=[A-Za-z])|[\[\]|{}<>]", "", l)
                 for l in raw.splitlines()]
        lines = [re.sub(r"\s+", " ", l).strip(" |_-") for l in lines]
        lines = [l for l in lines if len(l) >= 4 and len(re.findall(r"[A-Za-z]", l)) >= 3]

        noise = re.compile(
            r"^(DRAWING|REVISION|PROJECT|SCALE|DATE|NOTICE|ENBRIDGE|DRAWN|DESIGN|"
            r"REFERENCE|ENGINEERING\s+COMPANY|THIS\s+DRAWING)\b", re.I)
        lines = [l for l in lines if not noise.match(l)]
        if not lines:
            return [], ""

        # The facility/location line is set smaller and sits last.
        facility = ""
        if len(lines) > 1 and len(lines[-1]) > 6:
            facility = lines[-1]
            lines = lines[:-1]
        return lines, facility

    # -- revision history -------------------------------------------------

    def read_revision_history(self, image: Image.Image, grid: Grid,
                              anchor: tuple[int, int, int, int],
                              region: "RegionMap | None" = None) -> list[tuple[str, str, str]]:
        """Read the revision table one ruled row at a time.

        ``anchor`` is the 'REVISION DESCRIPTION' header box. Rows stack
        upward from it, and each is OCR'd as a single line, which is far
        more accurate than trying to segment the whole table at once.
        """
        x0, y0, x1, _y1 = anchor
        cx = (x0 + x1) // 2
        _left, right = grid.column_bounds(cx, y0 - 5)

        # The table starts two columns left of the description (revision,
        # then date), so walk left twice from the description column.
        table_left, _ = grid.column_bounds(cx, y0 - 5)
        for _ in range(2):
            nxt = max((s.pos for s in grid.verticals
                       if s.pos < table_left - 4 and s.spans(y0 - 5)), default=None)
            if nxt is None:
                break
            table_left = nxt

        rules = grid.rules_above(cx, y0)
        bounds = [y0 - 2] + rules
        rows: list[tuple[str, str, str]] = []
        max_row_height = image.height * 0.09

        for i in range(len(bounds) - 1):
            bottom, top = bounds[i], bounds[i + 1]
            height = bottom - top
            if height < 12 or height > max_row_height:
                continue
            crop = self._cell_image(
                image, region, (table_left + 3, top + 3, right - 3, bottom - 3),
                scale=2, dpi=450)
            if crop is None:
                continue
            text = self.ocr.text(crop, psm=7).strip()
            parsed = self._parse_history_row(text)
            if parsed:
                rows.append(parsed)
        return rows

    @staticmethod
    def _parse_history_row(text: str) -> tuple[str, str, str] | None:
        """Split one OCR'd table row into (revision, date, description)."""
        if not text or len(text) < 8:
            return None
        cleaned = re.sub(r"\s+", " ", text).strip()
        match = _ROW_RE.match(cleaned)
        if match:
            rev = normalise_revision(match.group("rev"))
            date = re.sub(r"\s+", "", match.group("date"))
            desc = match.group("desc").strip(" |_-")
        else:
            found = _DATE_ANYWHERE.search(cleaned)
            if not found:
                return None
            date = re.sub(r"\s+", "", found.group(0))
            head, desc = cleaned[:found.start()], cleaned[found.end():]
            desc = desc.strip(" |_-]),")
            rev = ""
            for token in re.findall(r"[A-Z0-9]+", head.upper()):
                candidate = normalise_revision(token)
                if candidate and len(candidate) <= 2:
                    rev = candidate
                    break
        # Strip the trailing initials columns (drawn/checked/approved).
        desc = re.sub(r"(\s+[|\[\]]?\s*[A-Z]{1,4}\.?){2,}\s*$", "", desc).strip(" |_-")
        if len(desc) < 3:
            return None
        return rev, date, desc

    # -- entry point ------------------------------------------------------

    def read(self, image: Image.Image, region: RegionMap | None = None) -> dict:
        """Extract every supported field from a rendered title block strip."""
        gray = np.array(image) if image.mode == "L" else np.array(image.convert("L"))
        grid = detect_grid(gray)
        words = self.ocr.words_multi(image, psms=ANCHOR_PSMS, min_conf=38)

        out: dict = {"confidences": {}}
        used: set[int] = set()
        anchors: dict[str, tuple[int, int, int, int] | None] = {}

        # Unambiguous multi-word labels first. These establish where the
        # title block actually is on the sheet.
        for key in ("revision_description", "drawing_number", "project_number",
                    "design_engineer", "drawing_check", "design_check",
                    "project_approval", "drawn_by"):
            anchors[key] = self._find_anchor(words, self.ANCHORS[key], used)

        # The metadata labels share one ruled row; its position disambiguates
        # the bare 'SCALE' and 'DATE' labels from identical words elsewhere
        # on the sheet, such as in the engineer's permit stamp.
        row = [anchors[k] for k in ("project_number", "design_engineer", "drawn_by")
               if anchors.get(k)]
        if row:
            row_y = sorted(a[1] for a in row)[len(row) // 2]
            row_x = max(a[2] for a in row)
            left_guard = min(a[0] for a in row) - grid.width // 12
        else:
            row_y = row_x = None
            left_guard = None

        for key in ("scale", "date"):
            anchors[key] = self._find_anchor(
                words, self.ANCHORS[key], used,
                near=(row_x, row_y) if row_y is not None else None,
                min_x=left_guard,
            )

        # The revision triangle sits on the drawing number's row, to its right.
        dn = anchors.get("drawing_number")
        anchors["revision"] = self._find_anchor(
            words, self.ANCHORS["revision"], used,
            near=(dn[2], dn[1]) if dn else None,
            min_x=dn[2] if dn else None,
        )

        if anchors["drawing_number"]:
            value, conf = self._read_drawing_number(
                image, grid, anchors["drawing_number"], region)
            out["drawing_number"], out["confidences"]["drawing_number"] = value, conf
        if anchors["revision"]:
            value, conf = self._read_revision(image, grid, anchors["revision"], region)
            out["revision"], out["confidences"]["revision"] = value, conf
        if anchors["date"]:
            value, conf = self._read_simple(
                image, grid, anchors["date"], WHITELIST_DATE, region=region)
            out["drawing_date"], out["confidences"]["drawing_date"] = value, conf
        if anchors["project_number"]:
            value, conf = self._read_simple(
                image, grid, anchors["project_number"], WHITELIST_DRAWING_NO, region=region)
            out["project_number"], out["confidences"]["project_number"] = value, conf
        for key in ("scale", "drawn_by", "design_engineer", "drawing_check",
                    "design_check", "project_approval"):
            if anchors.get(key):
                value, conf = self._read_simple(image, grid, anchors[key], region=region)
                out[key], out["confidences"][key] = value, conf

        title_lines, facility = self._read_title(image, grid, anchors, region)
        out["title_lines"] = title_lines
        out["facility"] = facility

        if anchors["revision_description"]:
            out["revision_history"] = self.read_revision_history(
                image, grid, anchors["revision_description"], region
            )
        return out


def render_region(page, region: tuple[float, float, float, float],
                  dpi: int) -> tuple[Image.Image, RegionMap]:
    """Render a fractional region of a page, with its coordinate mapping."""
    import fitz

    rect = page.rect
    origin_x = rect.x0 + rect.width * region[0]
    origin_y = rect.y0 + rect.height * region[1]
    clip = fitz.Rect(
        origin_x, origin_y,
        rect.x0 + rect.width * region[2],
        rect.y0 + rect.height * region[3],
    )
    pix = page.get_pixmap(clip=clip, dpi=dpi)
    image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    return image, RegionMap(page=page, origin_x=origin_x, origin_y=origin_y,
                            scale=72.0 / dpi)
