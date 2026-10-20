"""Benchmark Whisper models on clips with known text.

Usage: python -m tests.bench_stt [models...] [--clip file.wav "expected text"]
Without --clip, test clips are generated with the Piper voice.
"""

import re
import sys
import tempfile
import time
import wave
from pathlib import Path

from faster_whisper import WhisperModel

from jarvis import config, tts

SENTENCES = [
    "What time is it?",
    "Set a timer for ten minutes.",
    "Add a note to buy milk and eggs tomorrow.",
    "What's the weather like in Kingston today?",
    "How hot is the CPU running right now?",
]


def words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def word_error_rate(ref, hyp):
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[len(h)] / max(len(r), 1)


def make_clips(folder):
    clips = []
    for i, text in enumerate(SENTENCES):
        audio, rate = tts.synthesize(text)
        path = Path(folder) / f"clip{i}.wav"
        with wave.open(str(path), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(rate)
            f.writeframes(audio.tobytes())
        clips.append((path, text, len(audio) / rate))
    return clips


def clip_seconds(path):
    with wave.open(str(path)) as f:
        return f.getnframes() / f.getframerate()


def main():
    args = sys.argv[1:]
    clips = []
    if "--clip" in args:
        i = args.index("--clip")
        path, text = Path(args[i + 1]), args[i + 2]
        clips = [(path, text, clip_seconds(path))]
        del args[i:i + 3]
    models = args or ["tiny.en", "base.en"]

    with tempfile.TemporaryDirectory() as tmp:
        clips = clips or make_clips(tmp)
        total_audio = sum(c[2] for c in clips)
        for name in models:
            t0 = time.perf_counter()
            model = WhisperModel(name, device="cpu", compute_type="int8",
                                 cpu_threads=config.WHISPER_THREADS)
            load = time.perf_counter() - t0
            model.transcribe(str(clips[0][0]), language="en")  # warm-up
            elapsed, errors = 0.0, 0.0
            for path, text, _ in clips:
                t = time.perf_counter()
                segments, _ = model.transcribe(str(path), language="en", beam_size=1,
                                               condition_on_previous_text=False)
                hyp = " ".join(s.text.strip() for s in segments)
                elapsed += time.perf_counter() - t
                wer = word_error_rate(text, hyp)
                errors += wer
                print(f"  [{name}] {wer:4.0%}  {hyp}")
            print(f"{name}: load {load:.1f}s, {elapsed / total_audio:.3f}s per second of audio, "
                  f"avg {elapsed / len(clips):.2f}s per clip, WER {errors / len(clips):.0%}\n")


if __name__ == "__main__":
    main()
