"""Voice hooks for the Jarvis Panel (docs/panel-api.md): what the voice loop is doing, kept and
published from inside the Jarvis process.

The voice loop calls the small functions below as things happen: voice state, every mic block
(kept for 30 s in memory, overwritten continuously), wake word scores, what stop detection
decided. A Unix socket (SOCKET, owner only) serves them to the panel, a separate process. One
JSON request line per connection:

    {"cmd": "subscribe"}              a status line, then state, level and muted lines as they come
    {"cmd": "status"}                 one status line
    {"cmd": "snapshot"}               a line with the context and "samples", then that many int16s
    {"cmd": "mute", "muted": true}    one status line

Nothing here may ever break the voice loop. Every hook catches its own exceptions (logged, at
most once a minute each). The audio path takes no locks and never touches a socket: a message
goes on each client's bounded queue, the oldest dropping out if the client has fallen behind, and
the client's own thread sends it.

Doesn't import jarvis.config, audio or sounddevice, so the panel can use SOCKET while .env is broken.
"""

import json
import logging
import os
import socket
import threading
import time
from collections import deque
from functools import wraps
from pathlib import Path

import numpy as np

log = logging.getLogger("jarvis.events")

SOCKET = Path(__file__).resolve().parent.parent / "data" / "voice-events.sock"
SAMPLE_RATE = 16000  # audio.SAMPLE_RATE and BLOCK; audio imports this module
BLOCK = 1280
BUFFER_SECONDS = 30
BUFFER_BLOCKS = BUFFER_SECONDS * SAMPLE_RATE // BLOCK

MAX_CONNECTIONS = 8
SEND_TIMEOUT = 2.0  # a client that takes longer than this to accept data is dropped
POLL_SECONDS = 0.05  # how often a client's thread looks at its queue
PING_SECONDS = 5.0
CLIENT_QUEUE = 256  # messages (about 20 s of levels)

_ring = deque(maxlen=BUFFER_BLOCKS)  # the mic's last 30 s, one array per block
_scores = deque(maxlen=BUFFER_BLOCKS)  # (time, wake score) per block fed to the model
_wakes = deque(maxlen=20)  # wake words that fired
_stops = deque(maxlen=50)  # what stop detection decided
_states = deque(maxlen=50)  # state changes

_state = "idle"
_before_speaking = None
_muted = False
_threshold = None  # the running Jarvis's WAKE_THRESHOLD
_score = 0.0  # the latest wake score
_whisper = None  # what Whisper heard in the stop check under way
_alarm_ringing = None  # callable set by start()
_started = None  # when start() ran; None: hooks not running, mic_block just hands blocks back

_clients = ()  # one deque per subscriber; replaced, never changed, so the audio path needn't lock
_lock = threading.Lock()  # connection threads only, never the audio path
_connections = 0
_server = None

_complained = {}


def _complain(name):
    now = time.monotonic()
    if now - _complained.get(name, -3600.0) >= 60:
        _complained[name] = now
        log.exception("voice hook %s failed (the voice loop carries on)", name)


def _safe(hook):
    @wraps(hook)
    def safe(*args, **kwargs):
        try:
            return hook(*args, **kwargs)
        except Exception:
            _complain(hook.__name__)
    return safe


def _publish(message):
    for client in _clients:
        client.append(message)


def _set(state):
    global _state
    _state = state
    t = time.time()
    _states.append({"state": state, "t": t})
    _publish({"type": "state", "state": state, "t": t})


@_safe
def set_state(state):
    """idle, listening or thinking (speaking is speaking())."""
    if state != _state:
        _set(state)


@_safe
def speaking(on):
    """Sound started or finished (jarvis.tts). An alert can talk over any state, so afterwards
    it goes back to the state before."""
    global _before_speaking
    if on and _state != "speaking":
        _before_speaking = _state
        _set("speaking")
    elif not on and _state == "speaking":
        _set(_before_speaking or "idle")


