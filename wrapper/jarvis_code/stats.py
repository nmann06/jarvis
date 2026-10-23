"""Claude Code's usage stats, as its /stats screen shows them: its own cache of past days
(~/.claude/stats-cache.json), plus the days since, counted from the saved conversations.

load() reads; summary() works out what one date range shows. Pure and file-only, so the
window runs it off the main loop and the tests feed it a made-up cache.
"""

import datetime as dt
import glob
import json
import os
from dataclasses import dataclass, field

CLAUDE_DIR = os.path.expanduser("~/.claude")

RANGES = [("all", "All time", None), ("7d", "Last 7 days", 7), ("30d", "Last 30 days", 30)]


@dataclass
class Day:
    messages: int = 0
    sessions: int = 0
    tools: int = 0
    tokens: dict = field(default_factory=dict)  # model -> tokens


@dataclass
class Stats:
    days: dict                 # "YYYY-MM-DD" -> Day
    models: dict               # model -> {"input", "output", "cache_read", "cache_write"}
    sessions: int
    longest_ms: int
    first: dt.date | None
    today: dt.date


def _date(timestamp):
    try:
        return dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone().date()
    except (AttributeError, ValueError):
        return None


def load(claude_dir=None, today=None):
    claude_dir = claude_dir or CLAUDE_DIR
    today = today or dt.date.today()
    try:
        with open(os.path.join(claude_dir, "stats-cache.json")) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    days, models = {}, {}
    for a in cache.get("dailyActivity") or []:
        day = days.setdefault(a.get("date"), Day())
        day.messages += a.get("messageCount") or 0
        day.sessions += a.get("sessionCount") or 0
        day.tools += a.get("toolCallCount") or 0
    for t in cache.get("dailyModelTokens") or []:
        day = days.setdefault(t.get("date"), Day())
        for model, n in (t.get("tokensByModel") or {}).items():
            day.tokens[model] = day.tokens.get(model, 0) + (n or 0)
    for model, u in (cache.get("modelUsage") or {}).items():
        models[model] = {"input": u.get("inputTokens") or 0, "output": u.get("outputTokens") or 0,
                         "cache_read": u.get("cacheReadInputTokens") or 0,
                         "cache_write": u.get("cacheCreationInputTokens") or 0}
    sessions = cache.get("totalSessions") or 0
    longest = (cache.get("longestSession") or {}).get("duration") or 0
    first = _date(cache.get("firstSessionDate") or "")
    computed = None
    try:
        computed = dt.date.fromisoformat(cache.get("lastComputedDate") or "")
    except ValueError:
        pass
    new_sessions, new_longest, new_first = _scan(os.path.join(claude_dir, "projects"), computed, days, models)
    first = min(d for d in (first, new_first) if d) if (first or new_first) else None
    return Stats(days, models, sessions + new_sessions, max(longest, new_longest), first, today)


