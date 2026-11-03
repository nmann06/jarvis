"""Draw the app icons (an amber light) into assets/. Run once; the PNG and ICO are committed.

    QT_QPA_PLATFORM=offscreen .venv/bin/python make_icons.py
"""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter

from jarvis_panel.lightpaint import paint_light

app = QGuiApplication([])
out = Path(__file__).parent / "assets"
out.mkdir(exist_ok=True)
for size in (16, 24, 32, 48, 64, 128, 256):
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    paint_light(p, QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92), "listening", glow=size >= 64)
    p.end()
    if size in (256,):
        img.save(str(out / "jarvis-panel.png"))
        img.save(str(out / "jarvis-panel.ico"))   # Qt's ICO writer holds one image; 256 px scales down fine
print("wrote", sorted(x.name for x in out.iterdir()))
