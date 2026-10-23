"""Spotify: what's playing, play something, pause/skip/volume, and which device plays it.

Uses the Spotify Web API, which controls whatever device is running Spotify (your phone, the
desktop app, a speaker); controlling playback needs Spotify Premium. One-time setup (README):
make an app at developer.spotify.com, put its client ID in .env as SPOTIFY_CLIENT_ID, then
    .venv/bin/python -m jarvis.tools.spotify login
and open the link it prints (on a phone is fine). The token is kept in data/spotify-token.json
and renews itself.
"""

import base64
import hashlib
import json
import logging
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from jarvis import config
from jarvis.tools import tool

API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com"
# Spotify only allows plain http for a loopback IP, not "localhost".
REDIRECT = "http://127.0.0.1:8767/callback"
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private"
TOKEN_FILE = config.ROOT / "data" / "spotify-token.json"

_lock = threading.Lock()
log = logging.getLogger("jarvis.spotify")


def _save(token):
    TOKEN_FILE.parent.mkdir(exist_ok=True)
    TOKEN_FILE.touch(mode=0o600)
    TOKEN_FILE.write_text(json.dumps(token))


def _token():
    """A valid access token, renewed when it is about to expire."""
    if not config.SPOTIFY_CLIENT_ID:
        raise RuntimeError("Spotify isn't set up yet (SPOTIFY_CLIENT_ID in .env, then "
                           "python -m jarvis.tools.spotify login)")
    with _lock:
        try:
            token = json.loads(TOKEN_FILE.read_text())
        except FileNotFoundError:
            raise RuntimeError("Spotify isn't signed in yet (python -m jarvis.tools.spotify login)")
        if token["expires_at"] - time.time() < 60:
            r = requests.post(f"{ACCOUNTS}/api/token", timeout=10, data={
                "grant_type": "refresh_token", "refresh_token": token["refresh_token"],
                "client_id": config.SPOTIFY_CLIENT_ID})
            r.raise_for_status()
            new = r.json()
            token.update(access_token=new["access_token"],
                         expires_at=time.time() + new["expires_in"],
                         refresh_token=new.get("refresh_token", token["refresh_token"]))
            _save(token)
        return token["access_token"]


def _call(method, path, **kwargs):
    r = requests.request(method, API + path, timeout=10,
                         headers={"Authorization": f"Bearer {_token()}"}, **kwargs)
    if r.status_code == 204 or not r.content:
        return None
    if r.status_code >= 400:
        try:
            reason = r.json()["error"]
            reason = reason.get("reason") or reason.get("message")
        except (ValueError, KeyError, AttributeError):
            reason = r.text[:200]
        raise RuntimeError({"PREMIUM_REQUIRED": "Controlling playback needs Spotify Premium",
                            "NO_ACTIVE_DEVICE": "No device is playing Spotify: open Spotify "
                            "somewhere (phone, laptop, speaker) first"}.get(reason, f"Spotify: {reason}"))
    return r.json()


def _describe(item):
    if not item:
        return None
    artists = ", ".join(a["name"] for a in item.get("artists", [])) or item.get("show", {}).get("name", "")
    return f"{item['name']}" + (f" by {artists}" if artists else "")


def _minutes(ms):
    return f"{ms // 60000}:{ms // 1000 % 60:02d}"


@tool("What Spotify is playing now, and on which device.")
def spotify_now_playing():
    state = _call("GET", "/me/player", params={"additional_types": "episode"})
    if not state:
        return "Nothing is playing on Spotify."
    item = state.get("item")
    return {"playing": state["is_playing"], "track": _describe(item),
            "album": (item or {}).get("album", {}).get("name"),
            "position": f"{_minutes(state.get('progress_ms') or 0)} of {_minutes((item or {}).get('duration_ms') or 0)}",
            "device": state["device"]["name"], "volume": state["device"].get("volume_percent"),
            "shuffle": state.get("shuffle_state")}


def _device(name):
    """The id of the device whose name contains name, or None for the active device. With no
    name, Jarvis's own speaker (spotifyd); if that isn't running, the active device, or with
    nothing active the first available one, so "play X" still works."""
    devices = (_call("GET", "/me/player/devices") or {}).get("devices", [])
    if name:
        for d in devices:
            if name.lower() in d["name"].lower():
                return d["id"]
        raise RuntimeError(f"No Spotify device called {name!r}. Devices: "
                           + (", ".join(d["name"] for d in devices) or "none open"))
    for d in devices:
        if d["name"].lower() == config.SPOTIFY_SPEAKER.lower():
            return None if d["is_active"] else d["id"]
    if any(d["is_active"] for d in devices):
        return None
    if devices:
        return devices[0]["id"]
    raise RuntimeError("No device is running Spotify: open it on your phone or laptop first")


@tool("Play something on Spotify: search for it and start the best match. An empty query "
      "resumes whatever was playing.",
      query={"type": "string", "description": "What to play, e.g. 'Mt. Joy', 'Bohemian Rhapsody', "
             "'my workout playlist'; empty to resume"},
      kind={"type": "string", "enum": ["track", "album", "artist", "playlist"],
            "description": "What the query names"},
      device={"type": "string", "description": "Device name to play on, only if the owner names "
              "one (e.g. 'my laptop'); empty plays out of Jarvis's own speaker"})
