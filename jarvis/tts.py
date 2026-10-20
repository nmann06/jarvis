"""Text-to-speech with Piper. Run: python -m jarvis.tts "Some text" """

import sys
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
    """Synthesize text and play it, blocking until done. Returns synthesis time in seconds."""
    start = time.perf_counter()
    audio, rate = synthesize(text)
    elapsed = time.perf_counter() - start
    sd.play(audio, rate, device=config.OUTPUT_DEVICE)
    sd.wait()
    return elapsed


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
