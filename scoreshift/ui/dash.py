"""The dashboard pieces: nav rail, headline figures, page cards, panels.

The window is laid out like a dashboard rather than a toolbar-and-form: what
you have (pages, their quality, the key, what will be saved) is on screen as
figures and cards, and the controls sit next to the thing they change.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from . import icons, theme


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------

class Panel(QFrame):
    """A rounded section of the dashboard, with a title and a body."""

    def __init__(self, title: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setStyleSheet(f"""
            #Panel {{ background: {theme.PANEL};
                      border: 1px solid {theme.BORDER};
                      border-radius: {theme.RADIUS_LG}px; }}
        """)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(18, 16, 18, 18)
        self._col.setSpacing(12)

        self.head = QHBoxLayout()
        self.head.setSpacing(10)
        self.title = QLabel(title)
        self.title.setObjectName("PanelTitle")
        self.title.setStyleSheet(
            f"color:{theme.TEXT}; font-size:17px; font-weight:600;")
        self.head.addWidget(self.title)
        self.head.addStretch(1)
        self._col.addLayout(self.head)
        if not title:
            self.title.hide()

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._col.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout) -> None:
        self._col.addLayout(layout)

    def add_action(self, widget: QWidget) -> QWidget:
        self.head.addWidget(widget)
        return widget

    def set_title(self, text: str) -> None:
        self.title.setText(text)
        self.title.setVisible(bool(text))


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

class NavRail(QFrame):
    """The left-hand rail: where you are, and where else you can go."""

    changed = Signal(int)

    def __init__(self, items: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("NavRail")
        self.setFixedWidth(208)
        self.setStyleSheet(f"#NavRail {{ background: {theme.BG}; }}")

        col = QVBoxLayout(self)
        col.setContentsMargins(14, 18, 14, 16)
        col.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        mark = QLabel()
        mark.setPixmap(icons.pixmap("score", 22, theme.ACCENT))
        brand.addWidget(mark)
        name = QLabel("ScoreShift")
        name.setStyleSheet(
            f"color:{theme.TEXT}; font-size:15px; font-weight:700;"
            "letter-spacing:0.4px;")
        brand.addWidget(name)
        brand.addStretch(1)
        col.addLayout(brand)
        col.addSpacing(18)

        self.buttons: list[QPushButton] = []
        self._icons: list[str] = []
        for index, (text, icon_name) in enumerate(items):
            b = QPushButton(f"    {text}")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(44)
            b.setIconSize(QSize(32, 32))
            b.clicked.connect(lambda _=False, i=index: self.select(i))
            col.addWidget(b)
            self.buttons.append(b)
            self._icons.append(icon_name)
        col.addStretch(1)

        self.footer = QVBoxLayout()
        self.footer.setSpacing(6)
        col.addLayout(self.footer)
        self.select(0, notify=False)

    def add_footer(self, widget: QWidget) -> QWidget:
        self.footer.addWidget(widget)
        return widget

    @staticmethod
    def _badge(icon_name: str, on: bool) -> QPixmap:
        """The icon in a round badge, lit when the item is the current one."""
        size = 32
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(theme.ACCENT if on else theme.ELEVATED)))
        painter.drawEllipse(0, 0, size, size)
        glyph = icons.pixmap(icon_name, 17,
                             theme.ON_ACCENT if on else theme.TEXT_DIM)
        painter.drawPixmap(int((size - glyph.width() / glyph.devicePixelRatio()) / 2),
                           int((size - glyph.height() / glyph.devicePixelRatio()) / 2),
                           glyph)
        painter.end()
        return pix

    def select(self, index: int, notify: bool = True) -> None:
        for i, b in enumerate(self.buttons):
            on = i == index
            b.setChecked(on)
            b.setIcon(self._badge(self._icons[i], on))
            b.setStyleSheet(f"""
                QPushButton {{
                    text-align: left; padding: 0 10px; font-size: 14px;
                    font-weight: {600 if on else 500};
                    color: {theme.TEXT if on else theme.TEXT_DIM};
                    background: {theme.ACCENT_SOFT if on else 'transparent'};
                    border: none; border-radius: {theme.RADIUS}px;
                }}
                QPushButton:hover {{
                    background: {theme.ACCENT_SOFT if on else theme.ELEVATED};
                    color: {theme.TEXT};
                }}
                QPushButton:disabled {{ color: {theme.TEXT_FAINT}; }}
            """)
        if notify:
            self.changed.emit(index)

    @property
    def current(self) -> int:
        return next((i for i, b in enumerate(self.buttons) if b.isChecked()), 0)


class CircleButton(QPushButton):
    """A round icon button, for the actions in the title row."""

    def __init__(self, icon_name: str, tooltip: str = "", size: int = 38,
                 accent: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self._icon_name = icon_name
        self._accent = accent
        self.setIconSize(QSize(17, 17))
        self.refresh()

    def refresh(self) -> None:
        colour = theme.ON_ACCENT if self._accent else theme.TEXT_DIM
        self.setIcon(icons.icon(self._icon_name, 17, colour))
        background = theme.ACCENT if self._accent else theme.ELEVATED
        hover = theme.ACCENT_HOVER if self._accent else theme.OVERLAY
        self.setStyleSheet(f"""
            QPushButton {{ background: {background}; border: none;
                           border-radius: {self.width() // 2}px; }}
            QPushButton:hover {{ background: {hover}; }}
        """)

    def set_accent(self, on: bool) -> None:
        self._accent = on
        self.refresh()


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

class StatTile(QWidget):
    """A label and a big number - the headline figures along the top."""

    def __init__(self, caption: str, value: str = "-", parent=None) -> None:
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.caption = QLabel(caption)
        self.caption.setStyleSheet(f"color:{theme.TEXT_FAINT}; font-size:12px;")
        self.value = QLabel(value)
        self.value.setStyleSheet(
            f"color:{theme.TEXT}; font-size:25px; font-weight:600;")
        self.note = QLabel("")
        self.note.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:12px;")
        col.addWidget(self.caption)
        col.addWidget(self.value)
        col.addWidget(self.note)

    def set(self, value: str, note: str = "", tone: str = "") -> None:
        self.value.setText(value)
        self.note.setText(note)
        self.note.setVisible(bool(note))
        self.note.setStyleSheet(
            f"color:{tone or theme.TEXT_DIM}; font-size:12px;")


@dataclass
class Segment:
    label: str
    value: float
    colour: str
    detail: str = ""


class QualityBar(QWidget):
    """A proportional bar with a legend - how the pages divide up by quality."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._segments: list[Segment] = []
        self.setMinimumHeight(54)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_segments(self, segments: list[Segment]) -> None:
        self._segments = [s for s in segments if s.value > 0]
        self.update()

    def paintEvent(self, event):  # noqa: D102, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        width = self.width()
        total = sum(s.value for s in self._segments) or 1.0

        bar_h = 5
        gap = 6
        x = 0.0
        if not self._segments:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(theme.BORDER)))
            painter.drawRoundedRect(QRectF(0, 0, width, bar_h), 2.5, 2.5)
        for segment in self._segments:
            share = segment.value / total
            w = max(10.0, share * (width - gap * (len(self._segments) - 1)))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(segment.colour)))
            painter.drawRoundedRect(QRectF(x, 0, w, bar_h), 2.5, 2.5)
            x += w + gap

        # Legend: a dot, the name, and the count underneath.
        x = 0.0
        font = QFont(painter.font())
        font.setPointSizeF(8.5)
        painter.setFont(font)
        for segment in self._segments:
            share = segment.value / total
            w = max(10.0, share * (width - gap * (len(self._segments) - 1)))
            painter.setBrush(QBrush(QColor(segment.colour)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QRectF(x + 1, bar_h + 11, 6, 6))
            painter.setPen(QPen(QColor(theme.TEXT_DIM)))
            painter.drawText(QRectF(x + 13, bar_h + 6, max(60.0, w - 13), 16),
                             Qt.AlignLeft | Qt.AlignVCenter, segment.label)
            painter.setPen(QPen(QColor(theme.TEXT)))
            big = QFont(font)
            big.setPointSizeF(10.5)
            big.setBold(True)
            painter.setFont(big)
            painter.drawText(QRectF(x + 1, bar_h + 24, max(60.0, w), 18),
                             Qt.AlignLeft | Qt.AlignVCenter, segment.detail)
            painter.setFont(font)
            x += w + gap
        painter.end()


class MiniBars(QWidget):
    """A small bar chart - one bar per page."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self._colours: list[str] = []
        self.setMinimumHeight(60)

    def set_values(self, values: list[float], colours: list[str]) -> None:
        self._values = list(values)
        self._colours = list(colours)
        self.update()

    def paintEvent(self, event):  # noqa: D102, N802
        if not self._values:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        top = max(self._values) or 1.0
        count = len(self._values)
        gap = 4
        bar = min(16.0, max(3.0, (self.width() - gap * (count - 1)) / count))
        for i, value in enumerate(self._values):
            h = max(3.0, (value / top) * (self.height() - 4))
            x = i * (bar + gap)
            colour = QColor(self._colours[i] if i < len(self._colours)
                            else theme.ACCENT)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(colour))
            painter.drawRoundedRect(
                QRectF(x, self.height() - h, bar, h), 2.5, 2.5)
        painter.end()


class StatCard(QFrame):
    """A boxed figure with a small chart under it."""

    def __init__(self, caption: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setStyleSheet(f"""
            #StatCard {{ background: {theme.ELEVATED};
                         border: 1px solid {theme.BORDER};
                         border-radius: {theme.RADIUS}px; }}
        """)
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(4)
        self.caption = QLabel(caption)
        self.caption.setStyleSheet(f"color:{theme.TEXT_FAINT}; font-size:11px;")
        self.value = QLabel("-")
        self.value.setStyleSheet(
            f"color:{theme.TEXT}; font-size:20px; font-weight:600;")
        self.note = QLabel("")
        self.note.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:11px;")
        self.chart = MiniBars()
        col.addWidget(self.caption)
        col.addWidget(self.value)
        col.addWidget(self.note)
        col.addWidget(self.chart, 1)

    def set(self, value: str, note: str = "", tone: str = "") -> None:
        self.value.setText(value)
        self.note.setText(note)
        self.note.setStyleSheet(
            f"color:{tone or theme.TEXT_DIM}; font-size:11px;")


# ---------------------------------------------------------------------------
# Page cards
# ---------------------------------------------------------------------------

class PageCard(QFrame):
    """One page, as a card: how it looks, how good it is, where it came from."""

    clicked = Signal(int)

    WIDTH = 170
    HEIGHT = 168

    def __init__(self, index: int, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.selected = False
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)

        col = QVBoxLayout(self)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(4)

        self.headline = QLabel("-")
        self.headline.setStyleSheet("font-size:18px; font-weight:600;")
        col.addWidget(self.headline)

        self.delta = QLabel("")
        self.delta.setStyleSheet("font-size:11px;")
        col.addWidget(self.delta)
        col.addStretch(1)

        facts = QHBoxLayout()
        facts.setSpacing(8)
        self.fact_a = self._fact(facts, "Size")
        self.fact_b = self._fact(facts, "Tilt")
        col.addLayout(facts)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.thumb = QLabel()
        self.thumb.setFixedSize(34, 34)
        self.thumb.setStyleSheet(
            f"background:{theme.SURFACE}; border-radius:17px;")
        self.thumb.setAlignment(Qt.AlignCenter)
        foot.addWidget(self.thumb)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.name = QLabel("Page")
        self.name.setStyleSheet("font-size:12px; font-weight:600;")
        self.sub = QLabel("")
        self.sub.setStyleSheet("font-size:10px;")
        names.addWidget(self.name)
        names.addWidget(self.sub)
        foot.addLayout(names)
        foot.addStretch(1)
        col.addLayout(foot)
        self._restyle()

    def _fact(self, row: QHBoxLayout, caption: str) -> tuple[QLabel, QLabel]:
        box = QVBoxLayout()
        box.setSpacing(0)
        cap = QLabel(caption)
        cap.setStyleSheet("font-size:10px;")
        val = QLabel("-")
        val.setStyleSheet("font-size:11px; font-weight:600;")
        box.addWidget(cap)
        box.addWidget(val)
        row.addLayout(box)
        return cap, val

    def set_page(self, title: str, subtitle: str, detail: str, delta: str,
                 delta_tone: str, size: str, tilt: str,
                 thumb: QPixmap | None = None) -> None:
        self.name.setText(title)
        self.sub.setText(subtitle)
        self.headline.setText(detail)
        self.delta.setText(delta)
        self._delta_tone = delta_tone
        self.fact_a[1].setText(size)
        self.fact_b[1].setText(tilt)
        if thumb is not None and not thumb.isNull():
            rounded = QPixmap(34, 34)
            rounded.fill(Qt.transparent)
            painter = QPainter(rounded)
            painter.setRenderHint(QPainter.Antialiasing, True)
            path = QPainterPath()
            path.addEllipse(0, 0, 34, 34)
            painter.setClipPath(path)
            scaled = thumb.scaled(34, 34, Qt.KeepAspectRatioByExpanding,
                                  Qt.SmoothTransformation)
            painter.drawPixmap(int((34 - scaled.width()) / 2),
                               int((34 - scaled.height()) / 2), scaled)
            painter.end()
            self.thumb.setPixmap(rounded)
        self._restyle()

    def set_selected(self, on: bool) -> None:
        self.selected = on
        self._restyle()

    def _restyle(self) -> None:
        tone = getattr(self, "_delta_tone", theme.TEXT_DIM)
        if self.selected:
            self.setStyleSheet(f"""
                PageCard {{ background: {theme.ACCENT};
                            border: 1px solid {theme.ACCENT};
                            border-radius: {theme.RADIUS}px; }}
            """)
            strong, dim = theme.ON_ACCENT, "#2A2550"
        else:
            self.setStyleSheet(f"""
                PageCard {{ background: {theme.ELEVATED};
                            border: 1px solid {theme.BORDER};
                            border-radius: {theme.RADIUS}px; }}
                PageCard:hover {{ border: 1px solid {theme.BORDER_STRONG}; }}
            """)
            strong, dim = theme.TEXT, theme.TEXT_FAINT
        self.headline.setStyleSheet(
            f"color:{strong}; font-size:18px; font-weight:600;")
        self.delta.setStyleSheet(
            f"color:{dim if self.selected else tone}; font-size:11px;")
        self.name.setStyleSheet(f"color:{strong}; font-size:12px; font-weight:600;")
        self.sub.setStyleSheet(f"color:{dim}; font-size:10px;")
        for cap, val in (self.fact_a, self.fact_b):
            cap.setStyleSheet(f"color:{dim}; font-size:10px;")
            val.setStyleSheet(f"color:{strong}; font-size:11px; font-weight:600;")

    def mousePressEvent(self, event):  # noqa: D102, N802
        self.clicked.emit(self.index)
        super().mousePressEvent(event)


class CardRow(QScrollArea):
    """The row of page cards, scrolling sideways when there are many."""

    selected = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(PageCard.HEIGHT + 12)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("background: transparent;")

        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        self._row = QHBoxLayout(holder)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(10)
        self._row.addStretch(1)
        self.setWidget(holder)
        self.cards: list[PageCard] = []

    def clear(self) -> None:
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards.clear()

    def add_card(self) -> PageCard:
        card = PageCard(len(self.cards))
        card.clicked.connect(self.selected.emit)
        self._row.insertWidget(len(self.cards), card)
        self.cards.append(card)
        return card

    def set_selected(self, index: int) -> None:
        for i, card in enumerate(self.cards):
            card.set_selected(i == index)


# ---------------------------------------------------------------------------
# Rows of facts
# ---------------------------------------------------------------------------

class FactRow(QFrame):
    """A pill with a name on the left and a value on the right."""

    def __init__(self, name: str, value: str = "-", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("FactRow")
        self.setStyleSheet(f"""
            #FactRow {{ background: {theme.ELEVATED};
                        border: 1px solid {theme.BORDER};
                        border-radius: {theme.RADIUS_SM + 4}px; }}
        """)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(10)
        self.name = QLabel(name)
        self.name.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:12px;")
        self.value = QLabel(value)
        self.value.setStyleSheet(
            f"color:{theme.TEXT}; font-size:12px; font-weight:600;")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.name)
        row.addStretch(1)
        row.addWidget(self.value)

    def set(self, value: str, tone: str = "") -> None:
        self.value.setText(value)
        self.value.setStyleSheet(
            f"color:{tone or theme.TEXT}; font-size:12px; font-weight:600;")
