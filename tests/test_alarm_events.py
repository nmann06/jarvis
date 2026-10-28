"""A morning alarm against the day's first event: set_alarm's heads-up and goodnight's
mention, with a fake calendar and fake alarms.

Usage: python -m unittest tests.test_alarm_events (sets no real alarm, switches no light)
"""

import threading
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from jarvis import alarms, routines
from jarvis.tools.clock import TZ

NOW = datetime(2026, 10, 28, 22, 30, tzinfo=TZ)  # Wednesday evening
TOMORROW = date(2026, 10, 29)


def at(h, m=0, day=TOMORROW):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


def event(title, h, m=0, calendar="Queen's", hours=1, location=None, day=TOMORROW):
    return {"title": title, "calendar": calendar, "start": at(h, m, day),
            "end": at(h, m, day) + timedelta(hours=hours), "location": location, "uid": title}


LAB = event("ELEC 353 003", 8, calendar="Classes", hours=3, location="Beamish-Munro Hall 212")
LECTURE = event("ELEC 326 003", 8, calendar="Classes", location="Dunning Hall AUD")
MEETING = event("Meeting with Kelly Saunders", 9, calendar="Work")
ALL_DAY = {"title": "Reading week", "calendar": "Home", "start": TOMORROW,
           "end": TOMORROW + timedelta(days=1), "location": None, "uid": "all-day"}
DUE = event("Lab report due", 8, calendar="Queen's")
QUIZ = event("326 Quiz 2", 9, 30, calendar="Midterms")


class Fake:
    """Replaces the alarm store, the calendar and the clock around one test."""

    def __init__(self, events=(), alarm_times=(), calendar_error=None):
        self.stored = [{"id": i + 1, "at": t, "sound": "classic"} for i, t in enumerate(alarm_times)]
        self.events, self.calendar_error = list(events), calendar_error

    def add(self, when, sound_name=None):
        alarm = {"id": len(self.stored) + 1, "at": when, "sound": sound_name or "classic"}
        self.stored.append(alarm)
        return alarm

    def calendar(self, start, end):
        if self.calendar_error:
            raise self.calendar_error
        return [e for e in self.events
                if start.date() <= (e["start"].date() if isinstance(e["start"], datetime)
                                    else e["start"]) < end.date()]

    def __enter__(self):
        self.patches = [
            mock.patch.object(alarms, "alarms", lambda: sorted(self.stored, key=lambda a: a["at"])),
            mock.patch.object(alarms, "add", self.add),
            mock.patch("jarvis.tools.icloud_calendar.events", self.calendar),
            mock.patch("jarvis.tools.kasa.switch_all", lambda state: "Lights off, sir."),
        ]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


def set_alarm(fake, when, channel="voice", now=NOW):
    with fake:
        return alarms._set(when, None, now, channel)


def goodnight(fake, channel="voice", now=NOW):
    with fake, mock.patch("jarvis.routines.datetime") as dt:
        dt.now.return_value = now
        return routines.goodnight(channel)


