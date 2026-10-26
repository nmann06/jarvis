"""Double-clap detection for the homecoming sequence (jarvis/homecoming.py): two claps, then
"Daddy's home", with no wake word.

Run: python -m jarvis.clap  to print each clap (and each near miss, with why it didn't count)
as it's heard, for tuning the CLAP_ settings; add --phrase to listen for the phrase after a
double clap too.
"""

import difflib
import re
import sys
import time
from collections import deque

import numpy as np

from jarvis import audio, config

FRAME = 80  # 5 ms: a clap is over in a few of these, and an 80 ms block blurs its attack
FRAME_SECONDS = FRAME / audio.SAMPLE_RATE
FULL_SCALE_DB = 20 * np.log10(32768)
FLOOR_MIN_DB = 30  # the background never counts as quieter than this (rms ~30), so hiss can't
RISE_DB = 12  # a clap jumps this far above the level 10-15 ms before it: sudden, not a swell
PEAK_FRAMES = 4  # its peak is looked for in the first 20 ms, which also give its pitch
DECAY_DB = 15  # it has died away once this far below its peak (or within 6 dB of the floor)
# ...and stays down (no more than HOLD_DB below its peak) this long: a consonant ("t", "k") can
# start as suddenly and die away as fast, but a vowel follows straight away, about as loud.
HOLD_SECONDS = 0.08
HOLD_DB = 12
HOLD_FLOOR_DB = 9


def _level(power):
    return 10 * np.log10(power + 1)


