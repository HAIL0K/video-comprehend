# install.ps1 — copy yt-comprehend skill + agent into ~/.claude/
$ErrorActionPreference = 'Stop'

$repoRoot = $PSScriptRoot
$claudeRoot = Join-Path $env:USERPROFILE '.claude'
$skillDest = Join-Path $claudeRoot 'skills/yt-comprehend'
$agentDest = Join-Path $claudeRoot 'agents'

Write-Host '== yt-comprehend installer ==' -ForegroundColor Cyan

# 1. Tool checks
$missing = @()
foreach ($tool in @('ffmpeg', 'python')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        $missing += $tool
    }
}
# yt_dlp can be on PATH as 'yt-dlp' OR available as a Python module — accept either.
$ytDlpAvailable = $false
if (Get-Command 'yt-dlp' -ErrorAction SilentlyContinue) { $ytDlpAvailable = $true }
elseif ((Get-Command 'python' -ErrorAction SilentlyContinue) -and `
        ((& python -c "import yt_dlp" 2>&1; $LASTEXITCODE) -eq 0)) {
    $ytDlpAvailable = $true
}
if (-not $ytDlpAvailable) { $missing += 'yt-dlp' }

if ($missing.Count -gt 0) {
    Write-Host ''
    Write-Host "Missing tools: $($missing -join ', ')" -ForegroundColor Red
    Write-Host ''
    Write-Host 'Install commands:'
    if ($missing -contains 'yt-dlp') {
        Write-Host '  pip install yt-dlp           # recommended (uses Python module)'
        Write-Host '  winget install yt-dlp.yt-dlp # alternative (CLI binary)'
    }
    if ($missing -contains 'ffmpeg') { Write-Host '  winget install Gyan.FFmpeg' }
    if ($missing -contains 'python') { Write-Host '  winget install Python.Python.3.12' }
    exit 1
}

# 2. Python version check (>= 3.10)
$pyVer = (& python -c "import sys; print('.'.join(str(x) for x in sys.version_info[:2]))").Trim()
$pyMajor, $pyMinor = $pyVer -split '\.'
if ([int]$pyMajor -lt 3 -or ([int]$pyMajor -eq 3 -and [int]$pyMinor -lt 10)) {
    Write-Host "Python 3.10+ required; found $pyVer" -ForegroundColor Red
    exit 1
}

# 3. Copy files (idempotent — overwrites)
New-Item -ItemType Directory -Force -Path $skillDest | Out-Null
New-Item -ItemType Directory -Force -Path $agentDest | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $skillDest 'scripts') | Out-Null

Copy-Item -Force (Join-Path $repoRoot 'skill/SKILL.md') (Join-Path $skillDest 'SKILL.md')
Copy-Item -Force (Join-Path $repoRoot 'agent/youtube-analyzer.md') (Join-Path $agentDest 'youtube-analyzer.md')
Copy-Item -Force -Recurse (Join-Path $repoRoot 'scripts/*.py') (Join-Path $skillDest 'scripts')

Write-Host ''
Write-Host 'Installed:' -ForegroundColor Green
Write-Host "  $skillDest"
Write-Host "  $(Join-Path $agentDest 'youtube-analyzer.md')"
Write-Host ''
Write-Host 'Use /yt-comprehend <url> in any Claude Code session.'
