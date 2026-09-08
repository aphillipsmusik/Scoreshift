"""ScoreShift main window."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDockWidget,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSpinBox, QSplitter, QStackedWidget,
                               QStatusBar, QTabWidget, QToolBar, QVBoxLayout,
                               QWidget)

from ..config import IS_WINDOWS, Settings, work_dir
from ..engine import (CleanOptions, ExportRequest, ScoreJob, ScoreShiftEngine,
                      TransposeRequest)
from ..pipeline import export as exporter
from ..pipeline import omr, pagedetect
from ..pipeline import score as scoring
from ..pipeline import scanner as scanning
from .crop_view import CropView, ndarray_to_qpixmap
from .score_preview import ScorePreview
from .settings_dialog import SettingsDialog
from .workers import Job

MAX_LOAD_DIM = 4200


@dataclass
class PageItem:
    source: Path
    original: np.ndarray
    quad: np.ndarray | None = None
    processed: np.ndarray | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.source.name


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load()
        self.engine = ScoreShiftEngine(self.settings)
        self.pages: list[PageItem] = []
        self.job: ScoreJob | None = None
        self._job: Job | None = None

        self.setWindowTitle("ScoreShift - scan, read and transpose sheet music")
        self.resize(1440, 940)
        self._build_ui()
        QTimer.singleShot(400, self._startup_engine_check)
        QTimer.singleShot(2500, self._startup_update_check)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self._build_actions()
        self._build_toolbar()
        self._build_menu()

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_page_list())
        splitter.addWidget(self._build_center())
        splitter.addWidget(self._build_controls())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([250, 740, 430])
        self.setCentralWidget(splitter)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        self.log_view.setStyleSheet("font-family:Consolas,Menlo,monospace; font-size:11px;")
        dock = QDockWidget("Activity log", self)
        dock.setWidget(self.log_view)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        dock.hide()
        self.log_dock = dock

        bar = QStatusBar()
        self.status_label = QLabel("Ready. Add photos or scan a page to begin.")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.hide()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_job)
        self.cancel_btn.hide()
        bar.addWidget(self.status_label, 1)
        bar.addPermanentWidget(self.progress)
        bar.addPermanentWidget(self.cancel_btn)
        self.setStatusBar(bar)

    def _build_actions(self) -> None:
        self.act_add = QAction("Add images...", self)
        self.act_add.setShortcut(QKeySequence.Open)
        self.act_add.triggered.connect(self.add_images)

        self.act_camera = QAction("Camera...", self)
        self.act_camera.setShortcut("Ctrl+Shift+C")
        self.act_camera.triggered.connect(self.capture_from_camera)

        self.act_scan = QAction("Scan...", self)
        self.act_scan.triggered.connect(self.scan_pages)

        self.act_open_xml = QAction("Open MusicXML / MIDI...", self)
        self.act_open_xml.triggered.connect(self.open_score_file)

        self.act_transcribe = QAction("Transcribe", self)
        self.act_transcribe.setShortcut("Ctrl+R")
        self.act_transcribe.triggered.connect(self.transcribe)

        self.act_export = QAction("Export...", self)
        self.act_export.setShortcut(QKeySequence.Save)
        self.act_export.triggered.connect(self.export)

        self.act_settings = QAction("Settings...", self)
        self.act_settings.triggered.connect(self.open_settings)

        self.act_engine = QAction("Check OMR engine", self)
        self.act_engine.triggered.connect(lambda: self._engine_check(verbose=True))

        self.act_log = QAction("Show activity log", self)
        self.act_log.setCheckable(True)
        self.act_log.toggled.connect(lambda on: self.log_dock.setVisible(on))

        self.act_update = QAction("Check for updates...", self)
        self.act_update.triggered.connect(lambda: self.check_updates(quiet=False))

        self.act_about = QAction("About ScoreShift", self)
        self.act_about.triggered.connect(self.about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setMovable(False)
        for act in (self.act_add, self.act_camera, self.act_scan):
            tb.addAction(act)
        tb.addSeparator()
        tb.addAction(self.act_transcribe)
        tb.addAction(self.act_export)
        tb.addSeparator()
        tb.addAction(self.act_settings)
        self.addToolBar(tb)

    def _build_menu(self) -> None:
        m = self.menuBar()
        f = m.addMenu("&File")
        f.addAction(self.act_add)
        f.addAction(self.act_camera)
        f.addAction(self.act_scan)
        f.addAction(self.act_open_xml)
        f.addSeparator()
        f.addAction(self.act_transcribe)
        f.addAction(self.act_export)
        f.addSeparator()
        quit_act = QAction("Exit", self)
        quit_act.triggered.connect(self.close)
        f.addAction(quit_act)

        t = m.addMenu("&Tools")
        t.addAction(self.act_settings)
        t.addAction(self.act_engine)
        t.addAction(self.act_log)

        h = m.addMenu("&Help")
        h.addAction(self.act_update)
        h.addAction(self.act_about)

    # -- left panel ------------------------------------------------------
    def _build_page_list(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 8, 4, 8)
        title = QLabel("Pages")
        title.setStyleSheet("font-weight:600; font-size:13px;")
        lay.addWidget(title)

        self.page_list = QListWidget()
        self.page_list.setIconSize(QSize(108, 140))
        self.page_list.setSpacing(4)
        self.page_list.currentRowChanged.connect(self._on_page_selected)
        lay.addWidget(self.page_list, 1)

        row = QHBoxLayout()
        for text, slot, tip in (
            ("Add", self.add_images, "Add photos or image files"),
            ("Camera", self.capture_from_camera, "Capture pages with a webcam"),
            ("Scan", self.scan_pages, "Scan from your printer"),
            ("Remove", self.remove_page, "Remove the selected page"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        up = QPushButton("Move up")
        up.clicked.connect(lambda: self.move_page(-1))
        down = QPushButton("Move down")
        down.clicked.connect(lambda: self.move_page(1))
        row2.addWidget(up)
        row2.addWidget(down)
        lay.addLayout(row2)
        return box

    # -- centre ----------------------------------------------------------
    def _build_center(self) -> QWidget:
        self.tabs = QTabWidget()

        page_tab = QWidget()
        pl = QVBoxLayout(page_tab)
        pl.setContentsMargins(6, 6, 6, 6)

        tools = QHBoxLayout()
        self.btn_detect = QPushButton("Detect page edges")
        self.btn_detect.clicked.connect(self.detect_current)
        self.btn_full = QPushButton("Use whole image")
        self.btn_full.clicked.connect(self.reset_crop)
        self.btn_rotate = QPushButton("Rotate 90°")
        self.btn_rotate.clicked.connect(self.rotate_current)
        self.btn_detect_all = QPushButton("Detect on all pages")
        self.btn_detect_all.clicked.connect(self.detect_all)
        for b in (self.btn_detect, self.btn_full, self.btn_rotate, self.btn_detect_all):
            tools.addWidget(b)
        tools.addStretch(1)
        self.chk_show_clean = QCheckBox("Show cleaned result")
        self.chk_show_clean.toggled.connect(self._refresh_page_view)
        tools.addWidget(self.chk_show_clean)
        pl.addLayout(tools)

        self.crop_view = CropView()
        self.crop_view.quad_changed.connect(self._on_quad_changed)
        pl.addWidget(self.crop_view, 1)

        self.page_hint = QLabel(
            "Drag the blue corners to match the page. "
            "Ctrl + mouse wheel zooms."
        )
        self.page_hint.setStyleSheet("color:#6b7280;")
        pl.addWidget(self.page_hint)
        self.tabs.addTab(page_tab, "Page")

        score_tab = QWidget()
        sl = QVBoxLayout(score_tab)
        sl.setContentsMargins(6, 6, 6, 6)
        zoom = QHBoxLayout()
        zin = QPushButton("Zoom in")
        zin.clicked.connect(lambda: self.preview.zoom(1.2))
        zout = QPushButton("Zoom out")
        zout.clicked.connect(lambda: self.preview.zoom(1 / 1.2))
        zoom.addWidget(zin)
        zoom.addWidget(zout)
        zoom.addStretch(1)
        self.preview_label = QLabel("")
        self.preview_label.setStyleSheet("color:#6b7280;")
        zoom.addWidget(self.preview_label)
        sl.addLayout(zoom)
        self.preview = ScorePreview()
        sl.addWidget(self.preview, 1)
        self.tabs.addTab(score_tab, "Score")
        return self.tabs

    # -- right panel -----------------------------------------------------
    def _build_controls(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(12)

        # Clean-up
        clean = QGroupBox("1. Clean up the scan")
        cl = QVBoxLayout(clean)
        self.chk_crop = QCheckBox("Auto-crop to the page")
        self.chk_crop.setChecked(self.settings.auto_crop)
        self.chk_deskew = QCheckBox("Straighten crooked staves")
        self.chk_deskew.setChecked(self.settings.auto_deskew)
        self.chk_enhance = QCheckBox("Even out lighting and boost contrast")
        self.chk_enhance.setChecked(self.settings.enhance)
        self.chk_binarize = QCheckBox("Hard black and white (faint pencil scans)")
        for c in (self.chk_crop, self.chk_deskew, self.chk_enhance, self.chk_binarize):
            cl.addWidget(c)
            c.toggled.connect(self._invalidate_processed)
        lay.addWidget(clean)

        # Transcribe
        trans = QGroupBox("2. Read the notes")
        tl = QVBoxLayout(trans)
        self.engine_label = QLabel("Checking the OMR engine...")
        self.engine_label.setWordWrap(True)
        self.engine_label.setStyleSheet("color:#6b7280;")
        tl.addWidget(self.engine_label)
        self.btn_transcribe = QPushButton("Transcribe pages")
        self.btn_transcribe.clicked.connect(self.transcribe)
        tl.addWidget(self.btn_transcribe)
        self.score_summary = QLabel("No score loaded yet.")
        self.score_summary.setWordWrap(True)
        tl.addWidget(self.score_summary)
        lay.addWidget(trans)

        # Transpose
        tr = QGroupBox("3. Change the key")
        trl = QVBoxLayout(tr)
        form = QFormLayout()
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["To a key", "By half steps", "For an instrument"])
        self.cmb_mode.currentIndexChanged.connect(lambda i: self.stack.setCurrentIndex(i))
        form.addRow("Method:", self.cmb_mode)
        self.cmb_source_key = QComboBox()
        self.cmb_source_key.setEditable(False)
        self._fill_keys(self.cmb_source_key, add_auto=True)
        form.addRow("Original key:", self.cmb_source_key)
        trl.addLayout(form)

        self.stack = QStackedWidget()

        w_key = QWidget()
        f1 = QFormLayout(w_key)
        f1.setContentsMargins(0, 0, 0, 0)
        self.cmb_target_key = QComboBox()
        self._fill_keys(self.cmb_target_key)
        self.cmb_target_key.setCurrentText("F major")
        self.cmb_direction = QComboBox()
        self.cmb_direction.addItems(["Nearest", "Up", "Down"])
        f1.addRow("New key:", self.cmb_target_key)
        f1.addRow("Direction:", self.cmb_direction)
        self.stack.addWidget(w_key)

        w_semi = QWidget()
        f2 = QFormLayout(w_semi)
        f2.setContentsMargins(0, 0, 0, 0)
        self.spin_semitones = QSpinBox()
        self.spin_semitones.setRange(-24, 24)
        self.spin_semitones.setValue(2)
        self.spin_semitones.setSuffix(" half steps")
        f2.addRow("Move by:", self.spin_semitones)
        self.stack.addWidget(w_semi)

        w_instr = QWidget()
        f3 = QFormLayout(w_instr)
        f3.setContentsMargins(0, 0, 0, 0)
        self.cmb_instrument = QComboBox()
        self.cmb_instrument.addItems(list(scoring.INSTRUMENT_PRESETS.keys()))
        self.cmb_instrument.setCurrentIndex(1)
        self.cmb_instr_dir = QComboBox()
        self.cmb_instr_dir.addItems(["Concert pitch -> player's part",
                                     "Player's part -> concert pitch"])
        f3.addRow("Instrument:", self.cmb_instrument)
        f3.addRow("Direction:", self.cmb_instr_dir)
        self.stack.addWidget(w_instr)

        trl.addWidget(self.stack)

        extra = QFormLayout()
        self.spin_octave = QSpinBox()
        self.spin_octave.setRange(-3, 3)
        self.spin_octave.setValue(0)
        self.spin_octave.setSuffix(" octaves")
        extra.addRow("Then shift:", self.spin_octave)
        trl.addLayout(extra)
        self.chk_simplify = QCheckBox("Prefer the easier spelling (Db over C#)")
        self.chk_simplify.setChecked(True)
        trl.addWidget(self.chk_simplify)

        self.btn_transpose = QPushButton("Apply and preview")
        self.btn_transpose.clicked.connect(self.apply_transpose)
        trl.addWidget(self.btn_transpose)
        self.transpose_label = QLabel("")
        self.transpose_label.setWordWrap(True)
        self.transpose_label.setStyleSheet("color:#2563eb;")
        trl.addWidget(self.transpose_label)
        lay.addWidget(tr)

        # Export
        ex = QGroupBox("4. Save")
        el = QVBoxLayout(ex)
        self.chk_pdf = QCheckBox("Sheet music PDF")
        self.chk_pdf.setChecked(True)
        self.chk_midi = QCheckBox("MIDI")
        self.chk_midi.setChecked(True)
        self.chk_xml = QCheckBox("MusicXML (editable in MuseScore)")
        self.chk_scanpdf = QCheckBox("Cleaned scan as PDF")
        for c in (self.chk_pdf, self.chk_midi, self.chk_xml, self.chk_scanpdf):
            el.addWidget(c)
        ef = QFormLayout()
        self.edit_basename = QLineEdit()
        self.edit_basename.setPlaceholderText("File name")
        ef.addRow("Name:", self.edit_basename)
        folder_row = QHBoxLayout()
        self.lbl_folder = QLineEdit(self.settings.output_dir)
        self.lbl_folder.setReadOnly(True)
        pick = QPushButton("...")
        pick.setMaximumWidth(34)
        pick.clicked.connect(self.pick_output_dir)
        folder_row.addWidget(self.lbl_folder, 1)
        folder_row.addWidget(pick)
        ef.addRow("Folder:", folder_row)
        el.addLayout(ef)
        self.btn_export = QPushButton("Export")
        self.btn_export.clicked.connect(self.export)
        el.addWidget(self.btn_export)
        self.btn_open_folder = QPushButton("Open output folder")
        self.btn_open_folder.clicked.connect(self.open_output_folder)
        el.addWidget(self.btn_open_folder)
        lay.addWidget(ex)

        lay.addStretch(1)
        for child in inner.findChildren(QFormLayout):
            child.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            child.setRowWrapPolicy(QFormLayout.WrapLongRows)
            child.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for label in (self.engine_label, self.score_summary, self.transpose_label):
            label.setMaximumWidth(390)
        scroll.setWidget(inner)
        scroll.setMinimumWidth(410)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return scroll

    @staticmethod
    def _fill_keys(combo: QComboBox, add_auto: bool = False) -> None:
        if add_auto:
            combo.addItem("Detect automatically")
        for k in scoring.MAJOR_KEYS:
            combo.addItem(f"{k} major")
        for k in scoring.MINOR_KEYS:
            combo.addItem(f"{k[0].upper()}{k[1:]} minor")

    # ------------------------------------------------------------------
    # Logging / jobs
    # ------------------------------------------------------------------
    def log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        for line in str(text).splitlines() or [""]:
            self.log_view.appendPlainText(f"[{stamp}] {line}")

    def _busy(self, on: bool, message: str = "") -> None:
        self.progress.setRange(0, 0 if on else 1)
        self.progress.setVisible(on)
        self.cancel_btn.setVisible(on)
        if message:
            self.status_label.setText(message)
        for w in (self.btn_transcribe, self.btn_transpose, self.btn_export,
                  self.act_transcribe, self.act_export, self.act_add, self.act_scan):
            w.setEnabled(not on)

    def _run(self, fn, on_done, message: str) -> None:
        if self._job is not None and self._job.isRunning():
            QMessageBox.information(self, "Busy", "Something is already running.")
            return
        self._busy(True, message)
        self.log(message)
        job = Job(fn, self)
        self._job = job
        job.log.connect(self.log)
        job.step.connect(self.status_label.setText)

        def _finished(result):
            self._busy(False, "Ready.")
            self._job = None
            on_done(result)

        def _failed(msg):
            self._busy(False, "Ready.")
            self._job = None
            self.log("ERROR: " + msg)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("That didn't work")
            box.setText(msg.split("\n")[0])
            if "\n" in msg:
                box.setDetailedText(msg)
            box.exec()

        job.done.connect(_finished)
        job.failed.connect(_failed)
        job.start()

    def _cancel_job(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self.status_label.setText("Cancelling...")

    # ------------------------------------------------------------------
    # Engine
    # ------------------------------------------------------------------
    def _startup_engine_check(self) -> None:
        self._engine_check(verbose=False)

    def _engine_check(self, verbose: bool) -> None:
        path = self.settings.audiveris()

        def work(log, cancelled, step):
            return omr.engine_status(path)

        def done(result):
            ok, message = result
            if ok:
                self.engine_label.setText(f"OMR engine ready:\n{path}")
                self.engine_label.setStyleSheet("color:#15803d;")
            else:
                self.engine_label.setText(
                    "Audiveris (the note-reading engine) is not set up yet. "
                    "Open Settings to point ScoreShift at it, or install it from "
                    "github.com/Audiveris/audiveris/releases."
                )
                self.engine_label.setStyleSheet("color:#b45309;")
            if verbose:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Information if ok else QMessageBox.Warning)
                box.setWindowTitle("OMR engine")
                box.setText("Audiveris is ready." if ok else "Audiveris is not usable yet.")
                box.setDetailedText(message)
                box.exec()

        self._run(work, done, "Checking the OMR engine...")

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------
    def _startup_update_check(self) -> None:
        """One quiet look at GitHub, at most once a day."""
        from .. import updater
        if not self.settings.auto_update_check:
            return
        if not updater.due_for_check(self.settings.last_update_check):
            return
        self.check_updates(quiet=True)

    def check_updates(self, quiet: bool = False) -> None:
        from .. import updater
        from .update_dialog import CheckThread, UpdateDialog

        if getattr(self, "_update_thread", None) is not None:
            if self._update_thread.isRunning():
                return

        if not quiet:
            self.status_label.setText("Checking for updates...")

        thread = CheckThread(self.settings.update_repo, self)
        self._update_thread = thread

        def done(result) -> None:
            self._update_thread = None
            self.settings.last_update_check = time.time()
            self.settings.save()
            release = result.release
            if not result.available:
                message = (f"You have the latest version ({result.current})."
                           if release is not None
                           else (result.skipped_reason or "No updates found."))
                self.log(message)
                self.status_label.setText(message if not quiet else "Ready.")
                if not quiet:
                    QMessageBox.information(self, "Up to date", message)
                return
            if quiet and release.version == self.settings.skipped_version:
                self.log(f"Version {release.version} is available "
                         f"(skipped at your request).")
                return
            self.log(f"Version {release.version} is available.")
            self.status_label.setText(f"ScoreShift {release.version} is available.")
            dialog = UpdateDialog(release, self)
            dialog.skip_requested.connect(self._skip_version)
            dialog.exec()

        def failed(message: str) -> None:
            self._update_thread = None
            self.log("Update check: " + message)
            if quiet:
                self.status_label.setText("Ready.")
                return
            self.status_label.setText("Ready.")
            QMessageBox.warning(self, "Check for updates", message)

        thread.finished_ok.connect(done)
        thread.failed.connect(failed)
        thread.start()

    def _skip_version(self, version: str) -> None:
        self.settings.skipped_version = version
        self.settings.save()
        self.log(f"Skipping version {version}.")

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------
    def add_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add page images", self.settings.last_input_dir,
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp);;All files (*)")
        if not paths:
            return
        self.settings.last_input_dir = str(Path(paths[0]).parent)
        self.settings.save()
        self._load_paths([Path(p) for p in paths])

    def _load_paths(self, paths: list[Path]) -> None:
        auto = self.chk_crop.isChecked()

        def work(log, cancelled, step):
            items = []
            for i, p in enumerate(paths, 1):
                step(f"Loading {p.name} ({i}/{len(paths)})...")
                img = pagedetect.imread(p)
                h, w = img.shape[:2]
                if max(h, w) > MAX_LOAD_DIM:
                    f = MAX_LOAD_DIM / max(h, w)
                    img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
                    log(f"{p.name}: scaled down to {img.shape[1]}x{img.shape[0]} px")
                quad = pagedetect.detect_page(img) if auto else None
                log(f"{p.name}: {'page edges found' if quad is not None else 'no page edge detected'}")
                items.append(PageItem(source=p, original=img, quad=quad))
            return items

        def done(items):
            for item in items:
                self.pages.append(item)
                self._append_list_item(item)
            self.page_list.setCurrentRow(len(self.pages) - 1)
            self._suggest_basename()
            self.status_label.setText(f"{len(self.pages)} page(s) ready.")

        self._run(work, done, f"Loading {len(paths)} image(s)...")

    def _append_list_item(self, item: PageItem) -> None:
        thumb = cv2.resize(item.original, (108, 140), interpolation=cv2.INTER_AREA)
        li = QListWidgetItem(QIcon(ndarray_to_qpixmap(thumb)), item.name)
        li.setToolTip(str(item.source))
        self.page_list.addItem(li)

    def capture_from_camera(self) -> None:
        from .camera_dialog import CameraDialog
        dlg = CameraDialog(self)
        if dlg.exec() and dlg.captured_paths:
            self.log(f"Captured {len(dlg.captured_paths)} page(s) from the camera.")
            self._load_paths(list(dlg.captured_paths))

    def scan_pages(self) -> None:
        if not IS_WINDOWS:
            QMessageBox.information(
                self, "Scanning",
                "Scanning straight from a printer needs Windows. "
                "Use 'Add images...' on this machine.")
            return
        def find(log, cancelled, step):
            step("Looking for scanners...")
            try:
                devices = scanning.list_scanners()
            except scanning.ScannerError as exc:
                raise RuntimeError(str(exc)) from exc
            if not devices:
                raise RuntimeError(
                    "No scanner was found.\nCheck the printer is switched on and "
                    "connected, and that its Windows scanner (WIA) driver is "
                    "installed.")
            log("Scanners: " + ", ".join(d.name for d in devices))
            return devices

        self._run(find, self._choose_scanner, "Looking for scanners...")

    def _choose_scanner(self, devices) -> None:
        from .scan_dialog import ScanDialog
        dlg = ScanDialog(devices, self.settings, self)
        if not dlg.exec():
            return
        dlg.apply_to(self.settings)
        out = work_dir() / "scans"
        native = dlg.chk_native.isChecked()
        device_id = dlg.device_id
        dpi = self.settings.scan_dpi
        mode = self.settings.scan_mode
        source = self.settings.scan_source
        max_pages = dlg.spin_pages.value()

        def work(log, cancelled, step):
            if native:
                step("Waiting for the Windows scan dialog...")
                return scanning.acquire_with_dialog(out, log=log)
            step(f"Scanning at {dpi} DPI ({mode}, {source})...")
            return scanning.acquire(out, device_id=device_id, dpi=dpi, mode=mode,
                                    source=source, max_pages=max_pages, log=log)

        def done(paths):
            self.log(f"Scanned {len(paths)} page(s).")
            self._load_paths([Path(p) for p in paths])

        self._run(work, done, "Scanning...")

    def remove_page(self) -> None:
        row = self.page_list.currentRow()
        if row < 0:
            return
        self.pages.pop(row)
        self.page_list.takeItem(row)

    def move_page(self, delta: int) -> None:
        row = self.page_list.currentRow()
        new = row + delta
        if row < 0 or not (0 <= new < len(self.pages)):
            return
        self.pages[row], self.pages[new] = self.pages[new], self.pages[row]
        item = self.page_list.takeItem(row)
        self.page_list.insertItem(new, item)
        self.page_list.setCurrentRow(new)

    def _current_page(self) -> PageItem | None:
        row = self.page_list.currentRow()
        if 0 <= row < len(self.pages):
            return self.pages[row]
        return None

    def _on_page_selected(self, row: int) -> None:
        self._refresh_page_view()

    def _refresh_page_view(self) -> None:
        item = self._current_page()
        if item is None:
            self.crop_view.set_image(np.full((600, 800), 245, np.uint8), editable=False)
            return
        if self.chk_show_clean.isChecked():
            result = self._process_item(item)
            self.crop_view.set_image(result.image, editable=False)
            self.page_hint.setText(
                "Cleaned preview - this is what the note reader sees. "
                + " ".join(result.notes))
        else:
            self.crop_view.set_image(item.original, item.quad, editable=True)
            self.page_hint.setText(
                "Drag the blue corners to match the page. Ctrl + mouse wheel zooms.")

    def _on_quad_changed(self) -> None:
        item = self._current_page()
        if item is None:
            return
        item.quad = self.crop_view.quad()
        item.processed = None

    def _invalidate_processed(self) -> None:
        for p in self.pages:
            p.processed = None
        if self.chk_show_clean.isChecked():
            self._refresh_page_view()

    def detect_current(self) -> None:
        item = self._current_page()
        if item is None:
            return
        quad = pagedetect.detect_page(item.original)
        if quad is None:
            self.log(f"{item.name}: no page edge found.")
            QMessageBox.information(
                self, "No edge found",
                "I couldn't find a page outline. If the page fills the frame, "
                "'Use whole image' is the right choice - otherwise drag the "
                "corners yourself.")
            return
        item.quad = quad
        item.processed = None
        self.crop_view.set_quad(quad)
        self.log(f"{item.name}: page edges detected.")

    def detect_all(self) -> None:
        pages = list(self.pages)

        def work(log, cancelled, step):
            found = 0
            for i, p in enumerate(pages, 1):
                if cancelled():
                    break
                step(f"Detecting page {i}/{len(pages)}...")
                q = pagedetect.detect_page(p.original)
                p.quad = q
                p.processed = None
                found += q is not None
                log(f"{p.name}: {'found' if q is not None else 'not found'}")
            return found

        def done(found):
            self._refresh_page_view()
            self.status_label.setText(f"Page edges found on {found} of {len(pages)} page(s).")

        self._run(work, done, "Detecting page edges...")

    def reset_crop(self) -> None:
        item = self._current_page()
        if item is None:
            return
        item.quad = None
        item.processed = None
        self.crop_view.set_image(item.original, None, editable=True)
        self.crop_view.reset_to_full()
        item.quad = self.crop_view.quad()

    def rotate_current(self) -> None:
        item = self._current_page()
        if item is None:
            return
        item.original = cv2.rotate(item.original, cv2.ROTATE_90_CLOCKWISE)
        item.quad = None
        item.processed = None
        row = self.page_list.currentRow()
        thumb = cv2.resize(item.original, (108, 140), interpolation=cv2.INTER_AREA)
        self.page_list.item(row).setIcon(QIcon(ndarray_to_qpixmap(thumb)))
        self._refresh_page_view()

    def _process_item(self, item: PageItem) -> pagedetect.PageResult:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return self._process_item_inner(item)
        finally:
            QApplication.restoreOverrideCursor()

    def _clean_options(self) -> CleanOptions:
        return CleanOptions(
            auto_crop=self.chk_crop.isChecked(),
            deskew=self.chk_deskew.isChecked(),
            enhance=self.chk_enhance.isChecked(),
            binarize=self.chk_binarize.isChecked(),
        )

    def _process_item_inner(self, item: PageItem) -> pagedetect.PageResult:
        result = pagedetect.process_page(
            item.original, quad=item.quad, **self._clean_options().as_kwargs())
        item.processed = result.image
        item.notes = result.notes
        return result

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------
    def transcribe(self) -> None:
        if not self.pages:
            QMessageBox.information(self, "Nothing to read",
                                    "Add or scan at least one page first.")
            return
        audiveris = self.settings.audiveris()
        if audiveris is None:
            self._offer_engine_help()
            return

        pages = list(self.pages)
        options = self._clean_options()
        engine = self.engine

        def work(log, cancelled, step):
            step(f"Cleaning {len(pages)} page(s)...")
            cleaned = engine.clean_pages(
                [p.original for p in pages], options,
                quads=[p.quad for p in pages], log=log, cancel=cancelled)
            for page, result in zip(pages, cleaned):
                page.processed = result.image
            step("Reading the notes with Audiveris - this can take a minute...")
            return engine.transcribe(cleaned, log=log, cancel=cancelled)

        def done(job: ScoreJob):
            self.job = job
            self._show_score_info(job.info, seconds=job.omr_seconds)
            self._suggest_basename()
            self._render_preview("Transcribed score (original key)")

        self._run(work, done, "Transcribing...")

    def open_score_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a score", self.settings.last_input_dir,
            "Scores (*.xml *.musicxml *.mxl *.mid *.midi *.krn);;All files (*)")
        if not path:
            return
        engine = self.engine

        def work(log, cancelled, step):
            step("Reading the score...")
            return engine.load_score(path)

        def done(job: ScoreJob):
            self.job = job
            self._show_score_info(job.info)
            self._suggest_basename()
            self._render_preview("Loaded score (original key)")

        self._run(work, done, "Opening score...")

    def _show_score_info(self, info: scoring.ScoreInfo | None,
                         seconds: float | None = None) -> None:
        if info is None:
            self.score_summary.setText("No score loaded yet.")
            return
        bits = []
        if info.notated_key:
            bits.append(f"Written key: <b>{info.notated_key}</b>")
        if info.detected_key and info.detected_key != info.notated_key:
            bits.append(f"Sounds like: {info.detected_key}")
        if info.time_signature:
            bits.append(f"Time: {info.time_signature}")
        bits.append(f"{info.parts} part(s), {info.measures} bars, {info.notes} notes")
        if info.lowest and info.highest:
            bits.append(f"Range: {info.lowest} to {info.highest}")
        if seconds:
            bits.append(f"Read in {seconds:.0f}s")
        self.score_summary.setText("<br>".join(bits))
        for w in info.warnings:
            self.log("Note: " + w)
        if info.best_key:
            idx = self.cmb_source_key.findText(info.best_key, Qt.MatchFixedString)
            if idx >= 0:
                self.cmb_source_key.setCurrentIndex(idx)

    def _offer_engine_help(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("The note-reading engine isn't set up")
        box.setText(
            "ScoreShift needs Audiveris to turn the picture into notes.\n\n"
            "It's free. Install it, then point ScoreShift at it in Settings.")
        box.setInformativeText(f"Download: {omr.AUDIVERIS_DOWNLOAD}")
        open_settings = box.addButton("Open settings", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_settings:
            self.open_settings()

    # ------------------------------------------------------------------
    # Transposition
    # ------------------------------------------------------------------
    def apply_transpose(self) -> None:
        if self.job is None or self.job.source_score is None:
            QMessageBox.information(
                self, "No score yet",
                "Transcribe your pages first, or open a MusicXML file.")
            return

        mode = self.cmb_mode.currentIndex()
        src_key = self.cmb_source_key.currentText()
        request = TransposeRequest(
            source_key=None if src_key.startswith("Detect") else src_key,
            direction=self.cmb_direction.currentText().lower(),
            octaves=self.spin_octave.value(),
            simplify=self.chk_simplify.isChecked(),
        )
        if mode == 0:
            request.target_key = self.cmb_target_key.currentText()
        elif mode == 1:
            request.semitones = self.spin_semitones.value()
        else:
            request.instrument = self.cmb_instrument.currentText()
            request.to_written = self.cmb_instr_dir.currentIndex() == 0

        engine, job = self.engine, self.job

        def work(log, cancelled, step):
            step("Transposing...")
            return engine.transpose(job, request, log=log)

        def done(updated: ScoreJob):
            self.job = updated
            res = updated.transpose
            text = (f"{res.from_key} → {res.to_key} — {res.interval_name.lower()} "
                    f"({res.semitones:+d} half steps)")
            if res.notes:
                text += "\n" + "\n".join(res.notes)
            self.transpose_label.setText(text)
            self._suggest_basename()
            self._render_preview(f"Transposed to {res.to_key}")

        self._run(work, done, "Transposing...")

    def _render_preview(self, caption: str) -> None:
        if self.job is None:
            return
        self.tabs.setCurrentIndex(1)
        self.preview.set_message("Engraving preview...")
        engine, job = self.engine, self.job

        def work(log, cancelled, step):
            step("Engraving preview...")
            try:
                return engine.preview_svg(job)
            except exporter.ExportError as exc:
                log(f"Preview unavailable: {exc}")
                return []

        def done(svgs):
            if svgs:
                self.preview.set_svgs(svgs)
                self.preview_label.setText(caption)
            else:
                self.preview.set_message(
                    "Preview isn't available for this score, but exporting "
                    "still works.")

        self._run(work, done, "Engraving preview...")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _suggest_basename(self) -> None:
        if self.edit_basename.isModified():
            return
        stem = "Score"
        if self.pages:
            stem = self.pages[0].source.stem
        elif self.job is not None and self.job.musicxml:
            stem = self.job.musicxml.stem
        if self.job is not None and self.job.transpose is not None:
            stem = f"{stem} in {self.job.transpose.to_key}"
        self.edit_basename.setText(stem)

    def pick_output_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose output folder",
                                             self.settings.output_dir)
        if d:
            self.settings.output_dir = d
            self.settings.save()
            self.lbl_folder.setText(d)

    def open_output_folder(self) -> None:
        path = Path(self.settings.output_dir)
        path.mkdir(parents=True, exist_ok=True)
        try:
            if IS_WINDOWS:
                os.startfile(str(path))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:  # noqa: BLE001
            self.log(f"Could not open the folder: {exc}")

    def export(self) -> None:
        want_scan = self.chk_scanpdf.isChecked()
        has_score = self.job is not None and (
            self.job.current_score is not None or self.job.source_score is not None)
        if not has_score and not want_scan:
            QMessageBox.information(
                self, "Nothing to save",
                "Transcribe a score first, or tick 'Cleaned scan as PDF'.")
            return

        basename = self.edit_basename.text().strip() or "Score"
        out_dir = Path(self.settings.output_dir)
        request = ExportRequest(
            pdf=self.chk_pdf.isChecked() and has_score,
            midi=self.chk_midi.isChecked() and has_score,
            musicxml=self.chk_xml.isChecked() and has_score,
            scan_pdf=want_scan,
        )
        job = self.job or ScoreJob(job_id="scan-only")
        pages = list(self.pages)
        options = self._clean_options()
        engine = self.engine

        def work(log, cancelled, step):
            if want_scan:
                step(f"Preparing {len(pages)} page(s)...")
                cleaned = engine.clean_pages(
                    [p.original for p in pages], options,
                    quads=[p.quad for p in pages], log=log, cancel=cancelled)
                for page, result in zip(pages, cleaned):
                    page.processed = result.image
                job.pages = cleaned
            step("Saving...")
            return engine.export(job, out_dir, basename, request, log=log)

        def done(report: exporter.ExportReport):
            names = "\n".join(f"  {p.name}" for p in report.written)
            msg = f"Saved to {out_dir}:\n{names}" if report.written else "Nothing was saved."
            if report.skipped:
                msg += "\n\nSkipped:\n" + "\n".join(f"  {s}" for s in report.skipped)
            self.status_label.setText(
                f"Saved {len(report.written)} file(s) to {out_dir}.")
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Information if report.written else QMessageBox.Warning)
            box.setWindowTitle("Export")
            box.setText(msg)
            box.exec()

        self._run(work, done, "Exporting...")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            dlg.apply_to(self.settings)
            self.lbl_folder.setText(self.settings.output_dir)
            self._engine_check(verbose=False)

    def about(self) -> None:
        QMessageBox.about(
            self, "About ScoreShift",
            "<h3>ScoreShift</h3>"
            "<p>Scan or photograph sheet music, straighten and crop the page, "
            "read the notes, and save the music in any key.</p>"
            "<p>Note reading by <b>Audiveris</b>. Engraving by <b>MuseScore</b> "
            "or the built-in <b>Verovio</b> engine. Music handling by "
            "<b>music21</b>.</p>")

    def closeEvent(self, event):  # noqa: D102, N802
        self.settings.auto_crop = self.chk_crop.isChecked()
        self.settings.auto_deskew = self.chk_deskew.isChecked()
        self.settings.enhance = self.chk_enhance.isChecked()
        self.settings.save()
        if self._job is not None and self._job.isRunning():
            self._job.cancel()
            self._job.wait(3000)
        super().closeEvent(event)


def run() -> int:
    QApplication.setApplicationName("ScoreShift")
    QApplication.setOrganizationName("ScoreShift")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    return app.exec()