def _keep_listening():
    """Alarms are heard through mute: if it can't be told whether one is ringing, listen."""
    try:
        return bool(_alarm_ringing and _alarm_ringing())
    except Exception:
        _complain("alarm check")
        return True


def mic_block(block):
    """Every block the mic captures, from the audio callback, so it must be quick. Returns the
    block to use: the same one, or silence while muted (no alarm ringing)."""
    original = block
    try:
        if _started is None:
            return block
        if _muted and not _keep_listening():
            block = np.zeros_like(block)
        _ring.append(block)
        if _clients:
            x = block.astype(np.float32) / 32768
            _publish({"type": "level", "rms": round(float(np.sqrt(np.mean(x * x))), 4),
                      "peak": round(float(np.abs(x).max()), 4),
                      "wake_score": round(_score, 4), "t": time.time()})
        return block
    except Exception:
        _complain("mic_block")
        return original


@_safe
def wake_score(score, threshold):
    """The model's score for one block."""
    global _score, _threshold
    _score, _threshold = float(score), float(threshold)
    _scores.append((time.time(), _score))


@_safe
def wake_fired(score):
    _wakes.append({"t": time.time(), "score": float(score), "ignored": None})


@_safe
def wake_ignored(why):
    """The wake word just fired, but the voice loop didn't act on it."""
    if _wakes:
        _wakes[-1]["ignored"] = why


@_safe
def whisper_heard(text):
    """What Whisper heard while checking a stop for echo: goes with the next stop_event."""
    global _whisper
    _whisper = text


@_safe
def stop_event(decision, vosk=None, phrase=None, score=None):
    """What stop detection decided while Jarvis talked. decision: stopped, echo (Whisper heard
    Jarvis's own words), own_words (the stop phrase was in what Jarvis was saying), not_a_stop,
    wake_stopped ("Hey Jarvis" stopped it) or wake_own_name (ignored: Jarvis had said "Jarvis")."""
    global _whisper
    whisper, _whisper = _whisper, None
    _stops.append({"t": time.time(), "decision": decision, "vosk": vosk, "phrase": phrase,
                   "whisper": whisper, "wake_score": None if score is None else float(score)})
    if decision == "wake_own_name" and _wakes:
        _wakes[-1]["ignored"] = "Jarvis had just said its own name"


def _copy(items):
    """A list of a deque another thread may be appending to."""
    for _ in range(5):
        try:
            return list(items)
        except RuntimeError:  # changed during the copy
            time.sleep(0.001)
    return []


def status():
    return {"type": "status", "state": _state, "muted": _muted, "started": _started,
            "threshold": _threshold, "pid": os.getpid(), "t": time.time()}


def audio():
    """The mic's last 30 s as int16 samples."""
    blocks = _copy(_ring)
    return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.int16)


def context(samples=None):
    """Recent evidence, for a report: wake scores, wakes, stop decisions, state changes."""
    now = time.time()
    scores = [(t, s) for t, s in _copy(_scores) if now - t <= BUFFER_SECONDS]
    per_second = {}  # the highest score in each second, so it stays small
    for t, s in scores:
        per_second[int(t)] = max(s, per_second.get(int(t), 0.0))
    samples = audio() if samples is None else samples
    return {"t": now, "state": _state, "muted": _muted, "threshold": _threshold,
            "started": _started,
            "peak_wake_score": max((s for _, s in scores), default=None),
            "wake_scores": [{"t": t, "score": round(s, 4)} for t, s in sorted(per_second.items())],
            "wakes": _copy(_wakes),
            "stop_events": _copy(_stops)[-20:],
            "states": _copy(_states)[-20:],
            "mic_peak": round(int(np.abs(samples.astype(np.int32)).max()) / 32768, 4) if len(samples)
            else None}


