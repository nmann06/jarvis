"""hyde's nightly restart: whether now's a good time, and putting the Jarvis windows back after.

    python3 deploy/nightly_reboot.py check [--dry-run]   0: restart now (windows saved), 3: not now
    python3 deploy/nightly_reboot.py reopen              at boot: reopen the saved windows

`check` runs as the owner from the root timer deploy/nightly-reboot.timer (every 15 minutes
from 3:00 to 4:45), which restarts the machine when it exits 0. It says no unless all of these hold:

- hyde has been up MIN_UPTIME_DAYS or more (so it's once in a while, not every night);
- every Jarvis window (~/jarvis-wrapper) says it's idle: no turn going, no /manager thread working
  or waiting on a dialog, nothing waiting out the usage limit (/keepitgoin). The windows say so in
  $XDG_RUNTIME_DIR/jarvis-windows/<pid>.json (jarvis_code/presence.py); a window without one, or
  one gone quiet, counts as busy, so windows opened before that existed hold the restart off;
- Jarvis itself isn't doing anything: nothing running in jarvis.service besides Jarvis (a reply
  being spoken, an alarm ringing, a project's `claude -p`), and no user service mid-run (job funnel);
- no alarm in the next ALARM_MARGIN minutes (its briefing is written before it) and no timer
  running out in the next TIMER_MARGIN (running timers are saved and come back by themselves).

Then it writes REOPEN: every tmux session's windows (folder, and the Jarvis window's conversation
in it) plus Jarvis windows opened outside tmux, which come back in tmux sessions of their own.
At boot, deploy/jarvis-reopen.service runs `reopen`: the same tmux sessions, each Jarvis window
reopened on its conversation with `jarvis -r <id>` (manager threads come back with it).
`tmux attach -t <name>` (or `tmux ls`) finds them.
"""

import argparse
import json
import os
import shlex
import socket
import subprocess
import sys
import time
from datetime import datetime

HOME = os.path.expanduser("~")
JARVIS = os.path.join(HOME, "jarvis")
WRAPPER = os.path.join(HOME, "jarvis-wrapper")
WINDOWS = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "jarvis-windows")
REOPEN = os.path.join(HOME, ".claude", "jarvis-reopen.json")

MIN_UPTIME_DAYS = 3
FRESH = 30          # seconds: a window's file older than this means the window has stopped writing it
ALARM_MARGIN = 60   # minutes
TIMER_MARGIN = 15   # minutes
REOPEN_MAX_AGE = 6 * 3600  # seconds: an older REOPEN is from a restart that didn't happen
NOT_NOW = 3


def log(text):
    print(text, flush=True)


# --- check -------------------------------------------------------------------------------------

def uptime_days():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0]) / 86400


