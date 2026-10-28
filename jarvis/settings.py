"""Jarvis's settings, for the settings page in the Jarvis window (`/jarvis settings` in
jarvis-wrapper): each setting's category, kind, default and what it does, read from and
written to .env. Do not disturb is here too, kept in data/dnd.json (jarvis/tools/dnd.py).

Secrets (keys, tokens, passwords) are only ever reported as set or not: their values are
never printed, and they can't be changed from here (edit .env).

    python -m jarvis.settings list              every setting as JSON, with its value now
    python -m jarvis.settings set KEY VALUE     change one ("" or "default" for the default)
    python -m jarvis.settings restart           restart Jarvis, so .env changes take effect

.env changes take effect when Jarvis restarts; do not disturb at once. Doesn't import
jarvis.config, so a broken .env can still be fixed from here.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
DND = ROOT / "data" / "dnd.json"

CATEGORIES = ["Claude", "Voice", "Listening", "Speech recognition", "Do not disturb", "Alarms",
              "Homecoming", "Phone", "Discord", "Calendar & briefing", "Services", "Accounts & keys"]


def S(key, category, label, kind, default, about, choices=None, secret=False):
    return {"key": key, "category": category, "label": label, "kind": kind, "default": default,
            "about": about, "choices": choices, "secret": secret}


SETTINGS = [
    # Claude
    S("CLAUDE_MODEL", "Claude", "Model", "choice", "claude-haiku-5-5",
      "The Claude model that answers you", ["claude-haiku-5-5", "claude-sonnet-5-5",
                                            "claude-opus-5-5", "claude-fable-5-1"]),
    S("CLAUDE_EFFORT", "Claude", "Effort", "choice", "low",
      "How hard Claude thinks before answering: higher is slower", ["low", "medium", "high"]),
    S("CLAUDE_THINKING", "Claude", "Thinking", "bool", "off",
      "Let Claude think first: slower, rarely better for this"),
    S("CLAUDE_RACE", "Claude", "Race two requests", "bool", "on",
      "Send each request twice and use whichever answers first: fewer slow starts, a little more cost"),
    S("HISTORY_CHARS", "Claude", "Conversation memory (characters)", "int", "480000",
      "Conversations are kept whole up to this size (about 120k tokens), then the oldest quarter goes"),
    S("PROJECT_MODEL", "Claude", "Project mode model", "choice", "sonnet",
      "Claude Code model for project mode (opus uses up the plan faster)",
      ["haiku", "sonnet", "opus", "fable"]),
    # Voice
    S("VOICE_ENABLED", "Voice", "Voice", "bool", "on",
      "Listen for \"Hey Jarvis\" and talk; off leaves only Discord and the Claude connection"),
    S("TTS_LENGTH_SCALE", "Voice", "Speech speed", "float", "1.0",
      "How slowly Jarvis talks: over 1 is slower, under 1 faster"),
    S("TTS_VOLUME", "Voice", "Speech volume", "float", "1.0", "Loudness of Jarvis's voice (1 = normal)"),
    S("THINKING_SOUND", "Voice", "Thinking sound", "bool", "on",
      "Soft blips while waiting on Claude or a tool"),
    S("SAY_PLEASE_RATE", "Voice", "\"Say please\" rate", "float", "0.15",
      "How often a task gets \"Say please.\" (0 to 1; 0 never)"),
    S("PIPER_VOICE", "Voice", "Voice model", "text", "models/piper/en_GB-alan-medium.onnx",
      "Piper voice file, from ~/jarvis"),
    S("OUTPUT_DEVICE", "Voice", "Speaker", "text", "",
      "Speaker index or part of its name; empty for the system default (python -m sounddevice lists them)"),
    # Listening
    S("WAKE_THRESHOLD", "Listening", "Wake word sensitivity", "float", "0.5",
      "0 to 1: raise if it wakes by itself, lower if it misses you"),
    S("SILENCE_SECONDS", "Listening", "End-of-speech pause", "float", "0.8",
      "Seconds of silence that end what you're saying"),
    S("QUICK_SILENCE_SECONDS", "Listening", "Quick pause", "float", "0.5",
      "Shorter pause that ends it when it's already a finished sentence"),
    S("FOLLOW_UP_SECONDS", "Listening", "Follow-up window", "float", "5",
      "Seconds it listens for a follow-up after replying, without the wake word"),
    S("MAX_UTTERANCE_SECONDS", "Listening", "Longest request", "float", "15",
      "Seconds of talking before it stops listening"),
    S("SPEECH_THRESHOLD", "Listening", "Speech detection", "float", "0.5",
      "Silero speech probability that counts as talking (0 to 1)"),
    S("INPUT_DEVICE", "Listening", "Microphone", "text", "",
      "Mic index or part of its name; empty for the system default"),
    # Speech recognition
    S("WHISPER_MODEL", "Speech recognition", "Whisper model", "choice", "large-v3-turbo",
      "Bigger hears better; tiny.en / base.en / small.en for CPU",
      ["large-v3-turbo", "large-v3", "medium.en", "small.en", "base.en", "tiny.en"]),
    S("WHISPER_DEVICE", "Speech recognition", "Whisper device", "choice", "cuda",
      "cuda is the GPU; cpu uses int8 and the thread count", ["cuda", "cpu"]),
    S("WHISPER_THREADS", "Speech recognition", "CPU threads", "int", "4", "Threads for Whisper on CPU"),
    # Do not disturb (data/dnd.json, not .env)
    S("DND", "Do not disturb", "Do not disturb", "bool", "off",
      "No spoken alerts: timers, updates and reminders go to Discord, alarms still ring, and "
      "Hey Jarvis is still answered out loud. Applies at once"),
    # Alarms
    S("ALARM_SOUND", "Alarms", "Default sound", "choice", "classic",
      "Sound for alarms that don't name one", ["classic", "digital", "chimes", "marimba", "birds"]),
    S("ALARM_RING_MINUTES", "Alarms", "Ring for (minutes)", "int", "15", "Gives up after this long"),
    S("ALARM_RAMP_MINUTES", "Alarms", "Get louder over (minutes)", "float", "15",
      "From the start volume to full over this long"),
    S("ALARM_START_VOLUME", "Alarms", "Start volume (%)", "float", "10", "Percent of full, at first"),
    S("ALARM_MIN_VOLUME", "Alarms", "Speaker at least (%)", "int", "60",
      "The speaker is raised to this if it's lower"),
    S("ALARM_SNOOZE_MINUTES", "Alarms", "Snooze (minutes)", "int", "9", "How long a snooze lasts"),
    S("ALARM_BEFORE_FIRST_EVENT", "Alarms", "Before the first event (minutes)", "int", "30",
      "Jarvis warns if a morning alarm is closer than this to the day's first event (not classes)"),
    # Homecoming
    S("HOMECOMING", "Homecoming", "Homecoming", "bool", "on",
      "Clap twice, then \"Daddy's home\": lights, a greeting and an update"),
    S("CLAP_PEAK_DB", "Homecoming", "Clap above background (dB)", "float", "15",
      "Raise if it hears claps that weren't, lower if it misses yours"),
    S("CLAP_MIN_DBFS", "Homecoming", "Clap loudness (dBFS)", "float", "-40",
      "How loud a clap must be at least"),
    S("CLAP_HIGH_FREQ", "Homecoming", "Clap pitch", "float", "0.07",
      "Share of high frequencies: claps are 0.1 to 1, voices and knocks under 0.1"),
    S("CLAP_DECAY_SECONDS", "Homecoming", "Clap dies away within (s)", "float", "0.15",
      "Longer sounds aren't claps"),
    S("CLAP_MIN_GAP", "Homecoming", "Shortest gap (s)", "float", "0.15", "Between the two claps"),
    S("CLAP_MAX_GAP", "Homecoming", "Longest gap (s)", "float", "0.9", "Between the two claps"),
    # Phone
    S("PHONE_WARN_AT", "Phone", "Battery alerts at (%)", "text", "20,10",
      "Percentages to warn at while the phone isn't charging, comma separated"),
    S("FINDMY_PHONE", "Phone", "Phone name in Find My", "text", "",
      "Empty for your own iPhone"),
    # Discord
    S("DISCORD_CHANNEL", "Discord", "Channel", "text", "jarvis",
      "Jarvis answers everything here and posts alerts here"),
    S("DISCORD_MENTION", "Discord", "@mention in alerts", "bool", "on", "Ping you in alerts"),
    # Calendar & briefing
    S("BRIEFING_TIME", "Calendar & briefing", "Morning briefing at", "text", "7:45",
      "When the briefing is prepared and posted (H:MM), unless an alarm makes it earlier"),
    S("BRIEFING_BEFORE_ALARM", "Calendar & briefing", "Before a morning alarm (minutes)", "int",
      "15", "Prepared this long before a morning alarm, if that's before the time above"),
    S("TODO_REMIND_TIMES", "Calendar & briefing", "Todo reminders at", "text", "9:00,17:00",
      "Due Trello todos are reminded at these times (H:MM, comma separated) until they're done"),
    S("ICLOUD_CALENDAR", "Calendar & briefing", "Default calendar", "text", "Jarvis",
      "Apple calendar new events go in"),
    # Services
    S("TRELLO_BOARD", "Services", "Trello board", "text", "todo", "Main todo board"),
    S("SPOTIFY_SPEAKER", "Services", "Spotify speaker", "text", "Jarvis",
      "Spotify device music plays on unless you name another"),
    S("ONEDRIVE_TENANT", "Services", "OneDrive accounts", "text", "common",
      "common, consumers, organizations, or a tenant such as queensu.ca"),
    S("IMESSAGE_SSH", "Services", "Mac VM ssh host", "text", "", "Where Messages and Find My run"),
    S("GOOGLE_SHEET_ID", "Services", "Job-search sheet", "text", "", "Google Sheet id, from its URL"),
    S("MCP_PORT", "Services", "MCP port", "int", "8766", "Local port Tailscale Funnel forwards to"),
] + [S(key, "Accounts & keys", label, "secret", "", "Change it in ~/jarvis/.env", secret=True)
     for key, label in [("ANTHROPIC_API_KEY", "Anthropic API key"), ("DISCORD_TOKEN", "Discord bot token"),
                        ("DISCORD_OWNER_ID", "Discord owner ID"), ("MCP_TOKEN", "MCP token"),
                        ("ICLOUD_USER", "iCloud user"), ("ICLOUD_APP_PASSWORD", "iCloud app password"),
                        ("TRELLO_KEY", "Trello key"), ("TRELLO_TOKEN", "Trello token"),
                        ("SPOTIFY_CLIENT_ID", "Spotify client ID"), ("ONEDRIVE_CLIENT_ID", "OneDrive client ID"),
                        ("SMITH_USER", "Smith Learning user"), ("SMITH_PASSWORD", "Smith Learning password"),
                        ("KASA_USER", "Kasa user"), ("KASA_PASSWORD", "Kasa password")]]
BY_KEY = {s["key"]: s for s in SETTINGS}

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _unquote(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return re.sub(r"\s+#.*$", "", raw)  # an unquoted value's trailing comment


def env_values():
    """KEY -> value as written in .env (the last one wins, as with python-dotenv)."""
    values = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            m = _LINE.match(line)
            if m:
                values[m[1]] = _unquote(m[2])
    return values


def _dnd():
    try:
        state = json.loads(DND.read_text())
    except (OSError, ValueError):
        return "off", None
    until = state.get("until")
    if state.get("on") and until and datetime.fromisoformat(until) <= datetime.now().astimezone():
        return "off", None
    return ("on" if state.get("on") else "off"), until


def listing():
    values = env_values()
    out = []
    for s in SETTINGS:
        item = dict(s)
        if s["key"] == "DND":
            item["value"], until = _dnd()
            item["note"] = f"until {datetime.fromisoformat(until):%-I:%M %p}" if until else None
        elif s["secret"]:
            item["value"], item["set"] = None, bool(values.get(s["key"]))
        else:
            item["value"] = values.get(s["key"])  # None: not in .env, so the default
        out.append(item)
    return {"categories": CATEGORIES, "settings": out, "env": str(ENV)}


def _check(s, value):
    """value as it should be written, or raises ValueError."""
    kind = s["kind"]
    if kind == "bool":
        v = value.strip().lower()
        if v in ("on", "true", "yes", "1"):
            return "on"
        if v in ("off", "false", "no", "0"):
            return "off"
        raise ValueError(f"{s['label']} is on or off")
    if kind in ("int", "float"):
        try:
            return str(int(value.strip()) if kind == "int" else float(value.strip()))
        except ValueError:
            raise ValueError(f"{s['label']} must be a {'whole ' if kind == 'int' else ''}number") from None
    if kind == "choice" and s["choices"] and value.strip() not in s["choices"]:
        # Other model ids and Whisper models work too: allow anything without spaces.
        if not re.fullmatch(r"[\w.\-/]+", value.strip()):
            raise ValueError(f"{s['label']}: one of {', '.join(s['choices'])}")
    if s["key"] == "BRIEFING_TIME":
        m = re.fullmatch(r"(\d{1,2}):(\d\d)", value.strip())
        if not m or int(m[1]) > 23 or int(m[2]) > 59:
            raise ValueError(f"{s['label']}: a time like 7:45 (24-hour)")
    if s["key"] == "TODO_REMIND_TIMES":
        times = [t.strip() for t in value.split(",") if t.strip()]
        if not times or not all(re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", t) for t in times):
            raise ValueError(f"{s['label']}: times like 9:00,17:00 (24-hour)")
    if "\n" in value:
        raise ValueError("One line only")
    return value.strip()


def _write_env(key, value):
    """Set key in .env (None removes it, so the default applies), keeping every other line."""
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    out, done = [], False
    text = None if value is None else (f'"{value}"' if re.search(r"[\s#'\"]", value) else value)
    for line in lines:
        m = _LINE.match(line)
        if m and m[1] == key:
            if not done and text is not None:
                out.append(f"{key}={text}")
            done = True
            continue
        out.append(line)
    if not done and text is not None:
        out.append(f"{key}={text}")
    tmp = ENV.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n")
    os.chmod(tmp, ENV.stat().st_mode & 0o777 if ENV.exists() else 0o600)
    os.replace(tmp, ENV)


def set_setting(key, value):
    """Change one setting; returns what to tell the owner. value "" or "default" resets it."""
    s = BY_KEY.get(key)
    if s is None:
        raise ValueError(f"No setting called {key}")
    if s["secret"]:
        raise ValueError(f"{s['label']} can only be changed in {ENV}")
    reset = value.strip().lower() in ("", "default")
    if key == "DND":
        on = False if reset else _check(s, value) == "on"
        DND.parent.mkdir(exist_ok=True)
        tmp = DND.with_suffix(".tmp")
        tmp.write_text(json.dumps({"on": on, "until": None}))  # the format jarvis/tools/dnd.py keeps
        os.replace(tmp, DND)
        return {"message": f"Do not disturb is {'on' if on else 'off'}.", "restart": False}
    _write_env(key, None if reset else _check(s, value))
    shown = s["default"] if reset else _check(s, value)
    return {"message": f"{s['label']}: {shown or '(empty)'}{' (default)' if reset else ''}. "
                       "Restart Jarvis to apply.", "restart": True}


def restart():
    r = subprocess.run(["systemctl", "--user", "restart", "jarvis"], capture_output=True, text=True,
                       timeout=60)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"exit {r.returncode}")
    return {"message": "Jarvis restarted with the new settings.", "restart": False}


def main(args):
    try:
        if args[:1] == ["list"]:
            out = listing()
        elif args[:1] == ["set"] and len(args) == 3:
            out = set_setting(args[1], args[2])
        elif args[:1] == ["restart"]:
            out = restart()
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
