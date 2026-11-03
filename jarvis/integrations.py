"""Setup for each service Jarvis uses, for the Setup page of the Jarvis Panel: the steps to
follow, the .env fields, the sign-in command and a read-only connection test (docs/panel-api.md).

    list_integrations()   every integration with its steps, fields and whether it's configured
    test(id)              {"ok": bool, "message": str}: the smallest authenticated read the
                          service offers, 15 s at most; nothing is sent, posted or changed
    login_command(id)     the sign-in command to run in the Jarvis folder, or None
    checks()              machine checks (venv, GPU, models, mic, speaker, echo cancel, service)

Light to import: the service libraries are imported inside the tests, and jarvis.config is never
imported, so a broken .env can't stop this. Values are read through settings.env_values(). A
test's message never contains a secret: errors are turned into plain advice here rather than
passed on, since the libraries put URLs and keys in their exception text.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from jarvis import extras, settings

ROOT = settings.ROOT
DATA = ROOT / "data"
TIMEOUT = 15  # seconds a test may take
HTTP = 10  # per request, so a slow service fails inside TIMEOUT

GOOGLE_TOKEN = ROOT / ".google_token.json"
SPOTIFY_TOKEN = DATA / "spotify-token.json"
ONEDRIVE_TOKEN = DATA / "onedrive-token.json"
KASA_TOKEN = DATA / "kasa-token.json"
ONEDRIVE_SCOPES = "offline_access Files.ReadWrite User.Read"  # as jarvis/tools/onedrive.py
LP = "1.64"  # Brightspace API version, as jarvis/tools/onq.py
D2L_SITES = {"onq": "https://onq.queensu.ca", "smith": "https://smithlearning.ca"}


class Problem(Exception):
    """A failure with its message already in plain words (no secrets)."""


def _py(*args):
    return [sys.executable, "-m", *args]


def _field(key, label, help):
    return {"key": key, "label": label, "help": help}


def _all(values, *keys):
    return all(values.get(k) for k in keys)


def _file(path):
    return Path(path).exists()


# ---- the integrations: data first, then each one's test below -------------------------------

INTEGRATIONS = [
    {"id": "anthropic", "name": "Claude (Anthropic)", "required": True,
     "summary": "The brain: every answer comes from Claude through Anthropic's API.",
     "steps": [
         "Create an account at [console.anthropic.com](https://console.anthropic.com) and add a "
         "payment method or credit under **Plans & Billing**. API use is billed separately from a "
         "Claude chat subscription.",
         "Open [Settings -> API keys](https://console.anthropic.com/settings/keys) and press "
         "**Create Key**. Name it Jarvis and copy the key now: it is shown only once.",
         "Paste it into **API key** below and save. The change takes effect when Jarvis restarts.",
     ],
     "fields": [_field("ANTHROPIC_API_KEY", "API key", "Starts with sk-ant-, from the API keys page.")],
     "configured": lambda v: _all(v, "ANTHROPIC_API_KEY")},

    {"id": "discord", "name": "Discord", "required": False,
     "summary": "Chat with Jarvis from your phone, and get timers, reminders and briefings there.",
     "steps": [
         "Open the [Discord Developer Portal](https://discord.com/developers/applications), press "
         "**New Application**, name it Jarvis, then open **Bot** in the left menu.",
         "On the Bot page press **Reset Token**, copy the token and paste it into **Bot token** "
         "below. It is shown only once.",
         "On the same page, under Privileged Gateway Intents, turn on **Message Content Intent** "
         "and save. Without it the bot cannot read what you write.",
         "Open **OAuth2 -> URL Generator**, tick the scope `bot`, and under Bot Permissions tick "
         "Send Messages, Read Message History, Create Public Threads and Send Messages in Threads. "
         "Open the generated URL and add the bot to a server you are in (this is also what lets "
         "it send you direct messages).",
         "In that server create a text channel called `jarvis` (or choose another name in the "
         "**Channel** setting). Jarvis answers everything written there and posts its alerts there.",
         "In Discord open **User Settings -> Advanced** and turn on **Developer Mode**, then "
         "right-click your own name and choose **Copy User ID**. Paste it into **Your user ID**. "
         "Jarvis only answers this one account.",
         "Restart Jarvis, then send the bot a direct message. The test below only checks the token "
         "and never posts anything.",
     ],
     "fields": [_field("DISCORD_TOKEN", "Bot token", "From the Bot page, after Reset Token."),
                _field("DISCORD_OWNER_ID", "Your user ID", "Numbers only, from Copy User ID."),
                _field("DISCORD_CHANNEL", "Channel", "Channel name Jarvis answers in (default jarvis).")],
     "configured": lambda v: _all(v, "DISCORD_TOKEN", "DISCORD_OWNER_ID")},

    {"id": "icloud", "name": "Apple Calendar (iCloud)", "required": False,
     "summary": "Read your calendar and add events. It never edits or deletes one.",
     "steps": [
         "Sign in at [account.apple.com](https://account.apple.com). Two-factor authentication "
         "has to be on for your Apple ID, or Apple won't offer app-specific passwords.",
         "Open **Sign-In and Security -> App-Specific Passwords**, create one called Jarvis and "
         "copy it. Use this, never your real Apple password.",
         "Enter your Apple ID email as **Apple ID** and the new password as **App-specific "
         "password** below.",
         "New events go into the calendar named in **Default calendar**. Create a calendar with "
         "that name in the Calendar app, or type the name of one you already have. Jarvis picks "
         "another of your calendars when an event clearly belongs there.",
         "Restart Jarvis so it picks up the change, then press Test.",
     ],
     "fields": [_field("ICLOUD_USER", "Apple ID", "Your Apple ID email address."),
                _field("ICLOUD_APP_PASSWORD", "App-specific password", "From App-Specific Passwords."),
                _field("ICLOUD_CALENDAR", "Default calendar", "Calendar new events go in (default Jarvis).")],
     "configured": lambda v: _all(v, "ICLOUD_USER", "ICLOUD_APP_PASSWORD")},

    {"id": "google", "name": "Google (Gmail, Sheets, Drive notes)", "required": False,
     "summary": "Read your Gmail and save drafts (it never sends), edit one Google Sheet, and "
                "keep notes as Google Docs.",
     "steps": [
         "Open [console.cloud.google.com](https://console.cloud.google.com) and create a project, "
         "for example called Jarvis.",
         "Open **APIs & Services -> Library** and enable three APIs: **Gmail API**, **Google "
         "Sheets API** and **Google Drive API**.",
         "Open **Google Auth Platform** (the OAuth consent screen). Choose **External**, app name "
         "Jarvis, and your email as the support and contact address. Under **Audience** add "
         "yourself as a test user, then press **Publish app**. It stays unverified, which is fine "
         "for your own use. Left in Testing mode, the sign-in expires every 7 days.",
         "Open **Clients -> Create client**, choose **Desktop app**, create it and download the "
         "JSON file. Save it as `google_client.json` in the Jarvis folder (the one containing "
         "`deploy` and `jarvis`). **This can't be done from the panel**: it is a file copy.",
         "Press **Sign in** below. It prints a link: open it in a browser, choose your account and "
         "accept the warning (**Google hasn't verified this app** -> **Advanced** -> **Go to "
         "Jarvis**). The sign-in finishes on port 8765 of the Jarvis computer, so a browser on "
         "another computer needs that port forwarded first: `ssh -L 8765:localhost:8765 <jarvis "
         "computer>`.",
         "For the job-search sheet, open it in Google Sheets and copy the long part of its address "
         "between `/d/` and `/edit` into **Sheet ID**. Gmail and notes work without it.",
         "Restart Jarvis, then press Test.",
     ],
     "fields": [_field("GOOGLE_SHEET_ID", "Sheet ID", "Optional: the part of the sheet's URL between /d/ and /edit.")],
     "login": {"label": "Sign in", "command": lambda: _py("jarvis.google_auth")},
     "configured": lambda v: _file(GOOGLE_TOKEN)},

    {"id": "zoho", "name": "Zoho Mail", "required": False,
     "summary": "A second inbox, read next to Gmail. Drafts are saved in it; nothing is sent.",
     "steps": [
         "In Zoho Mail open **Settings -> Mail Accounts**, choose your address, then **IMAP "
         "Access** and turn it on.",
         "Open your Zoho accounts page ([accounts.zoho.com](https://accounts.zoho.com), or "
         "accounts.zohocloud.ca, accounts.zoho.eu and so on, whichever matches your data centre), "
         "go to **Security -> App Passwords** and generate one called Jarvis. Zoho needs it "
         "whenever two-factor sign-in is on.",
         "Enter your Zoho address as **Zoho address** and the password as **App password**.",
         "Set **IMAP server** to match your account's data centre: `imappro.zoho.com` (US), "
         "`imappro.zohocloud.ca` (Canada), `imappro.zoho.eu` (Europe) for a custom-domain "
         "account, or `imap.zoho.com` and its regional equivalents for an @zoho.com address.",
         "Restart Jarvis. Its first email check only learns what is already in the inbox, so old "
         "mail doesn't set off alerts.",
     ],
     "fields": [_field("ZOHO_USER", "Zoho address", "Your Zoho Mail address."),
                _field("ZOHO_APP_PASSWORD", "App password", "From Security -> App Passwords."),
                _field("ZOHO_IMAP_HOST", "IMAP server", "Your data centre's IMAP server.")],
     "configured": lambda v: _all(v, "ZOHO_USER", "ZOHO_APP_PASSWORD")},

    {"id": "trello", "name": "Trello", "required": False,
     "summary": "Your todo board: Jarvis lists, adds, dates and finishes cards, and reminds you.",
     "steps": [
         "Open [trello.com/power-ups/admin](https://trello.com/power-ups/admin), press **New** to "
         "make a Power-Up called Jarvis (any workspace, any contact details), then open its "
         "**API key** page and press **Generate a new API Key**. Copy the key into **API key**.",
         "Open `https://trello.com/1/authorize?expiration=never&scope=read,write&response_type=token&name=Jarvis&key=YOUR_KEY` "
         "with your key in place of `YOUR_KEY`, and press **Allow**. Copy the token it shows "
         "(it starts with ATTA) into **Token**.",
         "Set **Board** to the name of the board that holds your todo list (default todo). Jarvis "
         "adds new cards to its first list and moves finished ones to a list called Done.",
         "Restart Jarvis, then press Test.",
     ],
     "fields": [_field("TRELLO_KEY", "API key", "From the Power-Up's API key page."),
                _field("TRELLO_TOKEN", "Token", "Starts with ATTA, from the authorize link."),
                _field("TRELLO_BOARD", "Board", "Name of your main todo board (default todo).")],
     "configured": lambda v: _all(v, "TRELLO_KEY", "TRELLO_TOKEN")},

    {"id": "spotify", "name": "Spotify (and the Jarvis speaker)", "required": False,
     "summary": "Play and control music on any of your Spotify devices, including Jarvis's own "
                "speaker. Controlling playback needs Spotify Premium.",
     "steps": [
         "Open the [Spotify developer dashboard](https://developer.spotify.com/dashboard) and press "
         "**Create app**. Name it Jarvis, give it any description, set the Redirect URI to exactly "
         "`http://127.0.0.1:8767/callback`, tick **Web API** and save.",
         "Open the app's **Settings** and copy the **Client ID** into **Client ID** below. No secret "
         "is needed. If Spotify asks, add your Spotify account's email under **User Management**.",
         "Sign in once in a terminal on the Jarvis computer, in the Jarvis folder: "
         "`.venv/bin/python -m jarvis.tools.spotify login`. **This can't be done from the panel** "
         "because it asks you to paste something back.",
         "Open the link it prints (a phone is fine) and agree. Your browser then lands on an "
         "address starting `http://127.0.0.1:8767/callback?code=...` that won't load properly: that "
         "is expected. Copy the whole address from the address bar and paste it back into the terminal.",
         "Restart Jarvis, then press Test.",
         "Optional, Jarvis's own speaker: Spotify Soloist is Spotify's headless player, and shows "
         "up in your Spotify device list as **Jarvis** (change the name with **Speaker name**). "
         "Generate a Soloist API key from the Soloist page of the developer site (Premium needed) "
         "and save it: `mkdir -p ~/.config/soloist && (umask 077; echo 'SOLOIST_API_KEY=your-key' "
         "> ~/.config/soloist/soloist.env)`.",
         "Install Soloist and its service from the Jarvis folder: `mkdir -p ~/.local/bin && "
         "deploy/update-soloist.sh` (it may end with a complaint that the soloist service doesn't "
         "exist yet: that's fine), then `cp deploy/soloist.service ~/.config/systemd/user/ && "
         "systemctl --user daemon-reload && systemctl --user enable --now soloist`. Once, pick "
         "**Jarvis** in the Spotify app's device list (same network) so it signs in. Soloist "
         "builds expire after 90 days: run `deploy/update-soloist.sh` again every couple of months.",
     ],
     "fields": [_field("SPOTIFY_CLIENT_ID", "Client ID", "From the app's Settings page."),
                _field("SPOTIFY_SPEAKER", "Speaker name", "Device music plays on unless you name another (default Jarvis).")],
     "configured": lambda v: _all(v, "SPOTIFY_CLIENT_ID") and _file(SPOTIFY_TOKEN)},

    {"id": "onedrive", "name": "OneDrive", "required": False,
     "summary": "List, search and read your OneDrive files, make folders and save new files. It "
                "never deletes, moves or overwrites.",
     "steps": [
         "Open [entra.microsoft.com](https://entra.microsoft.com) and go to **Identity -> "
         "Applications -> App registrations -> New registration**. Name it Jarvis. For supported "
         "account types choose **Accounts in any organizational directory and personal Microsoft "
         "accounts**. For Redirect URI choose the platform **Public client/native** and enter "
         "`https://login.microsoftonline.com/common/oauth2/nativeclient`, then **Register**.",
         "Open **Authentication -> Advanced settings**, set **Allow public client flows** to "
         "**Yes** and save.",
         "Open **API permissions -> Add a permission -> Microsoft Graph -> Delegated "
         "permissions** and add `Files.ReadWrite`, `offline_access` and `User.Read`.",
         "Copy the **Application (client) ID** from the app's Overview page into **Client ID**.",
         "Set **Accounts** to `common` for a personal OneDrive, or to your school or work domain "
         "(for example `example.edu`) for that one.",
         "Press **Sign in** below. It shows a code: enter it at "
         "[microsoft.com/devicelogin](https://microsoft.com/devicelogin) (a phone is fine) and sign in.",
         "Restart Jarvis, then press Test. A school or work account may say the app **needs admin "
         "approval**: your organisation then has to approve it, or you can use a personal "
         "OneDrive. If you can't register apps in your organisation's directory, register it "
         "with a personal Microsoft account at portal.azure.com instead.",
     ],
     "fields": [_field("ONEDRIVE_CLIENT_ID", "Client ID", "The Application (client) ID."),
                _field("ONEDRIVE_TENANT", "Accounts", "common, consumers, organizations, or a domain.")],
     "login": {"label": "Sign in", "command": lambda: _py("jarvis.tools.onedrive", "login")},
     "configured": lambda v: _all(v, "ONEDRIVE_CLIENT_ID") and _file(ONEDRIVE_TOKEN)},

    {"id": "onq", "name": "OnQ (Queen's Brightspace)", "required": False,
     "summary": "Courses, due work, announcements and grades from onq.queensu.ca. Read-only. "
                "Skip it if you aren't at Queen's: the address is built in.",
     "steps": [
         "No keys are needed. OnQ signs in through Microsoft with two-factor, so a real browser "
         "does it once. Press **Sign in** below: a Chromium window opens **on the Jarvis "
         "computer's own screen, not in this panel**, so do this while sitting at that computer "
         "(or through a remote desktop).",
         "In that window sign in to Microsoft and complete the two-factor prompt. Choose **Yes** at "
         "**Stay signed in?**. The window closes by itself and the session is saved.",
         "If it says the browser is missing, run `.venv/bin/python -m playwright install chromium` "
         "in the Jarvis folder once, then press Sign in again.",
         "After that Jarvis renews the session by itself. If Microsoft asks for the password again, "
         "Jarvis says so in Discord and you press Sign in once more.",
         "Press Test to check the session.",
     ],
     "fields": [],
     "login": {"label": "Sign in", "command": lambda: _py("jarvis.tools.onq", "login")},
     "configured": lambda v: _file(DATA / "onq-cookies.json")},

    {"id": "smith", "name": "Smith Learning", "required": False,
     "summary": "The Smith School of Business Brightspace (smithlearning.ca), read-only, in the "
                "same tools as OnQ. Skip it if you aren't a Smith student.",
     "steps": [
         "Easiest: enter your Smith username and password below. Jarvis then signs in by itself, "
         "and again whenever the session expires. They are stored in `.env` only.",
         "Or, without storing the password: press **Sign in**. A Chromium window opens **on the "
         "Jarvis computer's own screen, not in this panel**. Sign in there (there is no "
         "two-factor step). You will be asked again when the session expires.",
         "Restart Jarvis if you added the username and password, then press Test.",
     ],
     "fields": [_field("SMITH_USER", "Smith username", "Your smithlearning.ca username."),
                _field("SMITH_PASSWORD", "Smith password", "Optional: lets Jarvis sign in again by itself.")],
     "login": {"label": "Sign in", "command": lambda: _py("jarvis.tools.onq", "login", "smith")},
     "configured": lambda v: _file(DATA / "smith-cookies.json") or _all(v, "SMITH_USER", "SMITH_PASSWORD")},

    {"id": "kasa", "name": "Kasa smart switches", "required": False,
     "summary": "Turn your TP-Link Kasa switches on and off by voice and say which are on. It "
                "goes through TP-Link's cloud (about a second a command).",
     "steps": [
         "Set up your switches in the Kasa app first and give each a clear name: Jarvis uses those "
         "names.",
         "Sign in once in a terminal on the Jarvis computer, in the Jarvis folder: "
         "`.venv/bin/python -m jarvis.tools.kasa login`. It asks for your Kasa email, password and, "
         "if TP-Link wants one, the code it emails you. **This can't be done from the panel** "
         "because it asks you questions as it runs. Only the login tokens are kept, and they "
         "renew themselves.",
         "Optional: enter your Kasa email and password below so Jarvis can sign in again by itself "
         "if the saved login ever stops renewing.",
         "Restart Jarvis, then press Test. `.venv/bin/python -m jarvis.tools.kasa` also lists the "
         "switches it can see.",
     ],
     "fields": [_field("KASA_USER", "Kasa email", "Optional, for signing in again by itself."),
                _field("KASA_PASSWORD", "Kasa password", "Optional, for signing in again by itself.")],
     "configured": lambda v: _file(KASA_TOKEN) or _all(v, "KASA_USER", "KASA_PASSWORD")},

    {"id": "mcp", "name": "Claude everywhere (MCP and Tailscale Funnel)", "required": False,
     "summary": "Lets Claude on the web, your phone and other computers use Jarvis's tools, "
                "through a private link over Tailscale Funnel. Also needed for project mode.",
     "steps": [
         "Install [Tailscale](https://tailscale.com/download) on the Jarvis computer and sign in "
         "(`sudo tailscale up`). **This can't be done from the panel.**",
         "Make a long random secret in a terminal: `openssl rand -hex 32`. Paste it into **MCP "
         "token** below. It becomes part of the address, so it is the only key: keep it private.",
         "Restart Jarvis. Its log then says `mcp server on 127.0.0.1:8766` (or your **Local "
         "port**).",
         "Turn on Funnel: `tailscale funnel --bg 8766` (your port). The first time it may print a "
         "link to switch Funnel on for your tailnet: open it and approve. If it says access "
         "denied, run `sudo tailscale set --operator=$USER` first. `tailscale funnel status` shows "
         "your address, such as `https://your-machine.your-tailnet.ts.net`.",
         "In [claude.ai](https://claude.ai) open **Settings -> Connectors -> Add custom "
         "connector**. Name it Jarvis and use the URL `https://your-machine.your-tailnet.ts.net/mcp/"
         "YOUR_MCP_TOKEN`. It then appears in every Claude app. Turn it on per chat.",
         "Or from a Claude Code install: `claude mcp add --scope user --transport http jarvis "
         "https://your-machine.your-tailnet.ts.net/mcp/YOUR_MCP_TOKEN`.",
         "To switch it off: `tailscale funnel --bg 8766 off`. To change the key, change **MCP "
         "token**, restart Jarvis and update the connector's URL.",
     ],
     "fields": [_field("MCP_TOKEN", "MCP token", "A long random secret (openssl rand -hex 32)."),
                _field("MCP_PORT", "Local port", "Port Funnel forwards to (default 8766).")],
     "configured": lambda v: _all(v, "MCP_TOKEN")},

    {"id": "claude_code", "name": "Claude Code (project mode)", "required": False,
     "summary": "Long jobs (schoolwork, research) are handed to Claude Code on your Claude plan, "
                "not the API. Needs a Claude Pro or Max plan, and the MCP link above.",
     "steps": [
         "Install Claude Code on the Jarvis computer: `curl -fsSL https://claude.ai/install.sh | "
         "bash`. See [claude.com/claude-code](https://claude.com/claude-code) for other ways.",
         "Run `claude` once in a terminal and sign in with your Claude plan when it asks. **This "
         "can't be done from the panel.**",
         "Optionally choose the model with **Project model** (sonnet by default; opus uses your "
         "plan up faster).",
         "Set up **Claude everywhere (MCP)** above: project mode reaches Jarvis's tools through it.",
         "Press Test to check that Claude Code is installed and signed in.",
     ],
     "fields": [_field("PROJECT_MODEL", "Project model", "haiku, sonnet, opus or fable.")],
     "configured": lambda v: _claude_bin() is not None},
]


def _claude_bin():
    return shutil.which("claude") or (str(p) if (p := Path.home() / ".local/bin/claude").exists() else None)


def list_integrations():
    values = settings.env_values()
    out = []
    for spec in INTEGRATIONS:
        fields = []
        for f in spec["fields"]:
            secret = bool(settings.BY_KEY.get(f["key"], {}).get("secret"))
            fields.append({**f, "secret": secret, "set": bool(values.get(f["key"])),
                           "value": None if secret else values.get(f["key"])})
        login = spec.get("login")
        out.append({"id": spec["id"], "name": spec["name"], "required": spec["required"],
                    "summary": spec["summary"], "steps": list(spec["steps"]), "fields": fields,
                    "login": {"label": login["label"], "command": login["command"]()} if login else None,
                    "configured": bool(spec["configured"](values)), "testable": True})
    return out


def login_command(id):
    spec = _spec(id)
    return spec["login"]["command"]() if spec.get("login") else None


def _spec(id):
    for spec in INTEGRATIONS:
        if spec["id"] == id:
            return spec
    raise KeyError(id)


# ---- tests ----------------------------------------------------------------------------------

def _http(method, url, **kw):
    """requests call whose failures become plain advice (the exception text holds the URL)."""
    import requests
    try:
        r = requests.request(method, url, timeout=HTTP, **kw)
    except requests.exceptions.Timeout:
        raise Problem("The service didn't answer in time. Try again in a moment.") from None
    except requests.exceptions.RequestException:
        raise Problem("Couldn't reach the service. Check the internet connection.") from None
    return r


def _status(r, what, bad=(401, 403)):
    """Raise a plain-words Problem for an unhappy HTTP status."""
    if r.status_code in bad:
        raise Problem(f"{what} was refused. Check it is correct and hasn't been revoked.")
    if r.status_code == 429:
        raise Problem("The service says too many requests. Wait a minute and try again.")
    if r.status_code >= 400:
        raise Problem(f"The service returned an error ({r.status_code}). Try again later.")


def _need(values, label, *keys):
    missing = [k for k in keys if not values.get(k)]
    if missing:
        raise Problem(f"Not set up yet: fill in {label} first.")


def _write_private(path, data):
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.touch(mode=0o600)
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def _oauth_token(path, refresh, name, login_hint):
    """A valid access token from a saved token file, renewing it the way the tool itself does
    (and saving it, since the service may hand out a new refresh token)."""
    try:
        token = json.loads(path.read_text())
        token["access_token"], token["refresh_token"], token["expires_at"]
    except FileNotFoundError:
        raise Problem(f"Not signed in to {name} yet: {login_hint}.") from None
    except (ValueError, KeyError, OSError):
        raise Problem(f"The saved {name} sign-in is unreadable: {login_hint} again.") from None
    if token["expires_at"] - time.time() < 120:
        r = refresh(token)
        if r.status_code >= 400:
            raise Problem(f"The {name} sign-in has expired or was revoked: {login_hint} again.")
        new = r.json()
        token.update(access_token=new["access_token"], expires_at=time.time() + new["expires_in"],
                     refresh_token=new.get("refresh_token", token["refresh_token"]))
        _write_private(path, token)
    return token["access_token"]


def _test_anthropic(v):
    _need(v, "the API key", "ANTHROPIC_API_KEY")
    r = _http("GET", "https://api.anthropic.com/v1/models", params={"limit": 1},
              headers={"x-api-key": v["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
    if r.status_code == 401:
        raise Problem("Anthropic rejected the API key. Create a new one at console.anthropic.com "
                      "and paste it in again.")
    _status(r, "The API key")
    return "The API key works (listing models is free)."


def _test_discord(v):
    _need(v, "the bot token", "DISCORD_TOKEN")
    r = _http("GET", "https://discord.com/api/v10/users/@me",
              headers={"Authorization": f"Bot {v['DISCORD_TOKEN']}",
                       "User-Agent": "DiscordBot (https://github.com/, 1.0)"})
    if r.status_code == 401:
        raise Problem("Discord rejected the bot token. Press Reset Token on the Bot page and paste "
                      "the new one in.")
    _status(r, "The bot token")
    msg = f"The bot token works (bot: {r.json().get('username', 'unknown')})."
    if not re.fullmatch(r"\d+", v.get("DISCORD_OWNER_ID", "").strip()):
        msg += " Still to do: set your numeric user ID, or the bot won't answer anyone."
    return msg


def _test_icloud(v):
    _need(v, "your Apple ID and app-specific password", "ICLOUD_USER", "ICLOUD_APP_PASSWORD")
    import caldav
    from caldav.lib import error
    try:
        client = caldav.DAVClient("https://caldav.icloud.com", username=v["ICLOUD_USER"],
                                  password=v["ICLOUD_APP_PASSWORD"], features="icloud", timeout=HTTP)
        names = [c.get_display_name() for c in client.principal().calendars()]
    except error.AuthorizationError:
        raise Problem("Apple refused the login. Use an app-specific password (not your Apple "
                      "password) and check the Apple ID.") from None
    msg = f"Connected: {len(names)} calendars."
    wanted = v.get("ICLOUD_CALENDAR") or "Jarvis"
    if wanted not in names:
        msg += f" There's no calendar called {wanted}: create one, or change Default calendar."
    return msg


def _test_google(v):
    if not GOOGLE_TOKEN.exists():
        raise Problem("Not signed in to Google yet: press Sign in.")
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    try:
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN))
        if not creds.valid:
            creds.refresh(Request())  # not saved: Jarvis does that itself
        gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
        total = gmail.users().getProfile(userId="me").execute().get("messagesTotal", 0)
        msg = f"Gmail works ({total} messages in the mailbox)."
        if v.get("GOOGLE_SHEET_ID"):
            sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
            title = sheets.spreadsheets().get(spreadsheetId=v["GOOGLE_SHEET_ID"],
                                              fields="properties.title").execute()
            msg += f" The sheet \"{title['properties']['title']}\" opens."
        else:
            msg += " No Sheet ID is set, so the job-search sheet tools are off."
        return msg
    except RefreshError:
        raise Problem("The Google sign-in expired or was revoked (apps left in Testing mode expire "
                      "every 7 days). Press Sign in again.") from None
    except HttpError as e:
        code = getattr(e.resp, "status", 0)
        if code == 404:
            raise Problem("Google can't find that sheet. Check the Sheet ID.") from None
        if code == 403:
            raise Problem("Google refused. Check the Gmail, Sheets and Drive APIs are enabled in "
                          "your Cloud project, and that you can open the sheet.") from None
        raise Problem(f"Google returned an error ({code}). Try again later.") from None
    except (ValueError, KeyError):
        raise Problem("The saved Google sign-in is unreadable. Press Sign in again.") from None


def _test_zoho(v):
    _need(v, "your address and app password", "ZOHO_USER", "ZOHO_APP_PASSWORD")
    import imaplib
    import socket
    host = v.get("ZOHO_IMAP_HOST") or "imappro.zohocloud.ca"
    try:
        imap = imaplib.IMAP4_SSL(host, timeout=HTTP)
        try:
            imap.login(v["ZOHO_USER"], v["ZOHO_APP_PASSWORD"])
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    except imaplib.IMAP4.error:
        raise Problem("Zoho refused the login. Use an application-specific password, check IMAP "
                      "access is on, and that IMAP server matches your data centre.") from None
    except (OSError, socket.timeout):
        raise Problem(f"Couldn't reach {host}. Check the IMAP server setting and the internet "
                      "connection.") from None
    return "Signed in to Zoho over IMAP."


def _test_trello(v):
    _need(v, "the API key and token", "TRELLO_KEY", "TRELLO_TOKEN")
    r = _http("GET", "https://api.trello.com/1/members/me/boards",
              params={"key": v["TRELLO_KEY"], "token": v["TRELLO_TOKEN"], "filter": "open",
                      "fields": "name"})
    _status(r, "The key or token", bad=(400, 401, 403))
    names = [b["name"].strip().lower() for b in r.json()]
    board = (v.get("TRELLO_BOARD") or "todo").strip()
    msg = f"Connected: {len(names)} open boards."
    if board.lower() not in names:
        msg += f" There's no board called {board}: check the Board setting."
    return msg


def _test_spotify(v):
    _need(v, "the client ID", "SPOTIFY_CLIENT_ID")
    hint = "run the Spotify sign-in command in a terminal (see the steps)"

    def refresh(token):
        return _http("POST", "https://accounts.spotify.com/api/token",
                     data={"grant_type": "refresh_token", "refresh_token": token["refresh_token"],
                           "client_id": v["SPOTIFY_CLIENT_ID"]})
    access = _oauth_token(SPOTIFY_TOKEN, refresh, "Spotify", hint)
    r = _http("GET", "https://api.spotify.com/v1/me/player/devices",
              headers={"Authorization": f"Bearer {access}"})
    if r.status_code == 401:
        raise Problem(f"Spotify rejected the saved sign-in: {hint} again.")
    _status(r, "Spotify")
    names = [d.get("name", "") for d in r.json().get("devices", [])]
    speaker = v.get("SPOTIFY_SPEAKER") or "Jarvis"
    msg = f"Signed in to Spotify: {len(names)} devices are available right now."
    if speaker in names:
        msg += f" The speaker \"{speaker}\" is online."
    else:
        msg += (f" The speaker \"{speaker}\" isn't showing: that's fine if you don't use Soloist, "
                "else check `systemctl --user status soloist`.")
    return msg


def _test_onedrive(v):
    _need(v, "the client ID", "ONEDRIVE_CLIENT_ID")
    hint = "press Sign in"
    tenant = v.get("ONEDRIVE_TENANT") or "common"

    def refresh(token):
        return _http("POST", f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                     data={"client_id": v["ONEDRIVE_CLIENT_ID"], "grant_type": "refresh_token",
                           "refresh_token": token["refresh_token"], "scope": ONEDRIVE_SCOPES})
    access = _oauth_token(ONEDRIVE_TOKEN, refresh, "OneDrive", hint)
    r = _http("GET", "https://graph.microsoft.com/v1.0/me/drive", params={"$select": "driveType"},
              headers={"Authorization": f"Bearer {access}"})
    _status(r, "The saved OneDrive sign-in")
    return f"OneDrive works ({r.json().get('driveType', 'unknown')} drive)."


def _test_d2l(key, name):
    def test(v):
        import requests
        host = D2L_SITES[key]
        try:
            cookies = json.loads((DATA / f"{key}-cookies.json").read_text())
        except FileNotFoundError:
            raise Problem(f"Not signed in to {name} yet: press Sign in.") from None
        except (ValueError, OSError):
            raise Problem(f"The saved {name} session is unreadable: press Sign in.") from None
        session = requests.Session()
        for c in cookies:
            try:
                session.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
            except (KeyError, TypeError):
                continue
        r = _http("GET", f"{host}/d2l/api/lp/{LP}/users/whoami", cookies=session.cookies,
                  allow_redirects=False)
        if r.status_code != 200:
            raise Problem(f"The saved {name} session has expired. Jarvis renews it by itself when "
                          "it can; if this keeps happening, press Sign in.")
        return f"The {name} session works."
    return test


def _test_kasa(v):
    try:
        saved = json.loads(KASA_TOKEN.read_text())
    except FileNotFoundError:
        raise Problem("Not signed in to Kasa yet: sign in from a terminal (see the steps).") from None
    except (ValueError, OSError):
        raise Problem("The saved Kasa login is unreadable: sign in again from a terminal.") from None
    import asyncio
    from tplinkcloud import TPLinkDeviceManager
    from tplinkcloud.exceptions import TPLinkTokenExpiredError
    try:
        manager = TPLinkDeviceManager(prefetch=False, include_tapo=False, term_id=saved["term"],
                                      tplink_cloud_api_host=saved["host"])
        manager.set_auth_token(saved["token"])
        devices = asyncio.run(manager.get_devices())
    except TPLinkTokenExpiredError:
        raise Problem("The saved Kasa login has expired. Jarvis renews it itself the next time "
                      "it uses the lights (if you gave it your Kasa email and password, or the "
                      "refresh still works); otherwise sign in again from a terminal.") from None
    except KeyError:
        raise Problem("The saved Kasa login is incomplete: sign in again from a terminal.") from None
    return f"Kasa works: {len(devices)} switches found."


def _test_mcp(v):
    _need(v, "the MCP token", "MCP_TOKEN")
    port = v.get("MCP_PORT") or "8766"
    if not port.isdigit():
        raise Problem("Local port must be a number.")
    r = _http("POST", f"http://127.0.0.1:{port}/mcp/{v['MCP_TOKEN']}",
              json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    if r.status_code == 404:
        raise Problem("Jarvis is running but doesn't know this token. Restart Jarvis so it loads "
                      "the current one.")
    _status(r, "The local server")
    local = "The local MCP server answers."
    if not shutil.which("tailscale"):
        raise Problem(local + " Tailscale isn't installed, so nothing outside this computer can "
                              "reach it yet: install it (see the steps).")
    try:
        t = subprocess.run(["tailscale", "funnel", "status"], capture_output=True, text=True,
                           timeout=8)
        shown = t.stdout + t.stderr
    except subprocess.TimeoutExpired:
        shown = ""
    if re.search(rf"(127\.0\.0\.1|localhost):{port}\b", shown) and "Funnel on" in shown:
        return local + f" Tailscale Funnel is forwarding to port {port}."
    raise Problem(local + f" Tailscale Funnel isn't forwarding port {port} yet: run "
                          f"`tailscale funnel --bg {port}` in a terminal.")


def _test_claude_code(v):
    claude = _claude_bin()
    if not claude:
        raise Problem("Not set up yet: Claude Code isn't installed (see the steps).")
    try:
        r = subprocess.run([claude, "auth", "status"], capture_output=True, text=True, timeout=12)
        status = json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        raise Problem("Claude Code didn't answer in time. Try again.") from None
    except ValueError:
        raise Problem("Claude Code is installed but didn't report its sign-in. Run `claude` in a "
                      "terminal and sign in.") from None
    if not status.get("loggedIn"):
        raise Problem("Claude Code isn't signed in. Run `claude` in a terminal and sign in with "
                      "your Claude plan.")
    return "Claude Code is installed and signed in."


TESTS = {"anthropic": _test_anthropic, "discord": _test_discord, "icloud": _test_icloud,
         "google": _test_google, "zoho": _test_zoho, "trello": _test_trello,
         "spotify": _test_spotify, "onedrive": _test_onedrive,
         "onq": _test_d2l("onq", "OnQ"), "smith": _test_d2l("smith", "Smith Learning"),
         "kasa": _test_kasa, "mcp": _test_mcp, "claude_code": _test_claude_code}
for extra in extras.more_integrations():  # those of the gitignored private/, if it has any
    TESTS[extra["id"]] = extra["test"]
    INTEGRATIONS.append({k: v for k, v in extra.items() if k != "test"})


def _scrub(text, values):
    """Last line of defence: no secret setting's value survives in a message."""
    for key, value in values.items():
        if value and len(value) >= 4 and settings.BY_KEY.get(key, {}).get("secret"):
            text = text.replace(value, "[hidden]")
    return text


