"""The panel's HTTP server: every route in docs/panel-api.md, and the web UI from app/web/."""

import asyncio
import hmac
import importlib
import ipaddress
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

from aiohttp import web

from jarvis import settings
from jarvis.panel import hooks, reports

log = logging.getLogger("jarvis.panel")

UNIT = "jarvis"
WEB = settings.ROOT / "app" / "web"
LOCALHOST = "127.0.0.1"
COOKIE = "panel"
COOKIE_SECONDS = 365 * 24 * 3600
KEEPALIVE_SECONDS = 15
TEST_SECONDS = 15
MAX_LOG_LINES = 2000
MAX_NOTE = 4000
MAX_STATS_DAYS = 366
MAX_JOBS = 20
MAX_JOB_OUTPUT = 65536
LINE_LIMIT = 1 << 20
TAILSCALE_RETRY_SECONDS = 60
STARTING_SECONDS = 120  # the voice loop loads its models before the hooks start

TOKEN = web.AppKey("token", str)
HUB = web.AppKey("hub", hooks.Hub)
JOBS = web.AppKey("jobs", dict)
TASKS = web.AppKey("tasks", list)


class Fail(Exception):
    """Ends a request with this status and {"error": message}."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _error(status, message):
    return web.json_response({"error": message}, status=status)


def load_token(path=None):
    """The panel's token, made at first start (owner only)."""
    path = path or reports.DATA / "panel-token"
    path.parent.mkdir(exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        os.chmod(path, 0o600)
        return path.read_text().strip()
    with os.fdopen(fd, "w") as f:
        token = secrets.token_urlsafe(32)
        f.write(token)
    return token


def _authorized(request):
    token = request.app[TOKEN].encode()
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer ") and hmac.compare_digest(header[7:].encode(), token):
        return True
    return hmac.compare_digest(request.cookies.get(COOKIE, "").encode(), token)


@web.middleware
async def guard(request, handler):
    """The token, JSON POST bodies, and every failure as {"error": ...}."""
    try:
        if request.path != "/login":
            if not _authorized(request):
                return _error(401, "Not signed in: this needs the panel's token.")
            if request.method == "POST" and request.content_type != "application/json":
                return _error(415, "Send this as application/json.")
        return await handler(request)
    except Fail as e:
        return _error(e.status, e.message)
    except web.HTTPException as e:
        if e.status < 400:
            raise
        return _error(e.status, e.reason)
    except ConnectionResetError:
        raise
    except Exception:
        log.exception("%s %s failed", request.method, request.path)
        return _error(500, "Something went wrong in the panel: see its log.")


async def _body(request):
    """A POST's JSON object ({} if empty)."""
    try:
        raw = await request.text()
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise Fail(400, "That isn't valid JSON.") from None
    if not isinstance(body, dict):
        raise Fail(400, "Send a JSON object.")
    return body


def _text_field(body, name):
    value = body.get(name)
    if not isinstance(value, str):
        raise Fail(400, f'"{name}" must be text.')
    return value


async def _run(args, timeout=10):
    """(exit code, output) of a command; (None, why) if it couldn't be run."""
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=settings.ROOT)
    except OSError as e:
        return None, str(e)
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return None, f"{args[0]} took more than {timeout} s"
    return process.returncode, out.decode(errors="replace").strip()


# Login and the web UI

async def login(request):
    """GET /login?token=...: sets the cookie and goes to the UI."""
    given = request.query.get("token", "").encode()
    if not hmac.compare_digest(given, request.app[TOKEN].encode()):
        return _error(401, "That token isn't right.")
    response = web.Response(status=302, headers={"Location": "/"})
    response.set_cookie(COOKIE, request.app[TOKEN], max_age=COOKIE_SECONDS, path="/", httponly=True,
                        samesite="Strict")
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def web_file(request):
    """Files under app/web/, with / as index.html."""
    name = request.match_info["path"] or "index.html"
    if not name.startswith("api/"):
        root = WEB.resolve()
        path = (root / name).resolve()
        if path.is_relative_to(root) and path.is_file():
            return web.FileResponse(path, headers={"Cache-Control": "no-cache"})
    raise Fail(404, f"Nothing at /{name}.")


