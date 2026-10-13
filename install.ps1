# install.ps1 — copy video-comprehend (+ the yt-comprehend alias) and its agent into ~/.claude/
$ErrorActionPreference = 'Stop'

$repoRoot   = $PSScriptRoot
$claudeRoot = Join-Path $env:USERPROFILE '.claude'
$skillDest  = Join-Path $claudeRoot 'skills/video-comprehend'
$aliasDest  = Join-Path $claudeRoot 'skills/yt-comprehend'
$agentDest  = Join-Path $claudeRoot 'agents'

Write-Host '== video-comprehend installer ==' -ForegroundColor Cyan

# 1. Tool checks. ffmpeg + python are required; yt-dlp is required for URLs
#    (local files work without it); faster-whisper is only used by the opt-in
#    --asr-fallback, so its absence is a warning, not a failure.
$missing = @()
foreach ($tool in @('ffmpeg', 'python')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { $missing += $tool }
}
function Test-PyModule($name) {
    if (-not (Get-Command 'python' -ErrorAction SilentlyContinue)) { return $false }
    & python -c "import $name" 2>$null
    return ($LASTEXITCODE -eq 0)
}
if (-not (Test-PyModule 'yt_dlp')) { $missing += 'yt-dlp' }
foreach ($m in @('numpy', 'PIL')) { if (-not (Test-PyModule $m)) { $missing += $m } }   # frame dedup

if ($missing.Count -gt 0) {
    Write-Host ''
    Write-Host "Missing tools: $($missing -join ', ')" -ForegroundColor Red
    Write-Host ''
    Write-Host 'Install commands:'
    if ($missing -contains 'yt-dlp') { Write-Host '  pip install yt-dlp          # the scripts call python -m yt_dlp' }
    if (($missing -contains 'numpy') -or ($missing -contains 'PIL')) { Write-Host '  pip install numpy pillow    # frame dedup' }
    if ($missing -contains 'ffmpeg') { Write-Host '  winget install Gyan.FFmpeg' }
    if ($missing -contains 'python') { Write-Host '  winget install Python.Python.3.12' }
    exit 1
}
if (-not (Test-PyModule 'faster_whisper')) {
    Write-Host 'Note: faster-whisper not found. Only needed for --asr-fallback (pip install faster-whisper).' -ForegroundColor Yellow
}

# 2. Python version check (>= 3.10)
$pyVer = (& python -c "import sys; print('.'.join(str(x) for x in sys.version_info[:2]))").Trim()
$pyMajor, $pyMinor = $pyVer -split '\.'
if ([int]$pyMajor -lt 3 -or ([int]$pyMajor -eq 3 -and [int]$pyMinor -lt 10)) {
    Write-Host "Python 3.10+ required; found $pyVer" -ForegroundColor Red
    exit 1
}

# 3. Remove what the v2 (yt-comprehend) installer left behind: its agent and
#    caption-only scripts are superseded by video-analyzer + the scripts below.
$legacy = @(
    (Join-Path $agentDest 'youtube-analyzer.md'),
    (Join-Path $aliasDest 'scripts')
)
foreach ($p in $legacy) {
    if (Test-Path $p) { Remove-Item -Recurse -Force $p; Write-Host "Removed legacy $p" }
}

# 4. Copy files (idempotent — overwrites)
New-Item -ItemType Directory -Force -Path (Join-Path $skillDest 'scripts') | Out-Null
New-Item -ItemType Directory -Force -Path $aliasDest | Out-Null
New-Item -ItemType Directory -Force -Path $agentDest | Out-Null

Copy-Item -Force (Join-Path $repoRoot 'skill/video-comprehend/SKILL.md') (Join-Path $skillDest 'SKILL.md')
Copy-Item -Force (Join-Path $repoRoot 'skill/yt-comprehend/SKILL.md')    (Join-Path $aliasDest 'SKILL.md')
Copy-Item -Force (Join-Path $repoRoot 'scripts/*.py')                    (Join-Path $skillDest 'scripts')
Copy-Item -Force (Join-Path $repoRoot 'agent/video-analyzer.md')         (Join-Path $agentDest 'video-analyzer.md')

Write-Host ''
Write-Host 'Installed:' -ForegroundColor Green
Write-Host "  $skillDest"
Write-Host "  $aliasDest  (alias)"
Write-Host "  $(Join-Path $agentDest 'video-analyzer.md')"
Write-Host ''
Write-Host 'Use /video-comprehend <url-or-path> (or /yt-comprehend <url>) in any Claude Code session.'