def test(id):
    """Run an integration's read-only test: {"ok": bool, "message": str}, within TIMEOUT."""
    _spec(id)
    values = settings.env_values()
    result = {}

    def run():
        try:
            result["r"] = {"ok": True, "message": TESTS[id](values)}
        except Problem as e:
            result["r"] = {"ok": False, "message": str(e)}
        except BaseException as e:  # the libraries' own text may hold URLs or keys: name only
            result["r"] = {"ok": False, "message": f"The test failed unexpectedly ({type(e).__name__}). "
                                                    "Check the connection and the values above, then try again."}

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(TIMEOUT)
    r = result.get("r") or {"ok": False, "message": f"No answer within {TIMEOUT} seconds. Try again."}
    return {"ok": r["ok"], "message": _scrub(r["message"], values)}


# ---- machine checks -------------------------------------------------------------------------

REQUIRED_PACKAGES = ["anthropic", "faster_whisper", "piper", "sounddevice", "openwakeword", "vosk",
                     "numpy", "discord", "caldav", "googleapiclient", "tplinkcloud", "playwright",
                     "psutil", "dotenv", "requests", "aiohttp"]
WHISPER_REPOS = {"large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"}  # others: Systran/faster-whisper-<name>
AUDIO_LIST = ("import json, sounddevice as sd\n"
              "print(json.dumps({'devices': [[i, d['name'], d['max_input_channels'], "
              "d['max_output_channels']] for i, d in enumerate(sd.query_devices())], "
              "'default': [sd.default.device[0], sd.default.device[1]]}))")


