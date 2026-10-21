"""Apple Calendar via iCloud CalDAV: read events and add new ones (never edits or deletes)."""

import threading
from datetime import date, datetime, timedelta

import caldav

from jarvis import config, notify
from jarvis.tools import tool
from jarvis.tools.clock import TZ

_calendars = None  # {display name: caldav.Calendar}
_lock = threading.Lock()  # voice and Discord may both ask at once


def _get_calendars():
    global _calendars
    if not (config.ICLOUD_USER and config.ICLOUD_APP_PASSWORD):
        raise RuntimeError("Apple Calendar isn't set up (ICLOUD_USER / ICLOUD_APP_PASSWORD in .env)")
    if _calendars is None:
        client = caldav.DAVClient("https://caldav.icloud.com", username=config.ICLOUD_USER,
                                  password=config.ICLOUD_APP_PASSWORD, features="icloud",
                                  timeout=20)
        _calendars = {c.get_display_name(): c for c in client.principal().calendars()
                      if _has_events(c)}
    return _calendars


def connect():
    with _lock:
        _get_calendars()


def _has_events(cal):
    # iCloud also lists Reminders lists as calendars; keep only ones that hold events.
    try:
        return "VEVENT" in cal.get_supported_components()
    except Exception:
        return True


def _local(value):
    """CalDAV values are dates (all-day) or datetimes, possibly UTC or naive."""
    if isinstance(value, datetime):
        return value.astimezone(TZ) if value.tzinfo else value.replace(tzinfo=TZ)
    return value


def _describe(start, end, title, location=""):
    if isinstance(start, datetime):
        when = f"{start:%a %b %d, %I:%M %p}".replace(" 0", " ") + f" to {end:%I:%M %p}".replace(" 0", " ")
    else:
        when = f"{start:%a %b %d} (all day)"
    return f"{title}, {when}" + (f", at {location}" if location else "")


def events(start, end):
    """Events overlapping [start, end) on every calendar, oldest first, as dicts with the
    start and end (datetimes, or dates for all-day events), title, calendar, location and uid."""
    found = []
    with _lock:
        for name, cal in _get_calendars().items():
            for ev in cal.search(start=start, end=end, event=True, expand=True):
                c = ev.icalendar_component
                s = _local(c.decoded("dtstart"))
                found.append({"start": s, "end": _local(c.decoded("dtend")) if "dtend" in c else s,
                              "title": str(c.get("summary", "(no title)")), "calendar": name,
                              "location": str(c.get("location", "")) or None,
                              "uid": str(c.get("uid", ""))})
    sort_key = lambda e: (e["start"] if isinstance(e["start"], datetime)
                          else datetime.combine(e["start"], datetime.min.time(), TZ))
    return sorted(found, key=sort_key)


@tool("List calendar events from the owner's Apple Calendar, oldest first.",
      start_date={"type": "string", "description": "First day, YYYY-MM-DD (local time)"},
      days={"type": "integer", "minimum": 1, "maximum": 31, "description": "How many days to cover"})
def get_events(start_date, days):
    start = datetime.combine(date.fromisoformat(start_date), datetime.min.time(), TZ)
    found = events(start, start + timedelta(days=days))
    return [{"title": e["title"], "calendar": e["calendar"],
             "when": _describe(e["start"], e["end"], "").removeprefix(", "),
             "location": e["location"]} for e in found] or "No events."


@tool("Add an event to the owner's Apple Calendar. Can't change or delete existing events. "
      "Calendars: Home (default), Work, Classes, Midterms, Queen's, Ultimate, Family.",
      title={"type": "string"},
      start={"type": "string", "description": "Local start, YYYY-MM-DDTHH:MM, or YYYY-MM-DD for all day"},
      duration_minutes={"type": "integer", "minimum": 1,
                        "description": "Length; 60 if not said. Ignored for all-day events"},
      location={"type": "string", "description": "Where, or empty"},
      calendar={"type": "string", "description": "Which calendar; empty for the default"})
def add_event(title, start, duration_minutes, location, calendar):
    if "T" in start:
        begin = datetime.fromisoformat(start).replace(tzinfo=TZ)
        finish = begin + timedelta(minutes=duration_minutes)
    else:
        begin = date.fromisoformat(start)
        finish = begin + timedelta(days=1)
    with _lock:
        cals = _get_calendars()
        # Match loosely ("queens" -> "Queen’s"); unknown or empty names go to the default.
        key = lambda n: "".join(ch for ch in n.lower() if ch.isalnum())
        by_key = {key(n): n for n in cals}
        name = (by_key.get(key(calendar)) or by_key.get(key(config.ICLOUD_CALENDAR))
                or next(iter(cals)))
        cal = cals[name]
        fields = {"dtstart": begin, "dtend": finish, "summary": title}
        if location:
            fields["location"] = location
        cal.add_event(**fields)
    summary = _describe(begin, finish, title, location)
    notify.record(f"📅 Added to {name}: {summary}")
    return f"Added to {name}: {summary}"
