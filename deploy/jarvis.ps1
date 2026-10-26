
# Jarvis from this laptop:
#     jarvis                                   the Jarvis window: Claude Code on this laptop, in blue
#     jarvis -c                                the window, continuing the last conversation (-r: pick one)
#     jarvis --hyde                            the window on hyde instead, over ssh (hyde's files)
#     jarvis --update                          install or update the window here, copied from hyde
#     jarvis what quizzes do I have next week  ask Jarvis something
#     jarvis "what's on tomorrow?" -a          (-a: Jarvis also says it out loud, on hyde)
# Quote the question if it has an apostrophe. Set up: docs/terminal.md in ~/jarvis on hyde.
$JarvisHyde = 'nate@100.71.163.55'
$JarvisWindow = Join-Path $env:LOCALAPPDATA 'Jarvis'

function jarvis {
    $window = '-c', '--continue', '-r', '--resume', '-p', '--prompt', '--model', '--effort', '--mode', '--plain',
              '--thinking', '--log', '-h', '--help'
    if ($args.Count -and $args[0] -eq '--update') { Update-JarvisWindow; return }
    if ($args.Count -and $args[0] -eq '--hyde') {
        # -t gives the window a terminal; each option is single-quoted for hyde's shell.
        $options = ($args | Select-Object -Skip 1 | ForEach-Object { "'" + ($_ -replace "'", "'\''") + "'" }) -join ' '
        ssh -t -o ConnectTimeout=10 $JarvisHyde "~/jarvis-wrapper/jarvis-code $options"
        return
    }
    if (-not $args -or $window -contains $args[0]) {
        $python = Join-Path $JarvisWindow '.venv\Scripts\python.exe'
        if (-not (Test-Path $python)) {
            Write-Host "The Jarvis window isn't on this laptop yet: run  jarvis --update  (it copies it from hyde)"
            return
        }
        $colours = $env:COLORTERM
        $env:COLORTERM = 'truecolor'  # Windows Terminal does 24-bit colour
        $env:JARVIS_HYDE = $JarvisHyde  # /jarvis settings reaches Jarvis's settings over ssh
        try { & $python -X utf8 -I (Join-Path $JarvisWindow 'launch.py') @args }
        finally { $env:COLORTERM = $colours }
        return
    }
    $aloud = $args -contains '-a'
    $question = ($args | Where-Object { $_ -ne '-a' }) -join ' '
    if (-not $question) { Write-Host 'usage: jarvis your question [-a]'; return }
    $flag = if ($aloud) { ' -a' } else { '' }
    $OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
    # The question goes on stdin, so no quoting can break on the way through ssh.
    $question | ssh -o ConnectTimeout=10 $JarvisHyde "python3 ~/jarvis/deploy/ask.py -$flag"
}

function Update-JarvisWindow {
    # The window's code (~/jarvis-wrapper on hyde) is copied to $JarvisWindow and runs here, on
    # this laptop's Python and Claude Code. Run it again after the window changes on hyde.
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Host 'Claude Code needs installing here first:  irm https://claude.ai/install.ps1 | iex'
        Write-Host 'then open a new PowerShell, run  claude  once to sign in, and run  jarvis --update  again.'
        return
    }
    $python = $null
    foreach ($candidate in @('py', '-3'), @('python')) {
        if (Get-Command $candidate[0] -ErrorAction SilentlyContinue) {
            $rest = @($candidate | Select-Object -Skip 1)
            & $candidate[0] @rest -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>$null
            if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
        }
    }
    if (-not $python) {
        Write-Host 'Python 3.10 or newer needs installing here first:  winget install Python.Python.3.12'
        Write-Host 'then open a new PowerShell and run  jarvis --update  again.'
        return
    }

    # Copy into a fresh folder first, so a failed copy leaves the working window alone.
    New-Item -Force -ItemType Directory $JarvisWindow | Out-Null
    $incoming = Join-Path $JarvisWindow 'incoming'
    Remove-Item -Recurse -Force $incoming -ErrorAction SilentlyContinue
    New-Item -ItemType Directory $incoming | Out-Null
    Write-Host 'Copying the window from hyde...'
    scp -q -r -o ConnectTimeout=10 "${JarvisHyde}:jarvis-wrapper/jarvis_code" "${JarvisHyde}:jarvis-wrapper/launch.py" `
        "${JarvisHyde}:jarvis-wrapper/requirements.txt" $incoming
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Couldn't copy it from hyde. Is Tailscale on, and does  ssh $JarvisHyde  work?"
        Remove-Item -Recurse -Force $incoming
        return
    }
    foreach ($item in 'jarvis_code', 'launch.py', 'requirements.txt') {
        Remove-Item -Recurse -Force (Join-Path $JarvisWindow $item) -ErrorAction SilentlyContinue
        Move-Item (Join-Path $incoming $item) $JarvisWindow
    }
    Remove-Item -Recurse -Force $incoming

    $venv = Join-Path $JarvisWindow '.venv'
    if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
        Write-Host 'Making its Python environment...'
        $rest = @($python | Select-Object -Skip 1)
        & $python[0] @rest -m venv $venv
        if ($LASTEXITCODE -ne 0) { Write-Host "Couldn't make the Python environment."; return }
    }
    Write-Host 'Installing what it needs...'
    & (Join-Path $venv 'Scripts\python.exe') -m pip install -q --disable-pip-version-check `
        -r (Join-Path $JarvisWindow 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Write-Host "pip couldn't install the window's packages."; return }
    Write-Host 'Done: jarvis opens the window here.'
}
