"""A local socket other programs use to have Jarvis say something, e.g. the Claude Code hook
(deploy/claude-hook.py) when a Claude session finishes or needs you.

Send one line of JSON, {"text": "..."}: it's spoken, or posted in Discord if voice is off.
Or {"ask": "...", "aloud": false}: a question for Jarvis, from the `jarvis` terminal command
(deploy/ask.py); the reply comes back as one line of JSON, {"reply": "..."}, and with aloud
it's also spoken. Only this user can connect (the socket is mode 600, in the user's runtime
directory).
"""

import json
import logging
import os
import queue
import socket
import threading

from jarvis import config, llm, notify

log = logging.getLogger(__name__)

_queue = queue.Queue()  # spoken one after another, in the order they came
_brains = {}  # the terminal's conversations: written replies, and ones to be spoken (-a)
_brains_lock = threading.Lock()


def _speak():
    while True:
        notify.send("voice", _queue.get())


def _serve(server):
    while True:
        conn, _ = server.accept()
        try:
            conn.settimeout(2)
            msg = json.loads(conn.makefile().readline())
        except Exception as e:
            log.warning("bad alert on %s: %s", config.ALERT_SOCKET, e)
            conn.close()
            continue
        if "ask" in msg:  # takes seconds: answered on its own thread, so alerts aren't held up
            threading.Thread(target=_answer, args=(conn, msg["ask"], bool(msg.get("aloud"))),
                             name="terminal", daemon=True).start()
            continue
        conn.close()
        if msg.get("text", "").strip():
            say(msg["text"].strip())


def _answer(conn, question, aloud):
    with conn:
        try:
            with _brains_lock:  # one question at a time per conversation
                brain = _brains.get(aloud) or _brains.setdefault(
                    aloud, llm.Brain("terminal", style="voice" if aloud else None))
                log.info("terminal%s: %s", " (aloud)" if aloud else "", question)
                reply = brain.ask(question)
            if aloud and reply:
                say(reply, limit=None)
        except Exception as e:
            log.exception("terminal question failed")
            reply = f"Sorry, something went wrong: {type(e).__name__}: {e}"
        try:
            conn.settimeout(10)
            conn.sendall((json.dumps({"reply": reply or ""}) + "\n").encode())
        except OSError:
            pass  # the terminal gave up waiting


def say(text, limit=300):
    """Speak text after anything already queued (posted in Discord if voice is off)."""
    _queue.put(text[:limit])


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