def _scan(projects, after, days, models):
    """Count the days after `after` (all of them if None) from the saved conversations."""
    start = dt.datetime.combine(after + dt.timedelta(days=1), dt.time()).timestamp() if after else 0
    spans, day_sessions, seen = {}, {}, set()
    for path in glob.glob(os.path.join(projects, "*", "*.jsonl")):
        try:
            if os.path.getmtime(path) < start:
                continue
            f = open(path, errors="replace")
        except OSError:
            continue
        with f:
            for line in f:
                if '"timestamp"' not in line or ('"user"' not in line and '"assistant"' not in line):
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                kind = record.get("type")
                date = _date(record.get("timestamp") or "")
                if kind not in ("user", "assistant") or date is None or (after and date <= after):
                    continue
                key = date.isoformat()
                day = days.setdefault(key, Day())
                session = record.get("sessionId")
                if session:
                    day_sessions.setdefault(key, set()).add(session)
                    stamp = dt.datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).timestamp()
                    low, high = spans.get(session, (stamp, stamp))
                    spans[session] = (min(low, stamp), max(high, stamp))
                if not record.get("isMeta"):
                    day.messages += 1
                if kind != "assistant":
                    continue
                message = record.get("message") or {}
                day.tools += sum(1 for b in message.get("content") or []
                                 if isinstance(b, dict) and b.get("type") == "tool_use")
                usage, model, mid = message.get("usage") or {}, message.get("model"), message.get("id")
                if not usage or not model or model == "<synthetic>" or mid in seen:
                    continue
                seen.add(mid)  # every block of a reply repeats the same usage
                parts = {"input": usage.get("input_tokens") or 0, "output": usage.get("output_tokens") or 0,
                         "cache_read": usage.get("cache_read_input_tokens") or 0,
                         "cache_write": usage.get("cache_creation_input_tokens") or 0}
                day.tokens[model] = day.tokens.get(model, 0) + sum(parts.values())
                totals = models.setdefault(model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
                for k, v in parts.items():
                    totals[k] += v
    for key, ids in day_sessions.items():
        days[key].sessions = max(days[key].sessions, len(ids))
    longest = max((int((high - low) * 1000) for low, high in spans.values()), default=0)
    first = min((dt.date.fromtimestamp(low) for low, _ in spans.values()), default=None)
    return len(spans), longest, first


def summary(stats, range_key="all"):
    """What the Overview shows for one range: totals, streaks and the busiest day."""
    span = dict((k, n) for k, _, n in RANGES).get(range_key)
    since = stats.today - dt.timedelta(days=span - 1) if span else None
    days = {k: d for k, d in stats.days.items() if since is None or dt.date.fromisoformat(k) >= since}
    active = sorted(dt.date.fromisoformat(k) for k, d in days.items() if d.messages or d.tokens)
    start = since or stats.first or (active[0] if active else stats.today)
    total_days = (stats.today - start).days + 1
    by_model = {}
    for d in days.values():
        for model, n in d.tokens.items():
            by_model[model] = by_model.get(model, 0) + n
    longest_streak = streak = 0
    previous = None
    for date in active:
        streak = streak + 1 if previous and (date - previous).days == 1 else 1
        longest_streak = max(longest_streak, streak)
        previous = date
    current = 0
    date = stats.today if stats.today in active else stats.today - dt.timedelta(days=1)
    active_set = set(active)
    while date in active_set:
        current += 1
        date -= dt.timedelta(days=1)
    busiest = max(days.items(), key=lambda kv: kv[1].messages, default=(None, None))[0]
    return {
        "favorite": max(by_model, key=by_model.get) if by_model else None,
        "tokens": sum(by_model.values()),
        "by_model": by_model,
        "sessions": stats.sessions if span is None else sum(d.sessions for d in days.values()),
        "longest_ms": stats.longest_ms,
        "active_days": len(active), "total_days": max(total_days, 1),
        "longest_streak": longest_streak, "current_streak": current,
        "busiest": dt.date.fromisoformat(busiest) if busiest else None,
    }


def heatmap_levels(stats, weeks=52):
    """A week-per-column grid (Sunday on top) of activity levels 0-4 ending this week, and the
    month each column starts, as Claude Code's /stats draws it."""
    end = stats.today
    since_sunday = (end.weekday() + 1) % 7
    start = end - dt.timedelta(days=since_sunday + 7 * (weeks - 1))  # a Sunday
    counts = [d.messages for d in stats.days.values() if d.messages]
    counts.sort()

    def level(n):
        if not n:
            return 0
        if not counts:
            return 1
        rank = sum(1 for c in counts if c <= n) / len(counts)
        return 1 if rank <= 0.25 else 2 if rank <= 0.5 else 3 if rank <= 0.75 else 4

    grid = [[None] * weeks for _ in range(7)]
    months = [""] * weeks
    for week in range(weeks):
        for weekday in range(7):
            date = start + dt.timedelta(days=week * 7 + weekday)
            if date > end:
                continue
            day = stats.days.get(date.isoformat())
            grid[weekday][week] = level(day.messages if day else 0)
            if date.day <= 7 and weekday == 0:
                months[week] = date.strftime("%b")
    return grid, months


def human_tokens(n):
    for unit, size in (("b", 1e9), ("m", 1e6), ("k", 1e3)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return str(int(n))


def human_duration(ms):
    seconds = int(ms // 1000)
    hours, minutes, secs = seconds // 3600, seconds % 3600 // 60, seconds % 60
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


# A comparison for the bottom of the Overview, like Claude Code's. (Film runtimes, minutes;
# books, tokens, at about 1.3 tokens a word.)
FILMS = [("Iron Man", 126), ("The Avengers", 143), ("Avengers: Endgame", 181)]
BOOKS = [("A Christmas Carol", 37_000), ("The Hobbit", 125_000), ("War and Peace", 780_000)]


def fun_fact(stats):
    io = sum(m["input"] + m["output"] for m in stats.models.values())
    for title, size in reversed(BOOKS):
        if io >= 2 * size:
            return f"Your input and output are ~{io // size}x the tokens in {title}"
    minutes = stats.longest_ms / 60000
    for title, length in FILMS:
        if minutes >= 2 * length:
            return f"Your longest session is ~{int(minutes // length)}x longer than watching {title}"
    return ""
