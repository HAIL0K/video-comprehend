# YouTube Comprehension v2 — Whisper Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `faster-whisper` ASR fallback to `fetch_captions.py` so the pipeline keeps working when YouTube rate-limits caption requests or when a video has no captions, without changing the downstream contract.

**Architecture:** v1's `fetch_captions.py` becomes the orchestrator. It still tries yt-dlp captions first. On failure, it downloads audio via yt-dlp and transcribes via a new helper (`scripts/transcribe_audio.py`) using `faster-whisper`. Output is the same `captions.vtt` shape the rest of the pipeline already consumes. Schema version bumps to 2; `meta.json` gains a `transcript_source` field that the agent surfaces in the report.

**Tech Stack:** `faster-whisper>=1.0` (CTranslate2-based, smaller than openai-whisper, no PyTorch dependency). Default model `small` (244 MB). Same Python 3.10+, pytest, yt-dlp, ffmpeg as v1.

**Spec:** `docs/superpowers/specs/2026-04-26-youtube-comprehension-v2-whisper.md`
**Builds on v1:** `docs/superpowers/plans/2026-04-26-youtube-comprehension.md` (already executed)

---

## File structure (changes from v1)

| File | Change | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Add `faster-whisper>=1.0` to runtime dependencies. |
| `scripts/transcribe_audio.py` | **Create** | Wraps faster-whisper. Pure helper `segments_to_vtt` + orchestrator `transcribe_audio`. |
| `scripts/fetch_captions.py` | Modify | Becomes orchestrator: yt-dlp captions → fallback to audio + Whisper. New `ensure_audio_downloaded` helper. Bumps `SCHEMA_VERSION` to 2. New `--no-asr-fallback` CLI flag. |
| `tests/test_transcribe_audio.py` | **Create** | Unit tests with mocked `WhisperModel`. |
| `tests/test_fetch_captions.py` | Modify | Append fallback-chain tests. Update existing tests for `transcript_source` field. |
| `agent/youtube-analyzer.md` | Modify | Schema version cache check accepts 1 OR 2. Frontmatter gains `transcript_source`. Open-caveats note when ASR was used. |
| `install.ps1` | Modify | Check `python -c "import faster_whisper"` alongside yt_dlp + ffmpeg. |
| `README.md` | Modify | Document Whisper fallback, install steps for faster-whisper, performance expectations. |

The cache layout gains one optional file: `~/.cache/yt-comprehend/<video-id>/audio.m4a` (only created when Whisper was needed).

---

## Task 1: Add `faster-whisper` to project dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Read current `pyproject.toml`**

Run: `cat pyproject.toml` to see the existing structure.

- [ ] **Step 2: Update `pyproject.toml` to add the runtime dep**

Replace `dependencies = []` with `dependencies = ["faster-whisper>=1.0.0"]`.

The `[project]` block should now read:

```toml
[project]
name = "yt-comprehend"
version = "0.2.0"
description = "YouTube comprehension skill for Claude Code"
requires-python = ">=3.10"
dependencies = ["faster-whisper>=1.0.0"]

[project.optional-dependencies]
dev = ["pytest>=7.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

Note: version bumped to `0.2.0`.

- [ ] **Step 3: Verify pytest still works (existing tests don't depend on faster-whisper at import time)**

Run: `python -m pytest -v 2>&1 | tail -3`
Expected: 50 passed (no regressions).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore(deps): add faster-whisper>=1.0; bump version to 0.2.0"
```

---

## Task 2: `scripts/transcribe_audio.py` — `segments_to_vtt` pure helper

**Files:**
- Create: `scripts/transcribe_audio.py`
- Test: `tests/test_transcribe_audio.py`

`faster-whisper`'s `WhisperModel.transcribe()` returns an iterable of `Segment` objects with `.start`, `.end`, and `.text` attributes. We need to format that as WebVTT — same shape `parse_vtt` already consumes. This task implements only the formatter (a pure function over a sequence of duck-typed segments). Task 3 wraps it with the actual model invocation.

- [ ] **Step 1: Write the failing tests in `tests/test_transcribe_audio.py`**

