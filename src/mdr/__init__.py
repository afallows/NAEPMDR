"""Enbridge Master Document Register.

Recursively scans folders of drawing PDFs, reads each title block with OCR
(or the companion DWG's attributes when available), and writes a formatted
Excel master document register with hyperlinks back to the source files.
"""

__version__ = "1.0.0"
