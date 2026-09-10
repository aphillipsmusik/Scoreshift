"""Small reusable pieces the window is assembled from."""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QSize, Qt,
                            Signal)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import icons, theme


def button(text: str = "", icon_name: str = "", variant: str = "",
           tooltip: str = "", icon_colour: str = "") -> QPushButton:
    """One button factory so every button in the app matches."""
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    if icon_name:
        colour = icon_colour or (theme.ON_ACCENT if variant == "primary"
                                 else theme.TEXT_DIM)
        b.setIcon(icons.icon(icon_name, 17, colour))
        b.setIconSize(QSize(17, 17))
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def icon_button(icon_name: str, tooltip: str = "", size: int = 34) -> QPushButton:
    b = button(icon_name=icon_name, variant="ghost", tooltip=tooltip)
    b.setFixedSize(size, size)
    b.setIconSize(QSize(18, 18))
    return b


def label(text: str = "", kind: str = "") -> QLabel:
    lab = QLabel(text)
    if kind:
        lab.setObjectName(kind)
    lab.setWordWrap(True)
    return lab


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.NoFrame)
    line.setFixedHeight(1)
    return line


def spacer(width: int = 0, height: int = 0) -> QWidget:
    w = QWidget()
    w.setFixedSize(QSize(width, height) if (width or height) else QSize(0, 0))
    if not width:
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return w


class Card(QFrame):
    """A padded surface that groups a few related controls."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(10)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)


class SectionHeading(QWidget):
    """Small uppercase caption, optionally with something on the right."""

    def __init__(self, text: str, trailing: QWidget | None = None, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(8)
        self.caption = label(text.upper(), "SectionName")
        self.caption.setWordWrap(False)
        row.addWidget(self.caption)
        row.addStretch(1)
        if trailing is not None:
            row.addWidget(trailing)


class Disclosure(QWidget):
    """A 'More' toggle that hides the options most people never touch."""

    toggled = Signal(bool)

    def __init__(self, text: str = "More options", parent=None) -> None:
        super().__init__(parent)
        self._open = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        self.header = button(text, "chevron-right", "link")
        self.header.setIconSize(QSize(14, 14))
        self.header.clicked.connect(self.toggle)
        outer.addWidget(self.header, 0, Qt.AlignLeft)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 2)
        self.content_layout.setSpacing(9)
        self.content.setVisible(False)
        outer.addWidget(self.content)

    def add(self, widget: QWidget) -> QWidget:
        self.content_layout.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self.content_layout.addLayout(layout)

    def toggle(self) -> None:
        self.set_open(not self._open)

    def set_open(self, is_open: bool) -> None:
        self._open = is_open
        self.content.setVisible(is_open)
        self.header.setIcon(icons.icon(
            "chevron-down" if is_open else "chevron-right", 14, theme.ACCENT))
        self.toggled.emit(is_open)

    @property
    def is_open(self) -> bool:
        return self._open


class Segmented(QWidget):
    """Two or three mutually exclusive views - Page / Score."""

    changed = Signal(int)

    def __init__(self, options: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.setStyleSheet(f"""
            #Segmented {{ background: {theme.SURFACE};
                          border: 1px solid {theme.BORDER};
                          border-radius: {theme.RADIUS_SM + 1}px; }}
        """)
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(3)
        self.buttons: list[QPushButton] = []
        for index, (text, icon_name) in enumerate(options):
            b = button(text, icon_name, "ghost")
            b.setCheckable(True)
            b.setMinimumWidth(96)
            b.clicked.connect(lambda _=False, i=index: self.select(i))
            row.addWidget(b)
            self.buttons.append(b)
        self.select(0, notify=False)

    def select(self, index: int, notify: bool = True) -> None:
        for i, b in enumerate(self.buttons):
            on = i == index
            b.setChecked(on)
            b.setStyleSheet(
                f"background: {theme.ELEVATED}; color: {theme.TEXT};"
                f"border-radius: {theme.RADIUS_SM}px; font-weight: 600;"
                if on else "")
            b.setIcon(icons.icon(
                ["page", "score"][i] if i < 2 else "page", 17,
                theme.TEXT if on else theme.TEXT_FAINT))
        if notify:
            self.changed.emit(index)

    @property
    def current(self) -> int:
        return next((i for i, b in enumerate(self.buttons) if b.isChecked()), 0)


class SlideOver(QFrame):
    """The right-hand panel that slides in when you need the controls."""

    closed = Signal()

    def __init__(self, title: str, width: int = 408, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SlideOver")
        self._width = width
        self._open = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(52)
        head = QHBoxLayout(header)
        head.setContentsMargins(18, 0, 10, 0)
        self.title = label(title, "Title")
        head.addWidget(self.title)
        head.addStretch(1)
        self.close_button = icon_button("close", "Hide this panel  (Ctrl+B)")
        self.close_button.clicked.connect(self.hide_panel)
        head.addWidget(self.close_button)
        outer.addWidget(header)
        outer.addWidget(divider())

        self.body = QVBoxLayout()
        self.body.setContentsMargins(16, 16, 16, 16)
        self.body.setSpacing(14)
        holder = QWidget()
        holder.setLayout(self.body)
        outer.addWidget(holder, 1)

        self._anim = QPropertyAnimation(self, b"maximumWidth", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.setMaximumWidth(width)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    @property
    def is_open(self) -> bool:
        return self._open

    def show_panel(self) -> None:
        self._animate(self._width)
        self._open = True

    def hide_panel(self) -> None:
        self._animate(0)
        self._open = False
        self.closed.emit()

    def toggle(self) -> None:
        self.hide_panel() if self._open else self.show_panel()

    def _animate(self, target: int) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.maximumWidth())
        self._anim.setEndValue(target)
        self._anim.start()


class StatusDot(QLabel):
    """A coloured dot for 'ready' / 'needs attention'."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(8, 8)
        self.set_state("idle")

    def set_state(self, state: str) -> None:
        colour = {"ok": theme.SUCCESS, "warn": theme.WARNING,
                  "busy": theme.ACCENT, "bad": theme.DANGER,
                  "idle": theme.TEXT_FAINT}.get(state, theme.TEXT_FAINT)
        self.setStyleSheet(f"background: {colour}; border-radius: 4px;")
