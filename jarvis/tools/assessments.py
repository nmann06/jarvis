"""Tests, quizzes, midterms and exams, from three places at once: the syllabi, the owner's
calendar and OnQ/Smith Learning quizzes. Merged, so each test is said once, and counted by kind.

Syllabus dates don't change during a term, so they're read once into data/assessments.csv
rather than on every question (Claude reads each cached syllabus and lists its tests):
    .venv/bin/python -m jarvis.tools.assessments build
Run it again at the start of a new term, or after a syllabus changes.
"""

import csv
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta

from jarvis import config
from jarvis.tools import icloud_calendar, onq, tool
from jarvis.tools.clock import TZ

log = logging.getLogger(__name__)

CSV = config.ROOT / "data" / "assessments.csv"
FIELDS = ["course", "kind", "title", "date", "time", "end_date", "location", "weight", "note"]
KINDS = ["quiz", "midterm", "test", "exam"]
# Monday of week 1 of the term, for syllabi that only give "Monday of Week 7": ELEC 324's
# syllabus has "Week 7 (Oct. 26)".
TERM_WEEK1 = date(2026, 9, 14)
EXTRACT_MODEL = "claude-opus-5-5"  # run once a term, so the best reader
TEST_CALENDARS = {"Midterms"}
TEST_WORDS = re.compile(r"\b(quiz|quizzes|midterm|test|exam|final)\b", re.I)

EXTRACT = f"""List every quiz, test, midterm and exam in this course syllabus that has a date \
or a week, for the term starting the week of {TERM_WEEK1:%B %d, %Y}. Week 1 starts Monday \
{TERM_WEEK1:%B %d, %Y}, so "Monday of Week 7" is {TERM_WEEK1 + timedelta(weeks=6):%B %d}. \
kind is the word the syllabus uses for it (quiz, midterm, test or exam). Give date as \
YYYY-MM-DD only when the syllabus gives a day or a week for it; a final exam "scheduled by the \
Exams Office" gets an empty date. For something open over several days (an online quiz), date \
is the day it opens and end_date the day it closes. time is HH:MM (24 hour) when given, for \
the owner's section if there are several (say which in note). Never guess a date. Answer \
only by calling record_assessments once."""

RECORD = {
    "name": "record_assessments",
    "description": "Record the course's dated quizzes, tests, midterms and exams.",
    "input_schema": {"type": "object", "required": ["items"], "properties": {"items": {
        "type": "array", "items": {
            "type": "object", "required": ["kind", "title", "date"],
            "properties": {
                "kind": {"type": "string", "enum": KINDS},
                "title": {"type": "string", "description": "As the syllabus names it, e.g. 'Test 1'"},
                "date": {"type": "string", "description": "YYYY-MM-DD, or empty"},
                "time": {"type": "string", "description": "HH:MM, or empty"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD, or empty"},
                "location": {"type": "string"},
                "weight": {"type": "string", "description": "e.g. '12.5%'"},
                "note": {"type": "string"}}}}}},
}


def build():
    """Read each current course's syllabus with Claude and write CSV."""
    import anthropic
    client = anthropic.Anthropic()

    def extract(course):
        _, text = onq.syllabus(course)
        r = client.messages.create(
            model=EXTRACT_MODEL, max_tokens=16000, tools=[RECORD],
            messages=[{"role": "user", "content": f"{EXTRACT}\n\n<syllabus>\n{text}\n</syllabus>"}])
        items = next(b.input for b in r.content if b.type == "tool_use")["items"]
        return [{"course": onq.short_code(course)} | {f: i.get(f, "") for f in FIELDS[1:]}
                for i in items]

    rows = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for course, found in zip(courses := onq.courses(), pool.map(_try(extract), courses)):
            print(f"{onq.short_code(course)}: {len(found)}", file=sys.stderr)
            rows += found
    CSV.parent.mkdir(exist_ok=True)
    with CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["date"] or "9999", r["course"])))
    return rows


def _try(fn):
    def run(course):
        try:
            return fn(course)
        except Exception as e:  # one course without a readable syllabus shouldn't stop the rest
            print(f"{course['name']}: {e}", file=sys.stderr)
            return []
    return run


def _number(text):
    """Course number ('324') in a title like '324 quiz 1' or a code like 'ELEC 324'."""
    m = re.search(r"\b(\d{3})\b", text)
    return m[1] if m else None


def _ordinal(title):
    m = re.search(r"(?:#|\b)(\d{1,2})\b(?!\d)", re.sub(r"\b\d{3}\b", "", title))
    return m[1] if m else None


def _kind(title, calendar=None):
    m = TEST_WORDS.search(title)
    if m:
        word = m[1].lower()
        return {"quizzes": "quiz", "final": "exam"}.get(word, word)
    return "midterm" if calendar in TEST_CALENDARS else "test"


