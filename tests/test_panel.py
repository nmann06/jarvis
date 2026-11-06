"""The panel server (jarvis.panel) against docs/panel-api.md, and settings.set_secret.

Usage: python -m unittest tests.test_panel (temp data and .env; systemctl, journalctl and git are
faked, so it never touches the real service; audio is simulated)
"""

import asyncio
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
import wave
from datetime import datetime
from pathlib import Path
from unittest import mock

import numpy as np
from aiohttp.test_utils import TestClient, TestServer

from jarvis import events, settings
from jarvis.panel import hooks, reports, server

TOKEN = "test-token-not-a-real-one"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FAKE_SECRET = "fake-secret-value-123"


class Panel(unittest.IsolatedAsyncioTestCase):
    """A panel on a temp port, with temp data and .env, and every subprocess faked."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pn")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.env = self.dir / ".env"
        self.env.write_text(f"ANTHROPIC_API_KEY={FAKE_SECRET}\nWAKE_THRESHOLD=0.5\n# a comment\n")
        os.chmod(self.env, 0o600)
        self.commands = []
        self.answers = {}

        async def run(args, timeout=10):
            self.commands.append(list(args))
            for prefix, answer in self.answers.items():
                if " ".join(args).startswith(prefix):
                    return answer
            return (0, "active") if "is-active" in args else (0, "abc1234")

        for patcher in (mock.patch.object(settings, "ENV", self.env),
                        mock.patch.object(settings, "DND", self.dir / "dnd.json"),
                        mock.patch.object(reports, "DATA", self.dir),
                        mock.patch.object(events, "SOCKET", self.dir / "s.sock"),
                        mock.patch.object(hooks, "RETRY_SECONDS", 0.05),
                        mock.patch.object(server, "_run", run)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(events.stop)
        events.stop()
        self.check_env = mock.Mock(return_value=None)
        patcher = mock.patch.object(settings, "check_env", self.check_env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(TestServer(server.create_app(TOKEN)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def get(self, path, **kwargs):
        return await self.client.get(path, headers=AUTH, **kwargs)

    async def post(self, path, body=None):
        return await self.client.post(path, headers=AUTH, json=body if body is not None else {})

    async def ok(self, response):
        self.assertLess(response.status, 300, await response.text())
        return await response.json()

    async def failed(self, response, status):
        self.assertEqual(response.status, status, await response.text())
        body = await response.json()
        self.assertTrue(body["error"])
        return body["error"]

    async def go_live(self):
        """Start the hooks, simulate some audio, and wait for the panel to see them."""
        events.start(lambda: False)
        for _ in range(10):
            events.mic_block(np.full(events.BLOCK, 2000, dtype=np.int16))
        hub = self.client.app[server.HUB]
        for _ in range(100):
            if hub.live:
                return hub
            await asyncio.sleep(0.02)
        self.fail("the panel never saw the hooks")


class Auth(Panel):
    async def test_every_route_needs_the_token(self):
        for path in ("/", "/api/status", "/api/settings", "/api/reports", "/api/logs", "/api/stats",
                     "/api/nope", "/nope"):
            await self.failed(await self.client.get(path), 401)
        await self.failed(await self.client.post("/api/reports", json={}), 401)
        await self.failed(await self.client.delete("/api/reports/x"), 401)
        await self.failed(await self.client.get("/api/status",
                                                headers={"Authorization": "Bearer wrong"}), 401)
        await self.failed(await self.client.get("/api/status",
                                                headers={"Authorization": "Bearer é"}), 401)

    async def test_login_sets_the_cookie(self):
        await self.failed(await self.client.get("/login?token=wrong"), 401)
        response = await self.client.get(f"/login?token={TOKEN}", allow_redirects=False)
        self.assertEqual((response.status, response.headers["Location"]), (302, "/"))
        cookie = response.cookies["panel"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        self.assertEqual(cookie.value, TOKEN)
        response = await self.client.get("/api/settings", headers={"Cookie": f"panel={TOKEN}"})
        await self.ok(response)

    async def test_post_must_be_json(self):
        response = await self.client.post("/api/settings", headers=AUTH, data="key=value")
        await self.failed(response, 415)
        response = await self.client.post("/api/settings", headers=AUTH, data=b"{bad",
                                          skip_auto_headers=["Content-Type"])
        await self.failed(response, 415)
        response = await self.client.post("/api/settings", data="{bad",
                                          headers={**AUTH, "Content-Type": "application/json"})
        await self.failed(response, 400)

    async def test_token_file(self):
        path = self.dir / "panel-token"
        token = server.load_token(path)
        self.assertGreaterEqual(len(token), 40)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        os.chmod(path, 0o644)
        self.assertEqual(server.load_token(path), token)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    async def test_errors_are_json(self):
        await self.failed(await self.get("/api/nope"), 404)
        await self.failed(await self.client.delete("/api/status", headers=AUTH), 405)

    async def test_failure_tells_nothing(self):
        with mock.patch.object(settings, "listing", side_effect=RuntimeError(FAKE_SECRET)), \
                self.assertLogs("jarvis.panel", "ERROR"):
            error = await self.failed(await self.get("/api/settings"), 500)
        self.assertNotIn(FAKE_SECRET, error)


class Binding(unittest.IsolatedAsyncioTestCase):
    async def test_only_a_tailscale_address_counts(self):
        cases = {"100.101.102.103\n": "100.101.102.103", "192.168.1.5\n": None, "0.0.0.0\n": None,
                 "garbage\n": None}
        for out, expected in cases.items():
            with mock.patch.object(server, "_run", mock.AsyncMock(return_value=(0, out))):
                self.assertEqual(await server.tailscale_ip(), expected)
        with mock.patch.object(server, "_run", mock.AsyncMock(return_value=(None, "no tailscale"))):
            self.assertIsNone(await server.tailscale_ip())

    async def test_never_all_interfaces(self):
        sites = []

        class Site:
            def __init__(self, runner, host, port):
                sites.append((host, port))

            async def start(self):
                pass

        class Runner:
            def __init__(self, *args, **kwargs):
                pass

            async def setup(self):
                pass

            async def cleanup(self):
                pass

        with tempfile.TemporaryDirectory(prefix="pn") as tmp, \
                mock.patch.object(reports, "DATA", Path(tmp)), \
                mock.patch.object(settings, "ENV", Path(tmp) / ".env"), \
                mock.patch.dict(os.environ, {"PANEL_PORT": "18999"}), \
                mock.patch.object(server, "tailscale_ip", mock.AsyncMock(return_value="100.64.0.9")), \
                mock.patch.object(server.web, "TCPSite", Site), \
                mock.patch.object(server.web, "AppRunner", Runner):
            task = asyncio.create_task(server.serve())
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(sites, [("127.0.0.1", 18999), ("100.64.0.9", 18999)])

    def test_default_port(self):
        with tempfile.TemporaryDirectory(prefix="pn") as tmp, \
                mock.patch.object(settings, "ENV", Path(tmp) / ".env"), \
                mock.patch.dict(os.environ):
            os.environ.pop("PANEL_PORT", None)
            self.assertEqual(server.port(), 8767)

    def test_does_not_need_config_or_the_audio_stack(self):
        code = ("import sys; import jarvis.panel.server; "
                "bad = {'jarvis.config', 'jarvis.voice', 'jarvis.audio', 'sounddevice'} & set(sys.modules); "
                "sys.exit(repr(bad) if bad else 0)")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              cwd=settings.ROOT, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)


class Status(Panel):
    async def test_hooks_not_live(self):
        body = await self.ok(await self.get("/api/status"))
        self.assertEqual(set(body), {"service", "hooks_live", "hooks_reason", "state", "muted",
                                     "version", "restart_needed"})
        self.assertEqual((body["service"], body["hooks_live"], body["state"], body["muted"]),
                         ("active", False, "unknown", False))
        self.assertEqual(body["version"], "abc1234")

    async def test_service_states(self):
        for out, expected in (("inactive", "inactive"), ("failed", "failed"),
                              ("activating", "unknown"), ("", "unknown")):
            self.answers["systemctl --user is-active"] = (3, out)
            self.assertEqual((await self.ok(await self.get("/api/status")))["service"], expected)

    async def reason(self):
        return (await self.ok(await self.get("/api/status")))["hooks_reason"]

    async def test_hooks_reason(self):
        self.answers["systemctl --user show"] = (0, "ActiveEnterTimestamp=@1000")
        self.assertEqual(await self.reason(), "needs_restart")
        self.answers["systemctl --user show"] = (0, f"ActiveEnterTimestamp=@{int(time.time()) - 5}")
        self.assertEqual(await self.reason(), "starting")
        self.answers["systemctl --user show"] = (0, "ActiveEnterTimestamp=@1000")
        self.env.write_text("VOICE_ENABLED=off\n")
        self.assertEqual(await self.reason(), "voice_off")
        self.answers["systemctl --user is-active"] = (3, "inactive")
        self.assertEqual(await self.reason(), "down")
        await self.go_live()
        self.assertIsNone(await self.reason())

    async def test_hooks_live(self):
        await self.go_live()
        events.set_state("listening")
        await asyncio.sleep(0.2)
        body = await self.ok(await self.get("/api/status"))
        self.assertEqual((body["hooks_live"], body["state"]), (True, "listening"))

    async def test_hooks_going_away(self):
        hub = await self.go_live()
        events.stop()
        for _ in range(100):
            if not hub.live:
                break
            await asyncio.sleep(0.02)
        body = await self.ok(await self.get("/api/status"))
        self.assertEqual((body["hooks_live"], body["state"]), (False, "unknown"))

    async def test_restart_needed(self):
        hub = await self.go_live()
        hub.started = self.env.stat().st_mtime + 100
        self.assertFalse((await self.ok(await self.get("/api/status")))["restart_needed"])
        hub.started = self.env.stat().st_mtime - 100
        self.assertTrue((await self.ok(await self.get("/api/status")))["restart_needed"])

    async def test_restart_needed_without_hooks(self):
        long_ago = int(self.env.stat().st_mtime) - 100
        self.answers["systemctl --user show"] = (0, f"ActiveEnterTimestamp=@{long_ago}")
        self.assertTrue((await self.ok(await self.get("/api/status")))["restart_needed"])


class Events(Panel):
    async def read_events(self, response, wanted, timeout=3):
        seen = []
        async with asyncio.timeout(timeout):
            while True:
                line = (await response.content.readline()).decode().strip()
                if line.startswith("data: "):
                    seen.append(json.loads(line[6:]))
                    if wanted(seen[-1]):
                        return seen

    async def test_not_live_says_so_at_once(self):
        response = await self.get("/api/events")
        self.assertEqual(response.headers["Content-Type"], "text/event-stream")
        seen = await self.read_events(response, lambda m: m["type"] == "state")
        self.assertEqual(seen[0]["type"], "hooks")
        self.assertFalse(seen[0]["live"])
        self.assertEqual(seen[1]["state"], "unknown")
        response.close()

    async def test_state_and_level_shapes(self):
        await self.go_live()
        response = await self.get("/api/events")
        await self.read_events(response, lambda m: m["type"] == "state")
        events.wake_score(0.2, 0.5)
        events.set_state("thinking")
        await self.read_events(response, lambda m: m.get("state") == "thinking")
        events.mic_block(np.full(events.BLOCK, 3277, dtype=np.int16))
        level = (await self.read_events(response, lambda m: m["type"] == "level"))[-1]
        self.assertEqual(set(level), {"type", "rms", "peak", "wake_score", "t"})
        self.assertEqual(level["wake_score"], 0.2)
        response.close()

    async def test_hooks_event_when_they_go_and_come(self):
        response = await self.get("/api/events")
        await self.read_events(response, lambda m: m["type"] == "state")
        await self.go_live()
        await self.read_events(response, lambda m: m["type"] == "hooks" and m["live"])
        events.stop()
        await self.read_events(response, lambda m: m["type"] == "hooks" and not m["live"])
        response.close()

    async def test_slow_client_loses_oldest(self):
        hub = self.client.app[server.HUB]
        client = hub.subscribe()
        for i in range(hooks.CLIENT_QUEUE + 10):
            hub._emit({"n": i})
        self.assertEqual(client.get_nowait()["n"], 10)
        hub.unsubscribe(client)


class Settings(Panel):
    async def test_listing_never_has_secrets(self):
        response = await self.get("/api/settings")
        self.assertNotIn(FAKE_SECRET, await response.text())
        body = await response.json()
        key = next(s for s in body["settings"] if s["key"] == "ANTHROPIC_API_KEY")
        self.assertEqual((key["value"], key["set"]), (None, True))
        self.assertIn("PANEL_PORT", {s["key"] for s in body["settings"]})

    async def test_set_setting(self):
        body = await self.ok(await self.post("/api/settings",
                                             {"key": "WAKE_THRESHOLD", "value": "0.4"}))
        self.assertTrue(body["restart"])
        self.assertIn("WAKE_THRESHOLD=0.4", self.env.read_text())
        await self.failed(await self.post("/api/settings", {"key": "WAKE_THRESHOLD", "value": "x"}), 400)
        await self.failed(await self.post("/api/settings", {"key": "NOPE", "value": "1"}), 400)
        await self.failed(await self.post("/api/settings", {"key": "WAKE_THRESHOLD"}), 400)

    async def test_set_setting_cannot_write_a_secret(self):
        await self.failed(await self.post("/api/settings",
                                          {"key": "ANTHROPIC_API_KEY", "value": "x"}), 400)
        self.assertIn(FAKE_SECRET, self.env.read_text())

    async def test_set_secret(self):
        body = await self.ok(await self.post("/api/secrets",
                                             {"key": "TRELLO_KEY", "value": "new-value-1"}))
        self.assertEqual(body["restart"], True)
        self.assertNotIn("new-value-1", json.dumps(body))
        text = self.env.read_text()
        self.assertIn("TRELLO_KEY=new-value-1", text)
        self.assertIn(f"ANTHROPIC_API_KEY={FAKE_SECRET}", text)  # the rest kept
        self.assertIn("# a comment", text)
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)

    async def test_set_secret_refuses(self):
        await self.failed(await self.post("/api/secrets", {"key": "WAKE_THRESHOLD", "value": "1"}), 400)
        await self.failed(await self.post("/api/secrets", {"key": "NOPE", "value": "1"}), 400)
        await self.failed(await self.post("/api/secrets", {"key": "TRELLO_KEY", "value": 5}), 400)
        for bad in ('a"b', "a\\b", "a\nb", "a${b}"):
            await self.failed(await self.post("/api/secrets", {"key": "TRELLO_KEY", "value": bad}), 400)
        await self.failed(await self.post("/api/secrets", {"key": "DISCORD_OWNER_ID", "value": "me"}), 400)
        self.assertNotIn("TRELLO_KEY", self.env.read_text())

    async def test_set_secret_empty_removes(self):
        await self.ok(await self.post("/api/secrets", {"key": "ANTHROPIC_API_KEY", "value": ""}))
        self.assertNotIn("ANTHROPIC_API_KEY", self.env.read_text())

    async def test_secret_with_spaces_reads_back(self):
        await self.ok(await self.post("/api/secrets", {"key": "ICLOUD_APP_PASSWORD",
                                                       "value": "abcd efgh 'x"}))
        self.assertEqual(settings.env_values()["ICLOUD_APP_PASSWORD"], "abcd efgh 'x")


class SettingChecks(unittest.TestCase):
    """Bad values can't get into .env, whichever way they come. Temp .env only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pn")
        self.addCleanup(self.tmp.cleanup)
        self.env = Path(self.tmp.name) / ".env"
        self.env.write_text(f"ANTHROPIC_API_KEY={FAKE_SECRET}\n")
        os.chmod(self.env, 0o600)
        patcher = mock.patch.object(settings, "ENV", self.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_number_has_a_range_and_every_default_passes(self):
        for s in settings.SETTINGS:
            if s["kind"] in ("int", "float"):
                self.assertIn(s["key"], settings.RANGES)
            if not s["secret"] and s["default"]:
                settings._check(s, s["default"])

    def test_bad_values_refused(self):
        bad = {"WAKE_THRESHOLD": ["nan", "inf", "-1", "1.5", "0", "abc"],
               "SPEECH_THRESHOLD": ["2"], "SAY_PLEASE_RATE": ["1.1"], "MCP_PORT": ["80", "70000", "x"],
               "PANEL_PORT": ["0", "8766"], "ALARM_RING_MINUTES": ["0", "1.5"],
               "ALARM_RAMP_MINUTES": ["0", "nan"], "ALARM_MIN_VOLUME": ["101"],
               "WHISPER_THREADS": ["0"], "HISTORY_CHARS": ["-1"], "TTS_VOLUME": ["inf"],
               "BRIEFING_TIME": ["25:00"], "TODO_REMIND_TIMES": ["soon", "9:00,25:00"],
               "WHISPER_DEVICE": ["gpu"], "CLAUDE_EFFORT": ["huge"],
               "DISCORD_CHANNEL": ['a"b', "a\nb", "${x}"]}
        before = self.env.read_text()
        for key, values in bad.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    settings.set_setting(key, value)
        self.assertEqual(self.env.read_text(), before)

    def test_good_values_written(self):
        for key, value, written in (("TODO_REMIND_TIMES", " 8:30,17:00 ", "8:30,17:00"), ("WAKE_THRESHOLD", "0.2", "0.2"),
                                    ("CLAUDE_MODEL", "claude-new-1", "claude-new-1"), ("PANEL_PORT", "18000", "18000")):
            settings.set_setting(key, value)
            self.assertEqual(settings.env_values()[key], written)
        settings.set_setting("TODO_REMIND_TIMES", "")  # back to the default
        self.assertNotIn("TODO_REMIND_TIMES", settings.env_values())

    def test_everything_accepted_still_loads(self):
        for key, value in (("TODO_REMIND_TIMES", "8:30,17:00"), ("WAKE_THRESHOLD", "0.01"), ("MCP_PORT", "1024"),
                           ("HISTORY_CHARS", "1000"), ("TTS_VOLUME", "0")):
            settings.set_setting(key, value)
        self.assertIsNone(settings.check_env())

    def test_check_env_names_the_setting_without_the_value(self):
        for line, key in ((f"DISCORD_OWNER_ID={FAKE_SECRET}", "DISCORD_OWNER_ID"),
                          (f"WAKE_THRESHOLD={FAKE_SECRET}", "WAKE_THRESHOLD"),
                          (f"SAY_PLEASE_TESTS={FAKE_SECRET}", "SAY_PLEASE_TESTS")):
            self.env.write_text(line + "\n")
            problem = settings.check_env()
            self.assertIn(key, problem)
            self.assertNotIn(FAKE_SECRET, problem)

    def test_check_env_ignores_the_callers_environment(self):
        self.env.write_text("WAKE_THRESHOLD=abc\n")
        with mock.patch.dict(os.environ, {"WAKE_THRESHOLD": "0.5"}):
            self.assertIn("WAKE_THRESHOLD", settings.check_env())

    def test_check_env_timeout_and_missing_python(self):
        with mock.patch.object(settings.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertIn("Couldn't check", settings.check_env())
        with mock.patch.object(settings.subprocess, "run", side_effect=FileNotFoundError(2, "nope")):
            self.assertIn("Couldn't check", settings.check_env())

    def test_restart_checks_first(self):
        with mock.patch.object(settings, "check_env", return_value="bad .env"), \
                mock.patch.object(settings.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                settings.restart()
            run.assert_not_called()
            with mock.patch("builtins.print") as printed:
                self.assertEqual(settings.main(["restart"]), 1)
            self.assertIn("bad .env", printed.call_args.args[0])
            run.assert_not_called()

    def test_restart_runs_when_fine(self):
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(settings, "check_env", return_value=None), \
                mock.patch.object(settings.subprocess, "run", return_value=done) as run:
            settings.restart()
        self.assertEqual(run.call_args.args[0], ["systemctl", "--user", "restart", "jarvis"])

    def test_temp_file_is_private_from_the_start_and_unique(self):
        os.chmod(self.env, 0o640)
        seen = []
        real = os.replace

        def replace(tmp, target):
            seen.append(tmp)
            return real(tmp, target)
        modes = []
        real_fdopen = os.fdopen

        def fdopen(fd, *args, **kwargs):
            modes.append(stat.S_IMODE(os.fstat(fd).st_mode))
            return real_fdopen(fd, *args, **kwargs)
        with mock.patch.object(settings.os, "replace", replace), \
                mock.patch.object(settings.os, "fdopen", fdopen):
            settings.set_secret("TRELLO_KEY", "one-value")
            settings.set_secret("TRELLO_TOKEN", "two-value")
        self.assertEqual(modes, [0o600, 0o600])
        self.assertNotEqual(seen[0], seen[1])
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o640)  # the file's own mode kept
        self.assertEqual([p.name for p in self.env.parent.iterdir()], [".env"])

    def test_temp_file_removed_on_failure(self):
        with mock.patch.object(settings.os, "replace", side_effect=OSError("disk")), \
                self.assertRaises(OSError):
            settings.set_secret("TRELLO_KEY", "one-value")
        self.assertEqual([p.name for p in self.env.parent.iterdir()], [".env"])
        self.assertEqual(self.env.read_text(), f"ANTHROPIC_API_KEY={FAKE_SECRET}\n")


class Service(Panel):
    async def test_actions_run_systemctl_on_the_fake(self):
        for action, word in (("start", "started"), ("stop", "stopped"), ("restart", "restarted")):
            body = await self.ok(await self.post("/api/service", {"action": action}))
            self.assertIn(word, body["message"])
            self.assertEqual(self.commands[-1], ["systemctl", "--user", action, "jarvis"])

    async def test_bad_env_stops_restart_and_start(self):
        self.check_env.return_value = "Jarvis wouldn't start: WAKE_THRESHOLD isn't valid."
        for action in ("restart", "start"):
            error = await self.failed(await self.post("/api/service", {"action": action}), 400)
            self.assertIn("WAKE_THRESHOLD", error)
        self.assertEqual(self.commands, [])

    async def test_stop_needs_no_check(self):
        self.check_env.return_value = "bad"
        await self.ok(await self.post("/api/service", {"action": "stop"}))
        self.assertEqual(self.commands[-1], ["systemctl", "--user", "stop", "jarvis"])

    async def test_bad_action(self):
        await self.failed(await self.post("/api/service", {"action": "enable"}), 400)
        self.assertEqual(self.commands, [])

    async def test_failure(self):
        self.answers["systemctl --user restart"] = (1, f"nope {FAKE_SECRET}")
        error = await self.failed(await self.post("/api/service", {"action": "restart"}), 500)
        self.assertNotIn(FAKE_SECRET, error)

    async def test_mute_needs_hooks(self):
        await self.failed(await self.post("/api/mute", {"muted": True}), 409)

    async def test_mute(self):
        await self.go_live()
        await self.failed(await self.post("/api/mute", {"muted": "yes"}), 400)
        self.assertEqual(await self.ok(await self.post("/api/mute", {"muted": True})), {"muted": True})
        self.assertTrue(events._muted)
        self.assertTrue((await self.ok(await self.get("/api/status")))["muted"])
        self.assertEqual(await self.ok(await self.post("/api/mute", {"muted": False})), {"muted": False})
        self.assertFalse(events._muted)

    async def test_logs_masked(self):
        self.answers["journalctl"] = (0, f"line one\nkey is {FAKE_SECRET} here")
        body = await self.ok(await self.get("/api/logs?lines=5"))
        self.assertEqual(body["lines"][0], "line one")
        self.assertNotIn(FAKE_SECRET, body["lines"][1])
        self.assertIn("5", self.commands[-1])
        await self.ok(await self.get("/api/logs?lines=99999"))
        self.assertIn(str(server.MAX_LOG_LINES), self.commands[-1])
        await self.failed(await self.get("/api/logs?lines=x"), 400)

    async def test_log_stream_masked_and_killed(self):
        reader = asyncio.StreamReader()
        reader.feed_data(f"Nov 01 jarvis: key {FAKE_SECRET}\n".encode())
        reader.feed_eof()
        process = mock.Mock(stdout=reader, returncode=None)
        process.wait = mock.AsyncMock(return_value=0)
        with mock.patch.object(server, "_follow_log", mock.AsyncMock(return_value=process)):
            response = await self.get("/api/logs/stream")
            text = await response.text()
        self.assertTrue(text.startswith('data: {"line": '))
        self.assertNotIn(FAKE_SECRET, text)
        process.kill.assert_called_once()


class Integrations(Panel):
    def fake(self, **overrides):
        module = types.SimpleNamespace(
            list_integrations=lambda: [{"id": "x", "name": "X"}],
            test=lambda i: {"ok": True, "message": f"fine {FAKE_SECRET}"},
            login_command=lambda i: [sys.executable, "-c", "print('signed in')"],
            checks=lambda: [{"id": "gpu", "name": "GPU", "ok": True, "detail": "", "fix": ""}])
        for name, value in overrides.items():
            setattr(module, name, value)
        patcher = mock.patch.dict(sys.modules, {"jarvis.integrations": module})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_missing_module_is_a_503(self):
        with mock.patch.object(server.importlib, "import_module", side_effect=ImportError("nope")):
            for path in ("/api/integrations", "/api/checks"):
                await self.failed(await self.get(path), 503)
            await self.failed(await self.post("/api/integrations/x/test"), 503)
            await self.failed(await self.post("/api/integrations/x/login"), 503)

    async def test_module_that_exits_is_a_503(self):
        with mock.patch.object(server.importlib, "import_module", side_effect=SystemExit("bad .env")):
            await self.failed(await self.get("/api/integrations"), 503)

    async def test_listing_and_checks(self):
        self.fake()
        self.assertEqual((await self.ok(await self.get("/api/integrations")))["integrations"][0]["id"], "x")
        self.assertTrue((await self.ok(await self.get("/api/checks")))["checks"][0]["ok"])

    async def test_test_masks_and_failure_is_200(self):
        self.fake()
        body = await self.ok(await self.post("/api/integrations/x/test"))
        self.assertTrue(body["ok"])
        self.assertNotIn(FAKE_SECRET, body["message"])

        def fails(i):
            raise RuntimeError("broke")
        self.fake(test=fails)
        body = await self.ok(await self.post("/api/integrations/x/test"))
        self.assertFalse(body["ok"])

    async def test_test_time_limit(self):
        self.fake(test=lambda i: time.sleep(0.5))
        with mock.patch.object(server, "TEST_SECONDS", 0.05):
            body = await self.ok(await self.post("/api/integrations/x/test"))
        self.assertFalse(body["ok"])

    async def test_unknown_id(self):
        def unknown(i):
            raise KeyError(i)
        self.fake(test=unknown, login_command=unknown)
        await self.failed(await self.post("/api/integrations/z/test"), 404)
        await self.failed(await self.post("/api/integrations/z/login"), 404)

    async def test_login_job(self):
        self.fake()
        job = (await self.ok(await self.post("/api/integrations/x/login")))["job"]
        for _ in range(200):
            body = await self.ok(await self.get(f"/api/jobs/{job}"))
            if not body["running"]:
                break
            await asyncio.sleep(0.02)
        self.assertEqual((body["running"], body["exit"]), (False, 0))
        self.assertIn("signed in", body["output"])
        await self.failed(await self.get("/api/jobs/nope"), 404)

    async def job_env(self):
        code = "import os; print(os.environ.get('DISPLAY'), os.environ.get('XAUTHORITY'))"
        self.fake(login_command=lambda i: [sys.executable, "-c", code])
        job = (await self.ok(await self.post("/api/integrations/x/login")))["job"]
        for _ in range(200):
            body = await self.ok(await self.get(f"/api/jobs/{job}"))
            if not body["running"]:
                return body["output"].split()
            await asyncio.sleep(0.02)
        self.fail("the job never ended")

    async def test_login_uses_the_sessions_display(self):
        self.answers["systemctl --user show-environment"] = (0, "HOME=/x\nDISPLAY=:1\nXAUTHORITY=/a/b\n")
        self.assertEqual(await self.job_env(), [":1", "/a/b"])

    async def test_login_falls_back_to_the_laptop_screen(self):
        self.answers["systemctl --user show-environment"] = (1, "")
        (self.dir / ".Xauthority").write_text("")
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "XAUTHORITY")}
        env.update(HOME=str(self.dir), XDG_RUNTIME_DIR=str(self.dir / "none"))
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(await self.job_env(), [":0", str(self.dir / ".Xauthority")])

    async def test_login_not_needed(self):
        self.fake(login_command=lambda i: None)
        await self.failed(await self.post("/api/integrations/x/login"), 400)


