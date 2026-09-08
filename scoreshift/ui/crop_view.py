"""Interactive crop editor: drag the four page corners."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QImage, QPainterPath, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem,
                               QGraphicsPathItem, QGraphicsPixmapItem,
                               QGraphicsScene, QGraphicsView)

HANDLE_R = 11.0
ACCENT = QColor(64, 158, 255)
SHADE = QColor(10, 12, 16, 130)


def ndarray_to_qpixmap(img: np.ndarray) -> QPixmap:
    """BGR or grayscale numpy image -> QPixmap."""
    if img is None:
        return QPixmap()
    arr = np.ascontiguousarray(img)
    if arr.ndim == 2:
        h, w = arr.shape
        qimg = QImage(arr.data, w, h, w, QImage.Format_Grayscale8)
    else:
        h, w, ch = arr.shape
        if ch == 4:
            arr = arr[:, :, :3]
            ch = 3
        rgb = np.ascontiguousarray(arr[:, :, ::-1])
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        return QPixmap.fromImage(qimg.copy())
    return QPixmap.fromImage(qimg.copy())


class _Handle(QGraphicsEllipseItem):
    def __init__(self, index: int, owner: "CropView") -> None:
        super().__init__(-HANDLE_R, -HANDLE_R, HANDLE_R * 2, HANDLE_R * 2)
        self.index = index
        self.owner = owner
        self.setBrush(QBrush(QColor(255, 255, 255)))
        self.setPen(QPen(ACCENT, 3))
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self.setZValue(20)
        self.setCursor(Qt.SizeAllCursor)

    def itemChange(self, change, value):  # noqa: D102, N802
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            pt: QPointF = value
            rect = self.owner.image_rect()
            pt.setX(min(max(pt.x(), rect.left()), rect.right()))
            pt.setY(min(max(pt.y(), rect.top()), rect.bottom()))
            self.owner.handle_moved(self.index, pt)
            return pt
        return super().itemChange(change, value)


class CropView(QGraphicsView):
    """Shows a page with a draggable four-corner crop outline."""

    quad_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(self.renderHints())
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setBackgroundBrush(QBrush(QColor(28, 30, 34)))
        self.setMinimumSize(320, 320)

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._shade = QGraphicsPathItem()
        self._shade.setBrush(QBrush(SHADE))
        self._shade.setPen(QPen(Qt.NoPen))
        self._shade.setZValue(5)
        self._outline = QGraphicsPathItem()
        self._outline.setPen(QPen(ACCENT, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        self._outline.setZValue(10)
        self._handles: list[_Handle] = []
        self._points: list[QPointF] = []
        self._editable = True

    # -- content ---------------------------------------------------------
    def set_image(self, img: np.ndarray, quad: np.ndarray | None = None,
                  editable: bool = True) -> None:
        self._scene.clear()
        self._handles.clear()
        self._points.clear()
        self._editable = editable

        pix = ndarray_to_qpixmap(img)
        self._pixmap_item = self._scene.addPixmap(pix)
        self._pixmap_item.setZValue(0)
        self._scene.setSceneRect(QRectF(pix.rect()))

        self._shade = self._scene.addPath(QPainterPath(), QPen(Qt.NoPen), QBrush(SHADE))
        self._shade.setZValue(5)
        self._outline = self._scene.addPath(
            QPainterPath(), QPen(ACCENT, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        )
        self._outline.setZValue(10)

        if editable:
            h, w = img.shape[:2]
            if quad is None:
                m = 0.03
                quad = np.array([[w * m, h * m], [w * (1 - m), h * m],
                                 [w * (1 - m), h * (1 - m)], [w * m, h * (1 - m)]],
                                dtype=np.float32)
            for i, (x, y) in enumerate(np.asarray(quad, dtype=float).reshape(4, 2)):
                pt = QPointF(float(x), float(y))
                self._points.append(pt)
                handle = _Handle(i, self)
                handle.setPos(pt)
                self._scene.addItem(handle)
                self._handles.append(handle)
            self._redraw()

        self.fit()

    def image_rect(self) -> QRectF:
        return self._scene.sceneRect()

    def quad(self) -> np.ndarray | None:
        if not self._points:
            return None
        return np.array([[p.x(), p.y()] for p in self._points], dtype=np.float32)

    def set_quad(self, quad: np.ndarray | None) -> None:
        if quad is None or not self._handles:
            return
        for i, (x, y) in enumerate(np.asarray(quad, dtype=float).reshape(4, 2)):
            self._points[i] = QPointF(float(x), float(y))
            self._handles[i].setPos(self._points[i])
        self._redraw()

    def reset_to_full(self) -> None:
        r = self.image_rect()
        self.set_quad(np.array(
            [[r.left(), r.top()], [r.right(), r.top()],
             [r.right(), r.bottom()], [r.left(), r.bottom()]], dtype=np.float32))

    # -- painting --------------------------------------------------------
    def handle_moved(self, index: int, pt: QPointF) -> None:
        if index < len(self._points):
            self._points[index] = QPointF(pt)
            self._redraw()
            self.quad_changed.emit()

    def _redraw(self) -> None:
        if not self._points:
            return
        poly = QPolygonF(self._points)
        path = QPainterPath()
        path.addPolygon(poly)
        path.closeSubpath()
        self._outline.setPath(path)

        outer = QPainterPath()
        outer.addRect(self.image_rect())
        self._shade.setPath(outer.subtracted(path))

    # -- view ------------------------------------------------------------
    def fit(self) -> None:
        if self._scene.sceneRect().isEmpty():
            return
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event):  # noqa: D102, N802
        super().resizeEvent(event)
        self.fit()

    def wheelEvent(self, event):  # noqa: D102, N802
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)
