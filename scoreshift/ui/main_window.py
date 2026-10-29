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
from PySide6.QtWidgets import (QFrame,  # noqa: F401
                               QApplication, QCheckBox, QComboBox, QDockWidget,
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
from ..pipeline import staves
from ..pipeline import omr, pagedetect
from ..pipeline import score as scoring
from ..pipeline import scanner as scanning
from . import icons, theme
from .capture_pane import CapturePane
from .dash import (CardRow, CircleButton, FactRow, NavRail, PageCard, Panel,
                   QualityBar, Segment, StatCard, StatTile)
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
    # One picture can hold two pages - an open booklet, or two sheets side by
    # side - and then it cleans up into two.
    parts: list[np.ndarray] = field(default_factory=list)
    readable: bool = True
    spread: bool = False             # holds two pages
    interline: float = 0.0           # staff spacing in this picture, px
    skew: float = 0.0                # how far it was rotated to straighten

    @property
    def page_count(self) -> int:
        if self.parts:
            return len(self.parts)
        return 2 if self.spread else 1

    @property
    def name(self) -> str:
        return self.source.name


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load()
        self.engine = ScoreShiftEngine(self.settings)
        self.pages: list[PageItem] = []
        self._row = 0
        self.job: ScoreJob | None = None
        self._job: Job | None = None
        self._engine_ready = False

        self.setWindowTitle("ScoreShift")
        self.resize(1360, 900)
        self.setMinimumSize(1120, 720)
        self._panel_auto_hidden = False
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

        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._build_nav())

        middle = QWidget()
        middle.setObjectName("Dashboard")
        column = QVBoxLayout(middle)
        column.setContentsMargins(20, 16, 20, 16)
        column.setSpacing(14)
        column.addLayout(self._build_title_row())
        column.addWidget(self._build_headline())
        column.addWidget(self._build_pages_panel())
        column.addWidget(self._build_stage(), 1)
        column.addWidget(self._build_summary())

        # The sections stack and scroll, so the working area keeps a usable
        # height however many of them are showing.
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroller.setWidget(middle)
        # Scoped, or every button inside the dashboard inherits "transparent".
        scroller.setObjectName("DashScroll")
        scroller.setStyleSheet(
            "#DashScroll, #DashScroll > QWidget > QWidget#Dashboard "
            "{ background: transparent; }")
        row.addWidget(scroller, 1)

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
    def _build_nav(self) -> QWidget:
        self.nav = NavRail([
            ("Pages", "page"),
            ("Capture", "camera"),
            ("Score", "score"),
            ("Save", "save"),
            ("Settings", "settings"),
        ])
        self.nav.changed.connect(self._switch_view)
        # The old two-way Page/Score switch and the rail are one thing now.
        self.view_switch = self.nav

        self.engine_pill = QWidget()
        pill = QHBoxLayout(self.engine_pill)
        pill.setContentsMargins(10, 8, 10, 8)
        pill.setSpacing(8)
        self.engine_dot = StatusDot("idle")
        self.engine_label = label("Checking the reader...", "Faint")
        self.engine_label.setWordWrap(True)
        pill.addWidget(self.engine_dot)
        pill.addWidget(self.engine_label, 1)
        self.engine_pill.setStyleSheet(
            f"background:{theme.SURFACE}; border-radius:{theme.RADIUS}px;")
        self.nav.add_footer(self.engine_pill)
        return self.nav

    def _build_title_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        headings = QVBoxLayout()
        headings.setSpacing(0)
        self.title_label = QLabel("Pages")
        self.title_label.setStyleSheet(
            f"color:{theme.TEXT}; font-size:26px; font-weight:600;")
        self.top_hint = label("", "Faint")
        self.top_hint.setWordWrap(False)
        headings.addWidget(self.title_label)
        headings.addWidget(self.top_hint)
        row.addLayout(headings)
        row.addStretch(1)

        self.btn_add_round = CircleButton("add", "Add photos or image files  (Ctrl+O)")
        self.btn_add_round.clicked.connect(self.add_images)
        self.btn_camera_round = CircleButton("camera", "Capture with a camera")
        self.btn_camera_round.clicked.connect(self.capture_from_camera)
        self.btn_scan_round = CircleButton("scanner", "Scan from your printer")
        self.btn_scan_round.clicked.connect(self.scan_pages)
        self.btn_more = CircleButton("settings", "More")
        self.btn_more.clicked.connect(self._show_menu)
        for b in (self.btn_add_round, self.btn_camera_round,
                  self.btn_scan_round, self.btn_more):
            row.addWidget(b)
        self.btn_panel = CircleButton("chevron-right", "Hide the controls  (Ctrl+B)")
        self.btn_panel.clicked.connect(self.toggle_panel)
        row.addWidget(self.btn_panel)
        return row

    def _build_headline(self) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)

        figures = QHBoxLayout()
        figures.setSpacing(28)
        self.stat_pages = StatTile("Pages", "0")
        self.stat_key = StatTile("Key", "-")
        self.stat_detail = StatTile("Detail", "-")
        for tile in (self.stat_pages, self.stat_key, self.stat_detail):
            figures.addWidget(tile)
        figures.addStretch(1)

        self.primary = button("Add pages", "add", "primary")
        self.primary.setMinimumHeight(40)
        self.primary.clicked.connect(self._do_primary)
        figures.addWidget(self.primary, 0, Qt.AlignVCenter)
        col.addLayout(figures)

        self.quality = QualityBar()
        col.addWidget(self.quality)
        return holder

    def _build_pages_panel(self) -> QWidget:
        self.pages_panel = Panel("Pages")
        self.cards = CardRow()
        self.cards.selected.connect(self._select_page)
        self.pages_panel.add(self.cards)
        self.pages_panel.setFixedHeight(PageCard.HEIGHT + 90)

        tools = QHBoxLayout()
        tools.setSpacing(4)
        self.btn_up = icon_button("up", "Move this page earlier", 30)
        self.btn_up.clicked.connect(lambda: self.move_page(-1))
        self.btn_down = icon_button("down", "Move this page later", 30)
        self.btn_down.clicked.connect(lambda: self.move_page(1))
        self.btn_remove = icon_button("trash", "Remove this page", 30)
        self.btn_remove.clicked.connect(self.remove_page)
        for b in (self.btn_up, self.btn_down, self.btn_remove):
            self.pages_panel.add_action(b)
        self.pages_panel.hide()
        return self.pages_panel

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
    # -- canvas ---------------------------------------------------------
    def _build_stage(self) -> QWidget:
        self.stage = Panel()
        self.stage_title = self.stage.title
        self.stage.set_title("")

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
        self.btn_page_zoom_out = icon_button("zoom-out", "Zoom out", 32)
        self.btn_page_zoom_out.clicked.connect(
            lambda: self.crop_view.zoom(1 / 1.25))
        self.btn_page_zoom_in = icon_button("zoom-in", "Zoom in  (Ctrl + wheel)", 32)
        self.btn_page_zoom_in.clicked.connect(lambda: self.crop_view.zoom(1.25))
        self.btn_page_fit = button("Fit", "crop", "quiet", "Fit the page to the window")
        self.btn_page_fit.clicked.connect(lambda: self.crop_view.zoom_fit())
        self.chk_show_clean = QCheckBox("Cleaned")
        self.chk_show_clean.setToolTip("Show the page as the note reader sees it")
        self.chk_show_clean.toggled.connect(self._refresh_page_view)
        for w in (self.btn_detect, self.btn_full, self.btn_rotate,
                  self.btn_page_zoom_out, self.btn_page_zoom_in,
                  self.btn_page_fit, self.chk_show_clean):
            page_tools.addWidget(w)
        self.stage.add_action(self.page_tools)

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
        self.stage.add_action(self.score_tools)
        self.score_tools.hide()

        self.stack = QStackedWidget()
        self.crop_view = CropView()
        self.crop_view.quad_changed.connect(self._on_quad_changed)
        self.capture = CapturePane(self.settings)
        self.capture.pages_ready.connect(self._captured_pages)
        self.capture.scan_requested.connect(self._run_scan)
        self.capture.closed.connect(self._capture_closed)
        self.capture.message.connect(self.log)
        self.preview = ScorePreview()
        self.empty_state = self._build_empty_state()
        self.score_empty = self._build_score_empty()
        self.stack.addWidget(self.crop_view)       # 0
        self.stack.addWidget(self.capture)         # 1
        self.stack.addWidget(self.preview)         # 2
        self.stack.addWidget(self.empty_state)
        self.stack.addWidget(self.score_empty)
        self.stack.setCurrentWidget(self.empty_state)
        self.stack.setMinimumHeight(430)
        self.stage.add(self.stack, 1)

        self.page_hint = label("", "Faint")
        self.stage.add(self.page_hint)
        return self.stage

    def _build_score_empty(self) -> QWidget:
        """What the Score view shows before there is a score.

        A piece that already exists as a file does not need a camera: this is
        where it comes in.
        """
        holder = QWidget()
        holder.setObjectName("ScoreDrop")
        holder.setStyleSheet(f"""
            #ScoreDrop {{ background: {theme.CANVAS};
                          border: 1.5px dashed {theme.BORDER_STRONG};
                          border-radius: {theme.RADIUS}px; }}
        """)
        col = QVBoxLayout(holder)
        col.setAlignment(Qt.AlignCenter)
        col.setSpacing(12)

        art = QLabel()
        art.setPixmap(icons.pixmap("score", 52, theme.BORDER_STRONG))
        art.setAlignment(Qt.AlignCenter)
        col.addWidget(art)

        headline = label("Drop a MusicXML or MIDI file here", "Title")
        headline.setAlignment(Qt.AlignCenter)
        col.addWidget(headline)

        sub = label("Already have the music as a file? Skip the scanning - "
                    "open it, change the key, save it.", "Faint")
        sub.setAlignment(Qt.AlignCenter)
        col.addWidget(sub)

        row = QHBoxLayout()
        row.addStretch(1)
        open_button = button("Open a score file", "folder", "primary")
        open_button.clicked.connect(self.open_score_file)
        row.addWidget(open_button)
        row.addStretch(1)
        col.addLayout(row)

        formats = label(".musicxml  ·  .xml  ·  .mxl  ·  .mid  ·  .krn", "Faint")
        formats.setAlignment(Qt.AlignCenter)
        col.addWidget(formats)
        return holder

    def _build_summary(self) -> QWidget:
        self.summary_panel = Panel("Summary")
        row = QHBoxLayout()
        row.setSpacing(14)

        facts = QVBoxLayout()
        facts.setSpacing(6)
        self.fact_key = FactRow("Key")
        self.fact_time = FactRow("Time and bars")
        self.fact_parts = FactRow("Parts")
        self.fact_range = FactRow("Range")
        for f in (self.fact_key, self.fact_time, self.fact_parts, self.fact_range):
            facts.addWidget(f)
        facts.addStretch(1)
        row.addLayout(facts, 3)

        self.card_detail = StatCard("Detail per page")
        self.card_output = StatCard("Ready to save")
        row.addWidget(self.card_detail, 2)
        row.addWidget(self.card_output, 2)
        self.summary_panel.add_layout(row)
        self.summary_panel.hide()
        return self.summary_panel

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
            ("Open a score file", "score", self.open_score_file),
        ):
            b = button(text, icon_name)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        col.addLayout(row)
        return holder

    def _switch_view(self, index: int) -> None:
        titles = ["Pages", "Capture", "Score", "Save", "Settings"]
        self.title_label.setText(titles[index] if index < len(titles) else "")
        if index == 3:                      # Save: the panel, not a view
            if not self.panel.is_open:
                self.panel.show_panel()
            self.nav.select(self.view_switch.current if
                            self.view_switch.current < 3 else 0, notify=False)
            self.title_label.setText("Pages")
            self.edit_basename.setFocus()
            return
        if index == 4:                      # Settings
            self.open_settings()
            self.nav.select(0, notify=False)
            self.title_label.setText("Pages")
            return
        if index != 1:
            # Leaving the capture view: let go of the camera rather than
            # holding it open behind another screen.
            self.capture.stop()
        self.page_tools.setVisible(index == 0)
        self.score_tools.setVisible(index == 2)
        if index == 0:
            self._refresh_page_view()
        elif index == 1:
            self.stack.setCurrentWidget(self.capture)
            self.page_hint.setText(
                "The outline follows the page as you move it. It shoots by "
                "itself once the page is steady - or press Space.")
            self.capture.open_camera()
            self.capture.setFocus()
        else:
            has_score = self.job is not None and self.job.source_score is not None
            self.stack.setCurrentWidget(
                self.preview if has_score else self.score_empty)
            self.score_tools.setVisible(has_score)
            if not has_score:
                self.top_hint.setText("Open a file, or read some pages first")
            self.page_hint.setText(
                "" if has_score else
                "A MusicXML or MIDI file can be dropped straight onto this "
                "window - there is nothing to scan or read.")

    # -- slide-over panel ------------------------------------------------
    def _build_panel(self) -> QWidget:
        self.panel = SlideOver("Controls")
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
        self.chk_split = QCheckBox("Split two-page shots")
        self.chk_split.setChecked(True)
        self.chk_split.setToolTip(
            "An open booklet, or two sheets side by side, becomes two pages. "
            "Turn this off to keep the picture whole.")
        self.chk_split.toggled.connect(self._invalidate_processed)
        more.add(self.chk_split)
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
        if self.view_switch.current == 2 and self.job is None:
            self.top_hint.setText("Open a file, or read some pages first")
        else:
            self.top_hint.setText(hints.get(step, ""))

        has_pages = bool(self.pages)
        for b in (self.btn_up, self.btn_down, self.btn_remove, self.btn_detect,
                  self.btn_full, self.btn_rotate, self.btn_page_zoom_in,
                  self.btn_page_zoom_out, self.btn_page_fit,
                  self.chk_show_clean):
            b.setEnabled(has_pages)
        self.page_tools.setVisible(has_pages and self.view_switch.current == 0)
        self.pages_panel.setVisible(has_pages)
        self.pages_panel.set_title(
            f"Pages  ·  {sum(p.page_count for p in self.pages)}" if has_pages
            else "Pages")
        self._refresh_headline()
        self._refresh_summary()

        has_score = self.job is not None and self.job.source_score is not None
        self.btn_transpose.setEnabled(has_score)
        self.btn_export.setEnabled(has_score or self.chk_scanpdf.isChecked())
        if self.view_switch.current == 2:
            self._switch_view(2)

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
        self._panel_auto_hidden = False
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
                # One staff analysis serves the outline, the spread check
                # and the detail figure on the page's card.
                info = staves.analyse(img)
                quad = pagedetect.detect_page(img, info=info) if auto else None
                log(f"{p.name}: "
                    f"{'page edges found' if quad is not None else 'no page edge found'}")
                flat = pagedetect.warp_quad(img, quad) if quad is not None else img
                spread = pagedetect.find_gutter(flat) is not None
                if spread:
                    log(f"{p.name}: two pages in this one - they will be "
                        "saved separately.")
                items.append(PageItem(source=p, original=img, quad=quad,
                                      spread=spread, interline=info.interline))
            return items

        def done(items):
            self.pages.extend(items)
            self._row = len(self.pages) - 1
            self._rebuild_cards()
            self.view_switch.select(0)
            self._switch_view(0)
            self._suggest_basename()
            total = sum(p.page_count for p in self.pages)
            if total != len(self.pages):
                self.status_label.setText(
                    f"{len(self.pages)} picture{'s' if len(self.pages) != 1 else ''} "
                    f"holding {total} pages, ready.")
            else:
                self.status_label.setText(
                    f"{total} page{'s' if total != 1 else ''} ready.")
            self._refresh_guidance()

        self._run(work, done, f"Loading {len(paths)} image(s)...")

    def _rebuild_cards(self) -> None:
        """Redraw the page cards from the pages themselves."""
        self.cards.clear()
        for index, item in enumerate(self.pages):
            card = self.cards.add_card()
            interline = item.interline
            if interline <= 0:
                detail, delta, tone = "-", "not measured yet", theme.TEXT_DIM
            elif interline >= staves.MIN_GOOD_INTERLINE:
                detail, delta, tone = f"{interline:.0f} px", "good detail", theme.SUCCESS
            elif interline >= staves.MIN_OPTICAL_INTERLINE:
                detail, delta, tone = f"{interline:.0f} px", "usable", theme.SUCCESS
            elif interline >= 6:
                detail, delta, tone = (f"{interline:.0f} px",
                                       "rough - expect mistakes", theme.WARNING)
            else:
                detail, delta, tone = (f"{interline:.0f} px",
                                       "too small to read", theme.DANGER)
            h, w = item.original.shape[:2]
            thumb = cv2.resize(item.original, (THUMB.width(), THUMB.height()),
                               interpolation=cv2.INTER_AREA)
            subtitle = (f"{item.page_count} pages in one" if item.page_count > 1
                        else item.name[:18])
            card.set_page(
                title=f"Page {index + 1}", subtitle=subtitle, detail=detail,
                delta=delta, delta_tone=tone, size=f"{w} x {h}",
                tilt=f"{item.skew:+.1f}" if item.skew else "level",
                thumb=ndarray_to_qpixmap(thumb))
        self._row = max(0, min(self._row, len(self.pages) - 1))
        self.cards.set_selected(self._row)
        self.pages_panel.setVisible(bool(self.pages))
        self._refresh_headline()

    def _select_page(self, index: int) -> None:
        self._row = index
        self.cards.set_selected(index)
        self.nav.select(0)
        self._switch_view(0)
        self._refresh_page_view()

    def _refresh_headline(self) -> None:
        """The three figures and the quality bar along the top."""
        total = sum(p.page_count for p in self.pages)
        self.stat_pages.set(str(total),
                            f"from {len(self.pages)} picture"
                            f"{'s' if len(self.pages) != 1 else ''}"
                            if total != len(self.pages) else "")

        if self.job is not None and self.job.transpose is not None:
            move = self.job.transpose
            self.stat_key.set(move.to_key or "-", f"was {move.from_key}")
        elif self.job is not None and self.job.info is not None:
            self.stat_key.set(self.job.info.best_key or "-", "not changed yet")
        else:
            self.stat_key.set("-", "nothing read yet")

        measured = [p.interline for p in self.pages if p.interline > 0]
        if measured:
            worst = min(measured)
            tone = (theme.SUCCESS if worst >= staves.MIN_OPTICAL_INTERLINE
                    else theme.WARNING if worst >= 6 else theme.DANGER)
            self.stat_detail.set(f"{worst:.0f} px",
                                 "between staff lines, at worst", tone)
        else:
            self.stat_detail.set("-", "measured when pages come in")

        bands = [
            ("Good", theme.SUCCESS, staves.MIN_GOOD_INTERLINE, 1e9),
            ("Usable", theme.CHART[2], staves.MIN_OPTICAL_INTERLINE,
             staves.MIN_GOOD_INTERLINE),
            ("Rough", theme.WARNING, 6.0, staves.MIN_OPTICAL_INTERLINE),
            ("Too far", theme.DANGER, 0.0, 6.0),
        ]
        segments = []
        for name, colour, low, high in bands:
            count = sum(1 for value in measured if low <= value < high)
            if count:
                segments.append(Segment(
                    name, float(count), colour,
                    f"{count} page{'s' if count != 1 else ''}"))
        waiting = len(self.pages) - len(measured)
        if waiting > 0:
            segments.append(Segment("Not measured", float(waiting),
                                    theme.BORDER_STRONG, f"{waiting} waiting"))
        self.quality.set_segments(segments)

    def _refresh_summary(self) -> None:
        info = self.job.info if self.job is not None else None
        if info is None:
            self.summary_panel.hide()
            return
        self.summary_panel.show()
        move = self.job.transpose if self.job is not None else None
        self.fact_key.set(f"{move.from_key} to {move.to_key}" if move
                          else (info.best_key or "-"))
        self.fact_time.set(f"{info.time_signature or '-'}  ·  {info.measures} bars")
        self.fact_parts.set(f"{info.parts} part{'s' if info.parts != 1 else ''}")
        self.fact_range.set(f"{info.lowest}-{info.highest}"
                            if info.lowest else "-")

        measured = [p.interline for p in self.pages if p.interline > 0]
        if measured:
            colours = []
            for value in measured:
                colours.append(
                    theme.SUCCESS if value >= staves.MIN_OPTICAL_INTERLINE
                    else theme.WARNING if value >= 6 else theme.DANGER)
            self.card_detail.chart.set_values(measured, colours)
            self.card_detail.set(f"{min(measured):.0f} px",
                                 "the page that will read worst")
        else:
            self.card_detail.set("-", "")

        wanted = [name for name, box in (
            ("PDF", self.chk_pdf), ("MIDI", self.chk_midi),
            ("MusicXML", self.chk_xml), ("Scan PDF", self.chk_scanpdf))
            if box.isChecked()]
        parts = info.parts if self.chk_parts.isChecked() else 0
        files = len(wanted) + (parts * 2 if parts > 1 else 0)
        self.card_output.set(f"{files} file{'s' if files != 1 else ''}",
                             ", ".join(wanted) or "nothing ticked")
        self.card_output.chart.set_values(
            [1.0] * max(1, files), [theme.ACCENT] * max(1, files))

    def capture_from_camera(self) -> None:
        self.view_switch.select(1)
        self._switch_view(1)

    def _captured_pages(self, paths: list) -> None:
        self.log(f"Captured {len(paths)} page(s).")
        self._load_paths([Path(p) for p in paths])

    def _capture_closed(self) -> None:
        self.view_switch.select(0)
        self._switch_view(0)

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

        self.view_switch.select(1)
        self._switch_view(1)
        self.capture.open_scanner()

    def _run_scan(self, options: dict) -> None:
        out = work_dir() / "scans"
        device_id = options["device"]
        dpi, mode = int(options["dpi"]), options["mode"]
        source, max_pages = options["source"], int(options["pages"])
        self.settings.scan_dpi, self.settings.scan_mode = dpi, mode
        self.settings.scan_source = source
        self.settings.save()

        def work(log, cancelled, step):
            step(f"Scanning at {dpi} DPI ({mode}, {source})...")
            return scanning.acquire(out, device_id=device_id, dpi=dpi, mode=mode,
                                    source=source, max_pages=max_pages, log=log)

        def done(paths):
            self.log(f"Scanned {len(paths)} page(s).")
            self._load_paths([Path(p) for p in paths])
            self.view_switch.select(0)
            self._switch_view(0)

        self._run(work, done, "Scanning...")

    def remove_page(self) -> None:
        if not 0 <= self._row < len(self.pages):
            return
        self.pages.pop(self._row)
        self._row = min(self._row, len(self.pages) - 1)
        self._rebuild_cards()
        self._refresh_page_view()
        self._refresh_guidance()

    def move_page(self, delta: int) -> None:
        row, new = self._row, self._row + delta
        if row < 0 or not (0 <= new < len(self.pages)):
            return
        self.pages[row], self.pages[new] = self.pages[new], self.pages[row]
        self._row = new
        self._rebuild_cards()

    def _current_page(self) -> PageItem | None:
        return (self.pages[self._row]
                if 0 <= self._row < len(self.pages) else None)

    def _refresh_page_view(self) -> None:
        if self.view_switch.current != 0:
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
        item.interline = 0.0
        self._rebuild_cards()
        self._refresh_page_view()

    def _clean_options(self) -> CleanOptions:
        return CleanOptions(
            auto_crop=self.chk_crop.isChecked(),
            deskew=self.chk_deskew.isChecked(),
            enhance=self.chk_enhance.isChecked(),
            binarize=self.chk_binarize.isChecked(),
            split_spreads=self.chk_split.isChecked(),
        )

    def _process_item(self, item: PageItem) -> pagedetect.PageResult:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            results = pagedetect.process_capture(
                item.original, quad=item.quad, **self._clean_options().as_kwargs())
            item.parts = [r.image for r in results]
            item.processed = results[0].image
            item.notes = results[0].notes
            item.readable = all(r.readable for r in results)
            item.spread = len(results) > 1
            item.interline = results[0].interline
            item.skew = results[0].skew_deg
            self._rebuild_cards()
            return results[0]
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
            step(f"Cleaning {len(pages)} picture(s)...")
            cleaned = []
            for page in pages:
                # One at a time, so a picture that turns out to hold two pages
                # can be tied back to the row it came from.
                parts = engine.clean_pages(
                    [page.original], options, quads=[page.quad],
                    log=log, cancel=cancelled)
                page.parts = [part.image for part in parts]
                page.processed = parts[0].image
                page.readable = all(part.readable for part in parts)
                cleaned.extend(parts)
            if len(cleaned) > len(pages):
                log(f"{len(pages)} picture(s) held {len(cleaned)} pages.")
            if any(not part.readable for part in cleaned):
                log("Some pages are too small in the frame to read reliably - "
                    "fill the frame with one page at a time for the best result.")
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
            "Scores (*.musicxml *.xml *.mxl *.mid *.midi *.krn);;"
            "MusicXML (*.musicxml *.xml *.mxl);;MIDI (*.mid *.midi);;"
            "All files (*)")
        if path:
            self.load_score_file(Path(path))

    def load_score_file(self, path: Path) -> None:
        """Open a score someone else has already written.

        MusicXML out of MuseScore, Finale or Sibelius, a MIDI file, a Humdrum
        kern file: there is nothing to photograph or read, so the piece lands
        ready for a key change and a save.
        """
        engine = self.engine
        self.settings.last_input_dir = str(path.parent)
        self.settings.save()

        def work(log, cancelled, step):
            step(f"Reading {path.name}...")
            return engine.load_score(path)

        def done(job: ScoreJob):
            self.job = job
            self._show_score_info(job.info)
            self._suggest_basename()
            self._render_preview("Original key")
            self.log(f"Opened {path.name}.")
            self.nav.select(2)
            self._switch_view(2)
            self.status_label.setText(
                f"{path.name} opened - pick a key and save.")

        self._run(work, done, f"Opening {path.name}...")

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
        self.view_switch.select(2)
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
    SCORE_SUFFIXES = {".musicxml", ".xml", ".mxl", ".mid", ".midi", ".krn"}

    def _dropped_files(self, event, suffixes: set[str]) -> list[Path]:
        if not event.mimeData().hasUrls():
            return []
        out = []
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.suffix.lower() in suffixes and path.is_file():
                out.append(path)
        return out

    def _dropped_images(self, event) -> list[Path]:
        return self._dropped_files(event, self.IMAGE_SUFFIXES)

    def _dropped_scores(self, event) -> list[Path]:
        return self._dropped_files(event, self.SCORE_SUFFIXES)

    def dragEnterEvent(self, event):  # noqa: D102, N802
        if self._dropped_images(event) or self._dropped_scores(event):
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
        self.empty_state.setStyleSheet(
            self.empty_state.styleSheet().replace(theme.ACCENT,
                                                  theme.BORDER_STRONG))
        scores = self._dropped_scores(event)
        if scores:
            # A score that is already written needs no camera and no reading:
            # it goes straight to the key change.
            event.acceptProposedAction()
            self.log(f"Dropped {scores[0].name}.")
            self.load_score_file(scores[0])
            if len(scores) > 1:
                self.log("Only the first score was opened - "
                         "ScoreShift works on one piece at a time.")
            return
        paths = self._dropped_images(event)
        if paths:
            event.acceptProposedAction()
            self.log(f"Dropped {len(paths)} file(s).")
            self._load_paths(paths)

    def resizeEvent(self, event):  # noqa: D102, N802
        super().resizeEvent(event)
        # The dashboard needs room: on a narrow window the controls get out
        # of the way by themselves, and come back when there is space again.
        narrow = self.width() < 1300
        if narrow and self.panel.is_open:
            self.panel.hide_panel()
            self._panel_auto_hidden = True
            self.btn_panel.setIcon(icons.icon("chevron-left", 17, theme.TEXT_DIM))
        elif not narrow and self._panel_auto_hidden:
            self.panel.show_panel()
            self._panel_auto_hidden = False
            self.btn_panel.setIcon(icons.icon("chevron-right", 17, theme.TEXT_DIM))

    def closeEvent(self, event):  # noqa: D102, N802
        self.capture.stop()
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