def add_rows(directory, exchange=None, turn=None):
    if exchange:
        (directory / "exchanges.jsonl").write_text(json.dumps(exchange) + "\n")
    if turn:
        (directory / "latency.jsonl").write_text(json.dumps(turn) + "\n")


class Reports(Panel):
    async def press(self, kind="missed_wake", note=""):
        return await self.ok(await self.post("/api/reports", {"kind": kind, "note": note}))

    async def test_shape_and_files_with_hooks_live(self):
        await self.go_live()
        events.wake_score(0.31, 0.5)
        report = await self.press(note="said it twice")
        self.assertEqual(set(report), {"id", "kind", "t", "note", "has_audio", "context",
                                       "suggestion", "status"})
        self.assertRegex(report["id"], r"^\d{8}-\d{6}-missed_wake$")
        self.assertEqual((report["kind"], report["note"], report["status"]),
                         ("missed_wake", "said it twice", "open"))
        self.assertTrue(report["has_audio"])
        for key in ("peak_wake_score", "threshold", "states", "stop_events", "last_exchange"):
            self.assertIn(key, report["context"])
        self.assertEqual(report["suggestion"], {
            "text": "Your wake word scored 0.31 and needs 0.5. Lower sensitivity to 0.28?",
            "key": "WAKE_THRESHOLD", "value": "0.28"})
        folder = self.dir / "reports" / report["id"]
        self.assertEqual(json.loads((folder / "meta.json").read_text())["id"], report["id"])
        with wave.open(str(folder / "audio.wav")) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()),
                             (1, 2, 16000, 10 * events.BLOCK))
        self.assertEqual(stat.S_IMODE((folder / "meta.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((folder / "audio.wav").stat().st_mode), 0o600)

    async def test_recent_turns_oldest_first(self):
        def row(ago, heard):
            stamp = datetime.fromtimestamp(time.time() - ago).isoformat(timespec="seconds")
            return {"time": stamp, "heard": heard, "reply": f"re {heard}", "outcome": "answered",
                    "audio_s": 1.0}
        rows = [row(1800, "too old")] + [row(60 * (7 - i), f"turn {i}") for i in range(7)]
        (self.dir / "latency.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        context = (await self.press("false_wake"))["context"]
        self.assertEqual([t["heard"] for t in context["turns"]],
                         ["turn 2", "turn 3", "turn 4", "turn 5", "turn 6"])
        self.assertEqual(set(context["turns"][0]), {"time", "heard", "reply", "outcome", "age_s"})
        self.assertIsNone(context["prompt"])

    async def test_hooks_not_live(self):
        for kind in reports.KINDS:
            report = await self.press(kind)
            self.assertFalse(report["has_audio"])
            self.assertFalse(report["context"]["hooks_live"])
            self.assertIn("weren't live", report["context"]["note"])
            self.assertEqual(report["context"]["threshold"], 0.5)  # from .env
            await self.failed(await self.get(f"/api/reports/{report['id']}/audio"), 404)
            self.assertFalse((self.dir / "reports" / report["id"] / "audio.wav").exists())

    async def test_bad_requests(self):
        await self.failed(await self.post("/api/reports", {"kind": "nope"}), 400)
        await self.failed(await self.post("/api/reports", {"kind": "misheard", "note": 3}), 400)

    async def test_list_newest_first_and_same_second_ids(self):
        with mock.patch.object(reports.time, "time", return_value=1793500000.0):
            first = await self.press()
            second = await self.press()
        self.assertEqual(second["id"], first["id"] + "-2")
        third = await self.press("misheard")
        body = await self.ok(await self.get("/api/reports"))
        self.assertEqual(body["reports"][0]["id"], third["id"])
        self.assertEqual(len(body["reports"]), 3)

    async def test_audio_route(self):
        await self.go_live()
        report = await self.press()
        response = await self.get(f"/api/reports/{report['id']}/audio")
        self.assertEqual((response.status, response.content_type), (200, "audio/wav"))
        self.assertEqual((await response.read())[:4], b"RIFF")

    async def test_apply(self):
        await self.go_live()
        events.wake_score(0.31, 0.5)
        report = await self.press()
        body = await self.ok(await self.post(f"/api/reports/{report['id']}/apply"))
        self.assertTrue(body["restart"])
        self.assertEqual(settings.env_values()["WAKE_THRESHOLD"], "0.28")

    async def test_nothing_changes_until_apply(self):
        await self.go_live()
        events.wake_score(0.31, 0.5)
        await self.press()
        self.assertEqual(settings.env_values()["WAKE_THRESHOLD"], "0.5")

    async def test_apply_advice_is_refused(self):
        await self.go_live()
        report = await self.press()  # no wake scores at all: nothing to suggest
        self.assertIsNone(report["suggestion"])
        await self.failed(await self.post(f"/api/reports/{report['id']}/apply"), 400)
        events.wake_score(0.05, 0.5)  # too low for a setting to help: advice
        report = await self.press()
        self.assertIsNone(report["suggestion"]["key"])
        await self.failed(await self.post(f"/api/reports/{report['id']}/apply"), 400)
        self.assertEqual(settings.env_values()["WAKE_THRESHOLD"], "0.5")
        await self.failed(await self.post("/api/reports/20261101-000000-misheard/apply"), 404)

    async def test_status_and_delete(self):
        await self.go_live()
        report = await self.press()
        rid = report["id"]
        body = await self.ok(await self.post(f"/api/reports/{rid}/status", {"status": "fixed"}))
        self.assertEqual(body["status"], "fixed")
        await self.failed(await self.post(f"/api/reports/{rid}/status", {"status": "x"}), 400)
        self.assertTrue((self.dir / "reports" / rid / "audio.wav").exists())
        await self.ok(await self.client.delete(f"/api/reports/{rid}", headers=AUTH))
        self.assertFalse((self.dir / "reports" / rid).exists())
        await self.failed(await self.client.delete(f"/api/reports/{rid}", headers=AUTH), 404)

    async def test_ids_cannot_escape(self):
        outside = self.dir / "keep"
        outside.mkdir()
        (outside / "meta.json").write_text("{}")
        for rid in ("..%2Fkeep", "%2E%2E", "..", "20261101-000000-x%2F..%2F..%2Fkeep"):
            response = await self.client.delete(f"/api/reports/{rid}", headers=AUTH)
            self.assertGreaterEqual(response.status, 400)
        self.assertTrue(outside.exists())
        self.assertIsNone(reports._path("../keep"))

    async def test_secrets_out_of_context(self):
        add_rows(self.dir, {"channel": "voice", "heard": "hello", "reply": f"key {FAKE_SECRET}",
                            "said": "", "tools": [], "time": "2026-11-01T00:00:00"})
        report = await self.press("wrong_answer", note=f"it said {FAKE_SECRET}")
        self.assertNotIn(FAKE_SECRET, json.dumps(report))
        folder = self.dir / "reports" / report["id"]
        self.assertNotIn(FAKE_SECRET, (folder / "meta.json").read_text())


NOW = 1793500000.0


def ctx(**overrides):
    return {"hooks_live": True, "peak_wake_score": None, "threshold": 0.5, "states": [],
            "stop_events": [], "wakes": [], "mic_peak": 0.3, "muted": False, "last_exchange": {},
            "last_turn": {}, **overrides}


def stop_event(decision, vosk=None, whisper=None, score=None, ago=5):
    return {"t": NOW - ago, "decision": decision, "vosk": vosk, "phrase": "stop", "whisper": whisper,
            "wake_score": score}


class Suggestions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pn")
        self.addCleanup(self.tmp.cleanup)
        env = Path(self.tmp.name) / ".env"
        env.write_text("")
        patcher = mock.patch.object(settings, "ENV", env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def suggest(self, kind, **overrides):
        result = reports.suggest(kind, ctx(**overrides), NOW)
        if result is not None:
            self.assertEqual(set(result), {"text", "key", "value"})
            if result["key"] is not None:
                self.assertIn(result["key"], settings.BY_KEY)  # never an invented setting
                self.assertFalse(settings.BY_KEY[result["key"]]["secret"])
                settings._check(settings.BY_KEY[result["key"]], result["value"])
            else:
                self.assertIsNone(result["value"])
        return result

    def test_missed_wake_slightly_under_the_peak(self):
        self.assertEqual(self.suggest("missed_wake", peak_wake_score=0.31)["value"], "0.28")
        self.assertEqual(self.suggest("missed_wake", peak_wake_score=0.45)["value"], "0.42")

    def test_missed_wake_floored(self):
        self.assertEqual(self.suggest("missed_wake", peak_wake_score=0.21)["value"], "0.20")

    def test_missed_wake_low_peak_is_mic_advice(self):
        result = self.suggest("missed_wake", peak_wake_score=0.05)
        self.assertIsNone(result["key"])
        self.assertIn("speaking closer", result["text"])
        result = self.suggest("missed_wake", peak_wake_score=0.05, mic_peak=0.005)
        self.assertIsNone(result["key"])
        self.assertIn("Microphone", result["text"])

    def test_missed_wake_muted(self):
        result = self.suggest("missed_wake", peak_wake_score=0.4, muted=True)
        self.assertIsNone(result["key"])
        self.assertIn("muted", result["text"])

    def test_missed_wake_that_fired(self):
        wake = {"t": NOW - 3, "score": 0.7, "ignored": None}
        self.assertIn("did fire", self.suggest("missed_wake", wakes=[wake])["text"])
        wake = {**wake, "ignored": "during an alert"}
        self.assertIn("during an alert", self.suggest("missed_wake", wakes=[wake])["text"])

    def test_false_wake_slightly_over_the_score(self):
        wake = {"t": NOW - 10, "score": 0.62, "ignored": None}
        self.assertEqual(self.suggest("false_wake", wakes=[wake])["value"], "0.67")

    def test_false_wake_capped(self):
        wake = {"t": NOW - 10, "score": 0.88, "ignored": None}
        self.assertEqual(self.suggest("false_wake", wakes=[wake])["value"], "0.90")

    def test_false_wake_over_the_cap_is_advice(self):
        wake = {"t": NOW - 10, "score": 0.97, "ignored": None}
        self.assertIsNone(self.suggest("false_wake", wakes=[wake])["key"])

    def test_false_wake_none_recent(self):
        wake = {"t": NOW - 1000, "score": 0.6, "ignored": None}
        self.assertIsNone(self.suggest("false_wake", wakes=[wake])["key"])

    def test_stopped_explains_what_was_heard(self):
        result = self.suggest("stopped_unprompted", stop_events=[
            stop_event("not_a_stop", "yes sir"), stop_event("stopped", "it stop", "the stamp")])
        self.assertIsNone(result["key"])
        self.assertIn('"it stop"', result["text"])
        self.assertIn('"the stamp"', result["text"])

    def test_stopped_with_whisper_hearing_nothing(self):
        result = self.suggest("stopped_unprompted", stop_events=[stop_event("stopped", "stop", "")])
        self.assertIn("heard nothing", result["text"])

    def test_stopped_by_the_wake_word_has_a_setting(self):
        result = self.suggest("stopped_unprompted",
                              stop_events=[stop_event("wake_stopped", score=0.6)])
        self.assertEqual((result["key"], result["value"]), ("WAKE_THRESHOLD", "0.65"))

    def test_stopped_nothing_recorded(self):
        result = self.suggest("stopped_unprompted",
                              stop_events=[stop_event("stopped", "stop", "stop", ago=1000)])
        self.assertIn("no stop", result["text"])

    def test_missed_stop(self):
        result = self.suggest("missed_stop",
                              stop_events=[stop_event("echo", "stop", "all done"),
                                           stop_event("not_a_stop", "sir")])
        self.assertIsNone(result["key"])
        self.assertIn('"all done"', result["text"])
        self.assertIn("echo", self.suggest("missed_stop",
                                           stop_events=[stop_event("own_words", "stop")])["text"])
        self.assertIn('"sir"', self.suggest("missed_stop",
                                            stop_events=[stop_event("not_a_stop", "sir")])["text"])
        self.assertIn("nothing at all", self.suggest("missed_stop")["text"])

    def test_misheard_cut_off_at_the_limit(self):
        turn = {"heard": "what is the", "endpoint": "max", "age_s": 10}
        result = self.suggest("misheard", last_turn=turn)
        self.assertEqual((result["key"], result["value"]), ("MAX_UTTERANCE_SECONDS", "20"))

    def test_misheard_small_model(self):
        settings.ENV.write_text("WHISPER_MODEL=tiny.en\n")
        result = self.suggest("misheard", last_turn={"heard": "x", "endpoint": "pause", "age_s": 5})
        self.assertEqual((result["key"], result["value"]), ("WHISPER_MODEL", "large-v3-turbo"))

    def test_misheard_advice_and_nothing(self):
        turn = {"heard": "x", "endpoint": "quick", "age_s": 5}
        self.assertIsNone(self.suggest("misheard", last_turn=turn)["key"])
        turn = {"heard": "x", "endpoint": "max", "age_s": 5000}  # too long ago to be about that
        self.assertIsNone(self.suggest("misheard", last_turn=turn))
        self.assertIsNone(self.suggest("misheard"))

    def test_wrong_answer(self):
        result = self.suggest("wrong_answer", last_exchange={"heard": "hi", "reply": "Hello.",
                                                             "tools": ["get_events"]})
        self.assertIsNone(result["key"])
        self.assertIn("get_events", result["text"])
        self.assertIsNone(self.suggest("wrong_answer"))

    def test_every_kind_copes_with_hooks_down(self):
        for kind in reports.KINDS:
            self.suggest(kind, hooks_live=False)


class Stats(Panel):
    async def test_counts_and_latency(self):
        day = time.strftime("%Y-%m-%d")
        rows = [{"time": f"{day}T08:00:00", "follow_up": False, "total_s": 1.0},
                {"time": f"{day}T08:00:10", "follow_up": True, "total_s": 2.0},
                {"time": f"{day}T08:01:00", "follow_up": False, "total_s": None},
                {"time": "2001-01-01T08:00:00", "follow_up": False, "total_s": 9.0}]
        lines = [json.dumps(row) for row in rows] + ["not json"]
        (self.dir / "latency.jsonl").write_text("\n".join(lines) + "\n")
        await self.ok(await self.post("/api/reports", {"kind": "missed_wake"}))
        body = await self.ok(await self.get("/api/stats?days=3"))
        self.assertEqual(len(body["days"]), 3)
        today = body["days"][-1]
        self.assertEqual(today, {"date": day, "wakes": 2, "turns": 3,
                                 "reports": {"missed_wake": 1}, "latency_ms_p50": 1500,
                                 "latency_ms_p90": 1900})
        self.assertEqual(body["days"][0]["latency_ms_p50"], None)
        self.assertEqual(body["summary"], {"wakes": 2, "turns": 3, "latency_ms_p50": 1500,
                                           "latency_ms_p90": 1900})

    async def test_no_data(self):
        body = await self.ok(await self.get("/api/stats"))
        self.assertEqual(len(body["days"]), 14)
        self.assertEqual(body["summary"]["turns"], 0)
        await self.failed(await self.get("/api/stats?days=x"), 400)


class WebFiles(Panel):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        root = self.dir / "web"
        root.mkdir()
        (root / "index.html").write_text("<p>home</p>")
        (root / "app.js").write_text("// js")
        (self.dir / "secret.txt").write_text("private")
        patcher = mock.patch.object(server, "WEB", root)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_files(self):
        self.assertEqual(await (await self.get("/")).text(), "<p>home</p>")
        response = await self.get("/app.js")
        self.assertEqual(await response.text(), "// js")
        self.assertIn("javascript", response.content_type)

    async def test_cannot_escape(self):
        for path in ("/../secret.txt", "/..%2Fsecret.txt", "/%2E%2E/secret.txt"):
            response = await self.get(path)
            self.assertEqual(response.status, 404, path)

    async def test_missing_is_404_json(self):
        await self.failed(await self.get("/nope.js"), 404)
        await self.failed(await self.get("/api/nope"), 404)


if __name__ == "__main__":
    unittest.main()
