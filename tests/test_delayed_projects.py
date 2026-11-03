"""Delayed projects ("start a project at 3am: do my prelab"): the start time, keeping them in
data/scheduled-projects.json, starting them (late ones too), cancelling, and what the owner is
told afterwards. Uses a temporary data folder and a fake project.start, so no Claude Code run
is ever started.

Usage: python -m unittest tests.test_delayed_projects
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from jarvis import mcp_server, notify, project, tools
from jarvis.tools import projects as project_tools
from jarvis.tools.clock import TZ

NOW = datetime(2026, 10, 28, 22, 30, tzinfo=TZ)  # Wednesday evening


def at(day, h, m=0):
    return datetime(2026, 10, day, h, m, tzinfo=TZ)


class Store(unittest.TestCase):
    """Each test gets its own empty data folder, and project.start is never the real one."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.started = []
        self.posted = []
        patches = [
            mock.patch.object(project, "SCHEDULE_FILE", self.dir / "scheduled-projects.json"),
            mock.patch.object(project, "PROJECTS", self.dir / "projects"),
            mock.patch.object(project, "_projects", {}),
            mock.patch.object(notify, "post", self.posted.append),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def fake_start(self, name, task, voice, folder="", set_for=None):
        self.started.append({"name": name, "task": task, "voice": voice, "folder": folder,
                             "set_for": set_for})
        return SimpleNamespace(name=name)

    def file(self):
        return json.loads(project.SCHEDULE_FILE.read_text())


class StartTime(unittest.TestCase):
    def test_full_time_tomorrow(self):
        self.assertEqual(project.start_time("2026-10-29 03:00", NOW), at(29, 3))

    def test_clock_time_already_past_today_means_tomorrow(self):
        self.assertEqual(project.start_time("03:00", NOW), at(29, 3))
        self.assertEqual(project.start_time("3:00", NOW), at(29, 3))

    def test_clock_time_still_ahead_today(self):
        self.assertEqual(project.start_time("23:15", NOW), at(28, 23, 15))

    def test_full_time_earlier_today_means_tomorrow(self):
        # Claude worked out "3am" as today's date, but 3 am today is gone.
        self.assertEqual(project.start_time("2026-10-28 03:00", NOW), at(29, 3))

    def test_long_past_is_refused(self):
        with self.assertRaises(ValueError):
            project.start_time("2026-10-20 03:00", NOW)

    def test_unreadable_is_refused(self):
        with self.assertRaises(ValueError):
            project.start_time("3am", NOW)

    def test_next_day_across_the_clock_change(self):
        # Daylight saving ends at 2 am on Sunday, November 1: still 3 am on the clock.
        now = datetime(2026, 10, 31, 22, 0, tzinfo=TZ)
        when = project.start_time("03:00", now)
        self.assertEqual((when.day, when.hour, when.minute), (1, 3, 0))
        self.assertEqual(when.utcoffset(), timedelta(hours=-5))

    def test_written(self):
        self.assertEqual(project.written(at(29, 3), NOW), "3:00 AM tomorrow (Thursday, October 29)")
        self.assertEqual(project.written(at(28, 23, 15), NOW),
                         "11:15 PM today (Wednesday, October 28)")


class Scheduling(Store):
    def test_kept_on_disk(self):
        project.schedule("ELEC 353 prelab", "Do the prelab for lab 4", "School/ELEC 353/Lab 4",
                         at(29, 3))
        saved = self.file()["scheduled"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["at"], "2026-10-29T03:00:00-04:00")
        self.assertEqual(saved[0]["folder"], "School/ELEC 353/Lab 4")
        # What a restarted Jarvis reads back
        waiting = project.scheduled()
        self.assertEqual(waiting[0]["at"], at(29, 3))
        self.assertEqual(waiting[0]["task"], "Do the prelab for lab 4")

    def test_tool_schedules_instead_of_starting(self):
        with mock.patch.object(project_tools, "datetime") as dt, \
                mock.patch.object(project, "start", self.fake_start):
            dt.now.return_value = NOW
            reply = project_tools.start_project("prelab", "Do the prelab", "", "2026-10-29 03:00")
        self.assertEqual(self.started, [])
        self.assertIn("3:00 AM tomorrow (Thursday, October 29)", reply)
        self.assertEqual(project.scheduled()[0]["name"], "prelab")

    def test_tool_without_a_time_starts_now(self):
        with mock.patch.object(project, "start", self.fake_start):
            project_tools.start_project("prelab", "Do the prelab", "", "")
        self.assertEqual(len(self.started), 1)
        self.assertIsNone(self.started[0]["set_for"])
        self.assertFalse(project.SCHEDULE_FILE.exists())

    def test_tool_list_shows_waiting(self):
        project.schedule("prelab", "Do the prelab", "", at(29, 3))
        with mock.patch.object(project_tools, "datetime") as dt:
            dt.now.return_value = NOW
            listed = project_tools.list_projects()
        self.assertEqual(listed["waiting_to_start"],
                         [{"name": "prelab", "starts": "3:00 AM tomorrow (Thursday, October 29)",
                           "task": "Do the prelab"}])


class Firing(Store):
    def test_due_starts_and_later_waits(self):
        project.schedule("prelab", "Do the prelab", "School/ELEC 353/Lab 4", at(29, 3))
        project.schedule("essay", "Outline the essay", "", at(29, 9))
        with mock.patch.object(project, "start", self.fake_start):
            project.check_scheduled({}, at(29, 2, 59))
            self.assertEqual(self.started, [])
            project.check_scheduled({}, at(29, 3, 0))
            project.check_scheduled({}, at(29, 3, 1))  # never twice
        self.assertEqual(len(self.started), 1)
        s = self.started[0]
        self.assertEqual((s["name"], s["folder"], s["voice"], s["set_for"]),
                         ("prelab", "School/ELEC 353/Lab 4", False, at(29, 3)))
        data = self.file()
        self.assertEqual([x["name"] for x in data["scheduled"]], ["essay"])
        self.assertEqual(data["news"][0]["state"], "running")

    def test_catch_up_after_being_off(self):
        # Set before a restart; Jarvis comes back at 7:20, long after both times.
        project.schedule("prelab", "Do the prelab", "", at(29, 3))
        project.schedule("essay", "Outline the essay", "", at(29, 5))
        with mock.patch.object(project, "start", self.fake_start):
            project.check_scheduled({}, at(29, 7, 20))
        self.assertEqual([s["name"] for s in self.started], ["prelab", "essay"])
        self.assertEqual(project.scheduled(), [])

    def test_start_failing_is_reported(self):
        project.schedule("prelab", "Do the prelab", "", at(29, 3))
        with mock.patch.object(project, "start", side_effect=RuntimeError("no claude")), \
                self.assertLogs(project.log, "ERROR"):
            project.check_scheduled({}, at(29, 3))
        self.assertIn("couldn't: no claude", self.posted[0])
        self.assertEqual(self.file()["news"][0]["state"], "failed")


class Unattended(Store):
    """The real project.start with set_for, with Project.send faked: no Claude Code run."""

    def start(self, now):
        sent, posts = [], []
        with mock.patch.object(project.Project, "send", lambda p, text: sent.append(text)), \
                mock.patch.object(project.Project, "post", lambda p, text: posts.append(text)), \
                mock.patch.object(project, "datetime") as dt, \
                mock.patch.object(project, "_voice_project", None):
            dt.now.return_value = now
            p = project.start("prelab", "Do the prelab", voice=False, set_for=at(29, 3))
            voice = project.voice_project()
        return p, sent, posts, voice

    def test_on_time(self):
        p, sent, posts, voice = self.start(at(29, 3, 1))
        self.assertTrue(p.unattended)
        self.assertIsNone(voice)  # never spoken
        self.assertTrue(sent[0].startswith("(Jarvis: this project was set to start at 3:00 AM"))
        self.assertTrue(sent[0].endswith("Do the prelab"))
        self.assertEqual(posts, ["⏰ Starting as scheduled. Working in Discord only: nothing is "
                                 "said aloud."])

    def test_late_says_why(self):
        _, _, posts, _ = self.start(at(29, 7, 20))
        self.assertIn("set to start at 3:00 AM today (Thursday, October 29), but I wasn't "
                      "running then", posts[0])

    def test_speaks_nothing(self):
        p = project.Project("prelab")
        with mock.patch.object(project, "_voice_project", None):
            p._say("Starting the prelab.")
        self.assertTrue(project.speech.empty())

    def test_first_run_ending_is_news(self):
        project.schedule("prelab", "Do the prelab", "", at(29, 3))
        with mock.patch.object(project, "start", self.fake_start):
            project.check_scheduled({}, at(29, 3))
        p = project._load("prelab")
        p.unattended = True
        with mock.patch.object(project.Project, "_run", lambda p, text: True):
            p._inbox.put("Do the prelab")
            p._work()
        self.assertFalse(p.unattended)
        self.assertEqual(self.file()["news"][0]["state"], "finished")


class News(Store):
    def running(self, busy):
        project.schedule("prelab", "Do the prelab", "", at(29, 3))
        with mock.patch.object(project, "start", self.fake_start):
            project.check_scheduled({}, at(29, 3))
        p = project._load("prelab")
        p._worker = object() if busy else None

    def test_still_running_then_finished(self):
        self.running(busy=True)
        self.assertEqual(project.news(at(29, 7)), [
            "The prelab project you set for three o'clock is still working; its updates are "
            "in Discord."])
        self.assertEqual(project.news(at(29, 7, 5)), [])  # said once
        project._set_news("prelab", "finished")
        self.assertEqual(project.news(at(29, 8)), [
            "The prelab project you set for three o'clock has finished; the write-up is in "
            "Discord."])
        self.assertEqual(project.news(at(29, 8, 5)), [])
        self.assertEqual(self.file()["news"], [])

    def test_failed(self):
        self.running(busy=True)
        project._set_news("prelab", "failed")
        self.assertIn("ran into a problem", project.news(at(29, 7))[0])

    def test_cut_short_by_a_restart(self):
        self.running(busy=False)  # "running" on disk, but nothing running here
        self.assertIn("cut short when I restarted", project.news(at(29, 7))[0])

    def test_old_news_is_dropped(self):
        self.running(busy=True)
        project._set_news("prelab", "finished")
        self.assertEqual(project.news(at(30, 9)), [])
        self.assertEqual(self.file()["news"], [])


class Cancelling(Store):
    def test_by_name_or_part_of_it(self):
        project.schedule("ELEC 353 prelab", "Do the prelab", "", at(29, 3))
        project.schedule("essay outline", "Outline the essay", "", at(29, 9))
        gone = project.cancel("prelab")
        self.assertEqual([g["name"] for g in gone], ["ELEC 353 prelab"])
        self.assertEqual([s["name"] for s in project.scheduled()], ["essay outline"])

    def test_empty_name_with_only_one(self):
        project.schedule("prelab", "Do the prelab", "", at(29, 3))
        self.assertEqual(len(project.cancel("")), 1)
        self.assertEqual(project.scheduled(), [])

    def test_empty_name_with_several_cancels_nothing(self):
        project.schedule("prelab", "Do the prelab", "", at(29, 3))
        project.schedule("essay", "Outline the essay", "", at(29, 9))
        self.assertEqual(project.cancel(""), [])
        self.assertEqual(len(project.scheduled()), 2)

    def test_cancelled_never_starts(self):
        project.schedule("prelab", "Do the prelab", "", at(29, 3))
        project.cancel("prelab")
        with mock.patch.object(project, "start", self.fake_start):
            project.check_scheduled({}, at(29, 4))
        self.assertEqual(self.started, [])

    def test_tool_replies(self):
        project.schedule("prelab", "Do the prelab", "", at(29, 3))
        with mock.patch.object(project_tools, "datetime") as dt:
            dt.now.return_value = NOW
            self.assertEqual(project_tools.cancel_delayed_project("essay"),
                             "None matched. Waiting to start: prelab at 3:00 AM tomorrow "
                             "(Thursday, October 29).")
            self.assertEqual(project_tools.cancel_delayed_project("prelab"),
                             "Cancelled: the prelab project, which was to start at 3:00 AM "
                             "tomorrow (Thursday, October 29).")
            self.assertEqual(project_tools.cancel_delayed_project(""),
                             "No delayed projects are waiting to start.")


class Mcp(unittest.TestCase):
    def test_offered_to_claude_sessions(self):
        offered = {t["name"]: t for t in mcp_server._tool_list()}
        self.assertIn("cancel_delayed_project", offered)
        self.assertIn("start_at", offered["start_project"]["inputSchema"]["properties"])
        self.assertIn("cancel_delayed_project", tools.REGISTRY)


if __name__ == "__main__":
    unittest.main()
