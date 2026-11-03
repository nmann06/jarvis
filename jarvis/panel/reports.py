"""Reports (the Improve page), what they suggest, and the numbers on the Stats page.

Suggestions come from what the hooks recorded when the button was pressed, and only ever name
settings that are in jarvis.settings. Most things that go wrong with stop detection have no
setting: they get advice, with key and value None.
"""

import json
import os
import re
import shutil
import statistics
import time
import wave
from datetime import date, datetime, timedelta

from jarvis import events, settings

DATA = settings.ROOT / "data"

KINDS = ("stopped_unprompted", "missed_wake", "false_wake", "misheard", "missed_stop",
         "wrong_answer")
STATUSES = ("open", "fixed", "dismissed")
_ID = re.compile(r"\d{8}-\d{6}-[a-z_]+(?:-\d+)?")

WAKE_FLOOR = 0.2  # lower than this, everything wakes it
WAKE_CEILING = 0.9  # higher than this, it stops hearing the owner too
UNDER_PEAK = 0.03
OVER_SCORE = 0.05
QUIET_MIC = 0.02  # a loudest sample under this in 30 s is a mic hearing almost nothing
RECENT_SECONDS = 120  # how far back a wake or a stop can be what the button is about
LAST_TURN_SECONDS = 300
MAX_UTTERANCE_LIMIT = 30.0
SMALL_WHISPER = ("medium.en", "small.en", "base.en", "tiny.en")
BIG_WHISPER = "large-v3-turbo"

NOT_LIVE = ("The voice hooks weren't live when this was pressed (Jarvis needs a restart, voice is "
            "off, or it's down), so there's no audio, wake scores or stop detection results.")


# Secrets

_SECRET_NAME = re.compile(r"KEY|TOKEN|PASSWORD|SECRET", re.I)
_masks = (None, ())


def _secret_values():
    """The values to mask, longest first; read again when .env changes."""
    global _masks
    try:
        stamp = (settings.ENV, settings.ENV.stat().st_mtime_ns)
    except OSError:
        return ()
    if _masks[0] != stamp:
        keys = {s["key"] for s in settings.SETTINGS if s["secret"]}
        values = {v for k, v in settings.env_values().items()
                  if (k in keys or _SECRET_NAME.search(k)) and len(v) >= 4}
        _masks = (stamp, tuple(sorted(values, key=len, reverse=True)))
    return _masks[1]


def mask(text):
    """text with the current value of every secret setting blanked out."""
    for value in _secret_values():
        text = text.replace(value, "•••")
    return text


def _masked(item):
    if isinstance(item, str):
        return mask(item)
    if isinstance(item, list):
        return [_masked(i) for i in item]
    if isinstance(item, dict):
        return {k: _masked(v) for k, v in item.items()}
    return item


# What was going on

def _setting_now(key):
    value = settings.env_values().get(key)
    return value if value not in (None, "") else settings.BY_KEY[key]["default"]


def _threshold_now():
    try:
        return float(_setting_now("WAKE_THRESHOLD"))
    except ValueError:
        return float(settings.BY_KEY["WAKE_THRESHOLD"]["default"])


def _last_line(path, wanted=lambda row: True):
    """The last JSON row of a log wanted() likes, looking only at the end of the file."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 262144))
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue  # the first line, cut in half by the seek
        if isinstance(row, dict) and wanted(row):
            return row
    return None


def _age(stamp, now):
    """Seconds since an ISO time from the logs, or None."""
    try:
        return now - datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return None


def build_context(hooks, now):
    """A report's context, from jarvis.events.context() (None: the hooks weren't live)."""
    hooks = hooks or {}
    live = bool(hooks)
    threshold = hooks.get("threshold")
    context = {"hooks_live": live, "peak_wake_score": hooks.get("peak_wake_score"),
               "threshold": _threshold_now() if threshold is None else threshold,
               "states": hooks.get("states", []), "stop_events": hooks.get("stop_events", []),
               "wake_scores": hooks.get("wake_scores", []), "wakes": hooks.get("wakes", []),
               "mic_peak": hooks.get("mic_peak"), "muted": hooks.get("muted", False),
               "last_exchange": _last_line(DATA / "exchanges.jsonl",
                                           lambda row: row.get("channel") == "voice") or {},
               "last_turn": _last_turn(now)}
    if not live:
        context["note"] = NOT_LIVE
    return _masked(context)


def _last_turn(now):
    row = _last_line(DATA / "latency.jsonl")
    if not row:
        return {}
    keys = ("time", "heard", "reply", "outcome", "endpoint", "audio_s", "follow_up", "total_s")
    return {**{k: row.get(k) for k in keys}, "age_s": _age(row.get("time"), now)}


# Suggestions

def _advice(text):
    return {"text": text, "key": None, "value": None}


def _setting(text, key, value):
    """A suggestion that changes a setting, which must be a real one."""
    spec = settings.BY_KEY.get(key)
    if spec is None or spec["secret"]:
        return _advice(text)
    return {"text": text, "key": key, "value": value}


def _recent(items, now):
    return [i for i in items if now - i.get("t", 0) <= RECENT_SECONDS]


def _quoted(text):
    return f'"{text}"' if text else "nothing"


def _raise_threshold(score, threshold, what):
    """Advice for a wake that shouldn't have fired at score."""
    if score >= WAKE_CEILING:
        return _advice(f"{what} scored {score:.2f}: the model was sure, and sensitivity can't go "
                       f"above {WAKE_CEILING} without missing you too. No setting will stop this "
                       "one; check what was playing or being said at the time in the audio.")
    value = min(round(score + OVER_SCORE, 2), WAKE_CEILING)
    if value <= threshold:
        return _advice(f"{what} scored {score:.2f}, and it needs {threshold:g} now, so that's "
                       "already been raised past it.")
    return _setting(f"{what} scored {score:.2f} and needs {threshold:g}. Raise sensitivity to "
                    f"{value:.2f}?", "WAKE_THRESHOLD", f"{value:.2f}")


