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
    """Synthesize text and play it, blocking until done. Piper works a sentence at a time, so
    the first sentence plays while the rest is synthesized. Returns seconds until sound starts."""
    start = time.perf_counter()
    voice = get_voice()
    syn = SynthesisConfig(length_scale=config.TTS_LENGTH_SCALE, volume=config.TTS_VOLUME)
    chunks = queue.Queue()

    def produce():
        try:
            for c in voice.synthesize(text, syn_config=syn):
                chunks.put(c.audio_int16_array)
        finally:
            chunks.put(None)

    threading.Thread(target=produce, daemon=True).start()
    first = None
    with sd.OutputStream(samplerate=voice.config.sample_rate, channels=1, dtype="int16",
                         device=config.OUTPUT_DEVICE) as out:
        while (chunk := chunks.get()) is not None:
            if first is None:
                first = time.perf_counter() - start
            out.write(chunk.reshape(-1, 1))
    return first or 0.0


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
