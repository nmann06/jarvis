from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.tools import tool

TZ = ZoneInfo("America/Toronto")


@tool("Current local date and time (America/Toronto).")
def get_time_and_date():
    return datetime.now(TZ).strftime("%A %d %B %Y, %I:%M %p")
