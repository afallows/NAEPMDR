# Enbridge Master Document Register

A standalone Windows application that recursively scans folders of drawing
PDFs, reads each title block with OCR (or the companion DWG's attributes
where available), and writes a formatted Excel master document register with
a clickable link on every row.

Built for Enbridge / Gas Liquids Engineering title blocks, and calibrated
against Aitken Creek EHT and P&ID drawing sets.

---

## What it produces

A single `.xlsx` with four sheets:

| Sheet | Contents |
| --- | --- |
| **Document Register** | One row per sheet. Drawing number, revision, status, title, and an `Open` hyperlink to the PDF. Filterable, frozen panes, banded rows. |
| **Summary** | Counts by status and discipline, unique drawing numbers, mean extraction confidence, QC totals. |
| **QC Review** | Only the rows needing a human check, with the reason. This is the worklist. |
| **Revision History** | Every revision row read from every drawing's revision table. |

### Register columns

Identity first, then metadata, then provenance:

`# · Open · Drawing Number · Rev · Status · Latest Revision Description ·
Rev Date · Title · Facility/Location · Discipline · Sheet · Sheets ·
Project No. · Scale · Drawing Date · Drawn/Design · Drawing Check · Design
Engineer · Design Check · Project Approval · File Name · Folder · Type · DWG
Available · Source · Confidence · QC Flags · Size · File Modified · Full Path`

---

## Install and run

### Option A - the built application (no Python needed)

Run `EnbridgeMDR.exe` from the `dist\EnbridgeMDR` folder. Distribute the
**whole folder**, not just the `.exe`.

