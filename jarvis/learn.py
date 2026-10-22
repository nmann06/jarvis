"""Learning mode: the owner's feedback on Jarvis's replies, turned into lessons Jarvis follows.

Every reply Claude gives (voice, Discord, Claude sessions) is logged to data/exchanges.jsonl.
In a terminal, run
    .venv/bin/python -m jarvis.learn
to see each reply as it happens and type feedback on it: the exact words you wanted ("Your
first class is at eight thirty in Kingston Hall."), or a general note ("too long", "don't call
me sir so much"). Enter skips. Claude turns each piece of feedback into a short lesson, kept in
data/lessons.json and added to Jarvis's instructions from the next reply on, for that channel
(voice, Discord) or all of them. Start feedback with "=" to say it is the exact reply wanted.

Other commands:
    python -m jarvis.learn recent [N]   give feedback on the last N replies (default 5)
    python -m jarvis.learn lessons      list what Jarvis has learned
    python -m jarvis.learn forget ID    drop a lesson
    python -m jarvis.learn add TEXT     add a lesson yourself, for every channel
"""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime

from jarvis import config

log = logging.getLogger(__name__)

EXCHANGES = config.ROOT / "data" / "exchanges.jsonl"
LESSONS = config.ROOT / "data" / "lessons.json"
MAX_LESSONS = 40  # newest lessons given to Claude; older ones stay in the file
MAX_EXAMPLES = 8  # newest rewritten replies shown as examples
KEEP_EXCHANGES = 3000  # the log is cut back to this many lines when it passes 5 MB

_write = threading.Lock()  # voice, Discord and Claude sessions reply from different threads
_count = 0


# --- Jarvis's side: logging replies and reading lessons ---

def log_exchange(channel, heard, reply, said, tools, follow_up=False):
    """Record one reply for learning mode. said is everything Claude wrote (including what it
    said before tool calls), tools the calls it made. Never raises."""
    global _count
    try:
        with _write:
            _count += 1
            now = datetime.now()
            rec = {"id": f"{now:%Y%m%d-%H%M%S}-{_count}", "time": now.isoformat(timespec="seconds"),
                   "channel": channel, "heard": heard, "reply": reply,
                   "said": " ".join(s.strip() for s in said if s.strip()),
                   "tools": tools, "follow_up": follow_up}
            EXCHANGES.parent.mkdir(exist_ok=True)
            EXCHANGES.touch(mode=0o600)  # replies quote email and the like: owner only
            with open(EXCHANGES, "a") as f:
                f.write(json.dumps(rec) + "\n")
            if EXCHANGES.stat().st_size > 5_000_000:
                lines = EXCHANGES.read_text().splitlines()[-KEEP_EXCHANGES:]
                EXCHANGES.write_text("\n".join(lines) + "\n")
    except Exception:
        log.exception("couldn't log the exchange for learning mode")


def _load():
    try:
        return json.loads(LESSONS.read_text())
    except (FileNotFoundError, ValueError):
        return {"lessons": [], "next_id": 1}


def _save(data):
    LESSONS.parent.mkdir(exist_ok=True)
    tmp = LESSONS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, LESSONS)  # Jarvis may be reading it


def version():
    """Changes whenever the lessons do (so Jarvis knows to rebuild its instructions)."""
    try:
        return LESSONS.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def instructions(channel):
    """The lessons for a channel ("voice", "discord" or "claude"), as a section for the system
    prompt, or "" if there are none."""
    lessons = [l for l in _load()["lessons"] if l["channel"] in ("all", channel)][-MAX_LESSONS:]
    if not lessons:
        return ""
    lines = ["Lessons from the owner's feedback on your earlier replies. Follow them; where two "
             "disagree, the later one wins:"]
    lines += [f"- {l['lesson']}" for l in lessons]
    examples = [l["example"] for l in lessons if l.get("example")][-MAX_EXAMPLES:]
    if examples:
        lines.append("Replies the owner rewrote, to show the wording they want (the facts in "
                     "them were only true at the time):")
        lines += [f"- Owner: \"{e['heard']}\" You said: \"{e['was']}\" They wanted: \"{e['better']}\""
                  for e in examples]
    return "\n".join(lines)


# --- The owner's side: the terminal ---

