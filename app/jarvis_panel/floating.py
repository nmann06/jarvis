"""A small frameless always-on-top light you can drag anywhere. Double-click opens the panel."""

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QMenu, QWidget

from .lightpaint import LABELS, paint_light, pulse

SIZE = 72


class FloatingLight(QWidget):
    open_panel = Signal()
    hide_requested = Signal()
    moved = Signal(int, int)      # where it was dropped

    def __init__(self, reduced_motion: bool = False):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(SIZE, SIZE)
        self.setWindowTitle("Jarvis light")
        self.state = "unknown"
        self._phase = 1.0
        self._t = 0.0
        self._drag: QPoint | None = None
        self._dragged = False
        self._reduced = reduced_motion
        self._timer = QTimer(self, interval=60)
        self._timer.timeout.connect(self._tick)
        self.setToolTip("Jarvis: Unknown. Drag to move, double-click to open the panel.")

    def set_state(self, state: str) -> None:
        self.state = state
        self._phase = 0.6 if state == "thinking" else 1.0
        if state == "thinking" and not self._reduced:
            self._t = 0.0
            self._timer.start()
        else:
            self._timer.stop()
        self.setToolTip(f"Jarvis: {LABELS.get(state, 'Unknown')}. Drag to move, double-click to open the panel.")
        self.update()

    def _tick(self) -> None:
        self._t += 0.06
        self._phase = pulse(self._t)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        paint_light(p, QRectF(6, 6, SIZE - 12, SIZE - 12), self.state, self._phase)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragged = False

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
            self._dragged = True

    def mouseReleaseEvent(self, e):
        if self._drag is not None and self._dragged:
            self.moved.emit(self.x(), self.y())
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.open_panel.emit()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction("Open panel", self.open_panel.emit)
        m.addAction("Hide light", self.hide_requested.emit)
        m.exec(e.globalPos())