If the build bundled Tesseract, nothing else is required. Otherwise install
[Tesseract for Windows](https://github.com/UB-Mannheim/tesseract/wiki) once.

### Option B - from source

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python run_gui.py
```

### Building the .exe

```powershell
powershell -ExecutionPolicy Bypass -File build\build.ps1 -BundleTesseract
```

`-BundleTesseract` copies an installed Tesseract into the distribution so
the result runs on a machine with nothing pre-installed. Omit it for a
smaller build that relies on Tesseract being installed separately.

---

## Command line

```
EnbridgeMDR-cli.exe "D:\Projects\Aitken Creek\Drawings" -o register.xlsx
```

| Flag | Effect |
| --- | --- |
| `-o, --output` | Register path. Defaults to a timestamped file in the scanned folder. |
| `--no-recursive` | Top-level folder only. |
| `--first-page-only` | One row per file instead of one per sheet. Much faster. |
| `--no-dwg` | Ignore companion DWGs; OCR only. |
| `--dpi` | Overview render DPI (default 300). |
| `--workers` | Parallel workers; `0` = auto (CPU count - 1). |
| `--absolute-links` | Absolute rather than relative hyperlinks. |
| `--exclude NAME` | Folder name to skip. Repeatable. |
| `--tesseract`, `--oda` | Explicit tool paths. |

---

## How extraction works

Drawing PDFs plotted from AutoCAD carry no useful text layer - the sample
corpus has 24 characters per sheet, all of it a stamp - so the title block
has to be read optically. The pipeline:

1. **Render the bottom strip** of the sheet (the title block and revision
   table both live there) at 300 DPI.
2. **Detect the ruled grid** with directional morphology. The rules are
   *segments*, not full-width lines, so they are collected as connected
   components and queried by the span they cover.
3. **Locate anchor labels** - `DRAWING NUMBER`, `REVISION DESCRIPTION`,
   `PROJECT NUMBER` and so on - by OCR'ing the strip at word level under
   several page-segmentation modes and merging the results.
4. **Read each value from the cell beneath its own printed label**, after
   re-rendering that cell from the vector page at 600 DPI.
5. **Read the revision table one ruled row at a time**, which turns a dense
   multi-column table into a series of clean single-line reads.

Because every field is found relative to its own label, the reader survives
different sheet sizes and title block scales without any hardcoded
coordinates. It has been checked against two different drawing types (EHT
schematics/isometrics and P&IDs) sharing the same title block template.

### Things that turned out to matter

- **Downscaling improves accuracy.** Tesseract is trained on text about
  40-50 px tall. Feeding it a 600 DPI cell makes the CAD stroke font's
  flat-topped `3` read as `5`. Each cell is normalised to several heights
  around the trained size and the readings are voted on.
- **Revision triangles must be removed, not cropped around.** The revision
  sits inside a triangle whose strokes swamp OCR. The enclosure is stripped
  by discarding connected components that span most of the cell or touch its
  edges, leaving the glyph alone.
- **Duplicate labels need positional disambiguation.** `DATE` and `REVISION`
  appear in the permit stamp, the revision table header and the title block.
  Anchors are chosen by row alignment with the labels around them.
- **The revision triangle is less reliable than the revision table.** Where
  the two disagree, the newest revision-history row wins and the row is
  flagged.

---

## Measured accuracy

Against the 31-sheet calibration set (28 EHT schematics and isometrics, 3
P&IDs), scored by opening each drawing rather than by trusting its filename:

| Field | Correct |
| --- | --- |
| Drawing number | 31 / 31 |
| Revision | 31 / 31 |
| Status | 31 / 31 |
| Title | 30 / 31 |

Two results are worth understanding rather than reading as a score:

- `PID2206405001007_R5.pdf` is **Revision 4** on the sheet - both its
  revision triangle and the newest row of its revision table read 4. The
  file is misnamed. The register reports 4 and flags the disagreement, which
  is the tool working, not failing.
- Title text is read at word level and occasionally substitutes a character
  in the CAD stroke font (`HTCP-94034` read as `HICP-94034`). Drawing
  number, revision and status are read cell-by-cell with multi-scale voting
  and are not subject to this.

Accuracy on a different title block template will differ. The QC Review
sheet is the control: it lists every row the tool is not confident about,
and on this set it correctly flagged the one misnamed file.

---

## Conflict handling and QC

The title block is authoritative. The filename and the companion DWG are
used as cross-checks, and any disagreement raises a QC flag rather than
being silently resolved:

| Flag | Meaning |
| --- | --- |
| `Drawing number mismatch - title block 'X' vs filename 'Y'` | Usually a misnamed file. |
| `Revision mismatch - title block 'X' vs filename 'Y'` | Same. |
| `Revision triangle read 'X' but the revision history ends at 'Y' - history used` | Triangle OCR overruled. |
| `DWG/PDF revision differ ...` | The PDF may be published from a stale DWG. |
| `Status could not be determined ...` | The revision description matched no known code. |
| `Drawing number/Title not readable` | Title block could not be read; check the sheet. |
| `Low OCR confidence (n%)` | The multi-scale vote did not converge. |
| `Drawing date not parseable ...` | The date cell was read but is not a valid date. The raw text is kept. |
| `Companion DWG not used - the PDF has n sheets ...` | A DWG describes one drawing, so it is not applied across a multi-sheet set. |
| `Only the first n of m sheets were read ...` | The `max_pages_per_file` cap was hit. The register is incomplete for that file. |
| `Hyperlink may not open - '#' in the file path ...` | Excel treats `#` as a fragment separator; rename the file or folder. |

Confidence is the share of independent readings that agreed, which is a
better signal on a short boxed cell than Tesseract's own per-character
confidence. It is colour-coded in the register: green above 80%, amber
60-80%, red below 60%.

Comparison ignores separators, so the P&ID convention of printing
`PID-22064-050-010-04` in the title block while naming the file
`PID2206405001004_R3` is correctly treated as a match.

---

## Status codes

Derived from the newest revision description, with the verbatim text kept
alongside so any mapping can be audited.

`IFC` construction · `IFB` bid/tender · `IFR` review · `IFA` approval ·
`IFI` information · `IFD` design · `IFDM` demolition · `IFP` permit ·
`IFQ` quotation · `AS-BUILT` · `PRELIMINARY` · `VOID`

Unrecognised descriptions leave Status blank and raise a QC flag rather than
guessing. Add new patterns to `STATUS_PATTERNS` in `src/mdr/parse.py`.

---

## DWG support

Where a `.dwg` sits beside a `.pdf` with the same stem, the app can read the
title block attributes exactly, with no OCR risk, and flags any field where
the DWG and the PDF disagree.

This needs the free
[ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)
installed - no pure-Python library reads DWG. The app finds it
automatically and **falls back to OCR when it is absent**, so it is
optional.

> The DWG path is implemented but has not been run against a real DWG, as
> the converter is Windows-only. The attribute tag mapping in
> `src/mdr/dwg_source.py` (`ATTRIBUTE_MAP`) may need adjusting to match your
> title block's ATTRIB tags on first use.

---

## Performance

Measured at roughly 10 seconds per sheet on one core - 0.4 s to render the
title block strip, 2.8 s to locate the anchor labels, and the remainder
reading and voting on the individual cells. Work is spread across processes
(`CPU count - 1` by default), so an 8-core workstation manages in the region
of 2,500 sheets per hour.

To go faster: `--first-page-only` on single-sheet drawing sets, or drop
`--dpi` to 200. Both trade accuracy for speed.

---

## Configuration

Settings persist to `%APPDATA%\EnbridgeMDR\settings.json`.

Brand colours and fonts live in one place - `src/mdr/theme.py` - and drive
both the workbook and the desktop UI, so they cannot drift apart.

---

## Layout

```
src/mdr/
  theme.py        Enbridge palette and typography - single source of truth
  models.py       DocumentRecord: one register row
  parse.py        Status/revision/date/drawing-number normalisation (pure)
  ocr.py          Tesseract discovery, normalisation, multi-scale voting
  titleblock.py   Grid detection, anchor location, field extraction
  dwg_source.py   ODA File Converter -> DXF -> ATTRIB extraction
  extractor.py    Per-file orchestration, conflict resolution, QC flags
  scanner.py      Parallel scan with progress and cancellation
  register.py     Excel writer
  gui.py          Desktop UI
  cli.py          Command line
tests/            Parsing, extraction rules, DWG mapping, register, GUI, scanner
build/            PyInstaller spec and PowerShell build script
```

## Tests

```powershell
.\.venv\Scripts\python -m pytest tests -q
```

The suite covers the parsing layer, the conflict-resolution and QC rules, the
DWG attribute mapping, the Excel writer's structure and hyperlinks, file
discovery and scan control, and a headless build of the desktop window. It
needs neither Tesseract nor AutoCAD; the GUI tests skip automatically where
no display is available.
