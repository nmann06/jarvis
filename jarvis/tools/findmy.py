"""The owner's iPhone battery, from Find My on the Mac VM.

The Mac VM is signed in to the owner's Apple ID (for Messages), and on Monterey the Find My
app keeps what it shows in a plain JSON cache, Devices.data, refreshed about once a minute
while the app is open (in the background is fine). Each device there has batteryLevel (0-1)
and batteryStatus (Charging, NotCharging, Charged, Unknown). The owner's own devices have
prsId "owner"; the rest are family members' (Family Sharing).

If the cache is more than STALE old (the app was quit, or the VM restarted), the app is opened
again, hidden. The phone is the owner's iPhone, or the device named FINDMY_PHONE in .env.

Run: python -m jarvis.tools.findmy  to print the battery of every device it can see.
"""

import json
import logging
import time

from jarvis import config
from jarvis.tools import imessage, tool

log = logging.getLogger(__name__)

CACHE = "~/Library/Caches/com.apple.findmy.fmipcore/Devices.data"
STALE = 15 * 60  # seconds: an older cache means Find My isn't running, so it's opened again
# The cache's age (seconds) on the first line, then the cache itself.
_READ = f'echo $(( $(date +%s) - $(stat -f %m {CACHE}) )); cat {CACHE}'


def devices(refresh=True):
    """(every device in Find My, the cache's age in seconds). Opens Find My first if its cache
    is missing or stale (refresh), then reads it again once it has had time to fill."""
    try:
        age, data = _read()
    except (RuntimeError, ValueError):
        age, data = None, None
    if refresh and (age is None or age > STALE):
        log.info("Find My cache %s: opening Find My on the Mac VM",
                 "missing" if age is None else f"{age // 60} min old")
        imessage._ssh("open -g -a FindMy")
        for _ in range(6):
            time.sleep(10)
            try:
                age, data = _read()
            except (RuntimeError, ValueError):
                continue
            if age <= STALE:
                break
    if data is None:
        raise RuntimeError("Find My on the Mac VM has no device list (is it signed in to iCloud?)")
    return data, age


def _read():
    out = imessage._ssh(_READ)
    first, _, rest = out.partition("\n")
    return int(first), json.loads(rest)


def phone(found):
    """The owner's iPhone among found: FINDMY_PHONE if set, else their own iPhone that last
    reported a battery level."""
    if config.FINDMY_PHONE:
        wanted = config.FINDMY_PHONE.strip().lower()
        return next((d for d in found if d.get("name", "").strip().lower() == wanted), None)
    mine = [d for d in found if d.get("prsId") == "owner" and d.get("deviceClass") == "iPhone"]
    known = [d for d in mine if d.get("batteryStatus") != "Unknown"]
    return (known or mine or [None])[0]


def battery():
    """{"name", "percent", "charging", "low_power_mode", "age"} for the owner's phone, or None
    if Find My doesn't list it. age: seconds since Find My last heard from the phone."""
    found, cache_age = devices()
    p = phone(found)
    if p is None or p.get("batteryStatus") == "Unknown":
        return None
    seen = (p.get("location") or {}).get("timeStamp")
    return {"name": p.get("name"), "percent": round(100 * (p.get("batteryLevel") or 0)),
            "charging": p.get("batteryStatus") in ("Charging", "Charged"),
            "low_power_mode": bool(p.get("lowPowerMode")),
            "age": round(time.time() - seen / 1000) if seen else cache_age}


@tool("The owner's iPhone battery level and whether it's charging, from Find My.")
def phone_battery():
    b = battery()
    if b is None:
        return "Find My isn't showing the phone's battery right now."
    minutes = b["age"] // 60
    return (f"{b['percent']}%, {'charging' if b['charging'] else 'not charging'}"
            + (", low power mode on" if b["low_power_mode"] else "")
            + (f" (as of {minutes} minutes ago)" if minutes >= 5 else ""))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    found, age = devices()
    print(f"Find My cache: {age} s old")
    for d in found:
        if d.get("batteryStatus") != "Unknown":
            print(f"{d.get('name')} ({d.get('deviceClass')}, {'own' if d.get('prsId') == 'owner' else 'family'}): "
                  f"{round(100 * (d.get('batteryLevel') or 0))}% {d.get('batteryStatus')}")
    print("phone:", phone_battery())
