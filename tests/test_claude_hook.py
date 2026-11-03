"""deploy/claude-hook.py: what it sends to Jarvis's alert socket, run as Claude Code runs it.

Usage: python -m unittest tests.test_claude_hook (a socket of its own; Jarvis needn't run)
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "deploy" / "claude-hook.py"


class ClaudeHookTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.runtime = Path(self.dir.name)
        self.socket_path = str(self.runtime / "alerts.sock")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.socket_path)
        self.server.listen(1)
        self.server.settimeout(0.5)
        (self.runtime / "jarvis-claude").mkdir()

    def tearDown(self):
        self.server.close()
        self.dir.cleanup()

    def run_hook(self, event, window=None):
        """What the hook sent for event, the session handed a task two minutes ago (None if nothing)."""
        (self.runtime / "jarvis-claude" / "s1").write_text(str(time.time() - 120))
        env = {k: v for k, v in os.environ.items() if k not in ("JARVIS_WINDOW", "JARVIS_PROJECT")}
        env.update(XDG_RUNTIME_DIR=str(self.runtime), ALERT_SOCKET=self.socket_path)
        if window:
            env["JARVIS_WINDOW"] = window
        event = {"session_id": "s1", "cwd": "/home/nate/jarvis", **event}
        done = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), text=True, env=env, timeout=10)
        self.assertEqual(done.returncode, 0)
        try:
            conn, _ = self.server.accept()
        except socket.timeout:
            return None
        with conn:
            return json.loads(conn.makefile().readline())["text"]

    def mute(self, window):
        muted = self.runtime / "jarvis-claude" / "muted"
        muted.mkdir(exist_ok=True)
        (muted / window).touch()

    def test_finished(self):
        self.assertEqual(self.run_hook({"hook_event_name": "Stop"}, window="w1"), "I'm finished in jarvis.")

    def test_a_muted_window_is_not_said_to_be_finished(self):
        self.mute("w1")
        self.assertIsNone(self.run_hook({"hook_event_name": "Stop"}, window="w1"))
        # What needs you still is, and other windows aren't muted.
        permission = {"hook_event_name": "Notification", "notification_type": "permission_prompt"}
        self.assertEqual(self.run_hook(permission, window="w1"), "I need your permission in jarvis.")
        self.assertEqual(self.run_hook({"hook_event_name": "Stop"}, window="w2"), "I'm finished in jarvis.")

    def test_outside_a_jarvis_window_mute_flags_do_nothing(self):
        self.mute("w1")
        self.assertEqual(self.run_hook({"hook_event_name": "Stop"}), "I'm finished in jarvis.")


if __name__ == "__main__":
    unittest.main()
