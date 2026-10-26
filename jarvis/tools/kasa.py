"""Kasa smart switches: turn them on and off, or say whether they're on.

Goes through TP-Link's cloud (the same one the Kasa app uses), not the LAN: the building's
Wi-Fi keeps devices from seeing each other, so hyde can't reach the switches directly. Each
command is a round trip to TP-Link, about a second. One-time setup (README):
    .venv/bin/python -m jarvis.tools.kasa login
asks for the Kasa account's email, password and, if the account has one, the emailed code,
then keeps only the tokens in data/kasa-token.json. They renew themselves; if they ever stop
renewing, KASA_USER/KASA_PASSWORD in .env let it log in again on its own.

The switches go by their names in the Kasa app; rename them there.
"""

import asyncio
import difflib
import getpass
import json
import logging
import re
import sys
import threading
import uuid

from tplinkcloud import TPLinkDeviceManager
from tplinkcloud.client import TPLinkApi
from tplinkcloud.exceptions import TPLinkTokenExpiredError

from jarvis import config
from jarvis.tools import tool

TOKEN_FILE = config.ROOT / "data" / "kasa-token.json"
NOT_LOGGED_IN = ("Not logged in to Kasa: run `.venv/bin/python -m jarvis.tools.kasa login` "
                 "on hyde.")
CODE_NEEDED = -20677

log = logging.getLogger(__name__)
_lock = threading.Lock()
_saved = None  # token, refresh token, regional server, terminal ID
_devices = None  # alias -> device, from the last listing


class KasaError(Exception):
    def __init__(self, code, message, result=None):
        self.code = code
        self.result = result or {}  # e.g. the code challenge: MFAProcessId, supportedMFATypes
        super().__init__(f"{message} ({code})")


def _call(api, host, path, body):
    """A TP-Link cloud account call; returns its result or raises its error. The cloud now
    reports some errors (wrong password, code needed) inside a result whose error_code is 0,
    which tplinkcloud 5.2.1 takes for success, so its login() can't be used."""
    response = api._request_post_v2(host, path, body)
    result = response.result or {}
    code = response.error_code or int(result.get("errorCode") or 0)
    if code:
        raise KasaError(code, response.msg or result.get("errorMsg") or "TP-Link cloud error",
                        result)
    return result


def _save(saved):
    TOKEN_FILE.parent.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(saved))
    TOKEN_FILE.chmod(0o600)


def _login(email, password, ask_code=None):
    """Log in as the Kasa app does. ask_code(email) -> the emailed code, if the account wants
    one; without it that's an error."""
    term = str(uuid.uuid4())
    api = TPLinkApi(term_id=term)
    host = api._get_regional_url(email)
    account = {"appType": api._app_type, "cloudUserName": email, "cloudPassword": password,
               "terminalUUID": term}
    try:
        result = _call(api, host, "/api/v2/account/login", {
            **account, "appVersion": api._app_ver, "platform": "Android",
            "refreshTokenNeeded": True, "supportBindAccount": False,
            "terminalName": "Jarvis", "terminalMeta": "Jarvis"})
    except KasaError as e:
        if e.code != CODE_NEEDED or ask_code is None:
            raise
        # The challenge's process ID goes back with the code, along with which kind of code
        # (mfaType: one of supportedMFATypes; the app emails it).
        challenge = {"MFAProcessId": e.result.get("MFAProcessId", "")}
        try:  # usually the login reply has sent the email already; this asks for it again
            _call(api, host, "/api/v2/account/getEmailVC4TerminalMFA", {**account, **challenge})
        except KasaError:
            pass
        code = ask_code(e.result.get("mfaEmail") or e.result.get("email") or email).strip()
        types = e.result.get("supportedMFATypes") or [1]
        for i, kind in enumerate(types):
            try:
                result = _call(api, host, "/api/v2/account/checkMFACodeAndLogin", {
                    **account, **challenge, "code": code, "mfaType": kind,
                    "terminalBindEnabled": True, "refreshTokenNeeded": True})
                break
            except KasaError as wrong:
                # -20105/-20106: TP-Link didn't take the request itself, not the code
                if wrong.code not in (-20105, -20106) or i == len(types) - 1:
                    raise KasaError(wrong.code, f"{wrong} - challenge was {e.result}")
    saved = {"token": result["token"], "refresh": result.get("refreshToken"), "host": host,
             "term": term}
    _save(saved)
    return saved