def wrapper_pids():
    """The owner's running Jarvis windows: python ... jarvis-wrapper/launch.py."""
    pids = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            if os.stat(f"/proc/{name}").st_uid != os.getuid():
                continue
            with open(f"/proc/{name}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if any(a.endswith(b"launch.py") and b"jarvis-wrapper" in a for a in args):
            pids.append(int(name))
    return sorted(pids)


def windows():
    """[(pid, state or None)] for each running Jarvis window."""
    out = []
    for pid in wrapper_pids():
        try:
            with open(os.path.join(WINDOWS, f"{pid}.json")) as f:
                out.append((pid, json.load(f)))
        except (OSError, ValueError):
            out.append((pid, None))
    return out


def window_problem(pid, state, now):
    if state is None:
        return f"window {pid} doesn't say what it's doing (opened before presence.py? reopen it)"
    if now - state.get("updated", 0) > FRESH:
        return f"window {pid} hasn't reported for {now - state.get('updated', 0):.0f}s"
    if state.get("busy"):
        return f"window {pid} is busy"
    return None


def systemctl_user(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True).stdout


def jarvis_problems():
    problems = []
    main = systemctl_user("show", "jarvis", "-p", "MainPID", "--value").strip()
    group = systemctl_user("show", "jarvis", "-p", "ControlGroup", "--value").strip()
    try:
        with open(f"/sys/fs/cgroup{group}/cgroup.procs") as f:
            others = [p for p in f.read().split() if p != main]
    except OSError:
        others = []
    for pid in others:
        try:
            with open(f"/proc/{pid}/comm") as f:
                problems.append(f"Jarvis is running {f.read().strip()} (pid {pid})")
        except OSError:
            pass
    running = systemctl_user("list-units", "--type=service", "--state=activating", "--no-legend", "--plain")
    for line in running.splitlines():
        if line.split():
            problems.append(f"{line.split()[0]} is running")
    return problems


def alarm_problems(now):
    problems = []
    try:
        with open(os.path.join(JARVIS, "data", "alarms.json")) as f:
            alarms = json.load(f).get("alarms", [])
    except (OSError, ValueError):
        alarms = []
    for alarm in alarms:
        minutes = (datetime.fromisoformat(alarm["at"]).timestamp() - now) / 60
        if minutes < ALARM_MARGIN:
            problems.append(f"alarm at {alarm['at']} in {minutes:.0f} min")
    try:
        with open(os.path.join(JARVIS, "data", "timers.json")) as f:
            timers = json.load(f)
    except (OSError, ValueError):
        timers = []
    for timer in timers:
        minutes = (timer["ends"] - now) / 60
        if minutes < TIMER_MARGIN:
            problems.append(f"timer {timer.get('label') or ''!r} ends in {minutes:.0f} min")
    return problems


def tmux_panes():
    """{pane id: (session, window index, folder)} for every tmux pane, in order."""
    out = subprocess.run(["tmux", "list-panes", "-a", "-F",
                          "#{pane_id}\t#{session_name}\t#{window_index}\t#{pane_current_path}"],
                         capture_output=True, text=True).stdout
    panes = {}
    for line in out.splitlines():
        pane, session, window, path = line.split("\t", 3)
        panes[pane] = (session, int(window), path)
    return panes


def layout(states):
    """What REOPEN holds: [{"session": name, "windows": [{"cwd", "jarvis": None or session id}]}]."""
    jarvis_in = {}  # pane id -> the window's state
    outside = []
    panes = tmux_panes()
    for state in states:
        if state.get("tmux") in panes:
            jarvis_in[state["tmux"]] = state
        else:
            outside.append(state)
    sessions = {}
    for pane, (session, window, path) in panes.items():
        state = jarvis_in.get(pane)
        entry = {"cwd": state["cwd"] if state else path,
                 "jarvis": (state.get("session") or "") if state else None}
        windows = sessions.setdefault(session, {})
        windows.setdefault(window, entry)  # split panes come back as one window
    out = [{"session": name, "windows": [w for _, w in sorted(windows.items())]}
           for name, windows in sessions.items()]
    for n, state in enumerate(outside, 1):
        name = f"window-{n}"
        while name in sessions:
            name += "x"
        out.append({"session": name, "windows": [{"cwd": state["cwd"], "jarvis": state.get("session") or ""}]})
    return out


def check(dry_run):
    now, up = time.time(), uptime_days()
    if up < MIN_UPTIME_DAYS:
        if not dry_run:
            log(f"not now: up {up:.1f} days, under {MIN_UPTIME_DAYS}")
            return NOT_NOW
        log(f"(up {up:.1f} days: the timer waits for {MIN_UPTIME_DAYS}; the rest as if it were time)")
    found = windows()
    problems = [p for p in (window_problem(pid, state, now) for pid, state in found) if p]
    problems += jarvis_problems() + alarm_problems(now)
    if problems:
        log("not now: " + "; ".join(problems))
        return NOT_NOW
    plan = {"saved": now, "sessions": layout([state for _, state in found])}
    if dry_run:
        log("would restart now; reopening:\n" + json.dumps(plan["sessions"], indent=1))
        return 0
    os.makedirs(os.path.dirname(REOPEN), exist_ok=True)
    with open(REOPEN + ".tmp", "w") as f:
        json.dump(plan, f, indent=1)
    os.replace(REOPEN + ".tmp", REOPEN)
    log(f"restarting: {len(found)} Jarvis window(s) idle, {len(plan['sessions'])} tmux session(s) saved")
    return 0


# --- reopen ------------------------------------------------------------------------------------

def wait_for_network(limit=180):
    """The windows' Claude Code needs to reach the API as it starts."""
    deadline = time.time() + limit
    while time.time() < deadline:
        try:
            socket.getaddrinfo("api.anthropic.com", 443)
            return True
        except OSError:
            time.sleep(3)
    return False


def tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def command(window):
    if window["jarvis"] is None:
        return None
    resume = ["-r", window["jarvis"]] if window["jarvis"] else []
    return shlex.join([os.path.join(WRAPPER, "jarvis-code"), *resume])


def reopen():
    try:
        with open(REOPEN) as f:
            plan = json.load(f)
    except (OSError, ValueError):
        return 0
    os.replace(REOPEN, REOPEN + ".done")  # once only, even if this fails halfway
    if time.time() - plan.get("saved", 0) > REOPEN_MAX_AGE:
        log("saved windows are from a restart that didn't happen; leaving them")
        return 0
    if not wait_for_network():
        log("no network after 3 minutes; reopening the windows anyway")
    for entry in plan["sessions"]:
        name = entry["session"]
        if tmux("has-session", "-t", f"={name}").returncode == 0:
            log(f"tmux session {name} is already there")
            continue
        for i, window in enumerate(entry["windows"]):
            cwd = window["cwd"] if os.path.isdir(window["cwd"]) else HOME
            if i == 0:
                # In a scope of its own, so the tmux server outlives this oneshot service.
                made = subprocess.run(["systemd-run", "--user", "--scope", "--quiet", "--collect",
                                       "tmux", "new-session", "-d", "-P", "-F", "#{pane_id}",
                                       "-s", name, "-c", cwd], capture_output=True, text=True)
            else:
                made = tmux("new-window", "-P", "-F", "#{pane_id}", "-t", f"={name}:", "-c", cwd)
            pane = made.stdout.strip()
            if made.returncode or not pane:
                log(f"couldn't open a tmux window in {name}: {made.stderr.strip()}")
                continue
            run = command(window)
            if run:
                # Typed into the shell, as the owner would: closing the window leaves the shell.
                tmux("send-keys", "-t", pane, run, "Enter")
            log(f"reopened {name}: {cwd}" + (f" ({run})" if run else ""))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=["check", "reopen"])
    parser.add_argument("--dry-run", action="store_true",
                        help="check: say what it would do (ignoring uptime), save nothing")
    args = parser.parse_args()
    return check(args.dry_run) if args.action == "check" else reopen()


if __name__ == "__main__":
    sys.exit(main())