```python
# tests/test_transcribe_audio.py
from dataclasses import dataclass

import pytest

from scripts.transcribe_audio import segments_to_vtt


@dataclass
class FakeSegment:
    """Duck-types faster_whisper.Segment for testing."""
    start: float
    end: float
    text: str


def test_segments_to_vtt_emits_webvtt_header():
    vtt = segments_to_vtt([FakeSegment(0.0, 1.5, "hi")])
    assert vtt.startswith("WEBVTT\n")


def test_segments_to_vtt_formats_timestamps_with_milliseconds():
    vtt = segments_to_vtt([FakeSegment(83.0, 87.25, "test")])
    assert "00:01:23.000 --> 00:01:27.250" in vtt


def test_segments_to_vtt_formats_hours():
    vtt = segments_to_vtt([FakeSegment(3725.0, 3730.0, "later")])
    assert "01:02:05.000 --> 01:02:10.000" in vtt


def test_segments_to_vtt_strips_text_whitespace():
    vtt = segments_to_vtt([FakeSegment(0.0, 1.0, "  padded   text  ")])
    assert "padded   text" in vtt
    # Leading/trailing trimmed at minimum:
    assert "  padded" not in vtt.split("\n")[2]


def test_segments_to_vtt_skips_empty_segments():
    """Whisper sometimes emits empty/whitespace-only segments — drop them."""
    vtt = segments_to_vtt([
        FakeSegment(0.0, 1.0, "real"),
        FakeSegment(1.0, 2.0, "   "),
        FakeSegment(2.0, 3.0, ""),
        FakeSegment(3.0, 4.0, "more"),
    ])
    cue_lines = [ln for ln in vtt.splitlines() if "-->" in ln]
    assert len(cue_lines) == 2


def test_segments_to_vtt_round_trips_through_parse_vtt(tmp_path):
    """Sanity: VTT we emit must be parseable by our own parse_vtt."""
    from scripts.select_keyframes import parse_vtt
    vtt = segments_to_vtt([
        FakeSegment(0.0, 5.0, "first cue"),
        FakeSegment(5.0, 10.0, "second cue"),
    ])
    f = tmp_path / "out.vtt"
    f.write_text(vtt, encoding="utf-8")
    cues = parse_vtt(f)
    assert len(cues) == 2
    assert cues[0].text == "first cue"
    assert cues[0].start_seconds == 0.0
    assert cues[1].start_seconds == 5.0
```

- [ ] **Step 2: Run tests, verify ImportError**

Run: `python -m pytest tests/test_transcribe_audio.py -v`
Expected: FAIL — `ImportError: cannot import name 'segments_to_vtt' from 'scripts.transcribe_audio'`.

- [ ] **Step 3: Implement `scripts/transcribe_audio.py`**

```python
# scripts/transcribe_audio.py
"""Wrap faster-whisper for ASR fallback. Output is WebVTT-shaped to match yt-dlp captions."""
from __future__ import annotations

from typing import Iterable, Protocol


class _Segment(Protocol):
    """Duck type for faster_whisper.Segment (or any compatible object)."""
    start: float
    end: float
    text: str


def _format_ts(seconds: float) -> str:
    """Format as HH:MM:SS.mmm (WebVTT-compliant)."""
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def segments_to_vtt(segments: Iterable[_Segment]) -> str:
    """Render an iterable of Whisper-style segments as a WebVTT string.

    Empty / whitespace-only segments are dropped. Whitespace inside text is preserved
    except for leading/trailing strip.
    """
    parts: list[str] = ["WEBVTT", ""]
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        parts.append(f"{_format_ts(seg.start)} --> {_format_ts(seg.end)}")
        parts.append(text)
        parts.append("")
    return "\n".join(parts) + "\n"
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/test_transcribe_audio.py -v`
Expected: 6 tests pass.

- [ ] **Step 5: Confirm no regressions**

Run: `python -m pytest -v 2>&1 | tail -3`
Expected: 56 passed (50 prior + 6 new).

- [ ] **Step 6: Commit**

```bash
git add scripts/transcribe_audio.py tests/test_transcribe_audio.py
git commit -m "feat(asr): segments_to_vtt formatter — Whisper segments → WebVTT"
```

---

## Task 3: `scripts/transcribe_audio.py` — `transcribe_audio` orchestrator

**Files:**
- Modify: `scripts/transcribe_audio.py` (append)
- Modify: `tests/test_transcribe_audio.py` (append)

This wraps `faster_whisper.WhisperModel`. Tests mock the model — we never load real Whisper weights in unit tests.

- [ ] **Step 1: Append failing tests**

```python
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.transcribe_audio import transcribe_audio


def _make_fake_model(segments: list[FakeSegment]):
    """Build a MagicMock that quacks like faster_whisper.WhisperModel."""
    info = MagicMock()
    info.language = "en"
    info.duration = sum(s.end - s.start for s in segments) or 1.0
    model = MagicMock()
    model.transcribe.return_value = (iter(segments), info)
    return model


def test_transcribe_audio_returns_vtt_string(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake audio")
    fake_segments = [
        FakeSegment(0.0, 2.0, "hello"),
        FakeSegment(2.0, 4.0, "world"),
    ]
    fake_model = _make_fake_model(fake_segments)

    with patch("scripts.transcribe_audio.WhisperModel", return_value=fake_model) as ctor:
        vtt = transcribe_audio(audio)

    assert vtt.startswith("WEBVTT\n")
    assert "hello" in vtt
    assert "world" in vtt
    # Default model is "small"
    args, kwargs = ctor.call_args
    assert args[0] == "small" or kwargs.get("model_size_or_path") == "small"


def test_transcribe_audio_respects_model_arg(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    with patch("scripts.transcribe_audio.WhisperModel", return_value=_make_fake_model([])) as ctor:
        transcribe_audio(audio, model_name="base")
    args, kwargs = ctor.call_args
    assert args[0] == "base" or kwargs.get("model_size_or_path") == "base"


def test_transcribe_audio_respects_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_WHISPER_MODEL", "tiny")
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    with patch("scripts.transcribe_audio.WhisperModel", return_value=_make_fake_model([])) as ctor:
        transcribe_audio(audio)
    args, kwargs = ctor.call_args
    assert args[0] == "tiny" or kwargs.get("model_size_or_path") == "tiny"


def test_transcribe_audio_explicit_arg_beats_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_WHISPER_MODEL", "tiny")
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    with patch("scripts.transcribe_audio.WhisperModel", return_value=_make_fake_model([])) as ctor:
        transcribe_audio(audio, model_name="medium")
    args, kwargs = ctor.call_args
    assert args[0] == "medium" or kwargs.get("model_size_or_path") == "medium"


def test_transcribe_audio_missing_file_raises(tmp_path):
    nope = tmp_path / "missing.m4a"
    with pytest.raises(FileNotFoundError):
        transcribe_audio(nope)


def test_transcribe_audio_missing_module_raises_clear_error(tmp_path):
    """If faster_whisper isn't installed, raise a friendly RuntimeError."""
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    with patch("scripts.transcribe_audio.WhisperModel", None):
        with pytest.raises(RuntimeError, match="faster-whisper"):
            transcribe_audio(audio)
```

