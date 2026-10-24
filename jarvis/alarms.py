"""Alarms, handled entirely here: no Claude. Requests are matched with regexes, alarms are kept
in data/alarms.json (so they survive a restart), and a ringing alarm is a looping sound that
gets louder for up to RING_SECONDS until the owner says stop (voice), or writes it (Discord).

Claude never sees an alarm request, so it can't say one is set when it isn't. Anything about
alarms that isn't understood gets a fixed local reply rather than going to Claude.

Try the parser: python -m jarvis.alarms "set an alarm for 7:30 tomorrow with birds"
Hear the sounds:  python -m jarvis.alarms --play birds
"""

import json
import logging
import random
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta

import numpy as np

from jarvis import config, notify
from jarvis.tools.clock import TZ

log = logging.getLogger(__name__)

PATH = config.ROOT / "data" / "alarms.json"
RATE = 48000
RING_SECONDS = config.ALARM_RING_MINUTES * 60  # rings this long at most, then gives up
RAMP_SECONDS = config.ALARM_RAMP_MINUTES * 60  # quietest to full volume over this long
START_GAIN = config.ALARM_START_VOLUME / 100  # how loud it starts, of full
HUSH_GAIN = 0.15  # while listening to what the owner says after "Hey Jarvis"
SNOOZE_MINUTES = config.ALARM_SNOOZE_MINUTES


# --- Sounds -------------------------------------------------------------------------------
# Each is one loop, synthesized here (no sound files). All have gaps, so the owner can be
# heard between beeps.

def _t(seconds):
    return np.arange(int(seconds * RATE)) / RATE


def _silence(seconds):
    return np.zeros(int(seconds * RATE))


def _fade(x, seconds=0.005):
    n = min(int(seconds * RATE), len(x) // 2)
    ramp = np.linspace(0, 1, n)
    x[:n] *= ramp
    x[len(x) - n:] *= ramp[::-1]
    return x


def _beep(freq, seconds):
    t = _t(seconds)
    # A few odd harmonics: buzzier than a sine, like an alarm clock, but not a harsh square.
    x = sum(np.sin(2 * np.pi * freq * k * t) / k for k in (1, 3, 5))
    return _fade(x)


def _bell(freq, seconds, decay):
    t = _t(seconds)
    partials = [(1, 1.0), (2.0, 0.5), (2.76, 0.35), (5.4, 0.15)]
    x = sum(a * np.sin(2 * np.pi * freq * p * t) * np.exp(-t * p / decay) for p, a in partials)
    return _fade(x, 0.002)


def _mix(notes, length):
    """notes: (start seconds, samples). Sums them into one buffer length seconds long."""
    out = _silence(length)
    for start, x in notes:
        i = int(start * RATE)
        x = x[:len(out) - i]
        out[i:i + len(x)] += x
    return out


def _classic():
    beep = np.concatenate([_beep(880, 0.12), _silence(0.08)])
    return np.concatenate([beep] * 4 + [_silence(0.7)])


def _digital():
    beep = np.concatenate([_beep(1500, 0.06), _silence(0.05)])
    return np.concatenate([beep] * 3 + [_silence(0.5)])


def _chimes():
    notes = [1047, 1319, 1568, 2093]  # C6 E6 G6 C7
    return _mix([(i * 0.35, _bell(f, 2.0, 0.8)) for i, f in enumerate(notes)], 3.2)


def _marimba():
    melody = [784, 659, 523, 659, 784, 1047, 784, 659]  # G5 E5 C5 E5 G5 C6 G5 E5
    t = _t(0.4)
    note = lambda f: _fade((np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * 4 * f * t))
                           * np.exp(-t / 0.12), 0.002)
    return _mix([(i * 0.22, note(f)) for i, f in enumerate(melody)], 2.9)


def _birds():
    rng = np.random.default_rng(7)  # the same birdsong every time

    def chirp(low, high, seconds):
        t = _t(seconds)
        freq = low + (high - low) * (t / seconds) ** 0.6
        x = np.sin(2 * np.pi * np.cumsum(freq) / RATE) * np.sin(np.pi * t / seconds) ** 2
        return x * (1 + 0.3 * np.sin(2 * np.pi * 60 * t))  # a little warble

    notes, start = [], 0.0
    for _ in range(3):  # three phrases of a few quick chirps
        for _ in range(rng.integers(3, 6)):
            notes.append((start, chirp(rng.uniform(2600, 3200), rng.uniform(4000, 4800),
                                       rng.uniform(0.05, 0.09))))
            start += rng.uniform(0.09, 0.14)
        start += rng.uniform(0.4, 0.7)
    return _mix(notes, start + 0.5)


