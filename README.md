# Jarvis

A local "Hey Jarvis" voice assistant for this laptop. Speech recognition and the voice run on the
machine itself; only the text of what you say is sent to Claude.

| Part | What it uses |
|---|---|
| Speech-to-text | faster-whisper `large-v3-turbo` on the RTX 3060 (CUDA, float16) |
| Text-to-speech | Piper, `en_GB-alan-medium` voice |
| Brain | Claude via the `anthropic` SDK (`claude-haiku-5-5` by default) |
| Microphone | A USB webcam mic |

## Speed

From you going quiet to Jarvis's voice takes about 1.5-1.8 s for a plain question and 2.5-3 s
when Claude needs a tool (measured on this laptop). What keeps it there, in the order it happens:

- **A short pause when you're done.** A request that sounds finished (Whisper ends it with `.`,
  `?` or `!`, and not on a word like "to", "and" or "um") ends after 0.5 s of quiet
  (`QUICK_SILENCE_SECONDS`); anything else waits 0.8 s (`SILENCE_SECONDS`).
- **Transcription during that pause.** Whisper starts 0.24 s into any pause, so the text is ready
  when the pause turns out to be the end: its 0.27 s (nearly the same for any length, as it
  always encodes a 30 s window) usually adds nothing. It is warmed up at startup, since its
  first run is 0.2 s slower.
- **Prompt caching.** The system prompt and tools never change (the time goes in a system message
  after your words), so they're cached, and each request caches the conversation for the next.
  On "Hey Jarvis" a `max_tokens=0` request loads the conversation so far into the cache while
  you're still talking: Claude's first word comes 0.65-0.9 s after the request instead of about
  0.95 s (up to 1.35 s). Having the time there also saves a tool call to look it up.
- **A connection kept open.** Idle connections to the API are kept for 60 s rather than 5, so the
  request reuses the one the pre-warm opened.