def _missed_wake(ctx, now):
    peak, threshold = ctx["peak_wake_score"], ctx["threshold"]
    if ctx["muted"]:
        return _advice("The mic was muted, so Jarvis couldn't hear you. Unmute it on the Home page.")
    wakes = [w for w in ctx["wakes"] if now - w["t"] <= events.BUFFER_SECONDS]
    if wakes:
        wake = wakes[-1]
        if wake["ignored"]:
            return _advice(f"The wake word did fire (score {wake['score']:.2f}), but Jarvis "
                           f"ignored it: {wake['ignored']}.")
        return _advice(f"The wake word did fire in the last {events.BUFFER_SECONDS} s (score "
                       f"{wake['score']:.2f}), so it should have chimed. If it didn't, check the "
                       "speaker.")
    if peak is None:
        return None
    if peak >= threshold:
        return _advice(f"The wake word scored {peak:.2f} and needs {threshold:g}, which should "
                       "have been enough. If that's since changed, restart Jarvis to apply it.")
    if peak < WAKE_FLOOR:
        loudest = ctx["mic_peak"]
        if loudest is not None and loudest < QUIET_MIC:
            return _advice(f"The mic barely heard anything in the last {events.BUFFER_SECONDS} s "
                           f"(loudest {loudest:.1%} of full scale). Check the Microphone setting, "
                           "that the mic is on and its input level.")
        return _advice(f"The wake word only scored {peak:.2f}, too low for sensitivity to help: "
                       "it wasn't recognised at all. Listen to the audio: try speaking closer to "
                       "the mic, with less noise or music in the room.")
    value = max(round(peak - UNDER_PEAK, 2), WAKE_FLOOR)
    return _setting(f"Your wake word scored {peak:.2f} and needs {threshold:g}. Lower sensitivity "
                    f"to {value:.2f}?", "WAKE_THRESHOLD", f"{value:.2f}")


def _false_wake(ctx, now):
    wakes = _recent(ctx["wakes"], now)
    if not wakes:
        return _advice(f"The wake word hasn't fired in the last {RECENT_SECONDS // 60} minutes. If "
                       "Jarvis started by itself, it was something else: a silent listener, or "
                       "a project or alert with something to say.")
    return _raise_threshold(wakes[-1]["score"], ctx["threshold"], "The wake word")


_NO_SETTING = "No setting governs this check."


def _stopped_unprompted(ctx, now):
    stops = [s for s in _recent(ctx["stop_events"], now)
             if s["decision"] in ("stopped", "wake_stopped")]
    if not stops:
        return _advice(f"Jarvis recorded no stop in the last {RECENT_SECONDS // 60} minutes, so "
                       "it didn't think it heard one. The reply may simply have ended, or Claude "
                       "may have failed partway: check the Logs page.")
    stop = stops[-1]
    if stop["decision"] == "wake_stopped":
        return _raise_threshold(stop["wake_score"], ctx["threshold"], "It heard \"Hey Jarvis\" "
                                "over its own voice and stopped to listen. That")
    vosk, whisper = stop["vosk"], stop["whisper"]
    if not whisper or not whisper.strip():
        why = ("Whisper, checking for echo, heard nothing there, and with no words to call its "
               "own, Jarvis takes that as a stop.")
    else:
        why = f"Whisper, checking for echo, heard {_quoted(whisper.strip())}"
        why += (", which has a stop word in it." if re.search(
            r"\b(stop|cancel|quiet|enough|forget|nevermind)\b", whisper, re.I)
            else ", which wasn't mostly Jarvis's own words.")
    return _advice(f"The quick listener (Vosk) heard {_quoted(vosk)}. {why} {_NO_SETTING} If that "
                   "was Jarvis's own voice, check echo cancel on the Setup page or turn the "
                   "speaker down; if it was the room, listen to the audio to see what set it off.")


