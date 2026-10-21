"""Send the owner a DM through the bot, to check notifications arrive.

Usage: python -m tests.discord_ping ["message"]
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
        await owner.send(text)
        print(f"sent to {owner}: {text}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "🔔 Test notification from Jarvis."))
