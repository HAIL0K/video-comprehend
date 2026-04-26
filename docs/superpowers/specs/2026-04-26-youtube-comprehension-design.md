# YouTube Comprehension Skill — Design

**Date:** 2026-04-26
**Status:** Approved (pending user review of this document)

## Purpose

Give Claude Code the ability to understand the content of a YouTube video (primarily but not exclusively trading content) from a single URL, and produce a structured report that can be handed off to the `research-before-building` skill for triangulation, and ultimately into `brainstorming`.

The driving constraint is **token efficiency**: the skill must not "waste usage." It achieves this by (a) preferring free YouTube captions over ASR, (b) extracting only a small number of keyframes selected by caption-driven heuristics rather than uniform sampling, (c) running the heavy work inside a subagent so intermediate state never enters the parent context, and (d) caching aggressively by video ID so re-runs are free.

The intended pipeline is:

```
YT URL  →  yt-comprehend  →  research-before-building  →  brainstorming
```

## Non-goals (v1)

- ASR fallback for caption-less videos (Whisper). Fail cleanly instead; can be added in v2.
- Live streams, premieres, playlists, or channel URLs.
- Cache eviction. The user can `rm -rf ~/.cache/yt-comprehend` if disk fills up.
- Hard-coded trading domain logic. The agent self-prompts based on what kind of video it detects.
- CI-automated tests for the agent. Helper scripts are unit-tested; the agent is verified via manual fixtures.

## Architecture

Three components, each with one clear job.

### 1. Slash skill — `~/.claude/skills/yt-comprehend/SKILL.md`

Thin entry point. Validates the URL shape, checks that `yt-dlp` and `ffmpeg` are on PATH (so we fail before spinning up an agent context), then dispatches the subagent. ~30 lines of skill markdown.

Invoked as:

```
/yt-comprehend <url>
/yt-comprehend <url> output=<path>
/yt-comprehend <url> --force
```

### 2. Subagent — `~/.claude/agents/youtube-analyzer.md`

The orchestrator. Runs in its own context window so the full transcript and frame descriptions never enter the parent session. Calls helper scripts via Bash, does the vision passes via Read on PNGs, writes the final report, returns `{report_path, one_line_summary}` to the caller.

This is also the agent that `research-before-building` dispatches when it sees a YouTube URL in its input — same agent, two entry points.

### 3. Helper scripts — `~/.claude/skills/yt-comprehend/scripts/*.py`

Thin Python wrappers around `yt-dlp` and `ffmpeg`. Three of them, each pure and unit-testable:

- `fetch_captions.py` — given a URL, returns metadata JSON + WebVTT captions, cached by video ID
- `select_keyframes.py` — given captions + metadata, returns up to N keyframe `{timestamp, reason}` records using a caption-driven heuristic
- `extract_frames.py` — given a list of timestamps, downloads frames as PNGs into the cache dir

### Why a subagent and not inline skill logic

The intermediate state — full transcript, frame descriptions, scratch reasoning — is bulky and disposable. The parent session only needs the final report path and a one-line summary. This is the shape `Agent` is designed for, and it is the main reason the skill stays cheap on tokens.

## Pipeline

When the agent is dispatched with a URL:

1. **Validate & cache check.** Extract video ID. If `~/.cache/yt-comprehend/<video-id>/report.md` exists and `--force` was not passed → return that path immediately. Fast path for re-runs.

2. **Fetch metadata + captions** (`fetch_captions.py`). yt-dlp emits `meta.json` (title, channel, duration, upload date) and `captions.vtt`. If no captions are available, fail cleanly with: *"No captions for `<video-id>`. Skill v1 doesn't fall back to ASR."*

3. **Self-prompt for video type.** The agent reads metadata + the first ~300 lines of captions and writes itself a focus prompt — for example: *"This is a momentum-trading tutorial focused on opening-range breakouts. Look for: chart setups shown on screen, specific tickers/levels mentioned, indicator settings, entry/exit logic, risk-management notes."* This focus prompt is included verbatim in the final report so the reader can see what lens was applied.

4. **Select keyframes** (`select_keyframes.py`). Caption-driven heuristic. Signals:
   - Visual deixis: *"look at this", "as you can see", "here's the setup", "on this chart"*
   - Topic-shift markers: long pauses, scene cuts inferable from caption gaps
   - Numeric mentions paired with deictic terms (likely a price level being pointed at)
   - Even spacing as fallback so the full video is covered

   Returns a list of `{timestamp, reason}` capped at **N=15 by default**. The cap exists because vision-captioning 50 frames is where token cost runs away.

5. **Extract frames** (`extract_frames.py`). ffmpeg pulls each timestamp as a PNG into `cache/<video-id>/frames/`.