def _check(id, name, ok, detail, fix):
    return {"id": id, "name": name, "ok": bool(ok), "detail": detail, "fix": fix}


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r


def _venv_python():
    py = ROOT / ".venv" / "bin" / "python"
    return str(py) if py.exists() else None


def _check_venv():
    fix = ("Run `deploy/install.sh` in the Jarvis folder. It builds the Python environment and "
           "installs everything Jarvis needs; it is safe to run again.")
    py = _venv_python()
    if not py:
        return _check("venv", "Python environment", False, "There is no .venv in the Jarvis folder.", fix)
    code = ("import importlib.util, json, sys; print(json.dumps([m for m in %r "
            "if importlib.util.find_spec(m) is None]))" % REQUIRED_PACKAGES)
    r = _run([py, "-c", code])
    try:
        missing = json.loads(r.stdout)
    except (AttributeError, ValueError):
        return _check("venv", "Python environment", False, "The .venv's Python doesn't run.", fix)
    if missing:
        return _check("venv", "Python environment", False,
                      "Packages missing: " + ", ".join(missing) + ".", fix)
    return _check("venv", "Python environment", True, "The .venv is there with every package Jarvis needs.", fix)


def _check_gpu(values):
    fix = ("Install NVIDIA's driver for your card (on Ubuntu: Software & Updates -> Additional "
           "Drivers, or `sudo ubuntu-drivers install`), then reboot. With Secure Boot on, the "
           "driver has to be signed: the installer asks you to set a password and enrol it at the "
           "next boot. No NVIDIA GPU? Set Whisper device to cpu and Whisper model to "
           "small.en in Settings.")
    cpu = (values.get("WHISPER_DEVICE") or "cuda") == "cpu"
    r = _run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"])
    if r is not None and r.returncode == 0 and r.stdout.strip():
        return _check("gpu", "NVIDIA GPU", True, r.stdout.strip().splitlines()[0], fix)
    why = "nvidia-smi isn't installed" if r is None else "nvidia-smi can't talk to the driver"
    if cpu:
        return _check("gpu", "NVIDIA GPU", True, f"No working GPU ({why}), but Whisper is set to run "
                                                 "on the CPU, so none is needed.", fix)
    return _check("gpu", "NVIDIA GPU", False, f"{why}.", fix)