def _missed_stop(ctx, now):
    if ctx["muted"]:
        return _advice("The mic was muted, so Jarvis couldn't hear you say stop.")
    stops = _recent(ctx["stop_events"], now)
    # What it decided about a stop phrase says more than the words it heard around that.
    decided = [s for s in stops if s["decision"] in ("echo", "own_words")]
    if decided:
        stop = decided[-1]
        if stop["decision"] == "echo":
            return _advice(f"Vosk heard {_quoted(stop['vosk'])}, but Whisper heard "
                           f"{_quoted(stop['whisper'])} there, mostly Jarvis's own words, so it "
                           f"was taken for echo. {_NO_SETTING} Saying it louder, or a quieter "
                           "speaker, helps Whisper hear you over Jarvis.")
        return _advice(f"Vosk heard {_quoted(stop['vosk'])}, but Jarvis was saying that word "
                       f"itself at the time, so it was taken for echo. {_NO_SETTING}")
    heard = [s for s in stops if s["decision"] == "not_a_stop"]
    if heard:
        return _advice(f"The last thing Vosk heard was {_quoted(heard[-1]['vosk'])}, which isn't "
                       f"a stop phrase. {_NO_SETTING} \"Stop\", \"cancel\" and \"be quiet\" work "
                       "best; listen to the audio to see how clearly it came through.")
    return _advice(f"Vosk heard nothing at all in the last {RECENT_SECONDS // 60} minutes. Listen "
                   "to the audio: if you can hear yourself there, Jarvis's voice may be drowning "
                   "you out; if you can't, check the mic.")


def _misheard(ctx):
    age = ctx["last_turn"].get("age_s")
    turn = ctx["last_turn"] if age is not None and age <= LAST_TURN_SECONDS else {}
    heard = turn.get("heard") or ctx["last_exchange"].get("heard")
    model = _setting_now("WHISPER_MODEL")
    said = f"Whisper last heard {_quoted(heard)}." if heard else "Nothing was heard recently."
    if turn.get("endpoint") == "max":
        now_max = _float_setting("MAX_UTTERANCE_SECONDS")
        if now_max < MAX_UTTERANCE_LIMIT:
            value = min(now_max + 5, MAX_UTTERANCE_LIMIT)
            return _setting(f"{said} It stopped listening at the longest request limit of "
                            f"{now_max:g} s, which may have cut you off. Raise it to {value:g} s?",
                            "MAX_UTTERANCE_SECONDS", f"{value:g}")
    if model in SMALL_WHISPER:
        return _setting(f"{said} Whisper is on {model}, a smaller model. {BIG_WHISPER} hears "
                        f"better. Switch to it (needs the GPU)?", "WHISPER_MODEL", BIG_WHISPER)
    if turn.get("endpoint") == "quick":
        return _advice(f"{said} It stopped listening at the short pause after a finished-sounding "
                       "sentence; if that cut you off, raise Quick pause on the Settings page.")
    if not heard:
        return None
    return _advice(f"{said} Whisper is already on {model}, so listen to the audio: noise or "
                   "distance from the mic are the usual causes.")


def _float_setting(key):
    try:
        return float(_setting_now(key))
    except ValueError:
        return float(settings.BY_KEY[key]["default"])


def _wrong_answer(ctx):
    exchange = ctx["last_exchange"]
    if not exchange.get("heard"):
        return None
    reply = exchange.get("said") or exchange.get("reply") or ""
    tools = exchange.get("tools")
    used = f" It used {', '.join(map(str, tools))}." if tools else ""
    return _advice(f"It heard {_quoted(exchange['heard'])} and answered {_quoted(reply[:300])}."
                   f"{used} If that isn't what you said, it was a mishearing. Otherwise no setting "
                   "covers this; the note on this report is the record of it.")


def suggest(kind, ctx, now):
    """A report's suggestion, from its context. None when there's nothing to go on."""
    if kind in ("missed_wake", "false_wake", "stopped_unprompted", "missed_stop"):
        if not ctx["hooks_live"]:
            return None
        return {"missed_wake": _missed_wake, "false_wake": _false_wake,
                "stopped_unprompted": _stopped_unprompted,
                "missed_stop": _missed_stop}[kind](ctx, now)
    return _misheard(ctx) if kind == "misheard" else _wrong_answer(ctx)


