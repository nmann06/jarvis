#!/usr/bin/env python3
"""Claude Code hook: Jarvis says when a Claude session is done with a long task or needs you
to check something (a permission, a question, a plan).

Wired up in ~/.claude/settings.json for UserPromptSubmit, Stop, Notification and PreToolUse
(see README). Reads the hook's JSON on stdin and sends a line to Jarvis's alert socket
(jarvis/alerts.py). Standard library only, and it never fails or holds up Claude: if Jarvis
isn't running, nothing happens.

Quick turns stay quiet, since you're probably still watching: only something that comes more
than JARVIS_CLAUDE_MIN_SECONDS (default 30) after you last sent Claude a message is said.
"""

import json
import os
import re
import socket
import sys
import time
from pathlib import Path

RUNTIME = Path(os.getenv("XDG_RUNTIME_DIR") or "/tmp")
SOCKET = os.getenv("ALERT_SOCKET") or str(RUNTIME / "jarvis.sock")
STATE = RUNTIME / "jarvis-claude"  # when each session was last handed something to do
MIN_SECONDS = float(os.getenv("JARVIS_CLAUDE_MIN_SECONDS", "30"))


def project(cwd):
    """The project's name as said aloud: the repo, not the worktree inside .claude/worktrees."""
    path = cwd.split("/.claude/worktrees/")[0]
    return re.sub(r"[-_.]+", " ", os.path.basename(path.rstrip("/")) or "home")


def busy_for(session):
    """Seconds since this session was last given something to do, or None if unknown."""
    try:
        return time.time() - float((STATE / session).read_text())
    except (OSError, ValueError):
        return None


def mark(session):
    STATE.mkdir(mode=0o700, exist_ok=True)
    (STATE / session).write_text(str(time.time()))


def message(event):
    name, where = event.get("hook_event_name"), project(event.get("cwd", ""))
    if name == "Stop":
        return f"I'm finished in {where}."
    if name == "Notification":
        kind = event.get("notification_type")
        if kind == "permission_prompt":
            return f"I need your permission in {where}."
        if kind == "elicitation_dialog":
            return f"I need an answer from you in {where}."
        return None  # idle_prompt comes after Stop has spoken already; auth_success is noise
    if name == "PreToolUse":
        tool = event.get("tool_name")
        if tool == "AskUserQuestion":
            return f"I need an answer from you in {where}."
        if tool == "ExitPlanMode":
            return f"I need you to check a plan in {where}."
    return None


def send(text):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        s.connect(SOCKET)
        s.sendall((json.dumps({"text": text}) + "\n").encode())


def main():
    if os.getenv("JARVIS_PROJECT"):
        return  # a Jarvis project run (jarvis/project.py), which announces itself
    event = json.load(sys.stdin)
    session = re.sub(r"[^\w-]", "", event.get("session_id", "")) or "unknown"
    if event.get("hook_event_name") == "UserPromptSubmit":
        mark(session)
        return
    text = message(event)
    busy = busy_for(session)
    if event.get("hook_event_name") == "Stop":
        # Claude can carry on later by itself (a background task finishing): time that from now.
        mark(session)
    if text and busy is not None and busy >= MIN_SECONDS:
        send(text)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # Jarvis not running, odd input: never bother Claude with it
    sys.exit(0)
