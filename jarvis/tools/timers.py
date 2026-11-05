"""Countdown timers. Running ones are kept in data/timers.json, so a restart (hyde's nightly one,
or a crash) picks them up again: restore() at start sets them going for what's left, and one
that ran out while Jarvis was down goes off at once."""
import json
import threading
import time

from jarvis import config, notify
from jarvis.tools import tool

PATH = config.ROOT / "data" / "timers.json"

CHANNEL_WAIT = 90  # seconds a timer waits for its channel (voice, Discord) after a restart

_timers = {}  # id -> (label, end time (epoch seconds), channel, threading.Timer)
_next_id = 1
_lock = threading.Lock()


def _save():
    """Called with _lock held."""
    data = [{"id": i, "label": label, "ends": end, "channel": channel}
            for i, (label, end, channel, _) in _timers.items()]
    try:
        PATH.parent.mkdir(exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(PATH)
    except OSError:
        pass


def _fire(timer_id, late=False, waited=0):
    with _lock:
        if timer_id not in _timers:
            return
        label, end, channel, _ = _timers[timer_id]
        if not notify.available(channel) and waited < CHANNEL_WAIT:
            # Just restarted: the voice loop or Discord isn't up yet.
            t = threading.Timer(2, _fire, args=(timer_id, late, waited + 2))
            t.daemon = True
            _timers[timer_id] = (label, end, channel, t)
            t.start()
            return
        del _timers[timer_id]
        _save()
    done = f"Sir, your {label} timer is done." if label else "Sir, your timer is done."
    notify.send(channel, done[:-1] + " (it ran out while I was restarting)." if late else done)


def _start(label, end, channel, late=False):
    """Called with _lock held."""
    global _next_id
    timer_id, _next_id = _next_id, _next_id + 1
    t = threading.Timer(max(0.0, end - time.time()), _fire, args=(timer_id, late))
    t.daemon = True
    _timers[timer_id] = (label, end, channel, t)
    _save()
    t.start()


def restore():
    """Set the timers saved by the last run going again."""
    try:
        saved = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return
    now = time.time()
    with _lock:
        for timer in saved:
            _start(timer.get("label") or "", timer["ends"], timer.get("channel") or "voice",
                   late=timer["ends"] <= now)


@tool("Start a countdown timer that announces itself when done. Several can run at once.",
      seconds={"type": "integer", "description": "Duration in seconds", "minimum": 1},
      label={"type": "string", "description": "Short name, e.g. 'tea'; empty if none given"})
def set_timer(seconds, label):
    with _lock:
        # The alert goes back wherever the timer was set: spoken, or a Discord message.
        _start(label, time.time() + seconds, notify.channel.get())
    return f"Timer set for {seconds} seconds."


@tool("List running timers and the seconds left on each.")
def list_timers():
    now = time.time()
    with _lock:
        running = [{"label": label or "(none)", "seconds_left": round(end - now)}
                   for label, end, _, _ in _timers.values()]
    return running or "No timers running."
