"""A local socket other programs use to have Jarvis say something, e.g. the Claude Code hook
(deploy/claude-hook.py) when a Claude session finishes or needs you.

Send one line of JSON, {"text": "..."}: it's spoken, or posted in Discord if voice is off.
Only this user can connect (the socket is mode 600, in the user's runtime directory).
"""

import json
import logging
import os
import queue
import socket
import threading

from jarvis import config, notify

log = logging.getLogger(__name__)

_queue = queue.Queue()  # spoken one after another, in the order they came


def _speak():
    while True:
        notify.send("voice", _queue.get())


def _serve(server):
    while True:
        conn, _ = server.accept()
        with conn:
            try:
                conn.settimeout(2)
                text = json.loads(conn.makefile().readline())["text"].strip()
            except Exception as e:
                log.warning("bad alert on %s: %s", config.ALERT_SOCKET, e)
                continue
        if text:
            say(text)


def say(text):
    """Speak text after anything already queued (posted in Discord if voice is off)."""
    _queue.put(text[:300])


def start():
    path = config.ALERT_SOCKET
    if os.path.exists(path):
        os.unlink(path)  # left over from the last run
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        server.bind(str(path))
    finally:
        os.umask(old)
    server.listen()
    threading.Thread(target=_serve, args=(server,), name="alerts", daemon=True).start()
    threading.Thread(target=_speak, name="alerts-speak", daemon=True).start()
    log.info("alerts socket: %s", path)
