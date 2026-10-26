"""Discord front end: a bot that chats with the owner.

It answers the owner's DMs, and in servers it answers the owner when @mentioned, when they reply
to one of its messages, or anywhere in a channel named DISCORD_CHANNEL. Only DISCORD_OWNER_ID gets
answers; messages from anyone else are ignored. Each project (jarvis.project) gets a thread in
that channel; what the owner writes there goes to the project.
"""

import asyncio
import logging

import discord

from jarvis import alarms, config, llm, notify, project
from jarvis.tools import dnd

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
        self.home = None  # the #jarvis channel, where alerts and records go

    async def on_ready(self):
        self.owner = await self.fetch_user(config.DISCORD_OWNER_ID)
        self.home = next((ch for g in self.guilds for ch in g.text_channels
                          if ch.name == config.DISCORD_CHANNEL), None)
        loop = asyncio.get_running_loop()
        # Alerts come from other threads (timers, the voice loop), so hop onto the bot's loop.
        notify.register("discord", lambda text: asyncio.run_coroutine_threadsafe(
            self.alert(text), loop).result(timeout=30))
        project.connect_discord(
            lambda title: asyncio.run_coroutine_threadsafe(
                self.open_thread(title), loop).result(timeout=30),
            lambda thread_id, text: asyncio.run_coroutine_threadsafe(
                self.post_in(thread_id, text), loop).result(timeout=30))
        log.info("discord: logged in as %s, talking to %s; alerts go to %s", self.user,
                 self.owner, f"#{self.home} in {self.home.guild}" if self.home else "DMs")

    async def alert(self, text):
        """Something Jarvis says unprompted: posted in #jarvis, with an @mention if
        DISCORD_MENTION is on (that notifies even when the server is muted). Falls back to a DM
        if that channel doesn't exist."""
        if self.home is None:
            for part in _chunks(text):
                await self.owner.send(part)
            return
        if config.DISCORD_MENTION:
            text = f"{self.owner.mention} {text}"
        for part in _chunks(text):
            await self.home.send(part, allowed_mentions=discord.AllowedMentions(users=[self.owner]))

    async def open_thread(self, title):
        """A new thread in #jarvis for a project; its id, or None without that channel."""
        if self.home is None:
            return None
        thread = await self.home.create_thread(
            name=title[:100], type=discord.ChannelType.public_thread,
            auto_archive_duration=10080)  # archived after a week of quiet
        await thread.add_user(self.owner)
        return thread.id

    async def post_in(self, channel_id, text):
        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        for part in _chunks(text):
            await channel.send(part)

    def _addressed(self, message):
        if message.guild is None:
            return True  # a DM
        replied_to = message.reference and message.reference.resolved
        return (self.user in message.mentions
                or getattr(message.channel, "name", None) == config.DISCORD_CHANNEL
                or (isinstance(replied_to, discord.Message) and replied_to.author == self.user))

    async def on_message(self, message):
        if message.author.id != config.DISCORD_OWNER_ID:
            return
        p = project.by_thread(message.channel.id)
        if p is None and not self._addressed(message):
            return
        text = message.content.replace(self.user.mention, "").replace(f"<@!{self.user.id}>", "").strip()
        if not text:
            return
        if p is not None:
            log.info("discord you (project %s): %s", p.slug, text)
            queued = await asyncio.get_running_loop().run_in_executor(None, p.send, text)
            # ⏳: waits for the run in progress; 📨: sent now
            await message.add_reaction("⏳" if queued else "📨")
            return
        log.info("discord you: %s", text)
        loop = asyncio.get_running_loop()
        if (on := dnd.request(text)) is not None:  # without Claude, as by voice
            reply = await loop.run_in_executor(None, dnd.set_dnd, on)
            log.info("discord jarvis (do not disturb, local): %s", reply)
            await message.channel.send(reply)
            return
        # Alarms are handled without Claude (jarvis.alarms), as by voice.
        if (reply := await loop.run_in_executor(None, alarms.local_reply, text, "discord")):
            log.info("discord jarvis (alarm, local): %s", reply)
            await message.channel.send(reply)
            await loop.run_in_executor(None, alarms.after_reply)
            return
        async with message.channel.typing():
            # Brain.ask blocks (network + tools), so run it off the event loop.
            reply = await asyncio.get_running_loop().run_in_executor(None, self.brain.ask, text)
        log.info("discord jarvis: %s", reply)
        for part in _chunks(reply):
            await message.channel.send(part)


def run():
    JarvisBot().run(config.DISCORD_TOKEN, log_handler=None)  # log through our own logging setup
