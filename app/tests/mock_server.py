"""A small stand-in for the Jarvis panel server (docs/panel-api.md), for testing the web UI and
the desktop shell without touching the real service.

    /home/nate/jarvis/.venv/bin/python app/tests/mock_server.py --port 18767 --token TEMP

Test controls (no token needed, mock only):
    POST /__mock/scenario {"name": "normal|hooks_off|voice_off|starting|stopped|env_broken|empty|failing|long_logs|slow"}
    POST /__mock/state    {"state": "listening"}      pushes a state event to open /api/events streams
    POST /__mock/drop     {}                           closes open event streams and refuses new ones until /__mock/scenario
    GET  /__mock/calls                                 the API calls received, as JSON
"""

import argparse
import asyncio
import hmac
import io
import json
import math
import struct
import time
import wave
from pathlib import Path

from aiohttp import web

WEB = Path(__file__).resolve().parent.parent / "web"
APP = web.AppKey("mock", dict)

CATEGORIES = ["Claude", "Voice", "Listening", "Do not disturb", "Accounts & keys"]


def S(key, category, label, kind, default, about, choices=None, secret=False, value=None, is_set=None):
    d = {"key": key, "category": category, "label": label, "kind": kind, "default": default,
         "about": about, "choices": choices, "secret": secret, "value": value}
    if secret:
        d["set"] = bool(is_set)
    return d


