"""Goodnight and good morning.

"Hey Jarvis, goodnight" (or "I'm going to bed"): the lights go off, Jarvis says goodnight and
when the alarm is, then do not disturb goes on, until the good morning routine.

Good morning: when the owner turns off a morning alarm (4 am to noon; not a snooze, nor one
that rang out), once a day, or whenever they say "good morning": do not disturb goes off, the
lights come on one by one, and Jarvis says good morning and the time, then reads out the
morning briefing. That's the one proactive put together before the alarm and posted in
#jarvis (data/briefing.json), so nothing is looked up twice; if there isn't one yet, it's put
together (and posted) now.

The voice loop (jarvis/voice.py) runs them; in Discord, "goodnight" and "good morning" do the
same without the talking.
"""

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta

from jarvis import alarms, homecoming, proactive
from jarvis.tools import dnd, kasa
from jarvis.tools.clock import TZ

log = logging.getLogger(__name__)

BRIEFING_WAIT = 90  # seconds to wait for a briefing still being put together
ALARM_AHEAD = timedelta(hours=18)  # goodnight mentions an alarm set within this long

pending = threading.Event()  # a morning alarm was just turned off: the voice loop says good morning
_last_alarm_morning = None  # the day a turned-off alarm last started good morning


def alarm_stopped(alarm):
    """alarms.on_stop: the owner turned an alarm off. A morning one starts good morning, once
    a day."""
    global _last_alarm_morning
    now = datetime.now(TZ)
    if proactive.MORNING_FROM <= now.hour < proactive.BRIEFING_LATEST_HOUR \
            and _last_alarm_morning != now.date():
        _last_alarm_morning = now.date()
        log.info("alarm %d turned off in the morning: good morning", alarm["id"])
        pending.set()


# A bare goodnight or good morning: the whole message must match, so "good morning, what's the
# weather" still goes to Claude.
_FILLER = r"(?:hey |ok |okay |jarvis |well |)*"
_END = r"(?: jarvis| sir| then| everyone| all)*"
_NIGHT = re.compile(_FILLER + r"(?:good ?night|night night|nighty night|night|"
                    r"(?:i m |im |i am )?(?:going|off|heading) to (?:bed|sleep)|"
                    r"time (?:for|to go to) bed|bedtime)" + _END)
_MORNING = re.compile(_FILLER + r"(?:good ?morning|morning)" + _END)


def request(text):
    """"night" or "morning" if text is a bare goodnight or good morning, else None."""
    words = " ".join(re.sub(r"[^a-z ]+", " ", text.lower().replace("'", " ")).split())
    if _NIGHT.fullmatch(words):
        return "night"
    if _MORNING.fullmatch(words):
        return "morning"
    return None


def goodnight(channel):
    """Turn the lights off; returns what to say: goodnight, when the alarm is, and any light
    that didn't answer. The caller turns do not disturb on once it's been said (with it on,
    the voice would only post it)."""
    lights = kasa.switch_all("off")
    now = datetime.now(TZ)
    reply = "Goodnight, sir."
    upcoming = [a for a in alarms.alarms() if a["at"] - now < ALARM_AHEAD]
    if upcoming:
        reply += f" Your alarm is set for {alarms._say(upcoming[0]['at'], now, channel)}."
    else:
        reply += " There's no alarm set for the morning."
    if lights != "Lights off, sir.":  # switch_all's answer when every light went off
        reply += f" {lights}"
    return reply


def _sentences(text):
    return [s for s in homecoming.SENTENCE_END.split(text.strip()) if s]


def good_morning(speak):
    """The morning routine, blocking until it's done. speak(texts) says a list of sentences
    and returns False if the owner said stop, which skips the rest. Never raises."""
    from jarvis.voice import clock_words  # voice imports this module

    pending.clear()
    try:
        if dnd.active():
            dnd.set_dnd(False)
        pool = ThreadPoolExecutor(max_workers=1)
        job = pool.submit(proactive.briefing)  # at once if it's ready, as it usually is
        pool.shutdown(wait=False)
        lights = {}
        switching = threading.Thread(target=homecoming._lights, args=(lights,),
                                     name="morning-lights", daemon=True)
        switching.start()
        if speak([f"Good morning, sir. It's {clock_words(datetime.now(TZ))}."]) is False:
            return
        said = []
        switching.join(timeout=2)  # still going after that: say nothing about them
        if not switching.is_alive() and homecoming._lights_line(lights):
            said.append(homecoming._lights_line(lights))
        if not job.done():
            if speak(said + ["Your briefing is on its way."]) is False:
                return
            said = []
        try:
            said += _sentences(job.result(timeout=BRIEFING_WAIT)["spoken"])
        except TimeoutError:
            said.append("Your briefing is taking too long, I'm afraid; it'll be in Discord.")
        except Exception:
            log.exception("good morning: no briefing")
            said.append("I couldn't put the briefing together, I'm afraid.")
        speak(said)
    except Exception:
        log.exception("good morning failed")


def written(routine):
    """Goodnight or good morning from Discord: the same, with a written reply."""
    if routine == "night":
        reply = goodnight("discord")
        dnd.set_dnd(True)
        return reply
    if dnd.active():
        dnd.set_dnd(False)
    lights = {}
    homecoming._lights(lights)
    reply = "Good morning, sir."
    if line := homecoming._lights_line(lights):
        reply += f" {line}"
    try:
        proactive.briefing()  # posted in #jarvis when it's put together, so it's there already
        return reply + " Your briefing is above."
    except Exception:
        log.exception("good morning: no briefing")
        return reply + " I couldn't put the briefing together, I'm afraid."


if __name__ == "__main__":
    import sys

    for line in sys.argv[1:] or ["goodnight", "hey jarvis good night", "good morning jarvis",
                                 "good morning what's the weather", "i'm going to bed"]:
        print(f"{line!r}: {request(line)}")
