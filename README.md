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

From you going quiet to Jarvis's voice takes about 1.4-1.8 s for a plain question and 2.5-3 s
when Claude needs a tool (measured on this laptop). What keeps it there, in the order it happens:

- **A short pause when you're done.** A request that sounds finished (Whisper ends it with `.`,
  `?` or `!`, and not on a word like "to", "and" or "um", though a question may end on "like")
  ends after 0.5 s of quiet (`QUICK_SILENCE_SECONDS`); anything else waits 0.8 s
  (`SILENCE_SECONDS`).
- **Transcription during that pause.** Whisper starts 0.24 s into any pause, so the text is ready
  when the pause turns out to be the end: its 0.27 s (nearly the same for any length, as it
  always encodes a 30 s window) usually adds nothing. It is warmed up at startup, since its
  first run is 0.2 s slower.
- **Prompt caching.** The system prompt and tools never change (the time goes in a system message
  after your words), so they're cached, and each request caches the conversation for the next.
  On "Hey Jarvis" a `max_tokens=0` request loads the conversation so far into the cache while
  you're still talking: Claude's first word comes 0.65-0.9 s after the request instead of about
  0.95 s (up to 1.35 s). The cache lasts an hour rather than 5 minutes, since talking to Jarvis
  comes in bursts. Old turns are dropped only as a conversation starts or once 5 extra have
  piled up (`HISTORY_TURNS`): dropping one every turn changed the start of the conversation, so
  none of it could be read from the cache, which costs about 0.25 s on a 20k-token conversation.
- **Two requests at once.** Now and then the API takes 1.3-3 s rather than 0.6-0.8 s to start a
  reply, and it happens to one request at a time, so each request goes out twice and Jarvis
  follows whichever starts first (`CLAUDE_RACE`; it costs a little more).
- **A connection kept open.** Idle connections to the API are kept for 60 s rather than 5, so the
  request reuses the one the pre-warm opened.
