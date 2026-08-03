"""Text normalisation: statuses, revisions, dates, drawing numbers, disciplines.

Everything here is pure string handling so it can be unit tested without a
PDF, an OCR engine or AutoCAD.
"""

from __future__ import annotations

import re
from datetime import datetime

# --- Status ------------------------------------------------------------
# Ordered longest/most specific first: the first pattern that matches the
# revision description wins.
STATUS_PATTERNS: list[tuple[str, str]] = [
    ("AS-BUILT", r"\bAS[\s\-_]?BUILTS?\b"),
    ("AS-BUILT", r"\bRECORD\s+DRAWING\b"),
    ("VOID", r"\bVOID(ED)?\b"),
    ("VOID", r"\bCANCELL?ED\b"),
    ("VOID", r"\bSUPERS[EC]?EDED\b"),
    ("IFDM", r"\bISSUED?\s+FOR\s+DEMOLITION\b"),
    ("IFDM", r"\bFOR\s+DEMOLITION\b"),
    ("IFDM", r"\bIFDE?M\b"),
    ("IFC", r"\bISSUED?\s+FOR\s+CONSTRUCTION\b"),
    ("IFC", r"\bFOR\s+CONSTRUCTION\b"),
    ("IFC", r"\bIFC\b"),
    ("IFB", r"\bISSUED?\s+FOR\s+BID\b"),
    ("IFB", r"\bRE[\s\-]?ISSUED?\s+FOR\s+BID\b"),
    ("IFB", r"\bFOR\s+BID\b"),
    ("IFB", r"\bISSUED?\s+FOR\s+TENDER\b"),
    ("IFB", r"\bIFB\b"),
    ("IFB", r"\bIFT\b"),
    ("IFR", r"\bISSUED?\s+FOR\s+REVIEW\b"),
    ("IFR", r"\bFOR\s+REVIEW\b"),
    ("IFR", r"\bIFR\b"),
    ("IFA", r"\bISSUED?\s+FOR\s+APPROVAL\b"),
    ("IFA", r"\bFOR\s+APPROVAL\b"),
    ("IFA", r"\bIFA\b"),
    ("IFI", r"\bISSUED?\s+FOR\s+INFORMATION\b"),
    ("IFI", r"\bFOR\s+INFORMATION\b"),
    ("IFI", r"\bIFI\b"),
    ("IFD", r"\bISSUED?\s+FOR\s+DESIGN\b"),
    ("IFD", r"\bIFD\b"),
    ("IFP", r"\bISSUED?\s+FOR\s+PERMIT\b"),
    ("IFP", r"\bFOR\s+PERMIT\b"),
    ("IFP", r"\bIFP\b"),
    ("IFQ", r"\bISSUED?\s+FOR\s+QUOTATION\b"),
    ("IFQ", r"\bFOR\s+QUOT(E|ATION)\b"),
    ("IFQ", r"\bIFQ\b"),
    ("PRELIMINARY", r"\bPRELIM(INARY)?\b"),
    ("PRELIMINARY", r"\bDRAFT\b"),
]

_COMPILED_STATUS = [(code, re.compile(pat, re.I)) for code, pat in STATUS_PATTERNS]


def normalise_status(description: str) -> str:
    """Map a free-text revision description onto a controlled status code.

    Returns an empty string when nothing matches, so the caller can flag the
    row for manual review rather than silently inventing a status.
    """
    if not description:
        return ""
    text = description.upper()
    # OCR frequently turns hyphens into em/en dashes.
    text = text.replace("—", "-").replace("–", "-")
    for code, pattern in _COMPILED_STATUS:
        if pattern.search(text):
            return code
    return ""


# --- Discipline --------------------------------------------------------
# Keyed on the discipline token found inside an Enbridge-style drawing
# number (e.g. ACGS-EL-6001-06 -> "EL").
DISCIPLINE_CODES = {
    "EL": "Electrical",
    "ELE": "Electrical",
    "E": "Electrical",
    "IN": "Instrumentation",
    "INS": "Instrumentation",
    "I": "Instrumentation",
    "IC": "Instrumentation & Controls",
    "ME": "Mechanical",
    "MEC": "Mechanical",
    "M": "Mechanical",
    "PI": "Piping",
    "PIP": "Piping",
    "P": "Piping",
    "PR": "Process",
    "PRO": "Process",
    "CI": "Civil",
    "CIV": "Civil",
    "C": "Civil",
    "ST": "Structural",
    "STR": "Structural",
    "S": "Structural",
    "AR": "Architectural",
    "GE": "General",
    "GEN": "General",
    "SA": "Safety",
    "TE": "Telecom",
}


