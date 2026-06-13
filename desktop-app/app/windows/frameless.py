from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QMainWindow, QWidget


class FramelessDraggableWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._drag_offset: QPoint | None = None
        self._shadow_effect: QGraphicsDropShadowEffect | None = None
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def apply_window_chrome(
        self,
        card: QWidget,
        *,
        blur_radius: int = 28,
        y_offset: int = 10,
        alpha: int = 48,
    ) -> None:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(blur_radius)
        shadow.setOffset(0, y_offset)
        shadow.setColor(QColor(15, 23, 42, alpha))
        card.setGraphicsEffect(shadow)
        self._shadow_effect = shadow

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._should_start_drag(event):
            if self._try_start_system_move():
                event.accept()
                return
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def _should_start_drag(self, event: QMouseEvent) -> bool:
        widget = self.childAt(event.position().toPoint())
        while widget is not None:
            if widget.property("dragHandle") is True:
                return True
            if widget.property("noWindowDrag") is True:
                return False
            widget = widget.parentWidget() if isinstance(widget.parentWidget(), QWidget) else None
        return True

    def _try_start_system_move(self) -> bool:
        handle = self.windowHandle()
        if handle is None:
            return False
        start_system_move = getattr(handle, "startSystemMove", None)
        if not callable(start_system_move):
            return False
        try:
            return bool(start_system_move())
        except Exception:
            return False
