
# Ask Jarvis (on hyde, over Tailscale) from this laptop:
#     jarvis what quizzes do I have next week
#     jarvis "what's on tomorrow?" -a        (-a: Jarvis also says it out loud, on hyde)
# Quote the question if it has an apostrophe. Set up: docs/terminal.md in ~/jarvis on hyde.
function jarvis {
    $aloud = $args -contains '-a'
    $question = ($args | Where-Object { $_ -ne '-a' }) -join ' '
    if (-not $question) { Write-Host 'usage: jarvis your question [-a]'; return }
    $flag = if ($aloud) { ' -a' } else { '' }
    $OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
    # The question goes on stdin, so no quoting can break on the way through ssh.
    $question | ssh -o ConnectTimeout=10 nate@100.71.163.55 "python3 ~/jarvis/deploy/ask.py -$flag"
}
