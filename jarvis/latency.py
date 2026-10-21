"""Where the time goes in each voice turn, from the owner going quiet to Jarvis's voice.

The voice loop notes moments on a Turn as they happen (recording stopped, transcript ready,
each Claude request and tool, first sentence, first sound). When the turn is over it logs one
line, whose parts add up to the total, e.g.

  latency 1.61s: pause 0.56 + stt 0.00 + claude 0.79 + sentence 0.09 + voice 0.08 + audio 0.09
    [answered, quick end; stt 0.26s, all in the pause; claude 6208 cached + 52 new in, 18 out]

and appends the whole record (every request and tool, token counts) to data/latency.jsonl.
The parts:
  pause     waiting after the owner stopped talking, to be sure they had finished
  stt       transcribing, beyond what was already done during that pause
  claude    a Claude request, from sending it to the first word of the reply (or, as claude#1
            etc., the whole request when it ended in a tool call)
  <tool>    a tool Claude called, e.g. get_events
  sentence  Claude writing the rest of the first sentence (speech goes a sentence at a time)
  voice     Piper turning that sentence into audio
  audio     the sound card starting to play it

Run: python -m jarvis.latency [N]  to print the last N turns and the typical size of each part.
"""

import json
import logging
import re
import statistics
import sys
import time
from datetime import datetime, timedelta

from jarvis import config

log = logging.getLogger("jarvis.latency")

LOG_FILE = config.ROOT / "data" / "latency.jsonl"


class Turn:
    """Timing of one voice turn. Times are time.perf_counter() values."""

    def __init__(self, utterance, follow_up=False):
        self.start = utterance.speech_end  # the owner stopped talking (to within a block)
        self.when = datetime.now() - timedelta(seconds=time.perf_counter() - self.start)
        self.marks = {"recorded": utterance.ended}
        self.endpoint, self.audio_seconds, self.follow_up = (utterance.endpoint, utterance.seconds,
                                                             follow_up)
        self.heard = self.reply = None
        self.stt = None  # (started, finished)
        self.rounds, self.tools = [], []
        self.outcome = "error"  # until the voice loop says otherwise

    def mark(self, name, at=None):
        """Note when something first happened: now, or at the given perf_counter time."""
        self.marks.setdefault(name, time.perf_counter() if at is None else at)

    def transcribed(self, text, started, finished):
        self.heard, self.stt = text, (started, finished)
        self.mark("heard")

    def claude(self):
        """A Claude request being sent now. Returns a dict for llm.py to fill in: first_token,
        first_text and done times, and the usage numbers."""
        request = {"sent": time.perf_counter()}
        self.rounds.append(request)
        return request

    def tool(self, name, started, finished, error=False):
        self.tools.append({"name": name, "round": len(self.rounds), "started": started,
                           "finished": finished, "error": error})

    def parts(self):
        """[(label, seconds)] along the way from speech end to the first sound (or to the last
        thing that happened, if nothing was said). They add up to the total."""
        text = self.marks.get("text")  # first words handed to the voice
        speaking = None  # the request whose words were spoken first
        if text is not None:
            speaking = next((r for r in reversed(self.rounds) if r["sent"] <= text), None)
        path = [("pause", self.marks["recorded"]), ("stt", self.marks.get("heard"))]
        for i, r in enumerate(self.rounds, 1):
            name = f"claude#{i}" if len(self.rounds) > 1 else "claude"
            if r is speaking:
                path.append((name, r.get("first_text") or r.get("done")))
                break
            path.append((name, r.get("done")))
            path += [(t["name"], t["finished"]) for t in self.tools if t["round"] == i]
        if text is not None:
            path += [("sentence", text), ("voice", self.marks.get("synth")),
                     ("audio", self.marks.get("sound"))]
        parts, last = [], self.start
        for label, at in path:
            if at is None:
                continue
            at = max(at, last)
            parts.append((label, at - last))
            last = at
        return parts

    def record(self):
        parts = self.parts()
        stt = None
        if self.stt:
            started, finished = self.stt
            stt = {"s": finished - started,
                   "in_pause_s": max(0.0, min(finished, self.marks["recorded"]) - started)}
        return {
            "time": self.when.isoformat(timespec="seconds"),
            "heard": self.heard, "reply": self.reply, "outcome": self.outcome,
            "follow_up": self.follow_up, "endpoint": self.endpoint,
            "audio_s": round(self.audio_seconds, 2),
            # Speech end to the first sound; None when nothing was said.
            "total_s": _round(sum(s for _, s in parts)) if "sound" in self.marks else None,
            "parts": [[label, _round(s)] for label, s in parts],
            "stt": stt and {k: _round(v) for k, v in stt.items()},
            "claude": [{"to_first_token_s": _since(r, "first_token"),
                        "to_first_word_s": _since(r, "first_text"), "s": _since(r, "done"),
                        **{k: r[k] for k in ("in", "cached", "written", "out", "stop") if k in r}}
                       for r in self.rounds],
            "tools": [{"name": t["name"], "s": _round(t["finished"] - t["started"]),
                       "error": t["error"]} for t in self.tools],
        }

    def finish(self):
        """Log the turn and append it to LOG_FILE. Never raises: timing mustn't break a turn."""
        try:
            rec = self.record()
            log.info("%s", summary(rec))
            LOG_FILE.parent.mkdir(exist_ok=True)
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            log.exception("couldn't log turn timing")


