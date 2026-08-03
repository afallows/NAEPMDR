"""Tesseract wrapper: engine discovery, cell OCR and multi-PSM consensus.

Title block cells are short, high-contrast and often boxed, which is the
worst case for a single Tesseract page-segmentation mode. Reading each cell
under several modes and voting on the result measurably beats any one mode
on the drawings in this corpus.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

try:
    import pytesseract
except ImportError:  # pragma: no cover - dependency is declared in requirements
    pytesseract = None


ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
WHITELIST_DRAWING_NO = ALNUM + "-./"
WHITELIST_REVISION = ALNUM
WHITELIST_DATE = "0123456789-/."

# There is deliberately no whitelist for free text. pytesseract shell-splits
# the config string, so a whitelist containing a space cannot be expressed
# safely - and a whitelist without a space makes Tesseract run the words of a
# title together. Free-text cells are read with no whitelist at all.

_CONFIG_HOSTILE = set("'\" \t\n\\")


def sanitise_whitelist(whitelist: str) -> str:
    """Strip characters that would break shlex parsing of the config string.

    A quote in the whitelist raises "No closing quotation" from shlex before
    Tesseract is ever reached, so this is a hard safety net rather than a
    nicety.
    """
    return "".join(c for c in whitelist if c not in _CONFIG_HOSTILE)


# Tesseract can take pathologically long on some inputs - a dense, noisy
# region can pin a core for many minutes on a single call. Every invocation
# is bounded so one bad page cannot stall an unattended scan of thousands of
# drawings. A timed-out read simply contributes nothing to the vote.
CELL_TIMEOUT_SECONDS = 20
PAGE_TIMEOUT_SECONDS = 90

# Tesseract gains nothing from very large inputs and slows down sharply.
MAX_PIXELS = 40_000_000


class OcrUnavailable(RuntimeError):
    """Raised when no usable Tesseract binary can be located."""


def _bundled_candidates() -> list[Path]:
    """Locations checked before falling back to PATH.

    Covers both a PyInstaller one-folder build (``sys._MEIPASS``) and a
    normal source checkout, so a bundled Tesseract is preferred over any
    system install and the .exe stays self-contained.
    """
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots.append(Path(sys.executable).parent)
    roots.append(Path(__file__).resolve().parents[2])
    names = ["tesseract.exe", "tesseract"]
    out = []
    for root in roots:
        for sub in ("", "tesseract", "vendor/tesseract", "_internal/tesseract"):
            for name in names:
                out.append(root / sub / name if sub else root / name)
    return out


def locate_tesseract(explicit: str | None = None) -> str:
    """Return a usable Tesseract executable path.

    Order: explicit setting, bundled copy, PATH, then the standard Windows
    install locations.
    """
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p)

    for candidate in _bundled_candidates():
        if candidate.is_file():
            return str(candidate)

    found = shutil.which("tesseract")
    if found:
        return found

    for guess in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ):
        if guess and Path(guess).is_file():
            return guess

    raise OcrUnavailable(
        "Tesseract OCR was not found. Install it from "
        "https://github.com/UB-Mannheim/tesseract/wiki or set the path in Settings."
    )


@dataclass
class OcrResult:
    text: str
    confidence: float


class OcrEngine:
    """Thin, configured facade over pytesseract."""

    def __init__(self, tesseract_path: str | None = None, language: str = "eng"):
        if pytesseract is None:
            raise OcrUnavailable("pytesseract is not installed.")
        self.exe = locate_tesseract(tesseract_path)
        pytesseract.pytesseract.tesseract_cmd = self.exe
        self.language = language
        # A bundled Tesseract needs TESSDATA_PREFIX pointed at its data dir.
        tessdata = Path(self.exe).parent / "tessdata"
        if tessdata.is_dir():
            os.environ.setdefault("TESSDATA_PREFIX", str(tessdata.parent))

    def version(self) -> str:
        try:
            return str(pytesseract.get_tesseract_version())
        except Exception as exc:  # pragma: no cover - environment dependent
            return f"unknown ({exc})"

    # -- Low level -------------------------------------------------------

    def _config(self, psm: int, whitelist: str | None) -> str:
        cfg = f"--oem 3 --psm {psm}"
        if whitelist:
            safe = sanitise_whitelist(whitelist)
            if safe:
                cfg += f" -c tessedit_char_whitelist={safe}"
        # Preserve inter-word spaces so multi-word titles survive intact.
        cfg += " -c preserve_interword_spaces=1"
        return cfg

    @staticmethod
    def _guard_size(image: Image.Image) -> tuple[Image.Image, float]:
        """Downscale anything large enough to make Tesseract crawl.

        Returns the image and the factor applied, so callers that need
        coordinates can map them back to the original frame.
        """
        pixels = image.width * image.height
        if pixels <= MAX_PIXELS:
            return image, 1.0
        factor = (MAX_PIXELS / pixels) ** 0.5
        resized = image.resize(
            (max(1, int(image.width * factor)), max(1, int(image.height * factor))),
            Image.LANCZOS,
        )
        return resized, factor

    def text(self, image: Image.Image, psm: int = 6, whitelist: str | None = None,
             timeout: int = CELL_TIMEOUT_SECONDS) -> str:
        try:
            guarded, _factor = self._guard_size(image)
            return pytesseract.image_to_string(
                guarded, lang=self.language,
                config=self._config(psm, whitelist), timeout=timeout,
            ).strip()
        except RuntimeError:
            # pytesseract raises RuntimeError on timeout; an empty read is
            # the right answer here - the caller votes across other reads.
            return ""

    def words_multi(
        self,
        image: Image.Image,
        psms: tuple[int, ...] = (4, 11, 6),
        min_conf: int = 40,
    ) -> list[dict]:
        """Merge word boxes detected under several page-segmentation modes.

        No single mode reliably finds both the large title text and the
        small boxed labels on an engineering title block - psm 4 excels at
        the ruled label grid while psm 11 picks up isolated large text.
        Merging the two and de-duplicating by position gives far better
        anchor recall than any mode alone.
        """
        merged: list[dict] = []
        for psm in psms:
            try:
                found = self.words(image, psm=psm, min_conf=min_conf)
            except Exception:
                continue
            for word in found:
                duplicate = False
                for existing in merged:
                    # Same token at essentially the same place.
                    if (
                        existing["text"].upper() == word["text"].upper()
                        and abs(existing["x0"] - word["x0"]) < max(12, word["h"])
                        and abs(existing["y0"] - word["y0"]) < max(12, word["h"])
                    ):
                        if word["conf"] > existing["conf"]:
                            existing.update(word)
                        duplicate = True
                        break
                if not duplicate:
                    merged.append(word)
        merged.sort(key=lambda w: (w["y0"], w["x0"]))
        return merged

    def words(self, image: Image.Image, psm: int = 11, min_conf: int = 45,
              timeout: int = PAGE_TIMEOUT_SECONDS) -> list[dict]:
        """Return word boxes: {text, x0, y0, x1, y1, conf}."""
        guarded, factor = self._guard_size(image)
        try:
            data = pytesseract.image_to_data(
                guarded,
                lang=self.language,
                config=self._config(psm, None),
                output_type=pytesseract.Output.DICT,
                timeout=timeout,
            )
        except RuntimeError:
            return []
        out = []
        for i, raw in enumerate(data["text"]):
            token = raw.strip()
            if not token:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                continue
            if conf < min_conf:
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            if factor != 1.0:
                # Map back to the caller's coordinate frame, which the grid
                # and every cell lookup are expressed in.
                inverse = 1.0 / factor
                x, y, w, h = (int(x * inverse), int(y * inverse),
                              int(w * inverse), int(h * inverse))
            out.append({
                "text": token, "x0": x, "y0": y, "x1": x + w, "y1": y + h,
                "conf": conf, "h": h, "w": w,
            })
        return out

    # -- Consensus -------------------------------------------------------

    # Tesseract is trained on text roughly this tall. Cells rendered from a
    # PDF at 600 DPI are many times larger, and feeding it oversized glyphs
    # is a measurable accuracy loss on CAD stroke fonts - the flat-topped
    # '3' in particular gets read as '5'. Normalising to a few heights
    # around the trained size and voting fixes it.
    NORMALISE_HEIGHTS = (40, 48, 64, 88)

    @staticmethod
    def normalise(image: Image.Image, height: int) -> Image.Image:
        """Binarise, trim to the ink, scale to ``height`` and pad with white."""
        array = np.array(image.convert("L"))
        binary = cv2.threshold(array, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
        rows, cols = np.where(binary < 128)
        if len(rows):
            binary = binary[
                max(0, rows.min() - 2):rows.max() + 3,
                max(0, cols.min() - 2):cols.max() + 3,
            ]
        trimmed = Image.fromarray(binary)
        if trimmed.height < 2:
            return image
        width = max(8, int(trimmed.width * height / trimmed.height))
        width = min(width, 3200)
        resized = trimmed.resize((width, height), Image.LANCZOS)
        return ImageOps.expand(resized, border=max(12, height // 3), fill=255)

    @staticmethod
    def isolate_glyphs(image: Image.Image, max_extent: float = 0.62) -> Image.Image | None:
        """Strip enclosing shapes and return just the characters inside.

        Revision numbers on these sheets are printed inside a large
        triangle. The triangle is a single connected component that spans
        most of the cell and touches its edges, while the character is a
        compact component in the middle - so discarding border-touching and
        oversized components leaves the glyph alone.
        """
        array = np.array(image.convert("L"))
        binary = cv2.threshold(array, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
        height, width = binary.shape
        count, labels, stats, _cent = cv2.connectedComponentsWithStats(binary, 8)
        if count <= 1:
            return None

        keep = []
        for i in range(1, count):
            x, y, w, h, area = stats[i]
            if w > width * max_extent or h > height * max_extent:
                continue                       # the enclosing triangle/box
            if x <= 1 or y <= 1 or x + w >= width - 1 or y + h >= height - 1:
                continue                       # clipped by the cell edge
            if area < (width * height) * 0.001:
                continue                       # speckle
            keep.append((i, x, y, w, h))
        if not keep:
            return None

        mask = np.zeros_like(binary)
        for i, *_ in keep:
            mask[labels == i] = 255
        x0 = max(0, min(k[1] for k in keep) - 4)
        y0 = max(0, min(k[2] for k in keep) - 4)
        x1 = min(width, max(k[1] + k[3] for k in keep) + 4)
        y1 = min(height, max(k[2] + k[4] for k in keep) + 4)
        if x1 - x0 < 4 or y1 - y0 < 6:
            return None
        # Back to black-on-white for Tesseract.
        return Image.fromarray(255 - mask[y0:y1, x0:x1])

    def consensus(
        self,
        image: Image.Image,
        psms: tuple[int, ...] = (7, 8, 13),
        whitelist: str | None = None,
        cleaner=None,
        heights: tuple[int, ...] | None = None,
        min_agreement: int = 3,
    ) -> OcrResult:
        """OCR one cell at several scales and page-segmentation modes, then vote.

        ``cleaner`` is applied before voting so cosmetic differences (stray
        dashes, case) do not split the vote. Confidence is the share of
        readings that agreed, which is a far better signal for a short boxed
        cell than Tesseract's own per-character confidence.

        Each reading costs a Tesseract process launch, so the loop exits as
        soon as ``min_agreement`` readings agree unanimously - the common
        case for a clean cell, and the difference between roughly 3 and 12
        subprocess spawns per field.
        """
        scales = heights if heights is not None else self.NORMALISE_HEIGHTS
        variants: dict[int, Image.Image] = {}
        readings: list[str] = []
        attempts = 0
        counts: Counter[str] = Counter()

        for height in scales:
            if height not in variants:
                try:
                    variants[height] = self.normalise(image, height)
                except Exception:
                    continue
            variant = variants[height]
            for psm in psms:
                attempts += 1
                try:
                    raw = self.text(variant, psm=psm, whitelist=whitelist)
                except Exception:
                    continue
                value = cleaner(raw) if cleaner else raw.strip()
                if value:
                    readings.append(value)
                    counts[value] += 1
            # Unanimous so far, and enough evidence: stop early.
            if len(readings) >= min_agreement and len(counts) == 1:
                break

        if not readings:
            return OcrResult("", 0.0)
        best, hits = counts.most_common(1)[0]
        return OcrResult(best, round(100.0 * hits / max(1, len(readings)), 1))
