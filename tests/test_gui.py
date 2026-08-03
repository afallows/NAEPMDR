"""Headless smoke test for the desktop UI.

Skipped where tkinter or a display is unavailable. On Windows both are
present, so this runs as part of an ordinary test run there. It catches the
failure mode that matters: the window failing to build at all, or building
only when the OCR and DWG tooling happens to be installed.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

tk = pytest.importorskip("tkinter")


@pytest.fixture
def app():
    from mdr.gui import RegisterApp

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.geometry("980x720")
    instance = RegisterApp(root)
    root.update_idletasks()
    yield instance
    root.destroy()


class TestWindowBuilds:
    def test_builds_without_ocr_or_dwg_tooling(self, app):
        # The dependency probe reports missing tools into the log; it must
        # never prevent the window from opening.
        assert app.root.title() == "Enbridge Master Document Register"
        assert app.scan_button.winfo_exists()
        assert app.progress.winfo_exists()

    def test_cannot_open_a_register_before_one_is_built(self, app):
        assert str(app.open_button["state"]) == "disabled"
        assert str(app.cancel_button["state"]) == "disabled"

    def test_uses_the_brand_palette(self, app):
        from mdr import theme

        assert app.scan_button["bg"] == theme.GUI.gold
        assert theme.GUI.gold == f"#{theme.ENBRIDGE_GOLD}"

    def test_log_wraps_so_long_messages_stay_readable(self, app):
        assert str(app.log["wrap"]) == "word"


class TestSettingsRoundTrip:
    def test_collect_reflects_the_widgets(self, app):
        app.root_var.set(os.sep + os.path.join("tmp", "demo"))
        app.recursive_var.set(False)
        app.dwg_var.set(False)
        app.relative_var.set(False)
        app.dpi_var.set(200)
        app.exclude_var.set("old, superseded ,")

        settings = app._collect_settings()
        assert settings.recursive is False
        assert settings.prefer_dwg is False
        assert settings.link_style == "absolute"
        assert settings.render_dpi == 200
        # Blank entries are dropped and whitespace trimmed.
        assert settings.exclude_folders == ["old", "superseded"]

    def test_output_path_defaults_into_the_scanned_folder(self, app):
        app.root_var.set(os.sep + os.path.join("tmp", "demo", "Drawings"))
        app.output_var.set("")
        settings = app._collect_settings()
        assert settings.output_path.endswith(".xlsx")
        assert "Master Document Register" in settings.output_path
        assert Path(settings.output_path).parent.name == "Drawings"

    def test_explicit_output_path_is_kept(self, app):
        app.root_var.set(os.sep + "tmp")
        app.output_var.set(os.sep + os.path.join("tmp", "mine.xlsx"))
        assert app._collect_settings().output_path.endswith("mine.xlsx")


class TestScanGuards:
    def test_refuses_to_scan_a_folder_that_does_not_exist(self, app, monkeypatch):
        shown = {}
        monkeypatch.setattr("mdr.gui.messagebox.showerror",
                            lambda title, msg: shown.update(title=title))
        app.root_var.set(os.sep + os.path.join("nope", "not", "here"))
        app._start_scan()
        assert shown.get("title") == "Folder required"
        assert app.worker is None
