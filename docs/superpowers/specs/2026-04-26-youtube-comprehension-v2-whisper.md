# YouTube Comprehension v2 — Whisper ASR Fallback

**Date:** 2026-04-26
**Status:** Spec (pending user review)
**Builds on:** `2026-04-26-youtube-comprehension-design.md`

## Why

v1 hard-fails when yt-dlp can't fetch captions. In live testing this turned out to happen often — YouTube rate-limits the `/api/timedtext` endpoint for unauthenticated yt-dlp traffic, and the cookie-auth workaround is broken on Chromium browsers due to a known DPAPI decryption issue. Caption-less videos exist too (creator opt-out, niche channels).

A skill that fails 20% of the time is worse than one that takes 12 extra minutes. v2 adds a Whisper ASR fallback: when captions can't be fetched, transcribe the audio locally and produce the same VTT contract.

## What changes

The output contract is unchanged. `captions.vtt` still exists in the cache with the same structure; `select_keyframes.py`, `extract_frames.py`, the agent, and the report schema are all untouched. The only architectural change is in `fetch_captions.py` and a new helper.

```
v1:  url  →  yt-dlp captions  →  captions.vtt    (or hard fail)
                                       ↓
                              select_keyframes → ...

v2:  url  →  yt-dlp captions  →  captions.vtt    →  select_keyframes → ...
              ↓ fail (429 / no captions)              ↑
            yt-dlp audio  →  faster-whisper  ────────┘
```

## Architecture

### New module: `scripts/transcribe_audio.py`

Wraps `faster-whisper`. Pure-ish: input is an audio file path; output is a VTT string. Encapsulates model loading and inference.

```
transcribe_audio(audio_path: Path, model_name: str = "small") -> str
```

Returns WebVTT text identical in structure to what yt-dlp emits, so `parse_vtt` consumes it without changes.

### Modified: `scripts/fetch_captions.py`

Becomes the orchestrator. Pseudocode:

```
1. Cache hit? Return.
2. Try yt-dlp captions. If success → return cached paths.
3. Else if --no-asr-fallback flag → raise the v1 RuntimeError.
4. Else fall through:
   a. Download audio via yt-dlp (--extract-audio --audio-format m4a)
   b. Transcribe via transcribe_audio()
   c. Write VTT to captions.vtt
5. Annotate meta.json with transcript_source: "captions" | "whisper".
```

The audio file is cached at `<cache>/audio.m4a`. Re-runs hit the cache.

### New CLI flag

`fetch_captions.py --no-asr-fallback <url>` — opt-out for users who want the v1 fail-fast behavior (e.g., they're on a flaky network and would rather know early than wait 12 minutes for Whisper).

### New env var

`YT_COMPREHEND_WHISPER_MODEL` — defaults to `small`. Accepts `tiny | base | small | medium | large`. Tuning knob for accuracy vs. speed.

## Cache layout (additions)

```
~/.cache/yt-comprehend/<video-id>/
├── meta.json              # gains transcript_source field
├── captions.vtt           # always present after success (whether yt-dlp or Whisper)
├── audio.m4a              # NEW — only present if Whisper was needed
├── frames/
├── video.mp4
└── report.md
```

Whisper model files are cached by faster-whisper itself in `~/.cache/huggingface/` — outside our cache, shared with any other Whisper users on the system.

## Report disclosure

Frontmatter gains one field:

```yaml
transcript_source: "captions" | "whisper"
```

If `whisper`, the **Open caveats** section in the report notes: *"Transcript was generated via Whisper ASR (faster-whisper, model: small). Auto-transcription accuracy is lower than human captions; expect occasional misheard ticker symbols, numerical levels, and homophones. Verify load-bearing claims against the audio."*

## Performance

| Phase | Time (1-hour video, CPU) | Time (1-hour video, GPU) |
|---|---|---|
| yt-dlp captions (when works) | 5-15 sec | same |
| yt-dlp audio download | 30-90 sec | same |
| Whisper `small` transcription | ~12 min | ~30 sec |
| Whisper `base` transcription | ~6 min | ~15 sec |

For trading videos that are typically 20-60 min, the cold-path total is acceptable as a fallback. The hot path (cache hit on re-run) is unchanged.

## Install changes

`install.ps1`:
- Add `faster-whisper` to the dependency check. Python module check, not a CLI binary.
- If missing, print: `pip install faster-whisper` (no winget alternative — Python-only).

`pyproject.toml`:
- `faster-whisper>=1.0.0` becomes a runtime dependency (not just dev).

## Error handling additions

| Failure | Behavior |
|---|---|
| yt-dlp captions returns 429 | Silently fall through to Whisper (this is the main case). |
| Video has no captions | Same — fall through to Whisper. |
| `faster-whisper` not installed | Hard-fail with `pip install faster-whisper` message. |
| Audio download itself fails (private video, true outage) | Surface yt-dlp error verbatim — no further fallback. |
| Whisper transcription errors out (corrupt audio, OOM) | Surface error with model name + suggestion to try a smaller model. |
| `--no-asr-fallback` set + caption fetch failed | Same v1 RuntimeError as before. |

## Non-goals (still)

- Word-level timestamps via WhisperX or forced alignment. Segment-level is enough for keyframe selection. Can add later if keyframe heuristic proves too coarse.
- Speaker diarization. Single-speaker is the trading-tutorial common case.
- Translation (Whisper supports `task=translate`). v2 is transcribe-only; multilingual analysis is a v3 thing.
- Cookie-auth setup for captions. We're sidestepping the captions reliability issue by adding a fallback, not by hardening the captions path.

## Testing

- `tests/test_transcribe_audio.py` — unit tests with mocked `faster-whisper.WhisperModel`. Verify VTT output structure, model selection from env var, error handling on failed inference.
- `tests/test_fetch_captions.py` — gains tests for the fallback chain: captions-success path (no change), captions-fail-asr-success path, captions-fail-asr-disabled path (raises), captions-fail-audio-fail path (raises with audio error).
- Manual smoke: run against the URL that hit the 429. With Whisper installed, should succeed in ~12 minutes (or ~30 sec on GPU).

## Migration

- v1 cache entries that lack `transcript_source` in `meta.json` are still valid — agent treats missing field as `"captions"` (best guess for cached entries). On `--force`, regenerate with current source labelled correctly.
- Schema version bumps to `2`. Agent's cache check accepts schema 1 OR 2 (no breaking change for users who built v1 caches).
