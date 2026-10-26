"""Settings loaded from ~/jarvis/.env (see .env.example)."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _device(value):
    """Audio device: empty means system default; digits mean an index; else a name substring."""
    if not value:
        return None
    return int(value) if value.isdigit() else value


CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "low")  # low/medium/high: more thinking = slower
CLAUDE_THINKING = os.getenv("CLAUDE_THINKING", "off") == "on"  # thinking before replying
# The conversation is kept whole; only past this many characters of it (about 120k tokens, to
# stay inside the context window) are the oldest exchanges dropped.
HISTORY_CHARS = int(os.getenv("HISTORY_CHARS", "480000"))
PROJECT_MODEL = os.getenv("PROJECT_MODEL", "sonnet")  # Claude Code model for project mode
# Send each Claude request twice at once and use whichever starts answering first: cuts the
# occasional 1.5-3 s wait for a reply to start, for a little more API cost.
CLAUDE_RACE = os.getenv("CLAUDE_RACE", "on") == "on"

VOICE_ENABLED = os.getenv("VOICE_ENABLED", "on") == "on"
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
_owner = os.getenv("DISCORD_OWNER_ID", "").strip()
if _owner and not _owner.isdigit():
    raise SystemExit(f"DISCORD_OWNER_ID must be your numeric Discord user ID, not {_owner!r} "
                     "(Developer Mode on, right-click your name, Copy User ID)")
DISCORD_OWNER_ID = int(_owner or 0)  # the only user the bot answers
DISCORD_CHANNEL = os.getenv("DISCORD_CHANNEL", "jarvis")  # answers everything here; alerts go here
DISCORD_MENTION = os.getenv("DISCORD_MENTION", "on") == "on"  # @mention the owner in alerts
# Socket other programs (the Claude Code hook) send alerts to; see jarvis/alerts.py.
ALERT_SOCKET = Path(os.getenv("ALERT_SOCKET") or
                    Path(os.getenv("XDG_RUNTIME_DIR") or "/tmp") / "jarvis.sock")

# Jarvis as an MCP server for Claude sessions on other devices (jarvis/mcp_server.py): the
# secret in its URL, and the local port Tailscale Funnel forwards to. No token, no server.
MCP_TOKEN = os.getenv("MCP_TOKEN", "")
MCP_PORT = int(os.getenv("MCP_PORT", "8766"))

ICLOUD_USER =os.getenv("ICLOUD_USER", "")  # Apple ID email
ICLOUD_APP_PASSWORD = os.getenv("ICLOUD_APP_PASSWORD", "")  # app-specific password, not the real one
ICLOUD_CALENDAR = os.getenv("ICLOUD_CALENDAR", "Jarvis")  # default calendar for new events
BRIEFING_HOUR = int(os.getenv("BRIEFING_HOUR", "8"))  # morning briefing posted in #jarvis
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")  # job-search sheet, from its URL
TRELLO_KEY = os.getenv("TRELLO_KEY", "")  # from trello.com/power-ups/admin
TRELLO_TOKEN = os.getenv("TRELLO_TOKEN", "")  # ATTA..., from trello.com/1/authorize
TRELLO_BOARD = os.getenv("TRELLO_BOARD", "todo")  # main todo board
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")  # from developer.spotify.com/dashboard
# Spotify device that plays out of Jarvis's own speaker (Soloist, deploy/soloist.service); music
# goes there unless the owner names another device.
SPOTIFY_SPEAKER = os.getenv("SPOTIFY_SPEAKER", "Jarvis")
ONEDRIVE_CLIENT_ID = os.getenv("ONEDRIVE_CLIENT_ID", "")  # Entra app registration (README)
# Which accounts can sign in: common (any), consumers (personal), organizations, or a
# school or work tenant such as queensu.ca
ONEDRIVE_TENANT = os.getenv("ONEDRIVE_TENANT", "common")
IMESSAGE_SSH = os.getenv("IMESSAGE_SSH", "")  # ssh host of the Mac VM running Messages (README)
SMITH_USER = os.getenv("SMITH_USER", "")  # smithlearning.ca login, so expired sessions renew
SMITH_PASSWORD = os.getenv("SMITH_PASSWORD", "")
# Kasa account, only so the switches log in again by themselves if the saved login expires;
# `python -m jarvis.tools.kasa login` works without them (README: Kasa switches)
KASA_USER = os.getenv("KASA_USER", "")
KASA_PASSWORD = os.getenv("KASA_PASSWORD", "")

PIPER_VOICE = ROOT / os.getenv("PIPER_VOICE", "models/piper/en_GB-alan-medium.onnx")
# Small Vosk model that listens only for "stop"/"cancel" while Jarvis is talking.
VOSK_MODEL = ROOT / os.getenv("VOSK_MODEL", "models/vosk-model-small-en-us-0.15")
SAVE_STOP_AUDIO = os.getenv("SAVE_STOP_AUDIO", "") == "1"  # debugging: keep what it heard in data/
TTS_LENGTH_SCALE = float(os.getenv("TTS_LENGTH_SCALE", "1.0"))  # >1 slower, <1 faster
TTS_VOLUME = float(os.getenv("TTS_VOLUME", "1.0"))
THINKING_SOUND = os.getenv("THINKING_SOUND", "on") == "on"  # soft blips while waiting on Claude
# How often a task gets "Say please." (and waits for it), unless please was said.
# The first SAY_PLEASE_TESTS times (counted in data/say-please-tests) it always does, to try it.
SAY_PLEASE_RATE = float(os.getenv("SAY_PLEASE_RATE", "0.15"))
SAY_PLEASE_TESTS = int(os.getenv("SAY_PLEASE_TESTS", "2"))
# Alarms (jarvis/alarms.py): handled without Claude.
ALARM_SOUND = os.getenv("ALARM_SOUND", "classic")  # default sound until the owner picks one
ALARM_RING_MINUTES = int(os.getenv("ALARM_RING_MINUTES", "15"))  # gives up after this long
ALARM_RAMP_MINUTES = float(os.getenv("ALARM_RAMP_MINUTES", "15"))  # quiet to full over this long
ALARM_START_VOLUME = float(os.getenv("ALARM_START_VOLUME", "10"))  # percent of full, at first
ALARM_MIN_VOLUME = int(os.getenv("ALARM_MIN_VOLUME", "60"))  # speaker raised to this if lower
ALARM_SNOOZE_MINUTES = int(os.getenv("ALARM_SNOOZE_MINUTES", "9"))
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")  # "cuda" or "cpu"
WHISPER_THREADS = int(os.getenv("WHISPER_THREADS", "4"))
WAKE_THRESHOLD = float(os.getenv("WAKE_THRESHOLD", "0.5"))  # 0-1; higher = fewer false wakes
# Homecoming (jarvis/homecoming.py): clap twice, then say "Daddy's home"; no wake word needed.
HOMECOMING = os.getenv("HOMECOMING", "on") == "on"
# A clap (jarvis/clap.py; tune with python -m jarvis.clap) peaks this many dB above the
# background and at least this loud (dBFS, 5 ms): raise them if it hears claps that weren't,
# lower them if it misses yours.
CLAP_PEAK_DB = float(os.getenv("CLAP_PEAK_DB", "15"))
CLAP_MIN_DBFS = float(os.getenv("CLAP_MIN_DBFS", "-40"))
# How high-pitched it is: 1 for hiss, 0.1 to 1 for claps, under 0.1 for a voice, a thump or a
# knock on a door
CLAP_HIGH_FREQ = float(os.getenv("CLAP_HIGH_FREQ", "0.07"))
CLAP_DECAY_SECONDS = float(os.getenv("CLAP_DECAY_SECONDS", "0.15"))  # dies away within this
CLAP_MIN_GAP = float(os.getenv("CLAP_MIN_GAP", "0.15"))  # seconds between the two claps
CLAP_MAX_GAP = float(os.getenv("CLAP_MAX_GAP", "0.9"))
SPEECH_THRESHOLD = float(os.getenv("SPEECH_THRESHOLD", "0.5"))  # Silero VAD speech probability
SILENCE_SECONDS = float(os.getenv("SILENCE_SECONDS", "0.8"))  # pause that ends an utterance
# Shorter pause that ends it when the words so far are a finished sentence ("What time is
# it?"); set it to SILENCE_SECONDS to always wait the full pause.
QUICK_SILENCE_SECONDS = float(os.getenv("QUICK_SILENCE_SECONDS", "0.5"))
FOLLOW_UP_SECONDS = float(os.getenv("FOLLOW_UP_SECONDS", "5"))  # listen again after a reply
MAX_UTTERANCE_SECONDS = float(os.getenv("MAX_UTTERANCE_SECONDS", "15"))
OUTPUT_DEVICE =_device(os.getenv("OUTPUT_DEVICE", ""))
INPUT_DEVICE = _device(os.getenv("INPUT_DEVICE", ""))
