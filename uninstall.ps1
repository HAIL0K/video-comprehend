# uninstall.ps1 — remove yt-comprehend from ~/.claude/
$ErrorActionPreference = 'Stop'

$claudeRoot = Join-Path $env:USERPROFILE '.claude'
$skillDest = Join-Path $claudeRoot 'skills/yt-comprehend'
$agentFile = Join-Path $claudeRoot 'agents/youtube-analyzer.md'

if (Test-Path $skillDest) {
    Remove-Item -Recurse -Force $skillDest
    Write-Host "Removed $skillDest"
}
if (Test-Path $agentFile) {
    Remove-Item -Force $agentFile
    Write-Host "Removed $agentFile"
}
Write-Host ''
Write-Host 'Cache at ~/.cache/yt-comprehend was NOT removed. To delete:'
Write-Host "  Remove-Item -Recurse -Force '$env:USERPROFILE\.cache\yt-comprehend'"
