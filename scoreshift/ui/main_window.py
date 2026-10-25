"""ScoreShift main window - pages on the left, the music in the middle,
and everything else in a panel that slides in when it's wanted."""
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
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDockWidget,
                               QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSpinBox,
                               QSizePolicy, QStackedWidget, QVBoxLayout,
                               QWidget)

from ..config import IS_WINDOWS, Settings, work_dir
from ..engine import (CleanOptions, ExportRequest, ScoreJob, ScoreShiftEngine,
                      TransposeRequest)
from ..pipeline import export as exporter
from ..pipeline import omr, pagedetect
from ..pipeline import score as scoring
from ..pipeline import scanner as scanning
from . import icons, theme
from .crop_view import CropView, ndarray_to_qpixmap
from .score_preview import ScorePreview
from .settings_dialog import SettingsDialog
from .widgets import (Card, Disclosure, SectionHeading, Segmented, SlideOver,
                      StatusDot, button, divider, icon_button, label)
from .workers import Job

MAX_LOAD_DIM = 4200
RAIL_WIDTH = 176
THUMB = QSize(112, 146)


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
        self._engine_ready = False

        self.setWindowTitle("ScoreShift")
        self.resize(1360, 900)
        self.setMinimumSize(1020, 680)
        self.setAcceptDrops(True)
        self._build()
        self._refresh_guidance()
        QTimer.singleShot(300, self._startup_engine_check)
        QTimer.singleShot(2500, self._startup_update_check)

    # ==================================================================
    # Construction
    # ==================================================================
    def _build(self) -> None:
        self._build_actions()

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_top_bar())

        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._build_rail())
        row.addWidget(self._build_canvas(), 1)
        row.addWidget(self._build_panel())
        outer.addWidget(body, 1)

        outer.addWidget(self._build_status())
        self.setCentralWidget(root)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("Log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        dock = QDockWidget("Activity log", self)
        dock.setWidget(self.log_view)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        dock.hide()
        self.log_dock = dock

    # -- actions / shortcuts -------------------------------------------
    def _build_actions(self) -> None:
        def act(text, slot, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(shortcut)
            a.triggered.connect(slot)
            self.addAction(a)
            return a

        self.act_add = act("Add images", self.add_images, QKeySequence.Open)
        self.act_camera = act("Camera", self.capture_from_camera, "Ctrl+Shift+C")
        self.act_scan = act("Scan", self.scan_pages, "Ctrl+Shift+S")
        self.act_transcribe = act("Transcribe", self.transcribe, "Ctrl+R")
        self.act_export = act("Save", self.export, QKeySequence.Save)
        self.act_panel = act("Toggle panel", self.toggle_panel, "Ctrl+B")
        self.act_open_xml = act("Open MusicXML or MIDI", self.open_score_file)
        self.act_settings = act("Settings", self.open_settings, "Ctrl+,")
        self.act_update = act("Check for updates",
                              lambda: self.check_updates(quiet=False))

    # -- top bar --------------------------------------------------------
    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(58)
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 0, 12, 0)
        row.setSpacing(6)

        mark = QLabel()
        mark.setPixmap(icons.pixmap("score", 22, theme.ACCENT))
        row.addWidget(mark)
        title = label("ScoreShift", "Title")
        title.setContentsMargins(4, 0, 14, 0)
        row.addWidget(title)

        for text, icon_name, slot, tip in (
            ("Add", "add", self.add_images, "Add photos or image files  (Ctrl+O)"),
            ("Camera", "camera", self.capture_from_camera,
             "Capture pages with a webcam  (Ctrl+Shift+C)"),
            ("Scan", "scanner", self.scan_pages,
             "Scan from your printer  (Ctrl+Shift+S)"),
        ):
            row.addWidget(button(text, icon_name, "ghost", tip))

        row.addStretch(1)

        self.top_hint = label("", "Hint")
        self.top_hint.setWordWrap(False)
        self.top_hint.setContentsMargins(0, 0, 10, 0)
        self.top_hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.top_hint)

        self.primary = button("Transcribe", "transcribe", "primary")
        self.primary.clicked.connect(self._do_primary)
        row.addWidget(self.primary)

        self.btn_panel = icon_button("chevron-right", "Hide the side panel  (Ctrl+B)")
        self.btn_panel.clicked.connect(self.toggle_panel)
        row.addWidget(self.btn_panel)

        more = icon_button("settings", "More")
        more.clicked.connect(self._show_menu)
        self.btn_more = more
        row.addWidget(more)
        return bar

    def _show_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(self.act_open_xml)
        menu.addSeparator()
        menu.addAction(self.act_settings)
        engine_action = menu.addAction("Check the note reader")
        engine_action.triggered.connect(lambda: self._engine_check(verbose=True))
        log_action = menu.addAction("Activity log")
        log_action.setCheckable(True)
        log_action.setChecked(self.log_dock.isVisible())
        log_action.toggled.connect(self.log_dock.setVisible)
        menu.addSeparator()
        menu.addAction(self.act_update)
        about = menu.addAction("About ScoreShift")
        about.triggered.connect(self.about)
        menu.exec(self.btn_more.mapToGlobal(self.btn_more.rect().bottomLeft()))

    # -- page rail ------------------------------------------------------
    def _build_rail(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("PageRail")
        rail.setFixedWidth(RAIL_WIDTH)
        col = QVBoxLayout(rail)
        col.setContentsMargins(12, 14, 12, 12)
        col.setSpacing(10)

        self.rail_heading = SectionHeading("Pages")
        col.addWidget(self.rail_heading)

        self.page_list = QListWidget()
        self.page_list.setIconSize(THUMB)
        self.page_list.setSpacing(0)
        self.page_list.setUniformItemSizes(False)
        self.page_list.currentRowChanged.connect(lambda _: self._refresh_page_view())
        col.addWidget(self.page_list, 1)

        self.rail_empty = label("No pages yet.\nAdd, capture or scan one.", "Faint")
        self.rail_empty.setAlignment(Qt.AlignCenter)
        col.addWidget(self.rail_empty)

        tools = QHBoxLayout()
        tools.setSpacing(2)
        self.btn_up = icon_button("up", "Move this page earlier", 30)
        self.btn_up.clicked.connect(lambda: self.move_page(-1))
        self.btn_down = icon_button("down", "Move this page later", 30)
        self.btn_down.clicked.connect(lambda: self.move_page(1))
        self.btn_remove = icon_button("trash", "Remove this page", 30)
        self.btn_remove.clicked.connect(self.remove_page)
        for b in (self.btn_up, self.btn_down, self.btn_remove):
            tools.addWidget(b)
        tools.addStretch(1)
        col.addLayout(tools)
        return rail

    # -- canvas ---------------------------------------------------------
    def _build_canvas(self) -> QWidget:
        canvas = QWidget()
        canvas.setObjectName("Canvas")
        col = QVBoxLayout(canvas)
        col.setContentsMargins(16, 12, 16, 12)
        col.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.view_switch = Segmented([("Page", "page"), ("Score", "score")])
        self.view_switch.changed.connect(self._switch_view)
        head.addWidget(self.view_switch)
        head.addStretch(1)

        self.page_tools = QWidget()
        page_tools = QHBoxLayout(self.page_tools)
        page_tools.setContentsMargins(0, 0, 0, 0)
        page_tools.setSpacing(4)
        self.btn_detect = button("Find edges", "crop", "quiet",
                                 "Detect the page outline again")
        self.btn_detect.clicked.connect(self.detect_current)
        self.btn_full = button("Whole image", "page", "quiet",
                               "Use the entire image, no cropping")
        self.btn_full.clicked.connect(self.reset_crop)
        self.btn_rotate = icon_button("rotate", "Rotate 90 degrees", 32)
        self.btn_rotate.clicked.connect(self.rotate_current)
        self.chk_show_clean = QCheckBox("Cleaned")
        self.chk_show_clean.setToolTip("Show the page as the note reader sees it")
        self.chk_show_clean.toggled.connect(self._refresh_page_view)
        for w in (self.btn_detect, self.btn_full, self.btn_rotate,
                  self.chk_show_clean):
            page_tools.addWidget(w)
        head.addWidget(self.page_tools)

        self.score_tools = QWidget()
        score_tools = QHBoxLayout(self.score_tools)
        score_tools.setContentsMargins(0, 0, 0, 0)
        score_tools.setSpacing(4)
        zin = icon_button("zoom-in", "Zoom in", 32)
        zin.clicked.connect(lambda: self.preview.zoom(1.2))
        zout = icon_button("zoom-out", "Zoom out", 32)
        zout.clicked.connect(lambda: self.preview.zoom(1 / 1.2))
        self.preview_label = label("", "Faint")
        score_tools.addWidget(self.preview_label)
        score_tools.addWidget(zout)
        score_tools.addWidget(zin)
        head.addWidget(self.score_tools)
        self.score_tools.hide()
        col.addLayout(head)

        self.stack = QStackedWidget()
        self.crop_view = CropView()
        self.crop_view.quad_changed.connect(self._on_quad_changed)
        self.preview = ScorePreview()
        self.empty_state = self._build_empty_state()
        self.stack.addWidget(self.crop_view)
        self.stack.addWidget(self.preview)
        self.stack.addWidget(self.empty_state)
        self.stack.setCurrentWidget(self.empty_state)
        col.addWidget(self.stack, 1)

        self.page_hint = label("", "Faint")
        col.addWidget(self.page_hint)
        return canvas

    def _build_empty_state(self) -> QWidget:
        holder = QWidget()
        holder.setObjectName("DropZone")
        holder.setStyleSheet(f"""
            #DropZone {{ background: {theme.CANVAS};
                         border: 1.5px dashed {theme.BORDER_STRONG};
                         border-radius: {theme.RADIUS}px; }}
        """)
        col = QVBoxLayout(holder)
        col.setAlignment(Qt.AlignCenter)
        col.setSpacing(14)

        art = QLabel()
        art.setPixmap(icons.pixmap("score", 56, theme.BORDER_STRONG))
        art.setAlignment(Qt.AlignCenter)
        col.addWidget(art)

        headline = label("Drop a page of music here", "Title")
        headline.setAlignment(Qt.AlignCenter)
        col.addWidget(headline)

        sub = label("or bring one in another way", "Faint")
        sub.setAlignment(Qt.AlignCenter)
        col.addWidget(sub)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        for text, icon_name, slot in (
            ("Add images", "add", self.add_images),
            ("Use a camera", "camera", self.capture_from_camera),
            ("Scan a page", "scanner", self.scan_pages),
        ):
            b = button(text, icon_name)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        col.addLayout(row)
        return holder

    def _switch_view(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.page_tools.setVisible(index == 0)
        self.score_tools.setVisible(index == 1)
        if index == 0:
            self._refresh_page_view()
        else:
            self.stack.setCurrentWidget(self.preview)
            self.page_hint.setText("")

    # -- slide-over panel ------------------------------------------------
    def _build_panel(self) -> QWidget:
        self.panel = SlideOver("Score")
        self.panel.closed.connect(self._refresh_guidance)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setMaximumWidth(372)
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 4, 0)
        col.setSpacing(16)

        col.addWidget(self._card_reader())
        col.addWidget(self._card_key())
        col.addWidget(self._card_cleanup())
        col.addWidget(self._card_save())
        col.addStretch(1)
        scroll.setWidget(inner)
        self.panel.add(scroll)
        return self.panel

    def _card_reader(self) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        col.addWidget(SectionHeading("The music"))

        card = Card()
        self.score_summary = label("Nothing read yet. Add pages, then Transcribe.",
                                   "Hint")
        card.add(self.score_summary)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.engine_dot = StatusDot()
        status_row.addWidget(self.engine_dot, 0, Qt.AlignVCenter)
        self.engine_label = label("Checking the note reader...", "Faint")
        status_row.addWidget(self.engine_label, 1)
        card.add_layout(status_row)
        col.addWidget(card)
        return holder

    def _card_key(self) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        col.addWidget(SectionHeading("Change the key"))

        card = Card()
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["To a key", "By half steps", "For an instrument"])
        self.cmb_mode.currentIndexChanged.connect(self._mode_changed)
        card.add(self._elastic(self.cmb_mode))

        self.row_key = QWidget()
        key_col = QVBoxLayout(self.row_key)
        key_col.setContentsMargins(0, 0, 0, 0)
        key_col.setSpacing(8)
        self.cmb_source_key = QComboBox()
        self._fill_keys(self.cmb_source_key, add_auto=True)
        self.cmb_target_key = QComboBox()
        self._fill_keys(self.cmb_target_key)
        self.cmb_target_key.setCurrentText("F major")
        key_col.addWidget(self._field("From", self._elastic(self.cmb_source_key)))
        key_col.addWidget(self._field("To", self._elastic(self.cmb_target_key)))
        card.add(self.row_key)

        self.row_semitones = QWidget()
        semi = QHBoxLayout(self.row_semitones)
        semi.setContentsMargins(0, 0, 0, 0)
        self.spin_semitones = QSpinBox()
        self.spin_semitones.setRange(-24, 24)
        self.spin_semitones.setValue(2)
        self.spin_semitones.setSuffix(" half steps")
        semi.addWidget(self.spin_semitones)
        card.add(self.row_semitones)
        self.row_semitones.hide()

        self.row_instrument = QWidget()
        instr = QVBoxLayout(self.row_instrument)
        instr.setContentsMargins(0, 0, 0, 0)
        instr.setSpacing(8)
        self.cmb_instrument = QComboBox()
        self.cmb_instrument.addItems(list(scoring.INSTRUMENT_PRESETS.keys()))
        self.cmb_instrument.setCurrentIndex(1)
        self.cmb_instr_dir = QComboBox()
        self.cmb_instr_dir.addItems(["Concert pitch to player's part",
                                     "Player's part to concert pitch"])
        instr.addWidget(self._elastic(self.cmb_instrument))
        instr.addWidget(self._elastic(self.cmb_instr_dir))
        card.add(self.row_instrument)
        self.row_instrument.hide()

        more = Disclosure()
        self.cmb_direction = QComboBox()
        self.cmb_direction.addItems(["Nearest", "Up", "Down"])
        more.add(self._field("Move", self._elastic(self.cmb_direction)))
        self.spin_octave = QSpinBox()
        self.spin_octave.setRange(-3, 3)
        self.spin_octave.setSuffix(" octaves")
        more.add(self._field("Then shift", self.spin_octave))
        self.chk_simplify = QCheckBox("Prefer the easier spelling (D♭ over C♯)")
        self.chk_simplify.setChecked(True)
        more.add(self.chk_simplify)
        card.add(more)

        self.btn_transpose = button("Apply", "key")
        self.btn_transpose.clicked.connect(self.apply_transpose)
        card.add(self.btn_transpose)

        self.transpose_label = label("", "Accent")
        self.transpose_label.setVisible(False)
        card.add(self.transpose_label)
        col.addWidget(card)
        return holder

    def _card_cleanup(self) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        col.addWidget(SectionHeading("Page clean-up"))

        card = Card()
        self.chk_crop = QCheckBox("Crop to the page")
        self.chk_crop.setChecked(self.settings.auto_crop)
        self.chk_deskew = QCheckBox("Straighten the staves")
        self.chk_deskew.setChecked(self.settings.auto_deskew)
        self.chk_enhance = QCheckBox("Even out the lighting")
        self.chk_enhance.setChecked(self.settings.enhance)
        for c in (self.chk_crop, self.chk_deskew, self.chk_enhance):
            card.add(c)
            c.toggled.connect(self._invalidate_processed)

        more = Disclosure()
        self.chk_binarize = QCheckBox("Hard black and white")
        self.chk_binarize.setToolTip("For faint pencil or very grey photocopies")
        self.chk_binarize.toggled.connect(self._invalidate_processed)
        more.add(self.chk_binarize)
        card.add(more)
        col.addWidget(card)
        return holder

    def _card_save(self) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        col.addWidget(SectionHeading("Save"))

        card = Card()
        self.chk_pdf = QCheckBox("Sheet music PDF")
        self.chk_pdf.setChecked(True)
        self.chk_midi = QCheckBox("MIDI")
        self.chk_midi.setChecked(True)
        card.add(self.chk_pdf)
        card.add(self.chk_midi)

        more = Disclosure()
        self.chk_parts = QCheckBox("Each part in its own file as well")
        self.chk_parts.setToolTip(
            "As well as the full score, save one file per part - whichever of "
            "PDF and MIDI you have ticked above. Named after the parts on the "
            "page, so \"Trumpet in Bb\" comes out as its own PDF a player can "
            "read from.")
        self.chk_parts.setEnabled(False)
        self.chk_xml = QCheckBox("MusicXML (to edit in MuseScore)")
        self.chk_scanpdf = QCheckBox("The cleaned scan as a PDF")
        more.add(self.chk_parts)
        more.add(self.chk_xml)
        more.add(self.chk_scanpdf)
        self.edit_basename = QLineEdit()
        self.edit_basename.setPlaceholderText("File name")
        more.add(self._field("Name", self.edit_basename))
        folder = QHBoxLayout()
        folder.setSpacing(6)
        self.lbl_folder = QLineEdit(self.settings.output_dir)
        self.lbl_folder.setReadOnly(True)
        pick = icon_button("folder", "Choose a different folder", 32)
        pick.clicked.connect(self.pick_output_dir)
        folder.addWidget(self.lbl_folder, 1)
        folder.addWidget(pick)
        more.add_layout(folder)
        card.add(more)

        self.btn_export = button("Save files", "save")
        self.btn_export.clicked.connect(self.export)
        card.add(self.btn_export)

        self.btn_open_folder = button("Open the folder", "folder", "link")
        self.btn_open_folder.clicked.connect(self.open_output_folder)
        card.add(self.btn_open_folder)
        col.addWidget(card)
        return holder

    @staticmethod
    def _elastic(combo: QComboBox) -> QComboBox:
        """Let a combo shrink to its box rather than to its longest item."""
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(10)
        combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        view = combo.view()
        if view is not None:
            view.setMinimumWidth(230)
        return combo

    @staticmethod
    def _field(caption: str, widget: QWidget) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        col.addWidget(label(caption, "Faint"))
        col.addWidget(widget)
        return holder

    def _mode_changed(self, index: int) -> None:
        self.row_key.setVisible(index == 0)
        self.row_semitones.setVisible(index == 1)
        self.row_instrument.setVisible(index == 2)

    # -- status strip ----------------------------------------------------
    def _build_status(self) -> QWidget:
        strip = QWidget()
        strip.setObjectName("StatusStrip")
        strip.setFixedHeight(40)
        row = QHBoxLayout(strip)
        row.setContentsMargins(16, 0, 12, 0)
        row.setSpacing(10)

        self.status_dot = StatusDot()
        row.addWidget(self.status_dot, 0, Qt.AlignVCenter)
        self.status_label = label("Ready.", "Hint")
        row.addWidget(self.status_label, 1)

        self.progress = QProgressBar()
        self.progress.setFixedWidth(180)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.hide()
        row.addWidget(self.progress)

        self.cancel_btn = button("Cancel", variant="quiet")
        self.cancel_btn.clicked.connect(self._cancel_job)
        self.cancel_btn.hide()
        row.addWidget(self.cancel_btn)
        return strip

    @staticmethod
    def _fill_keys(combo: QComboBox, add_auto: bool = False) -> None:
        if add_auto:
            combo.addItem("Detect automatically")
        for k in scoring.MAJOR_KEYS:
            combo.addItem(f"{k} major")
        for k in scoring.MINOR_KEYS:
            combo.addItem(f"{k[0].upper()}{k[1:]} minor")

    # ==================================================================
    # Guidance - what should I do next?
    # ==================================================================
    def next_step(self) -> tuple[str, str, str, bool]:
        """(key, button label, icon, enabled) for the primary action."""
        if not self.pages and self.job is None:
            return "add", "Add pages", "add", True
        if self.job is None:
            if not self._engine_ready:
                return "engine", "Set up the reader", "wand", True
            return "transcribe", "Transcribe", "transcribe", True
        if self.job.transpose is None:
            return "key", "Change the key", "key", True
        return "save", "Save files", "save", True

    def _refresh_guidance(self) -> None:
        step, text, icon_name, enabled = self.next_step()
        self.primary.setText(text)
        self.primary.setIcon(icons.icon(icon_name, 17, theme.ON_ACCENT))
        self.primary.setEnabled(enabled)

        hints = {
            "add": "Start with a page",
            "engine": "The note reader needs setting up",
            "transcribe": f"{len(self.pages)} page"
                          f"{'s' if len(self.pages) != 1 else ''} ready to read",
            "key": "Now pick a key",
            "save": "Ready to save",
        }
        self.top_hint.setText(hints.get(step, ""))

        has_pages = bool(self.pages)
        for b in (self.btn_up, self.btn_down, self.btn_remove, self.btn_detect,
                  self.btn_full, self.btn_rotate, self.chk_show_clean):
            b.setEnabled(has_pages)
        self.rail_empty.setVisible(not has_pages)
        self.page_tools.setVisible(has_pages and self.view_switch.current == 0)
        self.rail_heading.caption.setText(
            f"PAGES  ·  {len(self.pages)}" if has_pages else "PAGES")

        has_score = self.job is not None and self.job.source_score is not None
        self.btn_transpose.setEnabled(has_score)
        self.btn_export.setEnabled(has_score or self.chk_scanpdf.isChecked())
        self.view_switch.buttons[1].setEnabled(has_score)

    def _do_primary(self) -> None:
        step, _, _, _ = self.next_step()
        if step == "add":
            self.add_images()
        elif step == "engine":
            self._offer_engine_help()
        elif step == "transcribe":
            self.transcribe()
        elif step == "key":
            if not self.panel.is_open:
                self.panel.show_panel()
            self.cmb_target_key.setFocus()
            self.apply_transpose()
        else:
            self.export()

    def toggle_panel(self) -> None:
        self.panel.toggle()
        self.btn_panel.setIcon(icons.icon(
            "chevron-right" if self.panel.is_open else "chevron-left",
            18, theme.TEXT_DIM))

    # ==================================================================
    # Logging and jobs
    # ==================================================================
    def log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        for line in str(text).splitlines() or [""]:
            self.log_view.appendPlainText(f"[{stamp}] {line}")

    def _busy(self, on: bool, message: str = "") -> None:
        self.progress.setRange(0, 0 if on else 1)
        self.progress.setVisible(on)
        self.cancel_btn.setVisible(on)
        self.status_dot.set_state("busy" if on else "idle")
        if message:
            self.status_label.setText(message)
        self.primary.setEnabled(not on)
        for w in (self.btn_transpose, self.btn_export):
            w.setEnabled(not on)
        if not on:
            self._refresh_guidance()

    def _run(self, fn, on_done, message: str) -> None:
        if self._job is not None and self._job.isRunning():
            return
        self._busy(True, message)
        self.log(message)
        job = Job(fn, self)
        self._job = job
        job.log.connect(self.log)
        job.step.connect(self.status_label.setText)

        def finished(result):
            self._busy(False, "Ready.")
            self._job = None
            on_done(result)

        def failed(msg):
            self._busy(False, "Ready.")
            self._job = None
            self.status_dot.set_state("bad")
            self.log("ERROR: " + msg)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("That didn't work")
            box.setText(msg.split("\n")[0])
            if "\n" in msg:
                box.setDetailedText(msg)
            box.exec()

        job.done.connect(finished)
        job.failed.connect(failed)
        job.start()

    def _cancel_job(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self.status_label.setText("Cancelling...")

    # ==================================================================
    # The note reader
    # ==================================================================
    def _startup_engine_check(self) -> None:
        self._engine_check(verbose=False)

    def _engine_check(self, verbose: bool) -> None:
        path = self.settings.audiveris()

        def work(log, cancelled, step):
            return omr.engine_status(path)

        def done(result):
            ok, message = result
            self._engine_ready = ok
            self.engine_dot.set_state("ok" if ok else "warn")
            self.engine_label.setText(
                "Note reader ready" if ok
                else "Audiveris isn't set up yet - open Settings to point at it")
            self.engine_label.setObjectName("Good" if ok else "Warn")
            self.engine_label.setStyleSheet(
                f"color: {theme.SUCCESS if ok else theme.WARNING}; font-size: 12px;")
            self._refresh_guidance()
            if verbose:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Information if ok else QMessageBox.Warning)
                box.setWindowTitle("Note reader")
                box.setText("Audiveris is ready." if ok
                            else "Audiveris is not usable yet.")
                box.setDetailedText(message)
                box.exec()

        self._run(work, done, "Checking the note reader...")

    def _offer_engine_help(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("The note reader isn't set up")
        box.setText("ScoreShift needs Audiveris to turn the picture into notes.\n\n"
                    "It's free. Install it, then point ScoreShift at it in Settings.")
        box.setInformativeText(f"Download: {omr.AUDIVERIS_DOWNLOAD}")
        open_settings = box.addButton("Open settings", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_settings:
            self.open_settings()

    # ==================================================================
    # Pages
    # ==================================================================
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
                step(f"Loading {p.name} ({i} of {len(paths)})...")
                img = pagedetect.imread(p)
                h, w = img.shape[:2]
                if max(h, w) > MAX_LOAD_DIM:
                    f = MAX_LOAD_DIM / max(h, w)
                    img = cv2.resize(img, None, fx=f, fy=f,
                                     interpolation=cv2.INTER_AREA)
                quad = pagedetect.detect_page(img) if auto else None
                log(f"{p.name}: "
                    f"{'page edges found' if quad is not None else 'no page edge found'}")
                items.append(PageItem(source=p, original=img, quad=quad))
            return items

        def done(items):
            for item in items:
                self.pages.append(item)
                self._append_list_item(item)
            self.page_list.setCurrentRow(len(self.pages) - 1)
            self.view_switch.select(0)
            self._suggest_basename()
            self.status_label.setText(
                f"{len(self.pages)} page{'s' if len(self.pages) != 1 else ''} ready.")
            self._refresh_guidance()

        self._run(work, done, f"Loading {len(paths)} image(s)...")

    def _append_list_item(self, item: PageItem) -> None:
        thumb = cv2.resize(item.original, (THUMB.width(), THUMB.height()),
                           interpolation=cv2.INTER_AREA)
        li = QListWidgetItem(QIcon(ndarray_to_qpixmap(thumb)),
                             f"  {len(self.pages)}")
        li.setToolTip(str(item.source))
        li.setSizeHint(QSize(RAIL_WIDTH - 30, THUMB.height() + 16))
        self.page_list.addItem(li)

    def _renumber(self) -> None:
        for i in range(self.page_list.count()):
            self.page_list.item(i).setText(f"  {i + 1}")

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
                "Scanning from a printer needs Windows. Use Add on this machine.")
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
                    "that its Windows scanner (WIA) driver is installed.")
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
        dpi, mode = self.settings.scan_dpi, self.settings.scan_mode
        source, max_pages = self.settings.scan_source, dlg.spin_pages.value()

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
        self._renumber()
        self._refresh_guidance()

    def move_page(self, delta: int) -> None:
        row = self.page_list.currentRow()
        new = row + delta
        if row < 0 or not (0 <= new < len(self.pages)):
            return
        self.pages[row], self.pages[new] = self.pages[new], self.pages[row]
        item = self.page_list.takeItem(row)
        self.page_list.insertItem(new, item)
        self.page_list.setCurrentRow(new)
        self._renumber()

    def _current_page(self) -> PageItem | None:
        row = self.page_list.currentRow()
        return self.pages[row] if 0 <= row < len(self.pages) else None

    def _refresh_page_view(self) -> None:
        if self.stack.currentIndex() != 0:
            return
        item = self._current_page()
        if item is None:
            self.stack.setCurrentWidget(self.empty_state)
            self.page_hint.setText("")
            return
        self.stack.setCurrentWidget(self.crop_view)
        if self.chk_show_clean.isChecked():
            result = self._process_item(item)
            self.crop_view.set_image(result.image, editable=False)
            self.page_hint.setText("This is what the note reader sees.  "
                                   + " ".join(result.notes))
        else:
            self.crop_view.set_image(item.original, item.quad, editable=True)
            self.page_hint.setText(
                "Drag the corners to match the page.  Ctrl + wheel zooms.")

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
            self.status_label.setText(
                "No page outline found - drag the corners, or use Whole image.")
            return
        item.quad = quad
        item.processed = None
        self.crop_view.set_quad(quad)
        self.log(f"{item.name}: page edges detected.")

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
        thumb = cv2.resize(item.original, (THUMB.width(), THUMB.height()),
                           interpolation=cv2.INTER_AREA)
        self.page_list.item(row).setIcon(QIcon(ndarray_to_qpixmap(thumb)))
        self._refresh_page_view()

    def _clean_options(self) -> CleanOptions:
        return CleanOptions(
            auto_crop=self.chk_crop.isChecked(),
            deskew=self.chk_deskew.isChecked(),
            enhance=self.chk_enhance.isChecked(),
            binarize=self.chk_binarize.isChecked(),
        )

    def _process_item(self, item: PageItem) -> pagedetect.PageResult:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = pagedetect.process_page(
                item.original, quad=item.quad, **self._clean_options().as_kwargs())
            item.processed = result.image
            item.notes = result.notes
            return result
        finally:
            QApplication.restoreOverrideCursor()

    # ==================================================================
    # Transcribe
    # ==================================================================
    def transcribe(self) -> None:
        if not self.pages:
            self.status_label.setText("Add or scan at least one page first.")
            return
        if self.settings.audiveris() is None:
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
            step("Reading the notes - this can take a minute...")
            return engine.transcribe(cleaned, log=log, cancel=cancelled)

        def done(job: ScoreJob):
            self.job = job
            self._show_score_info(job.info, seconds=job.omr_seconds)
            self._suggest_basename()
            self._render_preview("Original key")
            if not self.panel.is_open:
                self.panel.show_panel()

        self._run(work, done, "Reading the notes...")

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
            self._render_preview("Original key")

        self._run(work, done, "Opening score...")

    def _show_score_info(self, info: scoring.ScoreInfo | None,
                         seconds: float | None = None) -> None:
        if info is None:
            self.score_summary.setText("Nothing read yet.")
            self.chk_parts.setEnabled(False)
            self.chk_parts.setChecked(False)
            return
        bits = []
        if info.notated_key:
            bits.append(f"<span style='color:{theme.TEXT}; font-size:15px; "
                        f"font-weight:600'>{info.notated_key}</span>")
        facts = []
        if info.time_signature:
            facts.append(info.time_signature)
        facts.append(f"{info.measures} bars")
        facts.append(f"{info.parts} part{'s' if info.parts != 1 else ''}")
        if info.lowest and info.highest:
            facts.append(f"{info.lowest}–{info.highest}")
        bits.append(f"<span style='color:{theme.TEXT_DIM}'>"
                    + "  ·  ".join(facts) + "</span>")
        self.score_summary.setText("<br>".join(bits))
        # Part files only mean something when there is more than one part.
        self.chk_parts.setEnabled(info.parts > 1)
        if info.parts > 1:
            self.chk_parts.setText(f"Each of the {info.parts} parts in its own "
                                   f"file as well")
        else:
            self.chk_parts.setChecked(False)
            self.chk_parts.setText("Each part in its own file as well")
        for w in info.warnings:
            self.log("Note: " + w)
        if seconds:
            self.log(f"Read in {seconds:.0f}s.")
        if info.best_key:
            idx = self.cmb_source_key.findText(info.best_key, Qt.MatchFixedString)
            if idx >= 0:
                self.cmb_source_key.setCurrentIndex(idx)
        self._refresh_guidance()

    # ==================================================================
    # Transpose
    # ==================================================================
    def apply_transpose(self) -> None:
        if self.job is None or self.job.source_score is None:
            self.status_label.setText("Read some pages first.")
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
            self.transpose_label.setVisible(True)
            self.transpose_label.setText(
                f"{res.from_key} → {res.to_key}"
                f"<br><span style='color:{theme.TEXT_DIM}; font-weight:400'>"
                f"{res.interval_name.lower()}, {res.semitones:+d} half steps</span>")
            for n in res.notes:
                self.log(n)
            self._suggest_basename()
            self._render_preview(f"In {res.to_key}")

        self._run(work, done, "Transposing...")

    def _render_preview(self, caption: str) -> None:
        if self.job is None:
            return
        self.view_switch.buttons[1].setEnabled(True)
        self.view_switch.select(1)
        self.preview.set_message("Engraving...")
        engine, job = self.engine, self.job

        def work(log, cancelled, step):
            step("Engraving the preview...")
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
                    "No preview for this score - saving still works.")

        self._run(work, done, "Engraving the preview...")

    # ==================================================================
    # Save
    # ==================================================================
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
            self.status_label.setText("Nothing to save yet.")
            return

        basename = self.edit_basename.text().strip() or "Score"
        out_dir = Path(self.settings.output_dir)
        request = ExportRequest(
            pdf=self.chk_pdf.isChecked() and has_score,
            midi=self.chk_midi.isChecked() and has_score,
            musicxml=self.chk_xml.isChecked() and has_score,
            parts=self.chk_parts.isChecked() and self.chk_parts.isEnabled()
            and has_score,
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
            names = ", ".join(p.name for p in report.written)
            if report.written:
                self.status_dot.set_state("ok")
                self.status_label.setText(f"Saved to {out_dir.name}:  {names}")
            else:
                self.status_label.setText("Nothing was saved.")
            for skipped in report.skipped:
                self.log("Skipped - " + skipped)
            self.log(f"Saved {len(report.written)} file(s) to {out_dir}.")

        self._run(work, done, "Saving...")

    # ==================================================================
    # Updates, settings, misc
    # ==================================================================
    def _startup_update_check(self) -> None:
        from .. import updater
        if not self.settings.auto_update_check:
            return
        if not updater.due_for_check(self.settings.last_update_check):
            return
        self.check_updates(quiet=True)

    def check_updates(self, quiet: bool = False) -> None:
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
                if not quiet:
                    self.status_label.setText(message)
                return
            if quiet and release.version == self.settings.skipped_version:
                self.log(f"Version {release.version} available (skipped).")
                return
            self.log(f"Version {release.version} is available.")
            dialog = UpdateDialog(release, self)
            dialog.skip_requested.connect(self._skip_version)
            dialog.exec()

        def failed(message: str) -> None:
            self._update_thread = None
            self.log("Update check: " + message)
            if not quiet:
                self.status_label.setText(message)

        thread.finished_ok.connect(done)
        thread.failed.connect(failed)
        thread.start()

    def _skip_version(self, version: str) -> None:
        self.settings.skipped_version = version
        self.settings.save()
        self.log(f"Skipping version {version}.")

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            dlg.apply_to(self.settings)
            self.lbl_folder.setText(self.settings.output_dir)
            self._engine_check(verbose=False)

    def about(self) -> None:
        from .. import __version__
        QMessageBox.about(
            self, "About ScoreShift",
            f"<h3>ScoreShift {__version__}</h3>"
            "<p>Scan or photograph sheet music, straighten and crop the page, "
            "read the notes, and save the music in any key.</p>"
            "<p>Note reading by <b>Audiveris</b>. Engraving by <b>MuseScore</b> "
            "or <b>Verovio</b>. Music handling by <b>music21</b>.</p>")

    # ==================================================================
    # Drag and drop
    # ==================================================================
    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

    def _dropped_images(self, event) -> list[Path]:
        if not event.mimeData().hasUrls():
            return []
        out = []
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.suffix.lower() in self.IMAGE_SUFFIXES and path.is_file():
                out.append(path)
        return out

    def dragEnterEvent(self, event):  # noqa: D102, N802
        if self._dropped_images(event):
            event.acceptProposedAction()
            self.empty_state.setStyleSheet(
                self.empty_state.styleSheet().replace(
                    theme.BORDER_STRONG, theme.ACCENT))
        else:
            event.ignore()

    def dragLeaveEvent(self, event):  # noqa: D102, N802
        self.empty_state.setStyleSheet(
            self.empty_state.styleSheet().replace(theme.ACCENT,
                                                  theme.BORDER_STRONG))
        super().dragLeaveEvent(event)

    def dropEvent(self, event):  # noqa: D102, N802
        paths = self._dropped_images(event)
        self.empty_state.setStyleSheet(
            self.empty_state.styleSheet().replace(theme.ACCENT,
                                                  theme.BORDER_STRONG))
        if paths:
            event.acceptProposedAction()
            self.log(f"Dropped {len(paths)} file(s).")
            self._load_paths(paths)

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
    app.setPalette(theme.palette())
    app.setFont(theme.base_font())
    app.setStyleSheet(theme.stylesheet())
    win = MainWindow()
    win.show()
    return app.exec()
