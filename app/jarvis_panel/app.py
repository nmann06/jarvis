"""The desktop shell: window with the web UI, tray icon, floating light and connection dialog."""

import os
import subprocess
import sys
from urllib.parse import quote

from PySide6.QtCore import QLockFile, QPoint, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QMessageBox, QSystemTrayIcon

from . import __version__, config
from .client import PanelClient
from .dialog import ConnectionDialog
from .floating import FloatingLight
from .lightpaint import LABELS, light_icon, pulse


def reduced_motion() -> bool:
    """Whether the OS asks for less animation (best effort; the web UI follows the browser's own setting)."""
    if os.environ.get("JARVIS_PANEL_REDUCED_MOTION"):
        return os.environ["JARVIS_PANEL_REDUCED_MOTION"] == "1"
    try:
        if os.name == "nt":
            import ctypes
            on = ctypes.c_int(1)
            ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # SPI_GETCLIENTAREAANIMATION
            return not on.value
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"],
                             capture_output=True, text=True, timeout=1).stdout.strip()
        return out == "false"
    except Exception:
        return False


class PanelPage(QWebEnginePage):
    """Keeps navigation inside the panel; anything else opens in the normal browser."""

    def __init__(self, profile, origin: str, parent=None):
        super().__init__(profile, parent)
        self.origin = origin

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if is_main_frame and not url.toString().startswith(self.origin) and url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
            return False
        return True

    def createWindow(self, _type):
        # target=_blank links: open them in the browser, never in a new web view.
        helper = QWebEnginePage(self)
        helper.urlChanged.connect(lambda u: (QDesktopServices.openUrl(u), helper.deleteLater()))
        return helper


class MainWindow(QMainWindow):
    def __init__(self, can_hide: bool):
        super().__init__()
        self.setWindowTitle("Jarvis Panel")
        self.resize(1180, 820)
        self.can_hide = can_hide
        self.view = QWebEngineView(self)
        self.setCentralWidget(self.view)
        self.on_hidden = None
        self.quitting = False

    def closeEvent(self, e):
        if self.can_hide and not self.quitting:
            e.ignore()
            self.hide()           # closing the window hides it to the tray; Quit is in the tray menu
            if self.on_hidden:
                self.on_hidden()
        else:
            e.accept()


