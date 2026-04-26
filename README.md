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

## Fallback behavior

The skill prefers YouTube's captions (free, instant). When that fails — most often because YouTube rate-limits the captions endpoint, occasionally because the video has no captions — it falls back to local ASR using `faster-whisper`:

| Phase | Captions path | Whisper fallback |
|---|---|---|
| Cold-cache cost | 5–15 sec | 30–90 sec download + ~12 min transcription on CPU (`small` model, 1-hour video). GPU drops this to ~30 sec. |
| Warm-cache cost | 0 (returns cached report) | 0 (returns cached report) |
| Disk | ~1 MB captions + meta | + ~30 MB cached audio |

The report frontmatter records `transcript_source: captions | whisper`, and a caveat is added to the report when ASR was used (Whisper occasionally mishears tickers and numerical levels).

To disable the fallback and restore v1 fail-fast behavior, pass `--no-asr-fallback` to the underlying `fetch_captions.py`, or invoke the slash skill in fail-fast mode (forthcoming UI knob).

Tune the Whisper model with `$env:YT_COMPREHEND_WHISPER_MODEL = "base"` (or `tiny`/`small`/`medium`/`large`).

## Install

Requires Windows + PowerShell. Tools checked at install time:
- `yt-dlp` Python module (`pip install yt-dlp`) or CLI binary (`winget install yt-dlp.yt-dlp`)
- `faster-whisper` Python module (`pip install faster-whisper`) — used as ASR fallback when YouTube rate-limits captions or a video has no captions
- `ffmpeg` on PATH (`winget install Gyan.FFmpeg`)
- Python 3.10+ on PATH

```powershell
.\install.ps1
```

This copies `skill/`, `agent/`, and `scripts/` into `~/.claude/`. Idempotent — re-run any time.

The first Whisper transcription downloads the model (~244 MB for `small`, the default) into `~/.cache/huggingface/`. Subsequent runs reuse it.

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

## Limits (v2)

- Single videos only — no playlists, channels, or live streams.
- 15 keyframes max per video.
- Whisper ASR is segment-level (not word-level). Sufficient for keyframe selection; not sub-second precise.
- Whisper transcription is local-CPU by default. Set up CUDA/cuDNN for `faster-whisper` if you want GPU speed (~30x faster).
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

- v1 Design: `docs/superpowers/specs/2026-04-26-youtube-comprehension-design.md`
- v1 Plan: `docs/superpowers/plans/2026-04-26-youtube-comprehension.md`
- v2 Design (Whisper fallback): `docs/superpowers/specs/2026-04-26-youtube-comprehension-v2-whisper.md`
- v2 Plan: `docs/superpowers/plans/2026-04-26-youtube-comprehension-v2-whisper.md`

## Validation Results

Last manual smoke test: **2026-04-26**

- **URL tested:** `https://www.youtube.com/watch?v=MGB43Q13WX8` (12:30 German Bitcoin trading video)
- **Transcript source:** `whisper` — yt-dlp captions returned HTTP 429 (rate limited), Whisper fallback engaged automatically
- **Whisper transcription:** ~3 min on CPU (`small` model, `int8`)
- **Keyframes selected:** 15 / 15 (cap)
- **Frames extracted:** 15 / 15 (0 skipped)
- **Vision-captioning verified:** chart frame at 10:52 successfully ingested by Read tool — TradingView BTCUSD chart with support/resistance levels, volume profile, watchlist all visible

### Notes from validation

- **CUDA crash on first run.** `faster-whisper` auto-detected CUDA (cuBLAS partially present on the test machine) and tried to load `cublas64_12.dll`, which failed. Fixed by forcing `device="cpu"` with `compute_type="int8"` as the default. GPU users can opt in via `$env:YT_COMPREHEND_WHISPER_DEVICE = "cuda"`.
- **Metadata gap on partial-failure replay.** When yt-dlp captions partially succeed (writes `info.json`) and the Whisper step then crashes, `info.json` is consumed and a re-run yields a meta.json with only `schema_version` and `transcript_source`. Title/uploader/duration are missing on the recovery run. Documented as a v2.1 fix; pipeline still works because `select_keyframes` falls back to cue-derived duration.
- **Deixis heuristic is English-only.** German trader content fell through to even-spacing fallback for most frames, but chart-keyword scoring still caught moments mentioning "Weekly-Chart", "Pie-Chart", "Levels". Multilingual deictic phrases would improve keyframe targeting on non-English content. Future enhancement.