def _renew(saved):
    """A new token from the refresh token, or by logging in again if that has expired too."""
    try:
        api = TPLinkApi(saved["host"], term_id=saved["term"])
        result = _call(api, saved["host"], "/api/v2/account/refreshToken",
                       {"appType": api._app_type, "refreshToken": saved["refresh"],
                        "terminalUUID": saved["term"]})
        saved = {**saved, "token": result["token"],
                 "refresh": result.get("refreshToken") or saved["refresh"]}
        _save(saved)
        return saved
    except KasaError:
        if not (config.KASA_USER and config.KASA_PASSWORD):
            raise RuntimeError(NOT_LOGGED_IN)
        return _login(config.KASA_USER, config.KASA_PASSWORD)


def _load():
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    if config.KASA_USER and config.KASA_PASSWORD:
        return _login(config.KASA_USER, config.KASA_PASSWORD)
    raise RuntimeError(NOT_LOGGED_IN)


def _fetch(saved):
    manager = TPLinkDeviceManager(prefetch=False, include_tapo=False, term_id=saved["term"],
                                  tplink_cloud_api_host=saved["host"])
    manager.set_auth_token(saved["token"])
    return asyncio.run(manager.get_devices())


def _list():
    """alias -> device, fresh from the cloud. Renews the token if it has expired."""
    global _saved, _devices
    if _saved is None:
        _saved = _load()
    try:
        devices = _fetch(_saved)
    except TPLinkTokenExpiredError:
        _saved = _renew(_saved)
        devices = _fetch(_saved)
    _devices = {d.get_alias(): d for d in devices}
    return _devices


NUMBERS = {"one": "1", "won": "1", "two": "2", "to": "2", "too": "2", "three": "3", "four": "4",
           "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
           "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5"}


def _norm(name):
    """Words that tell switches apart: "the Light One" -> ["1"]."""
    name = re.sub(r"[^a-z0-9 ]", "", name.lower())
    words = re.sub(r"\b(the|my|switch|light|lights|number)\b", "", name).split()
    return [NUMBERS.get(w, w) for w in words]


ALL = {"all", "every", "everything", "each", "both"}


def _match(name, devices):
    """Devices a spoken name means: all of them, one by its name, or the closest name."""
    words = _norm(name)
    if ALL & set(words):
        # "all three" was Light 3 alone, by its number; "all of them" matched nothing
        words = [w for w in words if w not in ALL | {"of", "them", "those"} and not w.isdigit()]
    if not words:
        return list(devices.values())
    names = {" ".join(_norm(alias)): device for alias, device in devices.items()}
    wanted = " ".join(words)
    if wanted in names:
        return [names[wanted]]
    partial = [d for n, d in names.items() if wanted in n or (n and n in wanted)]
    if partial:
        return partial
    close = difflib.get_close_matches(wanted, names, n=1, cutoff=0.8)
    return [names[close[0]]] if close else []


async def _act(device, action):
    """Do it to one switch; returns its state afterwards (True = on), or None if unreachable."""
    if action == "on":
        if await device.power_on() is None:
            return None
    elif action == "off":
        if await device.power_off() is None:
            return None
    elif action == "toggle":
        on = await device.is_on()
        if on is None:
            return None
        if await (device.power_off() if on else device.power_on()) is None:
            return None
        return not on
    else:
        return await device.is_on()
    return action == "on"


async def _act_all(devices, action, gap=0.0):
    """Do it to each switch, starting each gap seconds after the one before."""
    async def one(i, device):
        await asyncio.sleep(i * gap)
        return await _act(device, action)
    return await asyncio.gather(*(one(i, d) for i, d in enumerate(devices)), return_exceptions=True)


def _act_retrying(chosen, action, gap=0.0):
    """_act_all, then once more for any that failed; call with _lock held."""
    results = asyncio.run(_act_all(chosen, action, gap))
    if any(r is None or isinstance(r, Exception) for r in results):
        # Usually an expired token: list again (which renews it) and retry those once.
        devices = _list()
        retry = [i for i, r in enumerate(results) if r is None or isinstance(r, Exception)]
        again = asyncio.run(_act_all([devices.get(chosen[i].get_alias(), chosen[i])
                                      for i in retry], action, gap))
        for i, r in zip(retry, again):
            results[i] = r
    return results


@tool("The Kasa smart switches (the lights): turn them on or off, toggle, or say which are "
      "on. 'Lights on', 'turn off the bedroom light', 'is the lamp on?'. name is a switch's "
      "name, or 'all' for every switch: whenever none is named, as in 'lights on'.",
      action={"type": "string", "enum": ["on", "off", "toggle", "status"]},
      name={"type": "string"})