- [ ] **Step 2: Run tests, verify failures**

Run: `python -m pytest tests/test_transcribe_audio.py -v`
Expected: 6 new tests fail with `ImportError: cannot import name 'transcribe_audio'`.

- [ ] **Step 3: Append implementation**

Append to `scripts/transcribe_audio.py`:

```python
import os
from pathlib import Path

# Lazy import — faster_whisper is heavy. If unavailable, leave the symbol as None
# so transcribe_audio() can produce a friendly error message.
try:
    from faster_whisper import WhisperModel  # type: ignore
except ImportError:
    WhisperModel = None  # type: ignore


_DEFAULT_MODEL = "small"


def transcribe_audio(audio_path: Path, model_name: str | None = None) -> str:
    """Transcribe audio at `audio_path` using faster-whisper. Returns VTT.

    Model selection precedence: explicit `model_name` arg > $YT_COMPREHEND_WHISPER_MODEL > "small".

    Raises FileNotFoundError if `audio_path` doesn't exist.
    Raises RuntimeError with install message if faster-whisper isn't importable.
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    if WhisperModel is None:
        raise RuntimeError(
            "faster-whisper is not installed. Install with: pip install faster-whisper"
        )
    chosen = model_name or os.environ.get("YT_COMPREHEND_WHISPER_MODEL") or _DEFAULT_MODEL
    model = WhisperModel(chosen)
    segments, _info = model.transcribe(str(audio_path))
    return segments_to_vtt(segments)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/test_transcribe_audio.py -v`
Expected: 12 tests pass (6 from Task 2 + 6 new).

- [ ] **Step 5: Confirm no regressions**

