#!/usr/bin/env python3
"""Ask the running Jarvis something from a terminal; the reply is printed. -a also speaks it.

    jarvis what quizzes do I have next week
    jarvis "what's on tomorrow?" -a
    echo "what's the weather" | jarvis -        (the question on stdin, as the Windows laptop sends it)

Talks to Jarvis's local socket (jarvis/alerts.py), so it uses the service's tools and
remembers the last few questions. Only the standard library, so it starts quickly.
"""

import json
import os
import socket
import sys

SOCKET = os.getenv("ALERT_SOCKET") or os.path.join(
    os.getenv("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "jarvis.sock")
TIMEOUT = 180  # seconds: a question that reads syllabi or email can take a while


def main(args):
    aloud = "-a" in args
    words = [a for a in args if a != "-a"]
    question = sys.stdin.read() if words == ["-"] else " ".join(words)
    if not question.strip():
        sys.exit("usage: jarvis your question [-a]   (-a: Jarvis says the reply out loud too)")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(TIMEOUT)
            s.connect(SOCKET)
            s.sendall((json.dumps({"ask": question.strip(), "aloud": aloud}) + "\n").encode())
            line = s.makefile(encoding="utf-8").readline()
    except (FileNotFoundError, ConnectionRefusedError):
        sys.exit("Jarvis isn't running (systemctl --user status jarvis).")
    except socket.timeout:
        sys.exit("Jarvis took too long to answer.")
    if not line:
        sys.exit("Jarvis closed the connection without answering (see journalctl --user -u jarvis).")
    print(json.loads(line)["reply"])


if __name__ == "__main__":
    main(sys.argv[1:])
