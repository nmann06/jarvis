"""Voice front end: wake word -> chime -> record -> transcribe -> Claude -> speak -> follow-up.

Started by jarvis.main. Each turn logs how long every stage took.
"""

import json
import logging
import queue
import re
import threading
import time
import wave

import numpy as np
import sounddevice as sd

from jarvis import audio, config, llm, notify, stt, tools, tts
from jarvis.wakeword import WakeWord

log = logging.getLogger("jarvis.voice")

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


# Ways of asking Jarvis to repeat itself, answered locally with the last reply word for word.
# The whole utterance must be one of these (after dropping fillers), so "what did you say about
# the weather" still goes to Claude.
_FILLER = r"(?:hey |ok |okay |sorry |um |uh |jarvis |please |can you |could you |would you |" \
          r"just |a |)*"
_REPEAT = re.compile(_FILLER + r"(?:"
                     r"(?:say|repeat) (?:that|it|this|what you said|yourself|the last (?:thing|part))"
                     r"(?: again| one more time| for me)?|"
                     r"repeat|again|one more time|come again|pardon(?: me)?|what|sorry|"
                     r"what (?:was that|did you (?:just )?say)|"
                     r"i (?:didn t|did not|couldn t|could not) (?:catch|hear|get) (?:that|it|you)"
                     r")" + r"(?: please| jarvis)*")


def is_repeat_request(text):
    words = re.sub(r"[^a-z ]+", " ", text.lower().replace("'", " ")).split()
    return bool(_REPEAT.fullmatch(" ".join(words)))


class _Sentences:
    """Claude's reply as it streams in, handed out one whole sentence at a time so speech can
    start before Claude has finished. Iterating blocks until close()."""

    def __init__(self):
        self._queue, self._buffer, self.spoken = queue.Queue(), "", []

    def add(self, text):
        self._buffer += text
        *done, self._buffer = re.split(r"(?<=[.!?])\s+|\n+", self._buffer)
        for sentence in done:
            self._put(sentence)

    def close(self):
        self._put(self._buffer)
        self._queue.put(None)

    def _put(self, sentence):
        sentence = sentence.strip()
        # A follow-up that wasn't meant for Jarvis comes back as IGNORE: never say it.
        if sentence and sentence.rstrip(".").upper() != llm.IGNORE:
            self.spoken.append(sentence)
            self._queue.put(sentence)

    def __iter__(self):
        while (sentence := self._queue.get()) is not None:
            yield sentence


# Said while Jarvis is talking (or thinking), these cut it off. Vosk listens with only these
# phrases plus [unk] (anything else), and the whole utterance must be one of them, so Jarvis
# saying "stop by the office" or "nothing to cancel" (any echo the canceller misses) doesn't count.
STOP_PHRASES = ["stop", "cancel", "stop it", "stop talking", "shut up", "be quiet", "quiet",
                "enough", "that's enough", "never mind", "forget it", "jarvis stop", "jarvis cancel",
                "okay stop", "stop stop"]
_vosk = None


def _stop_recognizer():
    global _vosk
    import vosk
    if _vosk is None:
        vosk.SetLogLevel(-1)
        _vosk = vosk.Model(str(config.VOSK_MODEL))
    return vosk.KaldiRecognizer(_vosk, audio.SAMPLE_RATE, json.dumps(STOP_PHRASES + ["[unk]"]))


def _listen_for_stop(mic, stop, done, saying):
    """While Jarvis talks, listen on the (echo-cancelled) mic for "stop" or "cancel" and set
    stop when heard. saying() is what Jarvis is saying now. Runs until done is set."""
    rec, heard = _stop_recognizer(), []
    try:
        while not done.is_set():
            block = mic.read(timeout=0.1)
            if block is None:
                continue
            heard.append(block)
            # Act on the partial result too: Vosk only finishes an utterance after a pause,
            # which may not come while Jarvis is still talking.
            if rec.AcceptWaveform(block.tobytes()):
                text = json.loads(rec.Result())["text"]
            else:
                text = json.loads(rec.PartialResult())["partial"]
            if text in STOP_PHRASES:
                # The canceller lets some echo through, mostly as a reply starts, so Jarvis
                # saying "stop by the office" could otherwise stop itself.
                said = set(re.sub(r"[^a-z' ]+", " ", saying().lower()).split())
                if said & set(text.split()):
                    log.info("heard %r, but I just said it myself: ignored", text)
                    rec = _stop_recognizer()
                    continue
                log.info("heard %r: stopping", text)
                stop.set()
                return
            if text and text != getattr(rec, "_logged", None):
                rec._logged = text
                log.info("heard %r while talking (not a stop)", text)
    finally:
        if config.SAVE_STOP_AUDIO and heard:
            path = config.ROOT / "data" / f"stop-listen-{time.strftime('%H%M%S')}.wav"
            path.parent.mkdir(exist_ok=True)
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(audio.SAMPLE_RATE)
                w.writeframes(np.concatenate(heard).tobytes())


