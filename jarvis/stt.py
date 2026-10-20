"""Speech-to-text with faster-whisper. Run: python -m jarvis.stt [seconds] to record from the mic and transcribe."""

import sys
import time

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

from jarvis import config

SAMPLE_RATE = 16000  # what Whisper expects

_model = None


def get_model():
    # First use downloads the model to ~/.cache/huggingface, then it loads from disk.
    global _model
    if _model is None:
        _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8",
                              cpu_threads=config.WHISPER_THREADS)
    return _model


def transcribe(audio):
    """Transcribe float32 mono 16 kHz samples (or a path to an audio file) to text."""
    # vad_filter skips non-speech audio first; otherwise Whisper invents words like "You" on silence.
    segments, _ = get_model().transcribe(audio, language="en", beam_size=1,
                                         vad_filter=True, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


def record(seconds):
    """Record seconds of mono audio from the input device as float32 at 16 kHz."""
    audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1,
                   dtype="float32", device=config.INPUT_DEVICE)
    sd.wait()
    return audio[:, 0]


if __name__ == "__main__":
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5
    get_model()
    print(f"Recording {seconds:g}s, speak now...")
    audio = record(seconds)
    peak = float(np.abs(audio).max())
    start = time.perf_counter()
    text = transcribe(audio)
    elapsed = time.perf_counter() - start
    print(f"peak level {peak:.3f}, transcribed in {elapsed:.2f}s ({config.WHISPER_MODEL})")
    print(f"> {text}")
