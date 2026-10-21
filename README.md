# Jarvis

A local "Hey Jarvis" voice assistant for this laptop. Speech recognition and the voice run on the
machine itself; only the text of what you say is sent to Claude.

| Part | What it uses |
|---|---|
| Speech-to-text | faster-whisper `large-v3-turbo` on the RTX 3060 (CUDA, float16) |
| Text-to-speech | Piper, `en_GB-alan-medium` voice |
| Brain | Claude via the `anthropic` SDK (`claude-haiku-5-5` by default) |
| Microphone | Logitech Brio 101 webcam mic (the built-in mic is broken) |

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

### Discord setup

1. discord.com/developers/applications -> New Application -> Bot -> Reset Token; put it in `.env`
   as `DISCORD_TOKEN`. On the same page, turn on **Message Content Intent**.
2. OAuth2 -> URL Generator: scope `bot`; open the URL and add the bot to any server you're in
   (needed so it can DM you).
3. Discord settings -> Advanced -> Developer Mode on; right-click your name -> Copy User ID; put it
   in `.env` as `DISCORD_OWNER_ID`.
4. `systemctl --user restart jarvis`, then DM the bot.

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
| `WAKE_THRESHOLD` | `0.5` | Raise if it wakes by itself, lower if it misses you |
| `SILENCE_SECONDS` | `0.8` | Pause that ends what you're saying |
| `FOLLOW_UP_SECONDS` | `5` | How long it listens for a follow-up after replying |
| `INPUT_DEVICE`, `OUTPUT_DEVICE` | system default | Index or name substring; list with `python -m sounddevice` |

## Measured on this machine

- Whisper `large-v3-turbo` on GPU: 0.15 s per second of audio (CPU `base.en`: 0.24 s, and less accurate).
- Claude `claude-haiku-5-5`: about 0.9 s per request; a question needing a tool takes two (1.5-2 s).
- Piper: about 0.13 s to synthesize a one-sentence reply.
- End of speech to Jarvis's voice (simulated audio): 2.7-3.3 s, of which 0.8 s is the silence wait.

## Status

Done: TTS, STT (GPU), Claude with tools, wake word + end-of-speech detection, full pipeline,
systemd service, Discord bot. Next (JarvisP2): Google Calendar, Sheets, Gmail, OnQ, briefings.

## Tools

Claude can call: `get_events` / `add_event` (Apple Calendar over iCloud CalDAV; adds only, never
edits or deletes, and DMs you a record of each event added), `get_time_and_date`, `set_timer` / `list_timers`, `add_note` / `read_notes`
(`data/notes.md`), `get_weather` (Kingston, Open-Meteo), `system_status`.

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
  saved to disk.
- `av` is pinned to 18.1.0: av 19 breaks faster-whisper 1.2.1.
- The NVIDIA driver is the DKMS `nvidia-driver-595-open`, signed with the enrolled MOK key, so it
  works with Secure Boot on. cuBLAS/cuDNN come from pip and are loaded by `jarvis/stt.py`.