def fresh_settings():
    return [
        S("CLAUDE_MODEL", "Claude", "Model", "choice", "claude-haiku-5-5", "The Claude model that answers you",
          ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"], value="claude-sonnet-5-5"),
        S("CLAUDE_EFFORT", "Claude", "Effort", "choice", "low",
          "How hard Claude thinks before answering: higher is slower", ["low", "medium", "high"]),
        S("CLAUDE_THINKING", "Claude", "Thinking", "bool", "off", "Let Claude think first: slower, rarely better for this"),
        S("CLAUDE_RACE", "Claude", "Race two requests", "bool", "on",
          "Send each request twice and use whichever answers first: fewer slow starts, a little more cost", value="off"),
        S("HISTORY_CHARS", "Claude", "Conversation memory (characters)", "int", "480000",
          "Conversations are kept whole up to this size (about 120k tokens), then the oldest quarter goes"),
        S("VOICE_ENABLED", "Voice", "Voice", "bool", "on", 'Listen for "Hey Jarvis" and talk'),
        S("TTS_LENGTH_SCALE", "Voice", "Speech speed", "float", "1.0", "How slowly Jarvis talks: over 1 is slower", value="1.1"),
        S("WAKE_THRESHOLD", "Listening", "Wake sensitivity threshold", "float", "0.5",
          "Wake word score needed to wake Jarvis: lower wakes more easily"),
        S("WHISPER_MODEL", "Listening", "Speech recognition model", "choice", "small.en",
          "Which Whisper model turns your voice into text", ["tiny.en", "base.en", "small.en", "medium.en"]),
        S("BRIEFING_TIME", "Listening", "Briefing time", "text", "7:45", "When the morning briefing is ready (24-hour)"),
        S("DND", "Do not disturb", "Do not disturb", "bool", "off", "No spoken alerts. Applies at once", value="off"),
        S("ANTHROPIC_API_KEY", "Accounts & keys", "Anthropic API key", "secret", "", "Change it in ~/jarvis/.env",
          secret=True, is_set=True),
        S("DISCORD_TOKEN", "Accounts & keys", "Discord bot token", "secret", "", "Change it in ~/jarvis/.env",
          secret=True, is_set=False),
    ]


def fresh_integrations(failing=False):
    return [
        {"id": "anthropic", "name": "Claude (Anthropic)", "required": True,
         "summary": "The model that answers you.",
         "steps": ["Open [the Anthropic console](https://console.anthropic.com/settings/keys) and create a key.",
                   "Copy it **once**; the console never shows it again.",
                   "Paste it below and press Save, then Test."],
         "fields": [{"key": "ANTHROPIC_API_KEY", "label": "API key", "secret": True, "set": True, "value": None,
                     "help": "Starts with sk-ant-"}],
         "login": None, "configured": True, "testable": True},
        {"id": "discord", "name": "Discord", "required": False, "summary": "Messages to your #jarvis channel.",
         "steps": ["Create an application at [the developer portal](https://discord.com/developers/applications).",
                   "Under Bot, press `Reset Token` and copy it.",
                   "Invite the bot to your server, then paste the channel id."],
         "fields": [{"key": "DISCORD_TOKEN", "label": "Bot token", "secret": True, "set": False, "value": None,
                     "help": "Bot page, Reset Token"},
                    {"key": "DISCORD_CHANNEL", "label": "Channel id", "secret": False, "set": True, "value": "1234567890",
                     "help": "Right-click the channel, Copy ID"}],
         "login": None, "configured": False, "testable": True},
        {"id": "google", "name": "Google (calendar, mail, sheets)", "required": False,
         "summary": "Calendar, Gmail and the job sheet.",
         "steps": ["Put `google_client.json` in the Jarvis folder.", "Press **Sign in** and finish in the browser that opens."],
         "fields": [],
         "login": {"label": "Sign in", "command": ["python", "-m", "jarvis.google_login"]},
         "configured": False, "testable": True},
        {"id": "<img src=x onerror=alert(1)>", "name": "<b>Odd name</b> & friends", "required": False,
         "summary": "<script>alert(1)</script> untrusted summary",
         "steps": ["<img src=x onerror=alert(1)> step with [bad link](javascript:alert(1)) and [ok](https://example.com)"],
         "fields": [], "login": None, "configured": False, "testable": False},
    ]


CHECKS = [
    {"id": "venv", "name": "Python environment", "ok": True, "detail": "Python 3.12.3 in .venv", "fix": ""},
    {"id": "gpu", "name": "NVIDIA GPU", "ok": True, "detail": "RTX 3050, driver 565", "fix": ""},
    {"id": "models", "name": "Voice models", "ok": True, "detail": "wake word, Whisper, Piper found", "fix": ""},
    {"id": "mic", "name": "Microphone", "ok": True, "detail": "echo-cancelled source found", "fix": ""},
    {"id": "speaker", "name": "Speaker", "ok": True, "detail": "default sink: Built-in Audio", "fix": ""},
    {"id": "echo", "name": "Echo cancellation", "ok": False, "detail": "module-echo-cancel is not loaded",
     "fix": "Copy deploy/60-jarvis-echo-cancel.conf to ~/.config/pipewire/pipewire-pulse.conf.d/ and restart PipeWire."},
    {"id": "service", "name": "Jarvis service", "ok": True, "detail": "active (running) since 07:02", "fix": ""},
]

KINDS = ["stopped_unprompted", "missed_wake", "false_wake", "misheard", "missed_stop", "wrong_answer"]


def make_report(kind, t, idx, note="", audio=True, status="open"):
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(t))
    sug = None
    if kind == "missed_wake":
        sug = {"text": "Your wake word scored 0.31 and needs 0.5. Lower sensitivity to 0.28?",
               "key": "WAKE_THRESHOLD", "value": "0.28"}
    elif kind == "false_wake":
        sug = {"text": "It woke at a score of 0.52. Nothing to change yet; say what you heard and report again if it repeats.",
               "key": None, "value": None}
    return {"id": f"{stamp}-{idx}-{kind}", "kind": kind, "t": t, "note": note, "has_audio": audio,
            "context": {"peak_wake_score": 0.31, "threshold": 0.5,
                        "states": [{"state": "idle", "t": t - 3}, {"state": "listening", "t": t - 1.2}],
                        "stop_events": [], "last_exchange": {"heard": "what's the weather <b>like</b>", "reply": "It is 4 degrees."},
                        "turns": [{"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t - 40)), "heard": "what's the weather <b>like</b>",
                                   "reply": "It is 4 degrees.", "outcome": "answered", "age_s": 40}],
                        "prompt": "false wake" if kind == "false_wake" else None},
            "suggestion": sug, "status": status}


