"""Painting the light, shared by the tray icon and the floating window.

listening: on, amber.  speaking: blue.  thinking: amber, pulsing slowly.  idle: off.
unknown: grey ring with a hollow centre.
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap, QRadialGradient

AMBER = QColor("#ffb020")
BLUE = QColor("#3b8cff")
GREY = QColor("#8b8a84")
OFF = QColor("#1d1d1b")
RIM = QColor("#3a3a37")

LABELS = {"listening": "Listening", "speaking": "Speaking", "thinking": "Thinking", "idle": "Idle", "unknown": "Unknown"}
PULSE_SECONDS = 3.2


def pulse(t: float) -> float:
    """0.28 .. 1.0, one slow cycle every PULSE_SECONDS."""
    return 0.64 + 0.36 * math.sin(2 * math.pi * t / PULSE_SECONDS - math.pi / 2)


def paint_light(p: QPainter, rect: QRectF, state: str, phase: float = 1.0, glow: bool = True) -> None:
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = rect.center()
    r = min(rect.width(), rect.height()) / 2
    if state == "unknown":
        pen = QPen(GREY, max(2.0, r * 0.18))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        rr = r - pen.widthF() / 2
        p.drawEllipse(c, rr, rr)
        return
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(OFF if state == "idle" else QColor("#151514")))
    p.drawEllipse(c, r, r)
    p.setPen(QPen(RIM, max(1.5, r * 0.09)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(c, r - r * 0.045, r - r * 0.045)
    if state == "idle":
        return
    col = BLUE if state == "speaking" else AMBER
    a = phase if state == "thinking" else 1.0
    core = r * 0.72
    if glow:
        halo = QRadialGradient(c, r)
        g = QColor(col); g.setAlphaF(0.35 * a)
        halo.setColorAt(0.6, g)
        g2 = QColor(col); g2.setAlphaF(0.0)
        halo.setColorAt(1.0, g2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(c, r, r)
    grad = QRadialGradient(QPointF(c.x(), c.y() - core * 0.25), core)
    hi = QColor(col).lighter(150); hi.setAlphaF(a)
    mid = QColor(col); mid.setAlphaF(a)
    lo = QColor(col).darker(125); lo.setAlphaF(a)
    grad.setColorAt(0.0, hi); grad.setColorAt(0.55, mid); grad.setColorAt(1.0, lo)
    p.setBrush(QBrush(grad))
    p.drawEllipse(c, core, core)


def light_icon(state: str, phase: float = 1.0, size: int = 64) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint_light(p, QRectF(2, 2, size - 4, size - 4), state, phase, glow=False)
    p.end()
    return QIcon(pm)
