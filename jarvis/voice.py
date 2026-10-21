"""Voice front end: wake word -> chime -> record -> transcribe -> Claude -> speak -> follow-up.

Started by jarvis.main. Each turn logs where its time went (jarvis.latency).
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

from jarvis import audio, config, latency, llm, notify, stt, tools, tts
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


_alert_until = 0.0  # perf_counter time until which a wake is Jarvis hearing its own alert


def say(text):
    """Speak an alert (timer, Claude Code finished) from another thread."""
    global _alert_until
    with _speaker:
        _alert_until = float("inf")
        try:
            return tts.speak(text)
        finally:
            _alert_until = time.perf_counter() + 1.0  # the mic's audio lags the speaker a little


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


def _words(text):
    return " ".join(re.sub(r"[^a-z ]+", " ", text.lower().replace("'", " ")).split())


def is_repeat_request(text):
    return bool(_REPEAT.fullmatch(_words(text)))


# Picking up a reply that was cut off with "stop". Like repeat requests, the whole utterance
# must match, so "continue the story" still goes to Claude.
_CONTINUE = re.compile(_FILLER + r"(?:continue|keep going|go on|carry on|resume|"
                       r"finish (?:that|what you were saying)|where were you)"
                       r"(?: talking| speaking| where you left off| from there)?"
                       r"(?: please| jarvis)*")


def is_continue_request(text):
    return bool(_CONTINUE.fullmatch(_words(text)))


class _Sentences:
    """Claude's reply as it streams in, handed out one whole sentence at a time so speech can
    start before Claude has finished. Iterating blocks until close()."""

    def __init__(self):
        self._queue, self._buffer, self.spoken = queue.Queue(), "", []
        self.done = threading.Event()  # set once Claude has finished and spoken is complete

    def add(self, text):
        self._buffer += text
        *done, self._buffer = re.split(r"(?<=[.!?])\s+|\n+", self._buffer)
        for sentence in done:
            self._put(sentence)

    def close(self):
        self._put(self._buffer)
        self._queue.put(None)
        self.done.set()

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
# Not stops, but in the grammar so Vosk has somewhere else to put them: with only the stop
# phrases to choose from, an echoed "yes sir" came out as "stop".
_DECOYS = ["sir", "yes sir", "no sir", "of course sir", "certainly sir"]
_vosk = None


def _stop_recognizer():
    global _vosk
    import vosk
    if _vosk is None:
        vosk.SetLogLevel(-1)
        _vosk = vosk.Model(str(config.VOSK_MODEL))
    return vosk.KaldiRecognizer(_vosk, audio.SAMPLE_RATE,
                                json.dumps(STOP_PHRASES + _DECOYS + ["[unk]"]))


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
                    # A partial "stop" is usually echo or noise that Vosk drops once it decodes
                    # the whole utterance, so make it decide now (this also starts a new one).
                    text = json.loads(rec.FinalResult())["text"]
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


_unfinished = None  # (texts, index of the sentence cut off) after "stop", for "continue"


def _speak(mic, texts, thinking=False, timing=None):
    """Speak texts (a list, or _Sentences still streaming in), stopping if the owner says stop.
    thinking plays the thinking sound while waiting on texts; timing is a latency.Turn. Returns
    (time speech started or None, whether it was stopped). When stopped, remembers where, so
    "continue" can resume."""
    global _unfinished
    stop, done = threading.Event(), threading.Event()
    saying = ((lambda: " ".join(texts.spoken[-2:])) if isinstance(texts, _Sentences)
              else (lambda: " ".join(texts)))
    listener = threading.Thread(target=_listen_for_stop, args=(mic, stop, done, saying),
                                name="stop", daemon=True)
    listener.start()
    try:
        played = []
        first = tts.speak_all(texts, stop=stop, thinking=thinking, played=played, timing=timing)
        if stop.is_set():
            _unfinished = (texts, max(len(played) - 1, 0))
        return first, stop.is_set()
    finally:
        done.set()
        listener.join()


_last_reply = None


def _continue(mic, timing):
    """Say the rest of a reply that was cut off, from the start of the sentence it stopped in."""
    global _last_reply, _unfinished
    texts, start = _unfinished
    _unfinished = None
    if isinstance(texts, _Sentences):
        texts.done.wait(timeout=60)  # Claude may have still been writing when it was stopped
        texts = texts.spoken
    rest = texts[start:]
    log.info("continuing: %s", " ".join(rest))
    with _speaker:
        _, stopped = _speak(mic, rest, timing=timing)
    if stopped:  # _speak remembered the new place within rest; make it within the whole reply
        _unfinished = (texts, start + _unfinished[1])
        mic.clear()
        return False
    _last_reply = " ".join(texts)
    mic.clear()
    return True


def handle(mic, brain, utterance, follow_up=False):
    """Transcribe, ask Claude, speak. Returns False if there was nothing to answer."""
    turn = latency.Turn(utterance, follow_up)
    try:
        return _answer(mic, brain, utterance, follow_up, turn)
    finally:
        turn.finish()  # logs where the time went


def _answer(mic, brain, utterance, follow_up, turn):
    global _last_reply, _unfinished
    text, started, finished = utterance.transcribe()  # usually done during the pause already
    turn.transcribed(text, started, finished)
    if not text:
        log.info("heard nothing intelligible (%.1fs of audio)", utterance.seconds)
        turn.outcome = "nothing heard"
        return False
    log.info("you: %s", text)
    if _last_reply and is_repeat_request(text):
        log.info("repeating last reply (local, no Claude)")
        with _speaker:
            _, stopped = _speak(mic, [_last_reply], timing=turn)
        mic.clear()
        turn.reply, turn.outcome = _last_reply, "stopped" if stopped else "repeated"
        return not stopped
    if _unfinished and is_continue_request(text):
        done = _continue(mic, turn)
        turn.outcome = "continued" if done else "stopped"
        return done
    _unfinished = None  # a new request: whatever was cut off before is dropped
    sentences, result = _Sentences(), {}

    def think():
        try:
            result["reply"] = brain.ask(text, follow_up=follow_up, on_text=sentences.add,
                                        timing=turn)
        except Exception as e:
            result["error"] = e
        finally:
            sentences.close()

    thinker = threading.Thread(target=think, name="claude", daemon=True)
    with _speaker:
        thinker.start()
        # Speaks while Claude is still writing, with the thinking sound while it waits.
        _, stopped = _speak(mic, sentences, thinking=config.THINKING_SOUND, timing=turn)
        if stopped:
            # "Stop" or "cancel": Claude may still be writing, but nobody wants the rest.
            mic.clear()
            turn.outcome = "stopped"
            return False
        thinker.join()
        if "error" in result:
            raise result["error"]
        reply = result["reply"]
        if reply is None and not sentences.spoken:
            log.info("not meant for me, back to idle")
            turn.outcome = "ignored"
            return False
        if not sentences.spoken:  # a reply that wasn't streamed, e.g. after a refusal
            _, stopped = _speak(mic, [reply], timing=turn)
            if stopped:
                mic.clear()
                turn.outcome = "stopped"
                return False
    _last_reply = turn.reply = " ".join(sentences.spoken) or reply
    turn.outcome = "answered"
    log.info("jarvis: %s", _last_reply)
    mic.clear()  # drop Jarvis's own voice picked up by the mic
    return True


def run():
    wake, brain = WakeWord(), llm.Brain("voice")
    tools.warm_up()
    stt.warm_up()
    tts.get_voice()
    _stop_recognizer()
    notify.register("voice", say)
    with audio.Mic() as mic:
        log.info("ready, listening for 'Hey Jarvis' (threshold %.2f)", config.WAKE_THRESHOLD)
        while True:
            score = wake.wait(mic)
            if mic.captured < _alert_until:
                # An alert can name the project "jarvis"; don't wake on it.
                log.info("wake (score %.2f) during an alert: ignored", score)
                continue
            log.info("wake (score %.2f)", score)
            # While the owner talks, get Claude's cache ready for the conversation so far.
            threading.Thread(target=brain.prewarm, name="prewarm", daemon=True).start()
            play(CHIME)
            utterance = audio.record_utterance(mic, stt.transcribe, preroll=wake.preroll)
            if utterance is None:
                log.info("no request after the wake word")
            follow_up = False
            # Follow-up window: after a reply, listen again without needing the wake word.
            # Claude decides whether what it hears there was meant for Jarvis.
            while utterance is not None and handle(mic, brain, utterance, follow_up):
                utterance = audio.record_utterance(mic, stt.transcribe,
                                                   wait_seconds=config.FOLLOW_UP_SECONDS)
                follow_up = True
            play(END_CHIME)
            mic.clear()