def infer_discipline(drawing_number: str) -> str:
    """Infer a discipline from the tokens of a drawing number."""
    if not drawing_number:
        return ""
    tokens = re.split(r"[-_\s]+", drawing_number.upper())
    for token in tokens:
        if token.isdigit():
            continue
        if token in DISCIPLINE_CODES:
            return DISCIPLINE_CODES[token]
    return ""


# --- Revision ----------------------------------------------------------

_REV_CLEAN = re.compile(r"[^A-Z0-9]")


def normalise_revision(raw: str) -> str:
    """Clean an OCR'd revision token down to its alphanumeric core.

    Handles the common OCR artefacts around revision triangles, where the
    triangle strokes get read as extra characters.
    """
    if not raw:
        return ""
    text = _REV_CLEAN.sub("", raw.upper())
    if not text:
        return ""
    # Revision letter sequences conventionally omit I, O and Q precisely
    # because they are indistinguishable from digits in drafting fonts, so a
    # lone 'O' or 'I' is a misread zero or one rather than a revision letter.
    if text == "O":
        return "0"
    if text == "I":
        return "1"
    # A revision is a letter, a number, or a short combination (e.g. "0A").
    if len(text) <= 3:
        return text
    # Longer means the triangle bled in; keep the trailing alphanumeric run.
    match = re.search(r"([A-Z]?\d{1,2}|[A-Z]{1,2})$", text)
    return match.group(1) if match else text[:3]


# --- Dates -------------------------------------------------------------

_DATE_FORMATS = [
    "%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d",
    "%d/%m/%Y", "%d-%m-%Y",
    "%m/%d/%Y", "%m-%d-%Y",
    "%Y/%m", "%Y-%m",
    "%d-%b-%Y", "%d %b %Y", "%b %d %Y", "%b %d, %Y",
    "%Y%m%d",
]


def normalise_date(raw: str) -> str:
    """Return an ISO 'YYYY-MM-DD' date, or '' when unparseable.

    OCR routinely mangles separators, so digits are recovered first and the
    known formats tried against the cleaned string.
    """
    if not raw:
        return ""
    text = raw.strip()
    text = re.sub(r"[^0-9A-Za-z/\-.\s,]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Last resort: pull three numeric groups and assume the largest is a year.
    groups = re.findall(r"\d+", text)
    if len(groups) >= 3:
        a, b, c = groups[0], groups[1], groups[2]
        if len(a) == 4:
            y, m, d = a, b, c
        elif len(c) == 4:
            y, m, d = c, b, a
        else:
            return ""
        try:
            return datetime(int(y), int(m), int(d)).strftime("%Y-%m-%d")
        except ValueError:
            return ""
    return ""


# --- Drawing numbers ---------------------------------------------------

_DWG_CLEAN_MAP = str.maketrans({
    "—": "-", "–": "-", "−": "-", "­": "-",
    "‘": "", "’": "", "“": "", "”": "",
})


def clean_drawing_number(raw: str) -> str:
    """Normalise an OCR'd drawing number.

    Collapses the dash variants OCR produces, strips stray leading/trailing
    punctuation, and removes internal whitespace.
    """
    if not raw:
        return ""
    text = raw.translate(_DWG_CLEAN_MAP).upper()
    text = re.sub(r"[^A-Z0-9\-/.]+", "", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-./ ")


# Filenames in the sample corpus follow '<DRAWING-NUMBER>_R<REV>', e.g.
# 'PP-1-94000-5535-05_RB' or 'ACGS-EL-6001-06_R3'.
_FILENAME_REV = re.compile(r"^(?P<number>.+?)[_\-\s]+R(?:EV)?[._\-\s]?(?P<rev>[A-Z]?\d{1,2}|[A-Z]{1,2})$", re.I)


def parse_filename(stem: str) -> tuple[str, str]:
    """Split a file stem into (drawing_number, revision).

    Returns the whole stem as the drawing number and an empty revision when
    no revision suffix is recognised.
    """
    if not stem:
        return "", ""
    text = stem.strip()
    match = _FILENAME_REV.match(text)
    if match:
        return clean_drawing_number(match.group("number")), match.group("rev").upper()
    return clean_drawing_number(text), ""


def compare_numbers(a: str, b: str) -> bool:
    """Compare two drawing numbers ignoring separators and case.

    OCR and file-naming conventions differ on hyphens and spaces, so those
    are not treated as meaningful differences.
    """
    norm = lambda s: re.sub(r"[^A-Z0-9]", "", (s or "").upper())
    return bool(norm(a)) and norm(a) == norm(b)