def _speak(mic, texts):
    """Speak texts (a list, or _Sentences still streaming in), stopping if the owner says stop.
    Returns (time sound started or None, whether it was stopped)."""
    stop, done = threading.Event(), threading.Event()
    saying = ((lambda: " ".join(texts.spoken[-2:])) if isinstance(texts, _Sentences)
              else (lambda: " ".join(texts)))
    listener = threading.Thread(target=_listen_for_stop, args=(mic, stop, done, saying),
                                name="stop", daemon=True)
    listener.start()
    try:
        return tts.speak_all(texts, stop=stop), stop.is_set()
    finally:
        done.set()
        listener.join()


_last_reply = None


def handle(mic, brain, utterance, follow_up=False):
    """Transcribe, ask Claude, speak. Returns False if there was nothing to answer."""
    global _last_reply
    t0 = time.perf_counter()
    text = stt.transcribe(utterance)
    t1 = time.perf_counter()
    if not text:
        log.info("heard nothing intelligible (%.1fs of audio)", len(utterance) / audio.SAMPLE_RATE)
        return False
    log.info("you: %s", text)
    if _last_reply and is_repeat_request(text):
        log.info("repeating last reply (local, no Claude)")
        with _speaker:
            _, stopped = _speak(mic, [_last_reply])
        mic.clear()
        return not stopped
    sentences, result = _Sentences(), {}

    def think():
        try:
            result["reply"] = brain.ask(text, follow_up=follow_up, on_text=sentences.add)
        except Exception as e:
            result["error"] = e
        finally:
            result["done"] = time.perf_counter()
            sentences.close()

    thinker = threading.Thread(target=think, name="claude", daemon=True)
    with _speaker:
        thinker.start()
        first, stopped = _speak(mic, sentences)  # speaks while Claude is still writing
        if stopped:
            # "Stop" or "cancel": Claude may still be writing, but nobody wants the rest.
            mic.clear()
            return False
        thinker.join()
        if "error" in result:
            raise result["error"]
        reply = result["reply"]
        if reply is None and not sentences.spoken:
            log.info("not meant for me, back to idle (claude %.2fs)", result["done"] - t1)
            return False
        if not sentences.spoken:  # a reply that wasn't streamed, e.g. after a refusal
            start = time.perf_counter()
            _, stopped = _speak(mic, [reply])
            first = start
            if stopped:
                mic.clear()
                return False
    _last_reply = " ".join(sentences.spoken) or reply
    log.info("jarvis: %s", _last_reply)
    # Recording only stops after SILENCE_SECONDS of quiet, so that's part of the wait too.
    log.info("latency: silence %.2fs + stt %.2fs + claude to first word %.2fs = %.2fs from end "
             "of speech to voice (claude finished after %.2fs)", config.SILENCE_SECONDS, t1 - t0,
             first - t1, config.SILENCE_SECONDS + first - t0, result["done"] - t1)
    mic.clear()  # drop Jarvis's own voice picked up by the mic
    return True


def run():
    wake, brain = WakeWord(), llm.Brain("voice")
    tools.warm_up()
    stt.get_model()
    tts.get_voice()
    _stop_recognizer()
    notify.register("voice", say)
    with audio.Mic() as mic:
        log.info("ready, listening for 'Hey Jarvis' (threshold %.2f)", config.WAKE_THRESHOLD)
        while True:
            score = wake.wait(mic)
            log.info("wake (score %.2f)", score)
            play(CHIME)
            utterance = audio.record_utterance(mic, preroll=wake.preroll)
            follow_up = False
            # Follow-up window: after a reply, listen again without needing the wake word.
            # Claude decides whether what it hears there was meant for Jarvis.
            while utterance is not None and handle(mic, brain, utterance, follow_up):
                utterance = audio.record_utterance(mic, wait_seconds=config.FOLLOW_UP_SECONDS)
                follow_up = True
            play(END_CHIME)
            mic.clear()