- **Speaking while Claude writes.** Speech starts with the first sentence (Piper takes 0.03-0.23 s
  for it) while Claude writes the rest, and words said before a tool call ("Checking your
  calendar.") are spoken as soon as the call starts. If Claude thinks out loud there instead
  ("I need tomorrow's date, so I'll check..."), Jarvis says "One moment." in its place.
- **Faster tools.** Tools for different services in one reply (OnQ and the calendar, say) run at
  the same time. Email search fetches the messages in one batched request (2.5 s -> 0.6 s for
  ten), OnQ due work asks for every course at once (1.5-2.5 s -> 0.3-0.4 s), calendar queries go
  to every calendar at once, and the calendar no longer tries HTTP/3 first, which iCloud
  advertises but doesn't answer (a query after a few idle minutes: 1.4 s -> 0.45 s).
- **No wasted round trips.** A long tool call (a note, an email draft, six calendar events) is no
  longer cut off at the spoken-reply limit and asked for again. "Hey Jarvis" said again in the
  follow-up window gets the chime, as a wake does, rather than a trip to Claude for "Yes, sir?".
  Asking just the time or the date ("what time is it?", "what's the date?") right after the wake
  word is answered from the clock (Claude would look the time up with a tool first: two round
  trips, about 1.5 s); anything more, like "what time is my class?", still goes to Claude.

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
ignores it if it sounds like you're talking to someone else; starting with "Jarvis, ..." means
it's always answered, and "Hey Jarvis" on its own gets the chime again. A falling chime means it has gone
back to waiting for "Hey Jarvis". "Say that again", "sorry?", "what did you say?" and similar
replay the last reply word for word without asking Claude.

Discord: DM the bot, @mention it in a server, reply to one of its messages, or write in a
`#jarvis` channel (`DISCORD_CHANNEL`). It answers only `DISCORD_OWNER_ID`. Timers announce themselves where they
were set: spoken, or posted in `#jarvis` (with an @mention unless `DISCORD_MENTION=off`), where
calendar records also go.

### Learning mode

Every reply Jarvis gives (spoken, in Discord or to a Claude session) is logged to
`data/exchanges.jsonl`. To teach it, run this in a terminal:

```
.venv/bin/python -m jarvis.learn
```

It shows the last reply, then each new one as it happens. Type the reply you wanted ("Your first
class is at eight thirty in Kingston Hall.") or a note about it ("too long", "you should have
checked OnQ"); start with `=` to mark it as the exact wording. Enter skips. Claude turns each
piece of feedback into a one-line lesson and shows it; `undo` drops it. Lessons are kept in
`data/lessons.json` and Jarvis follows them from its next reply on, either for that channel
(how it sounds when speaking, how it formats Discord messages) or everywhere. The newest 40 are
used, plus the last 8 replies you rewrote as examples.

```
.venv/bin/python -m jarvis.learn recent 5    # feedback on the last 5 replies
.venv/bin/python -m jarvis.learn lessons     # what it has learned
.venv/bin/python -m jarvis.learn forget 3    # drop lesson #3
.venv/bin/python -m jarvis.learn add "Never read out email addresses."
```

### Apple Calendar setup

account.apple.com -> Sign-In and Security -> App-Specific Passwords -> create one, then put your
Apple ID email and that password in `.env` as `ICLOUD_USER` / `ICLOUD_APP_PASSWORD`. New events go
in `ICLOUD_CALENDAR` (default Jarvis) unless Claude picks a better fit (Work, Classes, ...).

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

### Spotify setup

Jarvis controls whatever device is playing Spotify (your phone, the desktop app, a speaker):
what's playing, play a song/album/artist/playlist by name, pause, skip, volume, shuffle, and
moving playback to another device. Controlling playback needs Spotify Premium.

1. developer.spotify.com/dashboard -> Create app. Name Jarvis, any description, Redirect URI
   `http://127.0.0.1:8767/callback` (exactly that), tick **Web API**, save.
2. Settings -> copy the **Client ID** into `.env` as `SPOTIFY_CLIENT_ID` (no secret needed).
   Under User Management, add your Spotify account's email if it asks.
3. `.venv/bin/python -m jarvis.tools.spotify login`, open the link (a phone is fine), agree, and
   paste back the `http://127.0.0.1:8767/callback?code=...` address the browser ends up on (it
   won't load; that's expected). Then `systemctl --user restart jarvis`.
4. Jarvis's own speaker as a Spotify device: Spotify Soloist (Spotify's official headless
   Connect player) runs as the user service `soloist`, shows up in Spotify as **Jarvis**, and
   plays through the default PipeWire sink (the echo-cancel sink, so "Hey Jarvis" is still heard
   over the music). It replaced spotifyd in October 2026: Spotify started refusing librespot's
   audio keys ("error audio key 0 1", every track skipped at 0:00; librespot issue #1649).
   "Play X" goes there unless you name another device (`SPOTIFY_SPEAKER` in `.env` to rename).
   While Jarvis listens and talks the music drops to 20% (`spotify.duck`, on the speaker's stream
   only), and comes back up after the end chime.
   Music is louder than Jarvis's voice: `echo-cancel-sink` is at 100%, and Jarvis's own stream
   ("PipeWire ALSA [python3.12]") at 65% (-11 dB), which WirePlumber remembers
   (~/.local/state/wireplumber/restore-stream) and applies to every new Jarvis stream.
   Soloist builds expire 90 days after their build date; Jarvis warns in Discord 14 and 3 days
   before, and `deploy/update-soloist.sh` installs the latest build and restarts the service.
   ```
   # API key: developer.spotify.com -> Soloist API Key page -> generate (needs Premium)
   mkdir -p ~/.config/soloist && (umask 077; echo 'SOLOIST_API_KEY=<key>' > ~/.config/soloist/soloist.env)
   ~/jarvis/deploy/update-soloist.sh   # downloads to ~/.local/opt/soloist-<version>, links ~/.local/bin/soloist
   cp ~/jarvis/deploy/soloist.service ~/.config/systemd/user/
   systemctl --user daemon-reload && systemctl --user enable --now soloist
   # once: pick "Jarvis" in the Spotify app's device list (same network) so it signs in
   ```

### OneDrive setup

Jarvis can list, search and read OneDrive files (Word, PDF, text), make folders and save new
files (Word documents or text). It never deletes, moves or overwrites: a taken name gets a
number added.

1. entra.microsoft.com (or portal.azure.com -> Microsoft Entra ID) -> App registrations -> New
   registration. Name Jarvis; account types: **Accounts in any organizational directory and
   personal Microsoft accounts**; Redirect URI: platform **Public client/native**, value
   `https://login.microsoftonline.com/common/oauth2/nativeclient`. Register.
2. Authentication -> Advanced settings -> **Allow public client flows: Yes** -> Save.
3. API permissions -> Add -> Microsoft Graph -> Delegated: `Files.ReadWrite`, `offline_access`,
   `User.Read`.
4. Copy the **Application (client) ID** into `.env` as `ONEDRIVE_CLIENT_ID`. For a personal
   OneDrive leave `ONEDRIVE_TENANT=common`; for the Queen's one, `ONEDRIVE_TENANT=queensu.ca`.
5. `.venv/bin/python -m jarvis.tools.onedrive login` prints a code: enter it at
   microsoft.com/devicelogin (a phone is fine) and sign in. Then `systemctl --user restart jarvis`.

A school account may say the app **needs admin approval**: Queen's then has to approve it, or
use a personal OneDrive. Your own account usually can't register apps in the school's directory,
which is why step 1 can be done with a personal Microsoft account at portal.azure.com.

### Kasa switches

`lights` turns the Kasa smart switches on and off and says which are on, by the names they
have in the Kasa app. It goes through TP-Link's cloud, not the LAN: the building's Wi-Fi keeps
devices from seeing each other, so hyde can't reach them directly (about a second a command).

1. `.venv/bin/python -m jarvis.tools.kasa login`: Kasa account email, password, and the emailed
   code if it asks. Only the tokens are kept, in `data/kasa-token.json`; they renew themselves.
2. Optional: `KASA_USER`/`KASA_PASSWORD` in `.env`, so it can log in again on its own if the
   saved login ever stops renewing.
3. `systemctl --user restart jarvis`.

`.venv/bin/python -m jarvis.tools.kasa` lists the switches it can see.

### Discord setup

1. discord.com/developers/applications -> New Application -> Bot -> Reset Token; put it in `.env`
   as `DISCORD_TOKEN`. On the same page, turn on **Message Content Intent**.
2. OAuth2 -> URL Generator: scope `bot`; open the URL and add the bot to any server you're in
   (needed so it can DM you).
3. Discord settings -> Advanced -> Developer Mode on; right-click your name -> Copy User ID; put it
   in `.env` as `DISCORD_OWNER_ID`.
4. `systemctl --user restart jarvis`, then DM the bot.

### Claude Code alerts

Jarvis says when a Claude Code session on this laptop is done ("I'm finished in jarvis.") or
needs you to check something ("I need your permission in jarvis.", "I need an answer from you
in ...", "I need you to check a plan in ..."), always in those two forms. Only
things more than 30 s after your last message to that session are said
(`JARVIS_CLAUDE_MIN_SECONDS`), since before that you're probably still watching. It goes through
a socket Jarvis listens on (`$XDG_RUNTIME_DIR/jarvis.sock`, `jarvis/alerts.py`; project-mode runs,
which announce themselves, are skipped), spoken, or
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

### Claude sessions talking to Jarvis (any device)

Jarvis is also an MCP server (`jarvis/mcp_server.py`), so Claude on any device (claude.ai on
the web, the desktop and phone apps, and Claude Code wherever you're signed in) can use it. It
offers `ask_jarvis` (a message to Jarvis, answered by its own Claude with all its tools; it
remembers the last `HISTORY_TURNS` messages from Claude sessions), `tell_owner` (spoken aloud
here, or posted in #jarvis) and every Jarvis tool directly.

It listens on `127.0.0.1:MCP_PORT` (8766) only; Tailscale Funnel gives it a public HTTPS
address. The secret in the URL is its only key, so keep the URL private.

1. Put a secret in `.env`: `echo "MCP_TOKEN=$(openssl rand -hex 32)" >> .env`, then
   `systemctl --user restart jarvis` (the log says `mcp server on 127.0.0.1:8766`).
2. `tailscale funnel --bg 8766` (the first time it may print a link to turn Funnel on for the
   tailnet; if it says access denied, run `sudo tailscale set --operator=$USER` first).
   `tailscale funnel status` shows the address, e.g. `https://hyde.tail3c1c60.ts.net`.
3. claude.ai -> Settings -> Connectors -> Add custom connector: name Jarvis, URL
   `https://hyde.tail3c1c60.ts.net/mcp/<MCP_TOKEN>`. It then appears in every Claude app and in
   Claude Code (`/mcp` lists it as `claude.ai Jarvis`). Turn it on per chat in the apps.

Without claude.ai, a Claude Code install can add it by itself:
`claude mcp add --scope user --transport http jarvis https://hyde.tail3c1c60.ts.net/mcp/<MCP_TOKEN>`.
To shut it off: `tailscale funnel --bg 8766 off`; to change the key, change `MCP_TOKEN`,
restart Jarvis, and update the connector's URL.

### The Jarvis window (Claude Code, in blue)

`jarvis` with nothing after it opens the Jarvis window in the current folder: Claude Code with a
screen of its own, JARVIS in blue where Claude Code has its orange mascot, and blue wherever
Claude Code is orange (the spinner, thinking, modes, dialogs). `jarvis -c` continues the last
conversation in that folder, `jarvis -r` picks one (`jarvis -r <id>` resumes that one). It starts
in auto mode (`--mode default`, or `JARVIS_MODE`, for another). On the Windows laptop, `jarvis`
runs it there, copied from hyde by `jarvis --update`, and `jarvis --hyde` opens it on hyde over
ssh (docs/terminal.md).

The answers are Claude Code's own: the window runs the real `claude` headless (`claude -p`,
stream-json both ways, on the Pro login), so every tool, MCP server (the Jarvis one included),
skill, hook, CLAUDE.md, auto mode and saved session is Claude Code's, and conversations are
shared with it (`/resume` sees both). Claude Code also takes on Jarvis's persona in this window
(calm, dry, the odd "sir"); `jarvis --plain` leaves it out. The terminal tab is titled with what the
conversation is about: its saved title, or one Haiku writes from the first prompt (headless
Claude Code doesn't title conversations), saved the way Claude Code saves its own so `/resume`
shows it in both.

The window draws the rest the way Claude Code does: streamed replies, thinking (one line,
ctrl+o shows it), tool calls with diffs and folded output, permission dialogs, AskUserQuestion,
plan approval, the task list, subagents, queued prompts and the context left; the footer also
always shows plan usage (5-hour, with when it resets, and weekly), the model and effort. The keys are
Claude Code's: shift+tab switches mode (default, accept edits, plan, auto), esc interrupts,
`\`+enter or ctrl+j adds a line, ctrl+x s sends now (stopping the turn in progress instead of
queueing), up and down go through past prompts (shared with Claude Code), left on an empty
prompt lists every saved conversation from every folder (resuming one moves the window to its
folder),
`/` lists commands, `@` files, `!` runs a shell command, ctrl+c twice quits. Commands Claude Code
runs headless (`/compact`, `/context`, `/model`, `/effort`, `/clear`, skills) go straight to it.
Claude Code's interactive menus are rebuilt here: `/usage`, `/status`, `/config` and `/stats`
open its Settings panel on that tab (←/→ between them: plan limits with reset times and what's
using them, `f` to put the suggested fix in the prompt; every setting, searchable, changed with
Enter; the activity heatmap and tokens per model, `r` for the date range), and `/mcp` lists the
MCP servers with their tools, to reconnect, disable or sign out of one. They ask Claude Code
for their data and change settings in a separate `claude -p`, so nothing lands in the
conversation. `/resume`, `/copy`, `/export` and `/help` are the window's own too; and `/claude`
opens the same conversation in plain Claude Code (for its own screens like `/plugin` or
`/login`), coming back when you exit it. Permission dialogs also run the `Notification` hooks,
which headless Claude Code skips, so the alerts above still say when it's waiting on you.

It lives in its own repo, [jarvis-wrapper](https://github.com/nmann06/jarvis-wrapper), cloned
to `~/jarvis-wrapper` on hyde (its README has the setup and tests).

## Checks

```
.venv/bin/python -m tests.claude_hello                     # Claude replies, prints token usage
.venv/bin/python -m jarvis.tts "Good evening, sir."        # speaks aloud
.venv/bin/python -m jarvis.stt 5                           # records 5 s, prints the transcript
.venv/bin/python -m tests.bench_stt                        # compares Whisper models (add @cpu to run one on CPU)
.venv/bin/python -m jarvis.llm "What time is it?"       # ask Claude by text, with tools
.venv/bin/python -m tests.discord_ping                      # DMs you a test notification
.venv/bin/python -m jarvis.wakeword                        # prints WAKE + what you said, Ctrl+C to stop
.venv/bin/python -m jarvis.clap                            # prints each clap (and near miss); --phrase listens for "Daddy's home" too
.venv/bin/python -m jarvis.homecoming --dry                # the homecoming sound, greeting and update, lights left alone
```

## Settings (`.env`)

| Setting | Default | Notes |
|---|---|---|
| `CLAUDE_MODEL` | `claude-haiku-5-5` | Any current Claude model id |
| `MAX_TOKENS` | `300` | Caps how long a spoken reply can be (counted as about 4 characters a token; tool calls such as long notes aren't limited by it) |
| `CLAUDE_THINKING` | `off` | `on` lets Claude think first: slower, rarely better for this |
| `CLAUDE_EFFORT` | `low` | `low` / `medium` / `high` |
| `HISTORY_TURNS` | `10` | Past exchanges sent with each request (up to 5 more pile up before the oldest are dropped, so the conversation stays cached) |
| `CLAUDE_RACE` | `on` | Send each Claude request twice at once and use whichever starts answering first |
| `PROJECT_MODEL` | `sonnet` | Claude Code model for project mode (`opus` uses up the plan faster) |
| `WHISPER_MODEL` | `large-v3-turbo` | `tiny.en` / `base.en` / `small.en` for CPU |
| `WHISPER_DEVICE` | `cuda` | `cpu` uses int8 and `WHISPER_THREADS` |
| `PIPER_VOICE` | `models/piper/en_GB-alan-medium.onnx` | |
| `TTS_LENGTH_SCALE`, `TTS_VOLUME` | `1.0` | Speech speed (>1 is slower) and loudness |
| `THINKING_SOUND` | `on` | Soft blips while waiting on Claude or a tool (`off` for silence) |
| `WAKE_THRESHOLD` | `0.5` | Raise if it wakes by itself, lower if it misses you |
| `HOMECOMING` | `on` | Clap twice, then "Daddy's home" (see Homecoming) |
| `CLAP_PEAK_DB`, `CLAP_MIN_DBFS` | `15`, `-40` | How far above the background, and how loud, a clap must be: raise if it hears claps that weren't, lower if it misses yours |
| `SILENCE_SECONDS` | `0.8` | Pause that ends what you're saying |
| `QUICK_SILENCE_SECONDS` | `0.5` | Shorter pause that ends it when it's already a finished sentence ("What time is it?"); set to `SILENCE_SECONDS` to always wait the full pause |
| `FOLLOW_UP_SECONDS` | `5` | How long it listens for a follow-up after replying |
| `INPUT_DEVICE`, `OUTPUT_DEVICE` | system default | Index or name substring; list with `python -m sounddevice` |

## Status

Done: TTS, STT (GPU), Claude with tools, wake word + end-of-speech detection, full pipeline,
systemd service, Discord bot, Apple Calendar, Gmail, the job-search sheet, Trello, OnQ and Smith
Learning, briefings and reminders, intake (todos and events from OnQ, email and texts), Claude
Code alerts, the MCP server, learning mode, project mode. Ready but waiting for your sign-in:
Spotify, OneDrive, iMessage/SMS (the Mac VM). Ideas for the Windows laptop (seeing its screen,
editing and running work there): docs/other-laptop.md.

## Project mode

For longer jobs (schoolwork, researching people or companies, a long discussion) Jarvis hands
the work to Claude Code (`claude -p`) on the Claude plan it's signed in with (Pro), not the API:
Jarvis removes the API key from its environment, so it can't bill the API. Say **"Jarvis, new
project"**:

> **You:** Jarvis, new project. **Jarvis:** What are you working on?
> **You:** 324 lab. **Jarvis:** Making a Lab 4 folder in your ELEC 324 folder in OneDrive... Done. How shall I start?
> **You:** Do the prelab. **Jarvis:** Starting the project. Anything you say now goes to it until you say "end project".

(or "start a project: ...", or ask in Discord). It runs in `data/projects/<name>/` with one
Claude Code session per project.

- **What it can use:** web search and fetch, and Jarvis's own tools through its MCP server:
  OnQ (content, syllabi, due dates), the calendar, notes, and OneDrive, where it can make
  folders and save new files (Word documents for written work) but never change or delete
  anything. No shell and no local file edits. If OneDrive isn't set up, it says so and the
  project runs without a folder.
- **What you hear:** Claude Code is told only text inside `<say>...</say>` is read aloud: a line
  when it starts, an update about once a minute or when it finds something, and a short summary
  at the end. Jarvis says those between conversations, and "still working on it" after two
  quiet minutes. Everything it writes goes to a thread in #jarvis.
- **Talking to it:** while a project is open, ask Jarvis anything as usual. Questions and
  instructions about the project's work ("what does question two ask?", "check my code for
  part three", "is my answer to question three right?") are passed to Claude Code, which reads
  your files (OneDrive, OnQ) and answers aloud; anything else (the time, weather, calendar,
  email, general questions) Jarvis answers itself. "End project" closes it (and stops a run in
  progress). Messages sent mid-run wait for it to finish. Writing in the project's Discord
  thread sends to it too. "Resume the ... project" reopens one; "what projects do I have"
  lists them.

It shares the Pro plan's usage limits with your own Claude Code use (Sonnet by default,
`PROJECT_MODEL`). Needs `MCP_TOKEN` set for the Jarvis tools (it is).

## Alarms

Handled entirely by `jarvis/alarms.py`, never by Claude, so it can't claim an alarm is set when
it isn't: requests that mention an alarm (or "wake me ...") are matched with regexes in the
voice loop and the Discord bot before Claude sees them, and anything about alarms it doesn't
understand gets a fixed reply. Claude's system prompt tells it alarms aren't its job.

- "Set an alarm for 7:30" / "wake me up at six tomorrow" / "alarm in 20 minutes" / "set an alarm
  for 7 on Monday with birds". A bare "7" is whichever of 7 am or 7 pm comes first; the reply
  says "tomorrow" or "tonight". "Set an alarm" alone asks for the time.
- "What alarms do I have?", "cancel my 7:30 alarm", "cancel all alarms".
- Sounds: classic, digital, chimes, marimba, birds (synthesized, no files), or random. "What
  alarm sounds are there?", "play the birds alarm sound", "change my alarm sound to chimes".
- Ringing: starts at `ALARM_START_VOLUME` (10%) and climbs evenly in decibels to full over
  `ALARM_RAMP_MINUTES` (15), for at most `ALARM_RING_MINUTES` (15), then posts in Discord that
  nobody turned it off. The speaker is unmuted and raised to at least `ALARM_MIN_VOLUME` (60%)
  while it rings. No wake word needed to stop it: "stop", "turn it off", "I'm up", "snooze"
  (`ALARM_SNOOZE_MINUTES`, 9). Vosk listens for those and Whisper confirms the word, so a
  beep can't switch it off; if Whisper can't make it out, a second "stop" within 20 s counts.
  "Hey Jarvis, ..." turns the alarm right down while you talk. In Discord, "stop" or "snooze"
  works too.
- Kept in `data/alarms.json`, so they survive a restart; one missed by more than 15 minutes
  (Jarvis not running) is reported in Discord instead.
- `python -m jarvis.alarms "set an alarm for 7:30 tomorrow"` shows what a phrase parses to;
  `python -m jarvis.alarms --play` plays each sound.

## Homecoming

Clap twice, then say "Daddy's home" or "Wake up" (or "Wake up, daddy's home"), no wake word
needed: a power-up sound, the lights coming on one by one (any already on are left alone), a
one-line greeting, and a short update (the next thing on the calendar, anything due in the
next two days, new texts and flagged emails; the time and weather when there's little else).
"Stop" cuts the talking short.

- `jarvis/clap.py` hears the claps: two sharp, high-pitched sounds 0.15 to 0.9 s apart that
  die away fast, with no third clap after (so applause, knocking and speech don't count).
  Whisper then has to hear the phrase within 4 s, or it goes back to listening without a sound.
  The update starts being gathered at the claps, so it's ready by the time the greeting ends.
- `jarvis/homecoming.py` runs the sequence. It waits 60 s before claps count again.
- Tuning: `python -m jarvis.clap` prints each clap and each near miss with why it didn't count;
  adjust `CLAP_PEAK_DB` / `CLAP_MIN_DBFS` (and the other `CLAP_` settings in `jarvis/config.py`)
  to suit. `python -m jarvis.homecoming --dry` tries the sequence without touching the lights.

"Lights on" and "lights off" (and "turn off the lights", "lights out", "kill the lights") switch
every light, answered without Claude; anything that names a switch or asks about them goes to
Claude as before.

## Intake

`jarvis/intake.py`, run every 20 minutes from the proactive loop, adds todos and calendar events
by itself. It gathers what's new since it last looked: OnQ and Smith Learning work due in the
next 3 weeks that isn't submitted, new course announcements, inbox email from the last two days
(promotions, social and forums left out) and texts from the Mac VM once they're 30 minutes old
(so a quick reply from you counts). Claude reads that next to the Trello cards, the next 60 days
of calendar (Classes left out) and what intake added before, and picks:

- todos, added to the `todo` list on the main Trello board with a due date if there is one:
  coursework due, forms, things you agreed to do, and "Respond to <name>" for messages that
  need a real answer;
- events, added to Apple Calendar: interviews, appointments, plans you agreed to and in-person
  tests (those go in Midterms, so the test reminders pick them up). Coursework stays on Trello,
  so it isn't reminded twice.

Anything already on Trello (any list) or the calendar is skipped, and so is anything intake
added in the last 60 days, so removing a wrong card or event keeps it gone. Each run's
additions are posted in #jarvis in one message, with where each came from. At most 8 per run;
calendars are limited to Jarvis (the default), Work, Midterms, Queen's, Ultimate and Family.
Email and texts are handed to Claude as untrusted content. What was already read is kept under
`intake` in `data/proactive.json`.

`python -m jarvis.proactive intake` shows how many new items each source has and what it would
add, without adding anything.

## Tools

Claude can call: `get_events` / `add_event` (Apple Calendar over iCloud CalDAV; adds only, never
edits or deletes, and DMs you a record of each event added), `get_time_and_date`, `set_timer` / `list_timers`, `add_note` / `list_notes` / `read_note`
(Google Docs in the Jarvis Notes Drive folder), `get_weather` (local weather, Open-Meteo), `system_status`, Spotify (`spotify_now_playing`, `spotify_play`, `spotify_control`, `spotify_devices`), `speaker_volume` (Jarvis and the music together; Spotify's own volume is the music alone), `lights` (Kasa switches, above), OneDrive (`onedrive_list`, `onedrive_search`, `onedrive_read`, `onedrive_create_folder`, `onedrive_save`; never deletes or overwrites), `start_project` / `project_message` / `resume_project` / `list_projects` (project mode, above), OnQ and Smith Learning (read-only):
`onq_courses`, `onq_due` (assignments with submitted/not, quizzes, course events),
`onq_announcements`, `onq_grades`, `onq_syllabus` (plus any "deliverable guidance" document; text cached in `data/onq-syllabi/`),
`onq_content` (everything posted under Content, plus pages linked from module descriptions, as some
courses keep their material on one page of course files) and `onq_read_content` (the text of one
posted file or page, or of a file on the course site by URL).

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