NO_HOOKS = ("hooks_off", "voice_off", "starting", "stopped")


def fresh_state(scenario="normal"):
    now = time.time()
    reports = [] if scenario == "empty" else [
        make_report("missed_wake", now - 600, 1),
        make_report("false_wake", now - 7200, 2, note="TV was on", status="fixed"),
        make_report("misheard", now - 90000, 3, audio=False, status="dismissed"),
    ]
    return {"scenario": scenario, "hooks": scenario not in NO_HOOKS, "state": "idle" if scenario not in NO_HOOKS else "unknown",
            "muted": False, "restart_needed": False, "service": "inactive" if scenario == "stopped" else "active", "settings": fresh_settings(),
            "integrations": fresh_integrations(), "reports": reports, "calls": [], "queues": set(), "dropped": False,
            "jobs": {}, "ints_failing": scenario == "failing"}


def stats(days):
    out = []
    today = time.time()
    for i in range(days):
        d = time.strftime("%Y-%m-%d", time.localtime(today - 86400 * (days - 1 - i)))
        w = int(8 + 6 * math.sin(i / 2) + (i % 3))
        out.append({"date": d, "wakes": w, "turns": max(0, w - (i % 4)),
                    "reports": ({"missed_wake": 1} if i % 5 == 0 else {}) | ({"misheard": 2, "false_wake": 1} if i % 7 == 3 else {}),
                    "latency_ms_p50": 1600 + (i * 37) % 400, "latency_ms_p90": 2900 + (i * 91) % 900})
    if days > 4:
        out[2].update(wakes=0, turns=0, latency_ms_p50=None, latency_ms_p90=None)
    return {"days": out, "summary": {"wakes": sum(d["wakes"] for d in out), "turns": sum(d["turns"] for d in out),
                                      "latency_ms_p50": 1700, "latency_ms_p90": 3200}}


def wav_bytes():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", 0) for _ in range(16000)))
    return buf.getvalue()


def err(msg, status=400):
    return web.json_response({"error": msg}, status=status)


@web.middleware
async def auth_mw(request, handler):
    m = request.app[APP]
    path = request.path
    if path.startswith("/__mock/") or path == "/login":
        return await handler(request)
    if m["scenario"] == "down":
        return err("mock: simulated outage", 503)
    tok = request.app["token"]
    got = request.cookies.get("panel", "")
    hdr = request.headers.get("Authorization", "")
    if hdr.startswith("Bearer "):
        got = hdr[7:]
    if not hmac.compare_digest(got.encode(), tok.encode()):
        if path.startswith("/api/"):
            return err("Not signed in. Open the panel from the Jarvis app.", 401)
        return web.Response(status=401, text="Not signed in")
    if request.method not in ("GET", "HEAD") and request.content_type != "application/json" and request.method != "DELETE":
        return err("Send JSON.", 415)
    if path.startswith("/api/"):
        m["calls"].append({"method": request.method, "path": path, "t": time.time()})
    return await handler(request)


async def login(request):
    if not hmac.compare_digest(request.query.get("token", "").encode(), request.app["token"].encode()):
        return web.Response(status=401, text="Wrong token")
    r = web.HTTPFound("/")
    r.set_cookie("panel", request.query["token"], httponly=True, samesite="Strict")
    raise r


async def index(request):
    return web.FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


async def static(request):
    p = (WEB / request.match_info["name"]).resolve()
    if WEB not in p.parents or not p.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(p, headers={"Cache-Control": "no-cache"})


