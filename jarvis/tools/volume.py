"""The speaker's overall volume: Jarvis's voice and the music on its speaker go up and down
together. That is the default PipeWire sink (the echo-cancel sink). Jarvis's own stream sits
at 65% inside it so the music can be louder than the voice (README: Spotify setup); Spotify's
own volume (spotify_control) changes the music alone.
"""

import re
import subprocess

from jarvis.tools import tool

SINK = "@DEFAULT_SINK@"
STEP = 10  # percent per "volume up"
MAX = 100  # above 100% is digital boost, which distorts loud music


def _volume():
    out = subprocess.run(["pactl", "get-sink-volume", SINK], capture_output=True, text=True,
                         timeout=5, check=True).stdout
    return int(re.search(r"(\d+)%", out).group(1))


@tool("The speaker's overall volume, which moves Jarvis's voice and the music together: 'volume "
      "up', 'louder', 'turn it down', 'volume 50'. Anything that names the music or Spotify "
      "('music up', 'Spotify volume up', 'turn the music down') is the music alone: "
      "spotify_control.",
      action={"type": "string", "enum": ["up", "down", "set", "get"]},
      percent={"type": "integer", "minimum": 0, "maximum": MAX,
               "description": "For set; otherwise anything"})
def speaker_volume(action, percent):
    now = _volume()
    if action == "get":
        return f"Speaker volume {now}%."
    if action in ("up", "down"):
        percent = now + (STEP if action == "up" else -STEP)
    percent = max(0, min(MAX, percent))
    if action == "up" and now >= MAX:
        return f"Already at full volume ({now}%)."
    subprocess.run(["pactl", "set-sink-volume", SINK, f"{percent}%"], timeout=5, check=True)
    return f"Speaker volume {now}% -> {percent}%."
