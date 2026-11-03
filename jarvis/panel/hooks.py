"""The panel's side of the voice hooks: talks to the Unix socket jarvis.events serves inside
Jarvis. A Jarvis without the hooks (it predates them, or it's down) just isn't there, and
everything here says so instead of failing.
"""

import asyncio
import json
import logging
import time

from jarvis import events

log = logging.getLogger("jarvis.panel")

RETRY_SECONDS = 2.0
READ_TIMEOUT = 15.0  # jarvis.events pings every 5 s: silence this long means it's hung
ASK_TIMEOUT = 3.0
CLIENT_QUEUE = 128
LINE_LIMIT = 1 << 20


class Hub:
    """Keeps one subscription to the hooks and passes what comes to every /api/events client."""

    def __init__(self):
        self.live, self.state, self.muted, self.started = False, "unknown", False, None
        self.threshold = None
        self._clients = set()

    def subscribe(self):
        client = asyncio.Queue(maxsize=CLIENT_QUEUE)
        self._clients.add(client)
        return client

    def unsubscribe(self, client):
        self._clients.discard(client)

    def close(self):
        """Ends every stream: each client gets None."""
        for client in self._clients:
            self._put(client, None)

    @staticmethod
    def _put(client, message):
        if client.full():  # a slow browser loses its oldest message
            client.get_nowait()
        client.put_nowait(message)

    def _emit(self, message):
        for client in self._clients:
            self._put(client, message)

    def _set_live(self, live, t):
        if live != self.live:
            self.live = live
            self._emit({"type": "hooks", "live": live, "t": t})
        if not live and self.state != "unknown":
            self.state = "unknown"
            self._emit({"type": "state", "state": "unknown", "t": t})

    def _take(self, message):
        kind, t = message.get("type"), message.get("t", 0.0)
        if kind == "status":
            self.muted, self.started = message["muted"], message["started"]
            self.threshold = message["threshold"]
            self._set_live(True, t)
            self.state = message["state"]
            self._emit({"type": "state", "state": self.state, "t": t})
        elif kind == "state":
            self.state = message["state"]
            self._emit(message)
        elif kind == "muted":
            self.muted = message["muted"]
        elif kind == "level":
            self._emit(message)

    async def run(self):
        """Stay subscribed for as long as the panel runs."""
        while True:
            writer = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(str(events.SOCKET), limit=LINE_LIMIT), ASK_TIMEOUT)
                writer.write(b'{"cmd": "subscribe"}\n')
                await writer.drain()
                while line := await asyncio.wait_for(reader.readline(), READ_TIMEOUT):
                    self._take(json.loads(line))
            except (OSError, ValueError, KeyError, asyncio.TimeoutError) as e:
                log.debug("voice hooks not there: %s", e)
            finally:
                if writer is not None:
                    writer.close()
            self._set_live(False, time.time())
            await asyncio.sleep(RETRY_SECONDS)


async def _ask(request):
    """(reader, writer) with request sent, or raises OSError or TimeoutError."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(str(events.SOCKET), limit=LINE_LIMIT), ASK_TIMEOUT)
    writer.write(json.dumps(request).encode() + b"\n")
    await writer.drain()
    return reader, writer


async def snapshot():
    """(context dict, little-endian int16 mono PCM bytes) from the hooks, or None if not live."""
    writer = None
    try:
        reader, writer = await _ask({"cmd": "snapshot"})
        header = json.loads(await asyncio.wait_for(reader.readline(), ASK_TIMEOUT))
        pcm = await asyncio.wait_for(reader.readexactly(header["samples"] * 2), ASK_TIMEOUT)
        return header["context"], pcm
    except (OSError, ValueError, KeyError, asyncio.TimeoutError, asyncio.IncompleteReadError) as e:
        log.info("no snapshot from the voice hooks: %s", e)
        return None
    finally:
        if writer is not None:
            writer.close()


async def mute(on):
    """Mute or unmute: the mute state now, or None if the hooks aren't live."""
    writer = None
    try:
        reader, writer = await _ask({"cmd": "mute", "muted": bool(on)})
        return json.loads(await asyncio.wait_for(reader.readline(), ASK_TIMEOUT))["muted"]
    except (OSError, ValueError, KeyError, asyncio.TimeoutError) as e:
        log.info("couldn't mute through the voice hooks: %s", e)
        return None
    finally:
        if writer is not None:
            writer.close()