def set_muted(on):
    global _muted
    _muted = bool(on)
    log.info("mic %s from the panel", "muted" if _muted else "unmuted")
    _publish({"type": "muted", "muted": _muted, "t": time.time()})


def _send(conn, message):
    conn.sendall(json.dumps(message).encode() + b"\n")


def _stream(conn):
    """A subscriber: its queue is filled by _publish and sent from this thread."""
    global _clients
    client = deque(maxlen=CLIENT_QUEUE)
    with _lock:
        _clients = (*_clients, client)
    try:
        _send(conn, status())
        last = time.monotonic()
        while _server is not None:
            lines = []
            while client:
                lines.append(json.dumps(client.popleft()).encode() + b"\n")
            if not lines and time.monotonic() - last >= PING_SECONDS:
                lines.append(b'{"type": "ping"}\n')  # finds out when the client has gone
            if lines:
                conn.sendall(b"".join(lines))
                last = time.monotonic()
            time.sleep(POLL_SECONDS)
    finally:
        with _lock:
            _clients = tuple(c for c in _clients if c is not client)


def _request(conn):
    data = b""
    while b"\n" not in data:
        more = conn.recv(4096)
        if not more or len(data) > 4096:
            raise ValueError("no request line")
        data += more
    return json.loads(data.split(b"\n", 1)[0])


def _connection(conn):
    global _connections
    try:
        with conn:
            conn.settimeout(SEND_TIMEOUT)
            request = _request(conn)
            cmd = request.get("cmd")
            if cmd == "subscribe":
                _stream(conn)
            elif cmd == "status":
                _send(conn, status())
            elif cmd == "mute":
                set_muted(request.get("muted"))
                _send(conn, status())
            elif cmd == "snapshot":
                samples = audio()
                _send(conn, {"type": "snapshot", "samples": len(samples), "rate": SAMPLE_RATE,
                             "context": context(samples)})
                conn.sendall(samples.astype("<i2").tobytes())
            else:
                _send(conn, {"error": f"unknown command {cmd!r}"})
    except (OSError, ValueError, AttributeError) as e:
        log.debug("panel connection ended: %s", e)
    except Exception:
        log.exception("panel connection failed")
    finally:
        with _lock:
            _connections -= 1


def _serve(server):
    global _connections
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            if _server is not server:
                return
            time.sleep(0.5)
            continue
        with _lock:
            full = _connections >= MAX_CONNECTIONS
            if not full:
                _connections += 1
        if full:
            conn.close()
            continue
        threading.Thread(target=_connection, args=(conn,), name="events-client",
                         daemon=True).start()


@_safe
def start(alarm_ringing=None, path=None):
    """Start the hooks and the socket, once. alarm_ringing() says when mute must not apply."""
    global _alarm_ringing, _started, _server
    if _started is not None:
        return
    _alarm_ringing, _started = alarm_ringing, time.time()
    path = Path(path or SOCKET)
    path.parent.mkdir(exist_ok=True)
    # Bound under another name and moved into place, so it's never there without its mode and a
    # socket left by a crash doesn't matter.
    tmp = path.with_name(f"{path.name}.{os.getpid()}")
    tmp.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(tmp))
    os.chmod(tmp, 0o600)
    server.listen(MAX_CONNECTIONS)
    os.replace(tmp, path)
    _server = server
    threading.Thread(target=_serve, args=(server,), name="events", daemon=True).start()
    log.info("voice hooks live on %s", path)


def stop():
    """Back to how it was before start() (tests: the running Jarvis just exits)."""
    global _alarm_ringing, _started, _server, _state, _before_speaking, _muted, _threshold, _score
    global _whisper
    server, _server = _server, None
    if server is not None:
        server.close()
    _alarm_ringing = _started = _before_speaking = _threshold = _whisper = None
    _state, _muted, _score = "idle", False, 0.0
    for items in (_ring, _scores, _wakes, _stops, _states):
        items.clear()
