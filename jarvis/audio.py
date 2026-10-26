"""Microphone streaming and end-of-speech detection."""

import queue
import re
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import sounddevice as sd
from faster_whisper.vad import get_vad_model

from jarvis import config

SAMPLE_RATE = 16000
BLOCK = 1280  # 80 ms, the frame size openWakeWord is built around
VAD_WINDOW = 1536  # Silero scores 512-sample frames; look at the last 3
EARLY_STT_AFTER = 0.24  # seconds into a pause to start transcribing, in case it's the end

_vad = None
_stt = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")  # one transcription at a time


class Mic:
    """Continuous 16 kHz int16 mic stream; read() returns the next 80 ms block.

    Blocks queue up while the program is busy (e.g. playing the chime), so nothing is lost.
    After read(), captured is when that block arrived (perf_counter time).
    """

    def __init__(self):
        self._queue = queue.Queue()
        self.captured = None
        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                      blocksize=BLOCK, device=config.INPUT_DEVICE,
                                      callback=self._callback)

    def _callback(self, data, frames, time_info, status):
        self._queue.put((time.perf_counter(), data[:, 0].copy()))

    def __enter__(self):
        self._stream.start()
        return self

    def __exit__(self, *exc):
        self._stream.stop()
        self._stream.close()

    def read(self, timeout=None):
        """The next block; with a timeout, None if none came in time."""
        try:
            self.captured, block = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        return block

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


def _timed(transcribe, audio):
    started = time.perf_counter()
    text = transcribe(audio)
    return text, started, time.perf_counter()


# Words a request doesn't end on: hesitations, and words that lead into more.
_TRAILING = {"uh", "um", "er", "erm", "hmm", "mm", "so", "and", "but", "or", "the", "a", "an",
             "to", "of", "for", "with", "like", "my", "your"}


def sounds_finished(text):
    """Whisper ends a finished sentence with . ? or !, and leaves one that trails off bare or
    with "...". Only real requests count: not "Jarvis." or "Um."."""
    # Numbers count as words: "How much is the test worth for 3-2-4?" doesn't end on "for".
    words = [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in ("hey", "jarvis")]
    text = text.strip()
    if len(words) < 2 or text[-1:] not in ".?!" or text.endswith(".."):
        return False
    # A question can end on "like" ("What's the weather like?") but no other trailing word.
    return words[-1] not in _TRAILING or (words[-1] == "like" and text.endswith("?"))


class Utterance:
    """What record_utterance heard: float32 audio, when the speaker stopped talking
    (speech_end, perf_counter time, to within a block), when recording stopped (ended), and
    why (endpoint): "pause" (SILENCE_SECONDS of quiet), "quick" (a finished-sounding sentence
    and QUICK_SILENCE_SECONDS of quiet) or "max" (MAX_UTTERANCE_SECONDS)."""

    def __init__(self, audio, speech_end, ended, endpoint, transcribe, early=None):
        self.audio, self.speech_end, self.ended, self.endpoint = audio, speech_end, ended, endpoint
        self._transcribe, self._early = transcribe, early

    @property
    def seconds(self):
        return len(self.audio) / SAMPLE_RATE

    def transcribe(self):
        """(text, when transcription started, when it finished). It has usually already been
        done during the pause at the end; otherwise it is done now."""
        job = self._early or _stt.submit(_timed, self._transcribe, self.audio)
        return job.result()


def _float(blocks):
    return np.concatenate(blocks).astype(np.float32) / 32768


def record_utterance(mic, transcribe, wait_seconds=5.0, preroll=(), max_seconds=None):
    """Record from mic until the speaker stops. Returns an Utterance, or None if nobody spoke.

    transcribe(audio) -> text is used to transcribe during pauses, so the text is ready the
    moment the speaker is done. wait_seconds is how long to wait for speech to start; after
    that, recording ends once there has been SILENCE_SECONDS of quiet (QUICK_SILENCE_SECONDS
    if what was said so far sounds finished), or at max_seconds (MAX_UTTERANCE_SECONDS if not
    given). preroll blocks (already heard) go at the front of the recording.
    """
    # Only new blocks are judged, so the tail of "Jarvis" in the preroll can't count as the
    # request starting. Time is counted in blocks rather than by the clock.
    preroll = list(preroll)
    new, started, quiet, speech_end, early = [], False, 0.0, None, None
    block_seconds = BLOCK / SAMPLE_RATE
    endpoint = "max"
    while True:
        new.append(mic.read())
        if len(new) < 2:
            continue
        p = speech_probability(np.concatenate(new[-2:]))
        if not started:
            if p >= config.SPEECH_THRESHOLD:
                started, speech_end = True, mic.captured
            elif len(new) * block_seconds > wait_seconds:
                return None
        # Silero dips between words, so only a sustained low score counts as the end.
        elif p >= config.SPEECH_THRESHOLD * 0.7:
            quiet, speech_end = 0.0, mic.captured
            if early is not None:
                early.cancel()  # still talking: that transcript is already out of date
                early = None
        else:
            quiet += block_seconds
            if early is None and quiet >= EARLY_STT_AFTER:
                # This pause may be the end: transcribe now, so the text is ready if it is.
                early = _stt.submit(_timed, transcribe, _float(preroll + new))
            if quiet >= config.SILENCE_SECONDS:
                endpoint = "pause"
                break
            if (quiet >= config.QUICK_SILENCE_SECONDS and early is not None and early.done()
                    and not early.exception() and sounds_finished(early.result()[0])):
                endpoint = "quick"
                break
        if len(new) * block_seconds >= (max_seconds or config.MAX_UTTERANCE_SECONDS):
            break
    # early, if any, covers everything said: only quiet has come in since it started. (Cut off
    # mid-speech at MAX_UTTERANCE_SECONDS there is none, and it is all transcribed afterwards.)
    return Utterance(_float(preroll + new), speech_end, time.perf_counter(), endpoint,
                     transcribe, early)