def _hf_cache():
    base = os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME") or Path.home() / ".cache/huggingface") / "hub"
    return Path(base)


def _check_models(values):
    fix = ("Run `deploy/install.sh` again: it downloads whatever is missing (the speech voice, the "
           "\"stop\" listener, the wake word and the Whisper model).")
    missing = []
    piper = ROOT / (values.get("PIPER_VOICE") or "models/piper/en_GB-alan-medium.onnx")
    if not (piper.exists() and Path(str(piper) + ".json").exists()):
        missing.append("voice (Piper)")
    if not (ROOT / (values.get("VOSK_MODEL") or "models/vosk-model-small-en-us-0.15") / "am" / "final.mdl").exists():
        missing.append("stop-word listener (Vosk)")
    if not list((ROOT / ".venv").glob("lib/python*/site-packages/openwakeword/resources/models/hey_jarvis*.onnx")):
        missing.append("wake word (Hey Jarvis)")
    whisper = values.get("WHISPER_MODEL") or "large-v3-turbo"
    repo = WHISPER_REPOS.get(whisper, f"Systran/faster-whisper-{whisper}")
    if not list((_hf_cache() / ("models--" + repo.replace("/", "--"))).glob("snapshots/*/model.bin")):
        missing.append(f"speech recognition (Whisper {whisper})")
    if missing:
        return _check("models", "Models", False, "Missing: " + ", ".join(missing) + ".", fix)
    return _check("models", "Models", True, f"Voice, stop listener, wake word and Whisper {whisper} are downloaded.", fix)


