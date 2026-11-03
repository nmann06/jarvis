# Jarvis Panel: architecture and API

The Jarvis Panel is the desktop app for setting up, configuring and improving Jarvis.

## Parts

| Part | Where | What it does |
|---|---|---|
| Voice hooks | `jarvis/events.py`, small calls in `jarvis/voice.py` and `jarvis/wakeword.py` | Publish voice state and keep the last 30 s of mic audio in memory, inside the Jarvis process |
| Panel server | `jarvis/panel/` (aiohttp), run with `python -m jarvis.panel`, unit `deploy/jarvis-panel.service` | HTTP API and web UI. A separate process from Jarvis, so it works while Jarvis is down or unconfigured |
| Integrations | `jarvis/integrations.py` | Each API's setup instructions, keys and read-only connection test |
| Web UI | `app/web/` (plain HTML, CSS and JS, no build step), served by the panel server at `/` | Every page |
| Desktop app | `app/jarvis_panel/` (PySide6) | Linux and Windows window showing the web UI, tray icon, floating light, connection dialog |

Jarvis needs Linux and an NVIDIA GPU, so the panel server only runs on the Jarvis machine. The
Windows app is the same desktop app connecting to that machine over Tailscale.

## Network and auth

- Listens on `127.0.0.1` and the machine's Tailscale IP (if any), port `PANEL_PORT` (default 8767).
  Never `0.0.0.0`, never behind Tailscale Funnel.
- Token: `data/panel-token`, mode 600, created at first start with `secrets.token_urlsafe(32)`.
- Every route except `/login` needs the token, as either `Authorization: Bearer <token>` or the
  `panel` cookie (HttpOnly, SameSite=Strict). Otherwise 401. Compare with `hmac.compare_digest`.
- `GET /login?token=...` sets the cookie and redirects to `/`.
- State-changing requests must be POST with `Content-Type: application/json`.
- The desktop app on the Jarvis machine reads the token file. Elsewhere the owner pastes it once
  and it's stored in the OS user's config directory with user-only permissions.

## Errors

Failures are JSON with a non-2xx status: `{"error": "what went wrong, in plain words"}`.

## Secrets

The API never returns a secret's value, only whether it is set. Secret values are never logged,
and never included in reports or in `/api/logs` responses knowingly (log lines are passed through
a filter that masks the current values of all secret settings).

## Routes

### Status and light

`GET /api/status`

```json
{"service": "active|inactive|failed|unknown", "hooks_live": true, "hooks_reason": null,
 "state": "idle", "muted": false, "version": "git short hash", "restart_needed": false}
```