# Saved reports

def _dir():
    return DATA / "reports"


def _path(report_id):
    """A report's directory, or None if there's no such report."""
    if not _ID.fullmatch(report_id):
        return None
    path = _dir() / report_id
    return path if (path / "meta.json").is_file() else None


def _new_dir(kind, now):
    stem = f"{datetime.fromtimestamp(now):%Y%m%d-%H%M%S}-{kind}"
    _dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    for n in range(1, 100):  # two presses in a second
        path = _dir() / (stem if n == 1 else f"{stem}-{n}")
        try:
            path.mkdir(mode=0o700)
            return path
        except FileExistsError:
            continue
    raise OSError("too many reports this second")


def _write(path, report):
    tmp = path / "meta.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path / "meta.json")


def create(kind, note, snapshot):
    """Save a report, with snapshot from the hooks: (context, PCM bytes), or None if not live."""
    now = time.time()
    hooks, pcm = snapshot or (None, b"")
    ctx = build_context(hooks, now)
    path = _new_dir(kind, now)
    if pcm:
        fd = os.open(path / "audio.wav", os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "wb") as f, wave.open(f, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(events.SAMPLE_RATE)
            w.writeframes(pcm)
    report = {"id": path.name, "kind": kind, "t": now, "note": mask(note), "has_audio": bool(pcm),
              "context": ctx, "suggestion": suggest(kind, ctx, now), "status": "open"}
    _write(path, report)
    return report


def load(report_id):
    """A saved report, or None."""
    path = _path(report_id)
    if path is None:
        return None
    try:
        return json.loads((path / "meta.json").read_text())
    except (OSError, ValueError):
        return None


def listing():
    """Every report, newest first."""
    try:
        ids = [p.name for p in _dir().iterdir()]
    except OSError:
        return []
    reports = [r for r in map(load, ids) if r]
    return sorted(reports, key=lambda r: r["t"], reverse=True)


def audio_path(report_id):
    path = _path(report_id)
    return path / "audio.wav" if path and (path / "audio.wav").is_file() else None


def set_status(report_id, status):
    report = load(report_id)
    if report is None:
        return None
    report["status"] = status
    _write(_path(report_id), report)
    return report


def delete(report_id):
    """Delete a report and its audio; False if there's no such report."""
    path = _path(report_id)
    if path is None:
        return False
    shutil.rmtree(path)
    return True


def apply(report_id):
    """Apply a report's suggestion. None if no such report; ValueError if it changes nothing."""
    report = load(report_id)
    if report is None:
        return None
    suggestion = report.get("suggestion") or {}
    if not suggestion.get("key"):
        raise ValueError("This report's suggestion is advice: there's no setting to change.")
    return settings.set_setting(suggestion["key"], suggestion["value"])


# Stats

def _percentile(values, q):
    """The q-th percentile in ms, or None with no values."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0] * 1000)
    return round(statistics.quantiles(ordered, n=100, method="inclusive")[q - 1] * 1000)


def stats(days):
    """Day by day for the last days days, oldest first, and the sum of them."""
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    rows = {d: {"date": d, "wakes": 0, "turns": 0, "reports": {}} for d in dates}
    totals = {d: [] for d in dates}
    try:
        lines = (DATA / "latency.jsonl").read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            turn = json.loads(line)
            day = turn["time"][:10]
        except (ValueError, KeyError, TypeError):
            continue
        if day not in rows:
            continue
        rows[day]["turns"] += 1
        rows[day]["wakes"] += not turn.get("follow_up")  # a follow-up needed no wake word
        if turn.get("total_s") is not None:
            totals[day].append(turn["total_s"])
    for report in listing():
        day = datetime.fromtimestamp(report["t"]).date().isoformat()
        if day in rows:
            kinds = rows[day]["reports"]
            kinds[report["kind"]] = kinds.get(report["kind"], 0) + 1
    for d, row in rows.items():
        row["latency_ms_p50"], row["latency_ms_p90"] = (_percentile(totals[d], 50),
                                                        _percentile(totals[d], 90))
    every = [s for d in dates for s in totals[d]]
    return {"days": list(rows.values()),
            "summary": {"wakes": sum(r["wakes"] for r in rows.values()),
                        "turns": sum(r["turns"] for r in rows.values()),
                        "latency_ms_p50": _percentile(every, 50),
                        "latency_ms_p90": _percentile(every, 90)}}