Run: `python -m pytest -v 2>&1 | tail -3`
Expected: 62 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/transcribe_audio.py tests/test_transcribe_audio.py
git commit -m "feat(asr): transcribe_audio wrapping faster-whisper with model selection"
```

---

## Task 4: `fetch_captions.py` — bump `SCHEMA_VERSION` to 2

**Files:**
- Modify: `scripts/fetch_captions.py`
- Modify: `tests/test_fetch_captions.py` (one assertion)

The schema version is stored in `meta.json` so the agent can detect when a cached report is from an older schema. v2 introduces the `transcript_source` field — anything written by v2 should be tagged version 2. Existing v1 caches still work (the agent accepts 1 OR 2).

- [ ] **Step 1: Modify the constant**

Open `scripts/fetch_captions.py`. Change:

```python
SCHEMA_VERSION = 1
```

To:

```python
SCHEMA_VERSION = 2
```

- [ ] **Step 2: Add a test that pins the schema version**

Append to `tests/test_fetch_captions.py`:

```python
def test_meta_json_carries_schema_version_2(tmp_path, monkeypatch):
    """Newly-written meta.json must record schema_version=2 in v2."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    def fake_yt_dlp(*args, **kwargs):
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{video_id}.info.json").write_text(json.dumps({"id": video_id, "title": "T"}))
        (cache / f"{video_id}.en.vtt").write_text("WEBVTT\n")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=fake_yt_dlp):
        result = fetch_captions(f"https://youtu.be/{video_id}")

    meta = json.loads(Path(result["meta_path"]).read_text())
    assert meta["schema_version"] == 2
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_fetch_captions.py -v`
Expected: 7 tests pass (6 existing + 1 new pin).

- [ ] **Step 4: Confirm full suite**

Run: `python -m pytest -v 2>&1 | tail -3`
Expected: 63 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_captions.py tests/test_fetch_captions.py
git commit -m "chore(captions): bump SCHEMA_VERSION to 2 for v2 transcript_source field"
```

---

## Task 5: `fetch_captions.py` — `ensure_audio_downloaded` helper

**Files:**
- Modify: `scripts/fetch_captions.py` (add helper)
- Modify: `tests/test_fetch_captions.py` (append)

We need a function that downloads only the audio track to `<cache>/audio.m4a` (m4a is small, AAC, ffmpeg-friendly). Mirrors `ensure_video_downloaded` from `extract_frames.py` but extracts audio instead.

- [ ] **Step 1: Append failing tests**

```python
from scripts.fetch_captions import ensure_audio_downloaded


def test_ensure_audio_downloaded_skips_if_exists(tmp_path):
    cache = tmp_path / "id"
    cache.mkdir()
    audio = cache / "audio.m4a"
    audio.write_bytes(b"existing")
    with patch("scripts.fetch_captions.subprocess.run") as mock_run:
        result = ensure_audio_downloaded("https://youtu.be/abc", cache)
    assert result == audio
    mock_run.assert_not_called()


def test_ensure_audio_downloaded_calls_yt_dlp_with_audio_format(tmp_path):
    cache = tmp_path / "id"
    cache.mkdir()

    def fake_run(*args, **kwargs):
        (cache / "audio.m4a").write_bytes(b"downloaded")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=fake_run) as mock_run:
        result = ensure_audio_downloaded("https://youtu.be/abc", cache)

    args = mock_run.call_args[0][0]
    assert "yt_dlp" in args
    assert "--no-playlist" in args
    assert "https://youtu.be/abc" in args
    # Audio-only format selector and m4a output:
    audio_flags = ["--extract-audio", "--audio-format", "m4a"]
    for flag in audio_flags:
        assert flag in args, f"missing {flag} in {args}"
    assert result == cache / "audio.m4a"


def test_ensure_audio_downloaded_yt_dlp_fail_raises(tmp_path):
    cache = tmp_path / "id"
    cache.mkdir()
    with patch("scripts.fetch_captions.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="ERROR: video private"
        )
        with pytest.raises(RuntimeError, match="audio download failed"):
            ensure_audio_downloaded("https://youtu.be/abc", cache)
```

- [ ] **Step 2: Run tests, verify fail**

Run: `python -m pytest tests/test_fetch_captions.py::test_ensure_audio_downloaded_skips_if_exists -v`
Expected: FAIL — `ImportError: cannot import name 'ensure_audio_downloaded'`.

- [ ] **Step 3: Append implementation to `scripts/fetch_captions.py`**

Add this function after `fetch_captions` and before `main`:

```python
def ensure_audio_downloaded(url: str, cache: Path) -> Path:
    """Download audio-only to cache/audio.m4a unless it already exists.

    Used by the Whisper fallback path. Audio downloads use a different yt-dlp endpoint
    than caption downloads, so this often succeeds even when captions are 429ed.

    Raises RuntimeError if yt-dlp returns non-zero or the file isn't produced.
    """
    audio_path = cache / "audio.m4a"
    if audio_path.exists():
        return audio_path
    cache.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--extract-audio",
        "--audio-format", "m4a",
        "--output", str(audio_path),
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not audio_path.exists():
        raise RuntimeError(f"audio download failed: {proc.stderr.strip()}")
    return audio_path
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/test_fetch_captions.py -v`
Expected: 10 tests pass (7 existing + 3 new).

- [ ] **Step 5: Confirm no regressions**

Run: `python -m pytest -v 2>&1 | tail -3`
Expected: 66 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/fetch_captions.py tests/test_fetch_captions.py
git commit -m "feat(captions): ensure_audio_downloaded helper for ASR fallback"
```

---

## Task 6: `fetch_captions.py` — wire the Whisper fallback into `fetch_captions`

**Files:**
- Modify: `scripts/fetch_captions.py` (refactor `fetch_captions`)
- Modify: `tests/test_fetch_captions.py` (append, update existing happy-path test)

The orchestration: try yt-dlp captions; on `RuntimeError` (which covers both yt-dlp non-zero and "no captions produced"), download audio and transcribe with Whisper. Annotate `meta.json` with `transcript_source` regardless of which path succeeded.

- [ ] **Step 1: Update existing happy-path test to assert `transcript_source: "captions"`**

In `tests/test_fetch_captions.py`, find `test_fetch_captions_happy_path`. After the existing assertions, add:

```python
    assert result["transcript_source"] == "captions"
    meta = json.loads(Path(result["meta_path"]).read_text())
    assert meta["transcript_source"] == "captions"
```

- [ ] **Step 2: Append new failing tests for the fallback chain**

```python
def test_fetch_captions_falls_back_to_whisper_on_yt_dlp_failure(tmp_path, monkeypatch):
    """yt-dlp captions fail (e.g. 429) — Whisper takes over and produces VTT."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    yt_dlp_call_count = {"n": 0}

    def fake_run(args, **kwargs):
        yt_dlp_call_count["n"] += 1
        # First call: caption download. Fail with 429.
        if "--write-auto-sub" in args:
            cache.mkdir(parents=True, exist_ok=True)
            (cache / f"{video_id}.info.json").write_text(json.dumps({"id": video_id, "title": "T"}))
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="",
                stderr="ERROR: HTTP Error 429: Too Many Requests",
            )
        # Second call: audio download. Succeed.
        if "--extract-audio" in args:
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "audio.m4a").write_bytes(b"fake audio")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=fake_run), \
         patch("scripts.fetch_captions.transcribe_audio", return_value="WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello\n"):
        result = fetch_captions(f"https://youtu.be/{video_id}")

    assert result["transcript_source"] == "whisper"
    captions = Path(result["captions_path"]).read_text()
    assert "hello" in captions
    meta = json.loads(Path(result["meta_path"]).read_text())
    assert meta["transcript_source"] == "whisper"
    assert meta["schema_version"] == 2