def _audio_devices():
    py = _venv_python() or sys.executable
    r = _run([py, "-c", AUDIO_LIST], 15)  # a fresh process, so a device plugged in later shows up
    try:
        return json.loads(r.stdout)
    except (AttributeError, ValueError):
        return None


def _pick(audio, setting, column, side):
    """(device name or None, what to say): the device Jarvis would use for this setting."""
    devices = audio["devices"]
    if setting:
        if setting.isdigit():
            hit = [d for d in devices if d[0] == int(setting)]
        else:
            hit = [d for d in devices if setting.lower() in d[1].lower()]
        hit = [d for d in hit if d[column]]
        if hit:
            return hit[0][1], None
        return None, f"No {side} matches \"{setting}\" (the {side} setting)."
    default = audio["default"][column - 2]
    named = [d for d in devices if d[0] == default and d[column]]
    if named:
        return named[0][1], None
    anyone = [d for d in devices if d[column]]
    if anyone:
        return anyone[0][1], None
    return None, f"No {side} found."


def _check_audio(values, id, name, setting, column, side, fix):
    audio = _audio_devices()
    if audio is None:
        return _check(id, name, False, "Couldn't list sound devices (is the Python environment set up?).", fix)
    device, problem = _pick(audio, (values.get(setting) or "").strip(), column, side)
    if device:
        return _check(id, name, True, f"Using: {device}.", fix)
    return _check(id, name, False, problem, fix)