def _round(seconds):
    return round(seconds, 3)


def _since(request, key):
    return _round(request[key] - request["sent"]) if key in request else None


def summary(rec):
    """One log line for a turn record."""
    parts = " + ".join(f"{label} {s:.2f}" for label, s in rec["parts"])
    head = (f"latency {rec['total_s']:.2f}s" if rec["total_s"] is not None
            else f"latency (nothing said, {rec['outcome']})")
    notes = [rec["outcome"] if rec["total_s"] is not None else None,
             f"{rec['endpoint']} end" if rec["endpoint"] != "pause" else None,
             "follow-up" if rec["follow_up"] else None]
    if rec["stt"]:
        hidden = rec["stt"]["in_pause_s"]
        notes.append(f"stt {rec['stt']['s']:.2f}s, " + ("all in the pause" if hidden >= rec["stt"]["s"]
                                                        else f"{hidden:.2f}s in the pause"))
    for i, c in enumerate(rec["claude"], 1):
        if "in" in c:
            notes.append(f"claude{f'#{i}' if len(rec['claude']) > 1 else ''} "
                         f"{c['cached']} cached + {c['in'] + c['written']} new in, {c['out']} out")
    return f"{head}: {parts} [{'; '.join(n for n in notes if n)}]"


def _group(label):
    if label.startswith("claude"):
        return "claude"
    return label if label in ("pause", "stt", "sentence", "voice", "audio") else "tools"


def report(n=20):
    """Print the last n turns and the median of each part over those that got a spoken reply."""
    try:
        lines = LOG_FILE.read_text().splitlines()
    except FileNotFoundError:
        print(f"No turns logged yet ({LOG_FILE}).")
        return
    recs = [json.loads(line) for line in lines[-n:] if line.strip()]
    for rec in recs:
        said = re.sub(r"\s+", " ", rec["heard"] or "")[:60]
        total = f"{rec['total_s']:5.2f}s" if rec["total_s"] is not None else "   -  "
        parts = " + ".join(f"{label} {s:.2f}" for label, s in rec["parts"])
        print(f"{rec['time'][5:16].replace('T', ' ')}  {total}  {parts}  ({rec['outcome']}) {said!r}")
    spoken = [r for r in recs if r["total_s"] is not None]
    if not spoken:
        return
    groups = ["pause", "stt", "claude", "tools", "sentence", "voice", "audio"]
    sums = {g: [] for g in groups}
    for rec in spoken:
        per = dict.fromkeys(groups, 0.0)
        for label, s in rec["parts"]:
            per[_group(label)] += s
        for g in groups:
            sums[g].append(per[g])
    print(f"\nMedian over {len(spoken)} spoken replies: "
          f"{statistics.median(r['total_s'] for r in spoken):.2f}s to voice")
    print("  " + "   ".join(f"{g} {statistics.median(v):.2f}" for g, v in sums.items()))


if __name__ == "__main__":
    report(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
