
# Jarvis (on hyde, over Tailscale) from this laptop:
#     jarvis                                   the Jarvis window: Claude Code on hyde, in blue
#     jarvis -c                                the window, continuing the last conversation (-r: pick one)
#     jarvis what quizzes do I have next week  ask Jarvis something
#     jarvis "what's on tomorrow?" -a          (-a: Jarvis also says it out loud, on hyde)
# Quote the question if it has an apostrophe. Set up: docs/terminal.md in ~/jarvis on hyde.
function jarvis {
    $window = '-c', '--continue', '-r', '--resume', '-p', '--prompt', '--model', '--effort', '--mode', '--plain'
    if (-not $args -or $window -contains $args[0]) {
        # -t gives the window a terminal; each option is single-quoted for hyde's shell.
        $options = ($args | ForEach-Object { "'" + ($_ -replace "'", "'\''") + "'" }) -join ' '
        ssh -t -o ConnectTimeout=10 nate@100.71.163.55 "~/jarvis/wrapper/jarvis-code $options"
        return
    }
    $aloud = $args -contains '-a'
    $question = ($args | Where-Object { $_ -ne '-a' }) -join ' '
    if (-not $question) { Write-Host 'usage: jarvis your question [-a]'; return }
    $flag = if ($aloud) { ' -a' } else { '' }
    $OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
    # The question goes on stdin, so no quoting can break on the way through ssh.
    $question | ssh -o ConnectTimeout=10 nate@100.71.163.55 "python3 ~/jarvis/deploy/ask.py -$flag"
}
