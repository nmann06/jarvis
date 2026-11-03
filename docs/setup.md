# Setting up Jarvis

Jarvis is a local "Hey Jarvis" voice assistant. Speech recognition and the voice run on your own
computer; only the text of what you say goes to Claude. This guide takes a fresh Linux machine to
a working Jarvis, using the **Jarvis Panel** (a web app with a desktop window) for everything
after the install.

## What you need

- **Linux** with systemd and PipeWire (Ubuntu 24.04 or newer is what it is developed on), and
  Python 3.11 or newer.
- **An NVIDIA GPU** for fast speech recognition (a 6 GB card like an RTX 3060 is enough). Without
  one Jarvis still works on the CPU with a smaller Whisper model, noticeably slower: after
  installing, set **Whisper device** to `cpu` and **Whisper model** to `small.en` in the panel's
  Settings page.
- **A microphone and a speaker**. A webcam mic is fine; a headset avoids echo.
- **An Anthropic API key**, the only thing Jarvis can't work without
  ([console.anthropic.com](https://console.anthropic.com)). Everything else is optional.
- About 5 GB of disk space for the Python packages and models, and an internet connection.

## Install

In a terminal, in the folder you cloned Jarvis into:

```
deploy/install.sh
```

It builds the Python environment, installs the pinned packages, downloads the voice, wake word,
"stop" listener and Whisper model (a few minutes the first time), sets up echo cancellation, and
installs two systemd user services: `jarvis` (the assistant) and `jarvis-panel` (the panel). It
creates `.env` from `.env.example` if you have none.

It is safe to run again: it does only what is missing, never overwrites `.env` or an existing
config, and never restarts or stops anything that is running. Anything that needs administrator
rights (such as `sudo apt install libportaudio2`) is **printed at the end, not run**: copy those
lines into a terminal yourself, then run the script again.

Options: `--skip-models` (no downloads), `--skip-python`, `--with-browser` (Chromium, only needed
for the OnQ and Smith Learning sign-in), `--no-systemd`.

When it finishes, load the echo cancel config once. This briefly interrupts any audio:

```
systemctl --user restart pipewire pipewire-pulse wireplumber
pactl set-default-source echo-cancel-source
pactl set-default-sink echo-cancel-sink
```

## Open the panel

The `jarvis-panel` service is started by the installer. Open `http://127.0.0.1:8767` in a browser
on that computer, or use the Jarvis Panel desktop app, which signs in by itself on the Jarvis
machine. (A different port can be set with `PANEL_PORT`.)

The panel is protected by a token, created the first time it starts and kept in `data/panel-token`
in the Jarvis folder. A browser asks for it once: it can be given as
`http://127.0.0.1:8767/login?token=THE_TOKEN`, which sets a cookie.

If the page doesn't load, check the service: `systemctl --user status jarvis-panel`.

## Connect from Windows (or another computer) over Tailscale

The panel listens only on the machine itself and on its Tailscale address, never on the open
network. To use it from another computer:

1. Install [Tailscale](https://tailscale.com/download) on both computers and sign in to the same
   account. On the Jarvis machine: `sudo tailscale up`. Find its name or address with
   `tailscale status` (an address like `100.x.y.z`).
2. Read the token on the Jarvis machine: `cat data/panel-token` in the Jarvis folder. Treat it like
   a password.
3. In the Jarvis Panel desktop app on the other computer, enter the address
   (`http://100.x.y.z:8767` or the machine's Tailscale name) and paste the token in the
   connection dialog. It remembers them for next time, stored in your user account's settings
   folder. Or use a browser: `http://100.x.y.z:8767/login?token=THE_TOKEN`.

Some things can't be done from the panel on another computer because they happen on the Jarvis
machine itself: the OnQ and Smith Learning sign-in opens a browser window on its screen, and a few
sign-ins (Spotify, Kasa) ask you to type in a terminal there. Each integration's steps say so.

## The order to set things up in

Open the **Setup** page. It starts with machine checks (Python environment, GPU, models,
microphone, speaker, echo cancellation, service), each with a plain-words fix when it's not green.
Then:

1. **Claude (Anthropic)**: paste the API key and press **Test**. Nothing else works without it.
2. **Start Jarvis**: `systemctl --user enable --now jarvis`, or the Start button on the panel's
   Home page. Say "Hey Jarvis", wait for the chime, then speak. If it doesn't wake or mishears
   you, the **Improve** page has a button for each problem.
3. **Discord**, to talk to Jarvis from your phone and to get timers, reminders and the morning
   briefing.
4. Then whatever you use, in any order: **Apple Calendar**, **Google** (Gmail, Sheets, notes),
   **Zoho Mail**, **Trello**, **Spotify**, **OneDrive**, **OnQ / Smith Learning** (only for
   those schools), **Kasa** lights, and finally **Claude everywhere (MCP)** and **Claude Code**
   for project mode.

Each integration shows its steps, its fields, a **Sign in** button where the app can do it, and a
**Test** button. A test only reads from the service (lists something or fetches a profile): it
never sends, posts or changes anything. After saving keys, **restart Jarvis** (the panel offers a
Restart button) so it picks them up.

## Troubleshooting

- **Nothing happens when I say "Hey Jarvis".** Is the service running? `systemctl --user status
  jarvis`. Is the right microphone the default? Check the **Microphone** check. Then use the
  Improve page's "I said Hey Jarvis and it didn't wake" button: it keeps the last 30 seconds of
  audio and suggests a more sensitive wake setting.
- **It wakes by itself.** The "It woke by itself" button on the Improve page suggests raising
  **Wake word sensitivity**.
- **Jarvis doesn't hear "stop" while it talks.** Echo cancellation isn't running or isn't the
  default. See the echo cancellation check, and the commands under Install.
- **The GPU check fails.** `nvidia-smi` must work in a terminal. Install NVIDIA's driver for your
  card and reboot (with Secure Boot on, enrol the signing key when the installer asks). Or run
  Whisper on the CPU, as above.
- **The service fails right away.** `journalctl --user -u jarvis -n 50` shows why (the panel's Logs
  page shows the same). The usual causes are a missing `ANTHROPIC_API_KEY` in `.env`, or a
  missing model: run `deploy/install.sh` again.
- **It stops when I log out, or doesn't start at boot.** Run `sudo loginctl enable-linger $USER`
  once so user services run without a login.
- **Google says the sign-in expired.** Apps left in Testing mode on the Google consent screen
  expire every 7 days. Publish the app (the Google steps say how) and press **Sign in** again.
- **A test fails with a "refused" message.** The key or token is wrong or was revoked: create a
  new one and paste it in again. The panel never shows a stored secret back; it only shows
  whether it is set.
- **I can't reach the panel from Windows.** Check both computers show in `tailscale status`, the
  address and port are right, and the token is exactly the contents of `data/panel-token`.
- **I changed a setting and nothing changed.** Settings in `.env` apply when Jarvis restarts.
