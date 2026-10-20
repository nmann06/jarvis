import threading
import time

from jarvis.tools import tool

# main.py points this at the speaker; until then alerts are printed.
on_alert = print

_timers = {}  # id -> (label, end time, threading.Timer)
_next_id = 1
_lock = threading.Lock()


def _fire(timer_id):
    with _lock:
        label, _, _ = _timers.pop(timer_id)
    on_alert(f"Sir, your {label} timer is done." if label else "Sir, your timer is done.")


@tool("Start a countdown timer that announces itself when done. Several can run at once.",
      seconds={"type": "integer", "description": "Duration in seconds", "minimum": 1},
      label={"type": "string", "description": "Short name, e.g. 'tea'; empty if none given"})
def set_timer(seconds, label):
    global _next_id
    with _lock:
        timer_id, _next_id = _next_id, _next_id + 1
        t = threading.Timer(seconds, _fire, args=(timer_id,))
        t.daemon = True
        _timers[timer_id] = (label, time.monotonic() + seconds, t)
    t.start()
    return f"Timer set for {seconds} seconds."


@tool("List running timers and the seconds left on each.")
def list_timers():
    now = time.monotonic()
    with _lock:
        running = [{"label": label or "(none)", "seconds_left": round(end - now)}
                   for label, end, _ in _timers.values()]
    return running or "No timers running."