def _syllabus(start, end):
    if not CSV.exists():
        return []
    found = []
    for r in csv.DictReader(CSV.open()):
        if not r["date"]:
            continue
        day = date.fromisoformat(r["date"])
        last = date.fromisoformat(r["end_date"]) if r["end_date"] else day
        if last < start.date() or day >= end.date():
            continue
        at = datetime.combine(day, time.fromisoformat(r["time"]) if r["time"] else time(), TZ)
        found.append({"course": r["course"], "kind": r["kind"], "title": r["title"], "start": at,
                      "timed": bool(r["time"]), "closes": last if r["end_date"] else None,
                      "location": r["location"] or None, "weight": r["weight"] or None,
                      "sources": ["syllabus"]})
    return found


def _calendar(start, end):
    found = []
    for e in icloud_calendar.events(start, end):
        if e["calendar"] not in TEST_CALENDARS and not TEST_WORDS.search(e["title"]):
            continue
        timed = isinstance(e["start"], datetime)
        found.append({"course": _number(e["title"]), "kind": _kind(e["title"], e["calendar"]),
                      "title": e["title"], "timed": timed,
                      "start": e["start"] if timed else datetime.combine(e["start"], time(), TZ),
                      "end": e["end"] if timed else None, "location": e["location"],
                      "sources": [f"{e['calendar']} calendar"]})
    return found


def _onq(start, end):
    return [{"course": i["code"], "kind": _kind(i["title"]) if i["kind"] != "quiz" else "quiz",
             "title": i["title"], "start": i["when"], "timed": True, "closes": i["when"].date(),
             "sources": ["OnQ"]}
            for i in onq.due_items(start, end)
            if i["kind"] == "quiz" or (i["kind"] == "event" and TEST_WORDS.search(i["title"]))]


def _same(a, b):
    """Whether two entries are the same test: same course, and the same day or the same
    numbered test of the same kind ("Online Quiz #1" opening Oct 26 and closing Nov 1)."""
    if not a["course"] or _number(a["course"]) != _number(b["course"]):
        return False
    days = lambda t: {t["start"].date(), t.get("closes")} - {None}
    if days(a) & days(b):
        return True
    return a["kind"] == b["kind"] and _ordinal(a["title"]) and _ordinal(a["title"]) == _ordinal(b["title"])


def tests(start, end):
    """Every test between start and end, merged across sources, in time order."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        cal, online = pool.submit(_calendar, start, end), pool.submit(_onq, start, end)
        entries = _syllabus(start, end) + cal.result() + online.result()
    merged = []
    for e in entries:  # syllabus first: its name for a test wins ("Test 1", not "324 quiz 1")
        same = next((m for m in merged if _same(m, e)), None)
        if same is None:
            merged.append(e)
            continue
        same["sources"] += e["sources"]
        # The owner's calendar has the time and room for this term (a syllabus may be last year's).
        if e["timed"] and ("calendar" in e["sources"][0] or not same["timed"]):
            same.update(start=e["start"], end=e.get("end"), timed=True)
        same["location"] = same.get("location") or e.get("location")
        if e.get("closes") and e["start"].date() != e["closes"]:
            same["closes"] = max(same.get("closes") or e["closes"], e["closes"])
    return sorted(merged, key=lambda t: t["start"])


def _plural(n, kind):
    return f"{n} {kind}" + ("" if n == 1 else "zes" if kind == "quiz" else "s")


def _when(t):
    s = t["start"]
    text = f"{s:%a %b} {s.day}" + (f", {s:%I:%M %p}".replace(" 0", " ") if t["timed"] else "")
    if t.get("end"):
        text += f" to {t['end']:%I:%M %p}".replace(" 0", " ")
    if t.get("closes") and t["closes"] != s.date():
        text = f"opens {text}, closes {t['closes']:%a %b} {t['closes'].day}"
    return text


@tool("The owner's tests, quizzes, midterms and exams, from the course syllabi, every calendar "
      "and OnQ quizzes at once, with duplicates merged. Use this for any question about tests, "
      "quizzes, midterms or exams: they all mean the same thing.",
      start_date={"type": "string", "description": "First day, YYYY-MM-DD (local time)"},
      days={"type": "integer", "minimum": 1, "maximum": 120, "description": "How many days to cover"})
def get_tests(start_date, days):
    start = datetime.combine(date.fromisoformat(start_date), time(), TZ)
    found = tests(start, start + timedelta(days=days))
    counts = {k: sum(t["kind"] == k for t in found) for k in KINDS}
    parts = [_plural(n, k) for k, n in counts.items() if n]
    return {
        "summary": (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else
                    parts[0] if parts else "none"),
        "tests": [{"what": f"{t['course']} {t['title']}" if t["course"] and _number(t["course"])
                   not in t["title"] else t["title"], "kind": t["kind"], "when": _when(t),
                   "location": t.get("location"), "weight": t.get("weight"),
                   "from": ", ".join(t["sources"])} for t in found],
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    if sys.argv[1:] == ["build"]:
        rows = build()
        print(f"{len(rows)} tests written to {CSV}")
    else:
        start = sys.argv[1] if len(sys.argv) > 1 else f"{datetime.now(TZ):%Y-%m-%d}"
        print(json.dumps(get_tests(start, int(sys.argv[2]) if len(sys.argv) > 2 else 7), indent=1))
