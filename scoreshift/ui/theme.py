"""The dark theme: colour tokens, spacing, and one stylesheet for the app.

Everything visual lives here so a colour is changed in one place rather than
sprinkled through the widgets.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette

# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

BG = "#0D0F13"            # window behind everything
SURFACE = "#15171C"       # panels, rails
ELEVATED = "#1C2027"      # cards, inputs, hovered rows
OVERLAY = "#272C35"       # pressed states, the slide-over
BORDER = "#2C313A"
BORDER_STRONG = "#3A414C"

TEXT = "#ECEFF4"
TEXT_DIM = "#9AA3B0"
TEXT_FAINT = "#6B7480"
ON_ACCENT = "#0E1116"

ACCENT = "#9B8CFF"
ACCENT_HOVER = "#AFA3FF"
ACCENT_PRESS = "#8878F2"
ACCENT_SOFT = "#262041"   # accent at low opacity over SURFACE

SUCCESS = "#4ADE80"
WARNING = "#FBBF24"
DANGER = "#F87171"

# Paper stays paper, even in a dark app - music has to read correctly.
# One colour per quality band, and for the per-page bars.
CHART = ("#9B8CFF", "#F5B544", "#35D0A5", "#4C9BF5", "#E8E04B")

PAPER = "#FFFFFF"
CANVAS = "#101216"

RADIUS = 14
RADIUS_SM = 9
RADIUS_LG = 22          # the big dashboard panels
PANEL = "#15171C"
SPACE = 8

FONT_FAMILY = '"Segoe UI Variable Display", "Segoe UI", Inter, system-ui, sans-serif'
MONO_FAMILY = '"Cascadia Mono", Consolas, "SF Mono", monospace'


def rgba(hex_colour: str, alpha: float) -> str:
    c = QColor(hex_colour)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.2f})"


# ---------------------------------------------------------------------------
# Palette (covers widgets the stylesheet doesn't reach, e.g. native dialogs)
# ---------------------------------------------------------------------------

def palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window, QColor(BG))
    p.setColor(QPalette.WindowText, QColor(TEXT))
    p.setColor(QPalette.Base, QColor(ELEVATED))
    p.setColor(QPalette.AlternateBase, QColor(SURFACE))
    p.setColor(QPalette.Text, QColor(TEXT))
    p.setColor(QPalette.PlaceholderText, QColor(TEXT_FAINT))
    p.setColor(QPalette.Button, QColor(ELEVATED))
    p.setColor(QPalette.ButtonText, QColor(TEXT))
    p.setColor(QPalette.BrightText, QColor(DANGER))
    p.setColor(QPalette.Highlight, QColor(ACCENT))
    p.setColor(QPalette.HighlightedText, QColor(ON_ACCENT))
    p.setColor(QPalette.ToolTipBase, QColor(OVERLAY))
    p.setColor(QPalette.ToolTipText, QColor(TEXT))
    p.setColor(QPalette.Link, QColor(ACCENT))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(TEXT_FAINT))
    return p


def base_font() -> QFont:
    font = QFont("Segoe UI Variable Display")
    if not font.exactMatch():
        font = QFont("Segoe UI")
    font.setPointSizeF(10.0)
    return font


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

def _tick_asset() -> str:
    """QSS can't draw a check mark, so paint one and point at the file."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

    from ..config import app_data_dir

    path = app_data_dir() / "ui" / "check.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        pix = QPixmap(34, 34)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(ON_ACCENT))
        pen.setWidthF(4.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline([QPointF(8, 17.5), QPointF(14.5, 24), QPointF(26, 11)])
        painter.end()
        pix.save(str(path))
    return str(path).replace("\\", "/")


def stylesheet() -> str:
    tick = _tick_asset()
    return f"""
* {{
    font-family: {FONT_FAMILY};
    outline: none;
}}

QWidget {{
    background: transparent;
    color: {TEXT};
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background: {BG};
}}

/* ---- Structure -------------------------------------------------------- */

#TopBar {{
    background: {SURFACE};
    border-bottom: 1px solid {BORDER};
}}

#PageRail {{
    background: {SURFACE};
    border-right: 1px solid {BORDER};
}}

#Canvas {{
    background: {CANVAS};
}}

#SlideOver {{
    background: {SURFACE};
    border-left: 1px solid {BORDER};
}}

#StatusStrip {{
    background: {SURFACE};
    border-top: 1px solid {BORDER};
}}

#Card {{
    background: {ELEVATED};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}

#Divider {{
    background: {BORDER};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

/* ---- Type ------------------------------------------------------------- */

#Title       {{ font-size: 15px; font-weight: 600; color: {TEXT}; }}
#SectionName {{ font-size: 11px; font-weight: 700; color: {TEXT_FAINT};
                letter-spacing: 1.1px; }}
#Hint        {{ font-size: 12px; color: {TEXT_DIM}; }}
#Faint       {{ font-size: 12px; color: {TEXT_FAINT}; }}
#Value       {{ font-size: 13px; font-weight: 600; color: {TEXT}; }}
#Good        {{ font-size: 12px; color: {SUCCESS}; }}
#Warn        {{ font-size: 12px; color: {WARNING}; }}
#Bad         {{ font-size: 12px; color: {DANGER}; }}
#Accent      {{ font-size: 13px; font-weight: 600; color: {ACCENT}; }}

/* ---- Buttons ---------------------------------------------------------- */

QPushButton {{
    background: {ELEVATED};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 500;
    min-height: 20px;
}}
QPushButton:hover  {{ background: {OVERLAY}; border-color: {BORDER_STRONG}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ background: {SURFACE}; color: {TEXT_FAINT};
                        border-color: {BORDER}; }}

QPushButton[variant="primary"] {{
    background: {ACCENT};
    color: {ON_ACCENT};
    border: none;
    font-weight: 600;
    padding: 9px 18px;
}}
QPushButton[variant="primary"]:hover   {{ background: {ACCENT_HOVER}; }}
QPushButton[variant="primary"]:pressed {{ background: {ACCENT_PRESS}; }}
QPushButton[variant="primary"]:disabled {{ background: {ELEVATED};
                                           color: {TEXT_FAINT}; }}

QPushButton[variant="ghost"] {{
    background: transparent;
    border: none;
    color: {TEXT_DIM};
    padding: 7px 10px;
}}
QPushButton[variant="ghost"]:hover  {{ background: {ELEVATED}; color: {TEXT}; }}
QPushButton[variant="ghost"]:pressed {{ background: {OVERLAY}; }}
QPushButton[variant="ghost"]:disabled {{ color: {TEXT_FAINT}; background: transparent; }}

QPushButton[variant="quiet"] {{
    background: transparent;
    border: 1px solid {BORDER};
    color: {TEXT_DIM};
    padding: 6px 12px;
    font-size: 12px;
}}
QPushButton[variant="quiet"]:hover {{ color: {TEXT}; border-color: {BORDER_STRONG}; }}

QPushButton[variant="link"] {{
    background: transparent; border: none; color: {ACCENT};
    padding: 2px 0; font-size: 12px; text-align: left;
}}
QPushButton[variant="link"]:hover {{ color: {ACCENT_HOVER}; }}

/* ---- Inputs ----------------------------------------------------------- */

QComboBox, QLineEdit, QSpinBox, QPlainTextEdit, QTextBrowser {{
    background: {ELEVATED};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 7px 10px;
    selection-background-color: {ACCENT};
    selection-color: {ON_ACCENT};
    min-height: 18px;
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ border-color: {BORDER_STRONG}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:read-only {{ background: {SURFACE}; color: {TEXT_DIM}; }}

QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM};
    width: 0; height: 0; margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    background: {OVERLAY};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS_SM}px;
    selection-background-color: {ACCENT};
    selection-color: {ON_ACCENT};
    padding: 4px;
    outline: none;
}}

QSpinBox::up-button, QSpinBox::down-button {{
    background: {OVERLAY}; border: none; width: 18px;
}}
QSpinBox::up-button {{ border-top-right-radius: {RADIUS_SM}px; }}
QSpinBox::down-button {{ border-bottom-right-radius: {RADIUS_SM}px; }}
QSpinBox::up-arrow {{
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid {TEXT_DIM}; width: 0; height: 0;
}}
QSpinBox::down-arrow {{
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM}; width: 0; height: 0;
}}

/* ---- Checkboxes ------------------------------------------------------- */

QCheckBox {{ spacing: 9px; color: {TEXT}; padding: 3px 0; }}
QCheckBox::indicator {{
    width: 17px; height: 17px;
    border: 1.5px solid {BORDER_STRONG};
    border-radius: 5px;
    background: {ELEVATED};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: url("{tick}");
}}
QCheckBox:disabled {{ color: {TEXT_FAINT}; }}

/* ---- Page rail -------------------------------------------------------- */

QListWidget {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget::item {{
    border-radius: {RADIUS_SM}px;
    padding: 4px;
    margin: 3px 0;
    color: {TEXT_DIM};
}}
QListWidget::item:hover {{ background: {ELEVATED}; color: {TEXT}; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT};
                              border: 1px solid {ACCENT}; }}

/* ---- Scrollbars ------------------------------------------------------- */

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {TEXT_FAINT}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {BORDER_STRONG}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- Progress --------------------------------------------------------- */

QProgressBar {{
    background: {ELEVATED};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

/* ---- Menus and dialogs ------------------------------------------------ */

QMenuBar {{ background: {SURFACE}; color: {TEXT_DIM}; padding: 2px 6px; }}
QMenuBar::item {{ padding: 6px 11px; border-radius: 6px; background: transparent; }}
QMenuBar::item:selected {{ background: {ELEVATED}; color: {TEXT}; }}

QMenu {{
    background: {OVERLAY};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS_SM}px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 22px 7px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT}; color: {ON_ACCENT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}

QToolTip {{
    background: {OVERLAY};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 6px 9px;
}}

QMessageBox {{ background: {SURFACE}; }}
QMessageBox QLabel {{ color: {TEXT}; }}

QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    margin-top: 14px;
    padding: 14px 12px 10px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
    color: {TEXT_DIM};
}}

QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}

QDockWidget {{ color: {TEXT_DIM}; titlebar-close-icon: none; }}
QPlainTextEdit#Log {{
    font-family: {MONO_FAMILY};
    font-size: 11px;
    color: {TEXT_DIM};
    background: {BG};
    border: none;
}}
"""