# Status and light

_SERVICE_STATES = ("active", "inactive", "failed")


async def _service_state():
    _, out = await _run(["systemctl", "--user", "is-active", UNIT])
    return out if out in _SERVICE_STATES else "unknown"


async def _version():
    code, out = await _run(["git", "rev-parse", "--short", "HEAD"])
    return out if code == 0 else "unknown"


async def _started(hub):
    """When the running Jarvis started (seconds since the epoch), or None."""
    if hub.live and hub.started:
        return hub.started
    code, out = await _run(["systemctl", "--user", "show", UNIT, "-p", "ActiveEnterTimestamp",
                            "--timestamp=unix"])
    m = re.search(r"=@(\d+)", out) if code == 0 else None
    return float(m[1]) if m else None


async def _restart_needed(hub, service):
    """.env has changed since the running Jarvis read it."""
    if service != "active":
        return False
    started = await _started(hub)
    try:
        return started is not None and settings.ENV.stat().st_mtime > started
    except OSError:
        return False


async def _hooks_reason(hub, service):
    """Why the hooks aren't live: None if they are, voice_off (a restart wouldn't help), starting,
    needs_restart (the running Jarvis predates them), or down."""
    if hub.live:
        return None
    if service != "active":
        return "down"
    if (settings.env_values().get("VOICE_ENABLED") or "on") != "on":  # as jarvis.config reads it
        return "voice_off"
    started = await _started(hub)
    if started is not None and time.time() - started < STARTING_SECONDS:
        return "starting"
    return "needs_restart"


async def status(request):
    hub = request.app[HUB]
    service = await _service_state()
    return web.json_response({"service": service, "hooks_live": hub.live,
                              "hooks_reason": await _hooks_reason(hub, service), "state": hub.state,
                              "muted": hub.live and hub.muted, "version": await _version(),
                              "restart_needed": await _restart_needed(hub, service)})


async def _sse_response(request):
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                           "Cache-Control": "no-cache",
                                           "X-Accel-Buffering": "no"})
    await response.prepare(request)
    return response


def _sse(message):
    return f"data: {json.dumps(message)}\n\n".encode()