- **Speaking while Claude writes.** Speech starts with the first sentence (Piper takes 0.03-0.23 s
  for it) while Claude writes the rest, and words said before a tool call ("Checking your
  calendar.") are spoken as soon as the call starts.
- **Faster tools.** Email search fetches the messages in one batched request (2.5 s -> 0.6 s for
  ten), OnQ due work asks for every course at once (1.5-2.5 s -> 0.3-0.4 s), and calendar
  queries go to every calendar at once.

### Where the time goes

Every voice turn logs one line whose parts add up to the total, and `data/latency.jsonl` gets
the full record (each Claude request with its token counts, each tool, what was heard and said):

```
latency 2.82s: pause 0.56 + stt 0.00 + claude#1 0.74 + get_weather 0.47 + claude#2 0.66
  + sentence 0.18 + voice 0.17 + audio 0.04 [answered; quick end; stt 0.28s, all in the pause;
  claude#1 5080 cached + 56 new in, 27 out; claude#2 5134 cached + 125 new in, 61 out]
```

`pause` is making sure you'd finished, `stt` transcription not already done during the pause,
`claude` a request from sending it to the first word (`claude#1`, `claude#2` when tools are
involved, with each tool's time between), `sentence` Claude finishing the first sentence,
`voice` Piper and `audio` the sound card. `.venv/bin/python -m jarvis.latency 30` prints the last
30 turns and the median of each part.

## Setup

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install --no-deps openwakeword==0.6.0   # see Notes
cp .env.example .env && chmod 600 .env   # then put your ANTHROPIC_API_KEY in .env
```

The Piper voice goes in `models/piper/` (`en_GB-alan-medium.onnx` plus its `.onnx.json`).
Whisper models download to `~/.cache/huggingface` on first use.

## Run

Jarvis runs as a systemd user service that starts on boot (lingering is on, so no login is
needed) and restarts if it crashes. It runs the voice loop and, when `DISCORD_TOKEN` is set,
the Discord bot; both share the same tools.

```
systemctl --user status jarvis          # is it running?
journalctl --user -u jarvis -f          # live logs, including per-turn latency
.venv/bin/python -m jarvis.latency 30   # last 30 voice turns: where the time went, and medians
systemctl --user restart jarvis         # after changing .env or code
systemctl --user stop jarvis            # stop it (e.g. to run a test that needs the mic)
```

To run it in the foreground instead: stop the service, then `.venv/bin/python -m jarvis.main`.
The unit file is `deploy/jarvis.service`, linked into `~/.config/systemd/user/`.

Voice: say "Hey Jarvis", wait for the chime, then speak. After each reply it listens for 5 s for
a follow-up without the wake word. Claude is told the follow-up came without the wake word and
ignores it if it sounds like you're talking to someone else. A falling chime means it has gone
back to waiting for "Hey Jarvis". "Say that again", "sorry?", "what did you say?" and similar
replay the last reply word for word without asking Claude.

Discord: DM the bot, @mention it in a server, reply to one of its messages, or write in a
`#jarvis` channel (`DISCORD_CHANNEL`). It answers only `DISCORD_OWNER_ID`. Timers announce themselves where they
were set: spoken, or posted in `#jarvis` (with an @mention unless `DISCORD_MENTION=off`), where
calendar records also go.

### Apple Calendar setup

account.apple.com -> Sign-In and Security -> App-Specific Passwords -> create one, then put your
Apple ID email and that password in `.env` as `ICLOUD_USER` / `ICLOUD_APP_PASSWORD`. New events go
in `ICLOUD_CALENDAR` (default Home) unless Claude picks a better fit (Work, Classes, ...).

### Google setup (Gmail, Sheets, Drive notes)

Jarvis reads one Gmail inbox (forward your other accounts into it) and can save drafts, never
send. It also reads and edits one Google Sheet (`GOOGLE_SHEET_ID`), and saves notes as Google Docs in a
"Jarvis Notes" Drive folder (it can only see files it created there).

1. console.cloud.google.com -> create a project (e.g. "Jarvis").
2. APIs & Services -> Library: enable **Gmail API**, **Google Sheets API** and **Google Drive API**.
3. Google Auth Platform (OAuth consent screen): External, app name Jarvis, your email as support
   and contact. Under Audience add yourself as a test user, then press **Publish app** (stays
   unverified, which is fine for yourself; in Testing mode the sign-in expires every 7 days).
4. Clients -> Create client -> **Desktop app** -> download the JSON and save it on this laptop as
   `~/jarvis/google_client.json`.
5. Sign in once. From another computer over SSH, forward the login port first:
   `ssh -L 8765:localhost:8765 <this laptop>`, then run
   `.venv/bin/python -m jarvis.google_auth`, open the link it prints, and accept the warning
   ("Google hasn't verified this app" -> Advanced -> Go to Jarvis). The token is saved to
   `.google_token.json` and renews itself.
6. Put the sheet's ID (the part of its URL between `/d/` and `/edit`) in `.env` as
   `GOOGLE_SHEET_ID`, then `systemctl --user restart jarvis`.

### OnQ setup

OnQ (Brightspace) signs in through Microsoft with MFA, so a real Chromium does it once, on this
laptop's own screen (works from SSH too; the window opens on display :0):
`.venv/bin/python -m jarvis.tools.onq login`. Choose **Yes** at "Stay signed in". The cookies
are kept in `data/onq-cookies.json` (private) and the browser profile in `data/onq-profile`;
expired OnQ sessions are renewed headless. If Microsoft wants the password again, Jarvis posts
the login command in #jarvis. Quick check: `.venv/bin/python -m jarvis.tools.onq`.

Smith Learning (smithlearning.ca, the Smith School of Business Brightspace) works the same
way and the OnQ tools cover both sites. It signs in with a Smith username and a password (no MFA): put `SMITH_USER`
and `SMITH_PASSWORD` in `.env` and Jarvis signs in by itself, or sign in once with
`.venv/bin/python -m jarvis.tools.onq login smith` (asked again when the session expires).

### Discord setup

1. discord.com/developers/applications -> New Application -> Bot -> Reset Token; put it in `.env`
   as `DISCORD_TOKEN`. On the same page, turn on **Message Content Intent**.
2. OAuth2 -> URL Generator: scope `bot`; open the URL and add the bot to any server you're in
   (needed so it can DM you).
3. Discord settings -> Advanced -> Developer Mode on; right-click your name -> Copy User ID; put it
   in `.env` as `DISCORD_OWNER_ID`.
4. `systemctl --user restart jarvis`, then DM the bot.

### Claude Code alerts

Jarvis says when a Claude Code session on this laptop is done ("Sir, Claude has finished in
jarvis.") or needs you to check something: a permission, a question, or a plan to approve. Only
things more than 30 s after your last message to that session are said
(`JARVIS_CLAUDE_MIN_SECONDS`), since before that you're probably still watching. It goes through
a socket Jarvis listens on (`$XDG_RUNTIME_DIR/jarvis.sock`, `jarvis/alerts.py`), spoken, or
posted in #jarvis if voice is off. Any program can send it `{"text": "..."}` on one line.

The hook is `deploy/claude-hook.py`, set up in `~/.claude/settings.json` for all sessions
(`|| true` so a missing script can never hold Claude up):

```json
"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 ~/jarvis/deploy/claude-hook.py || true"}]}],
  "Stop": [{"hooks": [{"type": "command", "command": "python3 ~/jarvis/deploy/claude-hook.py || true"}]}],
  "Notification": [{"matcher": "permission_prompt|elicitation_dialog",
                    "hooks": [{"type": "command", "command": "python3 ~/jarvis/deploy/claude-hook.py || true"}]}],
  "PreToolUse": [{"matcher": "AskUserQuestion|ExitPlanMode",
                  "hooks": [{"type": "command", "command": "python3 ~/jarvis/deploy/claude-hook.py || true"}]}]
}
```

## Checks

```
.venv/bin/python -m tests.claude_hello                     # Claude replies, prints token usage
.venv/bin/python -m jarvis.tts "Good evening, sir."        # speaks aloud
.venv/bin/python -m jarvis.stt 5                           # records 5 s, prints the transcript
.venv/bin/python -m tests.bench_stt                        # compares Whisper models (add @cpu to run one on CPU)
.venv/bin/python -m jarvis.llm "What time is it?"       # ask Claude by text, with tools
.venv/bin/python -m tests.discord_ping                      # DMs you a test notification
.venv/bin/python -m jarvis.wakeword                        # prints WAKE + what you said, Ctrl+C to stop
```

## Settings (`.env`)

| Setting | Default | Notes |
|---|---|---|
| `CLAUDE_MODEL` | `claude-haiku-5-5` | Any current Claude model id |
| `MAX_TOKENS` | `300` | Caps reply length and cost (raise to ~1024 if thinking is on) |
| `CLAUDE_THINKING` | `off` | `on` lets Claude think first: slower, rarely better for this |
| `CLAUDE_EFFORT` | `low` | `low` / `medium` / `high` |
| `HISTORY_TURNS` | `10` | Past exchanges sent with each request |
| `WHISPER_MODEL` | `large-v3-turbo` | `tiny.en` / `base.en` / `small.en` for CPU |
| `WHISPER_DEVICE` | `cuda` | `cpu` uses int8 and `WHISPER_THREADS` |
| `PIPER_VOICE` | `models/piper/en_GB-alan-medium.onnx` | |
| `TTS_LENGTH_SCALE`, `TTS_VOLUME` | `1.0` | Speech speed (>1 is slower) and loudness |
| `THINKING_SOUND` | `on` | Soft blips while waiting on Claude or a tool (`off` for silence) |
| `WAKE_THRESHOLD` | `0.5` | Raise if it wakes by itself, lower if it misses you |
| `SILENCE_SECONDS` | `0.8` | Pause that ends what you're saying |
| `QUICK_SILENCE_SECONDS` | `0.5` | Shorter pause that ends it when it's already a finished sentence ("What time is it?"); set to `SILENCE_SECONDS` to always wait the full pause |
| `FOLLOW_UP_SECONDS` | `5` | How long it listens for a follow-up after replying |
| `INPUT_DEVICE`, `OUTPUT_DEVICE` | system default | Index or name substring; list with `python -m sounddevice` |

## Status

Done: TTS, STT (GPU), Claude with tools, wake word + end-of-speech detection, full pipeline,
systemd service, Discord bot. Next (JarvisP2): Google Calendar, Sheets, Gmail, OnQ, briefings.

## Tools

Claude can call: `get_events` / `add_event` (Apple Calendar over iCloud CalDAV; adds only, never
edits or deletes, and DMs you a record of each event added), `get_time_and_date`, `set_timer` / `list_timers`, `add_note` / `list_notes` / `read_note`
(Google Docs in the Jarvis Notes Drive folder), `get_weather` (local weather, Open-Meteo), `system_status`, OnQ and Smith Learning (read-only):
`onq_courses`, `onq_due` (assignments with submitted/not, quizzes, course events),
`onq_announcements`, `onq_grades`, `onq_syllabus` (plus any "deliverable guidance" document; text cached in `data/onq-syllabi/`).

To add one, write a function in a module under `jarvis/tools/` and decorate it:

```python
from jarvis.tools import tool

@tool("What it does, for Claude.", city={"type": "string", "description": "City name"})
def my_tool(city):
    return "result text (or a dict, sent as JSON)"
```

then add the module to the import line at the bottom of `jarvis/tools/__init__.py`.

## Notes

- openWakeWord 0.6.0 is installed with `--no-deps`: it lists `tflite-runtime`, which has no
  Python 3.12 build, but Jarvis runs it on ONNX instead.
- Conversation memory (last `HISTORY_TURNS` exchanges) lasts until Jarvis restarts; notes are
  saved in Google Drive.
- `av` is pinned to 18.1.0: av 19 breaks faster-whisper 1.2.1.
- The NVIDIA driver is the DKMS `nvidia-driver-595-open`, signed with the enrolled MOK key, so it
  works with Secure Boot on. cuBLAS/cuDNN come from pip and are loaded by `jarvis/stt.py`.