async def status(request):
    m = request.app[APP]
    reason = None
    if not m["hooks"]:
        reason = {"hooks_off": "needs_restart", "voice_off": "voice_off", "starting": "starting"}.get(m["scenario"], "needs_restart")
    if m["service"] != "active":
        reason = "down" if not m["hooks"] else reason
    return web.json_response({"service": m["service"], "hooks_live": m["hooks"], "hooks_reason": reason, "state": m["state"],
                              "muted": m["muted"], "version": "a1b2c3d", "restart_needed": m["restart_needed"]})


async def push(m, obj):
    for q in list(m["queues"]):
        q.put_nowait(obj)


async def events(request):
    m = request.app[APP]
    if m["dropped"]:
        return err("mock: stream refused", 503)
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await resp.prepare(request)
    q = asyncio.Queue()
    m["queues"].add(q)
    try:
        await resp.write((": hello\n\n").encode())
        await resp.write(f"data: {json.dumps({'type': 'state', 'state': m['state'], 't': time.time()})}\n\n".encode())
        await resp.write(f"data: {json.dumps({'type': 'hooks', 'live': m['hooks'], 't': time.time()})}\n\n".encode())
        i = 0
        while True:
            try:
                obj = await asyncio.wait_for(q.get(), 0.1)
                if obj is None:
                    break
                await resp.write(f"data: {json.dumps(obj)}\n\n".encode())
            except asyncio.TimeoutError:
                if m["hooks"]:
                    i += 1
                    rms = 0.02 + (0.25 if m["state"] == "listening" else 0.0) * abs(math.sin(i / 3))
                    await resp.write(("data: " + json.dumps({"type": "level", "rms": rms, "peak": min(1, rms * 3),
                                                             "wake_score": 0.01, "t": time.time()}) + "\n\n").encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        m["queues"].discard(q)
    return resp


async def get_settings(request):
    m = request.app[APP]
    return web.json_response({"categories": CATEGORIES, "settings": m["settings"], "env": "/home/nate/jarvis/.env"})


async def post_settings(request):
    m = request.app[APP]
    b = await request.json()
    s = next((x for x in m["settings"] if x["key"] == b.get("key")), None)
    if not s or s["secret"]:
        return err(f"No setting called {b.get('key')}")
    v = str(b.get("value", "")).strip()
    reset = v in ("", "default")
    if not reset and s["kind"] in ("int", "float"):
        try:
            float(v)
        except ValueError:
            return err(f"{s['label']} must be a number")
    if not reset and s["kind"] == "bool" and v not in ("on", "off"):
        return err(f"{s['label']} is on or off")
    s["value"] = None if reset else v
    if s["key"] == "DND":
        return web.json_response({"message": "Do not disturb updated.", "restart": False})
    m["restart_needed"] = True
    return web.json_response({"message": f"{s['label']}: {v or s['default']}. Restart Jarvis to apply.", "restart": True})


async def post_secrets(request):
    m = request.app[APP]
    b = await request.json()
    s = next((x for x in m["settings"] if x["key"] == b.get("key") and x["secret"]), None)
    if not s:
        return err("That key is not a secret setting.")
    s["set"] = bool(b.get("value"))
    for it in m["integrations"]:
        for f in it["fields"]:
            if f["key"] == b["key"]:
                f["set"] = s["set"]
    m["restart_needed"] = True
    return web.json_response({"message": f"{s['label']} saved.", "restart": True})


async def post_service(request):
    m = request.app[APP]
    b = await request.json()
    act = b.get("action")
    if act not in ("start", "stop", "restart"):
        return err("Unknown action.")
    if m["scenario"] == "slow":
        await asyncio.sleep(2)
    if m["scenario"] == "env_broken" and act != "stop":
        return err("Jarvis can't load .env: CLAUDE_EFFORT must be low, medium or high. Fix it on the Settings page, then try again.")
    m["service"] = "inactive" if act == "stop" else "active"
    if act in ("restart", "start"):
        m["restart_needed"] = False
        if m["scenario"] in NO_HOOKS and m["scenario"] != "voice_off":
            m["scenario"], m["hooks"], m["state"] = "normal", True, "idle"
            await push(m, {"type": "hooks", "live": True, "t": time.time()})
            await push(m, {"type": "state", "state": "idle", "t": time.time()})
    return web.json_response({"message": f"Jarvis {act} done."})


async def post_mute(request):
    m = request.app[APP]
    if not m["hooks"]:
        return err("Jarvis has no voice hooks yet; restart it first.", 409)
    m["muted"] = bool((await request.json()).get("muted"))
    return web.json_response({"muted": m["muted"]})


def log_lines(m):
    base = [f"Nov 01 07:{i % 60:02d}:{i % 60:02d} hyde jarvis[812]: {t}" for i, t in enumerate(
        ["voice: wake score 0.91", "stt: 'what time is it' (310 ms)", "llm: first token 640 ms",
         "tts: speaking 2 sentences", "<script>alert('log')</script> raw html in a log line", "state: idle"] * 8)]
    if m["scenario"] == "long_logs":
        base.insert(3, "X" * 900 + " end-of-long-line")
        base.insert(5, "Traceback (most recent call last):\n".strip() + " " + "/very/long/path/" * 30 + "module.py")
    return base


async def get_logs(request):
    m = request.app[APP]
    n = int(request.query.get("lines", 200))
    return web.json_response({"lines": ([] if m["scenario"] == "empty" else log_lines(m))[-n:]})


async def logs_stream(request):
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await resp.prepare(request)
    try:
        i = 0
        while True:
            await asyncio.sleep(0.7)
            i += 1
            await resp.write(f"data: {json.dumps({'line': f'Nov 01 08:00:{i % 60:02d} hyde jarvis[812]: live line {i}'})}\n\n".encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    return resp


async def get_integrations(request):
    m = request.app[APP]
    return web.json_response({"integrations": [] if m["scenario"] == "empty" else m["integrations"]})


async def test_integration(request):
    m = request.app[APP]
    iid = request.match_info["id"]
    await asyncio.sleep(0.4)
    if iid == "discord" or m["ints_failing"]:
        return web.json_response({"ok": False, "message": "Discord answered 401: the bot token was rejected. "
                                                          "Reset the token in the developer portal and paste the new one."})
    return web.json_response({"ok": True, "message": "Connected. Model list returned 4 models."})


async def login_integration(request):
    m = request.app[APP]
    jid = f"job{len(m['jobs']) + 1}"
    m["jobs"][jid] = {"t": time.time()}
    return web.json_response({"job": jid})


async def get_job(request):
    m = request.app[APP]
    j = m["jobs"].get(request.match_info["id"])
    if not j:
        return err("No such job.", 404)
    el = time.time() - j["t"]
    out = "Opening your browser...\n" + ("Waiting for you to approve access...\n" if el > 1 else "") + ("Signed in as nate@example.com\n" if el > 3 else "")
    return web.json_response({"running": el <= 3, "output": out, "exit": None if el <= 3 else 0})


async def get_checks(request):
    m = request.app[APP]
    if m["scenario"] == "hooks_off":
        return web.json_response({"checks": CHECKS[:6] + [dict(CHECKS[6], ok=False, detail="active, but started before voice hooks", fix="Restart Jarvis.")]})
    return web.json_response({"checks": CHECKS})


async def post_report(request):
    m = request.app[APP]
    b = await request.json()
    if b.get("kind") not in KINDS:
        return err("Unknown kind.")
    r = make_report(b["kind"], time.time(), len(m["reports"]) + 10, b.get("note", ""), audio=m["hooks"])
    m["reports"].insert(0, r)
    return web.json_response(r)


async def get_reports(request):
    return web.json_response({"reports": request.app[APP]["reports"]})


def find(m, rid):
    return next((r for r in m["reports"] if r["id"] == rid), None)


async def report_audio(request):
    r = find(request.app[APP], request.match_info["id"])
    if not r or not r["has_audio"]:
        return err("No audio for that report.", 404)
    return web.Response(body=wav_bytes(), content_type="audio/wav")


async def report_apply(request):
    m = request.app[APP]
    r = find(m, request.match_info["id"])
    if not r or not r["suggestion"] or not r["suggestion"]["key"]:
        return err("That report has nothing to apply.")
    s = next(x for x in m["settings"] if x["key"] == r["suggestion"]["key"])
    s["value"] = r["suggestion"]["value"]
    r["status"] = "fixed"
    m["restart_needed"] = True
    return web.json_response({"message": f"{s['label']} set to {s['value']}. Restart Jarvis to apply.", "restart": True})


async def report_status(request):
    r = find(request.app[APP], request.match_info["id"])
    st = (await request.json()).get("status")
    if not r or st not in ("open", "fixed", "dismissed"):
        return err("Bad request.")
    r["status"] = st
    return web.json_response(r)


async def report_delete(request):
    m = request.app[APP]
    r = find(m, request.match_info["id"])
    if not r:
        return err("No such report.", 404)
    m["reports"].remove(r)
    return web.json_response({"message": "Deleted."})


async def get_stats(request):
    m = request.app[APP]
    d = stats(int(request.query.get("days", 14)))
    if m["scenario"] == "empty":
        d = {"days": [], "summary": {"wakes": 0, "turns": 0, "latency_ms_p50": None, "latency_ms_p90": None}}
    return web.json_response(d)


async def mock_scenario(request):
    m = request.app[APP]
    name = (await request.json())["name"]
    queues = m["queues"]
    m.clear()
    m.update(fresh_state(name))
    m["queues"] = queues              # streams opened earlier stay attached
    if name == "down":                # an outage kills open streams and refuses new ones
        m["dropped"] = True
        for q in list(queues):
            q.put_nowait(None)
    return web.json_response({"ok": True})


async def mock_state(request):
    m = request.app[APP]
    st = (await request.json())["state"]
    m["state"] = st
    await push(m, {"type": "state", "state": st, "t": time.time()})
    return web.json_response({"ok": True})


async def mock_drop(request):
    m = request.app[APP]
    m["dropped"] = True
    for q in list(m["queues"]):
        q.put_nowait(None)
    return web.json_response({"ok": True})


async def mock_calls(request):
    return web.json_response(request.app[APP]["calls"])


def make_app(token):
    app = web.Application(middlewares=[auth_mw])
    app["token"] = token
    app[APP] = fresh_state()
    r = app.router
    r.add_get("/login", login)
    r.add_get("/", index)
    r.add_get("/api/status", status)
    r.add_get("/api/events", events)
    r.add_get("/api/settings", get_settings)
    r.add_post("/api/settings", post_settings)
    r.add_post("/api/secrets", post_secrets)
    r.add_post("/api/service", post_service)
    r.add_post("/api/mute", post_mute)
    r.add_get("/api/logs", get_logs)
    r.add_get("/api/logs/stream", logs_stream)
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
    r.add_post("/__mock/scenario", mock_scenario)
    r.add_post("/__mock/state", mock_state)
    r.add_post("/__mock/drop", mock_drop)
    r.add_get("/__mock/calls", mock_calls)
    r.add_get("/{name}", static)  # flat files from app/web/, last so it never shadows a route
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18767)
    ap.add_argument("--token", default="test-token")
    ap.add_argument("--scenario", default="normal")
    a = ap.parse_args()
    if a.port == 8767:
        raise SystemExit("Refusing to use 8767: that is the real panel's port.")
    app = make_app(a.token)
    app[APP].update(fresh_state(a.scenario))
    web.run_app(app, host="127.0.0.1", port=a.port, shutdown_timeout=1, print=lambda *_: print(f"mock panel on {a.port}", flush=True))
