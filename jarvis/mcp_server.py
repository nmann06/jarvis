"""Jarvis as an MCP server, so Claude sessions on any device can talk to it.

Add https://<this laptop>.ts.net/mcp/<MCP_TOKEN> once as a custom connector on claude.ai and it
shows up in the Claude apps (web, desktop, phone) and in Claude Code wherever you're signed in.
Tailscale Funnel carries it from the internet to MCP_PORT on localhost (see README).

Tools offered:
- ask_jarvis: a message to Jarvis, answered by its own Claude with all its tools and a short
  memory of earlier messages from Claude sessions (separate from the voice and Discord ones).
- tell_owner: Jarvis says something out loud on the laptop, or posts it in #jarvis.
- Every Jarvis tool (calendar, email, notes, OnQ, ...), called directly with no extra API cost.

The secret in the URL is the only key, so treat that URL like a password: MCP_TOKEN in .env,
change it and update the connector if it leaks. Speaks MCP's Streamable HTTP transport
statelessly: each POST is one JSON-RPC message, answered with plain JSON.
"""

import asyncio
import hmac
import json
import logging
import threading

from aiohttp import web

from jarvis import alerts, config, llm, notify, tools

log = logging.getLogger(__name__)

VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")  # newest first
INSTRUCTIONS = """Jarvis is the owner's personal assistant, running on their laptop at home. \
Use ask_jarvis to talk to it in plain language (it remembers recent messages from Claude \
sessions), tell_owner to get something to the owner right away (spoken aloud at home or posted \
in their #jarvis Discord channel), or call its tools directly for calendar, email, notes, \
schoolwork (OnQ), the job-search sheet, Trello, weather and timers."""

OWN_TOOLS = [
    {"name": "ask_jarvis",
     "description": "Send Jarvis a message and get its reply. Jarvis answers with its own tools "
                    "(calendar, Gmail drafts, notes, OnQ, Trello, the job-search sheet, timers, "
                    "weather) and remembers the last few messages from Claude sessions.",
     "inputSchema": {"type": "object", "required": ["message"], "properties": {
         "message": {"type": "string", "description": "What to say or ask, in plain language"}}}},
    {"name": "tell_owner",
     "description": "Have Jarvis tell the owner something now: spoken aloud on the laptop at "
                    "home (aloud=true), or posted in their #jarvis Discord channel, which "
                    "reaches their phone (aloud=false). Keep spoken text to a sentence or two.",
     "inputSchema": {"type": "object", "required": ["text", "aloud"], "properties": {
         "text": {"type": "string", "description": "What to tell them"},
         "aloud": {"type": "boolean", "description": "true to speak it, false to post it"}}}},
]

_brain = None  # created on first use, so a server that's never asked costs nothing
_brain_lock = threading.Lock()  # Brain keeps one history, so one message at a time


def _ask(message):
    global _brain
    with _brain_lock:
        if _brain is None:
            _brain = llm.Brain("claude")
        log.info("claude session -> jarvis: %s", message)
        reply = _brain.ask(message)
        log.info("jarvis -> claude session: %s", reply)
        return reply


def _tell(text, aloud):
    if aloud:
        alerts.say(text)  # queued behind anything else being said
        return "Jarvis will say it now." if notify.available("voice") else \
            "Voice is off, so it was posted in #jarvis instead."
    if not notify.available("discord"):
        return "Discord isn't connected, so it couldn't be posted."
    notify.post(text)
    return "Posted in #jarvis."


def _call(name, args):
    """Run a tool; returns (text, is_error). Blocking, so it runs off the event loop."""
    if name == "ask_jarvis":
        return _ask(args["message"]), False
    if name == "tell_owner":
        return _tell(args["text"].strip(), bool(args.get("aloud"))), False
    if name not in tools.REGISTRY:
        return f"No tool named {name}", True
    # Alerts from tools like set_timer go to Discord: the owner may well not be at home.
    token = notify.channel.set("discord")
    try:
        log.info("claude session tool %s(%s)", name, args)
        return tools.run(name, args)
    finally:
        notify.channel.reset(token)


def _tool_list():
    return OWN_TOOLS + [{"name": s["name"], "description": s["description"],
                         "inputSchema": s["input_schema"]} for s in tools.SCHEMAS]


async def _handle(msg):
    """Answer one JSON-RPC message; None for a notification, which gets no reply."""
    method, params = msg.get("method"), msg.get("params") or {}
    if "id" not in msg:
        return None
    if method == "initialize":
        asked = params.get("protocolVersion")
        result = {"protocolVersion": asked if asked in VERSIONS else VERSIONS[0],
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "jarvis", "version": "1.0"},
                  "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": _tool_list()}
    elif method == "tools/call":
        text, is_error = await asyncio.get_running_loop().run_in_executor(
            None, _call, params.get("name"), params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    else:
        return {"jsonrpc": "2.0", "id": msg["id"],
                "error": {"code": -32601, "message": f"Unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": msg["id"], "result": result}


async def _endpoint(request):
    if not hmac.compare_digest(request.match_info["token"], config.MCP_TOKEN):
        raise web.HTTPNotFound()
    if request.method != "POST":
        raise web.HTTPMethodNotAllowed(request.method, ["POST"])  # no server-sent events
    try:
        msg = await request.json()
    except ValueError:
        return web.json_response({"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32700, "message": "Parse error"}}, status=400)
    if isinstance(msg, list):  # a batch (2025-03-26)
        replies = [r for r in await asyncio.gather(*map(_handle, msg)) if r]
    else:
        replies = await _handle(msg)
    return web.json_response(replies) if replies else web.Response(status=202)


def app():
    application = web.Application()
    application.router.add_route("*", "/mcp/{token}", _endpoint)
    return application


async def _run():
    runner = web.AppRunner(app(), access_log=None)
    await runner.setup()
    # Only Tailscale (on this machine) reaches it; it never listens on the network itself.
    await web.TCPSite(runner, "127.0.0.1", config.MCP_PORT).start()
    await asyncio.Event().wait()


def start():
    if not config.MCP_TOKEN:
        log.info("MCP_TOKEN not set, Claude sessions can't reach Jarvis")
        return
    threading.Thread(target=lambda: asyncio.run(_run()), name="mcp", daemon=True).start()
    log.info("mcp server on 127.0.0.1:%d", config.MCP_PORT)
