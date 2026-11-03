"""Drives the real desktop shell (offscreen) against the mock server.

    QT_QPA_PLATFORM=offscreen app/.venv/bin/python app/tests/shell_check.py [outdir]

Scenarios (run in one go, each in a fresh child process so Qt starts clean):
  local   token file found -> connects, window shows the panel, light follows states, survives an outage
  paste   no token anywhere -> dialog; wrong token -> clear error; right token -> connects and stores it 0600
  refused nothing listening -> dialog says so; Retry works once the server is up
Never reads the real data/panel-token: JARVIS_PANEL_* variables point at temp files.
"""

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
MOCK_PY = "/home/nate/jarvis/.venv/bin/python"
TOKEN = "throwaway-shell-token-0123456789"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "/tmp/panel-shell-shots")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def mock_call(port, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    return json.load(urllib.request.urlopen(req))


def stop(proc):
    proc.terminate()
    try:
        proc.wait(3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(3)


def start_mock(port):
    p = subprocess.Popen([MOCK_PY, str(HERE / "mock_server.py"), "--port", str(port), "--token", TOKEN],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return p
        except OSError:
            time.sleep(0.1)
    raise SystemExit("mock server didn't start")


# ---------------------------------------------------------------- child: the scenarios
def child(scenario):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    sys.path.insert(0, str(APP))
    from jarvis_panel import config
    from jarvis_panel.app import Controller

    port = int(os.environ["JARVIS_PANEL_PORT"])
    app = QApplication(["shell-check"])
    app.setQuitOnLastWindowClosed(False)
    OUT.mkdir(parents=True, exist_ok=True)
    results = []

    def check(name, ok, extra=""):
        results.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} {name} {'' if ok else extra}", flush=True)

    def wait(cond, ms=8000):
        loop = QEventLoop()
        t0 = time.time()
        timer = QTimer(interval=50)

        def poll():
            if cond() or (time.time() - t0) * 1000 > ms:
                loop.quit()

        timer.timeout.connect(poll)
        timer.start()
        loop.exec()
        timer.stop()
        return bool(cond())

    def pause(ms):
        wait(lambda: False, ms)

    ctl = Controller(app)
    ctl.start()

    if scenario == "local":
        check("connection source is the token file", ctl.conn.source == "local")
        check("connects and loads the web UI", wait(lambda: ctl.loaded_once and ctl.window.isVisible()))
        wait(lambda: ctl.window.view.url().path() == "/", 6000)
        pause(1500)
        check("web view landed on / after /login", ctl.window.view.url().path() == "/", ctl.window.view.url().toString().split("token")[0])
        title = []
        ctl.window.view.page().runJavaScript("document.querySelector('h1') && document.querySelector('h1').textContent", lambda v: title.append(v))
        wait(lambda: bool(title), 4000)
        check("page rendered Home", title == ["Home"], str(title))
        ctl.window.grab().save(str(OUT / "shell-window.png"))
        mock_call(port, "/__mock/state", {"state": "listening"})
        check("light follows listening", wait(lambda: ctl.client.shown_light == "listening" and ctl.float.state == "listening"))
        ctl.float.grab().save(str(OUT / "float-listening.png"))
        mock_call(port, "/__mock/state", {"state": "speaking"})
        check("light follows speaking", wait(lambda: ctl.float.state == "speaking"))
        mock_call(port, "/__mock/state", {"state": "thinking"})
        check("thinking starts the pulse timer", wait(lambda: ctl.float.state == "thinking") and ctl.float._timer.isActive())
        ctl.float.grab().save(str(OUT / "float-thinking.png"))
        mock_call(port, "/__mock/state", {"state": "idle"})
        check("idle is off", wait(lambda: ctl.float.state == "idle" and not ctl.float._timer.isActive()))
        ctl.float.grab().save(str(OUT / "float-idle.png"))
        mock_call(port, "/__mock/scenario", {"name": "down"})
        check("outage -> unknown (grey), not idle", wait(lambda: ctl.float.state == "unknown", 6000), ctl.float.state)
        ctl.float.grab().save(str(OUT / "float-unknown.png"))
        mock_call(port, "/__mock/scenario", {"name": "normal"})
        check("reconnects by itself", wait(lambda: ctl.client.shown_light == "idle", 15000), ctl.client.shown_light)
        mock_call(port, "/__mock/scenario", {"name": "hooks_off"})
        pause(300)
        ctl.client.stop(); ctl.client.start()
        check("hooks not live -> unknown", wait(lambda: ctl.client.connected and ctl.float.state == "unknown" and not ctl.client._hooks, 6000))
        # position memory
        ctl.float.move(123, 234)
        ctl.float.moved.emit(123, 234)
        check("floating position saved", config.Settings.load().float_pos == [123, 234])
        ctl.set_float(False)
        check("floating light can be hidden and the choice is saved", not ctl.float.isVisible() and config.Settings.load().float_visible is False)
        ctl.set_float(True)
        check("close hides to tray when a tray exists, else would quit", ctl.window.can_hide == ctl.have_tray)
        check("no token file written for the local source", not (config.config_dir() / "token").exists())
        from jarvis_panel.lightpaint import light_icon
        for st in ("idle", "listening", "thinking", "speaking", "unknown"):
            light_icon(st, 1.0, 96).pixmap(96, 96).save(str(OUT / f"tray-{st}.png"))

    elif scenario == "paste":
        check("starts with no token (source none)", ctl.conn.source == "none")
        check("dialog is shown", wait(lambda: ctl.dialog is not None and ctl.dialog.isVisible()))
        d = ctl.dialog
        d.host.setText(f"127.0.0.1:{port}")
        d.token.setText("short")
        d._submit()
        check("junk token gets a plain error before any network call", "doesn" in d.error.text() and d.error.isVisible(), d.error.text())
        d.token.setText("wrong-token-wrong-token-wrong-token")
        d._submit()
        check("wrong token is refused with advice", wait(lambda: "rejected the token" in d.error.text()), d.error.text())
        d.grab().save(str(OUT / "dialog-wrong-token.png"))
        check("Retry button is usable again", d.retry.isEnabled() and d.retry.text() == "Retry")
        d.token.setText(TOKEN)
        d._submit()
        check("right token connects", wait(lambda: ctl.loaded_once and ctl.dialog is None), "")
        tokfile = config.config_dir() / "token"
        check("token stored in the config dir", tokfile.read_text().strip() == TOKEN)
        if os.name != "nt":
            check("token file is user-only (600)", stat.S_IMODE(tokfile.stat().st_mode) == 0o600, oct(tokfile.stat().st_mode))
            check("settings file is user-only (600)", stat.S_IMODE((config.config_dir() / "settings.json").stat().st_mode) == 0o600)
        check("host remembered", config.Settings.load().host == "127.0.0.1")
        pause(1200)
        ctl.window.grab().save(str(OUT / "shell-window-paste.png"))

    elif scenario == "refused":
        check("dialog appears when nothing is listening", wait(lambda: ctl.dialog is not None and "Nothing is listening" in ctl.dialog.error.text(), 12000),
              ctl.dialog.error.text() if ctl.dialog else "no dialog")
        check("local mode dialog fields are locked", ctl.dialog.local and not ctl.dialog.token.isEnabled())
        ctl.dialog.grab().save(str(OUT / "dialog-refused.png"))
        proc = start_mock(port)
        try:
            ctl.dialog._submit()
            check("Retry connects once the server is up", wait(lambda: ctl.loaded_once and ctl.dialog is None, 12000))
        finally:
            stop(proc)

    ctl.client.stop()
    ctl.float.close()
    ctl.window.quitting = True
    ctl.window.close()
    ctl.cleanup()
    sys.exit(0 if all(results) else 1)


# ---------------------------------------------------------------- parent
def run(scenario):
    port = free_port()
    tmp = Path(tempfile.mkdtemp(prefix="panel-shell-"))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", JARVIS_PANEL_CONFIG_DIR=str(tmp / "cfg"), JARVIS_PANEL_HOST="127.0.0.1",
               JARVIS_PANEL_PORT=str(port), QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu",
               JARVIS_PANEL_REDUCED_MOTION="0")
    env.pop("JARVIS_PANEL_TOKEN_FILE", None)
    mock = None
    if scenario == "local":
        (tmp / "tok").write_text(TOKEN + "\n")
        env["JARVIS_PANEL_TOKEN_FILE"] = str(tmp / "tok")
    if scenario == "refused":
        (tmp / "tok").write_text(TOKEN + "\n")
        env["JARVIS_PANEL_TOKEN_FILE"] = str(tmp / "tok")
    else:
        mock = start_mock(port)
    print(f"[{scenario}]", flush=True)
    try:
        r = subprocess.run([sys.executable, __file__, "--child", scenario], env=env, timeout=180)
        return r.returncode
    finally:
        if mock:
            stop(mock)


if __name__ == "__main__":
    if "--child" in sys.argv:
        child(sys.argv[sys.argv.index("--child") + 1])
    else:
        rc = 0
        for sc in ("local", "paste", "refused"):
            rc |= run(sc)
        print("\nshell checks: " + ("all passed" if rc == 0 else "FAILURES"))
        sys.exit(rc)