6. **Vision-caption each frame.** Agent uses the Read tool on each PNG (Claude Code is multimodal). For each frame, the agent generates a 2–4 sentence description guided by the focus prompt — what is on screen, what chart/indicator/setup is visible, any text/numbers readable.

7. **Assemble report.** Agent merges metadata, TL;DR, focus prompt, specifics, keyframe descriptions, section summaries, claims, suggested queries, and the full appendix transcript into a single markdown file. Writes to the configured output path (default: `<cwd>/docs/research/yt/<video-id>-<slug>.md`).

8. **Return.** Agent returns `{report_path, one_line_summary}` to the caller. The full transcript and frame descriptions never enter the parent context.

## File layout

### Source-of-truth (this project directory)

```
C:\APPS\BSNS\AI\claude_code\yt_comprehension_skill\
├── skill\
│   └── SKILL.md
├── agent\
│   └── youtube-analyzer.md
├── scripts\
│   ├── fetch_captions.py
│   ├── select_keyframes.py
│   └── extract_frames.py
├── tests\
│   ├── test_fetch_captions.py
│   ├── test_select_keyframes.py
│   ├── test_extract_frames.py
│   ├── fixtures.txt              # 3 known URLs: with-captions, no-captions, region-locked
│   └── recorded\                 # saved VTT files etc. for offline unit tests
├── install.ps1
├── uninstall.ps1
└── README.md
```

### Installed layout

```
~/.claude/
├── agents/
│   └── youtube-analyzer.md
└── skills/
    └── yt-comprehend/
        ├── SKILL.md
        └── scripts/
            ├── fetch_captions.py
            ├── select_keyframes.py
            └── extract_frames.py
```

### Runtime data (created on first use, never committed)

```
~/.cache/yt-comprehend/<video-id>/
├── meta.json
├── captions.vtt
├── frames/
│   ├── 00-01-23.png
│   ├── 00-04-15.png
│   └── ...
└── report.md
```

### Output (where downstream tools read it)

```
<cwd>/docs/research/yt/<video-id>-<slug>.md   # default, overridable via output=<path>
```

### Install script behavior

- Checks `yt-dlp` and `ffmpeg` are on PATH; if missing, prints exact `winget install yt-dlp.yt-dlp` and `winget install Gyan.FFmpeg` commands and exits non-zero. Does **not** auto-install.
- Checks Python 3.10+ on PATH.
- Creates `~/.claude/skills/yt-comprehend/` and `~/.claude/agents/`, copies files.
- Idempotent — safe to re-run, overwrites existing files (useful during dev).

## Output schema (the report)

This is the artifact downstream tools consume, so the structure is fixed.

```markdown
---
video_id: dQw4w9WgXcQ
title: "How I trade opening-range breakouts"
channel: "TraderName"
duration: "47:32"
uploaded: "2026-03-14"
url: https://youtube.com/watch?v=dQw4w9WgXcQ
generated: "2026-04-26T15:30:00Z"
keyframe_count: 14
schema_version: 1
truncated: false
focus_prompt: "Momentum-trading tutorial; looking for chart setups, levels, entry/exit logic"
---

# How I trade opening-range breakouts

## TL;DR
One paragraph, ~3–5 sentences. The thesis of the video in plain English.

## Focus lens
The self-generated focus prompt the agent used during analysis, surfaced
verbatim so the reader can see what was prioritized — and what was
likely de-emphasized.

## Specifics
Flat structured list of items downstream tools want to grep without reading
prose. Categories self-prompted from video type:

- **Tickers/symbols mentioned:** SPY, QQQ, NVDA…
- **Levels/numbers:** 512.40, 510.85, 0.382 retracement…
- **Indicators/settings:** VWAP, RSI(14), 9/20/50 EMA…
- **Named strategies/concepts:** ORB, liquidity sweep, stop hunt…
- **Tools/platforms:** TradingView, ThinkOrSwim…

For non-trading videos the categories shift (commands, libraries, named
techniques, etc.) — the agent populates this section based on the focus
prompt.

## Key visual moments
For each keyframe (numbered 1..N), in chronological order:

### [1] 00:01:23 — Chart of SPY 5-min with ORB lines drawn
> **Why selected:** "as you can see here, the opening range is..."
>
> **What's on screen:** SPY 5-minute candlestick chart. Two horizontal
> lines marking the opening range high (~$512.40) and low (~$510.85).
> VWAP overlay visible. Volume pane at bottom shows a spike at the
> breakout candle.

## Section summaries
The agent segments the video into 5–10 logical sections (intro,
methodology, example trade 1, example trade 2, Q&A, etc.). Each section
header carries timestamp range and references to relevant keyframes;
3–5 sentence summary follows.

### 00:00 – 03:42 — Intro and thesis [Keyframe 1]
Three-to-five sentence summary…

### 03:42 – 12:18 — Methodology overview [Keyframes 2, 3]
Three-to-five sentence summary…

## Claims & evidence
Tier-tagged claims for the research skill to triangulate.

- **★★★ Demonstrated** — backed by something shown on screen (backtest
  result, citation, on-chart evidence)
- **★★ Anecdotal** — specific but unverifiable (the trader's own track
  record, named trade examples)
- **★ Asserted** — bare claim, no evidence shown

Example entries:

- ★ ORB works better on 5-min than 1-min charts.
- ★★ The trader claims his ORB strategy returned 23% in Q1 2024 [00:34:12].
- ★★★ The 2024 backtest shown on screen [Keyframe 7] returned 23% on SPY.

## Suggested research queries
3–5 explicit questions for the research skill to triangulate. Makes the
handoff to `research-before-building` literal instead of inferred.

- Verify: does the 9/20/50 EMA cross have a published edge on intraday SPY?
- Verify: is the 23% return claim supported by independent backtests?
- Open: how does ORB perform on lower-liquidity tickers (small caps)?

---

## Appendix: Full transcript
Full verbatim transcript with timestamps, clearly demarcated as appendix
so casual readers and the research skill can stop at the `---` above.
Anyone who needs exact quotes or wants to grep drops down here.

[00:00:01] Welcome back, today we're going to talk about…
[00:00:04] …
```