async def stream_events(request):
    hub = request.app[HUB]
    client = hub.subscribe()
    response = await _sse_response(request)
    try:
        now = time.time()
        await response.write(_sse({"type": "hooks", "live": hub.live, "t": now}))
        await response.write(_sse({"type": "state", "state": hub.state, "t": now}))
        while True:
            try:
                message = await asyncio.wait_for(client.get(), KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                await response.write(b": keepalive\n\n")
                continue
            if message is None:  # the panel is shutting down
                return response
            await response.write(_sse(message))
    except ConnectionResetError:  # the browser went away
        return response
    finally:
        hub.unsubscribe(client)


# Settings

async def get_settings(request):
    return web.json_response(settings.listing())


async def post_settings(request):
    body = await _body(request)
    try:
        return web.json_response(settings.set_setting(_text_field(body, "key"),
                                                      _text_field(body, "value")))
    except ValueError as e:
        raise Fail(400, str(e)) from None


async def post_secrets(request):
    body = await _body(request)
    try:
        return web.json_response(settings.set_secret(_text_field(body, "key"),
                                                     _text_field(body, "value")))
    except ValueError as e:
        raise Fail(400, str(e)) from None


# Service

async def post_service(request):
    action = (await _body(request)).get("action")
    if action not in ("start", "stop", "restart"):
        raise Fail(400, "The action is start, stop or restart.")
    if action in ("start", "restart") and (problem := await asyncio.to_thread(settings.check_env)):
        raise Fail(400, problem)
    code, out = await _run(["systemctl", "--user", action, UNIT], timeout=60)
    if code != 0:
        raise Fail(500, f"Couldn't {action} Jarvis: {reports.mask(out) or f'exit {code}'}")
    done = {"start": "started", "stop": "stopped", "restart": "restarted"}[action]
    return web.json_response({"message": f"Jarvis {done}."})


async def post_mute(request):
    muted = (await _body(request)).get("muted")
    if not isinstance(muted, bool):
        raise Fail(400, '"muted" must be true or false.')
    now = await hooks.mute(muted)
    if now is None:
        raise Fail(409, "Jarvis needs a restart before the mic can be muted from here.")
    request.app[HUB].muted = now
    return web.json_response({"muted": now})


async def get_logs(request):
    try:
        lines = int(request.query.get("lines", "200"))
    except ValueError:
        raise Fail(400, "lines must be a number.") from None
    lines = max(1, min(lines, MAX_LOG_LINES))
    code, out = await _run(["journalctl", "--user", "-u", UNIT, "-n", str(lines), "--no-pager",
                            "-o", "short"], timeout=20)
    if code is None:
        raise Fail(500, f"Couldn't read the log: {out}")
    return web.json_response({"lines": [reports.mask(line) for line in out.splitlines()]})


async def _follow_log():
    """The journalctl following Jarvis's log."""
    return await asyncio.create_subprocess_exec(
        "journalctl", "--user", "-u", UNIT, "-f", "-n", "0", "--no-pager", "-o", "short",
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        limit=LINE_LIMIT)


async def stream_logs(request):
    try:
        process = await _follow_log()
    except OSError as e:
        raise Fail(500, f"Couldn't read the log: {e}") from None
    response = await _sse_response(request)
    try:
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                await response.write(b": keepalive\n\n")
                continue
            except ValueError:  # one enormous line
                continue
            if not line:
                return response
            text = reports.mask(line.decode(errors="replace").rstrip("\n"))
            await response.write(_sse({"line": text}))
    except ConnectionResetError:  # the browser went away
        return response
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


# Setup

def _integrations():
    """jarvis.integrations, imported only when asked for (it may need a working .env)."""
    try:
        return importlib.import_module("jarvis.integrations")
    except (Exception, SystemExit) as e:
        log.warning("jarvis.integrations isn't available: %s", e)
        raise Fail(503, "The setup checks aren't available: jarvis/integrations.py is missing or "
                        "failed to load. See the panel's log.") from None


async def get_integrations(request):
    module = _integrations()
    return web.json_response({"integrations": await asyncio.to_thread(module.list_integrations)})


async def test_integration(request):
    module = _integrations()
    try:
        result = await asyncio.wait_for(asyncio.to_thread(module.test, request.match_info["id"]),
                                        TEST_SECONDS)
    except asyncio.TimeoutError:
        result = {"ok": False, "message": f"No answer within {TEST_SECONDS} s."}
    except (KeyError, LookupError):
        raise Fail(404, "There's no such integration.") from None
    except Exception as e:
        log.warning("test of %s failed: %s", request.match_info["id"], type(e).__name__)
        result = {"ok": False, "message": f"The test itself failed: {e}"}
    return web.json_response({"ok": bool(result["ok"]), "message": reports.mask(result["message"])})


def _xauthority():
    """The X cookie file of a desktop session, or None."""
    runtime = os.getenv("XDG_RUNTIME_DIR")
    for path in ([Path(runtime) / "gdm" / "Xauthority"] if runtime else []) + [Path.home() / ".Xauthority"]:
        if path.exists():
            return str(path)
    return None


async def _login_env():
    """The environment for a sign-in command. OnQ and Smith open a browser on the laptop's screen,
    and a service started before the desktop has no DISPLAY of its own: the user manager knows the
    session's, then this process, then :0, as jarvis.tools.onq assumes."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}  # its output shows while it waits
    code, out = await _run(["systemctl", "--user", "show-environment"])
    session = dict(line.split("=", 1) for line in out.splitlines() if "=" in line) if code == 0 else {}
    env["DISPLAY"] = session.get("DISPLAY") or env.get("DISPLAY") or ":0"
    if xauthority := session.get("XAUTHORITY") or env.get("XAUTHORITY") or _xauthority():
        env["XAUTHORITY"] = xauthority
    return env


async def _run_job(job, command):
    """Run a sign-in command, gathering its output in job."""
    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, cwd=settings.ROOT, env=await _login_env())
    except OSError as e:
        job.update(running=False, output=f"Couldn't start it: {e}", exit=-1)
        return
    try:
        while chunk := await process.stdout.read(4096):
            job["output"] = (job["output"] + chunk.decode(errors="replace"))[-MAX_JOB_OUTPUT:]
        job.update(running=False, exit=await process.wait())
    finally:
        if process.returncode is None:  # the panel is shutting down
            process.kill()
            await process.wait()


async def login_integration(request):
    module = _integrations()
    try:
        command = await asyncio.to_thread(module.login_command, request.match_info["id"])
    except (KeyError, LookupError):
        raise Fail(404, "There's no such integration.") from None
    if not command:
        raise Fail(400, "This one needs no sign-in.")
    if command[0] in ("python", "python3"):
        command = [sys.executable, *command[1:]]  # the venv's, whatever is on the PATH
    jobs = request.app[JOBS]
    job_id = secrets.token_hex(6)
    while len(jobs) >= MAX_JOBS:  # the oldest finished one, if any, makes room
        finished = next((i for i, j in jobs.items() if not j["running"]), None)
        if finished is None:
            raise Fail(429, "Too many sign-ins are running.")
        del jobs[finished]
    jobs[job_id] = job = {"running": True, "output": "", "exit": None}
    task = asyncio.create_task(_run_job(job, command))
    request.app[TASKS].append(task)
    task.add_done_callback(request.app[TASKS].remove)
    return web.json_response({"job": job_id})


async def get_job(request):
    job = request.app[JOBS].get(request.match_info["id"])
    if job is None:
        raise Fail(404, "There's no such job.")
    return web.json_response({**job, "output": reports.mask(job["output"])})


async def get_checks(request):
    module = _integrations()
    return web.json_response({"checks": await asyncio.to_thread(module.checks)})


# Improve

async def post_report(request):
    body = await _body(request)
    kind, note = body.get("kind"), body.get("note", "")
    if kind not in reports.KINDS:
        raise Fail(400, f"The kind is one of {', '.join(reports.KINDS)}.")
    if not isinstance(note, str):
        raise Fail(400, '"note" must be text.')
    snapshot = await hooks.snapshot()  # first, so it's what was going on at the press
    report = await asyncio.to_thread(reports.create, kind, note[:MAX_NOTE], snapshot)
    return web.json_response(report)


async def get_reports(request):
    return web.json_response({"reports": await asyncio.to_thread(reports.listing)})


def _no_report():
    return Fail(404, "There's no such report.")


async def report_audio(request):
    path = reports.audio_path(request.match_info["id"])
    if path is None:
        raise Fail(404, "This report has no audio.")
    return web.FileResponse(path, headers={"Content-Type": "audio/wav"})


async def report_apply(request):
    try:
        result = reports.apply(request.match_info["id"])
    except ValueError as e:
        raise Fail(400, str(e)) from None
    if result is None:
        raise _no_report()
    return web.json_response(result)


async def report_status(request):
    state = (await _body(request)).get("status")
    if state not in reports.STATUSES:
        raise Fail(400, f"The status is one of {', '.join(reports.STATUSES)}.")
    report = reports.set_status(request.match_info["id"], state)
    if report is None:
        raise _no_report()
    return web.json_response(report)


async def report_delete(request):
    if not reports.delete(request.match_info["id"]):
        raise _no_report()
    return web.json_response({"message": "Deleted."})


# Stats

async def get_stats(request):
    try:
        days = int(request.query.get("days", "14"))
    except ValueError:
        raise Fail(400, "days must be a number.") from None
    days = max(1, min(days, MAX_STATS_DAYS))
    return web.json_response(await asyncio.to_thread(reports.stats, days))


# The app

async def _start_hub(app):
    task = asyncio.create_task(app[HUB].run())
    app[TASKS].append(task)
    task.add_done_callback(app[TASKS].remove)


async def _close_streams(app):
    app[HUB].close()


async def _stop_tasks(app):
    tasks = list(app[TASKS])
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def create_app(token):
    app = web.Application(middlewares=[guard])
    app[TOKEN], app[HUB], app[JOBS], app[TASKS] = token, hooks.Hub(), {}, []
    app.on_startup.append(_start_hub)
    app.on_shutdown.append(_close_streams)
    app.on_cleanup.append(_stop_tasks)
    r = app.router
    r.add_get("/login", login)
    r.add_get("/api/status", status)
    r.add_get("/api/events", stream_events)
    r.add_get("/api/settings", get_settings)
    r.add_post("/api/settings", post_settings)
    r.add_post("/api/secrets", post_secrets)
    r.add_post("/api/service", post_service)
    r.add_post("/api/mute", post_mute)
    r.add_get("/api/logs", get_logs)
    r.add_get("/api/logs/stream", stream_logs)
    r.add_get("/api/integrations", get_integrations)
    r.add_post("/api/integrations/{id}/test", test_integration)
    r.add_post("/api/integrations/{id}/login", login_integration)
    r.add_get("/api/jobs/{id}", get_job)
    r.add_get("/api/checks", get_checks)
    r.add_post("/api/reports", post_report)
    r.add_get("/api/reports", get_reports)
    r.add_get("/api/reports/{id}/audio", report_audio)
    r.add_post("/api/reports/{id}/apply", report_apply)
    r.add_post("/api/reports/{id}/status", report_status)
    r.add_delete("/api/reports/{id}", report_delete)
    r.add_get("/api/stats", get_stats)
    r.add_get("/{path:.*}", web_file)
    return app


def port():
    """PANEL_PORT: the environment, then .env, then the default."""
    value = os.getenv("PANEL_PORT") or settings.env_values().get("PANEL_PORT")
    try:
        return int(value or settings.BY_KEY["PANEL_PORT"]["default"])
    except ValueError:
        log.warning("PANEL_PORT %r isn't a number: using the default", value)
        return int(settings.BY_KEY["PANEL_PORT"]["default"])


async def tailscale_ip():
    """This machine's Tailscale address, or None. Anything but a Tailscale address is refused,
    so the panel can't end up listening on another network."""
    code, out = await _run(["tailscale", "ip", "-4"])
    if code != 0:
        return None
    for line in out.splitlines():
        try:
            if ipaddress.ip_address(line.strip()) in ipaddress.ip_network("100.64.0.0/10"):
                return line.strip()
        except ValueError:
            continue
    return None


async def _listen_on_tailscale(runner, at):
    """Listen on the Tailscale address once there is one (Tailscale may start after the panel)."""
    while True:
        address = await tailscale_ip()
        if address:
            try:
                await web.TCPSite(runner, address, at).start()
                log.info("listening on %s:%d", address, at)
                return
            except OSError as e:
                log.warning("couldn't listen on %s:%d: %s", address, at, e)
        await asyncio.sleep(TAILSCALE_RETRY_SECONDS)


async def serve():
    runner = web.AppRunner(create_app(load_token()), access_log=None, shutdown_timeout=2.0)
    await runner.setup()
    at = port()
    try:
        await web.TCPSite(runner, LOCALHOST, at).start()
        log.info("listening on %s:%d", LOCALHOST, at)
        await _listen_on_tailscale(runner, at)
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
