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


PIPER_VOICE = ROOT / os.getenv("PIPER_VOICE", "models/piper/en_GB-alan-medium.onnx")
TTS_LENGTH_SCALE = float(os.getenv("TTS_LENGTH_SCALE", "1.0"))  # >1 slower, <1 faster
TTS_VOLUME = float(os.getenv("TTS_VOLUME", "1.0"))
OUTPUT_DEVICE = _device(os.getenv("OUTPUT_DEVICE", ""))
INPUT_DEVICE = _device(os.getenv("INPUT_DEVICE", ""))
