"""Tools Claude can call. Adding one = a function decorated with @tool in a module listed below.

The decorator registers the function with its JSON schema; llm.py sends SCHEMAS to Claude and
calls run() with whatever Claude asks for.
"""

import contextvars
import json
import logging

log = logging.getLogger(__name__)

REGISTRY = {}  # name -> function
SCHEMAS = []  # tool definitions in the API's format
# Who is asking: "voice", "discord" or "claude" (another Claude session) while llm.Brain runs a
# turn; None for tools called directly through MCP. And how many turns there have been, so a
# tool can tell a later turn from this one (imessage: a draft is only sent after the owner's yes).
asker = contextvars.ContextVar("asker", default=None)
turn = 0


def tool(description, **properties):
    """Register a function as a tool. properties maps argument name -> JSON schema.

    Every argument is required; give the function a default and leave it out of properties
    if it shouldn't be.
    """
    def register(fn):
        REGISTRY[fn.__name__] = fn
        SCHEMAS.append({
            "name": fn.__name__,
            "description": description,
            "input_schema": {"type": "object", "properties": properties,
                             "required": list(properties), "additionalProperties": False},
        })
        return fn
    return register


def service(name):
    """The module a tool is in (calendar, Gmail, OnQ, ...). Calls to different ones can run
    at the same time."""
    fn = REGISTRY.get(name)
    return fn.__module__ if fn else name


def run(name, args):
    """Call a tool and return (result text, is_error). Errors go back to Claude, not up."""
    try:
        result = REGISTRY[name](**args)
        return (result if isinstance(result, str) else json.dumps(result)), False
    except Exception as e:
        log.exception("tool %s failed", name)
        return f"{type(e).__name__}: {e}", True


def warm_up():
    """Log in to slow services now, in the background, so the first request isn't slow."""
    import threading

    def connect():
        try:
            icloud_calendar.connect()
        except Exception as e:
            log.warning("calendar not connected yet: %s", e)
    threading.Thread(target=connect, name="warm-up", daemon=True).start()


# Import the tool modules so their @tool decorators run.
from jarvis.tools import (clock, gmail, icloud_calendar, notes, sheets, system,  # noqa: E402,F401
                          assessments, imessage, onedrive, onq, projects, spotify, timers,
                          kasa, trello, volume, weather)
