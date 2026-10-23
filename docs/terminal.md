# Jarvis from a terminal

    jarvis                                     the Jarvis window (Claude Code, in blue)
    jarvis -c                                  the window, continuing the last conversation here
    jarvis what quizzes do I have next week    ask Jarvis something
    jarvis "what's on tomorrow?" -a

With nothing after it (or with the window's options: `-c`, `-r` to pick a conversation,
`--model`, `--plain`, `-p "first prompt"`, `--help`), `jarvis` opens the Jarvis window: Claude
Code with a screen of its own, answering as Jarvis. It opens in the current folder on hyde
(your home folder from Windows). See "The Jarvis window" in the README.

Anything else is a question: the reply is printed, and `-a` also has Jarvis say it out loud (on
hyde's speakers), as a short spoken reply. Questions go to the running Jarvis service
(deploy/ask.py over its local socket, see jarvis/alerts.py), so it has all its tools and
remembers your last few questions. Quote a question that has an apostrophe in it.

## On hyde

Already set up: `~/.bashrc` has the `jarvis` command (open a new terminal, or `source ~/.bashrc`).

## On the Windows laptop (PowerShell)

1. A key, so it doesn't ask for hyde's password every time. Skip `ssh-keygen` if you already
   have `~\.ssh\id_ed25519` (press Enter at each of its questions):

       ssh-keygen -t ed25519
       type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh nate@100.71.163.55 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"

   (that asks for hyde's password one last time).

2. The `jarvis` command, added to your PowerShell profile:

       Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
       New-Item -Force -ItemType Directory (Split-Path $PROFILE) | Out-Null
       ssh nate@100.71.163.55 cat ~/jarvis/deploy/jarvis.ps1 | Add-Content $PROFILE
       . $PROFILE

   Then `jarvis what's the weather` works in any new PowerShell window, and `jarvis` on its own
   opens the Jarvis window on hyde. It needs Tailscale on (hyde is 100.71.163.55 there).

   Set up before the window existed? Run the `ssh ... | Add-Content $PROFILE` line and
   `. $PROFILE` again: the new `jarvis` replaces the old one (`notepad $PROFILE` to delete the
   old copy, if you like).

The window wants a terminal with 24-bit colour and the mouse (Windows Terminal has both). Hold
Shift while dragging to select text the terminal's own way; a plain drag selects inside the
window, and ctrl+c copies it. Alt+Enter is Windows Terminal's full-screen key, so use `\`+Enter
or ctrl+j for a new line in a prompt.
