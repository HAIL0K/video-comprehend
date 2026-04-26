---
name: yt-comprehend
description: Analyze a YouTube video URL and produce a structured comprehension report (TL;DR, keyframe analysis, claims with evidence tiers, transcript appendix) for downstream tools. Use when the user shares a YouTube link and asks to summarize, analyze, or extract content from it. Designed to feed into research-before-building.
---

# /yt-comprehend

When invoked with a YouTube URL (and optional `output=<path>` or `--force`):

## Step 1: Validate the URL

The argument must be a single YouTube video URL — one of:
- `https://www.youtube.com/watch?v=<id>`
- `https://youtu.be/<id>`
- `https://www.youtube.com/embed/<id>`
- `https://www.youtube.com/shorts/<id>`

Reject playlists, channels, live URLs, and non-YouTube URLs with: `"Not a single YouTube video URL: <input>"`.

## Step 2: Check tools

Run these and confirm exit 0:
- `python -c "import yt_dlp"` (the helpers invoke yt-dlp via `python -m yt_dlp`, so the Python module is what matters — the CLI shim is not required)
- `ffmpeg -version`

If either is missing, print this message and STOP — do **not** dispatch the agent:

```
Missing required tools. Install:
  pip install yt-dlp           (recommended)
  winget install Gyan.FFmpeg

Then re-run /yt-comprehend.
```

## Step 3: Dispatch the youtube-analyzer agent

Use the `Agent` tool with `subagent_type: youtube-analyzer`. Pass:
- `url`: the validated URL
- `output_path` (if user provided `output=<path>`)
- `force` (if user passed `--force`)

The agent runs in its own context. Wait for it to return.

## Step 4: Surface the result

The agent returns `{report_path, summary}`. Print to the user:

```
Report saved: <report_path>
Summary: <summary>
```

Do not paste the full report content. The user (or downstream tool) will Read the file as needed.
