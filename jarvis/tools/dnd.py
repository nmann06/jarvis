"""Do not disturb: while it's on, Jarvis gives no notifications. Alerts it starts itself
(timers, "I'm finished in ...", tell_owner, the phone battery, reminders) are posted in Discord
instead of spoken; alarms still ring, and "Hey Jarvis" is still answered out loud. The state
is kept in data/dnd.json, so it lasts through a restart; the settings window (jarvis-wrapper's
/jarvis settings) writes the same file.

Turned on and off by voice or Discord ("do not disturb", "do not disturb off": answered here,
without Claude), or by Claude with the do_not_disturb tool ("no notifications for an hour").
"""

import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta

from jarvis import config
from jarvis.tools import tool
from jarvis.tools.clock import TZ

log = logging.getLogger(__name__)

STATE = config.ROOT / "data" / "dnd.json"
_lock = threading.Lock()


def _load():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"on": False, "until": None}


def active():
    """Whether do not disturb is on now (it turns itself off once its end time has passed)."""
    state = _load()
    if not state.get("on"):
        return False
    until = state.get("until")
    if until and datetime.fromisoformat(until) <= datetime.now(TZ):
        set_dnd(False)
        log.info("do not disturb ended (it was until %s)", until)
        return False
    return True


def set_dnd(on, minutes=None):
    """Turn it on (for minutes, or until turned off) or off. Returns a short confirmation."""
    until = (datetime.now(TZ) + timedelta(minutes=minutes)) if on and minutes else None
    with _lock:
        STATE.parent.mkdir(exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"on": bool(on),
                                   "until": until.isoformat(timespec="seconds") if until else None}))
        os.replace(tmp, STATE)  # the settings window may be reading it
    log.info("do not disturb %s%s", "on" if on else "off", f" until {until:%H:%M}" if until else "")
    if not on:
        return "Do not disturb is off, sir."
    return (f"Do not disturb is on until {until:%-I:%M %p}." if until else
            "Do not disturb is on. I'll post alerts in Discord instead of saying them.")


@tool("Do not disturb: while on, Jarvis doesn't speak alerts it starts itself (timers, "
      "updates, tell_owner go to Discord instead; alarms still ring), but still answers out "
      "loud when the owner says Hey Jarvis. Turn it on or off, or check it with status (only "
      "change it when asked to); minutes is how long it stays on, 0 for until turned off.",
      action={"type": "string", "enum": ["on", "off", "status"]},
      minutes={"type": "integer", "minimum": 0})
def do_not_disturb(action, minutes):
    if action == "status":
        if not active():
            return "Do not disturb is off."
        until = _load().get("until")
        return ("Do not disturb is on" + (f" until {datetime.fromisoformat(until):%-I:%M %p}."
                                         if until else ", until turned off."))
    return set_dnd(action == "on", minutes or None)


# A bare request to turn it on or off, by voice or Discord: the whole message must match, so
# "what does do not disturb do" still goes to Claude.
_FILLER = r"(?:hey |ok |okay |jarvis |please |can you |could you |would you |just |)*"
_DND = r"(?:do not disturb(?: me)?|don t disturb(?: me)?|dnd|quiet mode|silent mode)"
_ON = re.compile(_FILLER + r"(?:(?:turn |switch |put )?(?:on )?(?:the )?" + _DND + r"(?: mode)?"
                 r"(?: on)?)(?: please| jarvis| sir| now)*")
_OFF = re.compile(_FILLER + r"(?:(?:turn |switch )?(?:off )?(?:the )?" + _DND + r"(?: mode)?"
                  r" off|(?:turn |switch )off (?:the )?" + _DND + r"(?: mode)?|"
                  r"end " + _DND + r"(?: mode)?|you can (?:talk|speak) (?:again|now))"
                  r"(?: please| jarvis| sir| now)*")


def request(text):
    """True (turn it on) or False (off) if text is a bare do-not-disturb request, else None."""
    words = " ".join(re.sub(r"[^a-z ]+", " ", text.lower().replace("'", " ")).split())
    if _OFF.fullmatch(words):
        return False
    if _ON.fullmatch(words):
        return True
    return None
