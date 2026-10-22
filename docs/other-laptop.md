# Jarvis and the Windows laptop: seeing it, editing on it, running work there

Research notes, 2026-10-21. Jarvis runs on **hyde** (this Linux laptop); the other laptop is
**nates-laptop**, Windows, on the same Tailscale network (100.122.158.102).

## What already works (built tonight)

- **Project mode on the Pro plan.** "Jarvis, new project" hands longer work to Claude Code
  (`claude -p`, logged in with Pro on hyde, never the API key). It can read OnQ, the calendar
  and notes, and read/make folders/save new files in OneDrive through Jarvis's MCP server.
  While a project is open, questions about the work go to it and anything else Jarvis answers.
- **OneDrive as the shared workspace** (after the one-time OneDrive login in the README).
  Windows syncs OneDrive, and Windows 11 backs up Desktop and Documents to it by default, so
  homework, lab files and code saved there are readable by Jarvis and by projects (Word, PDF,
  any text/code file, notebooks). Jarvis saves new files there; the laptop gets them within a
  minute. It never overwrites or deletes.

This covers "read what I'm working on and answer" for anything in OneDrive. What it can't do:
see the screen, touch files outside OneDrive, or run programs on the Windows laptop.

## The options for the rest

| Option | Sees the screen | Edits files | Runs things there | Install on Windows | Risk |
|---|---|---|---|---|---|
| A. OneDrive only (done) | no | OneDrive, new files only | no | nothing | low |
| B. Jarvis agent on Windows (recommended next) | yes (screenshots on request) | allowed folders | Claude Code on that laptop | Python + one script, starts at login | low-medium (token, Tailscale only, allow-listed folders) |
| C. OpenSSH server + Claude Code on Windows | **no** (see below) | yes | yes (`ssh nates-laptop claude -p ...`) | enable OpenSSH, add hyde's key | medium (a shell) |
| D. VNC / remote desktop | yes, and control | through the GUI | through the GUI | a VNC server | high (full control) |

**Why SSH alone can't see the screen:** Windows runs the OpenSSH server and every shell it starts
in "session 0", the services session, which has no access to your desktop: screenshot and
window APIs return nothing there. Tailscale's own SSH server doesn't run on Windows either. So
anything that looks at the screen has to be a program running inside your logged-in session.

**RDP** isn't an option on Windows Home (only Pro can host it), and Chrome Remote Desktop has no
API.

## Recommended: B, a small Jarvis agent on the Windows laptop

A Python script that starts when you log in (Task Scheduler, "At log on", hidden) and is an MCP
server like Jarvis's own, listening only on the laptop's Tailscale address with a long secret.
Tools it would offer:

- `screenshot` (whole screen or the active window), returned as an image so Claude can read it:
  "Jarvis, what's this error?", "what does this code on my screen do?", "is my circuit right?"
- `active_window`: the app and title (e.g. `Lab4.m - MATLAB`, `prelab.docx - Word`).
- `list_folder`, `read_file` (Word, PDF, code, notebooks), limited to folders you allow
  (OneDrive, Documents, a code folder).
- `save_file`: new files, or a new version next to the old one (`main_v2.py`); never overwrite.
- `open` a file or link on the laptop, so Jarvis can put something in front of you.
- Optional `run_claude`: runs Claude Code *on the Windows laptop* (Pro login there) in a project
  folder, for work that needs local apps or many files (MATLAB, LTspice, a compiler).

How Jarvis uses it:
- **Voice/Discord:** new Jarvis tools that call the agent (`laptop_screen`, `laptop_window`,
  `laptop_read`), so "what's on my screen?" works by voice.
- **Project mode:** the agent becomes a second MCP server for project runs, so a project can
  read and save code where you're working, or run there.

Safety: Tailscale-only, token in the URL (like Jarvis's MCP), folder allow-list, no general
shell, screenshots only when asked, a Windows notification each time one is taken, and a log.
Prompt injection (text on a web page or in a document telling Claude to do something) stays
limited to what those tools allow.

Effort: the agent is about 200 lines of Python plus a setup script; the Jarvis side is a few
tools and an image-capable tool result. I can build and test the server part on Linux; the
Windows-specific parts (window title, startup task, firewall rule) need one session at that
laptop.

## Also worth having: C, for running work on Windows

If a project needs Windows apps or files outside OneDrive, project mode can run Claude Code on
the Windows laptop instead of hyde: enable **Settings > System > Optional features > OpenSSH
Server**, add hyde's public key to `C:\Users\<you>\.ssh\authorized_keys` (or
`administrators_authorized_keys` for an admin account), install Claude Code there
(`irm https://claude.ai/install.ps1 | iex`, Git for Windows optional since v2.1.120) and log in
with Pro. Then a project runs `ssh nates-laptop claude -p ... --output-format stream-json` in,
say, `%OneDrive%\School\ELEC 324\Lab 4`, and its updates come back the same way. The laptop has
to be awake.

## Pro plan for long and costly work: notes

- Project mode calls the official `claude` CLI, logged in with your Pro plan, with the API key
  removed from its environment. Anthropic's February 2026 terms forbid using a subscription's
  login tokens outside its official apps (claude.ai, Claude Code); calling the official CLI on
  your own laptop for yourself is within them. A June 2026 plan to bill `claude -p` from a
  separate monthly credit was paused; it still draws on the normal plan limits. Worth checking
  Anthropic's help pages now and then, as this has been changing.
- It shares Pro's limits (5-hour windows) with your own Claude and Claude Code use. Projects run
  Sonnet by default (`PROJECT_MODEL`) to stretch them; `opus` uses them up much faster.
- `claude --bare` (recommended for scripts) can't use a subscription login, so project runs are
  normal runs: they load `~/.claude` settings and hooks. The "Claude has finished" hook now skips
  them (they announce themselves).
- Quick questions stay on the API (Haiku, about $0.0001 each): a Claude Code run takes several
  seconds just to start, so it's only worth it for real work.

## Sources

- Windows session 0 isolation and SSH: https://kb.firedaemon.com/support/solutions/articles/4000086228 ,
  https://cua.ai/docs/how-to-guides/driver/windows-ssh
- Claude Code on Windows (Git for Windows optional): https://code.claude.com/docs/en/setup
- Running Claude Code from scripts (`-p`, `--bare`, permission modes): https://code.claude.com/docs/en/headless
- Subscription use outside official tools (February 2026 terms):
  https://smartscope.blog/en/ai-development/claude-code-reasoning-subscription-system-integration/
- The paused June 2026 `claude -p` credit change: https://wmedia.es/en/tips/claude-code-agent-sdk-credit ,
  https://claudefa.st/blog/guide/development/agent-sdk-credit