class ClapDetector:
    """Fed every mic block; heard(block) is True when a double clap has just finished: two
    claps CLAP_MIN_GAP to CLAP_MAX_GAP apart, with no clap in the CLAP_MAX_GAP before them and
    none soon after (so applause or knocking doesn't count). It waits a moment after the
    second clap to be sure; preroll then holds the blocks heard since that clap, which may
    have the start of what was said next.

    A clap is a peak CLAP_PEAK_DB above the background, reached within 5-15 ms and gone within
    CLAP_DECAY_SECONDS, high-pitched enough (CLAP_HIGH_FREQ) and not speech (Silero, only for
    the rare sound that gets that far). Quiet blocks cost a few array operations.

    report(line), if given, is called for each clap and near miss.
    """

    def __init__(self, report=None):
        self.report = report
        self.floor = None  # background level (dB of a 5 ms frame's mean square)
        self.preroll = []
        self.fired, self.gap = False, None  # the last heard() was True; seconds between the claps
        self._frame = 0  # frames fed so far: the detector's clock, so time not fed doesn't count
        self._recent = deque(maxlen=12)  # ~1 s of blocks, for Silero and the preroll
        self.reset()

    def reset(self):
        """Forget claps in progress, e.g. after the mic was read elsewhere (a conversation)."""
        self.fired = False
        self._claps = []  # frame of each clap's peak in the current run of claps
        self._candidate = None  # a loud transient being followed until it dies away
        self._armed = False  # the level has been below the threshold since the last transient
        self._before = np.full(3, np.inf)  # levels of the 3 frames before this block
        self._recent.clear()

    def heard(self, block):
        """Feed one block; True if that finished a double clap."""
        self._recent.append(block)
        x = block.astype(np.float32)
        power = (x.reshape(-1, FRAME) ** 2).mean(1)
        levels = _level(power)
        if self.floor is None:
            self.floor = max(float(np.median(levels)), FLOOR_MIN_DB)
        threshold = max(self.floor + config.CLAP_PEAK_DB, config.CLAP_MIN_DBFS + FULL_SCALE_DB)
        start, self._end = self._frame, self._frame + len(levels)
        if self._candidate or levels.max() >= threshold:
            # Something loud: follow it frame by frame (rare, so a Python loop is fine).
            d = np.diff(x, prepend=x[0])
            high = (d.reshape(-1, FRAME) ** 2).mean(1)
            history = np.concatenate([self._before, levels])
            for i in range(len(levels)):
                self._step(start + i, levels[i], history[i:i + 2], power[i], high[i], threshold)
        else:
            self._armed = True
        self._before = np.concatenate([self._before, levels])[-3:]
        self._frame = self._end
        # The background follows quieter sound at once and louder sound over a few seconds,
        # so speech or music raises it (claps must then be louder) but a clap barely does.
        med = float(np.median(levels))
        self.floor = max(self.floor + (0.3 if med < self.floor else 0.02) * (med - self.floor),
                         FLOOR_MIN_DB)
        self.fired = self._double()
        return self.fired

    def _step(self, n, level, before, power, high, threshold):
        """One 5 ms frame at or after something loud. before: levels 10 and 15 ms back."""
        c = self._candidate
        if c is None:
            if level < threshold:
                self._armed = True
                return
            if not self._armed:
                return
            self._armed = False
            c = self._candidate = {"onset": n, "at": n, "peak": level, "power": 0.0,
                                   "high": 0.0, "rise": level - before.max()}
        if n - c["onset"] < PEAK_FRAMES:
            c["power"] += power
            c["high"] += high
            if level > c["peak"]:
                c["peak"], c["at"] = level, n
            return
        since_peak = (n - c["at"]) * FRAME_SECONDS
        quiet = level <= max(c["peak"] - DECAY_DB, self.floor + 6)
        if "decay" in c:  # died away: it must stay quiet for HOLD_SECONDS
            if level > max(c["peak"] - HOLD_DB, self.floor + HOLD_FLOOR_DB):
                self._miss(c, "more sound right after")
            elif since_peak - c["decay"] >= HOLD_SECONDS:
                self._candidate = None
                self._judge(c)
        elif level > c["peak"] + 3:
            self._miss(c, "kept getting louder")
        elif quiet:
            c["decay"] = since_peak
        elif since_peak > config.CLAP_DECAY_SECONDS:
            self._miss(c, f"still loud after {since_peak:.2f}s")
        self._armed = self._candidate is None and level < threshold

    def _describe(self, c):
        return (f"peak {c['peak'] - FULL_SCALE_DB:.0f} dBFS, {c['peak'] - self.floor:.0f} dB over "
                f"the floor ({self.floor - FULL_SCALE_DB:.0f} dBFS), rise {c['rise']:.0f} dB, "
                f"high {c['high'] / (2 * c['power'] + 1):.2f}")

    def _miss(self, c, why):
        self._candidate = None
        if self.report:
            self.report(f"  not a clap ({why}): {self._describe(c)}")

    def _judge(self, c):
        if c["rise"] < RISE_DB:
            return self._miss(c, "rose too slowly")
        if c["high"] / (2 * c["power"] + 1) < config.CLAP_HIGH_FREQ:
            return self._miss(c, "too low-pitched")
        if (speech := self._speech(c["onset"])) >= config.SPEECH_THRESHOLD:
            return self._miss(c, f"speech {speech:.2f}")
        if self.report:
            gap = (f", {(c['at'] - self._claps[-1]) * FRAME_SECONDS:.2f}s after the last"
                   if self._claps else "")
            self.report(f"CLAP: {self._describe(c)}, decay {c['decay']:.2f}s, speech {speech:.2f}"
                        f"{gap}")
        if self._claps and (c["at"] - self._claps[-1]) * FRAME_SECONDS > config.CLAP_MAX_GAP:
            self._claps = []
        self._claps.append(c["at"])

    def _speech(self, onset):
        """Silero's speech probability for the ~100 ms from just before onset."""
        heard = np.concatenate(self._recent)
        at = len(heard) - (self._end - onset) * FRAME
        start = max(min(at - audio.VAD_WINDOW // 3, len(heard) - audio.VAD_WINDOW), 0)
        window = heard[start:start + audio.VAD_WINDOW]
        if len(window) < audio.VAD_WINDOW:
            return 0.0
        return audio.speech_probability(window)

    def _double(self):
        if not self._claps or self._candidate:
            return False
        quiet = (self._frame - self._claps[-1]) * FRAME_SECONDS
        if len(self._claps) == 2:
            gap = (self._claps[1] - self._claps[0]) * FRAME_SECONDS
            # Long enough for a third clap at the same pace to show up.
            if quiet < min(config.CLAP_MAX_GAP, max(0.4, 1.5 * gap)):
                return False
            second, self._claps = self._claps[1], []
            if gap < config.CLAP_MIN_GAP:
                if self.report:
                    self.report(f"  two claps {gap:.2f}s apart: too close for a double clap")
                return False
            # The blocks after the one with the second clap in it.
            per_block = len(self._recent[-1]) // FRAME
            after = self._frame // per_block - second // per_block - 1
            self.preroll = list(self._recent)[len(self._recent) - after:] if after > 0 else []
            self.gap = gap
            if self.report:
                self.report(f"DOUBLE CLAP: {gap:.2f}s apart, quiet {quiet:.2f}s after")
            return True
        if quiet > config.CLAP_MAX_GAP:
            if len(self._claps) > 2 and self.report:
                self.report(f"  {len(self._claps)} claps in a row: not a double clap")
            self._claps = []
        return False


# After a double clap, "Daddy's home" or what Whisper makes of it ("Daddy is home.", "Daddies
# home!", "Daddy's here"), "I'm home", or "Wake up" (alone or before any of those). Like
# voice.py's local commands, the whole utterance must match, give or take a "hey", "Jarvis" or
# "sir", plus anything within PHRASE_CLOSE of "daddy s home" ("Daddy's hom").
_PHRASE = re.compile(r"(?:(?:hey|hi|ok|okay|well|honey|jarvis|yo|wake up) )*"
                     r"(?:(?:(?:daddy|daddie|dad)(?: s| is|s)?|i m|i am) (?:home|here|back)|"
                     r"wake up)"
                     r"(?: (?:jarvis|sir|now|again|baby))*")
PHRASE_CLOSE = 0.8
PHRASE_SECONDS = 4  # how long after the claps to wait for it to start
PHRASE_MAX_SECONDS = 6  # recording stops by then (counted from the claps): it's a short phrase


def _words(text):
    return " ".join(re.sub(r"[^a-z ]+", " ", text.lower().replace("'", " ")).split())


def is_homecoming(text):
    words = _words(text)
    return bool(_PHRASE.fullmatch(words)
                or difflib.SequenceMatcher(None, words, "daddy s home").ratio() >= PHRASE_CLOSE)


def heard_phrase(mic, transcribe, preroll=()):
    """After a double clap, record what's said next: (whether it's the phrase, the text, or None
    if nothing was said)."""
    utterance = audio.record_utterance(mic, transcribe, wait_seconds=PHRASE_SECONDS,
                                       preroll=preroll, max_seconds=PHRASE_MAX_SECONDS)
    if utterance is None:
        return False, None
    text, _, _ = utterance.transcribe()
    return is_homecoming(text), text


if __name__ == "__main__":
    phrase = "--phrase" in sys.argv[1:]
    if phrase:
        from jarvis import stt
        stt.get_model()
    started = time.perf_counter()
    claps = ClapDetector(report=lambda line: print(f"{time.perf_counter() - started:7.2f}  {line}",
                                                   flush=True))
    print(f"Listening for claps (CLAP_PEAK_DB {config.CLAP_PEAK_DB:g}, CLAP_MIN_DBFS "
          f"{config.CLAP_MIN_DBFS:g}, CLAP_HIGH_FREQ {config.CLAP_HIGH_FREQ:g}, CLAP_DECAY_SECONDS "
          f"{config.CLAP_DECAY_SECONDS:g}, gap {config.CLAP_MIN_GAP:g}-{config.CLAP_MAX_GAP:g}s). "
          "Ctrl+C to stop.")
    with audio.Mic() as mic:
        try:
            while True:
                if claps.heard(mic.read()) and phrase:
                    matched, text = heard_phrase(mic, stt.transcribe, claps.preroll)
                    print(f"  > {text!r}: {'HOMECOMING' if matched else 'not the phrase'}"
                          if text else "  (nothing said)", flush=True)
                    mic.clear()
                    claps.reset()
        except KeyboardInterrupt:
            pass
