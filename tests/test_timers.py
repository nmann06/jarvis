"""Timers kept across a restart (data/timers.json, here a temporary file).

Usage: python -m unittest tests.test_timers
"""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from jarvis import notify
from jarvis.tools import timers


class Timers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sent = []
        self.done = threading.Event()
        for p in [mock.patch.object(timers, "PATH", Path(self.tmp.name) / "timers.json"),
                  mock.patch.object(timers, "_timers", {}),
                  mock.patch.dict(notify._sinks, {"voice": self.sink}, clear=True)]:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def sink(self, text):
        self.sent.append(text)
        self.done.set()

    def saved(self):
        return json.loads(timers.PATH.read_text())

    def test_saved_while_running_and_removed_when_done(self):
        timers.set_timer(1, "tea")
        self.assertEqual([(t["label"], t["channel"]) for t in self.saved()], [("tea", "voice")])
        self.assertTrue(self.done.wait(3))
        self.assertEqual(self.sent, ["Sir, your tea timer is done."])
        self.assertEqual(self.saved(), [])

    def test_restored_with_time_left(self):
        timers.PATH.write_text(json.dumps([{"id": 7, "label": "pasta", "ends": time.time() + 600,
                                            "channel": "voice"}]))
        timers.restore()
        (left,) = timers.list_timers()
        self.assertEqual(left["label"], "pasta")
        self.assertAlmostEqual(left["seconds_left"], 600, delta=2)

    def test_ran_out_while_down_waits_for_its_channel(self):
        timers.PATH.write_text(json.dumps([{"id": 1, "label": "", "ends": time.time() - 60,
                                            "channel": "discord"}]))
        discord = []
        timers.restore()
        time.sleep(0.3)
        self.assertEqual(self.sent, [])  # not spoken while Discord is still connecting
        notify._sinks["discord"] = lambda text: (discord.append(text), self.done.set())
        self.assertTrue(self.done.wait(4))
        self.assertEqual(discord, ["Sir, your timer is done (it ran out while I was restarting)."])


if __name__ == "__main__":
    unittest.main()
