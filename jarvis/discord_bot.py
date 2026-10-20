"""Discord front end: a bot that chats with the owner in direct messages.

Only DISCORD_OWNER_ID gets answers; messages from anyone else are ignored.
"""

import asyncio
import logging

import discord

from jarvis import config, llm, notify

log = logging.getLogger(__name__)

LIMIT = 2000  # Discord's maximum message length


def _chunks(text):
    while text:
        cut = text.rfind("\n", 0, LIMIT) if len(text) > LIMIT else len(text)
        cut = cut if cut > 0 else LIMIT
        yield text[:cut]
        text = text[cut:].lstrip("\n")


class JarvisBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # also enable "Message Content Intent" in the portal
        super().__init__(intents=intents)
        self.brain = llm.Brain("discord")
        self.owner = None

    async def on_ready(self):
        self.owner = await self.fetch_user(config.DISCORD_OWNER_ID)
        loop = asyncio.get_running_loop()
        # Alerts come from other threads (timers, the voice loop), so hop onto the bot's loop.
        notify.register("discord", lambda text: asyncio.run_coroutine_threadsafe(
            self.dm(text), loop).result(timeout=30))
        log.info("discord: logged in as %s, talking to %s", self.user, self.owner)

    async def dm(self, text):
        for part in _chunks(text):
            await self.owner.send(part)

    async def on_message(self, message):
        if message.author.id != config.DISCORD_OWNER_ID or message.guild is not None:
            return  # owner's DMs only
        text = message.content.strip()
        if not text:
            return
        log.info("discord you: %s", text)
        async with message.channel.typing():
            # Brain.ask blocks (network + tools), so run it off the event loop.
            reply = await asyncio.get_running_loop().run_in_executor(None, self.brain.ask, text)
        log.info("discord jarvis: %s", reply)
        for part in _chunks(reply):
            await message.channel.send(part)


def run():
    JarvisBot().run(config.DISCORD_TOKEN, log_handler=None)  # log through our own logging setup
