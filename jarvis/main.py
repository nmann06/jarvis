"""The assistant: wake word -> chime -> record -> transcribe -> Claude -> speak -> follow-up.

Run: python -m jarvis.main   (Ctrl+C to stop). Each turn logs how long every stage took.
"""

import logging
import threading
import time

import numpy as np
import sounddevice as sd

from jarvis import audio, config, llm, stt, tts
from jarvis.tools import timers
from jarvis.wakeword import WakeWord

log = logging.getLogger("jarvis")

_speaker = threading.Lock()  # timer alerts speak from another thread; one voice at a time


def _tone(freqs, seconds=0.09, rate=48000):
    t = np.arange(int(seconds * rate)) / rate
    fade = np.minimum(1, np.minimum(t, t[::-1]) / 0.01)  # 10 ms fades so it doesn't click
    return np.concatenate([0.25 * np.sin(2 * np.pi * f * t) * fade for f in freqs]).astype(np.float32)


CHIME = _tone([660, 990])  # rising: listening
END_CHIME = _tone([990, 660])  # falling: follow-up window closed


def play(samples, rate=48000):
    with _speaker:
        sd.play(samples, rate, device=config.OUTPUT_DEVICE)
        sd.wait()


def say(text):
    with _speaker:
        return tts.speak(text)


def handle(mic, brain, utterance):
    """Transcribe, ask Claude, speak. Returns False if there was nothing to answer."""
    t0 = time.perf_counter()
    text = stt.transcribe(utterance)
    t1 = time.perf_counter()
    if not text:
        log.info("heard nothing intelligible (%.1fs of audio)", len(utterance) / audio.SAMPLE_RATE)
        return False
    log.info("you: %s", text)
    reply = brain.ask(text)
    t2 = time.perf_counter()
    log.info("jarvis: %s", reply)
    synth = say(reply)
    # Recording only stops after SILENCE_SECONDS of quiet, so that's part of the wait too.
    log.info("latency: silence %.2fs + stt %.2fs + claude %.2fs + tts %.2fs = %.2fs from end of "
             "speech to voice", config.SILENCE_SECONDS, t1 - t0, t2 - t1, synth,
             config.SILENCE_SECONDS + (t2 - t0) + synth)
    mic.clear()  # drop Jarvis's own voice picked up by the mic
    return True


def run():
    wake, brain = WakeWord(), llm.Brain()
    stt.get_model()
    tts.get_voice()
    timers.on_alert = say
    with audio.Mic() as mic:
        log.info("ready, listening for 'Hey Jarvis' (threshold %.2f)", config.WAKE_THRESHOLD)
        while True:
            score = wake.wait(mic)
            log.info("wake (score %.2f)", score)
            play(CHIME)
            utterance = audio.record_utterance(mic, preroll=wake.preroll)
            # Follow-up window: after a reply, listen again without needing the wake word.
            while utterance is not None and handle(mic, brain, utterance):
                utterance = audio.record_utterance(mic, wait_seconds=config.FOLLOW_UP_SECONDS)
            play(END_CHIME)
            mic.clear()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "faster_whisper"):  # per-request and per-clip chatter
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        run()
    except KeyboardInterrupt:
        pass
