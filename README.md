# video-comprehend (formerly yt-comprehend)

A Claude Code skill + subagent that turns a video into a structured, searchable
comprehension report. It works on YouTube, any other site yt-dlp supports, and local
video files.

v3 replaces the YouTube-only v2 pipeline. `/yt-comprehend` still works: it is now a
thin alias that forwards to `/video-comprehend`, and the old
`~/.cache/yt-comprehend/<video-id>` cache is reused, so nothing re-downloads.

Everything runs locally and costs nothing: ffmpeg, yt-dlp, faster-whisper (opt-in),
stdlib sqlite3 FTS5. No paid API is called.

## What changed in v3

- **Any source.** YouTube, any yt-dlp URL, or a local file. Local files use a sidecar
  `.vtt`/`.srt` or an embedded subtitle track.
- **Frames come from three channels.** v2 picked frames from captions only. v3 merges
  three channels:
  - *semantic*: the speaker points at something ("look at this", domain terms, numbers)
  - *scene*: ffmpeg scene changes, which catch slides, silent screen shares and
    charts marked up without narration
  - *floor*: evenly spaced frames, which cover long stretches nobody narrates

  Candidates less than 3 s apart merge into one. A frame that several channels agree
  on ranks above a frame only one channel found.
- **Two profiles.**
  - `chart`: 1280 px frames, budget 12–40. Global dedup is loose and local-change
    detection is very sensitive, so a chart that gains one drawn line is kept.
  - `general`: 768 px, budget 10–25, stricter dedup.

  The frame budget adapts to video length and scene density.
- **Reports are project-scoped and searchable.** Reports and an append-only
  `_index.jsonl` go into the project you are working in (`docs/research/video/`, or
  `docs/research/yt/` if that already exists). `/video-comprehend --search <query>`
  returns the video, the section and the timestamp of the spoken moment.
- **Cleaner transcripts.** Auto-captions arrive as a rolling window and are about two
  thirds redundant. `render_transcript.py` collapses the overlap at word level and
  keeps the real timestamps. HTML entities that were escaped twice (`&amp;nbsp;`,
  common in caption tracks uploaded by publishers) are decoded.
- **Downloads hold up better.** YouTube now gates player clients behind PO tokens. v3
  retries `default → web_embedded → tv_simply → android_vr → web` instead of
  trusting yt-dlp's default client, which returns 403 on many videos. Downloads are
  video-only, capped at 1080p, and deleted once frames are extracted.
- **Whisper is opt-in.** Captions are the default and only transcript channel. Local
  ASR runs only with `--asr-fallback`, because Whisper is slow, invents text over music
  and crosstalk, and loses the spoken timestamps that make reports searchable. When it
  does run, it uses a GPU if one is present (`large-v3-turbo`, batched) and falls back
  to CPU (`small`).

## What a report contains

TL;DR, focus lens, specifics (tickers, levels, named strategies…), key visual moments
with timestamps, section summaries, claims tagged by evidence tier, suggested research
queries, and the deduplicated transcript as an appendix. The report frontmatter
records `transcript_source: captions | whisper`.

The analysis runs in a subagent (`video-analyzer`), so frames and the full transcript
never enter your main session's context.

## Install

Windows + PowerShell. Requirements:

```powershell
pip install yt-dlp numpy pillow     # required (yt-dlp only for URLs)
pip install faster-whisper          # optional, for --asr-fallback
winget install Gyan.FFmpeg          # required
.\install.ps1
```

The installer copies `skill/video-comprehend`, `skill/yt-comprehend` (alias),
`scripts/` and `agent/video-analyzer.md` into `~/.claude/`. It also removes what
the v2 installer left behind (`agents/youtube-analyzer.md` and
`skills/yt-comprehend/scripts`). You can re-run it at any time.
`.\uninstall.ps1` removes everything the installer added. It leaves the cache and
the reports in your projects alone.

## Usage

```
/video-comprehend <url-or-path>
/video-comprehend <url> --profile chart
/video-comprehend <url> --from 10:00 --to 25:00 --budget 20
/video-comprehend <file.mp4> --no-frames          # audio-first content
/video-comprehend <url> output=./report.md --force
/video-comprehend --search "funding rate"
```

`research-before-building` and other skills can dispatch the `video-analyzer` agent
directly.

### Environment knobs

| variable | effect |
|---|---|
| `VIDEO_COMPREHEND_CACHE` | cache root (default `~/.cache/yt-comprehend`) |
| `VIDEO_COMPREHEND_WHISPER_MODEL` | Whisper model for `--asr-fallback` |
| `VIDEO_COMPREHEND_WHISPER_BATCH` | GPU batch size |
| `VIDEO_COMPREHEND_YTDLP_COOKIES_FROM_BROWSER` | pass `--cookies-from-browser` to yt-dlp |
| `VIDEO_COMPREHEND_YTDLP_COOKIES` | path to a cookies.txt for yt-dlp |

The v2 `YT_COMPREHEND_*` names are still read as fallbacks.

## Tests

```powershell
python tests/test_pipeline.py      # or: python -m pytest -v
```

The 31 tests cover source resolution, scene/floor candidates, caption dedup and
scoring, adaptive budget, channel-agreement merging, temporal distribution, both dedup
profiles, report chunking and project search. They need no network and no ffmpeg.

## Limits

- Single videos only. Playlists, channels and live streams are rejected.
- Whisper output is segment-level, not word-level.
- The installer is Windows-only. The scripts are plain Python and run on any OS.

## Credits

The scene-detection channel, adaptive budget and dedup ideas come from
[claude-real-video](https://github.com/HUANGCHIHHUNGLeo/claude-real-video) (MIT). v3
changes them for chart content (local-change dedup, 1280 px) and keeps memory
per-project instead of in a global database.

## History

The v1/v2 design and plans are in `docs/superpowers/`.