class Controller:
    def __init__(self, app: QApplication):
        self.app = app
        self.settings = config.Settings.load()
        self.conn = config.resolve()
        self.have_tray = QSystemTrayIcon.isSystemTrayAvailable()
        self.window = MainWindow(can_hide=self.have_tray)
        self.window.on_hidden = self.settings_save
        # Off the record: no cookies, cache or audio left on disk by the web view.
        self.profile = QWebEngineProfile(self.app)
        self.profile.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, True)
        self.page = None
        self.view_failed = False
        self.loaded_once = False
        self.dialog: ConnectionDialog | None = None
        self.quitting = False
        self.client = PanelClient(self.conn)
        self.light_state = "unknown"
        self.phase = 1.0
        self._t = 0.0
        self.reduced = reduced_motion()

        self.float = FloatingLight(self.reduced)
        self.float.open_panel.connect(self.show_window)
        self.float.hide_requested.connect(lambda: self.set_float(False))
        self.float.moved.connect(self._float_moved)
        self.tray = None
        self._build_tray()
        self._place_float()
        if self.settings.float_visible:
            self.float.show()

        self.pulse_timer = QTimer(self.app, interval=250)
        self.pulse_timer.timeout.connect(self._pulse)
        self.client.light.connect(self.on_light)
        self.client.connected.connect(self.on_connected)
        self.client.problem.connect(self.on_problem)
        self.client.auth_failed.connect(self.on_auth_failed)
        self.on_light("unknown")

    # -- start
    def start(self) -> None:
        if not self.conn.token:
            self.show_dialog("")
        else:
            self.client.start()

    # -- window and page
    def _login_url(self) -> QUrl:
        return QUrl(f"{self.conn.base}/login?token={quote(self.conn.token, safe='')}")

    def load_view(self) -> None:
        self.page = PanelPage(self.profile, self.conn.base + "/", self.window)
        self.window.view.setPage(self.page)
        self.page.loadFinished.connect(self._load_finished)
        self.view_failed = False
        self.window.view.load(self._login_url())

    def _load_finished(self, ok: bool) -> None:
        self.view_failed = not ok

    def show_window(self) -> None:
        if self.dialog and self.dialog.isVisible():
            self.dialog.raise_(); self.dialog.activateWindow()
            return
        if not self.loaded_once:
            return
        w = self.window
        if self.settings.geometry and not w.isVisible():
            from PySide6.QtCore import QByteArray
            w.restoreGeometry(QByteArray.fromBase64(self.settings.geometry.encode()))
        w.show(); w.raise_(); w.activateWindow()

    def toggle_window(self) -> None:
        if self.window.isVisible() and not self.window.isMinimized():
            self.window.hide()
            self.settings_save()
        else:
            self.window.showNormal()
            self.show_window()

    # -- connection events
    def on_connected(self, ok: bool) -> None:
        if not ok:
            return
        if self.dialog:
            self.dialog.accept()
            self.dialog = None
        if not self.loaded_once:
            self.loaded_once = True
            self.load_view()
            self.show_window()
        elif self.view_failed:
            self.load_view()

    def on_problem(self, msg: str) -> None:
        if not self.loaded_once:
            self.show_dialog(msg)
        elif self.dialog:
            self.dialog.show_error(msg)

    def on_auth_failed(self, msg: str) -> None:
        self.show_dialog(msg)

    def show_dialog(self, error: str) -> None:
        if self.dialog:
            self.dialog.show_error(error)
            return
        local = self.conn.source == "local"
        d = ConnectionDialog(self.conn, error, local=local)
        d.on_submit = self._dialog_submit
        d.rejected.connect(self._dialog_rejected)
        self.dialog = d
        d.show()

    def _dialog_rejected(self) -> None:
        if self.dialog is not None:     # the owner pressed Quit; accept() clears self.dialog first
            self.dialog = None
            self.quit()

    def _dialog_submit(self, conn: config.Connection) -> None:
        probe = PanelClient(conn)
        probe.checked.connect(lambda ok, msg: self._dialog_checked(conn, ok, msg, probe))
        probe.check(conn)

    def _dialog_checked(self, conn, ok: bool, msg: str, probe: PanelClient) -> None:
        probe.deleteLater()
        if not self.dialog:
            return
        if not ok:
            self.dialog.show_error(msg)
            return
        if conn.source != "local":
            config.save_token(conn.token)
            self.settings.host, self.settings.port = conn.host, conn.port
            self.settings_save()
        self.conn = conn
        self.client.stop()
        self.client.set_connection(conn)
        self.client.start()

    # -- light
    def on_light(self, state: str) -> None:
        self.light_state = state
        self.phase = 0.6 if state == "thinking" else 1.0
        self._t = 0.0
        self.float.set_state(state)
        if state == "thinking" and not self.reduced:
            self.pulse_timer.start()
        else:
            self.pulse_timer.stop()
        self._update_tray()

    def _pulse(self) -> None:
        self._t += 0.25
        self.phase = pulse(self._t)
        self._update_tray()

    # -- tray and floating light
    def _build_tray(self) -> None:
        if not self.have_tray:
            return
        self.tray = QSystemTrayIcon(light_icon("unknown"), self.app)
        menu = QMenu()
        act_open = QAction("Open Jarvis Panel", menu)
        act_open.triggered.connect(lambda: (self.window.showNormal(), self.show_window()))
        self.act_float = QAction("Floating light", menu, checkable=True, checked=self.settings.float_visible)
        self.act_float.toggled.connect(self.set_float)
        act_quit = QAction("Quit", menu)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_open); menu.addAction(self.act_float); menu.addSeparator(); menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self._menu = menu
        self.tray.activated.connect(lambda r: self.toggle_window() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def _update_tray(self) -> None:
        if not self.tray:
            return
        self.tray.setIcon(light_icon(self.light_state, self.phase))
        word = LABELS.get(self.light_state, "Unknown")
        self.tray.setToolTip(f"Jarvis: {word}")

    def set_float(self, on: bool) -> None:
        self.settings.float_visible = on
        if self.tray and self.act_float.isChecked() != on:
            self.act_float.blockSignals(True); self.act_float.setChecked(on); self.act_float.blockSignals(False)
        self.float.setVisible(on)
        self.settings_save()

    def _place_float(self) -> None:
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        pos = self.settings.float_pos
        if len(pos) == 2 and any(g.contains(QPoint(pos[0] + 10, pos[1] + 10)) for g in screens):
            self.float.move(pos[0], pos[1])
        elif screens:
            g = screens[0]
            self.float.move(g.right() - self.float.width() - 24, g.top() + 24)

    def _float_moved(self, x: int, y: int) -> None:
        self.settings.float_pos = [x, y]
        self.settings_save()

    # -- end
    def settings_save(self) -> None:
        try:
            if self.window.isVisible():
                self.settings.geometry = bytes(self.window.saveGeometry().toBase64()).decode()
            self.settings.save()
        except OSError:
            pass

    def quit(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        self.window.quitting = True
        self.settings_save()
        self.client.stop()
        if self.tray:
            self.tray.hide()
        self.float.close()
        self.window.close()
        self.app.quit()

    def cleanup(self) -> None:
        """Delete the web view, its page and the profile in that order, or Qt warns on exit."""
        from shiboken6 import delete
        for obj in (self.window.view, self.page, self.profile):
            if obj is not None:
                try:
                    delete(obj)
                except RuntimeError:
                    pass


def _prefer_x11_if_possible() -> None:
    """A floating always-on-top light needs X11 (or XWayland): Wayland won't let a window stay on top or
    remember its place. Only forced when the X11 plugin's one extra library is present."""
    if not sys.platform.startswith("linux") or os.environ.get("QT_QPA_PLATFORM"):
        return
    if os.environ.get("DISPLAY") and os.environ.get("WAYLAND_DISPLAY"):
        from ctypes.util import find_library
        if find_library("xcb-cursor"):
            os.environ["QT_QPA_PLATFORM"] = "xcb"


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv:
        print(f"Jarvis Panel {__version__}")
        return 0
    _prefer_x11_if_possible()
    app = QApplication(argv)
    app.setApplicationName("Jarvis Panel")
    app.setQuitOnLastWindowClosed(False)
    config.config_dir().mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(config.config_dir() / "panel.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(200):
        if QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.information(None, "Jarvis Panel", "Jarvis Panel is already running. Look for its icon in the system tray.")
        print("Jarvis Panel is already running.", file=sys.stderr)
        return 0
    ctl = Controller(app)
    app.aboutToQuit.connect(ctl.settings_save)
    app.aboutToQuit.connect(ctl.cleanup)
    QTimer.singleShot(0, ctl.start)
    rc = app.exec()
    lock.unlock()
    return rc
