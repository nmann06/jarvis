"""Routes messages Jarvis starts on its own (timer alerts, reminders) to voice or Discord."""

import contextvars
import logging

log = logging.getLogger(__name__)

# The channel of the conversation currently being handled; llm.Brain sets it during ask(),
# so a tool like set_timer knows where to send its alert later.
channel = contextvars.ContextVar("channel", default="voice")

_sinks = {}  # channel name -> function(text)


def register(name, send):
    _sinks[name] = send


def send(name, text):
    """Deliver text on channel name, or on any available channel if that one isn't running."""
    sink = _sinks.get(name) or next(iter(_sinks.values()), None)
    if sink is None:
        log.warning("no channel to deliver: %s", text)
        return
    log.info("alert via %s: %s", name if name in _sinks else "fallback", text)
    try:
        sink(text)
    except Exception:
        log.exception("failed to deliver on %s: %s", name, text)


def record(text):
    """A written heads-up of something Jarvis did (e.g. added an event): Discord only, never
    spoken, since the spoken reply already covers it."""
    sink = _sinks.get("discord")
    log.info("record%s: %s", "" if sink else " (discord not running)", text)
    if sink:
        try:
            sink(text)
        except Exception:
            log.exception("failed to send record: %s", text)
