---
name: video-comprehend
description: Analyze a video — YouTube, any yt-dlp-supported URL, or a local file — and produce a structured comprehension report (TL;DR, chart readings, keyframe analysis, claims with evidence tiers, transcript appendix) written into the current project. Also searches every video already analysed in that project. Use when the user shares a video link or file and asks to summarize, analyze, extract from, or find something in it. Designed to feed into research-before-building.
---

# /video-comprehend

Invoked as:

```
/video-comprehend <url-or-path> [options]
/video-comprehend --search <query>
```

Options: `output=<path>`, `--profile chart|general`, `--from <t>`, `--to <t>`,
`--budget <n>`, `--force`, `--no-frames`.

Everything runs locally and free: ffmpeg, yt-dlp, faster-whisper, stdlib sqlite3.
No paid API is called at any point.

## What it does

Frame selection is a **union of three channels**, which is the whole point of the
design:

| channel | catches | blind spot it fixes |
|---|---|---|
| semantic | moments the speaker points at ("look at this", domain terms, numbers) | — |
| scene | ffmpeg scene changes | slides, silent screen shares, code appearing mid-pause, charts marked up without narration |
| floor | evenly spaced timestamps | long static stretches nobody narrates |

Candidates within 3 seconds collapse into one, and clusters that **several channels
agree on** score above any single channel — a scene cut landing exactly on "here is
the setup" is the best frame in the video.

Two profiles:

- **`chart`** — 1280px frames, budget 12–40, loose global / very sensitive local
  dedup. For charts, trading platforms, backtests, equity curves, dense numeric UI.
  640px destroys axis labels and levels; whole-image dedup throws away a chart that
  gained one drawn line. Both are handled here.
- **`general`** — 768px, budget 10–25, stricter dedup. Talks, tutorials, interviews,
  demos.

The budget is adaptive on length and scene density, not a fixed cap.

## Step 1: Validate

Accept any of:
- a YouTube video URL (`watch?v=`, `youtu.be/`, `embed/`, `shorts/`)
- any other http(s) URL — handed to yt-dlp
- a path to a local video file

Reject playlists and channel URLs with:
`"Playlists and channels are not supported — pass a single video: <input>"`

If the argument is `--search`, skip to Step 4.

## Step 2: Check tools

Run and confirm exit 0:
- `python -c "import yt_dlp"` — the scripts invoke `python -m yt_dlp`, so the module
  is what matters, not the CLI shim. Not required for local-file input.
- `ffmpeg -version`

If missing, print this and STOP — do **not** dispatch the agent:

```
Missing required tools. Install:
  pip install yt-dlp faster-whisper     (free, local)
  winget install Gyan.FFmpeg

Then re-run /video-comprehend.
```

## Step 3: Dispatch the video-analyzer agent

Use the `Agent` tool with `subagent_type: video-analyzer`. Pass `target` plus any of
`profile`, `output_path`, `from`, `to`, `budget`, `force`, `no_frames` the user gave.
Leave `profile` unset unless the user named one — the agent picks it from the video's
own content, and that choice is usually better than a guess made from the URL.

The agent runs in its own context so frames never enter this session. Wait for it.

It returns `{report_path, summary}`. Print:

```
Report saved: <report_path>
Summary: <summary>
```

Do not paste the report body. The user or a downstream tool reads the file.

## Step 4: Search past videos

```bash
cd ~/.claude/skills/video-comprehend
python -m scripts.search_videos "<query>" [--section transcript] [--limit 10]
```

Memory is **project-scoped**: reports and an append-only `_index.jsonl` live in the
repo being analysed (`docs/research/video/`, or `docs/research/yt/` where that
already exists). A repo therefore accumulates the corpus relevant to it, and any
future session opened there inherits the whole video context — including videos added
later — without dragging in unrelated material from other projects.

Search returns the video, the section, and the **timestamp** of the spoken moment, so
hits are actionable without re-watching. The FTS index is built in memory on each
run: nothing on disk can go stale, and a few hundred reports build in about a second.

To list what a project has seen:

```bash
python -m scripts.index_video --list --root <project-root>
```

## Notes

- The existing `~/.cache/yt-comprehend/<video-id>` cache is reused as-is, so videos
  already analysed do not re-download.
- Downloads are video-only, capped at 1080p, and pruned once frames are extracted —
  the old pipeline left a ~170 MB mp4 per video in the cache forever.
- YouTube gates player clients behind PO tokens, so downloads retry across
  `default → web_embedded → tv_simply → android_vr → web` rather than trusting
  yt-dlp's default pick, which returns HTTP 403 on many videos. `web_embedded` and
  `web` pass `--remote-components ejs:github`, which fetches yt-dlp's own JS solver
  from GitHub on first use — free, cached after, and the only network call in this
  pipeline that is not the media itself.
- Auto-captions are a rolling window, roughly two thirds redundant.
  `python -m scripts.render_transcript <captions.vtt>` collapses the overlap at word
  level and keeps real spoken timestamps; `--stats` reports how much was removed.
- `--no-frames` skips all visual work. Use it for podcasts and audio-first content.
- Reports are written, never rewritten; the index is append-only. Nothing under
  `docs/research/` is modified in place.

## Tests

```bash
cd ~/.claude/skills/video-comprehend && python tests/test_pipeline.py
```

Covers source resolution, scene/floor candidate generation, caption dedup and
scoring, adaptive budget, channel-agreement merging, temporal distribution, both
dedup profiles, report chunking, and project search. No network, no ffmpeg.
