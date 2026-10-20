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
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "300"))  # caps reply length and cost

PIPER_VOICE = ROOT / os.getenv("PIPER_VOICE", "models/piper/en_GB-alan-medium.onnx")
TTS_LENGTH_SCALE = float(os.getenv("TTS_LENGTH_SCALE", "1.0"))  # >1 slower, <1 faster
TTS_VOLUME = float(os.getenv("TTS_VOLUME", "1.0"))
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")  # "cuda" or "cpu"
WHISPER_THREADS = int(os.getenv("WHISPER_THREADS", "4"))
OUTPUT_DEVICE =_device(os.getenv("OUTPUT_DEVICE", ""))
INPUT_DEVICE = _device(os.getenv("INPUT_DEVICE", ""))
