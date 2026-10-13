---
name: yt-comprehend
description: Alias for video-comprehend. Analyze a YouTube video URL and produce a structured comprehension report (TL;DR, keyframe analysis, claims with evidence tiers, transcript appendix). Use when the user shares a YouTube link and asks to summarize, analyze, or extract content from it.
---

# /yt-comprehend — alias

This skill was superseded by **`video-comprehend`**, which handles YouTube plus any
other yt-dlp source and local files, selects frames by union of caption semantics and
ffmpeg scene detection, and writes project-scoped searchable reports.

**Invoke `video-comprehend` instead** and pass the URL through unchanged. Every
argument this skill accepted still works there:

| old | new |
|---|---|
| `/yt-comprehend <url>` | `/video-comprehend <url>` |
| `output=<path>` | `output=<path>` |
| `--force` | `--force` |
| `--no-frames` | `--no-frames` |

Nothing is lost by switching: the existing `~/.cache/yt-comprehend/<video-id>` cache
is reused as-is, so videos already analysed under this skill do not re-download.