def _check_echo():
    fix = ("Echo cancellation lets Jarvis hear \"stop\" over its own voice. Run `deploy/install.sh`, "
           "then restart the sound system with `systemctl --user restart pipewire pipewire-pulse "
           "wireplumber` (or log out and in) and make the echo-cancelled devices the default in "
           "your sound settings. It needs the `pipewire-audio` and `libspa-0.2-modules` packages "
           "(`sudo apt install pipewire-audio libspa-0.2-modules` on Ubuntu).")
    conf = Path.home() / ".config/pipewire/pipewire.conf.d/60-jarvis-echo-cancel.conf"
    if not conf.exists():
        return _check("echo_cancel", "Echo cancellation", False, "The echo cancel config isn't installed.", fix)
    r = _run(["pactl", "list", "short", "sources"], 5)
    if r is None or r.returncode:
        return _check("echo_cancel", "Echo cancellation", False,
                      "The config is installed, but the sound system didn't answer.", fix)
    if "echo-cancel-source" not in r.stdout:
        return _check("echo_cancel", "Echo cancellation", False,
                      "The config is installed, but the echo-cancelled devices aren't running yet.", fix)
    default = _run(["pactl", "get-default-source"], 5)
    if default is None or "echo-cancel" not in default.stdout:
        return _check("echo_cancel", "Echo cancellation", False,
                      "The echo-cancelled devices exist but aren't the default microphone.", fix)
    return _check("echo_cancel", "Echo cancellation", True, "Running and set as the default microphone.", fix)


