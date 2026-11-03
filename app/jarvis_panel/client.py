"""Keeps track of whether the panel is reachable and what the light is doing.

GET /api/status once, then the /api/events stream. When anything fails it reports `unknown` for
the light (not `idle`: a dropped connection must never look like a quiet Jarvis) and tries again
with a growing delay. A 401 is reported separately, since retrying won't fix a wrong token.
"""

import json

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .config import Connection

STATES = ("idle", "listening", "thinking", "speaking", "unknown")
STREAM_SILENCE_MS = 40_000   # the server sends a comment line every 15 s


def explain(err: QNetworkReply.NetworkError, status: int, conn: Connection) -> str:
    """What went wrong and what to do about it, in plain words."""
    E = QNetworkReply.NetworkError
    where = f"{conn.host}:{conn.port}"
    if status == 401:
        return ("The panel rejected the token. Copy it again from data/panel-token on the Jarvis machine "
                "and paste it here.")
    if status:
        return f"The panel at {where} answered with an error ({status}). Check the panel service on the Jarvis machine."
    if err == E.HostNotFoundError:
        return (f"Can't find a machine called “{conn.host}”. Check that Tailscale is connected, "
                "or use the machine's Tailscale IP address instead.")
    if err == E.ConnectionRefusedError:
        return (f"Nothing is listening at {where}. The Jarvis machine may be off, or the panel service isn't running "
                "(on that machine: systemctl --user status jarvis-panel).")
    if err in (E.TimeoutError, E.OperationCanceledError):
        return (f"{where} didn't answer in time. Check that the Jarvis machine is on and that Tailscale is connected.")
    return f"Couldn't connect to {where}: network error {err.name if hasattr(err, 'name') else int(err)}."


class PanelClient(QObject):
    light = Signal(str)            # idle | listening | thinking | speaking | unknown (already folds in connection and hooks)
    connected = Signal(bool)
    hooks = Signal(bool)
    auth_failed = Signal(str)      # the token was refused
    problem = Signal(str)          # why the last attempt failed, in plain words
    checked = Signal(bool, str)    # result of check(): ok, message

    def __init__(self, conn: Connection, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.nam = QNetworkAccessManager(self)
        self._connected = False
        self._hooks = False
        self._state = "unknown"
        self._stream: QNetworkReply | None = None
        self._buf = b""
        self._retry = 0
        self._running = False
        self._retry_timer = QTimer(self, singleShot=True)
        self._retry_timer.timeout.connect(self._attempt)
        self._watchdog = QTimer(self, singleShot=True)
        self._watchdog.timeout.connect(self._stream_silent)
        self._probe: QNetworkReply | None = None

    # -- public
    @property
    def shown_light(self) -> str:
        return self._state if self._connected and self._hooks else "unknown"

    def set_connection(self, conn: Connection) -> None:
        self.conn = conn

    def start(self) -> None:
        self._running = True
        self._retry = 0
        self._attempt()

    def stop(self) -> None:
        self._running = False
        self._retry_timer.stop()
        self._watchdog.stop()
        self._drop_stream()
        if self._probe:
            self._probe.abort()
        self._set_connected(False)

    def check(self, conn: Connection | None = None) -> None:
        """One-off test of a connection (for the dialog). Emits `checked`."""
        c = conn or self.conn
        reply = self.nam.get(self._request(c, "/api/status"))
        reply.finished.connect(lambda: self._checked(reply, c))

    # -- internals
    def _request(self, conn: Connection, path: str) -> QNetworkRequest:
        req = QNetworkRequest(QUrl(conn.base + path))
        req.setRawHeader(b"Authorization", f"Bearer {conn.token}".encode())
        req.setRawHeader(b"Accept", b"application/json")
        req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        req.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute, QNetworkRequest.CacheLoadControl.AlwaysNetwork)
        return req

    @staticmethod
    def _status(reply: QNetworkReply) -> int:
        v = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        return int(v) if v else 0

    def _checked(self, reply: QNetworkReply, conn: Connection) -> None:
        st = self._status(reply)
        ok = st == 200
        msg = "Connected." if ok else explain(reply.error(), st, conn)
        reply.deleteLater()
        self.checked.emit(ok, msg)

    def _set_connected(self, ok: bool) -> None:
        if ok != self._connected:
            self._connected = ok
            self.connected.emit(ok)
        self.light.emit(self.shown_light)

    def _attempt(self) -> None:
        if not self._running:
            return
        req = self._request(self.conn, "/api/status")
        req.setTransferTimeout(8000)
        self._probe = reply = self.nam.get(req)
        reply.finished.connect(lambda: self._status_done(reply))

    def _status_done(self, reply: QNetworkReply) -> None:
        if reply is not self._probe:
            reply.deleteLater()
            return
        self._probe = None
        st = self._status(reply)
        if st == 200:
            try:
                data = json.loads(bytes(reply.readAll()).decode("utf-8"))
            except ValueError:
                data = {}
            reply.deleteLater()
            self._hooks = bool(data.get("hooks_live"))
            self._state = data.get("state") if data.get("state") in STATES else "unknown"
            self._retry = 0
            self._set_connected(True)
            self.hooks.emit(self._hooks)
            self._open_stream()
            return
        msg = explain(reply.error(), st, self.conn)
        reply.deleteLater()
        self._set_connected(False)
        if st == 401:
            self._running = False
            self.auth_failed.emit(msg)
            return
        self.problem.emit(msg)
        self._schedule_retry()

    def _schedule_retry(self) -> None:
        if not self._running:
            return
        delay = min(10_000, 1000 * 2 ** self._retry)
        self._retry += 1
        self._retry_timer.start(delay)

    def _open_stream(self) -> None:
        self._drop_stream()
        req = self._request(self.conn, "/api/events")
        req.setRawHeader(b"Accept", b"text/event-stream")
        self._buf = b""
        self._stream = reply = self.nam.get(req)
        reply.readyRead.connect(lambda: self._on_data(reply))
        reply.finished.connect(lambda: self._stream_done(reply))
        self._watchdog.start(STREAM_SILENCE_MS)

    def _drop_stream(self) -> None:
        if self._stream is not None:
            r, self._stream = self._stream, None
            r.abort()
            r.deleteLater()

    def _stream_silent(self) -> None:
        self._drop_stream()
        self._lost()

    def _stream_done(self, reply: QNetworkReply) -> None:
        if reply is not self._stream:
            return
        self._stream = None
        reply.deleteLater()
        self._lost()

    def _lost(self) -> None:
        self._watchdog.stop()
        self._set_connected(False)
        self._schedule_retry()

    def _on_data(self, reply: QNetworkReply) -> None:
        if reply is not self._stream:
            return
        self._watchdog.start(STREAM_SILENCE_MS)
        self._buf += bytes(reply.readAll())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            try:
                ev = json.loads(line[5:].decode("utf-8"))
            except ValueError:
                continue
            self._event(ev)

    def _event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "state" and ev.get("state") in STATES:
            self._state = ev["state"]
            self.light.emit(self.shown_light)
        elif kind == "hooks":
            self._hooks = bool(ev.get("live"))
            self.hooks.emit(self._hooks)
            self.light.emit(self.shown_light)
