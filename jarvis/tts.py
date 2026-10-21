"""Text-to-speech with Piper. Run: python -m jarvis.tts "Some text" """

import queue
import sys
import threading
import time

import numpy as np
import sounddevice as sd
from piper import PiperVoice, SynthesisConfig

from jarvis import config

_voice = None
THINKING_AFTER = 0.5  # seconds of silence while waiting before the thinking sound starts


def get_voice():
    # Loading the model takes a moment, so do it once and reuse it.
    global _voice
    if _voice is None:
        _voice = PiperVoice.load(str(config.PIPER_VOICE))
    return _voice


def synthesize(text):
    """Return (int16 samples, sample_rate) for text."""
    voice = get_voice()
    syn = SynthesisConfig(length_scale=config.TTS_LENGTH_SCALE, volume=config.TTS_VOLUME)
    chunks = [c.audio_int16_array for c in voice.synthesize(text, syn_config=syn)]
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    return audio, voice.config.sample_rate


def speak(text):
    """Synthesize text and play it, blocking until done. Returns seconds until sound starts."""
    start = time.perf_counter()
    first = speak_all([text])
    return first - start if first else 0.0


def _thinking_sound(rate):
    """One loop of the thinking sound: a soft two-note blip every 1.2 seconds."""
    t = np.arange(int(0.07 * rate)) / rate
    fade = np.minimum(1, np.minimum(t, t[::-1]) / 0.015)  # gentle edges so it doesn't click

    def note(freq):
        return np.sin(2 * np.pi * freq * t) * fade

    blip = np.concatenate([note(520), np.zeros(int(0.05 * rate)), note(660)])
    loop = np.concatenate([blip, np.zeros(int(1.2 * rate) - len(blip))])
    return (loop * 0.06 * config.TTS_VOLUME * 32767).astype(np.int16)


def speak_all(texts, stop=None, thinking=False, played=None):
    """Speak each piece of text as it arrives from texts (any iterable, e.g. sentences still
    being written by Claude), blocking until the last is played or the stop event is set.
    Piper works a sentence at a time, so one sentence plays while the next is synthesized.
    With thinking, a soft sound plays whenever it has been waiting on texts for a moment (Claude
    thinking, a tool running). If played is a list, each text is added to it as it starts
    playing. Returns the perf_counter time speech started, or None if nothing was said."""
    voice = get_voice()
    syn = SynthesisConfig(length_scale=config.TTS_LENGTH_SCALE, volume=config.TTS_VOLUME)
    chunks = queue.Queue()

    def produce():
        try:
            for text in texts:
                for c in voice.synthesize(text, syn_config=syn):
                    chunks.put((text, c.audio_int16_array))
        finally:
            chunks.put(None)

    threading.Thread(target=produce, daemon=True).start()
    first = None
    slice_ = voice.config.sample_rate // 10  # write 100 ms at a time so stop is quick
    hum = _thinking_sound(voice.config.sample_rate) if thinking else None
    hum_at, quiet_since = 0, time.perf_counter()
    with sd.OutputStream(samplerate=voice.config.sample_rate, channels=1, dtype="int16",
                         device=config.OUTPUT_DEVICE) as out:
        while not (stop and stop.is_set()):
            try:
                chunk = chunks.get(timeout=0.1)
            except queue.Empty:
                if hum is not None and time.perf_counter() - quiet_since >= THINKING_AFTER:
                    piece = hum[hum_at:hum_at + slice_]
                    out.write(piece.reshape(-1, 1))
                    hum_at = (hum_at + len(piece)) % len(hum)
                continue
            if chunk is None:
                break
            text, chunk = chunk
            if played is not None and (not played or played[-1] is not text):
                played.append(text)
            hum_at = 0  # the next wait starts the sound from the top
            if first is None:
                first = time.perf_counter()
            for i in range(0, len(chunk), slice_):
                if stop and stop.is_set():
                    out.abort()  # cut off now rather than playing what's buffered
                    break
                out.write(chunk[i:i + slice_].reshape(-1, 1))
            quiet_since = time.perf_counter()
    return first


if __name__ == "__main__":
    text = " ".join(sys.argv[1:]) or "Good evening. All systems are operational."
    t0 = time.perf_counter()
    get_voice()
    load = time.perf_counter() - t0
    audio, rate = synthesize(text)
    synth = time.perf_counter() - t0 - load
    duration = len(audio) / rate
    print(f"model load {load:.2f}s, synthesis {synth:.2f}s for {duration:.2f}s of audio "
          f"(real-time factor {synth / duration:.2f})")
    sd.play(audio, rate, device=config.OUTPUT_DEVICE)
    sd.wait()