def spotify_play(query, kind, device):
    device_id = _device(device)
    params = {"device_id": device_id} if device_id else {}
    if not query.strip():
        _call("PUT", "/me/player/play", params=params)
        return "Resumed."
    found = _call("GET", "/search", params={"q": query, "type": kind, "limit": 1})
    items = [i for i in found[kind + "s"]["items"] if i]
    if not items:
        return f"Nothing on Spotify matches {query!r} as a {kind}."
    item = items[0]
    body = {"uris": [item["uri"]]} if kind == "track" else {"context_uri": item["uri"]}
    _call("PUT", "/me/player/play", params=params, json=body)
    what = _describe(item) if kind in ("track", "album") else item["name"]
    return f"Playing the {kind} {what}."


@tool("Control Spotify playback: pause, resume, skip to the next or previous track, set volume, "
      "or turn shuffle on or off.",
      action={"type": "string", "enum": ["pause", "resume", "next", "previous", "volume",
                                         "shuffle on", "shuffle off"]},
      volume_percent={"type": "integer", "minimum": 0, "maximum": 100,
                      "description": "For action volume; otherwise anything"})
def spotify_control(action, volume_percent):
    if action == "pause":
        _call("PUT", "/me/player/pause")
    elif action == "resume":
        _call("PUT", "/me/player/play")
    elif action in ("next", "previous"):
        _call("POST", f"/me/player/{action}")
    elif action == "volume":
        _call("PUT", "/me/player/volume", params={"volume_percent": volume_percent})
    else:
        _call("PUT", "/me/player/shuffle", params={"state": "true" if action == "shuffle on" else "false"})
    return "Done."


@tool("List the devices Spotify can play on (phone, laptop, speakers), or move playback to one. "
      f"The device called {config.SPOTIFY_SPEAKER!r} is Jarvis's own speaker ('here').",
      move_to={"type": "string", "description": "Device name to move playback to, or empty to just list"})
def spotify_devices(move_to):
    devices = (_call("GET", "/me/player/devices") or {}).get("devices", [])
    if move_to:
        device_id = _device(move_to)
        _call("PUT", "/me/player", json={"device_ids": [device_id], "play": True})
        return f"Moved playback to {next(d['name'] for d in devices if d['id'] == device_id)}."
    return [{"name": d["name"], "type": d["type"], "active": d["is_active"],
             "volume": d.get("volume_percent")} for d in devices] or "No devices have Spotify open."


DUCK_VOLUME = "20%"
_ducks = 0
_duck_lock = threading.Lock()


def duck(on):
    """Turn the music on Jarvis's speaker down while Jarvis listens and talks (on=True), and back
    up after (on=False). Calls nest: the music comes back up when the last one ends. Done on
    spotifyd's PipeWire stream, so it is instant and leaves Spotify's own volume alone."""
    global _ducks
    with _duck_lock:
        _ducks = _ducks + 1 if on else max(_ducks - 1, 0)
        if _ducks > (1 if on else 0):
            return
        try:
            streams = json.loads(subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"],
                                                capture_output=True, text=True, timeout=2).stdout)
            for s in streams:
                if s["properties"].get("application.process.binary") == "spotifyd":
                    subprocess.run(["pactl", "set-sink-input-volume", str(s["index"]),
                                    DUCK_VOLUME if on else "100%"], timeout=2)
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            log.warning("couldn't %s the music: %s", "lower" if on else "raise", e)


def login():
    """One-time sign-in (PKCE, so no client secret is needed). Works from another device: open
    the link there and paste back the address it ends up on."""
    if not config.SPOTIFY_CLIENT_ID:
        raise SystemExit("Put SPOTIFY_CLIENT_ID in .env first (see README: Spotify setup)")
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = f"{ACCOUNTS}/authorize?" + urlencode({
        "client_id": config.SPOTIFY_CLIENT_ID, "response_type": "code", "redirect_uri": REDIRECT,
        "code_challenge_method": "S256", "code_challenge": challenge, "scope": SCOPES, "state": state})
    print(f"Open this link and agree:\n\n{url}\n")
    print("Spotify then sends you to http://127.0.0.1:8767/callback?code=..., which won't load on "
          "another device. Copy that whole address from the address bar and paste it here.")
    answer = parse_qs(urlparse(input("Address: ").strip()).query)
    if answer.get("state", [""])[0] != state:
        raise SystemExit("That address is from a different sign-in attempt; run login again.")
    if "code" not in answer:
        raise SystemExit(f"Spotify said no: {answer.get('error', ['?'])[0]}")
    r = requests.post(f"{ACCOUNTS}/api/token", timeout=10, data={
        "grant_type": "authorization_code", "code": answer["code"][0], "redirect_uri": REDIRECT,
        "client_id": config.SPOTIFY_CLIENT_ID, "code_verifier": verifier})
    r.raise_for_status()
    token = r.json()
    _save({"access_token": token["access_token"], "refresh_token": token["refresh_token"],
           "expires_at": time.time() + token["expires_in"]})
    print(f"Signed in. Saved {TOKEN_FILE}. Now playing: {spotify_now_playing()}")


if __name__ == "__main__":
    if sys.argv[1:] == ["login"]:
        login()
    else:
        print(spotify_now_playing())
