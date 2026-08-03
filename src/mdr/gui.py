"""Enbridge-branded desktop UI (tkinter).

tkinter is used deliberately: it ships with CPython, so the packaged .exe
gains no extra runtime and stays small. All colour comes from ``theme`` so
the register and the UI never drift apart.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import theme
from .config import Settings
from .dwg_source import locate_oda
from .ocr import OcrUnavailable, locate_tesseract
from .register import write_register
from .scanner import ScanCancelled, ScanProgress, scan

T = theme.GUI
PAD = 12


class RegisterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings.load()
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()
        self.records: list = []
        self.output_written: str | None = None

        root.title("Enbridge Master Document Register")
        root.configure(bg=T.white)
        root.minsize(880, 640)
        self._set_icon()
        self._build_styles()
        self._build_layout()
        self._probe_dependencies()
        self.root.after(120, self._drain_events)

    # -- chrome -----------------------------------------------------------

    def _set_icon(self) -> None:
        try:
            icon = tk.PhotoImage(width=32, height=32)
            icon.put(T.gold, to=(0, 0, 32, 32))
            self.root.iconphoto(True, icon)
        except tk.TclError:
            pass

    def _build_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=T.white)
        style.configure("Panel.TFrame", background=T.panel)
        style.configure("TLabel", background=T.white, foreground=T.black,
                        font=(T.font, 10))
        style.configure("Muted.TLabel", background=T.white, foreground=T.muted,
                        font=(T.font, 9))
        style.configure("Heading.TLabel", background=T.white, foreground=T.dark,
                        font=(T.font, 11, "bold"))
        style.configure("TCheckbutton", background=T.white, foreground=T.black,
                        font=(T.font, 10))
        style.configure("TEntry", fieldbackground=T.white, font=(T.font, 10))
        style.configure("TSpinbox", fieldbackground=T.white, font=(T.font, 10))
        style.configure("Gold.Horizontal.TProgressbar",
                        troughcolor=T.border, background=T.gold,
                        bordercolor=T.border, lightcolor=T.gold, darkcolor=T.gold)

    def _build_layout(self) -> None:
        self._build_banner()

        body = ttk.Frame(self.root, padding=(PAD + 4, PAD))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        self._build_folder_row(body)
        self._build_output_row(body)
        self._build_options(body)
        self._build_actions(body)
        self._build_log(body)
        self._build_statusbar()

    def _build_banner(self) -> None:
        banner = tk.Frame(self.root, bg=T.white)
        banner.pack(fill="x")
        inner = tk.Frame(banner, bg=T.white)
        inner.pack(fill="x", padx=PAD + 4, pady=(PAD + 2, 6))

        mark = tk.Canvas(inner, width=34, height=34, bg=T.white,
                         highlightthickness=0)
        mark.create_oval(2, 8, 26, 32, outline=T.gold, width=5)
        mark.create_arc(6, 2, 32, 26, start=200, extent=200,
                        style="arc", outline=T.dark, width=5)
        mark.pack(side="left", padx=(0, 10))

        text = tk.Frame(inner, bg=T.white)
        text.pack(side="left", anchor="w")
        tk.Label(text, text="Master Document Register", bg=T.white, fg=T.dark,
                 font=(T.font, 17, "bold")).pack(anchor="w")
        tk.Label(text, text="Scan drawing folders and build a register with OCR",
                 bg=T.white, fg=T.muted, font=(T.font, 9)).pack(anchor="w")

        rule = tk.Frame(self.root, bg=T.gold, height=4)
        rule.pack(fill="x")

    def _build_folder_row(self, parent) -> None:
        ttk.Label(parent, text="Drawing folder", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w", pady=(6, 2))
        row = ttk.Frame(parent)
        row.grid(row=1, column=0, sticky="ew")
        row.columnconfigure(0, weight=1)
        self.root_var = tk.StringVar(value=self.settings.root_folder)
        ttk.Entry(row, textvariable=self.root_var).grid(row=0, column=0, sticky="ew")
        self._button(row, "Browse...", self._choose_root, primary=False).grid(
            row=0, column=1, padx=(8, 0))

    def _build_output_row(self, parent) -> None:
        ttk.Label(parent, text="Register file", style="Heading.TLabel").grid(
            row=2, column=0, sticky="w", pady=(PAD, 2))
        row = ttk.Frame(parent)
        row.grid(row=3, column=0, sticky="ew")
        row.columnconfigure(0, weight=1)
        self.output_var = tk.StringVar(value=self.settings.output_path)
        ttk.Entry(row, textvariable=self.output_var).grid(row=0, column=0, sticky="ew")
        self._button(row, "Save as...", self._choose_output, primary=False).grid(
            row=0, column=1, padx=(8, 0))
        ttk.Label(parent,
                  text="Left blank, the register is written into the drawing "
                       "folder with a timestamped name.",
                  style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(3, 0))

    def _build_options(self, parent) -> None:
        ttk.Label(parent, text="Options", style="Heading.TLabel").grid(
            row=5, column=0, sticky="w", pady=(PAD, 4))
        box = tk.Frame(parent, bg=T.panel, highlightbackground=T.border,
                       highlightthickness=1)
        box.grid(row=6, column=0, sticky="ew")
        grid = tk.Frame(box, bg=T.panel)
        grid.pack(fill="x", padx=PAD, pady=PAD)

        self.recursive_var = tk.BooleanVar(value=self.settings.recursive)
        self.all_pages_var = tk.BooleanVar(value=self.settings.all_pages)
        self.dwg_var = tk.BooleanVar(value=self.settings.prefer_dwg)
        self.open_var = tk.BooleanVar(value=self.settings.open_when_done)
        self.relative_var = tk.BooleanVar(value=self.settings.link_style == "relative")

        checks = [
            ("Include sub-folders", self.recursive_var),
            ("One row per sheet (read every page)", self.all_pages_var),
            ("Use DWG title block data when available", self.dwg_var),
            ("Relative hyperlinks (keeps the folder portable)", self.relative_var),
            ("Open the register when finished", self.open_var),
        ]
        for index, (label, var) in enumerate(checks):
            tk.Checkbutton(grid, text=label, variable=var, bg=T.panel, fg=T.black,
                           activebackground=T.panel, selectcolor=T.white,
                           font=(T.font, 10), anchor="w",
                           highlightthickness=0, bd=0).grid(
                row=index // 2, column=index % 2, sticky="w", padx=(0, 24), pady=2)

        numbers = tk.Frame(grid, bg=T.panel)
        numbers.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        tk.Label(numbers, text="Render DPI", bg=T.panel, fg=T.black,
                 font=(T.font, 10)).pack(side="left")
        self.dpi_var = tk.IntVar(value=self.settings.render_dpi)
        ttk.Spinbox(numbers, from_=150, to=600, increment=50, width=6,
                    textvariable=self.dpi_var).pack(side="left", padx=(6, 18))
        tk.Label(numbers, text="Workers (0 = auto)", bg=T.panel, fg=T.black,
                 font=(T.font, 10)).pack(side="left")
        self.workers_var = tk.IntVar(value=self.settings.workers)
        ttk.Spinbox(numbers, from_=0, to=64, width=6,
                    textvariable=self.workers_var).pack(side="left", padx=(6, 18))
        tk.Label(numbers, text="Skip folders", bg=T.panel, fg=T.black,
                 font=(T.font, 10)).pack(side="left")
        self.exclude_var = tk.StringVar(value=", ".join(self.settings.exclude_folders))
        ttk.Entry(numbers, textvariable=self.exclude_var, width=34).pack(
            side="left", padx=(6, 0))

    def _build_actions(self, parent) -> None:
        row = ttk.Frame(parent)
        row.grid(row=7, column=0, sticky="ew", pady=(PAD, 6))
        row.columnconfigure(2, weight=1)
        self.scan_button = self._button(row, "Build Register", self._start_scan)
        self.scan_button.grid(row=0, column=0)
        self.cancel_button = self._button(row, "Cancel", self._cancel, primary=False)
        self.cancel_button.grid(row=0, column=1, padx=(8, 0))
        self.cancel_button.configure(state="disabled")
        self.open_button = self._button(row, "Open Register", self._open_output,
                                        primary=False)
        self.open_button.grid(row=0, column=3, sticky="e")
        self.open_button.configure(state="disabled")

        self.progress = ttk.Progressbar(parent, mode="determinate",
                                        style="Gold.Horizontal.TProgressbar")
        self.progress.grid(row=8, column=0, sticky="ew")

    def _build_log(self, parent) -> None:
        parent.rowconfigure(10, weight=1)
        ttk.Label(parent, text="Activity", style="Heading.TLabel").grid(
            row=9, column=0, sticky="w", pady=(PAD, 4))
        frame = tk.Frame(parent, bg=T.white, highlightbackground=T.border,
                         highlightthickness=1)
        frame.grid(row=10, column=0, sticky="nsew")
        # Wrap rather than clip: the dependency and QC messages are long, and
        # a truncated one is worse than useless.
        self.log = tk.Text(frame, height=10, wrap="word", bd=0,
                           bg=T.white, fg=T.black, font=(T.mono, 9),
                           padx=8, pady=6, state="disabled")
        bar = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        for tag, colour in (("ok", T.ok), ("warn", T.warn), ("error", T.error),
                            ("muted", T.muted)):
            self.log.tag_configure(tag, foreground=colour)

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self.root, bg=T.panel, height=26)
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(bar, textvariable=self.status_var, bg=T.panel, fg=T.muted,
                 font=(T.font, 9), anchor="w").pack(side="left", padx=PAD, pady=4)

    def _button(self, parent, text, command, primary: bool = True) -> tk.Button:
        if primary:
            return tk.Button(parent, text=text, command=command, bd=0,
                             bg=T.gold, fg=T.black, activebackground="#E5A519",
                             activeforeground=T.black, font=(T.font, 10, "bold"),
                             padx=18, pady=7, cursor="hand2")
        return tk.Button(parent, text=text, command=command, bd=1,
                         bg=T.white, fg=T.dark, activebackground=T.panel,
                         highlightbackground=T.border, relief="solid",
                         font=(T.font, 10), padx=14, pady=6, cursor="hand2")

    # -- dependency probe --------------------------------------------------

    def _probe_dependencies(self) -> None:
        try:
            exe = locate_tesseract(self.settings.tesseract_path or None)
            self._log(f"OCR engine: {exe}", "muted")
        except OcrUnavailable as exc:
            self._log(str(exc), "error")
            self.status_var.set("Tesseract OCR not found")

        oda = locate_oda(self.settings.oda_converter_path or None)
        if oda:
            self._log(f"DWG converter: {oda}", "muted")
        else:
            self._log(
                "DWG converter not found - DWG files will be skipped and the "
                "PDF title block read instead. Install the free ODA File "
                "Converter to read DWG attributes exactly.", "muted")

    # -- actions -----------------------------------------------------------

    def _choose_root(self) -> None:
        chosen = filedialog.askdirectory(title="Select the drawing folder",
                                         initialdir=self.root_var.get() or os.getcwd())
        if chosen:
            self.root_var.set(chosen)

    def _choose_output(self) -> None:
        initial = self.output_var.get() or self._default_output()
        chosen = filedialog.asksaveasfilename(
            title="Save register as", defaultextension=".xlsx",
            initialfile=Path(initial).name,
            initialdir=str(Path(initial).parent) if initial else os.getcwd(),
            filetypes=[("Excel workbook", "*.xlsx")])
        if chosen:
            self.output_var.set(chosen)

    def _default_output(self) -> str:
        root = self.root_var.get().strip()
        if not root:
            return ""
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        return str(Path(root) / f"Master Document Register - {Path(root).name} - {stamp}.xlsx")

    def _collect_settings(self) -> Settings:
        s = self.settings
        s.root_folder = self.root_var.get().strip()
        s.output_path = self.output_var.get().strip() or self._default_output()
        s.recursive = self.recursive_var.get()
        s.all_pages = self.all_pages_var.get()
        s.prefer_dwg = self.dwg_var.get()
        s.open_when_done = self.open_var.get()
        s.link_style = "relative" if self.relative_var.get() else "absolute"
        s.render_dpi = int(self.dpi_var.get())
        s.workers = int(self.workers_var.get())
        s.exclude_folders = [p.strip() for p in self.exclude_var.get().split(",") if p.strip()]
        return s

    def _start_scan(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        settings = self._collect_settings()
        if not settings.root_folder or not Path(settings.root_folder).is_dir():
            messagebox.showerror("Folder required",
                                 "Choose a folder containing the drawings to register.")
            return
        self.output_var.set(settings.output_path)

        self._clear_log()
        self._log(f"Scanning {settings.root_folder}")
        self.cancel_flag.clear()
        self.records = []
        self.output_written = None
        self.scan_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self.progress.configure(value=0, maximum=100)

        self.worker = threading.Thread(target=self._run_scan, args=(settings,), daemon=True)
        self.worker.start()

    def _run_scan(self, settings: Settings) -> None:
        try:
            records = scan(
                settings.root_folder, settings,
                str(Path(settings.output_path).parent),
                on_progress=lambda p: self.events.put(("progress", p)),
                should_cancel=self.cancel_flag.is_set,
            )
            if not records:
                self.events.put(("done", (None, [])))
                return
            path = write_register(records, settings.root_folder,
                                  settings.output_path,
                                  settings.include_full_path_column)
            settings.save()
            self.events.put(("done", (path, records)))
        except ScanCancelled:
            self.events.put(("cancelled", None))
        except OcrUnavailable as exc:
            self.events.put(("error", str(exc)))
        except Exception:
            self.events.put(("error", traceback.format_exc(limit=4)))

    def _cancel(self) -> None:
        self.cancel_flag.set()
        self.status_var.set("Cancelling...")

    def _open_output(self) -> None:
        if not self.output_written:
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(self.output_written)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self.output_written])
            else:
                subprocess.Popen(["xdg-open", self.output_written])
        except OSError as exc:
            messagebox.showerror("Could not open", str(exc))

    # -- event pump --------------------------------------------------------

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self._on_progress(payload)
                elif kind == "done":
                    self._on_done(*payload)
                elif kind == "cancelled":
                    self._finish("Cancelled", "warn")
                elif kind == "error":
                    self._log(payload, "error")
                    self._finish("Failed", "error")
        except queue.Empty:
            pass
        self.root.after(120, self._drain_events)

    def _on_progress(self, progress: ScanProgress) -> None:
        if progress.total:
            self.progress.configure(maximum=progress.total, value=progress.done)
            self.status_var.set(
                f"{progress.done} of {progress.total} files  -  {progress.current}")
        if progress.current:
            self._log(f"  [{progress.done}/{progress.total}] {progress.current}", "muted")

    def _on_done(self, path: str | None, records: list) -> None:
        if path is None:
            self._log("No PDF files were found in that folder.", "warn")
            self._finish("Nothing to register", "warn")
            return
        self.records = records
        self.output_written = path
        flagged = sum(1 for r in records if r.qc_flags)
        errors = sum(1 for r in records if r.has_error)
        self._log("")
        self._log(f"Registered {len(records)} sheet(s).", "ok")
        if flagged:
            self._log(f"{flagged} row(s) flagged for review - see the QC Review sheet.",
                      "warn")
        if errors:
            self._log(f"{errors} file(s) could not be read.", "error")
        self._log(f"Written to {path}", "ok")
        self.open_button.configure(state="normal")
        self._finish(f"Done - {len(records)} sheet(s), {flagged} flagged", "ok")
        if self.open_var.get():
            self._open_output()

    def _finish(self, status: str, tag: str) -> None:
        self.scan_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.status_var.set(status)

    # -- logging -----------------------------------------------------------

    def _log(self, message: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n", tag or "")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


def launch() -> None:
    root = tk.Tk()
    RegisterApp(root)
    root.mainloop()
