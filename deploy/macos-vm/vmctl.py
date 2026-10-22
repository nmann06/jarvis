#!/usr/bin/env python3
"""Drive the headless macOS VM through QEMU's QMP socket.

  vmctl.py shot [name]        screenshot -> ~/vm/shots/<name>.png (half size) and prints the path
  vmctl.py click X Y [right]  click at X,Y in screenshot (half-size) coordinates
  vmctl.py dclick X Y         double-click
  vmctl.py key K [K...]       QEMU key names, combos with '-': ret, tab, esc, spc, meta_l-q ...
  vmctl.py type TEXT          type text (US layout)
  vmctl.py hmp COMMAND...     any QEMU monitor command
"""
import json
import os
import socket
import sys
import time

from PIL import Image

VM = os.path.expanduser("~/vm/macos-monterey")
SHOTS = os.path.expanduser("~/vm/shots")
SCALE = 2  # screenshots are saved at half size; coordinates given in that space


class QMP:
    def __init__(self, path=os.path.join(VM, "qmp.sock")):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(path)
        self.f = self.s.makefile("rw")
        self.f.readline()  # greeting
        self.cmd("qmp_capabilities")

    def cmd(self, execute, **arguments):
        msg = {"execute": execute}
        if arguments:
            msg["arguments"] = arguments
        self.f.write(json.dumps(msg) + "\n")
        self.f.flush()
        while True:
            reply = json.loads(self.f.readline())
            if "return" in reply or "error" in reply:
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                return reply["return"]

    def hmp(self, line):
        return self.cmd("human-monitor-command", **{"command-line": line})

    def close(self):
        # QEMU serves one QMP client at a time: hang up as soon as we're done.
        self.f.close()
        self.s.close()


def screen_size(q):
    path = "/tmp/vmctl-size.ppm"
    q.hmp(f"screendump {path}")
    with Image.open(path) as im:
        return im.size


def shot(q, name="screen"):
    os.makedirs(SHOTS, exist_ok=True)
    raw = "/tmp/vmctl-shot.ppm"
    q.hmp(f"screendump {raw}")
    with Image.open(raw) as im:
        out = os.path.join(SHOTS, f"{name}.png")
        im.resize((im.width // SCALE, im.height // SCALE)).save(out)
        return out, im.size


def move(q, x, y):
    w, h = screen_size(q)
    ax, ay = int(x * SCALE * 32767 / w), int(y * SCALE * 32767 / h)
    q.cmd("input-send-event", events=[{"type": "abs", "data": {"axis": "x", "value": ax}},
                                      {"type": "abs", "data": {"axis": "y", "value": ay}}])


def click(q, x, y, button="left", times=1):
    move(q, x, y)
    time.sleep(0.15)
    for _ in range(times):
        for down in (True, False):
            q.cmd("input-send-event", events=[{"type": "btn", "data": {"down": down, "button": button}}])
            time.sleep(0.05)
        time.sleep(0.08)


SHIFTED = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9',
           ')': '0', '_': 'minus', '+': 'equal', '{': 'bracket_left', '}': 'bracket_right',
           '|': 'backslash', ':': 'semicolon', '"': 'apostrophe', '<': 'comma', '>': 'dot',
           '?': 'slash', '~': 'grave_accent'}
PLAIN = {' ': 'spc', '-': 'minus', '=': 'equal', '[': 'bracket_left', ']': 'bracket_right',
         '\\': 'backslash', ';': 'semicolon', "'": 'apostrophe', ',': 'comma', '.': 'dot',
         '/': 'slash', '`': 'grave_accent', '\n': 'ret', '\t': 'tab'}


def type_text(q, text):
    for ch in text:
        if ch.isalpha() and ch.isupper():
            key = f"shift-{ch.lower()}"
        elif ch.isalnum():
            key = ch.lower()
        elif ch in SHIFTED:
            key = f"shift-{SHIFTED[ch]}"
        elif ch in PLAIN:
            key = PLAIN[ch]
        else:
            raise ValueError(f"can't type {ch!r}")
        q.hmp(f"sendkey {key}")
        time.sleep(0.04)


def main(args):
    q = QMP()
    try:
        run(q, args)
    finally:
        q.close()


def run(q, args):
    what = args[0]
    if what == "shot":
        path, size = shot(q, args[1] if len(args) > 1 else "screen")
        print(path, f"(screen {size[0]}x{size[1]}, coordinates at 1/{SCALE})")
    elif what in ("click", "dclick"):
        click(q, float(args[1]), float(args[2]), args[3] if len(args) > 3 else "left",
              2 if what == "dclick" else 1)
    elif what == "key":
        for k in args[1:]:
            q.hmp(f"sendkey {k}")
            time.sleep(0.1)
    elif what == "type":
        type_text(q, " ".join(args[1:]))
    elif what == "hmp":
        print(q.hmp(" ".join(args[1:])))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
