# Asking Jarvis from a terminal

    jarvis what quizzes do I have next week
    jarvis "what's on tomorrow?" -a

The reply is printed; `-a` also has Jarvis say it out loud (on hyde's speakers), as a short
spoken reply. The questions go to the running Jarvis service (deploy/ask.py over its local
socket, see jarvis/alerts.py), so it has all its tools and remembers your last few questions.
Quote a question that has an apostrophe in it.

## On hyde

Already set up: `~/.bashrc` has the `jarvis` command (open a new terminal, or `source ~/.bashrc`).

## On the Windows laptop (PowerShell)

1. A key, so it doesn't ask for hyde's password every time. Skip `ssh-keygen` if you already
   have `~\.ssh\id_ed25519` (press Enter at each of its questions):

       ssh-keygen -t ed25519
       type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh nate@100.71.163.55 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"

   (that asks for hyde's password one last time).

2. The `jarvis` command, added to your PowerShell profile:

       Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
       if (!(Test-Path $PROFILE)) { New-Item -Force $PROFILE }
       ssh nate@100.71.163.55 cat ~/jarvis/deploy/jarvis.ps1 | Add-Content $PROFILE
       . $PROFILE

   Then `jarvis what's the weather` works in any new PowerShell window. It needs Tailscale on
   (hyde is 100.71.163.55 there).
