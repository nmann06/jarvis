"""Send the owner a test alert through the bot, the way Jarvis's alerts are sent: an @mention in
the DISCORD_CHANNEL channel (or a DM if there isn't one).

Usage: python -m tests.discord_ping ["message"]   (DISCORD_MENTION=off to send without the @mention)
Uses Discord's HTTP API only, so it works while the Jarvis service is running.
"""

import asyncio
import sys

import discord

from jarvis import config


async def main(text):
    client = discord.Client(intents=discord.Intents.none())
    await client.login(config.DISCORD_TOKEN)  # HTTP login, no gateway connection
    try:
        owner = await client.fetch_user(config.DISCORD_OWNER_ID)
        channel = None
        async for guild in client.fetch_guilds():
            for ch in await (await client.fetch_guild(guild.id)).fetch_channels():
                if isinstance(ch, discord.TextChannel) and ch.name == config.DISCORD_CHANNEL:
                    channel = ch
        if channel:
            await channel.send(f"{owner.mention} {text}" if config.DISCORD_MENTION else text,
                               allowed_mentions=discord.AllowedMentions(users=[owner]))
            print(f"sent to #{channel} in {channel.guild}"
                  f"{f', mentioning {owner}' if config.DISCORD_MENTION else ', no mention'}: {text}")
        else:
            await owner.send(text)
            print(f"no #{config.DISCORD_CHANNEL} channel; sent a DM to {owner}: {text}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "🔔 Test notification from Jarvis."))
