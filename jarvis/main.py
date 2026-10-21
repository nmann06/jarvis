"""Start Jarvis: the voice loop and/or the Discord bot, sharing the same tools.

Run: python -m jarvis.main   (Ctrl+C to stop). This is what the systemd service runs.
"""

import logging
import os
import threading

from jarvis import config

log = logging.getLogger("jarvis")


def _voice():
    from jarvis import voice
    try:
        voice.run()
    except Exception:
        log.exception("voice loop crashed")
    os._exit(1)  # take the whole process down so systemd restarts it


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "faster_whisper", "discord"):  # routine chatter
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("discord.client").setLevel(logging.ERROR)  # "PyNaCl not installed": voice chat, unused

    if not config.VOICE_ENABLED and not config.DISCORD_TOKEN:
        raise SystemExit("Nothing to run: voice is off and DISCORD_TOKEN is not set.")
    if not config.VOICE_ENABLED:
        from jarvis import tools
        tools.warm_up()  # the voice loop does this itself when it runs
    from jarvis import alerts
    alerts.start()  # e.g. "Claude has finished": spoken, or posted if voice is off
    if config.VOICE_ENABLED:
        threading.Thread(target=_voice, name="voice", daemon=True).start()
    if config.DISCORD_TOKEN and not config.DISCORD_OWNER_ID:
        raise SystemExit("DISCORD_TOKEN is set but DISCORD_OWNER_ID isn't")
    if config.DISCORD_TOKEN:
        from jarvis import discord_bot, proactive
        proactive.start()  # briefing, reminders and email alerts, once the bot is connected
        discord_bot.run()  # blocks
        os._exit(1)
    else:
        log.info("DISCORD_TOKEN not set, voice only")
        threading.Event().wait()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
