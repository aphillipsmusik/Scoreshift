"""Scrollable preview of the engraved score."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (QLabel, QScrollArea, QVBoxLayout, QWidget)

PAGE_BG = "#ffffff"


class _Page(QSvgWidget):
    def __init__(self, svg: str, width: int) -> None:
        super().__init__()
        self.load(QByteArray(svg.encode("utf-8")))
        self.setStyleSheet(f"background:{PAGE_BG}; border:1px solid #d0d3d8;")
        self.set_page_width(width)

    def set_page_width(self, width: int) -> None:
        size = self.renderer().defaultSize()
        if size.width() > 0:
            ratio = size.height() / size.width()
            self.setFixedSize(width, int(width * ratio))


class ScorePreview(QScrollArea):
    """Shows engraved pages, or a friendly placeholder."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setBackgroundRole(QPalette.Dark)
        self._page_width = 780
        self._svgs: list[str] = []

        self._holder = QWidget()
        self._holder.setStyleSheet("background:#2b2e33;")
        self._layout = QVBoxLayout(self._holder)
        self._layout.setContentsMargins(24, 24, 24, 24)
        self._layout.setSpacing(18)
        self._layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setWidget(self._holder)

        self._placeholder = QLabel(
            "Nothing engraved yet.\n\n"
            "Add or scan your pages, run Transcribe, then choose a key."
        )
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet("color:#aeb4bd; font-size:14px;")
        self._layout.addWidget(self._placeholder)

    def clear(self) -> None:
        self._svgs = []
        self._rebuild()

    def set_message(self, text: str) -> None:
        self._svgs = []
        self._placeholder.setText(text)
        self._rebuild()

    def set_svgs(self, svgs: list[str]) -> None:
        self._svgs = list(svgs)
        self._rebuild()

    def zoom(self, factor: float) -> None:
        self._page_width = int(max(280, min(2400, self._page_width * factor)))
        for i in range(self._layout.count()):
            w = self._layout.itemAt(i).widget()
            if isinstance(w, _Page):
                w.set_page_width(self._page_width)

    def _rebuild(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._placeholder:
                w.deleteLater()
        if not self._svgs:
            self._layout.addWidget(self._placeholder)
            self._placeholder.show()
            return
        self._placeholder.hide()
        for svg in self._svgs:
            try:
                self._layout.addWidget(_Page(svg, self._page_width))
            except Exception:  # noqa: BLE001
                continue
        self._layout.addStretch(1)
