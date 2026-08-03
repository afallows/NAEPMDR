"""Enbridge brand theme.

Single source of truth for colour and typography used by both the Excel
register and the desktop GUI. Change the values here and everything
downstream follows.
"""

from __future__ import annotations

from dataclasses import dataclass


# --- Core brand palette -------------------------------------------------
# Hex without the leading '#'. openpyxl wants 'RRGGBB' or 'AARRGGBB';
# tkinter wants '#RRGGBB'. Use the helpers at the bottom to convert.

ENBRIDGE_GOLD = "FFB81C"        # primary accent - header bars, key rules
ENBRIDGE_DARK_GREY = "53565A"   # primary text, table headers
ENBRIDGE_BLACK = "1A1A1A"
WHITE = "FFFFFF"

# Supporting greys for banding, borders and secondary text.
GREY_100 = "F2F2F2"             # zebra banding / panel fill
GREY_200 = "E3E4E5"             # subtle borders
GREY_400 = "A7A9AC"             # grid lines, disabled text
GREY_600 = "6D6E71"             # secondary text

# Status accents. Deliberately desaturated so the gold stays dominant.
STATUS_COLOURS = {
    "IFC": "1E7B4F",            # issued for construction - green
    "IFB": "0B5FA5",            # issued for bid - blue
    "IFR": "8A6A00",            # issued for review - amber/dark gold
    "IFA": "6B4E9B",            # issued for approval - purple
    "IFI": "0B7C8C",            # issued for information - teal
    "IFD": "9B5A00",            # issued for design - orange
    "IFDM": "8C3A6B",           # issued for demolition - magenta
    "IFP": "7A5C00",            # issued for permit
    "IFQ": "4A6572",            # issued for quotation
    "AS-BUILT": "B23A1E",       # as-built - rust
    "VOID": "9E2B25",
    "PRELIMINARY": "6D6E71",
}

# QC severity accents.
QC_OK = "1E7B4F"
QC_WARN = "8A6A00"
QC_ERROR = "B23A1E"

# --- Typography ---------------------------------------------------------

FONT_NAME = "Segoe UI"          # Windows default; falls back gracefully
FONT_NAME_MONO = "Consolas"

TITLE_SIZE = 16
SUBTITLE_SIZE = 10
HEADER_SIZE = 10
BODY_SIZE = 10


@dataclass(frozen=True)
class GuiTheme:
    """Colours for the tkinter desktop UI, pre-formatted with '#'."""

    gold: str = f"#{ENBRIDGE_GOLD}"
    dark: str = f"#{ENBRIDGE_DARK_GREY}"
    black: str = f"#{ENBRIDGE_BLACK}"
    white: str = f"#{WHITE}"
    panel: str = f"#{GREY_100}"
    border: str = f"#{GREY_200}"
    muted: str = f"#{GREY_600}"
    ok: str = f"#{QC_OK}"
    warn: str = f"#{QC_WARN}"
    error: str = f"#{QC_ERROR}"
    font: str = FONT_NAME
    mono: str = FONT_NAME_MONO


GUI = GuiTheme()


def hexrgb(value: str) -> str:
    """Return a '#RRGGBB' string for tkinter from a bare 'RRGGBB' value."""
    return value if value.startswith("#") else f"#{value}"


def status_colour(status: str | None) -> str:
    """Return the accent colour for a normalised status code."""
    if not status:
        return GREY_600
    return STATUS_COLOURS.get(status.upper(), GREY_600)
