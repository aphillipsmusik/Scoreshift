"""Capturing pages inside the main window - no second window to manage.

The camera lives in the canvas, beside Pages and Score, so the live view, the
page outline and the pages you have already taken are all in the window you
were already looking at. The scanner sits in the same pane behind a switch,
because it answers the same question: how do the pages get in.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QSize, Qt, QThread, Signal
from PySide6.QtGui import (QBrush, QColor, QIcon, QPainter, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QSizePolicy,
                               QSlider, QSpinBox, QStackedWidget, QVBoxLayout,
                               QWidget)

from ..config import Settings, work_dir
from ..pipeline import camera as cam
from . import icons, theme
from .crop_view import ndarray_to_qpixmap
from .widgets import Segmented, button, icon_button, label

RESOLUTIONS = [
    ("Best the camera offers", 0, 0),
    ("1280 x 720", 1280, 720),
    ("1920 x 1080", 1920, 1080),
    ("2560 x 1440", 2560, 1440),
    ("3840 x 2160", 3840, 2160),
    ("640 x 480", 640, 480),
]

STATE_COLOURS = {
    "searching": QColor(theme.TEXT_FAINT),
    "adjusting": QColor(theme.WARNING),
    "holding": QColor(theme.ACCENT),
    "ready": QColor(theme.SUCCESS),
}


# ---------------------------------------------------------------------------
# The camera, on its own thread
# ---------------------------------------------------------------------------

class CameraWorker(QThread):
    frame_ready = Signal(object, object)      # ndarray, FrameCheck
    captured = Signal(object)                 # ndarray (full resolution)
    size_chosen = Signal(int, int)            # what the camera actually gave
    zoom_mode = Signal(str)                   # "camera" or "crop"
    failed = Signal(str)

    def __init__(self, index: int, width: int, height: int, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.width = width
        self.height = height
        self.auto = True
        self._stop = False
        self._shutter = False
        self.zoom = 1.0
        self.centre = (0.5, 0.5)
        self.tracker = cam.PageTracker()

    def stop(self) -> None:
        self._stop = True

    def shutter(self) -> None:
        self._shutter = True

    def run(self) -> None:  # noqa: D102
        try:
            stream = cam.CameraStream(self.index, self.width or 1920,
                                      self.height or 1080)
            if not self.width:
                size = stream.best_size()
                self.size_chosen.emit(int(size[0]), int(size[1]))
            else:
                self.size_chosen.emit(self.width, self.height)
        except cam.CameraError as exc:
            self.failed.emit(str(exc))
            return

        # A camera that can zoom itself is worth using: it crops its sensor
        # before handing the frame over, so the page arrives with more detail
        # rather than less. Otherwise the frame is cropped here, which costs
        # detail but still takes the desk out of the picture.
        base = stream.zoom_base()
        self.zoom_mode.emit("camera" if base else "crop")
        applied = None
        try:
            while not self._stop:
                frame = stream.read()
                if frame is None:
                    time.sleep(0.03)
                    continue
                if base:
                    if applied != self.zoom:
                        stream.set_zoom(base * min(self.zoom, cam.MAX_ZOOM))
                        applied = self.zoom
                else:
                    frame = cam.crop_zoom(frame, self.zoom, self.centre)
                check = self.tracker.check(frame)
                take = self._shutter or (self.auto
                                         and self.tracker.should_capture(check))
                self._shutter = False
                self.frame_ready.emit(frame, check)
                if take:
                    self.tracker.block()
                    self.captured.emit(frame.copy())
                    time.sleep(0.05)
        finally:
            stream.release()


# ---------------------------------------------------------------------------
# Live view
# ---------------------------------------------------------------------------

class CameraView(QWidget):
    """The feed, with the page outlined as it moves."""

    panned = Signal(float, float)       # fractions of the whole frame

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(360, 240)
        self._pix: QPixmap | None = None
        self._quad: np.ndarray | None = None
        self._state = "searching"
        self._pages = 1
        self._flash = 0.0
        self._placeholder = "Starting the camera..."
        self._zoom = 1.0
        self._pannable = False
        self._drag: tuple[float, float] | None = None
        self._scaled_width = 1.0


    def update_frame(self, frame: np.ndarray, check) -> None:
        self._pix = ndarray_to_qpixmap(frame)
        self._quad = check.quad
        self._state = check.state
        self._pages = getattr(check, "pages_in_shot", 1)
        self.update()

    def clear(self, message: str = "Starting the camera...") -> None:
        self._pix = None
        self._quad = None
        self._state = "searching"
        self._placeholder = message
        self.update()

    def flash(self) -> None:
        self._flash = time.time()
        self.update()

    def set_window(self, zoom: float, centre: tuple[float, float],
                   pannable: bool) -> None:
        self._zoom = zoom
        self._pannable = pannable and zoom > 1.001
        self.setCursor(Qt.OpenHandCursor if self._pannable else Qt.ArrowCursor)
        self.update()

    # Dragging moves the framed window, so the page can be anywhere on the
    # desk and still be the only thing the detector is shown.
    def mousePressEvent(self, event):  # noqa: D102, N802
        if self._pannable and event.button() == Qt.LeftButton:
            self._drag = (event.position().x(), event.position().y())
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: D102, N802
        if self._drag is not None:
            x, y = event.position().x(), event.position().y()
            dx, dy = x - self._drag[0], y - self._drag[1]
            self._drag = (x, y)
            span = max(self._scaled_width, 1.0)
            self.panned.emit(-(dx / span) / self._zoom,
                             -(dy / span) / self._zoom)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: D102, N802
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):  # noqa: D102, N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.CANVAS))
        if self._pix is None or self._pix.isNull():
            painter.setPen(QColor(theme.TEXT_FAINT))
            painter.drawText(self.rect(), Qt.AlignCenter, self._placeholder)
            painter.end()
            return

        scaled = self._pix.scaled(self.size(), Qt.KeepAspectRatio,
                                  Qt.SmoothTransformation)
        ox = (self.width() - scaled.width()) / 2
        oy = (self.height() - scaled.height()) / 2
        painter.drawPixmap(int(ox), int(oy), scaled)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._scaled_width = float(scaled.width())

        if self._zoom > 1.001:
            badge = f"{self._zoom:.1f}x"
            painter.setPen(QColor(theme.TEXT))
            painter.setBrush(QBrush(QColor(0, 0, 0, 150)))
            painter.drawRoundedRect(int(ox) + 10, int(oy) + 10, 44, 22, 8, 8)
            painter.drawText(int(ox) + 10, int(oy) + 10, 44, 22,
                             Qt.AlignCenter, badge)

        if self._quad is not None and self._pix.width() > 0:
            sx = scaled.width() / self._pix.width()
            sy = scaled.height() / self._pix.height()
            pts = [QPointF(ox + x * sx, oy + y * sy) for x, y in self._quad]
            polygon = QPolygonF(pts)
            colour = STATE_COLOURS.get(self._state, STATE_COLOURS["searching"])

            # Everything that is not the page goes dim, the way a document
            # scanner shows you what it is about to keep.
            outside = QPolygonF([
                QPointF(ox, oy), QPointF(ox + scaled.width(), oy),
                QPointF(ox + scaled.width(), oy + scaled.height()),
                QPointF(ox, oy + scaled.height()),
            ]).subtracted(polygon)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(8, 10, 14, 110)))
            painter.drawPolygon(outside)

            fill = QColor(colour)
            fill.setAlpha(30 if self._state in ("holding", "ready") else 14)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(colour, 3))
            painter.drawPolygon(polygon)

            if self._pages > 1:
                # Where the two pages will be cut apart.
                top = (pts[0] + pts[1]) / 2
                bottom = (pts[3] + pts[2]) / 2
                pen = QPen(colour, 2, Qt.DashLine)
                painter.setPen(pen)
                painter.drawLine(top, bottom)

            painter.setBrush(QBrush(colour))
            painter.setPen(QPen(QColor(theme.CANVAS), 2))
            for p in pts:
                painter.drawEllipse(p, 6, 6)

        if self._flash and time.time() - self._flash < 0.18:
            painter.fillRect(self.rect(), QColor(255, 255, 255, 140))
            self.update()
        painter.end()


# ---------------------------------------------------------------------------
# The pane
# ---------------------------------------------------------------------------

class CapturePane(QWidget):
    """Camera and scanner, inside the main window."""

    pages_ready = Signal(list)          # list[Path], captured and kept
    scan_requested = Signal(dict)       # the main window runs the scan
    closed = Signal()
    message = Signal(str)

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.captured_paths: list[Path] = []
        self.worker: CameraWorker | None = None
        self._out_dir = work_dir() / "camera"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.source_switch = Segmented([("Camera", "camera"),
                                        ("Scanner", "scanner")])
        self.source_switch.changed.connect(self._source_changed)
        top.addWidget(self.source_switch)

        self.controls = QStackedWidget()
        self.controls.addWidget(self._camera_controls())
        self.controls.addWidget(self._scanner_controls())
        top.addWidget(self.controls, 1)
        root.addLayout(top)

        self.view = CameraView()
        self.view.panned.connect(self.pan)
        self.scanner_art = self._scanner_art()
        self.stage = QStackedWidget()
        self.stage.addWidget(self.view)
        self.stage.addWidget(self.scanner_art)
        root.addWidget(self.stage, 1)

        self.status = label("Starting the camera...", "Title")
        self.status.setAlignment(Qt.AlignCenter)
        root.addWidget(self.status)

        self.detail = label("", "Faint")
        self.detail.setAlignment(Qt.AlignCenter)
        root.addWidget(self.detail)

        self.strip = QListWidget()
        self.strip.setFlow(QListWidget.LeftToRight)
        self.strip.setIconSize(QSize(72, 94))
        self.strip.setFixedHeight(122)
        self.strip.setSelectionMode(QListWidget.SingleSelection)
        self.strip.hide()
        root.addWidget(self.strip)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_shutter = button("Take page", "camera", "primary",
                                  "Take a page now  (Space)")
        self.btn_shutter.clicked.connect(self.take_shot)
        self.btn_scan = button("Scan now", "scanner", "primary")
        self.btn_scan.clicked.connect(self._start_scan)
        self.btn_scan.hide()
        self.btn_remove = button("Remove", "trash", "quiet")
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_remove.setEnabled(False)
        buttons.addWidget(self.btn_shutter)
        buttons.addWidget(self.btn_scan)
        buttons.addWidget(self.btn_remove)
        buttons.addStretch(1)
        self.lbl_count = label("", "Faint")
        buttons.addWidget(self.lbl_count)
        self.btn_done = button("Add to pages", "check", "primary")
        self.btn_done.clicked.connect(self.finish)
        self.btn_done.setEnabled(False)
        self.btn_stop = button("Done", "close", "quiet")
        self.btn_stop.clicked.connect(self.cancel)
        buttons.addWidget(self.btn_done)
        buttons.addWidget(self.btn_stop)
        root.addLayout(buttons)

    # -- building blocks --------------------------------------------------
    def _camera_controls(self) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.cmb_camera = QComboBox()
        self.cmb_res = QComboBox()
        for combo in (self.cmb_camera, self.cmb_res):
            # Let the row give way on a narrow window rather than forcing
            # the whole dashboard wider than the screen.
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            combo.setMinimumWidth(96)
        for text, _, _ in RESOLUTIONS:
            self.cmb_res.addItem(text)
        self.cmb_res.setCurrentIndex(0)
        self.lbl_size = label("", "Faint")
        self.chk_auto = QCheckBox("Shoot when steady")
        self.chk_auto.setChecked(True)
        self.chk_auto.toggled.connect(self._auto_toggled)

        # Framing the page by hand is the quickest way to stop the detector
        # being offered the desk, the tray and everything else on it.
        self.zoom = 1.0
        self.centre = [0.5, 0.5]
        self._zoom_mode = "crop"
        self.btn_zoom_out = icon_button("zoom-out", "Widen the view", 30)
        self.btn_zoom_out.clicked.connect(lambda: self.nudge_zoom(1 / 1.25))
        self.slider_zoom = QSlider(Qt.Horizontal)
        self.slider_zoom.setRange(10, int(cam.MAX_ZOOM * 10))
        self.slider_zoom.setValue(10)
        self.slider_zoom.setFixedWidth(110)
        self.slider_zoom.setToolTip(
            "Zoom in to show the detector nothing but the page")
        self.slider_zoom.valueChanged.connect(
            lambda v: self.set_zoom(v / 10.0, from_slider=True))
        self.btn_zoom_in = icon_button("zoom-in", "Tighten the view", 30)
        self.btn_zoom_in.clicked.connect(lambda: self.nudge_zoom(1.25))
        self.lbl_zoom = label("1.0x", "Faint")
        self.lbl_zoom.setFixedWidth(34)
        self.cmb_camera.currentIndexChanged.connect(lambda _: self.restart())
        self.cmb_res.currentIndexChanged.connect(lambda _: self.restart())
        row.addStretch(1)
        row.addWidget(self.cmb_camera, 2)
        row.addWidget(self.cmb_res)
        row.addWidget(self.lbl_size)
        row.addWidget(self.btn_zoom_out)
        row.addWidget(self.slider_zoom)
        row.addWidget(self.btn_zoom_in)
        row.addWidget(self.lbl_zoom)
        row.addWidget(self.chk_auto)
        return holder

    def _scanner_controls(self) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.cmb_scanner = QComboBox()
        self.cmb_paper = QComboBox()
        self.cmb_paper.addItems(["Flatbed", "Feeder"])
        self.cmb_paper.setCurrentText(self.settings.scan_source)
        self.spin_dpi = QSpinBox()
        self.spin_dpi.setRange(150, 1200)
        self.spin_dpi.setSingleStep(50)
        self.spin_dpi.setValue(self.settings.scan_dpi)
        self.spin_dpi.setSuffix(" DPI")
        self.cmb_colour = QComboBox()
        self.cmb_colour.addItems(["Grayscale", "Color", "Black and white"])
        self.cmb_colour.setCurrentText(self.settings.scan_mode)
        self.spin_pages = QSpinBox()
        self.spin_pages.setRange(1, 100)
        self.spin_pages.setValue(20)
        self.spin_pages.setPrefix("up to ")
        self.spin_pages.setSuffix(" pages")
        for w in (self.cmb_scanner, self.cmb_paper, self.spin_dpi,
                  self.cmb_colour, self.spin_pages):
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            w.setMinimumWidth(88)
        row.addStretch(1)
        row.addWidget(self.cmb_scanner, 2)
        row.addWidget(self.cmb_paper)
        row.addWidget(self.spin_dpi)
        row.addWidget(self.cmb_colour)
        row.addWidget(self.spin_pages)
        return holder

    def _scanner_art(self) -> QWidget:
        holder = QWidget()
        holder.setObjectName("ScanStage")
        holder.setStyleSheet(f"""
            #ScanStage {{ background: {theme.CANVAS};
                          border: 1.5px dashed {theme.BORDER_STRONG};
                          border-radius: {theme.RADIUS}px; }}
        """)
        col = QVBoxLayout(holder)
        col.setAlignment(Qt.AlignCenter)
        col.setSpacing(12)
        art = QLabel()
        art.setPixmap(icons.pixmap("scanner", 52, theme.BORDER_STRONG))
        art.setAlignment(Qt.AlignCenter)
        col.addWidget(art)
        self.scan_headline = label("Put a page on the glass", "Title")
        self.scan_headline.setAlignment(Qt.AlignCenter)
        col.addWidget(self.scan_headline)
        hint = label("300 DPI grayscale reads best - and a scan has the detail "
                     "a webcam cannot reach", "Faint")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        col.addWidget(hint)
        return holder

    # -- starting and stopping -------------------------------------------
    def open_camera(self) -> None:
        self.source_switch.select(0)
        self._source_changed(0)

    def open_scanner(self) -> None:
        self.source_switch.select(1)
        self._source_changed(1)

    def _source_changed(self, index: int) -> None:
        self.controls.setCurrentIndex(index)
        self.stage.setCurrentIndex(index)
        self.btn_shutter.setVisible(index == 0)
        self.btn_scan.setVisible(index == 1)
        if index == 0:
            self.detail.show()
            self.list_cameras()
        else:
            self.stop_worker()
            self.detail.hide()
            self.status.setText("Ready to scan")
            self.list_scanners()

    def list_cameras(self) -> None:
        if self.cmb_camera.count():
            if self.worker is None:
                self.restart()
            return
        self.status.setText("Looking for cameras...")
        cameras = cam.list_cameras()
        self.cmb_camera.blockSignals(True)
        self.cmb_camera.clear()
        for info in cameras:
            self.cmb_camera.addItem(info.name, info.index)
        self.cmb_camera.blockSignals(False)
        if not cameras:
            self.status.setText("No camera found")
            self.view.clear("No camera found - plug one in, or use Add images")
            return
        self.restart()

    def list_scanners(self) -> None:
        if self.cmb_scanner.count():
            return
        try:
            from ..pipeline import scanner
            devices = scanner.list_scanners()
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"No scanner available: {exc}")
            return
        self._scanners = devices
        for device in devices:
            extras = []
            if device.has_feeder:
                extras.append("feeder")
            if device.has_flatbed:
                extras.append("flatbed")
            self.cmb_scanner.addItem(
                device.name + (f"  ({', '.join(extras)})" if extras else ""))
        if not devices:
            self.status.setText("No scanner found")
            self.btn_scan.setEnabled(False)

    def restart(self) -> None:
        self.stop_worker()
        index = self.cmb_camera.currentData()
        if index is None:
            return
        _, width, height = RESOLUTIONS[self.cmb_res.currentIndex()]
        self.worker = CameraWorker(int(index), width, height, self)
        self.worker.auto = self.chk_auto.isChecked()
        self.worker.frame_ready.connect(self.on_frame)
        self.worker.captured.connect(self.on_capture)
        self.worker.size_chosen.connect(self.on_size_chosen)
        self.worker.zoom_mode.connect(self.on_zoom_mode)
        self.worker.zoom = self.zoom
        self.worker.centre = tuple(self.centre)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def stop_worker(self) -> None:
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.stop()
            if not worker.wait(2500):
                worker.quit()
                worker.wait(500)
            worker.deleteLater()

    def stop(self) -> None:
        self.stop_worker()
        self.view.clear()

    # -- camera signals ---------------------------------------------------
    def on_size_chosen(self, width: int, height: int) -> None:
        self.lbl_size.setText(f"{width} x {height}")

    def on_frame(self, frame: np.ndarray, check) -> None:
        self.view.update_frame(frame, check)
        colour = {"searching": theme.TEXT_DIM, "adjusting": theme.WARNING,
                  "holding": theme.ACCENT,
                  "ready": theme.SUCCESS}.get(check.state, theme.TEXT_DIM)
        text = check.message
        if not self.chk_auto.isChecked() and check.state in ("holding", "ready"):
            text = "Looks good - press Space"
        if check.pages_in_shot > 1 and check.state in ("holding", "ready"):
            text += "  -  two pages, saved separately"
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{colour};")

        # How fine the printing is, which is what decides whether the notes
        # can be read at all. Nothing later puts back detail the camera never
        # caught, so it is worth saying as it happens.
        verdict = cam.PageTracker.detail_verdict(check.interline)
        self.detail.setText(verdict)
        if not verdict:
            tone = theme.TEXT_FAINT
        elif check.readable:
            tone = theme.SUCCESS
        elif check.interline >= 6:
            tone = theme.WARNING
        else:
            tone = theme.DANGER
        self.detail.setStyleSheet(f"color:{tone};")

    def on_capture(self, frame: np.ndarray) -> None:
        path = cam.save_frame(frame, self._out_dir)
        self.captured_paths.append(path)
        thumb = ndarray_to_qpixmap(frame).scaled(
            72, 94, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.strip.addItem(QListWidgetItem(QIcon(thumb),
                                           f"{len(self.captured_paths)}"))
        self.strip.scrollToBottom()
        self.strip.show()
        self._refresh_count()
        self.view.flash()

    def on_failed(self, message: str) -> None:
        self.view.clear(message.splitlines()[0])
        self.status.setText(message.splitlines()[0])
        self.status.setStyleSheet(f"color:{theme.DANGER};")
        self.message.emit(message)

    def _auto_toggled(self, on: bool) -> None:
        if self.worker is not None:
            self.worker.auto = on
        if not on:
            self.status.setText("Press Space to take a page")
            self.status.setStyleSheet(f"color:{theme.TEXT_DIM};")

    # -- actions ----------------------------------------------------------
    def set_zoom(self, value: float, from_slider: bool = False) -> None:
        self.zoom = max(1.0, min(float(value), cam.MAX_ZOOM))
        if self.zoom <= 1.001:
            self.centre = [0.5, 0.5]
        if not from_slider:
            self.slider_zoom.blockSignals(True)
            self.slider_zoom.setValue(int(round(self.zoom * 10)))
            self.slider_zoom.blockSignals(False)
        self.lbl_zoom.setText(f"{self.zoom:.1f}x")
        if self.worker is not None:
            self.worker.zoom = self.zoom
            self.worker.centre = tuple(self.centre)
        self.view.set_window(self.zoom, tuple(self.centre),
                             self._zoom_mode == "crop")
        self._announce_zoom()

    def nudge_zoom(self, factor: float) -> None:
        self.set_zoom(self.zoom * factor)

    def pan(self, dx: float, dy: float) -> None:
        """Move the framed window, when it is a crop of the picture."""
        if self._zoom_mode != "crop" or self.zoom <= 1.001:
            return
        span = 1.0 - 1.0 / self.zoom
        self.centre[0] = min(max(self.centre[0] + dx, 0.5 - span / 2),
                             0.5 + span / 2)
        self.centre[1] = min(max(self.centre[1] + dy, 0.5 - span / 2),
                             0.5 + span / 2)
        if self.worker is not None:
            self.worker.centre = tuple(self.centre)
        self.view.set_window(self.zoom, tuple(self.centre), True)

    def on_zoom_mode(self, mode: str) -> None:
        self._zoom_mode = mode
        self._announce_zoom()

    def _announce_zoom(self) -> None:
        if self.zoom <= 1.001:
            self.lbl_zoom.setToolTip("The whole frame")
            return
        self.lbl_zoom.setToolTip(
            "The camera's own zoom - the page keeps its detail"
            if self._zoom_mode == "camera" else
            "A crop of the picture - fewer distractions, same detail. "
            "Drag the picture to move the frame.")

    def take_shot(self) -> None:
        if self.worker is not None:
            self.worker.shutter()

    def remove_selected(self) -> None:
        row = self.strip.currentRow()
        if row < 0:
            return
        self.strip.takeItem(row)
        path = self.captured_paths.pop(row)
        try:
            path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        for i in range(self.strip.count()):
            self.strip.item(i).setText(f"{i + 1}")
        self._refresh_count()

    def _refresh_count(self) -> None:
        count = len(self.captured_paths)
        self.lbl_count.setText(
            f"{count} page{'s' if count != 1 else ''} taken" if count else "")
        self.btn_done.setEnabled(bool(count))
        self.btn_remove.setEnabled(bool(count))
        self.strip.setVisible(bool(count))

    def _start_scan(self) -> None:
        if not getattr(self, "_scanners", None):
            self.status.setText("No scanner found")
            return
        index = max(0, self.cmb_scanner.currentIndex())
        self.scan_requested.emit({
            "device": self._scanners[index].device_id,
            "source": self.cmb_paper.currentText(),
            "dpi": int(self.spin_dpi.value()),
            "mode": self.cmb_colour.currentText(),
            "pages": int(self.spin_pages.value()),
        })

    def finish(self) -> None:
        paths = list(self.captured_paths)
        self.captured_paths.clear()
        self.strip.clear()
        self._refresh_count()
        self.stop()
        if paths:
            self.pages_ready.emit(paths)
        self.closed.emit()

    def cancel(self) -> None:
        if self.captured_paths:
            self.finish()
            return
        self.stop()
        self.closed.emit()

    def wheelEvent(self, event):  # noqa: D102, N802
        if self.source_switch.current == 0:
            steps = event.angleDelta().y() / 120.0
            if steps:
                self.nudge_zoom(1.15 ** steps)
                event.accept()
                return
        super().wheelEvent(event)

    def keyPressEvent(self, event):  # noqa: D102, N802
        if self.source_switch.current == 0:
            if event.key() == Qt.Key_Space:
                self.take_shot()
                event.accept()
                return
            if event.key() in (Qt.Key_Plus, Qt.Key_Equal):
                self.nudge_zoom(1.25)
                event.accept()
                return
            if event.key() == Qt.Key_Minus:
                self.nudge_zoom(1 / 1.25)
                event.accept()
                return
            step = 0.06
            moves = {Qt.Key_Left: (-step, 0.0), Qt.Key_Right: (step, 0.0),
                     Qt.Key_Up: (0.0, -step), Qt.Key_Down: (0.0, step)}
            if event.key() in moves:
                self.pan(*moves[event.key()])
                event.accept()
                return
        super().keyPressEvent(event)
