# yt-comprehend

A Claude Code slash skill + subagent that turns a YouTube URL into a structured comprehension report. Designed to feed `research-before-building` and ultimately `brainstorming`.

## What it produces

A markdown report at `<cwd>/docs/research/yt/<video-id>-<slug>.md` containing:

- TL;DR (3–5 sentences)
- Focus lens (the agent's self-generated analysis prompt — surfaced so you can see what was prioritized)
- Specifics (flat list: tickers, levels, indicators, named strategies, etc., adapted to video type)
- Key visual moments (up to 15 keyframes, each with timestamp + on-screen description)
- Section summaries (5–10 logical sections, ~3–5 sentences each)
- Claims & evidence (tier-tagged: ★ asserted, ★★ anecdotal, ★★★ demonstrated)
- Suggested research queries (3–5 triangulation prompts for the research skill)
- Full transcript appendix (verbatim, below `---`)

## Why it's token-efficient

1. Captions over ASR — free, fast, no audio download.
2. Caption-driven keyframe selection — only ~15 frames vision-captioned, not uniform sampling.
3. Subagent context — full transcript and frame descriptions never enter the parent session.
4. Aggressive caching by video ID — re-runs are free.

## Install

Requires Windows + PowerShell. Tools checked at install time:
- `yt-dlp`, `ffmpeg` on PATH (install via `winget install yt-dlp.yt-dlp` and `winget install Gyan.FFmpeg`)
- Python 3.10+ on PATH

```powershell
.\install.ps1
```

This copies `skill/`, `agent/`, and `scripts/` into `~/.claude/`. Idempotent — re-run any time.

## Usage

In any Claude Code session:

```
/yt-comprehend https://youtu.be/<id>
/yt-comprehend https://youtu.be/<id> output=./my-report.md
/yt-comprehend https://youtu.be/<id> --force
```

Or have `research-before-building` dispatch the `youtube-analyzer` agent — same behavior.

## Cache

`~/.cache/yt-comprehend/<video-id>/` holds `meta.json`, `captions.vtt`, `video.mp4`, `frames/*.png`, and `report.md`. Re-runs hit the cache. To clear:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.cache\yt-comprehend"
```

## Limits (v1)

- No ASR fallback — videos without captions hard-fail.
- Single videos only — no playlists, channels, or live streams.
- 15 keyframes max per video. Override with `output=` not yet supported for the cap.
- Windows-only installer (Linux/macOS planned).

## Development

```powershell
# Run unit tests
python -m pytest -v

# Manual end-to-end (after install)
/yt-comprehend <url>
```

Test fixtures: `tests/fixtures.txt` lists three URLs (with-captions, no-captions, region-locked) for manual smoke tests.

## Spec & Plan

- Design: `docs/superpowers/specs/2026-04-26-youtube-comprehension-design.md`
- Plan: `docs/superpowers/plans/2026-04-26-youtube-comprehension.md`
