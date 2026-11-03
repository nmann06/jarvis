# Jarvis Panel

The desktop app for setting up, configuring and improving Jarvis. It is a window onto the panel
server's web UI (`jarvis/panel/`, see `docs/panel-api.md`), plus a tray icon whose colour follows
the light, a small floating light you can drag anywhere, and a connection dialog.

    app/web/            the web UI: plain HTML, CSS and JS, no build step, nothing loaded from the internet
    app/jarvis_panel/   the PySide6 shell (python -m jarvis_panel, run from app/)
    app/tests/          a mock panel server and the UI and shell checks

## The light

| State | Looks like |
|---|---|
| `listening` (you are talking to Jarvis) | on, amber |
| `speaking` | blue |
| `thinking` | amber, pulsing slowly (steady and dimmer if the OS asks for reduced motion) |
| `idle` | off |
| `unknown` | grey ring with a hollow centre: not connected, or Jarvis has no voice hooks |

When Jarvis isn't connected the light is grey, never "off": a dropped connection must not look like
a quiet Jarvis. The Home page says why there is no light (`hooks_reason`): only `needs_restart` asks for
a restart; `voice_off`, `starting` and `down` say what is actually going on.

## Running it

On the Jarvis machine (`hyde`) it finds `data/panel-token` itself and connects to `127.0.0.1:8767`:

    cd app
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m jarvis_panel

Anywhere else (the Windows laptop) it connects to the host `hyde` over Tailscale. The first time, a
dialog asks for the host and the token: paste the contents of `data/panel-token` from the Jarvis
machine. The token is kept in the per-user config directory (`~/.config/jarvis-panel/` on Linux, mode
600; `%APPDATA%\JarvisPanel\` on Windows) and the host is remembered. If it can't connect, the dialog
comes back with what went wrong and what to do, and Retry.

Closing the window hides it to the tray; **Quit** is in the tray menu. The tray menu also toggles the
floating light, which remembers where you left it (double-click it to open the panel; right-click to
hide it). If the desktop has no system tray (GNOME needs the AppIndicator extension), closing the window
quits the app instead, so nothing is left running unseen.

### Linux notes

- The Qt X11 plugin needs `libxcb-cursor0` (`sudo apt install libxcb-cursor0`). Without it Qt prints
  "Could not load the Qt platform plugin xcb". It isn't installed on hyde at the time of writing.
- On a Wayland session the app uses XWayland when that library is there, because Wayland doesn't let a
  window stay on top or remember its position, which the floating light needs. Without it Qt falls back to
  Wayland, where the floating light can't be pinned on top.

## Building

Linux (output `dist/jarvis-panel/jarvis-panel`, about 590 MB because it carries Chromium):

    cd app && ./build.sh

Windows (PowerShell, Python 3.12 from python.org on PATH; output `dist\jarvis-panel\jarvis-panel.exe`):

    cd app; .\build.ps1

The Windows build can't be made from Linux. The workflow `.github/workflows/build-panel.yml` builds
both on GitHub (Actions tab, Build Jarvis Panel, Run workflow; it also runs on pushes that touch `app/`)
and uploads a zip and a tarball as artifacts. Unzip the Windows one anywhere and run `jarvis-panel.exe`.

To put the Linux build in the application menu: `cp jarvis-panel.desktop ~/.local/share/applications/`
(edit `Exec` and `Icon` first if the folder differs).

## Tests

These never touch the real service, port 8767 or real data; they use temp ports and throwaway tokens.
`tests/mock_server.py` imitates the panel server (use `/home/nate/jarvis/.venv/bin/python`, which has aiohttp).

    # web UI, headless Chromium via Playwright: behaviour checks, then screenshots of every page and state
    /home/nate/jarvis/.venv/bin/python tests/behaviour.py
    /home/nate/jarvis/.venv/bin/python tests/shots.py /tmp/panel-shots

    # the desktop shell, offscreen, against the mock (needs the app venv)
    .venv/bin/python tests/shell_check.py

    # the web UI against the real jarvis/panel server with temp data and a temp .env
    /home/nate/jarvis/.venv/bin/python tests/real_server_check.py

The shell checks need no display (`QT_QPA_PLATFORM=offscreen` is set by the script).

## Security notes

- The web UI sets a strict Content-Security-Policy (`default-src 'none'`; scripts, styles, images, audio and
  connections from the same origin only). All dynamic text is inserted with `textContent`; the little markdown
  in the setup steps (bold, `code`, http(s) links) is turned into DOM nodes, never HTML.
- Secrets are write-only in the UI: it shows "Set" or "Not set", never pre-fills, never keeps the value.
- The web view in the desktop app is off the record (no cookies, cache or audio on disk) and logs in again
  with the token on each start. Links to other sites open in your normal browser.
