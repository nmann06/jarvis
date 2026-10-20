"""Tools Claude can call. Adding one = a function decorated with @tool in a module listed below.

The decorator registers the function with its JSON schema; llm.py sends SCHEMAS to Claude and
calls run() with whatever Claude asks for.
"""

import json
import logging

log = logging.getLogger(__name__)

REGISTRY = {}  # name -> function
SCHEMAS = []  # tool definitions in the API's format


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


def run(name, args):
    """Call a tool and return (result text, is_error). Errors go back to Claude, not up."""
    try:
        result = REGISTRY[name](**args)
        return (result if isinstance(result, str) else json.dumps(result)), False
    except Exception as e:
        log.exception("tool %s failed", name)
        return f"{type(e).__name__}: {e}", True


# Import the tool modules so their @tool decorators run.
from jarvis.tools import clock, notes, system, timers, weather  # noqa: E402,F401
