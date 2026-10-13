# uninstall.ps1 — remove video-comprehend, the yt-comprehend alias and their agents from ~/.claude/
$ErrorActionPreference = 'Stop'

$claudeRoot = Join-Path $env:USERPROFILE '.claude'
$targets = @(
    (Join-Path $claudeRoot 'skills/video-comprehend'),
    (Join-Path $claudeRoot 'skills/yt-comprehend'),
    (Join-Path $claudeRoot 'agents/video-analyzer.md'),
    (Join-Path $claudeRoot 'agents/youtube-analyzer.md')
)
foreach ($p in $targets) {
    if (Test-Path $p) { Remove-Item -Recurse -Force $p; Write-Host "Removed $p" }
}
Write-Host ''
Write-Host 'Not removed: the frame/download cache and the reports inside your projects.'
Write-Host "  Cache:   Remove-Item -Recurse -Force '$env:USERPROFILE\.cache\yt-comprehend'"
Write-Host '  Reports: <project>/docs/research/video/ (or docs/research/yt/) — yours to keep or delete.'
