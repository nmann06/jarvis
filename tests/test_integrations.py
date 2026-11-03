"""The Setup page's integrations: listing, configured flags, read-only tests, machine checks.

Usage: python -m unittest tests.test_integrations (no network: HTTP and the service libraries
are faked, and .env and the token files are in a temporary folder)
"""

import json
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import requests

from jarvis import integrations, settings

SECRET = "sk-secret-value-123456"


class Reply:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {}

    def json(self):
        return self._body


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.env = self.tmp / ".env"
        patches = [mock.patch.object(settings, "ENV", self.env),
                   mock.patch.object(integrations, "DATA", self.data),
                   mock.patch.object(integrations, "GOOGLE_TOKEN", self.tmp / ".google_token.json"),
                   mock.patch.object(integrations, "SPOTIFY_TOKEN", self.data / "spotify-token.json"),
                   mock.patch.object(integrations, "ONEDRIVE_TOKEN", self.data / "onedrive-token.json"),
                   mock.patch.object(integrations, "KASA_TOKEN", self.data / "kasa-token.json")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def write_env(self, **values):
        self.env.write_text("".join(f"{k}={v}\n" for k, v in values.items()))

    def http(self, handler):
        """Fake requests.request: handler(method, url, **kw) -> Reply, or raises."""
        p = mock.patch("requests.request", side_effect=handler)
        self.addCleanup(p.stop)
        return p.start()


class Listing(Base):
    def test_every_integration_is_complete(self):
        items = integrations.list_integrations()
        ids = [i["id"] for i in items]
        for wanted in ("anthropic", "discord", "icloud", "google", "zoho", "trello", "spotify",
                       "onedrive", "onq", "smith", "kasa", "mcp"):
            self.assertIn(wanted, ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual([i["id"] for i in items if i["required"]], ["anthropic"])
        for i in items:
            self.assertTrue(i["name"] and i["summary"], i["id"])
            self.assertTrue(i["steps"] and all(isinstance(s, str) and s for s in i["steps"]), i["id"])
            self.assertTrue(i["testable"])
            self.assertIn(i["id"], integrations.TESTS)
            for f in i["fields"]:
                self.assertEqual(set(f), {"key", "label", "help", "secret", "set", "value"})
                self.assertEqual(f["secret"], settings.BY_KEY[f["key"]]["secret"], f["key"])

    def test_no_owner_details_in_the_public_text(self):
        text = json.dumps([{k: v for k, v in i.items() if k != "login"}  # the command is this machine's own
                           for i in integrations.list_integrations()])
        for needle in (r"hyde", r"nate\b", r"nmann", r"/home/", r"tail3c1c60", r"mannn"):
            self.assertIsNone(re.search(needle, text, re.I), needle)
        self.assertIsNone(re.search(r"[\w.]+@[\w.]+\.\w+", text), "an email address")
        self.assertNotIn("~/jarvis", text)

    def test_secrets_are_reported_set_never_shown(self):
        self.write_env(ANTHROPIC_API_KEY=SECRET, ICLOUD_CALENDAR="Home")
        items = {i["id"]: i for i in integrations.list_integrations()}
        key = items["anthropic"]["fields"][0]
        self.assertEqual((key["set"], key["value"], key["secret"]), (True, None, True))
        self.assertNotIn(SECRET, json.dumps(items))
        calendar = [f for f in items["icloud"]["fields"] if f["key"] == "ICLOUD_CALENDAR"][0]
        self.assertEqual((calendar["set"], calendar["value"], calendar["secret"]), (True, "Home", False))
        self.assertTrue(items["anthropic"]["configured"])
        self.assertFalse(items["discord"]["configured"])

    def test_configured_looks_at_keys_and_token_files_only(self):
        configured = lambda id: {i["id"]: i for i in integrations.list_integrations()}[id]["configured"]
        self.assertFalse(configured("spotify"))
        self.write_env(SPOTIFY_CLIENT_ID="abc")
        self.assertFalse(configured("spotify"))  # no sign-in yet
        (self.data / "spotify-token.json").write_text("{}")
        self.assertTrue(configured("spotify"))
        self.assertFalse(configured("google"))
        (self.tmp / ".google_token.json").write_text("{}")
        self.assertTrue(configured("google"))
        self.assertFalse(configured("kasa"))
        self.write_env(KASA_USER="a@example.com", KASA_PASSWORD="pw")
        self.assertTrue(configured("kasa"))
        self.assertFalse(configured("onq"))
        (self.data / "onq-cookies.json").write_text("[]")
        self.assertTrue(configured("onq"))

    def test_a_broken_env_does_not_stop_it(self):
        self.env.write_text("DISCORD_OWNER_ID=not a number\n=== garbage\n")
        self.assertTrue(integrations.list_integrations())

    def test_login_commands(self):
        for id in ("google", "onedrive", "onq", "smith"):
            command = integrations.login_command(id)
            self.assertEqual(command[1], "-m", id)
        self.assertEqual(integrations.login_command("smith")[-2:], ["login", "smith"])
        for id in ("anthropic", "spotify", "kasa", "mcp"):
            self.assertIsNone(integrations.login_command(id), id)
        listed = {i["id"]: i for i in integrations.list_integrations()}
        self.assertEqual(listed["google"]["login"]["command"], integrations.login_command("google"))
        self.assertIsNone(listed["spotify"]["login"])
        with self.assertRaises(KeyError):
            integrations.login_command("nope")

    def test_importing_is_light(self):
        code = ("import sys; import jarvis.integrations; "
                "bad = [m for m in ('torch', 'jarvis.config', 'faster_whisper', 'requests', 'caldav', "
                "'googleapiclient', 'sounddevice') if m in sys.modules]; print(bad)")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=settings.ROOT, timeout=30)
        self.assertEqual(out.stdout.strip(), "[]", out.stderr)


class Tests(Base):
    def test_unconfigured_means_not_set_up_and_no_network(self):
        request = self.http(lambda *a, **k: self.fail("network used"))
        for id in ("anthropic", "discord", "icloud", "zoho", "trello", "spotify", "onedrive", "mcp"):
            r = integrations.test(id)
            self.assertFalse(r["ok"], id)
            self.assertIn("Not set up yet", r["message"], id)
        request.assert_not_called()

    def test_anthropic(self):
        self.write_env(ANTHROPIC_API_KEY=SECRET)
        calls = self.http(lambda method, url, **kw: Reply(200, {"data": []}))
        r = integrations.test("anthropic")
        self.assertTrue(r["ok"], r)
        method, url = calls.call_args.args[:2]
        self.assertEqual((method, url), ("GET", "https://api.anthropic.com/v1/models"))
        self.assertEqual(calls.call_args.kwargs["headers"]["x-api-key"], SECRET)
        self.assertLessEqual(calls.call_args.kwargs["timeout"], 10)
        calls.side_effect = lambda *a, **k: Reply(401)
        r = integrations.test("anthropic")
        self.assertFalse(r["ok"])
        self.assertIn("rejected", r["message"])

    def test_network_errors_never_leak_the_key(self):
        self.write_env(ANTHROPIC_API_KEY=SECRET, TRELLO_KEY="trellokey1234", TRELLO_TOKEN="trellotoken5678")

        def boom(method, url, **kw):
            raise requests.exceptions.ConnectionError(f"failed {url} {kw}")
        self.http(boom)
        for id in ("anthropic", "trello"):
            r = integrations.test(id)
            self.assertFalse(r["ok"])
            for secret in (SECRET, "trellokey1234", "trellotoken5678"):
                self.assertNotIn(secret, r["message"])
            self.assertIn("internet", r["message"])

    def test_unexpected_errors_are_named_not_quoted_and_secrets_scrubbed(self):
        self.write_env(ANTHROPIC_API_KEY=SECRET)
        with mock.patch.dict(integrations.TESTS, {"anthropic": mock.Mock(side_effect=RuntimeError(SECRET))}):
            r = integrations.test("anthropic")
        self.assertFalse(r["ok"])
        self.assertNotIn(SECRET, r["message"])
        self.assertIn("RuntimeError", r["message"])
        with mock.patch.dict(integrations.TESTS, {"anthropic": lambda v: f"fine {SECRET}"}):
            self.assertNotIn(SECRET, integrations.test("anthropic")["message"])

    def test_a_slow_test_gives_up(self):
        self.write_env(ANTHROPIC_API_KEY=SECRET)
        with mock.patch.dict(integrations.TESTS, {"anthropic": lambda v: time.sleep(2)}), \
                mock.patch.object(integrations, "TIMEOUT", 0.2):
            start = time.time()
            r = integrations.test("anthropic")
        self.assertFalse(r["ok"])
        self.assertLess(time.time() - start, 1.5)
        self.assertIn("No answer", r["message"])

    def test_discord_only_reads_the_bot_profile(self):
        self.write_env(DISCORD_TOKEN=SECRET)
        calls = self.http(lambda m, u, **kw: Reply(200, {"username": "jarvis"}))
        r = integrations.test("discord")
        self.assertTrue(r["ok"])
        self.assertIn("user ID", r["message"])  # owner id not set yet
        self.assertEqual(calls.call_args.args[:2], ("GET", "https://discord.com/api/v10/users/@me"))
        self.assertEqual(calls.call_args.kwargs["headers"]["Authorization"], f"Bot {SECRET}")
        calls.side_effect = lambda *a, **k: Reply(401)
        self.assertFalse(integrations.test("discord")["ok"])

    def test_trello_checks_the_board_name(self):
        self.write_env(TRELLO_KEY="k1234", TRELLO_TOKEN="t5678", TRELLO_BOARD="todo")
        calls = self.http(lambda m, u, **kw: Reply(200, [{"name": "Todo"}, {"name": "Other"}]))
        r = integrations.test("trello")
        self.assertTrue(r["ok"])
        self.assertNotIn("no board", r["message"])
        self.assertEqual(calls.call_args.args[:2], ("GET", "https://api.trello.com/1/members/me/boards"))
        self.write_env(TRELLO_KEY="k1234", TRELLO_TOKEN="t5678", TRELLO_BOARD="missing")
        self.assertIn("no board called missing", integrations.test("trello")["message"])
        calls.side_effect = lambda *a, **k: Reply(401)
        self.assertFalse(integrations.test("trello")["ok"])

    def test_spotify_refreshes_an_old_token_and_lists_devices(self):
        self.write_env(SPOTIFY_CLIENT_ID="client1234", SPOTIFY_SPEAKER="Jarvis")
        token = self.data / "spotify-token.json"
        self.assertIn("Not signed in", integrations.test("spotify")["message"])
        token.write_text(json.dumps({"access_token": "old", "refresh_token": "r1", "expires_at": 0}))

        def handler(method, url, **kw):
            if url.endswith("/api/token"):
                return Reply(200, {"access_token": "new", "expires_in": 3600, "refresh_token": "r2"})
            self.assertEqual((method, url), ("GET", "https://api.spotify.com/v1/me/player/devices"))
            self.assertEqual(kw["headers"]["Authorization"], "Bearer new")
            return Reply(200, {"devices": [{"name": "Jarvis"}, {"name": "Phone"}]})
        self.http(handler)
        r = integrations.test("spotify")
        self.assertTrue(r["ok"], r)
        self.assertIn("2 devices", r["message"])
        self.assertIn("online", r["message"])
        saved = json.loads(token.read_text())
        self.assertEqual((saved["access_token"], saved["refresh_token"]), ("new", "r2"))
        self.assertEqual(token.stat().st_mode & 0o077, 0)

    def test_spotify_revoked_login(self):
        self.write_env(SPOTIFY_CLIENT_ID="client1234")
        (self.data / "spotify-token.json").write_text(
            json.dumps({"access_token": "old", "refresh_token": "r1", "expires_at": 0}))
        self.http(lambda *a, **k: Reply(400, {"error": "invalid_grant"}))
        r = integrations.test("spotify")
        self.assertFalse(r["ok"])
        self.assertIn("expired or was revoked", r["message"])

    def test_onedrive(self):
        self.write_env(ONEDRIVE_CLIENT_ID="app1234", ONEDRIVE_TENANT="example.edu")
        (self.data / "onedrive-token.json").write_text(
            json.dumps({"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 3600}))
        calls = self.http(lambda m, u, **kw: Reply(200, {"driveType": "personal"}))
        r = integrations.test("onedrive")
        self.assertTrue(r["ok"], r)
        self.assertEqual(calls.call_args.args[:2], ("GET", "https://graph.microsoft.com/v1.0/me/drive"))
        calls.side_effect = lambda *a, **k: Reply(401)
        self.assertFalse(integrations.test("onedrive")["ok"])

    def test_brightspace_sites_check_the_session_with_whoami(self):
        for id, host in (("onq", "https://onq.queensu.ca"), ("smith", "https://smithlearning.ca")):
            self.assertIn("Not signed in", integrations.test(id)["message"])
            (self.data / f"{id}-cookies.json").write_text(json.dumps(
                [{"name": "d2lSessionVal", "value": "v", "domain": host.split("//")[1], "path": "/"}]))
            calls = self.http(lambda m, u, **kw: Reply(200))
            self.assertTrue(integrations.test(id)["ok"])
            self.assertEqual(calls.call_args.args[0], "GET")
            self.assertEqual(calls.call_args.args[1], f"{host}/d2l/api/lp/1.64/users/whoami")
            calls.side_effect = lambda *a, **k: Reply(302)
            r = integrations.test(id)
            self.assertFalse(r["ok"])
            self.assertIn("expired", r["message"])

    def test_icloud(self):
        self.write_env(ICLOUD_USER="someone@example.com", ICLOUD_APP_PASSWORD="abcd-efgh", ICLOUD_CALENDAR="Jarvis")
        import caldav
        from caldav.lib import error
        cal = mock.Mock()
        cal.get_display_name.return_value = "Jarvis"
        client = mock.Mock()
        client.principal.return_value.calendars.return_value = [cal, cal]
        with mock.patch.object(caldav, "DAVClient", return_value=client) as dav:
            r = integrations.test("icloud")
        self.assertTrue(r["ok"], r)
        self.assertEqual(dav.call_args.args[0], "https://caldav.icloud.com")
        self.assertLessEqual(dav.call_args.kwargs["timeout"], 10)
        cal.get_display_name.return_value = "Other"
        with mock.patch.object(caldav, "DAVClient", return_value=client):
            self.assertIn("no calendar called Jarvis", integrations.test("icloud")["message"])
        client.principal.side_effect = error.AuthorizationError("401 for abcd-efgh")
        with mock.patch.object(caldav, "DAVClient", return_value=client):
            r = integrations.test("icloud")
        self.assertFalse(r["ok"])
        self.assertNotIn("abcd-efgh", r["message"])

    def test_zoho_logs_in_and_out_only(self):
        self.write_env(ZOHO_USER="someone@example.com", ZOHO_APP_PASSWORD="app-pass-1", ZOHO_IMAP_HOST="imap.example.com")
        import imaplib
        with mock.patch.object(imaplib, "IMAP4_SSL") as imap:
            r = integrations.test("zoho")
            self.assertTrue(r["ok"], r)
            imap.assert_called_once_with("imap.example.com", timeout=10)
            self.assertEqual([c[0] for c in imap.return_value.method_calls], ["login", "logout"])
        with mock.patch.object(imaplib, "IMAP4_SSL") as imap:
            imap.return_value.login.side_effect = imaplib.IMAP4.error("LOGIN failed for app-pass-1")
            r = integrations.test("zoho")
        self.assertFalse(r["ok"])
        self.assertNotIn("app-pass-1", r["message"])
        with mock.patch.object(imaplib, "IMAP4_SSL", side_effect=OSError("no route")):
            self.assertIn("Couldn't reach", integrations.test("zoho")["message"])

    def test_google_reads_the_profile_and_sheet_title(self):
        self.write_env(GOOGLE_SHEET_ID="sheet123")
        self.assertIn("Not signed in", integrations.test("google")["message"])
        (self.tmp / ".google_token.json").write_text("{}")
        from google.auth.exceptions import RefreshError
        creds = mock.Mock(valid=True)
        service = mock.Mock()
        service.users.return_value.getProfile.return_value.execute.return_value = {"messagesTotal": 42}
        service.spreadsheets.return_value.get.return_value.execute.return_value = {"properties": {"title": "Jobs"}}
        with mock.patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=creds), \
                mock.patch("googleapiclient.discovery.build", return_value=service):
            r = integrations.test("google")
        self.assertTrue(r["ok"], r)
        self.assertIn("42", r["message"])
        self.assertIn("Jobs", r["message"])
        creds.valid = False
        creds.refresh.side_effect = RefreshError("invalid_grant secret-detail")
        with mock.patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=creds), \
                mock.patch("googleapiclient.discovery.build", return_value=service):
            r = integrations.test("google")
        self.assertFalse(r["ok"])
        self.assertIn("Sign in again", r["message"])
        self.assertNotIn("secret-detail", r["message"])

    def test_kasa_uses_the_saved_token_only(self):
        self.assertIn("Not signed in", integrations.test("kasa")["message"])
        (self.data / "kasa-token.json").write_text(json.dumps({"token": "t", "host": "h", "term": "x"}))
        manager = mock.Mock()

        async def devices():
            return [object(), object(), object()]
        manager.get_devices = devices
        with mock.patch("tplinkcloud.TPLinkDeviceManager", return_value=manager) as cls:
            r = integrations.test("kasa")
        self.assertTrue(r["ok"], r)
        self.assertIn("3 switches", r["message"])
        manager.set_auth_token.assert_called_once_with("t")
        self.assertFalse(cls.call_args.kwargs["prefetch"])

        from tplinkcloud.exceptions import TPLinkTokenExpiredError

        async def expired():
            raise TPLinkTokenExpiredError("token expired: t")
        manager.get_devices = expired
        with mock.patch("tplinkcloud.TPLinkDeviceManager", return_value=manager):
            r = integrations.test("kasa")
        self.assertFalse(r["ok"])
        self.assertIn("expired", r["message"])

    def test_mcp_pings_locally_and_looks_at_funnel(self):
        self.write_env(MCP_TOKEN=SECRET, MCP_PORT="8766")
        calls = self.http(lambda m, u, **kw: Reply(200, {"result": {}}))
        funnel = subprocess.CompletedProcess([], 0, "https://x.ts.net (Funnel on)\n|-- / proxy http://127.0.0.1:8766\n", "")
        with mock.patch("shutil.which", return_value="/usr/bin/tailscale"), \
                mock.patch("subprocess.run", return_value=funnel):
            r = integrations.test("mcp")
        self.assertTrue(r["ok"], r)
        self.assertEqual(calls.call_args.args[:2], ("POST", f"http://127.0.0.1:8766/mcp/{SECRET}"))
        self.assertEqual(calls.call_args.kwargs["json"]["method"], "ping")
        off = subprocess.CompletedProcess([], 0, "No serve config\n", "")
        with mock.patch("shutil.which", return_value="/usr/bin/tailscale"), mock.patch("subprocess.run", return_value=off):
            r = integrations.test("mcp")
        self.assertFalse(r["ok"])
        self.assertIn("funnel --bg 8766", r["message"])
        self.assertNotIn(SECRET, r["message"])
        calls.side_effect = lambda *a, **k: Reply(404)
        self.assertIn("Restart Jarvis", integrations.test("mcp")["message"])

    def test_claude_code(self):
        status = lambda out: subprocess.CompletedProcess([], 0, out, "")
        with mock.patch.object(integrations, "_claude_bin", return_value=None):
            self.assertIn("not installed", integrations.test("claude_code")["message"].lower().replace("isn't", "not"))
        with mock.patch.object(integrations, "_claude_bin", return_value="/bin/claude"):
            with mock.patch("subprocess.run", return_value=status('{"loggedIn": true, "email": "x@example.com"}')) as run:
                r = integrations.test("claude_code")
                self.assertEqual(run.call_args.args[0], ["/bin/claude", "auth", "status"])
            self.assertTrue(r["ok"])
            self.assertNotIn("example.com", r["message"])
            with mock.patch("subprocess.run", return_value=status('{"loggedIn": false}')):
                self.assertFalse(integrations.test("claude_code")["ok"])


class Checks(Base):
    def test_shape_and_one_failure_does_not_hide_the_rest(self):
        with mock.patch.object(integrations, "_run", return_value=None), \
                mock.patch.object(integrations, "_audio_devices", return_value=None), \
                mock.patch.object(integrations, "_venv_python", return_value=None), \
                mock.patch.object(integrations, "_check_echo", side_effect=RuntimeError("boom")):
            out = integrations.checks()
        self.assertEqual([c["id"] for c in out], ["venv", "env", "gpu", "models", "mic", "speaker",
                                                  "echo_cancel", "service"])
        for c in out:
            self.assertEqual(set(c), {"id", "name", "ok", "detail", "fix"})
            self.assertTrue(c["detail"] and c["fix"], c["id"])
        failed = {c["id"]: c["ok"] for c in out}
        self.assertFalse(failed["gpu"])
        self.assertFalse(failed["echo_cancel"])
        self.assertIn("RuntimeError", [c for c in out if c["id"] == "echo_cancel"][0]["detail"])

    def test_gpu_not_needed_on_cpu(self):
        with mock.patch.object(integrations, "_run", return_value=None):
            self.assertFalse(integrations._check_gpu({})["ok"])
            self.assertTrue(integrations._check_gpu({"WHISPER_DEVICE": "cpu"})["ok"])
        smi = subprocess.CompletedProcess([], 0, "Some GPU, 595.0, 6144 MiB\n", "")
        with mock.patch.object(integrations, "_run", return_value=smi):
            self.assertTrue(integrations._check_gpu({})["ok"])

    def test_models_are_looked_up_on_disk(self):
        cache = self.tmp / "hf"
        with mock.patch.object(integrations, "ROOT", self.tmp), mock.patch.object(integrations, "_hf_cache", return_value=cache):
            r = integrations._check_models({})
            self.assertFalse(r["ok"])
            for part in ("voice", "stop-word", "wake word", "Whisper"):
                self.assertIn(part, r["detail"])
            (self.tmp / "models/piper").mkdir(parents=True)
            (self.tmp / "models/piper/en_GB-alan-medium.onnx").touch()
            (self.tmp / "models/piper/en_GB-alan-medium.onnx.json").touch()
            (self.tmp / "models/vosk-model-small-en-us-0.15/am").mkdir(parents=True)
            (self.tmp / "models/vosk-model-small-en-us-0.15/am/final.mdl").touch()
            wake = self.tmp / ".venv/lib/python3.12/site-packages/openwakeword/resources/models"
            wake.mkdir(parents=True)
            (wake / "hey_jarvis_v0.1.onnx").touch()
            snap = cache / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/abc"
            snap.mkdir(parents=True)
            (snap / "model.bin").touch()
            self.assertTrue(integrations._check_models({})["ok"])

    def test_devices_follow_the_settings(self):
        audio = {"devices": [[0, "HDA Intel", 0, 2], [1, "USB Webcam Mic", 1, 0], [2, "Headset", 1, 2]],
                 "default": [1, 0]}
        self.assertEqual(integrations._pick(audio, "", 2, "microphone"), ("USB Webcam Mic", None))
        self.assertEqual(integrations._pick(audio, "head", 3, "speaker"), ("Headset", None))
        self.assertEqual(integrations._pick(audio, "0", 3, "speaker"), ("HDA Intel", None))
        self.assertIsNone(integrations._pick(audio, "nothing", 2, "microphone")[0])
        self.assertIsNone(integrations._pick(audio, "0", 2, "microphone")[0])  # no input channels
        self.assertEqual(integrations._pick(audio, "", 3, "speaker")[0], "HDA Intel")  # default has none: any

    def test_service_check_reads_systemd(self):
        def run(cmd, timeout=10):
            out = {"is-active": "active\n", "is-enabled": "enabled\n"}.get(cmd[2], "Linger=yes\n")
            return subprocess.CompletedProcess(cmd, 0, out, "")
        with mock.patch.object(integrations, "_run", side_effect=run):
            r = integrations._check_service()
        self.assertTrue(r["ok"])
        self.assertIn("active", r["detail"])
        with mock.patch.object(integrations, "_run", return_value=subprocess.CompletedProcess([], 3, "inactive\n", "")):
            self.assertFalse(integrations._check_service()["ok"])

    def test_env_check_wants_a_private_file(self):
        self.write_env(A="1")
        self.env.chmod(0o644)
        self.assertFalse(integrations._check_env()["ok"])
        self.env.chmod(0o600)
        self.assertTrue(integrations._check_env()["ok"])


if __name__ == "__main__":
    unittest.main()
