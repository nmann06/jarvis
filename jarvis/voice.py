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
from datetime import datetime

import numpy as np
import sounddevice as sd

from jarvis import audio, config, latency, learn, llm, notify, project, stt, tools, tts
from jarvis.tools.clock import TZ
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


# Asking just the time or the date: answered here from the clock. Claude would look the time up
# with a tool even though every request tells it (two round trips, about 1.5 s). Like repeat
# requests, the whole utterance must match, so "what time is my class" still goes to Claude.
_TIME = re.compile(_FILLER + r"(?:what time is it|what s the time|what is the time|"
                   r"do you know what time it is|(?:tell me )?the time|time check)"
                   r"(?: now| right now)?(?: please| jarvis| sir)*")
_DATE = re.compile(_FILLER + r"(?:what s the date|what is the date|what s today s date|"
                   r"what is today s date|what day is it)"
                   r"(?: today)?(?: please| jarvis| sir)*")
_UNITS = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
          "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
          "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty"]
_ORDINAL = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
            "nine": "ninth", "twelve": "twelfth", "twenty": "twentieth", "thirty": "thirtieth"}


def _number(n):
    return _UNITS[n] if n < 20 else _TENS[n // 10] + (f" {_UNITS[n % 10]}" if n % 10 else "")


def _ordinal(n):
    words = _number(n).split()
    last = words[-1]
    words[-1] = _ORDINAL.get(last) or (last[:-1] + "ieth" if last.endswith("y") else last + "th")
    return " ".join(words)


def clock_words(t):
    """The time the way the owner wants it said: "ten oh five", "twelve thirty", "nine
    o'clock"; never "half past", "quarter to" or "noon"."""
    hour = _number(t.hour % 12 or 12)
    if t.minute == 0:
        return f"{hour} o'clock"
    return f"{hour} {'oh ' if t.minute < 10 else ''}{_number(t.minute)}"


def local_answer(text):
    """The reply to a bare time or date question, or None for anything else."""
    words, now = _words(text), datetime.now(TZ)
    if _TIME.fullmatch(words):
        part = ("in the morning" if 5 <= now.hour < 12 else "in the afternoon" if 12 <= now.hour < 17
                else "in the evening" if 17 <= now.hour < 22 else "at night")
        return f"It's {clock_words(now)} {part}."
    if _DATE.fullmatch(words):
        return f"It's {now:%A}, {now:%B} the {_ordinal(now.day)}."
    return None


# Leaving project mode. Like repeat requests, the whole utterance must match.
_END_PROJECT = re.compile(_FILLER + r"(?:end|close|exit|leave|quit|stop|finish|that s it for)"
                          r" (?:the |this )?project(?: mode)?(?: please| jarvis)*")


# Only the wake word ("Hey Jarvis?", "Jarvis"): often said in the follow-up window by someone
# who thinks Jarvis has stopped listening. Treated as a wake: the chime, then listening, with
# no trip to Claude for a "Yes, sir?".
_WAKE_ONLY = re.compile(r"(?:(?:hey|hi|ok|okay|yo) )?jarvis(?: jarvis)*")
WOKEN = "woken"  # handle()'s answer for it


def is_wake_only(text):
    return bool(_WAKE_ONLY.fullmatch(_words(text)))


# "Hey Jarvis, ..." or "Jarvis, ..." in the follow-up window is plainly meant for Jarvis, so
# Claude isn't asked to judge whether it was (and can't wrongly decide it wasn't).
_ADDRESSED = re.compile(r"\s*(?:(?:hey|hi|ok|okay|yo)\s+jarvis\b|jarvis\s*[,.!?])", re.I)

# Said before a tool call, these sound like Claude working the request out aloud rather than
# saying what it's doing ("I need tomorrow's date, so I'll check your calendar first.").
_THINKING_ALOUD = re.compile(r"\b(?:i need|i'll need|i will need|need to (?:know|check|find|work)|"
                             r"so i'll|so i will|so let me|in order to|to answer (?:that|this)|"
                             r"to work (?:that|it|this) out|to find out|i should|i don't have|"
                             r"i do not have|first,? i)\b", re.I)
PREAMBLE_WORDS = 15  # longer than this before a tool call is too long to say anyway


class _Sentences:
    """Claude's reply as it streams in, handed out one whole sentence at a time so speech can
    start before Claude has finished. Iterating blocks until close()."""

    def __init__(self):
        self._queue, self._buffer, self.spoken = queue.Queue(), "", []
        self.done = threading.Event()  # set once Claude has finished and spoken is complete
        self._held = False  # said "One moment." already this reply
        self.tools = []  # names of the tools Claude called

    def add(self, text):
        self._buffer += text
        *done, self._buffer = re.split(r"(?<=[.!?])\s+|\n+", self._buffer)
        for sentence in done:
            self._put(sentence)

    def tool(self, name):
        """Claude has started calling a tool: what it said just before ("Checking your
        calendar.") is spoken while the tool runs. Haiku sometimes thinks out loud there
        instead ("I need tomorrow's date, so I'll check your calendar first."), so that, or
        anything over PREAMBLE_WORDS words, becomes "One moment." (only once a reply)."""
        self.tools.append(name)
        preamble, self._buffer = self._buffer.strip(), ""
        if (_THINKING_ALOUD.search(preamble.replace("\u2019", "'"))
                or len(preamble.split()) > PREAMBLE_WORDS):
            log.info("before %s, said 'One moment.' instead of: %s", name, preamble)
            preamble, self._held = "" if self._held else "One moment.", True
        self._put(preamble)

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
# Whisper checks this much of the end of a "stop" for echo: long enough for "but it stays",
# short enough that Jarvis's echo doesn't drown out the owner's "stop".
ECHO_CHECK_SECONDS = 1.5
# Any of these in Whisper's words means the owner did say stop (not "it" from "stop it").
_STOP_WORDS = {"stop", "cancel", "shut", "quiet", "enough", "forget", "nevermind"}


def _stop_recognizer():
    global _vosk
    import vosk
    if _vosk is None:
        vosk.SetLogLevel(-1)
        _vosk = vosk.Model(str(config.VOSK_MODEL))
    return vosk.KaldiRecognizer(_vosk, audio.SAMPLE_RATE,
                                json.dumps(STOP_PHRASES + _DECOYS + ["[unk]"]))


def _word_list(text):
    return re.sub(r"[^a-z' ]+", " ", text.lower()).split()


def _is_echo(heard, reply):
    """Whether a stop Vosk heard was really Jarvis's own voice getting past the canceller
    (Vosk heard "but it stays" as "stop"). Whisper hears words in context far better: if it
    hears no stop word at the end of heard, but mostly words from reply, it was echo."""
    clip = np.concatenate(heard)[-int(ECHO_CHECK_SECONDS * audio.SAMPLE_RATE):]
    words = _word_list(stt.transcribe(clip.astype(np.float32) / 32768))
    if _STOP_WORDS & set(words):
        return False
    echoed = [w for w in words if w in set(_word_list(reply))]
    if len(words) >= 2 and len(echoed) * 2 > len(words):
        log.info("heard a stop, but Whisper heard %r, my own words: ignored", " ".join(words))
        return True
    return False


def _listen_for_stop(mic, stop, done, saying, reply):
    """While Jarvis talks, listen on the (echo-cancelled) mic for "stop" or "cancel" and set
    stop when heard. saying() is what Jarvis is saying now, reply() the whole reply so far.
    Runs until done is set."""
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
                if set(_word_list(saying())) & set(text.split()):
                    log.info("heard %r, but I just said it myself: ignored", text)
                    rec = _stop_recognizer()
                    continue
                if _is_echo(heard, reply()):
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
    reply = ((lambda: " ".join(texts.spoken)) if isinstance(texts, _Sentences)
             else (lambda: " ".join(texts)))
    listener = threading.Thread(target=_listen_for_stop, args=(mic, stop, done, saying, reply),
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
    if is_wake_only(text):
        log.info("just the wake word: listening again")
        turn.outcome = "woken again"
        return WOKEN
    if follow_up and _ADDRESSED.match(text):
        follow_up = False  # "Jarvis, ..." is meant for Jarvis
    if follow_up and _last_reply and "?" in _last_reply:
        # Jarvis just asked something ("Who should it say? Tell me the message and I'll draft
        # it."; the question isn't always last), so this is the answer, however
        # much it sounds like talk to someone else: Claude ignored "This is sick as fuck" here.
        follow_up = False
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
    # Not in the follow-up window, where it may be someone else asking: Claude judges that.
    if not follow_up and (reply := local_answer(text)) is not None:
        log.info("answering locally: %s", reply)
        with _speaker:
            _, stopped = _speak(mic, [reply], timing=turn)
        mic.clear()
        _last_reply = turn.reply = reply
        turn.outcome = "stopped" if stopped else "answered locally"
        learn.log_exchange("voice", text, reply, [reply], [], follow_up)
        return not stopped
    # With a project open, Claude still hears everything first: it passes on what's about the
    # project's work (project_message) and answers the rest itself.
    p = project.voice_project()
    if p is not None and _END_PROJECT.fullmatch(_words(text)):
        return _end_project(mic, p, turn)
    sentences, result = _Sentences(), {}

    def think():
        try:
            result["reply"] = brain.ask(text, follow_up=follow_up, on_text=sentences.add,
                                        timing=turn, on_tool=sentences.tool,
                                        context=project.context(p) if p else None)
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
    if "project_message" in sentences.tools or "start_project" in sentences.tools:
        turn.outcome = "to project"
        return False  # no follow-up window, so the project's answer is said as soon as it comes
    return True


def _end_project(mic, p, turn):
    """ "End project": stop what's running and go back to plain Jarvis."""
    global _last_reply
    project.end_voice()
    reply = f"Closed the {p.name} project. Back to normal, sir."
    with _speaker:
        _speak(mic, [reply], timing=turn)
    _last_reply = turn.reply = reply
    turn.outcome = "ended project"
    log.info("jarvis (project %s): %s", p.slug, reply)
    mic.clear()
    return False


def _speak_updates(mic):
    """Say what a project has queued to say, between conversations."""
    global _last_reply
    lines = []
    while not project.speech.empty():
        lines.append(project.speech.get_nowait())
    log.info("project says: %s", " ".join(lines))
    with _speaker:
        _speak(mic, lines)
    _last_reply = " ".join(lines)
    mic.clear()


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
            score = wake.wait(mic, interrupt=lambda: not project.speech.empty())
            if score is None:  # a project has something to say
                _speak_updates(mic)
                continue
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
            while utterance is not None:
                answered = handle(mic, brain, utterance, follow_up)
                if answered == WOKEN:  # just "Hey Jarvis" again: chime and listen, as for a wake
                    play(CHIME)
                    utterance, follow_up = audio.record_utterance(mic, stt.transcribe), False
                    continue
                if not answered:
                    break
                utterance = audio.record_utterance(mic, stt.transcribe,
                                                   wait_seconds=config.FOLLOW_UP_SECONDS)
                follow_up = True
            play(END_CHIME)
            mic.clear()

