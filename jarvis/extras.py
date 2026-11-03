"""Hooks for private/, a gitignored package of extras that aren't part of this repository (its
own README.md says what's in it). Each hook calls the function of the same name in
private/__init__.py, if there is one. Without private/, or without that function, a hook does
nothing, so Jarvis runs as published.

A hook that fails is logged and skipped, so a broken extra never stops Jarvis; only load_config()
raises, as jarvis.config does on a bad setting. Imports nothing else of Jarvis, so
jarvis.settings can use it without jarvis.config.
"""

import logging

log = logging.getLogger(__name__)

try:
    import private
except ModuleNotFoundError as e:
    if e.name != "private":
        raise
    private = None


def _call(name, default, *args):
    hook = getattr(private, name, None)
    if hook is None:
        return default
    try:
        return hook(*args)
    except Exception:
        log.exception("private %s() failed", name)
        return default


def load_config():
    """Load the extras' settings from .env, raising on a bad one (jarvis.settings.check_env)."""
    if hook := getattr(private, "load_config", None):
        hook()


def load_tools():
    """Import the extras' tool modules, so their @tool decorators run (jarvis.tools)."""
    _call("load_tools", None)


def listeners():
    """Objects voice.run() feeds every mic block alongside the wake word: heard(block) True
    when it has heard its sound, reset(), fired, name, and handle(mic), which acts on it
    (returning voice.BARGED_IN if "Hey Jarvis" stopped it)."""
    return list(_call("listeners", []))


def fix_transcript(text):
    """What was heard, with the extras' corrections (names speech-to-text spells as they sound)."""
    return _call("fix_transcript", text, text)


def contact_name(name):
    """The contact a name, as heard, means, or None (no such contact, several, or no contacts)."""
    return _call("contact_name", None, name)


def checks():
    """[(name, check(state, now), seconds between runs)] for the proactive loop."""
    return list(_call("checks", []))


def intake_sources():
    """[(heading, source(state, now) -> (entries, state to remember))] for intake."""
    return list(_call("intake_sources", []))


def more_settings(make):
    """The extras' settings for the settings page: {"categories": [...], "settings": [made with
    make, which is jarvis.settings.S], "ranges": {key: (low, high)}, "check": check(setting,
    value) -> value as written, None if it isn't one of theirs, or raises ValueError}."""
    return _call("more_settings", None, make) or {}


def more_integrations():
    """The extras' entries for the Setup page, as jarvis.integrations.INTEGRATIONS, each with
    a "test" function (values -> message, or raises integrations.Problem)."""
    return list(_call("more_integrations", []))