def _check_service():
    fix = ("Start it with `systemctl --user enable --now jarvis`. If it says the unit doesn't "
           "exist, run `deploy/install.sh` first. To have it start at boot without you logging "
           "in, run `sudo loginctl enable-linger $USER` once.")
    r = _run(["systemctl", "--user", "is-active", "jarvis"], 5)
    if r is None:
        return _check("service", "Jarvis service", False, "systemd isn't available here.", fix)
    state = r.stdout.strip() or "unknown"
    enabled = (_run(["systemctl", "--user", "is-enabled", "jarvis"], 5) or r).stdout.strip() or "unknown"
    lingering = "Linger=yes" in ((_run(["loginctl", "show-user", str(os.getuid()), "-p", "Linger"], 5) or r).stdout)
    detail = f"The service is {state} and {enabled}." + ("" if lingering else " It won't start at boot until you log in (linger is off).")
    return _check("service", "Jarvis service", state == "active", detail, fix)


def _check_env():
    fix = "Copy the example: `cp .env.example .env && chmod 600 .env` in the Jarvis folder, then add your Anthropic key."
    if not settings.ENV.exists():
        return _check("env", "Settings file", False, "There is no .env file.", fix)
    mode = settings.ENV.stat().st_mode & 0o077
    if mode:
        return _check("env", "Settings file", False,
                      "The .env file holds your keys but other users on this computer can read it.",
                      "Run `chmod 600 .env` in the Jarvis folder.")
    return _check("env", "Settings file", True, "The .env file exists and is private.", fix)


def checks():
    values = settings.env_values()
    steps = [_check_venv, lambda: _check_env(), lambda: _check_gpu(values),
             lambda: _check_models(values),
             lambda: _check_audio(values, "mic", "Microphone", "INPUT_DEVICE", 2, "microphone",
                                  "Plug in a microphone and check it in your sound settings. To "
                                  "pick one other than the default, set Microphone in Settings."),
             lambda: _check_audio(values, "speaker", "Speaker", "OUTPUT_DEVICE", 3, "speaker",
                                  "Connect a speaker and check it in your sound settings. To pick "
                                  "one other than the default, set Speaker in Settings."),
             _check_echo, _check_service]
    out = []
    for step, (id, name) in zip(steps, [("venv", "Python environment"), ("env", "Settings file"),
                                        ("gpu", "NVIDIA GPU"), ("models", "Models"),
                                        ("mic", "Microphone"), ("speaker", "Speaker"),
                                        ("echo_cancel", "Echo cancellation"),
                                        ("service", "Jarvis service")]):
        try:
            out.append(step())
        except Exception as e:  # one broken check must not hide the others
            out.append(_check(id, name, False, f"The check itself failed ({type(e).__name__}).",
                              "Try again; if it keeps failing, look at the panel's log."))
    return out