def test_fetch_captions_falls_back_to_whisper_on_no_captions_produced(tmp_path, monkeypatch):
    """yt-dlp succeeds but produces no VTT (caption-less video) — Whisper takes over."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    def fake_run(args, **kwargs):
        cache.mkdir(parents=True, exist_ok=True)
        if "--write-auto-sub" in args:
            (cache / f"{video_id}.info.json").write_text(json.dumps({"id": video_id, "title": "T"}))
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        if "--extract-audio" in args:
            (cache / "audio.m4a").write_bytes(b"fake audio")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=fake_run), \
         patch("scripts.fetch_captions.transcribe_audio", return_value="WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nasr\n"):
        result = fetch_captions(f"https://youtu.be/{video_id}")

    assert result["transcript_source"] == "whisper"


def test_fetch_captions_no_asr_fallback_kwarg_keeps_v1_behavior(tmp_path, monkeypatch):
    """When asr_fallback=False, fall back paths are disabled — re-raise the original error."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    def fake_run(args, **kwargs):
        cache.mkdir(parents=True, exist_ok=True)
        if "--write-auto-sub" in args:
            (cache / f"{video_id}.info.json").write_text("{}")
            # No VTT produced.
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="No captions available"):
            fetch_captions(f"https://youtu.be/{video_id}", asr_fallback=False)


def test_fetch_captions_audio_download_failure_raises(tmp_path, monkeypatch):
    """If captions fail AND audio download also fails, surface the audio error."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    def fake_run(args, **kwargs):
        cache.mkdir(parents=True, exist_ok=True)
        if "--write-auto-sub" in args:
            (cache / f"{video_id}.info.json").write_text("{}")
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="429")
        if "--extract-audio" in args:
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="ERROR: video unavailable"
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="audio download failed"):
            fetch_captions(f"https://youtu.be/{video_id}")
```

- [ ] **Step 3: Refactor `fetch_captions` in `scripts/fetch_captions.py`**

Replace the existing `fetch_captions` function. The full new body:

```python
def fetch_captions(url: str, asr_fallback: bool = True) -> dict:
    """Ensure cache dir for the video has meta.json + captions.vtt.

    v2: tries yt-dlp captions first; on failure (429, no captions, etc.) falls back
    to downloading audio and transcribing with faster-whisper. Set asr_fallback=False
    to disable the fallback and restore v1 fail-fast behavior.

    Returns: {video_id, meta_path, captions_path, cached, transcript_source}.

    Raises ValueError for non-YouTube URLs.
    Raises RuntimeError if yt-dlp fails and (asr_fallback is False, or audio download fails).
    """
    video_id = extract_video_id(url)
    cache = cache_dir_for(video_id)
    meta_path = cache / "meta.json"
    captions_path = cache / "captions.vtt"

    if meta_path.exists() and captions_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return {
            "video_id": video_id,
            "meta_path": str(meta_path),
            "captions_path": str(captions_path),
            "cached": True,
            "transcript_source": meta.get("transcript_source", "captions"),
        }

    cache.mkdir(parents=True, exist_ok=True)
    output_template = str(cache / f"{video_id}.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--skip-download",
        "--write-auto-sub",
        "--write-sub",
        "--sub-lang", "en.*",
        "--sub-format", "vtt",
        "--write-info-json",
        "--no-playlist",
        "--output", output_template,
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    captions_error: str | None = None
    if proc.returncode != 0:
        captions_error = f"yt-dlp failed (exit {proc.returncode}): {proc.stderr.strip()}"

    # Move info.json -> meta.json regardless of caption outcome — we still want metadata.
    info_json = cache / f"{video_id}.info.json"
    meta_data: dict = {}
    if info_json.exists():
        meta_data = json.loads(info_json.read_text(encoding="utf-8"))
        info_json.unlink()

    transcript_source = "captions"
    if captions_error is None:
        vtt_candidates = list(cache.glob(f"{video_id}*.vtt"))
        if not vtt_candidates:
            captions_error = (
                f"No captions available for {video_id}. "
                f"Skill v1 doesn't fall back to ASR; try a video with captions enabled."
            )
        else:
            vtt_candidates.sort(key=lambda p: ("auto" in p.name.lower(), p.name))
            shutil.move(str(vtt_candidates[0]), str(captions_path))

    if captions_error is not None:
        if not asr_fallback:
            raise RuntimeError(captions_error)
        # Fall back to Whisper.
        audio_path = ensure_audio_downloaded(url, cache)
        vtt = transcribe_audio(audio_path)
        captions_path.write_text(vtt, encoding="utf-8")
        transcript_source = "whisper"

    meta_data["schema_version"] = SCHEMA_VERSION
    meta_data["transcript_source"] = transcript_source
    meta_path.write_text(json.dumps(meta_data, indent=2), encoding="utf-8")

    return {
        "video_id": video_id,
        "meta_path": str(meta_path),
        "captions_path": str(captions_path),
        "cached": False,
        "transcript_source": transcript_source,
    }
```

Also add the import at the top of `scripts/fetch_captions.py`:

```python
from scripts.transcribe_audio import transcribe_audio
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/test_fetch_captions.py -v`
Expected: 14 tests pass (10 existing + 4 new). The previously-passing `test_fetch_captions_no_captions_produced` is now superseded by the fallback — keep it as a regression test for the `asr_fallback=False` path *only if* it was already in that mode. **Note:** the existing `test_fetch_captions_no_captions_produced` test will now FAIL because the new default behavior falls back to ASR instead of raising. Update it to pass `asr_fallback=False`:

Find the test in `tests/test_fetch_captions.py`:

```python
def test_fetch_captions_no_captions_produced(tmp_path, monkeypatch):
    """yt-dlp succeeds but produces no .vtt — hard fail with the spec's message."""
    ...
    with patch("scripts.fetch_captions.subprocess.run", side_effect=yt_dlp_writes_only_meta):
        with pytest.raises(RuntimeError, match="No captions available"):
            fetch_captions(f"https://youtu.be/{video_id}")