TEACH = """You help a personal voice assistant called Jarvis learn from its owner's feedback. \
You get one exchange (what the owner said, what Jarvis replied, the tools it used, and whether \
it was spoken aloud or written in Discord) and the owner's feedback on the reply.

The feedback is either the exact reply the owner wanted (kind "rewrite": it reads like \
something Jarvis would say, in Jarvis's voice), or a comment about the reply (kind "note": \
"too long", "you should have checked OnQ", "don't say sir so much").

Write one lesson for Jarvis: one or two sentences addressed to Jarvis as "you", saying what to \
do differently in situations like this one. Make it general enough to apply to similar requests \
later, but specific about the change the owner wants. Leave out facts that will go stale \
(today's events, numbers) unless the feedback is about them. For a rewrite, work out what \
changed between Jarvis's reply and the one the owner wanted (length, wording, what was left out \
or added) and make that the lesson.

applies_to is "this channel" when the lesson is about how replies sound or look there (spoken \
style, length, formatting), else "all channels" (what to check, what to include, facts about the \
owner's preferences)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["rewrite", "note"]},
        "lesson": {"type": "string"},
        "applies_to": {"type": "string", "enum": ["this channel", "all channels"]},
    },
    "required": ["kind", "lesson", "applies_to"],
    "additionalProperties": False,
}

_client = None


def teach(exchange, feedback):
    """Turn the owner's feedback on one exchange into a lesson, save it and return it."""
    global _client
    import anthropic
    _client = _client or anthropic.Anthropic()
    exact = feedback.startswith("=")
    feedback = feedback.lstrip("=").strip()
    shown = {"medium": "spoken aloud" if exchange["channel"] == "voice" else
             "written in Discord" if exchange["channel"] == "discord" else
             "written to another Claude assistant",
             "owner said": exchange["heard"],
             "Jarvis replied": exchange["said"] or exchange["reply"] or
             "(nothing: it decided this wasn't said to it)",
             "tools used": exchange["tools"],
             "owner's feedback": feedback}
    if exact:
        shown["note"] = "The owner marked this feedback as the exact reply they wanted."
    response = _client.messages.create(
        model=config.CLAUDE_MODEL, max_tokens=1000, system=TEACH,
        messages=[{"role": "user", "content": json.dumps(shown, indent=1)}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        thinking={"type": "disabled"})
    out = json.loads(next(b.text for b in response.content if b.type == "text"))
    rewrite = exact or out["kind"] == "rewrite"
    data = _load()
    lesson = {"id": data["next_id"], "time": datetime.now().isoformat(timespec="seconds"),
              "channel": exchange["channel"] if out["applies_to"] == "this channel" else "all",
              "lesson": out["lesson"].strip(), "feedback": feedback, "exchange": exchange["id"],
              "example": {"heard": exchange["heard"], "was": exchange["said"] or exchange["reply"] or "",
                          "better": feedback} if rewrite else None}
    data["lessons"].append(lesson)
    data["next_id"] += 1
    _save(data)
    return lesson


def add(text):
    data = _load()
    lesson = {"id": data["next_id"], "time": datetime.now().isoformat(timespec="seconds"),
              "channel": "all", "lesson": text.strip(), "feedback": text.strip(),
              "exchange": None, "example": None}
    data["lessons"].append(lesson)
    data["next_id"] += 1
    _save(data)
    return lesson


def forget(lesson_id):
    data = _load()
    kept = [l for l in data["lessons"] if l["id"] != lesson_id]
    if len(kept) == len(data["lessons"]):
        return False
    data["lessons"] = kept
    _save(data)
    return True


def _exchanges():
    try:
        lines = EXCHANGES.read_text().splitlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def _show(ex):
    when = ex["time"][11:16]
    print(f"\n[{ex['channel']} {when}{', follow-up' if ex.get('follow_up') else ''}]")
    print(f"  You:    {ex['heard']}")
    print(f"  Jarvis: {ex['said'] or ex['reply'] or '(stayed quiet: decided it was not said to it)'}")
    if ex["tools"]:
        print("  Tools:  " + ", ".join(t["name"] for t in ex["tools"]))


def _ask_feedback(ex, last):
    """Ask about one exchange until the owner moves on with Enter. Returns the last lesson
    made (or the one before, so "undo" at the next reply can still find it)."""
    _show(ex)
    prompt = "  Feedback (Enter skips): "
    while True:
        try:
            feedback = input(prompt).strip()
        except EOFError:
            raise KeyboardInterrupt
        if not feedback:
            return last
        if feedback.lower() == "undo":
            if last and forget(last["id"]):
                print(f"  Dropped lesson #{last['id']}.")
                last = None
            else:
                print("  Nothing to undo.")
            continue
        try:
            lesson = teach(ex, feedback)
        except Exception as e:
            print(f"  Couldn't make a lesson from that ({type(e).__name__}: {e}). Try again?")
            continue
        where = "everywhere" if lesson["channel"] == "all" else f"for {lesson['channel']}"
        print(f"  Learned #{lesson['id']} ({where}): {lesson['lesson']}")
        last, prompt = lesson, "  More feedback, 'undo' to drop that lesson, or Enter to go on: "


def follow():
    """Show each new reply as it happens and ask for feedback on it (the last one first)."""
    print("Learning mode. Each reply Jarvis gives shows up here: type the reply you wanted (start "
          "with = to mark it as exact) or a note about it. Ctrl+C to stop.")
    seen = _exchanges()
    pos = EXCHANGES.stat().st_size if EXCHANGES.exists() else 0
    last = None
    if seen:
        last = _ask_feedback(seen[-1], last)
    while True:
        size = EXCHANGES.stat().st_size if EXCHANGES.exists() else 0
        if size < pos:  # the log was cut back
            pos = 0
        if size > pos:
            with open(EXCHANGES) as f:
                f.seek(pos)
                chunk = f.read()
            complete, _, partial = chunk.rpartition("\n")
            pos += len((complete + "\n").encode()) if complete else 0
            for line in complete.splitlines():
                if line.strip():
                    last = _ask_feedback(json.loads(line), last)
            continue
        time.sleep(0.5)


def recent(n):
    last = None
    for ex in _exchanges()[-n:]:
        last = _ask_feedback(ex, last)


def show_lessons():
    lessons = _load()["lessons"]
    if not lessons:
        print("No lessons yet.")
    for l in lessons:
        where = "all" if l["channel"] == "all" else l["channel"]
        print(f"#{l['id']} [{where}, {l['time'][:10]}] {l['lesson']}")
        if l.get("example"):
            print(f"      wanted: {l['example']['better']}")


def main(args):
    try:
        if not args:
            follow()
        elif args[0] == "recent":
            recent(int(args[1]) if len(args) > 1 else 5)
        elif args[0] == "lessons":
            show_lessons()
        elif args[0] == "forget" and len(args) == 2:
            print("Dropped." if forget(int(args[1].lstrip("#"))) else "No lesson with that number.")
        elif args[0] == "add" and len(args) > 1:
            lesson = add(" ".join(args[1:]))
            print(f"Added #{lesson['id']}: {lesson['lesson']}")
        else:
            print(__doc__)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