### Design decisions in the schema

- **Frontmatter is structured YAML** so the research skill (or any downstream tool) can parse it without scraping the body.
- **TL;DR is short and lossy on purpose** — the research skill needs the gist in ~100 tokens, not the whole report.
- **Specifics is flat and grep-friendly** — this is what brainstorming and research will actually mine.
- **Keyframes are first-class**, not buried in transcript — they're the load-bearing section for trading content.
- **Section summaries are bounded** (~3–5 sentences × 5–10 sections), so the structured top-half stays O(1) in video duration.
- **Claims are tier-tagged** to match what `research-before-building` expects from upstream sources, so it knows what to triangulate vs. trust.
- **Suggested research queries** make the handoff to research literal.
- **Full transcript lives in an appendix** below a `---` separator. Downstream tools that don't need verbatim text can stop reading at the separator. Tools that do need it can grep the appendix.

## Error handling

| Failure | Behavior |
|---|---|
| `yt-dlp` or `ffmpeg` not on PATH | Skill exits with the same install message the install script prints. Do not dispatch the agent. |
| Invalid URL / not a YouTube video | Validate up front, return `"Not a YouTube URL: <input>"`. |
| Video has no captions | Hard-fail: `"No captions available for <video-id>. Skill v1 doesn't fall back to ASR; try a video with captions enabled."` |
| Region-locked / private / age-gated | Surface the yt-dlp error verbatim — it is already clear. |
| ffmpeg fails to extract a specific frame | Skip that keyframe, log a warning, continue with the rest. Do not fail the whole run for one bad frame. |
| Vision captioning fails on a specific frame | Note `[caption unavailable]` in the report and continue. |
| Live stream / premiere / playlist URL | yt-dlp will refuse — surface its error. Out of scope to handle. |

## Caching

- **Cache key:** video ID. Anything yt-dlp/ffmpeg produces is reusable forever for that ID.
- **Cache hit:** `~/.cache/yt-comprehend/<video-id>/report.md` exists → return immediately. Re-runs are free.
- `--force` blows the cache for that video and re-runs end-to-end.
- **No eviction in v1.** User can `rm -rf ~/.cache/yt-comprehend`.
- **Schema-version invalidation.** `meta.json` stores `schema_version`. If the skill version expects a newer schema, re-generate the report but reuse the cached captions and frames (those are schema-independent).

## Edge cases

- **Very long videos (>2hr).** N=15 keyframes still applies; section summaries scale (more sections, same density per section). Frontmatter `truncated` field flips to `true` if anything was capped.
- **Live streams / premieres / playlists / channel URLs.** Out of scope. Skill validates URL shape up front and rejects.

## Testing

- **Helper scripts** (`fetch_captions.py`, `select_keyframes.py`, `extract_frames.py`) get pytest unit tests. They are pure functions over recorded inputs (saved VTT files etc.), so they can be tested without hitting YouTube. Recorded inputs live in `tests/recorded/`.
- **Fixture set:** 3 known YouTube URLs in `tests/fixtures.txt` — one with captions, one without, one private/region-locked. Used for manual end-to-end smoke tests, not automated CI.
- **The agent itself is not unit-tested** — it is an LLM. It is verified via the manual fixture set during development and after schema changes.
