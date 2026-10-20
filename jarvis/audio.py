"""Microphone streaming and end-of-speech detection."""

import queue

import numpy as np
import sounddevice as sd
from faster_whisper.vad import get_vad_model

from jarvis import config

SAMPLE_RATE = 16000
BLOCK = 1280  # 80 ms, the frame size openWakeWord is built around
VAD_WINDOW = 1536  # Silero scores 512-sample frames; look at the last 3

_vad = None


class Mic:
    """Continuous 16 kHz int16 mic stream; read() returns the next 80 ms block.

    Blocks queue up while the program is busy (e.g. playing the chime), so nothing is lost.
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                      blocksize=BLOCK, device=config.INPUT_DEVICE,
                                      callback=self._callback)

    def _callback(self, data, frames, time_info, status):
        self._queue.put(data[:, 0].copy())

    def __enter__(self):
        self._stream.start()
        return self

    def __exit__(self, *exc):
        self._stream.stop()
        self._stream.close()

    def read(self):
        return self._queue.get()

    def clear(self):
        """Drop queued audio, e.g. Jarvis's own voice recorded while it was speaking."""
        while not self._queue.empty():
            self._queue.get_nowait()


def speech_probability(window):
    """Silero speech probability (0-1) for int16 samples, using the last VAD_WINDOW of them."""
    global _vad
    if _vad is None:
        _vad = get_vad_model()
    frames = window[-VAD_WINDOW:].astype(np.float32) / 32768
    return float(_vad(frames).max())


def record_utterance(mic, wait_seconds=5.0, preroll=()):
    """Record from mic until the speaker stops. Returns float32 audio, or None if nobody spoke.

    wait_seconds is how long to wait for speech to start; after that, recording ends once
    there has been SILENCE_SECONDS of quiet, or at MAX_UTTERANCE_SECONDS. preroll blocks
    (already heard) go at the front of the recording.
    """
    # Only new blocks are judged, so the tail of "Jarvis" in the preroll can't count as the
    # request starting. Time is counted in blocks rather than by the clock.
    preroll = list(preroll)
    new, started, quiet = [], False, 0.0
    block_seconds = BLOCK / SAMPLE_RATE
    while True:
        new.append(mic.read())
        if len(new) < 2:
            continue
        p = speech_probability(np.concatenate(new[-2:]))
        if not started:
            if p >= config.SPEECH_THRESHOLD:
                started = True
            elif len(new) * block_seconds > wait_seconds:
                return None
        else:
            # Silero dips between words, so only a sustained low score counts as the end.
            quiet = quiet + block_seconds if p < config.SPEECH_THRESHOLD * 0.7 else 0.0
            if quiet >= config.SILENCE_SECONDS:
                break
        if len(new) * block_seconds >= config.MAX_UTTERANCE_SECONDS:
            break
    return np.concatenate(preroll + new).astype(np.float32) / 32768