SOUNDS = {"classic": _classic, "digital": _digital, "chimes": _chimes, "marimba": _marimba,
          "birds": _birds}
# What the owner might call each one.
_SOUND_NAMES = {"classic": "classic", "beep": "classic", "beeps": "classic", "beeping": "classic",
                "normal": "classic", "digital": "digital", "watch": "digital",
                "chime": "chimes", "chimes": "chimes", "bell": "chimes", "bells": "chimes",
                "marimba": "marimba", "xylophone": "marimba", "melody": "marimba",
                "bird": "birds", "birds": "birds", "birdsong": "birds", "chirping": "birds",
                "random": "random", "surprise": "random"}
_cache = {}


def sound(name):
    """One loop of the named sound, float32 with peak 0.9."""
    if name not in _cache:
        x = SOUNDS[name]()
        _cache[name] = (0.9 * x / np.abs(x).max()).astype(np.float32)
    return _cache[name]


# --- Storage ------------------------------------------------------------------------------

_lock = threading.RLock()


def _load():
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("alarms", [])
    data.setdefault("sound", config.ALARM_SOUND)
    return data


def _save(data):
    PATH.parent.mkdir(exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(PATH)


def alarms():
    """Set alarms, soonest first: dicts with "at" (aware datetime), "sound", "id"."""
    with _lock:
        out = [dict(a, at=datetime.fromisoformat(a["at"])) for a in _load()["alarms"]]
    return sorted(out, key=lambda a: a["at"])


def add(at, sound_name=None):
    with _lock:
        data = _load()
        alarm = {"id": max((a["id"] for a in data["alarms"]), default=0) + 1,
                 "at": at.isoformat(timespec="seconds"), "sound": sound_name or data["sound"]}
        data["alarms"].append(alarm)
        _save(data)
    _wake.set()
    log.info("alarm %d set for %s (%s)", alarm["id"], alarm["at"], alarm["sound"])
    return alarm


def remove(ids):
    with _lock:
        data = _load()
        data["alarms"] = [a for a in data["alarms"] if a["id"] not in ids]
        _save(data)
    log.info("alarms removed: %s", sorted(ids))


def default_sound():
    with _lock:
        return _load()["sound"]


def set_default_sound(name):
    with _lock:
        data = _load()
        data["sound"] = name
        _save(data)


# --- Ringing ------------------------------------------------------------------------------

_ringing = None  # while an alarm rings: {"alarm", "stop" Event, "hush" Event, "proc"}
_wake = threading.Event()  # the scheduler looks again now (an alarm was added)
on_ring = []  # functions called (from the alarm thread) when an alarm starts ringing


def ringing():
    return _ringing is not None


def hush(on):
    """Turn a ringing alarm right down while the owner talks to Jarvis, and back up."""
    r = _ringing
    if r:
        (r["hush"].set if on else r["hush"].clear)()


def stop():
    """Turn off the ringing alarm. Returns whether one was ringing."""
    r = _ringing
    if not r:
        return False
    r["stop"].set()
    r["stopped_by_owner"] = True
    if r.get("proc"):
        r["proc"].kill()  # don't wait for the buffered audio to play out
    log.info("alarm %d turned off", r["alarm"]["id"])
    return True


def snooze():
    """Turn off the ringing alarm and set it again SNOOZE_MINUTES from now."""
    r = _ringing
    if not r or not stop():
        return None
    return add(datetime.now(TZ) + timedelta(minutes=SNOOZE_MINUTES), r["alarm"]["sound"])


def _paplay():
    return subprocess.Popen(["paplay", "--raw", "--format=s16le", f"--rate={RATE}",
                             "--channels=1", "--latency-msec=150", "--volume=65536",
                             "--client-name=Jarvis alarm", "--stream-name=Alarm"],
                            stdin=subprocess.PIPE)


def _pactl(*args):
    return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=5).stdout