def lights(action, name):
    with _lock:
        devices = _devices or _list()
        chosen = _match(name, devices)
        if not chosen:
            devices = _list()  # maybe added or renamed in the app since
            chosen = _match(name, devices)
        if not chosen:
            return f"No switch called {name!r}. Switches: {', '.join(devices) or 'none'}."
        results = _act_retrying(chosen, action)
    lines = []
    for device, state in zip(chosen, results):
        if state is None or isinstance(state, Exception):
            state = "unreachable (offline or unplugged?)"
        else:
            state = "on" if state else "off"
        lines.append(f"{device.get_alias()}: {state}")
    return "\n".join(lines)


def on_in_turn(gap):
    """Turn the switches on one after another, gap seconds apart, in name order (the
    homecoming sequence), leaving out any already on. Returns [(name, True if on now, None if
    unreachable)] for the ones it switched; raises if Kasa can't be reached at all."""
    with _lock:
        devices = _devices or _list()
        ordered = [devices[alias] for alias in sorted(devices)]
        states = _act_retrying(ordered, "status")
        off = [d for d, on in zip(ordered, states) if on is not True]  # unknown: try it anyway
        results = _act_retrying(off, "on", gap)
    return [(d.get_alias(), None if isinstance(r, Exception) else r) for d, r in zip(off, results)]


# Bare "lights on" / "turn off the lights", or just "lights" (a toggle): voice switches every
# light here, without Claude's two round trips. Like voice's repeat and time matchers, the whole
# utterance must match, so "turn off the bedroom light" or "are the lights on" still go to Claude.
_FILLER = r"(?:hey |ok |okay |um |uh |jarvis |please |can you |could you |would you |just |)*"
_LIGHTS = r"(?:all (?:of )?)?(?:the |my )?lights?"
_ALL_LIGHTS = re.compile(_FILLER + r"(?:"
                         r"(?:turn |switch |put )?" + _LIGHTS + r" (?P<after>on|off|out)|"
                         r"(?:turn|switch|put) (?P<before>on|off) " + _LIGHTS + r"|"
                         r"kill " + _LIGHTS + r"|"
                         r"(?P<toggle>(?:toggle |flip |switch )?" + _LIGHTS + r"))"
                         r"(?: for me)?(?: please| jarvis| sir| now| thanks)*")


def all_lights_request(text):
    """"on", "off" or "toggle" if text is a bare request to switch all the lights, else None."""
    words = " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower().replace("'", " ")).split())
    m = _ALL_LIGHTS.fullmatch(words)
    if not m:
        return None
    if m["toggle"]:
        return "toggle"
    return "on" if "on" in (m["after"], m["before"]) else "off"


def switch_all(action):
    """Turn every switch on or off, or toggle them: all off if any is on, else all on.
    Returns what to say: a short confirmation, or which switches didn't answer. Never raises."""
    try:
        with _lock:
            chosen = list((_devices or _list()).values())
            if action == "toggle":
                states = _act_retrying(chosen, "status")
                action = "off" if any(s is True for s in states) else "on"
            results = _act_retrying(chosen, action)
    except Exception as e:
        log.warning("couldn't reach the lights: %s", e)
        return "I couldn't reach the lights, I'm afraid."
    missed = [d.get_alias() for d, r in zip(chosen, results)
              if r is None or isinstance(r, Exception)]
    if not missed:
        return f"Lights {action}, sir."
    if len(missed) == len(chosen):
        return "The lights didn't answer, I'm afraid."
    names = " and ".join([", ".join(missed[:-1]), missed[-1]] if len(missed) > 1 else missed)
    return f"{names} didn't answer, I'm afraid; the rest are {action}."


def login():
    global _saved
    email = input("Kasa account email: ").strip()
    password = getpass.getpass("Kasa password: ")
    try:
        _saved = _login(email, password,
                        ask_code=lambda to: input(f"TP-Link emailed a code to {to}: "))
    except KasaError as e:
        sys.exit(f"Login failed: {e}")
    devices = _list()
    print(f"Logged in. Switches: {', '.join(devices) or 'none found'}")


if __name__ == "__main__":
    if sys.argv[1:] == ["login"]:
        login()
    else:
        for alias, device in _list().items():
            print(alias, device.device_info.device_model)
