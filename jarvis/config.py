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
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "300"))  # caps spoken reply length and cost
MAX_TOKENS_DISCORD = int(os.getenv("MAX_TOKENS_DISCORD", "2000"))  # text replies can be longer
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "low")  # low/medium/high: more thinking = slower
CLAUDE_THINKING = os.getenv("CLAUDE_THINKING", "off") == "on"  # thinking before replying
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "10"))  # past exchanges sent with each request

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
ICLOUD_CALENDAR = os.getenv("ICLOUD_CALENDAR", "Home")  # default calendar for new events
BRIEFING_HOUR = int(os.getenv("BRIEFING_HOUR", "8"))  # morning briefing posted in #jarvis
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")  # job-search sheet, from its URL
TRELLO_KEY = os.getenv("TRELLO_KEY", "")  # from trello.com/power-ups/admin
TRELLO_TOKEN = os.getenv("TRELLO_TOKEN", "")  # ATTA..., from trello.com/1/authorize
TRELLO_BOARD = os.getenv("TRELLO_BOARD", "todo")  # main todo board
SMITH_USER = os.getenv("SMITH_USER", "")  # smithlearning.ca login, so expired sessions renew
SMITH_PASSWORD = os.getenv("SMITH_PASSWORD", "")

PIPER_VOICE = ROOT / os.getenv("PIPER_VOICE", "models/piper/en_GB-alan-medium.onnx")
# Small Vosk model that listens only for "stop"/"cancel" while Jarvis is talking.
VOSK_MODEL = ROOT / os.getenv("VOSK_MODEL", "models/vosk-model-small-en-us-0.15")
SAVE_STOP_AUDIO = os.getenv("SAVE_STOP_AUDIO", "") == "1"  # debugging: keep what it heard in data/
TTS_LENGTH_SCALE = float(os.getenv("TTS_LENGTH_SCALE", "1.0"))  # >1 slower, <1 faster
TTS_VOLUME = float(os.getenv("TTS_VOLUME", "1.0"))
THINKING_SOUND = os.getenv("THINKING_SOUND", "on") == "on"  # soft blips while waiting on Claude
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")  # "cuda" or "cpu"
WHISPER_THREADS = int(os.getenv("WHISPER_THREADS", "4"))
WAKE_THRESHOLD = float(os.getenv("WAKE_THRESHOLD", "0.5"))  # 0-1; higher = fewer false wakes
SPEECH_THRESHOLD = float(os.getenv("SPEECH_THRESHOLD", "0.5"))  # Silero VAD speech probability
SILENCE_SECONDS = float(os.getenv("SILENCE_SECONDS", "0.8"))  # pause that ends an utterance
# Shorter pause that ends it when the words so far are a finished sentence ("What time is
# it?"); set it to SILENCE_SECONDS to always wait the full pause.
QUICK_SILENCE_SECONDS = float(os.getenv("QUICK_SILENCE_SECONDS", "0.5"))
FOLLOW_UP_SECONDS = float(os.getenv("FOLLOW_UP_SECONDS", "5"))  # listen again after a reply
MAX_UTTERANCE_SECONDS = float(os.getenv("MAX_UTTERANCE_SECONDS", "15"))
OUTPUT_DEVICE =_device(os.getenv("OUTPUT_DEVICE", ""))
INPUT_DEVICE = _device(os.getenv("INPUT_DEVICE", ""))
