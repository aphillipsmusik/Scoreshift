"""Live camera capture: point at the page, it shoots when the page is steady."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QKeySequence, QPainter, QPen,
                           QPixmap, QPolygonF, QShortcut, QIcon)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..config import work_dir
from ..pipeline import camera as cam
from .crop_view import ndarray_to_qpixmap

RESOLUTIONS = [
    ("Best the camera offers", 0, 0),
    ("1280 x 720", 1280, 720),
    ("1920 x 1080", 1920, 1080),
    ("2560 x 1440", 2560, 1440),
    ("3840 x 2160", 3840, 2160),
    ("640 x 480", 640, 480),
]

COLOURS = {
    "searching": QColor(150, 156, 166),
    "adjusting": QColor(245, 158, 11),
    "holding": QColor(59, 178, 226),
    "ready": QColor(34, 197, 94),
}


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class CameraWorker(QThread):
    frame_ready = Signal(object, object)      # ndarray, FrameCheck
    captured = Signal(object)                 # ndarray (full resolution)
    size_chosen = Signal(int, int)            # what the camera actually gave
    failed = Signal(str)

    def __init__(self, index: int, width: int, height: int, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.width = width
        self.height = height
        self.auto = True
        self._stop = False
        self._shutter = False
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
        except cam.CameraError as exc:
            self.failed.emit(str(exc))
            return
        try:
            while not self._stop:
                frame = stream.read()
                if frame is None:
                    time.sleep(0.03)
                    continue
                check = self.tracker.check(frame)
                take = self._shutter or (self.auto and self.tracker.should_capture(check))
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
    """Shows the camera feed with the detected page outlined."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(720, 480)
        self._pix: QPixmap | None = None
        self._quad: np.ndarray | None = None
        self._state = "searching"
        self._flash = 0.0

    def update_frame(self, frame: np.ndarray, check) -> None:
        self._pix = ndarray_to_qpixmap(frame)
        self._quad = check.quad
        self._state = check.state
        self.update()

    def flash(self) -> None:
        self._flash = time.time()
        self.update()

    def paintEvent(self, event):  # noqa: D102, N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(18, 20, 24))
        if self._pix is None or self._pix.isNull():
            painter.setPen(QColor(160, 165, 172))
            painter.drawText(self.rect(), Qt.AlignCenter, "Starting the camera...")
            return

        scaled = self._pix.scaled(self.size(), Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
        ox = (self.width() - scaled.width()) / 2
        oy = (self.height() - scaled.height()) / 2
        painter.drawPixmap(int(ox), int(oy), scaled)

        if self._quad is not None:
            sx = scaled.width() / self._pix.width()
            sy = scaled.height() / self._pix.height()
            pts = [QPointF(ox + x * sx, oy + y * sy) for x, y in self._quad]
            colour = COLOURS.get(self._state, COLOURS["searching"])
            painter.setRenderHint(QPainter.Antialiasing, True)
            fill = QColor(colour)
            fill.setAlpha(38 if self._state in ("holding", "ready") else 18)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(colour, 3))
            painter.drawPolygon(QPolygonF(pts))
            painter.setBrush(QBrush(colour))
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            for p in pts:
                painter.drawEllipse(p, 7, 7)

        if self._flash and time.time() - self._flash < 0.18:
            painter.fillRect(self.rect(), QColor(255, 255, 255, 150))
            self.update()
        painter.end()


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class CameraDialog(QDialog):
    """Capture one or more pages from a webcam or document camera."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Capture pages with a camera")
        self.resize(1080, 820)
        self.captured_paths: list[Path] = []
        self._frames: list[np.ndarray] = []
        self.worker: CameraWorker | None = None
        self._out_dir = work_dir() / "camera"

        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.cmb_camera = QComboBox()
        self.cmb_res = QComboBox()
        for label, _, _ in RESOLUTIONS:
            self.cmb_res.addItem(label)
        self.cmb_res.setCurrentIndex(0)
        self.chk_auto = QCheckBox("Shoot automatically when the page is steady")
        self.chk_auto.setChecked(True)
        self.chk_auto.toggled.connect(self._auto_toggled)
        top.addWidget(QLabel("Camera:"))
        top.addWidget(self.cmb_camera, 1)
        top.addWidget(QLabel("Quality:"))
        top.addWidget(self.cmb_res)
        self.lbl_size = QLabel("")
        self.lbl_size.setStyleSheet("color:#6b7280;")
        top.addWidget(self.lbl_size)
        top.addWidget(self.chk_auto)
        root.addLayout(top)

        self.view = CameraView()
        root.addWidget(self.view, 1)

        self.status = QLabel("Looking for a camera...")
        self.status.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(12)
        font.setBold(True)
        self.status.setFont(font)
        self.status.setMinimumHeight(34)
        root.addWidget(self.status)

        self.lbl_detail = QLabel("")
        self.lbl_detail.setAlignment(Qt.AlignCenter)
        self.lbl_detail.setStyleSheet("color:#6b7280;")
        root.addWidget(self.lbl_detail)

        self.strip = QListWidget()
        self.strip.setFlow(QListWidget.LeftToRight)
        self.strip.setIconSize(QSize(84, 108))
        self.strip.setFixedHeight(140)
        self.strip.setSelectionMode(QListWidget.SingleSelection)
        root.addWidget(self.strip)

        buttons = QHBoxLayout()
        self.btn_shutter = QPushButton("Capture now  (Space)")
        self.btn_shutter.clicked.connect(self.take_shot)
        self.btn_remove = QPushButton("Remove selected")
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_done = QPushButton("Use these pages")
        self.btn_done.setDefault(True)
        self.btn_done.clicked.connect(self.accept)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_shutter)
        buttons.addWidget(self.btn_remove)
        buttons.addStretch(1)
        self.lbl_count = QLabel("0 pages captured")
        buttons.addWidget(self.lbl_count)
        buttons.addWidget(self.btn_cancel)
        buttons.addWidget(self.btn_done)
        root.addLayout(buttons)

        QShortcut(QKeySequence(Qt.Key_Space), self, self.take_shot)

        self.cmb_camera.currentIndexChanged.connect(lambda _: self.restart())
        self.cmb_res.currentIndexChanged.connect(lambda _: self.restart())

        self._populate_cameras()

    # -- camera lifecycle -------------------------------------------------
    def _populate_cameras(self) -> None:
        self.status.setText("Looking for cameras...")
        cameras = cam.list_cameras()
        self.cmb_camera.blockSignals(True)
        self.cmb_camera.clear()
        for c in cameras:
            self.cmb_camera.addItem(f"{c.name}", c.index)
        self.cmb_camera.blockSignals(False)
        if not cameras:
            self.status.setText("No camera found")
            self.status.setStyleSheet("color:#b91c1c;")
            QMessageBox.warning(
                self, "No camera",
                "No camera was found.\n\nPlug in a webcam, or check "
                "Settings > Privacy & security > Camera in Windows and allow "
                "desktop apps to use it.")
            return
        self.restart()

    def restart(self) -> None:
        self.stop_worker()
        index = self.cmb_camera.currentData()
        if index is None:
            return
        _, w, h = RESOLUTIONS[self.cmb_res.currentIndex()]
        self.worker = CameraWorker(int(index), w, h, self)
        self.worker.auto = self.chk_auto.isChecked()
        self.worker.frame_ready.connect(self.on_frame)
        self.worker.captured.connect(self.on_capture)
        self.worker.size_chosen.connect(self.on_size_chosen)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def stop_worker(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(2500)
            self.worker = None

    def _auto_toggled(self, on: bool) -> None:
        if self.worker is not None:
            self.worker.auto = on
        if not on:
            self.status.setText("Manual mode - press Space or Capture now")
            self.status.setStyleSheet("color:#374151;")

    # -- signals ----------------------------------------------------------
    def on_size_chosen(self, width: int, height: int) -> None:
        self.lbl_size.setText(f"{width} x {height}")

    def on_frame(self, frame: np.ndarray, check) -> None:
        self.view.update_frame(frame, check)
        colour = {"searching": "#6b7280", "adjusting": "#b45309",
                  "holding": "#0369a1", "ready": "#15803d"}.get(check.state, "#6b7280")
        text = check.message
        if not self.chk_auto.isChecked() and check.state in ("holding", "ready"):
            text = "Looks good - press Space to capture"
        if check.pages_in_shot > 1 and check.state in ("holding", "ready"):
            text += "  -  two pages, they will be saved separately"
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{colour};")

        # How fine the printing is, which is what decides whether the notes
        # can be read at all. Nothing later can put back detail the camera
        # never captured, so it is worth saying out loud, as it happens.
        verdict = cam.PageTracker.detail_verdict(check.interline)
        self.lbl_detail.setText(verdict)
        if not verdict:
            self.lbl_detail.setStyleSheet("color:#6b7280;")
        elif check.readable:
            self.lbl_detail.setStyleSheet("color:#15803d;")
        elif check.interline >= 6:
            self.lbl_detail.setStyleSheet("color:#b45309;")
        else:
            self.lbl_detail.setStyleSheet("color:#b91c1c;")

    def on_capture(self, frame: np.ndarray) -> None:
        path = cam.save_frame(frame, self._out_dir)
        self.captured_paths.append(path)
        self._frames.append(frame)
        thumb = ndarray_to_qpixmap(frame).scaled(
            84, 108, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        item = QListWidgetItem(QIcon(thumb), f"Page {len(self.captured_paths)}")
        self.strip.addItem(item)
        self.strip.scrollToBottom()
        self.lbl_count.setText(f"{len(self.captured_paths)} page"
                               f"{'s' if len(self.captured_paths) != 1 else ''} captured")
        self.view.flash()

    def on_failed(self, message: str) -> None:
        self.status.setText("Camera error")
        self.status.setStyleSheet("color:#b91c1c;")
        QMessageBox.warning(self, "Camera", message)

    # -- actions ----------------------------------------------------------
    def take_shot(self) -> None:
        if self.worker is not None:
            self.worker.shutter()

    def remove_selected(self) -> None:
        row = self.strip.currentRow()
        if row < 0:
            return
        self.strip.takeItem(row)
        path = self.captured_paths.pop(row)
        self._frames.pop(row)
        try:
            path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        for i in range(self.strip.count()):
            self.strip.item(i).setText(f"Page {i + 1}")
        self.lbl_count.setText(f"{len(self.captured_paths)} pages captured")

    # -- teardown ---------------------------------------------------------
    def accept(self) -> None:  # noqa: D102
        if not self.captured_paths:
            QMessageBox.information(self, "Nothing captured",
                                    "Capture at least one page first.")
            return
        self.stop_worker()
        super().accept()

    def reject(self) -> None:  # noqa: D102
        self.stop_worker()
        for p in self.captured_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        self.captured_paths.clear()
        super().reject()

    def closeEvent(self, event):  # noqa: D102, N802
        self.stop_worker()
        super().closeEvent(event)