def _make_audible():
    """Unmute the speaker and raise it to at least ALARM_MIN_VOLUME. Returns how to put it
    back afterwards."""
    sink = "@DEFAULT_SINK@"
    try:
        muted = "yes" in _pactl("get-sink-mute", sink)
        volume = int(re.search(r"(\d+)%", _pactl("get-sink-volume", sink)).group(1))
    except (OSError, AttributeError, subprocess.SubprocessError) as e:
        log.warning("couldn't read the speaker volume: %s", e)
        return lambda: None
    if muted:
        _pactl("set-sink-mute", sink, "0")
    if volume < config.ALARM_MIN_VOLUME:
        _pactl("set-sink-volume", sink, f"{config.ALARM_MIN_VOLUME}%")

    def restore():
        if volume < config.ALARM_MIN_VOLUME:
            _pactl("set-sink-volume", sink, f"{volume}%")
        if muted:
            _pactl("set-sink-mute", sink, "1")
    return restore


def _gain(t):
    """Volume (of full) t seconds into ringing: rising evenly in decibels, which sounds like a
    steady climb, from START_GAIN to full at RAMP_SECONDS."""
    return START_GAIN ** (1 - np.minimum(t / RAMP_SECONDS, 1))


def ring(alarm):
    """Play alarm's sound, louder and louder, until stop() or RING_SECONDS. Blocks."""
    global _ringing
    from jarvis.tools import spotify
    name = alarm["sound"]
    if name not in SOUNDS:
        name = random.choice(list(SOUNDS))
    loop = sound(name)
    r = {"alarm": alarm, "stop": threading.Event(), "hush": threading.Event()}
    restore = _make_audible()
    spotify.duck(True)
    _ringing = r
    log.info("alarm %d ringing (%s)", alarm["id"], name)
    for fn in on_ring:
        fn()
    chunk = RATE // 10  # 0.1 s at a time, so hushing takes effect quickly
    written, pos = 0, 0
    try:
        while not r["stop"].is_set() and written < RING_SECONDS * RATE:
            if r.get("proc") is None or r["proc"].poll() is not None:
                r["proc"] = _paplay()  # first time, or paplay died (PipeWire restarted)
            idx = (pos + np.arange(chunk)) % len(loop)
            gain = _gain((written + np.arange(chunk)) / RATE)
            if r["hush"].is_set():
                gain = np.minimum(gain, HUSH_GAIN)
            samples = (loop[idx] * gain * 32767).astype(np.int16)
            try:
                r["proc"].stdin.write(samples.tobytes())  # blocks: paplay paces it
            except (BrokenPipeError, ValueError):
                if r["stop"].is_set():
                    break
                time.sleep(0.5)
                continue
            pos, written = (pos + chunk) % len(loop), written + chunk
    finally:
        if r.get("proc"):
            r["proc"].kill()
            r["proc"].wait()
        _ringing = None
        spotify.duck(False)
        restore()
    if not r.get("stopped_by_owner"):
        log.info("alarm %d rang %d minutes unanswered", alarm["id"], RING_SECONDS // 60)
        notify.post(f"Your {_written(alarm['at'], datetime.now(TZ))} alarm rang for "
                    f"{RING_SECONDS // 60} minutes and nobody turned it off.")


def preview(name, seconds=4.0):
    """A few seconds of a sound at a comfortable volume, to choose one."""
    loop = sound(name)
    n = int(seconds * RATE)
    samples = (np.resize(loop, n) * 0.5 * 32767).astype(np.int16)
    subprocess.run(["paplay", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1",
                    "--client-name=Jarvis alarm", "--stream-name=Alarm preview"],
                   input=samples.tobytes(), timeout=seconds + 10)


_previews = []  # sound names to play once the reply announcing them has been said/sent


def after_reply():
    """Play any sound preview the last reply promised."""
    while _previews:
        preview(_previews.pop(0))


def _scheduler():
    while True:
        now = datetime.now(TZ)
        due = [a for a in alarms() if a["at"] <= now]
        for alarm in due:
            remove({alarm["id"]})
            late = (now - alarm["at"]).total_seconds()
            if late > RING_SECONDS:  # Jarvis wasn't running at the time
                notify.post(f"I missed your {_written(alarm['at'], now)} alarm: I wasn't "
                            "running then.")
                continue
            try:
                ring(alarm)
            except Exception:
                log.exception("alarm %d failed to ring", alarm["id"])
                notify.post("Your alarm failed to ring. Sorry, sir.")
        if due:
            continue  # more may have come due while that one rang
        _wake.wait(timeout=1.0)
        _wake.clear()


def start():
    threading.Thread(target=_scheduler, name="alarms", daemon=True).start()
    if (n := len(alarms())):
        log.info("%d alarm(s) set; next at %s", n, alarms()[0]["at"])


# --- Understanding requests ---------------------------------------------------------------

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def normalize(text):
    """Lowercase words with numbers as digits: "Set an alarm for seven-thirty A.M." ->
    "set an alarm for 7 30 am"; "7.30" -> "7:30"."""
    s = text.lower().replace("’", "'")
    s = re.sub(r"\b([ap])\.?\s?m\b\.?", r" \1m", s)
    s = re.sub(r"(\d)\.(\d\d)\b", r"\1:\2", s)
    s = re.sub(r"(\d)(am|pm)\b", r"\1 \2", s)
    s = re.sub(r"o'?\s?clock", " ", s)
    s = s.replace("'", "")
    s = re.sub(r"[^a-z0-9: ]+", " ", s)
    words, out, i = s.split(), [], 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w in _TENS:
            n = _TENS[w]
            if 0 < _UNITS.get(nxt, 0) < 10:
                n, i = n + _UNITS[nxt], i + 1
            out.append(str(n))
        elif w in _UNITS:
            out.append(str(_UNITS[w]))
        elif w in ("oh", "o") and out and out[-1].isdigit() and 0 < _UNITS.get(nxt, 0) < 10:
            out.append(f"0{_UNITS[nxt]}")
            i += 1
        else:
            out.append(w)
        i += 1
    return " ".join(out)


_RELATIVE = re.compile(r"\b(?:in|for) ((?:(?:an?|half an?|\d+(?::\d+)?) (?:and a half )?"
                       r"(?:hours?|hrs?|minutes?|mins?)(?: and a half)?(?: and)? ?)+)")
_TIME = re.compile(r"(?<![\d:])(?:(?P<compact>\d{3,4})|(?P<h>\d{1,2})(?::(?P<m>\d\d)| (?P<m2>\d\d)"
                   r"(?! (?:minutes?|mins?|hours?|hrs?)\b))?)(?: (?P<ap>am|pm))?(?![\d:])")
_PAST = re.compile(r"\b(half|quarter|\d{1,2}) (?:minutes? )?(past|after|to|before) (\d{1,2})\b")


def _relative(n):
    m = _RELATIVE.search(n)
    if not m:
        return None
    minutes = 0.0
    for num, half1, unit, half2 in re.findall(
            r"(an?|half an?|\d+)(?: (and a half))? (hours?|hrs?|minutes?|mins?)( and a half)?",
            m.group(1)):
        k = 0.5 if num.startswith("half") else 1 if num in ("a", "an") else int(num)
        k += 0.5 if half1 or half2 else 0
        minutes += k * (60 if unit.startswith("h") else 1)
    return minutes or None


def _clock_time(n):
    """(hour, minute, "am"/"pm"/None) of the time in n, or None. A time after "at" or "for" is
    preferred ("the one at 7": 7, not 1)."""
    if re.search(r"\bnoon\b|\bmidday\b", n):
        return 12, 0, "pm"
    if re.search(r"\bmidnight\b", n):
        return 0, 0, None
    ap = "am" if re.search(r"\bmorning\b", n) else (
        "pm" if re.search(r"\b(?:afternoon|evening|tonight|night)\b", n) else None)
    if m := _PAST.search(n):
        amount = {"half": 30, "quarter": 15}.get(m[1]) or int(m[1])
        h = int(m[3])
        if m[2] in ("to", "before"):
            h, minute = (h - 1) % 24, 60 - amount
        else:
            minute = amount
        return (h, minute, ap) if 0 <= h < 24 and 0 <= minute < 60 else None
    candidates = [t for t in _TIME.finditer(n)]
    after = [t for t in candidates if re.search(r"\b(?:at|for|to|by)\s*$", n[:t.start()])]
    for t in after + candidates:
        if t["compact"]:
            h, minute = divmod(int(t["compact"]), 100)
        else:
            h, minute = int(t["h"]), int(t["m"] or t["m2"] or 0)
        when = t["ap"] or ap
        if when and not 1 <= h <= 12:
            continue
        if 0 <= h < 24 and 0 <= minute < 60:
            return h, minute, when
    return None


def parse_time(text, now=None):
    """When the alarm in text should go off, or None if it names no time."""
    now = now or datetime.now(TZ)
    n = normalize(text)
    if (minutes := _relative(n)) is not None:
        return (now + timedelta(minutes=minutes)).replace(microsecond=0)
    if (found := _clock_time(n)) is None:
        return None
    h, minute, ap = found
    if ap:
        hours = [h % 12 + (12 if ap == "pm" else 0)]
    elif h == 0 or h > 12:
        hours = [h]
    else:
        hours = [h % 12, h % 12 + 12]  # 7 could be 7 am or 7 pm: whichever comes first
    day = None
    if re.search(r"\btomorrow\b", n):
        day = now.date() + timedelta(days=1)
    elif m := re.search(r"\b(" + "|".join(_DAYS) + r")\b", n):
        ahead = (_DAYS.index(m[1]) - now.weekday()) % 7
        day = now.date() + timedelta(days=ahead)
    times = []
    for hour in hours:
        if day is None:
            at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            times.append(at if at > now else at + timedelta(days=1))
        else:
            at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
            if at <= now:
                at += timedelta(days=7)  # "Saturday" on a Saturday afternoon: next week
            times.append(at)
    if day is not None and len(times) == 2:
        return times[0]  # "tomorrow at 7": for an alarm, the morning
    return min(times)


def _when_words(at, now):
    from jarvis.voice import clock_words
    days = (at.date() - now.date()).days
    if days == 0:
        day = "tonight" if at.hour >= 17 else "today"
    elif days == 1:
        day = "tomorrow"
    elif days < 7:
        day = f"on {at:%A}"
    else:
        day = f"on {at:%B} {at.day}"
    return f"{clock_words(at)} {day}"


def _written(at, now):
    days = (at.date() - now.date()).days
    day = "today" if days == 0 else "tomorrow" if days == 1 else f"{at:%a %b} {at.day}"
    return f"{at:%-I:%M %p} {day}"


def _say(at, now, channel):
    return _when_words(at, now) if channel == "voice" else _written(at, now)


def _count(n, channel):
    from jarvis.voice import _number
    return _number(n) if channel == "voice" and n < 60 else str(n)


def _sound_in(n):
    for word in n.split():
        if word in _SOUND_NAMES:
            return _SOUND_NAMES[word]
    return None


def _sound_list():
    return "classic, digital, chimes, marimba and birds"


# A request to text or email someone may well mention an alarm ("tell Hyde my alarm didn't go
# off"): that's for Claude.
_MESSAGE = re.compile(r"^(?:(?:hey |ok |okay )?jarvis )?(?:can you |could you |would you |please )?"
                      r"(?:text|message|send|email|draft|reply|forward|tell|write|note|save)\b")
_CANCEL = re.compile(r"\b(?:cancel|delete|remove|clear|turn off|switch off|disable|get rid of|"
                     r"kill|unset|dont need|do not need)\b")
_QUESTION = re.compile(r"^(?:(?:hey |ok |okay )?jarvis )?(?:whats?|which|whens?|is|are|do|did|"
                       r"have|any|list|show|check|tell me|how many|read)\b")
_SET = re.compile(r"\b(?:set|make|create|add|put|new|start|wake me|get me up)\b")
_SOUND_WORDS = re.compile(r"\b(?:sounds?|tones?|ringtones?|noises?)\b")
_OFF = re.compile(r"\b(?:stop|off|cancel|enough|dismiss|awake|up|quiet|shut|silence|done)\b")

_pending = {}  # channel -> ("time", sound) or ("cancel", None): what the last reply asked for


def local_reply(text, channel="voice", now=None):
    """The reply to an alarm request, having done it; None if text isn't about alarms."""
    now = now or datetime.now(TZ)
    n = normalize(text)
    if ringing():
        if re.search(r"\bsnooze\b", n):
            again = snooze()
            return (f"Snoozing for {_count(SNOOZE_MINUTES, channel)} minutes, sir." if again
                    else "It's already off, sir.")
        if _OFF.search(n):
            return "Alarm off, sir." if stop() else None
    pending = _pending.pop(channel, None)
    about = re.search(r"\balarms?\b|\bwake me\b|\bsnooze\b", n)
    if not about and not pending:
        return None
    if about and _MESSAGE.match(n):
        return None
    if pending and not about:
        kind, extra = pending
        if kind == "time" and (at := parse_time(text, now)):
            return _set(at, extra or _sound_in(n), now, channel)
        if kind == "cancel":
            return _cancel(n, now, channel, answering=True)
        return None  # moved on to something else
    if re.search(r"\bsnooze\b", n):
        return "Nothing's ringing, sir."
    if _CANCEL.search(n) or (re.search(r"\bstop\b", n) and not _SOUND_WORDS.search(n)):
        return _cancel(n, now, channel)
    if _SOUND_WORDS.search(n) or (_sound_in(n) and not _clock_time(n) and not _relative(n)):
        if reply := _sounds(n, channel):
            return reply
    if _QUESTION.match(n):
        return _list(now, channel)
    if at := parse_time(text, now):
        return _set(at, _sound_in(n), now, channel)
    if _SET.search(n):
        _pending[channel] = ("time", _sound_in(n))
        return "For what time, sir?"
    return ("I can set, list or cancel alarms, sir. Say, for example, set an alarm for "
            "seven thirty." if channel == "voice" else
            "I can set, list or cancel alarms: e.g. `set an alarm for 7:30 tomorrow with birds`, "
            "`what alarms do I have`, `cancel my 7:30 alarm`, `alarm sounds`.")


def _set(at, sound_name, now, channel):
    if sound_name and sound_name not in SOUNDS and sound_name != "random":
        sound_name = None
    for a in alarms():
        if a["at"] == at:
            return f"You already have an alarm at {_say(at, now, channel)}, sir."
    alarm = add(at, sound_name)
    reply = f"Alarm set for {_say(at, now, channel)}"
    if sound_name:
        reply += f", with {alarm['sound']}"
    if at - now < timedelta(minutes=60) and channel == "voice":
        minutes = max(1, round((at - now).total_seconds() / 60))
        reply += f". That's in {_count(minutes, channel)} minute{'s' if minutes != 1 else ''}"
    return reply + "."


def _cancel(n, now, channel, answering=False):
    set_ = alarms()
    if not set_:
        return "You have no alarms set, sir."
    if re.search(r"\b(?:all|every|both|alarms|everything)\b", n):
        remove({a["id"] for a in set_})
        if len(set_) == 1:
            return "Alarm cancelled."
        return ("Both alarms cancelled." if len(set_) == 2
                else f"All {_count(len(set_), channel)} alarms cancelled.")
    target = parse_time(n, now) if _clock_time(n) else None
    if target is not None:
        found = [a for a in set_ if (a["at"].hour, a["at"].minute) == (target.hour, target.minute)]
        if not found:  # "the 7 alarm" may mean 7 pm as well as the 7 am it parsed as
            found = [a for a in set_ if a["at"].minute == target.minute
                     and a["at"].hour % 12 == target.hour % 12]
        if not found:
            return f"There's no alarm at that time, sir. {_list(now, channel)}"
        remove({a["id"] for a in found})
        return f"Cancelled your {_say(found[0]['at'], now, channel)} alarm."
    if len(set_) == 1:
        remove({set_[0]["id"]})
        return f"Cancelled your {_say(set_[0]['at'], now, channel)} alarm."
    if answering:
        return None
    _pending[channel] = ("cancel", None)
    return f"{_list(now, channel)} Which one, or all of them?"


def _list(now, channel):
    set_ = alarms()
    if not set_:
        return "You have no alarms set, sir."
    times = [_say(a["at"], now, channel) for a in set_]
    if len(times) == 1:
        return f"You have one alarm, at {times[0]}."
    listed = ", ".join(times[:-1]) + f" and {times[-1]}"
    return f"You have {_count(len(times), channel)} alarms: {listed}."


def _sounds(n, channel):
    name = _sound_in(n)
    if name and re.search(r"\b(?:change|switch|make|use|default|set|always)\b", n):
        if name == "random":
            set_default_sound("random")
            return "Alarms will play a different sound each time, sir."
        set_default_sound(name)
        return f"Alarms will play {name} from now on, sir."
    if name and name != "random" and re.search(r"\b(?:play|test|preview|hear|try|sounds? like|"
                                                r"what does)\b", n):
        _previews.append(name)
        return f"This is {name}."
    if re.search(r"\b(?:what|which|list|options|available|have|choose|pick|are there)\b", n):
        return (f"The alarm sounds are {_sound_list()}. Yours is {default_sound()}.")
    return None


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["--play"]:
        for name in sys.argv[2:] or SOUNDS:
            print(name)
            preview(name)
    else:
        now = datetime.now(TZ)
        text = " ".join(sys.argv[1:])
        print("normalized:", normalize(text))
        print("time:", parse_time(text, now))