```

Change the last line to:

```python
            fetch_captions(f"https://youtu.be/{video_id}", asr_fallback=False)
```

Same for `test_fetch_captions_yt_dlp_failure` — it now needs `asr_fallback=False` to raise:

```python
def test_fetch_captions_yt_dlp_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    with patch("scripts.fetch_captions.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="ERROR: Video unavailable",
        )
        with pytest.raises(RuntimeError, match="yt-dlp failed"):
            fetch_captions("https://youtu.be/dQw4w9WgXcQ", asr_fallback=False)
```

- [ ] **Step 5: Re-run tests**

Run: `python -m pytest tests/test_fetch_captions.py -v`
Expected: 14 tests pass.

- [ ] **Step 6: Confirm full suite**

Run: `python -m pytest -v 2>&1 | tail -3`
Expected: 67 passed.

- [ ] **Step 7: Commit**

```bash
git add scripts/fetch_captions.py tests/test_fetch_captions.py
git commit -m "feat(captions): wire Whisper fallback into fetch_captions orchestrator"
```

---

## Task 7: `fetch_captions.py` — `--no-asr-fallback` CLI flag

**Files:**
- Modify: `scripts/fetch_captions.py` (update `main`)
- Modify: `tests/test_fetch_captions.py` (append CLI test — optional, simple subprocess test)

The library function already supports `asr_fallback=False`. This task surfaces it as a CLI flag.

- [ ] **Step 1: Replace `main()` in `scripts/fetch_captions.py`**

```python
def main() -> int:
    args = sys.argv[1:]
    asr_fallback = True
    if "--no-asr-fallback" in args:
        args = [a for a in args if a != "--no-asr-fallback"]
        asr_fallback = False
    if len(args) != 1:
        print("usage: fetch_captions.py [--no-asr-fallback] <youtube-url>", file=sys.stderr)
        return 2
    try:
        result = fetch_captions(args[0], asr_fallback=asr_fallback)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0