class SettingAlarms(unittest.TestCase):
    def test_lab_alarm_too_close(self):
        reply = set_alarm(Fake([LAB]), at(7, 45))
        self.assertEqual(reply, "Alarm set for seven forty five tomorrow. Heads up, sir: your ELEC "
                                "three fifty three lab starts at eight o'clock, so that's less "
                                "than half an hour before it.")

    def test_lab_alarm_early_enough(self):
        self.assertEqual(set_alarm(Fake([LAB]), at(7, 30)), "Alarm set for seven thirty tomorrow.")

    def test_discord_wording(self):
        reply = set_alarm(Fake([LAB]), at(7, 45), "discord")
        self.assertEqual(reply, "Alarm set for 7:45 AM tomorrow. Heads up, sir: your ELEC 353 lab "
                                "starts at 8:00 AM, so that's less than half an hour before it.")

    def test_lectures_only(self):
        self.assertNotIn("Heads up", set_alarm(Fake([LECTURE]), at(7, 55)))

    def test_lecture_before_meeting_still_counts_the_meeting(self):
        reply = set_alarm(Fake([LECTURE, MEETING]), at(8, 45))
        self.assertIn("Meeting with Kelly Saunders starts at nine o'clock", reply)

    def test_meeting_in_work_calendar(self):
        self.assertIn("Heads up", set_alarm(Fake([MEETING]), at(8, 40)))
        self.assertNotIn("Heads up", set_alarm(Fake([MEETING]), at(8, 30)))

    def test_all_day_event_ignored(self):
        self.assertNotIn("Heads up", set_alarm(Fake([ALL_DAY]), at(7, 45)))

    def test_work_deadline_ignored_but_test_counts(self):
        self.assertNotIn("Heads up", set_alarm(Fake([DUE]), at(7, 45)))
        reply = set_alarm(Fake([DUE, QUIZ]), at(9, 15))
        self.assertIn("326 Quiz 2 starts at nine thirty".replace("326", "three twenty six"), reply)

    def test_work_word_meeting_counts(self):
        self.assertIn("Heads up", set_alarm(Fake([event("Project meeting", 8, calendar="Work")]),
                                            at(7, 45)))
        self.assertNotIn("Heads up", set_alarm(Fake([event("ELEC 353 lab report", 8, hours=0)]),
                                               at(7, 45)))

    def test_alarm_after_the_event(self):
        reply = set_alarm(Fake([LAB]), at(8, 30))
        self.assertIn("starts at eight o'clock, so that's after it starts", reply)

    def test_earliest_morning_alarm_counts(self):
        # an earlier alarm is already set and fine: the new, later one needn't be checked
        self.assertNotIn("Heads up", set_alarm(Fake([LAB], [at(7, 0)]), at(7, 45)))
        # the new one is the earliest, and too late
        self.assertIn("Heads up", set_alarm(Fake([LAB], [at(7, 50)]), at(7, 45)))

    def test_not_a_morning_alarm(self):
        self.assertNotIn("Heads up", set_alarm(Fake([event("Dentist", 15, 15)]), at(15, 0)))

    def test_alarm_less_than_an_hour_away(self):
        now = at(7, 20, date(2026, 10, 29))
        self.assertNotIn("Heads up", set_alarm(Fake([LAB]), now + timedelta(minutes=25), now=now))

    def test_other_day_ignored(self):
        later = event("Meeting", 8, calendar="Work", day=date(2026, 10, 30))
        self.assertNotIn("Heads up", set_alarm(Fake([later]), at(7, 55)))

    def test_calendar_error(self):
        with self.assertLogs("jarvis.alarms", "WARNING"):
            reply = set_alarm(Fake([LAB], calendar_error=OSError("no network")), at(7, 55))
        self.assertEqual(reply, "Alarm set for seven fifty five tomorrow.")

    def test_calendar_timeout(self):
        fake, gate = Fake([LAB]), threading.Event()
        fake.calendar = lambda start, end: gate.wait(2) and []
        with mock.patch.object(alarms, "CALENDAR_TIMEOUT", 0.1), \
                self.assertLogs("jarvis.alarms", "WARNING"):
            try:
                reply = set_alarm(fake, at(7, 55))
            finally:
                gate.set()
        self.assertEqual(reply, "Alarm set for seven fifty five tomorrow.")


class Goodnight(unittest.TestCase):
    def test_no_alarm_with_event(self):
        reply = goodnight(Fake([LAB]))
        self.assertEqual(reply, "Goodnight, sir. There's no alarm set for the morning. Your first "
                                "event tomorrow is ELEC three fifty three lab, at eight o'clock.")

    def test_no_alarm_without_events(self):
        self.assertEqual(goodnight(Fake([LECTURE, ALL_DAY])),
                         "Goodnight, sir. There's no alarm set for the morning.")

    def test_no_alarm_discord(self):
        self.assertIn("Your first event tomorrow is Meeting with Kelly Saunders, at 9:00 AM.",
                      goodnight(Fake([MEETING]), "discord"))

    def test_alarm_too_late(self):
        reply = goodnight(Fake([LAB], [at(7, 45)]))
        self.assertEqual(reply, "Goodnight, sir. Your alarm is set for seven forty five tomorrow. "
                                "Heads up, sir: your ELEC three fifty three lab starts at eight "
                                "o'clock, so that's less than half an hour before it.")

    def test_alarm_fine(self):
        self.assertEqual(goodnight(Fake([LAB], [at(7, 30)])),
                         "Goodnight, sir. Your alarm is set for seven thirty tomorrow.")

    def test_after_midnight_is_today(self):
        now = at(1, 10, TOMORROW)
        reply = goodnight(Fake([LAB]), now=now)
        self.assertIn("Your first event today is", reply)

    def test_calendar_error(self):
        with self.assertLogs("jarvis.alarms", "WARNING"):
            reply = goodnight(Fake([LAB], calendar_error=OSError("no network")))
        self.assertEqual(reply, "Goodnight, sir. There's no alarm set for the morning.")


if __name__ == "__main__":
    unittest.main()
