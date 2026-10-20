"""Speech-to-text with faster-whisper. Run: python -m jarvis.stt [seconds] to record from the mic and transcribe."""

import ctypes
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

from jarvis import config

SAMPLE_RATE = 16000  # what Whisper expects

_model = None


def _load_cuda_libs():
    # cuBLAS/cuDNN come from the nvidia-*-cu12 pip packages, which aren't on the loader path,
    # so load them by full path before CTranslate2 looks for them.
    try:
        import nvidia.cublas, nvidia.cudnn
    except ImportError:
        return
    for pkg, names in ((nvidia.cublas, ["libcublasLt.so.12", "libcublas.so.12"]),
                       (nvidia.cudnn, ["libcudnn*.so.9"])):
        lib_dir = Path(pkg.__path__[0]) / "lib"
        for pattern in names:
            for lib in sorted(lib_dir.glob(pattern)):
                ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)


def get_model():
    # First use downloads the model to ~/.cache/huggingface, then it loads from disk.
    global _model
    if _model is None:
        if config.WHISPER_DEVICE == "cuda":
            _load_cuda_libs()
            _model = WhisperModel(config.WHISPER_MODEL, device="cuda", compute_type="float16")
        else:
            _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8",
                                  cpu_threads=config.WHISPER_THREADS)
    return _model


def transcribe(audio):
    """Transcribe float32 mono 16 kHz samples (or a path to an audio file) to text."""
    # vad_filter skips non-speech audio first; otherwise Whisper invents words like "You" on silence.
    segments, _ = get_model().transcribe(audio, language="en", beam_size=1,
                                         vad_filter=True, condition_on_previous_text=False,
                                         hotwords="Jarvis")
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