```

- [ ] **Step 2: Append a CLI flag test**

```python
def test_main_passes_no_asr_fallback_flag(tmp_path, monkeypatch, capsys):
    """Smoke test: invoking main() with --no-asr-fallback propagates to fetch_captions."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    monkeypatch.setattr(
        "sys.argv",
        ["fetch_captions.py", "--no-asr-fallback", "https://example.com"],  # invalid URL → ValueError
    )
    from scripts.fetch_captions import main
    rc = main()
    assert rc == 1  # ValueError surfaced as exit 1
    err = capsys.readouterr().err
    assert "Not a YouTube video URL" in err
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_fetch_captions.py -v`
Expected: 15 tests pass.

- [ ] **Step 4: Confirm full suite**

Run: `python -m pytest -v 2>&1 | tail -3`
Expected: 68 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_captions.py tests/test_fetch_captions.py
git commit -m "feat(cli): --no-asr-fallback flag for fetch_captions"
```

---

## Task 8: Update `agent/youtube-analyzer.md` for `transcript_source` disclosure

**Files:**
- Modify: `agent/youtube-analyzer.md`

Three changes:

1. Cache check accepts `schema_version == 1` OR `2` (don't invalidate v1 caches on v2 install).
2. Frontmatter template gains `transcript_source: <captions|whisper>`.
3. Open-caveats note added when `transcript_source == "whisper"`.

- [ ] **Step 1: Read the current agent file**

Run: `cat agent/youtube-analyzer.md` to see the structure.

- [ ] **Step 2: Update the cache-check step (Step 1 of the agent's pipeline)**

Find:

```markdown
- If `<cache>/report.md` exists, `<cache>/meta.json` exists, `meta.json.schema_version == 1`, and `force` is not set: output the report path + first paragraph as the one-line summary, and stop.
- If `report.md` exists but `meta.json.schema_version` is missing or != 1: skip step 2 (captions are schema-independent), but regenerate the report from step 3 onward, reusing cached frames if present.
```

Replace with:

```markdown
- If `<cache>/report.md` exists, `<cache>/meta.json` exists, `meta.json.schema_version` is `1` or `2`, and `force` is not set: output the report path + first paragraph as the one-line summary, and stop.
- If `report.md` exists but `meta.json.schema_version` is missing or > 2: skip step 2 (captions are schema-independent), but regenerate the report from step 3 onward, reusing cached frames if present.
```

- [ ] **Step 3: Update the report frontmatter template**

Find the frontmatter inside the Step 11 code block:

```markdown
keyframe_count: <int>
schema_version: 1
truncated: <bool>
focus_prompt: "<the prompt from step 3>"
```

Replace with:

```markdown
keyframe_count: <int>
schema_version: 2
truncated: <bool>
transcript_source: <captions|whisper>
focus_prompt: "<the prompt from step 3>"
```

- [ ] **Step 4: Add a sentence about transcript-source disclosure to the report assembly step**

Find the section in Step 11 right before the appendix block. Insert this line into the agent's instructions (above the markdown code block in step 11, in the prose paragraphs):

```markdown
**Transcript source disclosure.** Read `transcript_source` from `meta.json`. If it is `"whisper"`, add this line to the bottom of the **TL;DR** section (or to a new **Open caveats** subsection just below TL;DR if one is warranted): *"Transcript was generated via Whisper ASR. Auto-transcription accuracy is lower than human captions; expect occasional misheard ticker symbols and numerical levels. Verify load-bearing claims against the audio."* Set the `transcript_source` field in the frontmatter accordingly.
```

(Place it as a new paragraph right before the `Write to the resolved output path. Use exactly this structure:` sentence.)

- [ ] **Step 5: Commit**

```bash
git add agent/youtube-analyzer.md
git commit -m "feat(agent): support schema v2 + transcript_source disclosure"
```

---

## Task 9: Update `install.ps1` to check for `faster-whisper`

**Files:**
- Modify: `install.ps1`

Add a Python-module check for `faster_whisper`. If missing, print install command and exit non-zero.

- [ ] **Step 1: Read current install.ps1**

Run: `cat install.ps1`

- [ ] **Step 2: Add the faster-whisper check**

After the `yt_dlp` availability block (around line 24, just before `if (-not $ytDlpAvailable)`), insert:

```powershell
# faster-whisper module check (used by ASR fallback in v2)
$whisperAvailable = $false
if (Get-Command 'python' -ErrorAction SilentlyContinue) {
    & python -c "import faster_whisper" 2>$null
    if ($LASTEXITCODE -eq 0) { $whisperAvailable = $true }
}
if (-not $whisperAvailable) { $missing += 'faster-whisper' }
```

Then in the install-commands block lower down, add (right after the yt-dlp install lines):

```powershell
if ($missing -contains 'faster-whisper') {
    Write-Host '  pip install faster-whisper  # required for ASR fallback'
}
```

- [ ] **Step 3: Run install.ps1 to confirm the check fires correctly**

Run via PowerShell tool: `.\install.ps1`
Expected: if `faster-whisper` is not installed, the script lists it as missing and prints the pip install line, then exits non-zero. If it is installed, install proceeds to the copy step.

- [ ] **Step 4: Commit**

```bash
git add install.ps1
git commit -m "feat(install): check faster-whisper for ASR fallback"
```

---

## Task 10: Update `README.md`

**Files:**
- Modify: `README.md`

Document the v2 changes: new install dep, new fallback behavior, performance expectations, the `--no-asr-fallback` escape hatch.

- [ ] **Step 1: Read current README**

Run: `cat README.md`

- [ ] **Step 2: Replace the `## Install` section**

Find the `## Install` section. Replace it with:

```markdown
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
```

- [ ] **Step 3: Add a new section "## Fallback behavior" between "## Why it's token-efficient" and "## Install"**

```markdown
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
```

- [ ] **Step 4: Update the `## Limits (v1)` section header to `## Limits (v2)` and revise the bullet list**

Find:

```markdown
## Limits (v1)

- No ASR fallback — videos without captions hard-fail.
- Single videos only — no playlists, channels, or live streams.
- 15 keyframes max per video. Override with `output=` not yet supported for the cap.
- Windows-only installer (Linux/macOS planned).
```

Replace with:

```markdown
## Limits (v2)

- Single videos only — no playlists, channels, or live streams.
- 15 keyframes max per video.
- Whisper ASR is segment-level (not word-level). Sufficient for keyframe selection; not sub-second precise.
- Whisper transcription is local-CPU by default. Set up CUDA/cuDNN for `faster-whisper` if you want GPU speed (~30x faster).
- Windows-only installer (Linux/macOS planned).
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document v2 Whisper fallback, install steps, performance"
```

---

## Task 11: End-to-end smoke test

**Files:**
- (No code changes; possibly README update at the end.)

Validate the v2 pipeline against a real URL. This task assumes:
1. `pip install --user faster-whisper` has been run (separately by the user — installer will block if missing).
2. The previous URL (`https://www.youtube.com/watch?v=MGB43Q13WX8`) is still rate-limited on captions, exercising the Whisper fallback.

- [ ] **Step 1: Confirm faster-whisper is importable**

Run: `python -c "import faster_whisper; print(faster_whisper.__version__)"`
Expected: a version string. If ImportError, stop and ask user to `pip install --user faster-whisper`.

- [ ] **Step 2: Re-run install.ps1**

Run via PowerShell: `.\install.ps1`
Expected: `Installed:` block — all checks pass.

- [ ] **Step 3: Run fetch_captions against the real URL**

```bash
python -m scripts.fetch_captions "https://www.youtube.com/watch?v=MGB43Q13WX8"
```

Expected: depending on YouTube's current rate limit:
- If captions succeed: stdout JSON has `"transcript_source": "captions"`.
- If captions fail: yt-dlp errors are silenced internally, audio downloads, Whisper transcribes (~12 min on CPU), stdout JSON has `"transcript_source": "whisper"`.

- [ ] **Step 4: Verify cache contents**

Run: `ls ~/.cache/yt-comprehend/MGB43Q13WX8/`
Expected: `meta.json`, `captions.vtt`. If Whisper path was taken, also `audio.m4a`.

Run: `cat ~/.cache/yt-comprehend/MGB43Q13WX8/meta.json | python -c "import json, sys; m = json.load(sys.stdin); print(m['schema_version'], m['transcript_source'])"`
Expected: `2 captions` or `2 whisper`.

- [ ] **Step 5: Run select_keyframes against the produced VTT**

```bash
python -m scripts.select_keyframes \
  ~/.cache/yt-comprehend/MGB43Q13WX8/captions.vtt \
  ~/.cache/yt-comprehend/MGB43Q13WX8/meta.json \
  15 \
  ~/.cache/yt-comprehend/MGB43Q13WX8/keyframes.json
```

Expected: stdout JSON with `count` >= 1 and a written `keyframes.json` file.

- [ ] **Step 6: Run extract_frames to download the video and pull frames**

```bash
python -m scripts.extract_frames "https://www.youtube.com/watch?v=MGB43Q13WX8" \
  ~/.cache/yt-comprehend/MGB43Q13WX8/keyframes.json
```

Expected: stdout JSON with `count` >= 1 and PNG files under `~/.cache/yt-comprehend/MGB43Q13WX8/frames/`.

- [ ] **Step 7: Append a "Validation Results" section to README**

Append at the bottom of `README.md`:

```markdown
## Validation Results

Last manual smoke test: 2026-04-26

- Real URL tested: `https://www.youtube.com/watch?v=MGB43Q13WX8` (trader content)
- Transcript source taken: `<captions or whisper>`
- Keyframes extracted: `<count>`
- Cold path duration: `<seconds, approximate>`
- Warm path duration: `<seconds, approximate>` (re-run hits cache)
- Notes: `<anything observed — e.g. "captions hit 429, Whisper fallback worked at 4:51 wall-clock for 18-min video">`
```

Replace the `<...>` placeholders with the actual observed values.

- [ ] **Step 8: Commit**

```bash
git add README.md
git commit -m "test(smoke): record v2 end-to-end validation results"
```

---

## Self-review

After writing the full plan, re-checking against the spec:

**1. Spec coverage:**

- New `transcribe_audio.py` module → Tasks 2, 3 ✓
- Modified `fetch_captions.py` orchestrator → Tasks 4, 5, 6, 7 ✓
- `audio.m4a` in cache → Task 5 (creates) ✓
- `--no-asr-fallback` CLI flag → Task 7 ✓
- `$YT_COMPREHEND_WHISPER_MODEL` env var → Task 3 ✓
- Schema bump to 2, agent accepts 1 OR 2 → Tasks 4, 8 ✓
- `transcript_source` field in `meta.json` and report frontmatter → Tasks 6, 8 ✓
- Open-caveats note in report when ASR used → Task 8 ✓
- `install.ps1` checks `faster-whisper` → Task 9 ✓
- `pyproject.toml` adds `faster-whisper` → Task 1 ✓
- Error matrix (yt-dlp 429 → fallback, no-captions → fallback, faster-whisper missing → hard fail, audio fail → surface error, --no-asr-fallback → v1 behavior) → Tasks 6, 7 ✓
- Migration: v1 caches still valid (schema 1 accepted) → Task 8 ✓
- Tests for transcribe_audio with mocked WhisperModel → Tasks 2, 3 ✓
- Tests for fallback chain → Task 6 ✓
- Manual smoke against real video → Task 11 ✓

No spec gaps.

**2. Placeholder scan:** No "TBD", "TODO", or "implement later" in the plan. The Task 11 README "Validation Results" section has `<...>` placeholders that are explicitly intended for runtime values — those are filled in by the engineer based on observed output, not left blank.

**3. Type/name consistency:**

- `segments_to_vtt(segments)` and `transcribe_audio(audio_path, model_name=None)` — consistent in both module and tests. ✓
- `WhisperModel` — same import path everywhere (mocked via `scripts.transcribe_audio.WhisperModel`). ✓
- `ensure_audio_downloaded(url, cache)` returns `Path` — consistent with v1's `ensure_video_downloaded`. ✓
- `fetch_captions` return shape gains `transcript_source` field — used consistently in tests and agent. ✓
- `SCHEMA_VERSION = 2` — bumped in Task 4, referenced in Task 6 (`meta_data["schema_version"] = SCHEMA_VERSION`), accepted in Task 8 (agent cache check). ✓
- `asr_fallback: bool = True` — same kwarg name in lib function and CLI flag conversion (`--no-asr-fallback` toggles it to False). ✓

No type/name drift.