`hooks_reason` says why the hooks aren't live, and only a `needs_restart` wants a restart: `null`
(they are), `needs_restart` (the running Jarvis predates them), `voice_off` (`VOICE_ENABLED=off`:
a restart won't help), `starting` (started under 2 minutes ago, still loading) or `down` (the
service isn't active).

`hooks_live` is false when the running Jarvis has no voice hooks yet (it predates them, or it's
down). `state` is then `"unknown"`, and the UI must say that Jarvis needs a restart for the light
and audio capture to work, rather than showing a plain idle light.

`GET /api/events`: server-sent events, one JSON object per `data:` line:

```json
{"type": "state", "state": "idle|listening|thinking|speaking|unknown", "t": 1793500000.0}
{"type": "level", "rms": 0.03, "peak": 0.2, "wake_score": 0.01, "t": 1793500000.0}
{"type": "hooks", "live": true, "t": 1793500000.0}
```

`level` arrives about 10 times a second while the hooks are live. A comment line every 15 s keeps
the connection alive.

Light meanings: `listening` is the owner talking to Jarvis (the light is on, amber). `speaking` is
blue, `thinking` is a slow amber pulse, `idle` is off, `unknown` is grey with a hollow centre.

### Settings

- `GET /api/settings`: `jarvis.settings.listing()` as is.
- `POST /api/settings` `{"key": "...", "value": "..."}`: `jarvis.settings.set_setting`, returns
  `{"message": "...", "restart": true}`. A ValueError is a 400.
- `POST /api/secrets` `{"key": "...", "value": "..."}`: writes one secret to `.env`, returns
  `{"message": "...", "restart": true}`. Only keys listed as secrets in `jarvis.settings`.

### Service

- `POST /api/service` `{"action": "start|stop|restart"}`: `systemctl --user <action> jarvis`.
  Returns `{"message": "..."}`. The UI confirms stop and restart first. Start and restart first
  check that Jarvis can load `.env`; if not, a 400 names the setting (never its value) and Jarvis
  is left as it is.
- `POST /api/mute` `{"muted": true}`: mic mute. Returns `{"muted": true}`, or 409 when hooks aren't
  live. Alarms still ring while muted.
- `GET /api/logs?lines=200`: `{"lines": ["..."]}` from `journalctl --user -u jarvis`, secrets masked.
- `GET /api/logs/stream`: server-sent events, `{"line": "..."}`.

### Setup

- `GET /api/integrations`:

```json
{"integrations": [{"id": "anthropic", "name": "Claude (Anthropic)", "required": true,
  "summary": "one line", "steps": ["markdown step", "..."],
  "fields": [{"key": "ANTHROPIC_API_KEY", "label": "API key", "secret": true, "set": true,
              "value": null, "help": "where to find it"}],
  "login": {"label": "Sign in", "command": ["python", "-m", "..."]},
  "configured": true, "testable": true}]}
```

  `login` is null when no sign-in command is needed.
- `POST /api/integrations/<id>/test`: `{"ok": true, "message": "..."}`. Read-only against the
  service, with a 15 s limit. A failed test is still 200 with `ok: false`.
- `POST /api/integrations/<id>/login`: runs the sign-in command in the background and returns
  `{"job": "id"}`.
- `GET /api/jobs/<id>`: `{"running": true, "output": "...", "exit": null}`.
- `GET /api/checks`: machine checks for the setup page:
  `{"checks": [{"id": "gpu", "name": "NVIDIA GPU", "ok": true, "detail": "...", "fix": "..."}]}`
  covering the Python venv, GPU, models, mic, speaker, echo cancel, and the systemd service.

### Improve

`POST /api/reports` `{"kind": "...", "note": "optional free text"}`, where kind is one of:

| kind | Button |
|---|---|
| `stopped_unprompted` | Jarvis stopped without me saying stop |
| `missed_wake` | I said Hey Jarvis and it didn't wake |
| `false_wake` | It woke by itself |
| `misheard` | It misheard me |
| `missed_stop` | It didn't stop when I said stop |
| `wrong_answer` | It said the wrong thing |

Takes effect the moment the button is pressed. Saves `data/reports/<id>/meta.json`, plus
`audio.wav` when the hooks are live. Returns the report. Recent context comes from the voice hooks
(last wake scores, stop-detection results, state changes) and `data/exchanges.jsonl`.

```json
{"id": "20261101-071502-missed_wake", "kind": "missed_wake", "t": 1793500000.0, "note": "",
 "has_audio": true, "context": {"peak_wake_score": 0.31, "threshold": 0.5,
 "states": [], "stop_events": [], "last_exchange": {}},
 "suggestion": {"text": "Your wake word scored 0.31 and needs 0.5. Lower sensitivity to 0.28?",
                "key": "WAKE_THRESHOLD", "value": "0.28"},
 "status": "open"}
```

`suggestion` is null when there's nothing to change. `key` and `value` are null for advice with
nothing to apply. A suggestion only ever changes a setting when the owner presses Apply; nothing
changes by itself.

- `GET /api/reports`: `{"reports": [...]}`, newest first.
- `GET /api/reports/<id>/audio`: the WAV.
- `POST /api/reports/<id>/apply`: applies the suggestion through `set_setting`, returns
  `{"message": "...", "restart": true}`.
- `POST /api/reports/<id>/status` `{"status": "open|fixed|dismissed"}`.
- `DELETE /api/reports/<id>`: deletes the report and its audio.

### Stats

`GET /api/stats?days=14`:

```json
{"days": [{"date": "2026-11-01", "wakes": 12, "turns": 10, "reports": {"missed_wake": 1},
           "latency_ms_p50": 1700, "latency_ms_p90": 3100}],
 "summary": {"wakes": 80, "turns": 70, "latency_ms_p50": 1700, "latency_ms_p90": 3200}}
```

Built from `data/latency.jsonl` and `data/reports/`.

## Pages

1. **Home**: big light, service status and buttons, mic meter, mute, restart banner.
2. **Setup**: machine checks, then each integration's instructions, fields, sign-in and Test button.
3. **Settings**: every setting from `/api/settings` by category, with Restart when needed.
4. **Improve**: the six report buttons, large and pressable mid-conversation; reports listed below
   with audio player, context, suggestion and Apply.
5. **Stats**: charts from `/api/stats`.
6. **Logs**: live log viewer.

## Audio privacy

The 30 s mic buffer lives in the Jarvis process's memory and is overwritten continuously. Audio
reaches disk only when a report button is pressed. Reports stay in `data/` (gitignored) and can be
deleted from the Improve page.
