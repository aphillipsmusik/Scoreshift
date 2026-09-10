"""Line icons drawn with QPainter - no image files, no icon font to ship."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from . import theme

_CACHE: dict[tuple[str, int, str], QIcon] = {}
GRID = 24.0


def _pen(painter: QPainter, colour: str, width: float = 1.7) -> None:
    pen = QPen(QColor(colour))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)


def _draw(name: str, painter: QPainter, colour: str) -> None:
    _pen(painter, colour)
    p = painter

    if name == "add":
        p.drawLine(QPointF(12, 5), QPointF(12, 19))
        p.drawLine(QPointF(5, 12), QPointF(19, 12))

    elif name == "camera":
        p.drawRoundedRect(QRectF(3, 7, 18, 13), 3, 3)
        p.drawLine(QPointF(8.5, 7), QPointF(10, 4.5))
        p.drawLine(QPointF(15.5, 7), QPointF(14, 4.5))
        p.drawLine(QPointF(10, 4.5), QPointF(14, 4.5))
        p.drawEllipse(QPointF(12, 13.5), 3.6, 3.6)

    elif name == "scanner":
        p.drawRoundedRect(QRectF(3, 9, 18, 9), 2.5, 2.5)
        p.drawLine(QPointF(6, 9), QPointF(6, 5))
        p.drawLine(QPointF(18, 9), QPointF(18, 5))
        p.drawLine(QPointF(6, 5), QPointF(18, 5))
        _pen(painter, theme.ACCENT, 1.7)
        p.drawLine(QPointF(6.5, 13.5), QPointF(17.5, 13.5))

    elif name == "transcribe":                      # note + sparkle
        p.drawEllipse(QPointF(8.5, 17), 3.2, 2.4)
        p.drawLine(QPointF(11.7, 17), QPointF(11.7, 6))
        p.drawLine(QPointF(11.7, 6), QPointF(17, 8))
        p.drawLine(QPointF(19, 13), QPointF(19, 17))
        p.drawLine(QPointF(17, 15), QPointF(21, 15))

    elif name == "key":                             # tuning fork / pitch
        p.drawLine(QPointF(9, 4), QPointF(9, 11))
        p.drawLine(QPointF(15, 4), QPointF(15, 11))
        path = QPainterPath(QPointF(9, 11))
        path.cubicTo(QPointF(9, 15), QPointF(15, 15), QPointF(15, 11))
        p.drawPath(path)
        p.drawLine(QPointF(12, 14.5), QPointF(12, 20))

    elif name == "save":
        p.drawLine(QPointF(12, 4), QPointF(12, 15))
        p.drawPolyline([QPointF(7.5, 10.5), QPointF(12, 15), QPointF(16.5, 10.5)])
        p.drawPolyline([QPointF(4.5, 17), QPointF(4.5, 20), QPointF(19.5, 20),
                        QPointF(19.5, 17)])

    elif name == "settings":
        p.drawEllipse(QPointF(12, 12), 3.1, 3.1)
        for angle, length in ((0, 0), (60, 0), (120, 0), (180, 0), (240, 0), (300, 0)):
            p.save()
            p.translate(12, 12)
            p.rotate(angle)
            p.drawLine(QPointF(0, -7.6), QPointF(0, -5.2))
            p.restore()
        p.drawEllipse(QPointF(12, 12), 7.0, 7.0)

    elif name == "chevron-right":
        p.drawPolyline([QPointF(10, 6.5), QPointF(15.5, 12), QPointF(10, 17.5)])

    elif name == "chevron-left":
        p.drawPolyline([QPointF(14, 6.5), QPointF(8.5, 12), QPointF(14, 17.5)])

    elif name == "chevron-down":
        p.drawPolyline([QPointF(6.5, 10), QPointF(12, 15.5), QPointF(17.5, 10)])

    elif name == "close":
        p.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
        p.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))

    elif name == "check":
        p.drawPolyline([QPointF(5.5, 12.5), QPointF(10, 17), QPointF(18.5, 7)])

    elif name == "trash":
        p.drawLine(QPointF(4.5, 7), QPointF(19.5, 7))
        p.drawPolyline([QPointF(6.5, 7), QPointF(7.5, 20), QPointF(16.5, 20),
                        QPointF(17.5, 7)])
        p.drawPolyline([QPointF(9.5, 7), QPointF(9.5, 4.5), QPointF(14.5, 4.5),
                        QPointF(14.5, 7)])

    elif name == "crop":
        p.drawLine(QPointF(6.5, 2.5), QPointF(6.5, 17.5))
        p.drawLine(QPointF(2.5, 6.5), QPointF(17.5, 6.5))
        p.drawLine(QPointF(17.5, 6.5), QPointF(17.5, 21.5))
        p.drawLine(QPointF(6.5, 17.5), QPointF(21.5, 17.5))

    elif name == "rotate":
        path = QPainterPath()
        path.arcMoveTo(QRectF(5, 5, 14, 14), 60)
        path.arcTo(QRectF(5, 5, 14, 14), 60, 280)
        p.drawPath(path)
        p.drawPolyline([QPointF(13.5, 3.5), QPointF(16.6, 6.4), QPointF(13.2, 8.6)])

    elif name == "page":
        p.drawPolyline([QPointF(6, 3.5), QPointF(14, 3.5), QPointF(18.5, 8),
                        QPointF(18.5, 20.5), QPointF(6, 20.5), QPointF(6, 3.5)])
        p.drawPolyline([QPointF(14, 3.5), QPointF(14, 8), QPointF(18.5, 8)])

    elif name == "score":
        for y in (8, 11, 14, 17):
            p.drawLine(QPointF(4, y), QPointF(20, y))
        p.setBrush(QColor(colour))
        p.drawEllipse(QPointF(9, 15.5), 2.1, 1.6)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(11.1, 15.5), QPointF(11.1, 6.5))

    elif name == "refresh":
        path = QPainterPath()
        path.arcMoveTo(QRectF(5, 5, 14, 14), 120)
        path.arcTo(QRectF(5, 5, 14, 14), 120, 250)
        p.drawPath(path)
        p.drawPolyline([QPointF(7.4, 4.2), QPointF(7.0, 8.4), QPointF(11.2, 8.0)])

    elif name == "zoom-in":
        p.drawEllipse(QPointF(11, 11), 6.2, 6.2)
        p.drawLine(QPointF(15.6, 15.6), QPointF(20, 20))
        p.drawLine(QPointF(8.4, 11), QPointF(13.6, 11))
        p.drawLine(QPointF(11, 8.4), QPointF(11, 13.6))

    elif name == "zoom-out":
        p.drawEllipse(QPointF(11, 11), 6.2, 6.2)
        p.drawLine(QPointF(15.6, 15.6), QPointF(20, 20))
        p.drawLine(QPointF(8.4, 11), QPointF(13.6, 11))

    elif name == "wand":
        p.drawLine(QPointF(4.5, 19.5), QPointF(15, 9))
        p.drawLine(QPointF(15, 9), QPointF(19.5, 4.5))
        p.drawLine(QPointF(12.5, 6.5), QPointF(12.5, 3))
        p.drawLine(QPointF(10.8, 4.8), QPointF(14.2, 4.8))
        p.drawLine(QPointF(19, 11), QPointF(19, 8))
        p.drawLine(QPointF(17.5, 9.5), QPointF(20.5, 9.5))

    elif name == "folder":
        p.drawPolyline([QPointF(3.5, 19), QPointF(3.5, 6), QPointF(9.5, 6),
                        QPointF(11.5, 8.5), QPointF(20.5, 8.5), QPointF(20.5, 19),
                        QPointF(3.5, 19)])

    elif name == "info":
        p.drawEllipse(QPointF(12, 12), 8.2, 8.2)
        p.drawLine(QPointF(12, 11), QPointF(12, 16.5))
        p.drawPoint(QPointF(12, 7.9))
        p.drawLine(QPointF(12, 7.6), QPointF(12, 8.2))

    elif name == "up":
        p.drawLine(QPointF(12, 19), QPointF(12, 5.5))
        p.drawPolyline([QPointF(6.5, 11), QPointF(12, 5.5), QPointF(17.5, 11)])

    elif name == "down":
        p.drawLine(QPointF(12, 5), QPointF(12, 18.5))
        p.drawPolyline([QPointF(6.5, 13), QPointF(12, 18.5), QPointF(17.5, 13)])


def icon(name: str, size: int = 18, colour: str = theme.TEXT_DIM) -> QIcon:
    key = (name, size, colour)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.scale(size / GRID, size / GRID)
    _draw(name, painter, colour)
    painter.end()
    result = QIcon(pixmap)
    _CACHE[key] = result
    return result


def pixmap(name: str, size: int = 18, colour: str = theme.TEXT_DIM) -> QPixmap:
    return icon(name, size, colour).pixmap(QSize(size, size))
