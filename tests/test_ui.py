"""Headless smoke test: build the window, drive it, and screenshot it."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Key names contain arrows; a Windows console defaults to cp1252 and would
# raise rather than print them.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from scoreshift.ui.main_window import MainWindow  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)

failures: list[str] = []


def pump(app: QApplication, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_idle(app: QApplication, win: MainWindow, limit: float = 120.0) -> None:
    end = time.time() + limit
    while time.time() < end:
        app.processEvents()
        if win._job is None or not win._job.isRunning():
            pump(app, 0.3)
            if win._job is None or not win._job.isRunning():
                return
        time.sleep(0.02)
    failures.append("a background job never finished")


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from scoreshift.ui import theme
    app.setPalette(theme.palette())
    app.setFont(theme.base_font())
    app.setStyleSheet(theme.stylesheet())
    win = MainWindow()
    win.resize(1500, 950)
    win.show()
    pump(app, 1.2)
    wait_idle(app, win)          # startup engine check
    check("window built", win.isVisible())
    check("engine status reported", bool(win.engine_label.text()),
          win.engine_label.text()[:60])
    check("the first thing to do is suggested", win.next_step()[0] == "add",
          win.primary.text())
    win.grab().save(str(OUT / "ui_empty.png"))

    # The capture view lives in the window now, so switching to it must not
    # open anything of its own.
    from PySide6.QtWidgets import QDialog
    before = [w for w in app.topLevelWidgets() if w.isVisible()]
    win.view_switch.select(1)
    win._switch_view(1)
    pump(app, 0.6)
    opened = [w for w in app.topLevelWidgets()
              if w.isVisible() and w not in before]
    check("capture opens inside the window", not opened
          and win.stack.currentWidget() is win.capture,
          win.stack.currentWidget().__class__.__name__)
    check("no dialog was opened to capture",
          not any(isinstance(w, QDialog) for w in opened))
    win.grab().save(str(OUT / "ui_capture.png"))
    win.view_switch.select(0)
    win._switch_view(0)
    pump(app, 0.3)

    # A score that already exists as a file skips the scanning entirely.
    win.view_switch.select(2)
    win._switch_view(2)
    pump(app, 0.4)
    check("the score view offers a file when there is no score",
          win.stack.currentWidget().objectName() == "ScoreDrop",
          win.stack.currentWidget().objectName())
    win.load_score_file(FIX / "original.musicxml")
    wait_idle(app, win)
    pump(app, 0.5)
    check("opening a MusicXML file loads the piece",
          win.job is not None and win.job.source_score is not None)
    check("it lands on the score view", win.view_switch.current == 2
          and win.stack.currentWidget() is win.preview,
          win.stack.currentWidget().__class__.__name__)
    check("the key is read from the file", win.stat_key.value.text() == "D major",
          win.stat_key.value.text())
    check("and the next step is the key change, not scanning",
          win.next_step()[0] == "key", win.primary.text())
    win.job = None
    win._show_score_info(None)
    win.view_switch.select(0)
    win._switch_view(0)
    pump(app, 0.3)

    win._load_paths([FIX / "photo_p1.jpg", FIX / "photo_p2.jpg"])
    wait_idle(app, win)
    check("two pages loaded", len(win.pages) == 2 and len(win.cards.cards) == 2)
    check("each page has a card showing its detail",
          all(c.headline.text() for c in win.cards.cards),
          ", ".join(c.headline.text() for c in win.cards.cards))
    check("the headline figures are filled in",
          win.stat_pages.value.text() == "2" and bool(win.stat_detail.value.text()),
          f"{win.stat_pages.value.text()} pages, detail {win.stat_detail.value.text()}")
    check("page edges auto-detected", all(p.quad is not None for p in win.pages))
    win.grab().save(str(OUT / "ui_pages.png"))

    win.chk_show_clean.setChecked(True)
    pump(app, 0.8)
    win.grab().save(str(OUT / "ui_cleaned.png"))
    check("cleaned preview rendered",
          any(p.processed is not None for p in win.pages))
    win.chk_show_clean.setChecked(False)
    pump(app, 0.4)

    # Skip OMR (engine not installed here) and load the score directly.
    win.job = win.engine.load_score(FIX / "original.musicxml")
    win._show_score_info(win.job.info)
    check("score summary filled", "D major" in win.score_summary.text(),
          win.score_summary.text()[:70])
    check("source key auto-selected", win.cmb_source_key.currentText() == "D major",
          win.cmb_source_key.currentText())
    parts = win.job.info.parts if win.job.info else 0
    check("per-part saving offered only when there is more than one part",
          win.chk_parts.isEnabled() == (parts > 1),
          f"{parts} part(s), checkbox {'on' if win.chk_parts.isEnabled() else 'off'}")

    win.cmb_target_key.setCurrentText("F major")
    win.apply_transpose()
    wait_idle(app, win)
    wait_idle(app, win)          # preview render job
    pump(app, 1.0)
    check("transposition reported in the UI", "F major" in win.transpose_label.text(),
          win.transpose_label.text())
    check("score preview engraved", win.preview._svgs != [],
          f"{len(win.preview._svgs)} page(s)")
    win.view_switch.select(2)
    pump(app, 0.8)
    win.grab().save(str(OUT / "ui_score.png"))

    win.settings.output_dir = str(OUT / "ui_export")
    win.lbl_folder.setText(win.settings.output_dir)
    win.edit_basename.setText("UI test in F major")
    win.chk_xml.setChecked(True)
    win.chk_scanpdf.setChecked(True)
    win.chk_parts.setChecked(True)
    # Suppress the modal completion dialog.
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.exec = lambda self, *a, **k: None  # type: ignore[assignment]
    win.export()
    wait_idle(app, win, limit=240)
    produced = sorted(p.name for p in (OUT / "ui_export").glob("*"))
    check("export produced files from the UI", len(produced) >= 4, ", ".join(produced))
    midis = [n for n in produced if n.endswith(".mid")]
    pdfs = [n for n in produced if n.endswith(".pdf") and "scan" not in n]
    check("the full score and every part came out as MIDI",
          len(midis) == 1 + parts, ", ".join(midis))
    check("and as PDF", len(pdfs) == 1 + parts, ", ".join(pdfs))

    win.close()
    print(f"\n{'ALL UI CHECKS PASSED' if not failures else 'FAILURES: ' + '; '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
