# PyInstaller spec: one folder, no console window. Works on Linux and Windows.
#   pyinstaller --noconfirm jarvis-panel.spec     (run from app/, with the app venv active)
# Output: dist/jarvis-panel/  (the executable is jarvis-panel, or jarvis-panel.exe on Windows)
import sys
from pathlib import Path

here = Path(SPECPATH)
icon = here / "assets" / ("jarvis-panel.ico" if sys.platform == "win32" else "jarvis-panel.png")

a = Analysis(
    [str(here / "launch.py")],
    pathex=[str(here)],
    datas=[(str(here / "assets" / "jarvis-panel.png"), "assets")],
    hiddenimports=[],
    excludes=["tkinter", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
              "PySide6.QtDesigner", "PySide6.QtQuick3D", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtHelp"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="jarvis-panel", console=False, icon=str(icon),
          bootloader_ignore_signals=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="jarvis-panel")
