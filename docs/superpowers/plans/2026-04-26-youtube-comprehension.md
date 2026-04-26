# YouTube Comprehension Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude Code slash skill (`/yt-comprehend`) plus subagent (`youtube-analyzer`) that turns a YouTube URL into a structured markdown report (TL;DR, focus lens, specifics, keyframes, section summaries, tier-tagged claims, suggested research queries, transcript appendix), token-efficient enough to feed into `research-before-building`.

**Architecture:** Three layers. (1) Slash skill at `~/.claude/skills/yt-comprehend/SKILL.md` — thin entry point that validates URL and tool presence, then dispatches the subagent. (2) Subagent at `~/.claude/agents/youtube-analyzer.md` — runs in isolated context, orchestrates the pipeline, does vision passes, writes the report. (3) Three Python helper scripts that wrap `yt-dlp` and `ffmpeg`. Cache by video ID at `~/.cache/yt-comprehend/<id>/`. Output to `<cwd>/docs/research/yt/<id>-<slug>.md`.

**Tech Stack:** Python 3.10+, `yt-dlp` (PATH), `ffmpeg` (PATH), pytest (dev), Claude Code skill+agent system, PowerShell installer.

**Spec:** `docs/superpowers/specs/2026-04-26-youtube-comprehension-design.md`

---

## File structure

The development repo is `C:\APPS\BSNS\AI\claude_code\yt_comprehension_skill\`. Paths below are relative to that root.

| File | Responsibility |
|---|---|
| `pyproject.toml` | Python package metadata + pytest config + dev deps. No runtime deps (yt-dlp invoked as subprocess). |
| `.gitignore` | Standard Python ignores + cache dir if it ever ends up in-repo. |
| `README.md` | Install + usage + troubleshooting. |
| `scripts/_lib.py` | Shared utilities: `extract_video_id`, `cache_dir_for`, `slugify`. Used by all three scripts. |
| `scripts/fetch_captions.py` | yt-dlp wrapper: download metadata + captions for a URL into the cache dir. |
| `scripts/select_keyframes.py` | Pure caption analysis: parse VTT, score cues, return top-N keyframe timestamps. |
| `scripts/extract_frames.py` | yt-dlp + ffmpeg wrapper: ensure video.mp4 cached, extract one PNG per timestamp. |
| `tests/test_lib.py` | Unit tests for shared utilities. |
| `tests/test_fetch_captions.py` | Unit tests with mocked subprocess. |
| `tests/test_select_keyframes.py` | Unit tests against handcrafted VTT in `tests/recorded/`. |
| `tests/test_extract_frames.py` | Unit tests with mocked subprocess. |
| `tests/recorded/sample.vtt` | Handcrafted ~30-line VTT with mixed deictic/non-deictic cues. |
| `tests/recorded/sample_meta.json` | Handcrafted metadata JSON matching `sample.vtt` duration. |
| `tests/fixtures.txt` | 3 real YouTube URLs (with-captions, no-captions, region-locked) for manual smoke tests. |
| `agent/youtube-analyzer.md` | Full subagent instructions: pipeline, focus prompt generation, frame captioning, report assembly with the exact schema, return contract. |
| `skill/SKILL.md` | Slash skill: validate URL, check tools, dispatch agent, surface result. |
| `install.ps1` | Tool checks (yt-dlp, ffmpeg, Python 3.10+), then copy `skill/` and `agent/` into `~/.claude/`. Idempotent. |
| `uninstall.ps1` | Symmetrical removal. |

**Note on the `video.mp4` cache file:** The spec listed key cache items (meta.json, captions.vtt, frames/, report.md) but `extract_frames.py` needs the source video to feed ffmpeg. We cache it at `~/.cache/yt-comprehend/<id>/video.mp4` alongside the others — implementation detail, no eviction in v1.

---

## Task 1: Project scaffold + git init

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md` (placeholder)
- Create: `scripts/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)
- Create: `tests/recorded/.gitkeep` (empty)

- [ ] **Step 1: Initialize git repo**

```bash
git init
git config user.name "$(git config --global user.name)"
git config user.email "$(git config --global user.email)"
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "yt-comprehend"
version = "0.1.0"
description = "YouTube comprehension skill for Claude Code"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=7.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 3: Create `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
dist/
build/
.venv/
```

- [ ] **Step 4: Create placeholder `README.md`**

```markdown
# yt-comprehend

YouTube comprehension skill for Claude Code. See `docs/superpowers/specs/2026-04-26-youtube-comprehension-design.md` for design.

Install: `.\install.ps1`
Usage: `/yt-comprehend <url>` in Claude Code.

(Full README populated at end of implementation.)
```

- [ ] **Step 5: Create empty `__init__.py` files and `.gitkeep`**

```bash
touch scripts/__init__.py tests/__init__.py tests/recorded/.gitkeep
```

- [ ] **Step 6: Verify pytest runs (no tests yet, should report 0 collected)**

Run: `python -m pytest`
Expected: `collected 0 items` and exit code 5 (no tests). This confirms pytest discovers the project.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore README.md scripts tests
git commit -m "chore: scaffold project structure"
```

---

## Task 2: `scripts/_lib.py` — shared utilities

**Files:**
- Create: `scripts/_lib.py`
- Test: `tests/test_lib.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_lib.py
import pytest
from pathlib import Path
from scripts._lib import extract_video_id, cache_dir_for, slugify


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
])
def test_extract_video_id_valid(url, expected):
    assert extract_video_id(url) == expected


@pytest.mark.parametrize("url", [
    "https://example.com",
    "https://www.youtube.com/playlist?list=PL123",
    "not a url",
    "",
])
def test_extract_video_id_invalid(url):
    with pytest.raises(ValueError):
        extract_video_id(url)


def test_cache_dir_for_returns_path_under_user_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    result = cache_dir_for("dQw4w9WgXcQ")
    assert result == tmp_path / "dQw4w9WgXcQ"


def test_cache_dir_for_default_location(monkeypatch):
    monkeypatch.delenv("YT_COMPREHEND_CACHE", raising=False)
    result = cache_dir_for("dQw4w9WgXcQ")
    assert result.name == "dQw4w9WgXcQ"
    assert "yt-comprehend" in str(result)


@pytest.mark.parametrize("title,expected", [
    ("How I trade ORB", "how-i-trade-orb"),
    ("Trading: 10x Returns!!!", "trading-10x-returns"),
    ("  spaces   everywhere  ", "spaces-everywhere"),
    ("UPPER lower MiXeD", "upper-lower-mixed"),
    ("é unicode ñ", "unicode"),
])
def test_slugify(title, expected):
    assert slugify(title) == expected


def test_slugify_truncates_long_titles():
    long_title = "word " * 50
    result = slugify(long_title)
    assert len(result) <= 60
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_lib.py -v`
Expected: FAIL — `ImportError: cannot import name 'extract_video_id' from 'scripts._lib'` (module doesn't exist yet).

- [ ] **Step 3: Implement `_lib.py`**

```python
# scripts/_lib.py
"""Shared utilities for yt-comprehend scripts."""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

_VIDEO_ID_PATTERNS = [
    re.compile(r"(?:youtube\.com/watch\?(?:[^&]*&)*v=)([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/embed/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/shorts/([A-Za-z0-9_-]{11})"),
]


def extract_video_id(url: str) -> str:
    """Extract the 11-char YouTube video ID from a watch/short/embed URL.

    Raises ValueError for non-YouTube URLs, playlists, channels, or empty input.
    """
    if not url or not isinstance(url, str):
        raise ValueError(f"Empty or non-string URL: {url!r}")
    for pattern in _VIDEO_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    raise ValueError(f"Not a YouTube video URL: {url!r}")


def cache_dir_for(video_id: str) -> Path:
    """Return the per-video cache dir. Uses $YT_COMPREHEND_CACHE if set, else ~/.cache/yt-comprehend."""
    base = os.environ.get("YT_COMPREHEND_CACHE")
    root = Path(base) if base else Path.home() / ".cache" / "yt-comprehend"
    return root / video_id


def slugify(title: str, max_len: int = 60) -> str:
    """Lowercase, ASCII-only, hyphen-separated. Truncated to max_len chars."""
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if len(cleaned) <= max_len:
        return cleaned
    truncated = cleaned[:max_len].rsplit("-", 1)[0]
    return truncated or cleaned[:max_len]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_lib.py -v`
Expected: PASS — all parametrized cases green.

- [ ] **Step 5: Commit**

```bash
git add scripts/_lib.py tests/test_lib.py
git commit -m "feat(lib): add extract_video_id, cache_dir_for, slugify"
```

---

## Task 3: `fetch_captions.py` — happy path with mocked subprocess

**Files:**
- Create: `scripts/fetch_captions.py`
- Test: `tests/test_fetch_captions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fetch_captions.py
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.fetch_captions import fetch_captions


def _fake_yt_dlp_writes_files(cache: Path, video_id: str):
    """Side effect that simulates yt-dlp writing the expected files."""
    (cache / f"{video_id}.info.json").write_text(json.dumps({
        "id": video_id,
        "title": "Test Video",
        "uploader": "TestChannel",
        "duration": 300,
        "upload_date": "20260314",
    }))
    (cache / f"{video_id}.en.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello world\n"
    )
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_fetch_captions_happy_path(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    with patch("scripts.fetch_captions.subprocess.run") as mock_run:
        mock_run.side_effect = lambda *a, **kw: (
            cache.mkdir(parents=True, exist_ok=True)
            or _fake_yt_dlp_writes_files(cache, video_id)
        )
        result = fetch_captions(f"https://youtu.be/{video_id}")

    assert result["video_id"] == video_id
    assert result["cached"] is False
    assert Path(result["meta_path"]).exists()
    assert Path(result["captions_path"]).exists()
    args = mock_run.call_args[0][0]
    assert "yt-dlp" in args[0]
    assert "--skip-download" in args
    assert "--write-auto-sub" in args
    assert "--write-info-json" in args


def test_fetch_captions_cache_hit(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id
    cache.mkdir(parents=True)
    (cache / "meta.json").write_text("{}")
    (cache / "captions.vtt").write_text("WEBVTT\n")

    with patch("scripts.fetch_captions.subprocess.run") as mock_run:
        result = fetch_captions(f"https://youtu.be/{video_id}")

    assert result["cached"] is True
    mock_run.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fetch_captions.py -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_captions'`.

- [ ] **Step 3: Implement `fetch_captions.py`**

```python
# scripts/fetch_captions.py
"""Wrap yt-dlp to download metadata + captions for a YouTube URL into the cache."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from scripts._lib import extract_video_id, cache_dir_for

SCHEMA_VERSION = 1


def fetch_captions(url: str) -> dict:
    """Ensure cache dir for the video has meta.json + captions.vtt. Returns paths + cached flag.

    Raises ValueError for non-YouTube URLs.
    Raises RuntimeError if yt-dlp fails or no captions are produced.
    """
    video_id = extract_video_id(url)
    cache = cache_dir_for(video_id)
    meta_path = cache / "meta.json"
    captions_path = cache / "captions.vtt"

    if meta_path.exists() and captions_path.exists():
        return {
            "video_id": video_id,
            "meta_path": str(meta_path),
            "captions_path": str(captions_path),
            "cached": True,
        }

    cache.mkdir(parents=True, exist_ok=True)
    output_template = str(cache / f"{video_id}.%(ext)s")

    cmd = [
        "yt-dlp",
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
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp failed (exit {proc.returncode}): {proc.stderr.strip()}")

    info_json = cache / f"{video_id}.info.json"
    if info_json.exists():
        meta_data = json.loads(info_json.read_text(encoding="utf-8"))
        meta_data["schema_version"] = SCHEMA_VERSION
        meta_path.write_text(json.dumps(meta_data, indent=2), encoding="utf-8")
        info_json.unlink()

    vtt_candidates = list(cache.glob(f"{video_id}*.vtt"))
    if not vtt_candidates:
        raise RuntimeError(
            f"No captions available for {video_id}. "
            f"Skill v1 doesn't fall back to ASR; try a video with captions enabled."
        )
    shutil.move(str(vtt_candidates[0]), str(captions_path))

    return {
        "video_id": video_id,
        "meta_path": str(meta_path),
        "captions_path": str(captions_path),
        "cached": False,
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: fetch_captions.py <youtube-url>", file=sys.stderr)
        return 2
    try:
        result = fetch_captions(sys.argv[1])
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fetch_captions.py -v`
Expected: PASS — both tests green.

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_captions.py tests/test_fetch_captions.py
git commit -m "feat(captions): yt-dlp wrapper with cache + cache-hit short-circuit"
```

---

## Task 4: `fetch_captions.py` — error paths

**Files:**
- Modify: `tests/test_fetch_captions.py` (append)

- [ ] **Step 1: Add failing tests for the error cases**

Append to `tests/test_fetch_captions.py`:

```python
def test_fetch_captions_invalid_url():
    with pytest.raises(ValueError):
        fetch_captions("https://example.com")


def test_fetch_captions_yt_dlp_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    with patch("scripts.fetch_captions.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="ERROR: Video unavailable",
        )
        with pytest.raises(RuntimeError, match="yt-dlp failed"):
            fetch_captions("https://youtu.be/dQw4w9WgXcQ")


def test_fetch_captions_no_captions_produced(tmp_path, monkeypatch):
    """yt-dlp succeeds but produces no .vtt — hard fail with the spec's message."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    def yt_dlp_writes_only_meta(*args, **kwargs):
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{video_id}.info.json").write_text("{}")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=yt_dlp_writes_only_meta):
        with pytest.raises(RuntimeError, match="No captions available"):
            fetch_captions(f"https://youtu.be/{video_id}")
```

- [ ] **Step 2: Run tests to verify they pass**

The implementation from Task 3 already covers these error cases.

Run: `python -m pytest tests/test_fetch_captions.py -v`
Expected: PASS — all 5 tests green.

- [ ] **Step 3: Commit**

```bash
git add tests/test_fetch_captions.py
git commit -m "test(captions): cover invalid URL, yt-dlp failure, no-captions paths"
```

---

## Task 5: `select_keyframes.py` — VTT parser

**Files:**
- Create: `scripts/select_keyframes.py`
- Create: `tests/recorded/sample.vtt`
- Create: `tests/recorded/sample_meta.json`
- Test: `tests/test_select_keyframes.py`

- [ ] **Step 1: Create the recorded VTT fixture**

`tests/recorded/sample.vtt`:

```
WEBVTT

00:00:01.000 --> 00:00:05.000
Welcome back everyone, today we'll be looking at opening range breakouts.

00:00:30.000 --> 00:00:34.000
The methodology I use is fairly simple at its core.

00:01:23.000 --> 00:01:28.000
As you can see here, the opening range high is right at 512.40.

00:02:10.000 --> 00:02:14.000
Now look at this chart — notice the volume spike on the breakout candle.

00:03:00.000 --> 00:03:04.000
Let me explain why I prefer the 5-minute timeframe.

00:04:15.000 --> 00:04:19.000
Here's the entry trigger — we want a hold above 512.40 for two bars.

00:05:00.000 --> 00:05:04.000
Risk management is critical, I always use a hard stop.

00:06:30.000 --> 00:06:34.000
Take a look at this setup from last Tuesday on QQQ.

00:08:00.000 --> 00:08:04.000
That wraps up the methodology. Subscribe for more.
```

- [ ] **Step 2: Create the matching meta fixture**

`tests/recorded/sample_meta.json`:

```json
{
  "id": "sampletest11",
  "title": "Sample ORB Walkthrough",
  "uploader": "TestChannel",
  "duration": 510,
  "upload_date": "20260314"
}
```

- [ ] **Step 3: Write the failing test for `parse_vtt`**

```python
# tests/test_select_keyframes.py
from pathlib import Path
import pytest

from scripts.select_keyframes import parse_vtt, Cue

FIXTURES = Path(__file__).parent / "recorded"


def test_parse_vtt_returns_cues_in_order():
    cues = parse_vtt(FIXTURES / "sample.vtt")
    assert len(cues) == 9
    assert isinstance(cues[0], Cue)
    assert cues[0].start_seconds == 1.0
    assert cues[0].end_seconds == 5.0
    assert "Welcome back" in cues[0].text
    starts = [c.start_seconds for c in cues]
    assert starts == sorted(starts)


def test_parse_vtt_handles_empty_file(tmp_path):
    empty = tmp_path / "empty.vtt"
    empty.write_text("WEBVTT\n")
    assert parse_vtt(empty) == []


def test_parse_vtt_strips_html_tags(tmp_path):
    f = tmp_path / "tags.vtt"
    f.write_text(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:05.000\n"
        "<c.colorE5E5E5>Hello</c> <i>world</i>\n"
    )
    cues = parse_vtt(f)
    assert cues[0].text == "Hello world"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/test_select_keyframes.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_vtt'`.

- [ ] **Step 5: Implement `parse_vtt`**

```python
# scripts/select_keyframes.py
"""Caption-driven keyframe selection. Pure logic — no I/O beyond reading VTT."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TIMESTAMP_LINE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})\.\d{3}\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})\.\d{3}"
)
_HTML_TAG = re.compile(r"<[^>]+>")


@dataclass
class Cue:
    start_seconds: float
    end_seconds: float
    text: str


def _hms_to_seconds(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s)


def parse_vtt(path: Path) -> list[Cue]:
    """Parse a WebVTT file into a list of Cue. HTML/styling tags stripped."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    cues: list[Cue] = []
    blocks = re.split(r"\n\s*\n", raw)
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        ts_idx = next(
            (i for i, ln in enumerate(lines) if _TIMESTAMP_LINE.search(ln)),
            None,
        )
        if ts_idx is None:
            continue
        m = _TIMESTAMP_LINE.search(lines[ts_idx])
        start = _hms_to_seconds(m.group(1), m.group(2), m.group(3))
        end = _hms_to_seconds(m.group(4), m.group(5), m.group(6))
        text_lines = lines[ts_idx + 1 :]
        text = " ".join(_HTML_TAG.sub("", ln).strip() for ln in text_lines)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            cues.append(Cue(start_seconds=float(start), end_seconds=float(end), text=text))
    return cues
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_select_keyframes.py -v`
Expected: PASS — all 3 tests green.

- [ ] **Step 7: Commit**

```bash
git add scripts/select_keyframes.py tests/test_select_keyframes.py tests/recorded/sample.vtt tests/recorded/sample_meta.json
git commit -m "feat(keyframes): parse_vtt with HTML stripping and Cue dataclass"
```

---

## Task 6: `select_keyframes.py` — `score_cue` heuristic

**Files:**
- Modify: `scripts/select_keyframes.py`
- Modify: `tests/test_select_keyframes.py` (append)

- [ ] **Step 1: Append failing tests for scoring**

Append to `tests/test_select_keyframes.py`:

```python
from scripts.select_keyframes import score_cue


def _cue(text, start=10.0):
    return Cue(start_seconds=start, end_seconds=start + 4.0, text=text)


def test_score_cue_plain_text_low_score():
    assert score_cue(_cue("This is generic narration.")) == 0


def test_score_cue_deictic_phrase_scores():
    assert score_cue(_cue("As you can see, the volume spiked.")) >= 2
    assert score_cue(_cue("Look at this chart.")) >= 2
    assert score_cue(_cue("Here's the setup we want.")) >= 2


def test_score_cue_numeric_plus_deixis_scores_highest():
    plain_deixis = score_cue(_cue("As you can see, it works."))
    deixis_plus_number = score_cue(_cue("As you can see, the level is 512.40."))
    assert deixis_plus_number > plain_deixis


def test_score_cue_chart_keyword_scores():
    assert score_cue(_cue("Notice the candlestick pattern on this chart.")) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_select_keyframes.py::test_score_cue_plain_text_low_score -v`
Expected: FAIL — `ImportError: cannot import name 'score_cue'`.

- [ ] **Step 3: Add `score_cue` to `select_keyframes.py`**

Append to `scripts/select_keyframes.py`:

```python
_DEIXIS_PHRASES = [
    "as you can see",
    "look at this",
    "notice the",
    "here's the",
    "here is the",
    "take a look",
    "on this chart",
    "in this chart",
    "see this",
    "this setup",
    "this pattern",
    "this candle",
    "this level",
    "this trade",
    "this entry",
    "right here",
]
_CHART_KEYWORDS = [
    "chart", "candle", "candlestick", "volume", "vwap", "ema", "rsi",
    "macd", "level", "support", "resistance", "breakout", "breakdown",
]
_NUMERIC_LEVEL = re.compile(r"\b\d{2,5}(?:\.\d{1,4})?\b")


def score_cue(cue: Cue) -> int:
    """Heuristic score: how likely is this cue to coincide with a chart-relevant frame?"""
    text = cue.text.lower()
    score = 0
    has_deixis = any(phrase in text for phrase in _DEIXIS_PHRASES)
    has_chart_keyword = any(kw in text for kw in _CHART_KEYWORDS)
    has_number = bool(_NUMERIC_LEVEL.search(text))
    if has_deixis:
        score += 2
    if has_chart_keyword:
        score += 1
    if has_deixis and has_number:
        score += 2
    return score
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_select_keyframes.py -v`
Expected: PASS — all 7 tests green.

- [ ] **Step 5: Commit**

```bash
git add scripts/select_keyframes.py tests/test_select_keyframes.py
git commit -m "feat(keyframes): score_cue heuristic for deixis + chart keywords + numbers"
```

---

## Task 7: `select_keyframes.py` — `select_top_n_with_distribution`

**Files:**
- Modify: `scripts/select_keyframes.py`
- Modify: `tests/test_select_keyframes.py` (append)

- [ ] **Step 1: Append failing tests**

```python
from scripts.select_keyframes import select_top_n_with_distribution


def test_select_top_n_returns_at_most_n():
    cues = [_cue(f"Look at this {i}", start=i * 10) for i in range(50)]
    result = select_top_n_with_distribution(cues, n=15, duration=600.0)
    assert len(result) <= 15


def test_select_top_n_distributes_across_duration():
    """All-deixis cues clustered in the first half — selection should still cover the second half."""
    cues = [_cue("Look at this", start=i * 5) for i in range(60)]  # 0..295s
    cues += [_cue("filler", start=300 + i * 5) for i in range(60)]  # 300..595s
    result = select_top_n_with_distribution(cues, n=10, duration=600.0)
    # At least 30% of selected cues should fall in the second half (>= 300s).
    second_half = sum(1 for kf in result if kf["timestamp"] >= 300)
    assert second_half >= 3, f"Got distribution: {[kf['timestamp'] for kf in result]}"


def test_select_top_n_returns_records_with_timestamp_and_reason():
    cues = [_cue("Look at this 512.40", start=10)]
    result = select_top_n_with_distribution(cues, n=15, duration=60.0)
    assert result[0]["timestamp"] == 10.0
    assert "reason" in result[0]
    assert isinstance(result[0]["reason"], str)
    assert len(result[0]["reason"]) > 0


def test_select_top_n_empty_cues():
    assert select_top_n_with_distribution([], n=15, duration=60.0) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_select_keyframes.py -v`
Expected: FAIL — `ImportError: cannot import name 'select_top_n_with_distribution'`.

- [ ] **Step 3: Implement the selector**

Append to `scripts/select_keyframes.py`:

```python
def select_top_n_with_distribution(
    cues: list[Cue],
    n: int,
    duration: float,
) -> list[dict]:
    """Pick up to n keyframe records, balancing score with even temporal coverage.

    Algorithm: divide the video into n equal time bins. For each bin, take the
    highest-scoring cue. If a bin has no cue, skip it. Falls back to even spacing
    via lowest-score (zero) cues if needed to fill the bin coverage.
    """
    if not cues or n <= 0 or duration <= 0:
        return []

    bin_size = duration / n
    bins: list[list[Cue]] = [[] for _ in range(n)]
    for cue in cues:
        idx = min(int(cue.start_seconds // bin_size), n - 1)
        bins[idx].append(cue)

    selections: list[dict] = []
    for i, bin_cues in enumerate(bins):
        if not bin_cues:
            continue
        best = max(bin_cues, key=lambda c: (score_cue(c), -c.start_seconds))
        reason = best.text if score_cue(best) > 0 else f"even-spacing fallback (~{int(best.start_seconds)}s)"
        selections.append({
            "timestamp": best.start_seconds,
            "reason": reason,
            "score": score_cue(best),
        })

    selections.sort(key=lambda s: s["timestamp"])
    return selections[:n]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_select_keyframes.py -v`
Expected: PASS — all 11 tests green.

- [ ] **Step 5: Commit**

```bash
git add scripts/select_keyframes.py tests/test_select_keyframes.py
git commit -m "feat(keyframes): bin-based top-n selection with temporal distribution"
```

---

## Task 8: `select_keyframes.py` — `main` + JSON output

**Files:**
- Modify: `scripts/select_keyframes.py`
- Modify: `tests/test_select_keyframes.py` (append)

- [ ] **Step 1: Append failing test**

```python
import json
from scripts.select_keyframes import select_keyframes_to_file


def test_select_keyframes_to_file_writes_json(tmp_path):
    out_path = tmp_path / "keyframes.json"
    result = select_keyframes_to_file(
        captions_path=FIXTURES / "sample.vtt",
        meta_path=FIXTURES / "sample_meta.json",
        n=5,
        out_path=out_path,
    )
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert "keyframes" in data
    assert len(data["keyframes"]) <= 5
    assert all("timestamp" in kf and "reason" in kf for kf in data["keyframes"])
    assert result["count"] == len(data["keyframes"])
    assert result["keyframes_path"] == str(out_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_select_keyframes.py::test_select_keyframes_to_file_writes_json -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement `select_keyframes_to_file` and CLI main**

Append to `scripts/select_keyframes.py`:

```python
import json
import sys


def select_keyframes_to_file(
    captions_path: Path,
    meta_path: Path,
    n: int,
    out_path: Path,
) -> dict:
    """End-to-end: parse captions, load meta, select keyframes, write JSON."""
    cues = parse_vtt(Path(captions_path))
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    duration = float(meta.get("duration", 0))
    if duration <= 0 and cues:
        duration = max(c.end_seconds for c in cues)
    keyframes = select_top_n_with_distribution(cues, n=n, duration=duration)
    Path(out_path).write_text(json.dumps({"keyframes": keyframes}, indent=2), encoding="utf-8")
    return {"count": len(keyframes), "keyframes_path": str(out_path)}


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: select_keyframes.py <captions.vtt> <meta.json> <n> [out.json]", file=sys.stderr)
        return 2
    captions = Path(sys.argv[1])
    meta = Path(sys.argv[2])
    n = int(sys.argv[3])
    out = Path(sys.argv[4]) if len(sys.argv) > 4 else captions.parent / "keyframes.json"
    result = select_keyframes_to_file(captions, meta, n, out)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_select_keyframes.py -v`
Expected: PASS — all 12 tests green.

- [ ] **Step 5: Commit**

```bash
git add scripts/select_keyframes.py tests/test_select_keyframes.py
git commit -m "feat(keyframes): end-to-end select_keyframes_to_file with JSON output"
```

---

## Task 9: `extract_frames.py` — pure helpers (`format_timestamp_label`, `build_ffmpeg_command`)

**Files:**
- Create: `scripts/extract_frames.py`
- Test: `tests/test_extract_frames.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_extract_frames.py
from pathlib import Path
import pytest

from scripts.extract_frames import format_timestamp_label, build_ffmpeg_command


@pytest.mark.parametrize("seconds,expected", [
    (0, "00-00-00"),
    (5, "00-00-05"),
    (83, "00-01-23"),
    (3725, "01-02-05"),
    (3661.7, "01-01-01"),
])
def test_format_timestamp_label(seconds, expected):
    assert format_timestamp_label(seconds) == expected


def test_build_ffmpeg_command_uses_fast_seek_before_input():
    cmd = build_ffmpeg_command(
        video_path=Path("/cache/video.mp4"),
        timestamp_seconds=83.0,
        out_path=Path("/cache/frames/00-01-23.png"),
    )
    assert cmd[0] == "ffmpeg"
    ss_idx = cmd.index("-ss")
    i_idx = cmd.index("-i")
    assert ss_idx < i_idx, "Fast seek requires -ss before -i"
    assert cmd[ss_idx + 1] == "83.0" or cmd[ss_idx + 1] == "83"
    assert "-frames:v" in cmd
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert "-y" in cmd
    assert cmd[-1] == "/cache/frames/00-01-23.png"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_extract_frames.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement the pure helpers**

```python
# scripts/extract_frames.py
"""Extract PNG frames at given timestamps from a YouTube video using yt-dlp + ffmpeg."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts._lib import extract_video_id, cache_dir_for


def format_timestamp_label(seconds: float) -> str:
    """Format seconds as 'HH-MM-SS' (filesystem-safe)."""
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}-{m:02d}-{s:02d}"


def build_ffmpeg_command(video_path: Path, timestamp_seconds: float, out_path: Path) -> list[str]:
    """Build ffmpeg arg list. -ss before -i for fast seek."""
    return [
        "ffmpeg",
        "-y",                              # overwrite output
        "-ss", str(timestamp_seconds),     # fast seek (before -i)
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",                       # high quality
        str(out_path),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_extract_frames.py -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Commit**

```bash
git add scripts/extract_frames.py tests/test_extract_frames.py
git commit -m "feat(frames): format_timestamp_label + build_ffmpeg_command"
```

---

## Task 10: `extract_frames.py` — video download + frame extraction with mocked subprocess

**Files:**
- Modify: `scripts/extract_frames.py`
- Modify: `tests/test_extract_frames.py` (append)

- [ ] **Step 1: Append failing tests**

```python
import subprocess
from unittest.mock import patch, call

from scripts.extract_frames import ensure_video_downloaded, extract_frames


def test_ensure_video_downloaded_skips_if_exists(tmp_path):
    cache = tmp_path / "id"
    cache.mkdir()
    video = cache / "video.mp4"
    video.write_bytes(b"existing")
    with patch("scripts.extract_frames.subprocess.run") as mock_run:
        result = ensure_video_downloaded("https://youtu.be/abc", cache)
    assert result == video
    mock_run.assert_not_called()


def test_ensure_video_downloaded_calls_yt_dlp(tmp_path):
    cache = tmp_path / "id"
    cache.mkdir()

    def fake_run(*args, **kwargs):
        (cache / "video.mp4").write_bytes(b"downloaded")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("scripts.extract_frames.subprocess.run", side_effect=fake_run) as mock_run:
        result = ensure_video_downloaded("https://youtu.be/abc", cache)

    args = mock_run.call_args[0][0]
    assert "yt-dlp" in args[0]
    assert "--no-playlist" in args
    assert "https://youtu.be/abc" in args
    assert result == cache / "video.mp4"


def test_extract_frames_runs_ffmpeg_per_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "abcdefghij1"
    cache = tmp_path / video_id
    cache.mkdir()
    (cache / "video.mp4").write_bytes(b"fake")
    keyframes_path = cache / "keyframes.json"
    import json as _json
    keyframes_path.write_text(_json.dumps({
        "keyframes": [
            {"timestamp": 1.0, "reason": "intro"},
            {"timestamp": 83.0, "reason": "ORB chart"},
        ]
    }))

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[0] == "ffmpeg":
            out = Path(args[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"PNG")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("scripts.extract_frames.subprocess.run", side_effect=fake_run):
        result = extract_frames(f"https://youtu.be/{video_id}", keyframes_path)

    assert result["count"] == 2
    assert (cache / "frames" / "00-00-01.png").exists()
    assert (cache / "frames" / "00-01-23.png").exists()
    ffmpeg_calls = [c for c in calls if c[0] == "ffmpeg"]
    assert len(ffmpeg_calls) == 2


def test_extract_frames_skips_failing_frame_continues(tmp_path, monkeypatch):
    """One ffmpeg call fails — other frames still extracted, no exception bubbles."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "abcdefghij1"
    cache = tmp_path / video_id
    cache.mkdir()
    (cache / "video.mp4").write_bytes(b"fake")
    keyframes_path = cache / "keyframes.json"
    import json as _json
    keyframes_path.write_text(_json.dumps({
        "keyframes": [
            {"timestamp": 1.0, "reason": "ok"},
            {"timestamp": 99.0, "reason": "will-fail"},
            {"timestamp": 200.0, "reason": "ok"},
        ]
    }))

    def fake_run(args, **kwargs):
        if args[0] == "ffmpeg" and "99.0" in args:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="bad seek")
        if args[0] == "ffmpeg":
            out = Path(args[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"PNG")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("scripts.extract_frames.subprocess.run", side_effect=fake_run):
        result = extract_frames(f"https://youtu.be/{video_id}", keyframes_path)

    assert result["count"] == 2
    assert result["skipped"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_extract_frames.py -v`
Expected: FAIL — `ImportError: cannot import name 'ensure_video_downloaded'`.

- [ ] **Step 3: Implement download + extraction**

Append to `scripts/extract_frames.py`:

```python
def ensure_video_downloaded(url: str, cache: Path) -> Path:
    """Download video to cache/video.mp4 unless it already exists."""
    video_path = cache / "video.mp4"
    if video_path.exists():
        return video_path
    cache.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format", "mp4/bestvideo+bestaudio",
        "--merge-output-format", "mp4",
        "--output", str(video_path),
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not video_path.exists():
        raise RuntimeError(f"yt-dlp video download failed: {proc.stderr.strip()}")
    return video_path


def extract_frames(url: str, keyframes_path: Path) -> dict:
    """Read keyframes.json, ensure video, extract one PNG per timestamp.

    Failing frames are skipped (logged in 'skipped' count); the run continues.
    """
    video_id = extract_video_id(url)
    cache = cache_dir_for(video_id)
    video_path = ensure_video_downloaded(url, cache)
    frames_dir = cache / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    keyframes = json.loads(Path(keyframes_path).read_text(encoding="utf-8"))["keyframes"]
    extracted: list[dict] = []
    skipped = 0
    for kf in keyframes:
        ts = float(kf["timestamp"])
        label = format_timestamp_label(ts)
        out = frames_dir / f"{label}.png"
        cmd = build_ffmpeg_command(video_path, ts, out)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not out.exists():
            skipped += 1
            continue
        extracted.append({"timestamp": ts, "label": label, "path": str(out), "reason": kf.get("reason", "")})
    return {"count": len(extracted), "skipped": skipped, "frames": extracted}


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: extract_frames.py <youtube-url> <keyframes.json>", file=sys.stderr)
        return 2
    result = extract_frames(sys.argv[1], Path(sys.argv[2]))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_extract_frames.py -v`
Expected: PASS — all 10 tests green.

- [ ] **Step 5: Confirm full test suite still green**

Run: `python -m pytest -v`
Expected: PASS — all tests across all files green.

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_frames.py tests/test_extract_frames.py
git commit -m "feat(frames): ensure_video_downloaded + extract_frames with skip-on-failure"
```

---

## Task 11: `agent/youtube-analyzer.md` — subagent definition

**Files:**
- Create: `agent/youtube-analyzer.md`

This file is the agent prompt — read by Claude when the agent is dispatched. It is not unit-tested; verified manually via fixture URLs (Task 15).

- [ ] **Step 1: Write the agent file**

```markdown
---
name: youtube-analyzer
description: Subagent that downloads YouTube captions, extracts chart keyframes, vision-captions them, and assembles a structured comprehension report. Invoked by /yt-comprehend or by research-before-building when a YouTube URL is in scope.
tools: Bash, Read, Write
---

# YouTube Analyzer Subagent

You are a focused subagent. Your only job is to produce a single comprehension report for one YouTube video and return its path. Intermediate state (transcripts, frame descriptions, scratch reasoning) stays in your context — only the final report path and a one-line summary are returned.

## Inputs

You will be invoked with:
- `url`: a YouTube video URL (single video — not a playlist or channel)
- `output_path` (optional): where the final report should be written. Defaults to `<cwd>/docs/research/yt/<video-id>-<slug>.md`.
- `force` (optional, bool): if true, ignore the cache and regenerate.

## Pipeline

Execute these steps in order. Stop and return on the first hard failure.

### 1. Validate + cache check

- Parse the video ID from the URL yourself using the same patterns the script uses (`watch?v=`, `youtu.be/`, `embed/`, `shorts/`) — 11-char ID after the marker.
- Determine the cache dir: `~/.cache/yt-comprehend/<video-id>/` (Windows: `$env:USERPROFILE/.cache/yt-comprehend/<video-id>/`).
- If `<cache>/report.md` exists, `<cache>/meta.json` exists, `meta.json.schema_version == 1`, and `force` is not set: output the report path + first paragraph as the one-line summary, and stop.
- If `report.md` exists but `meta.json.schema_version` is missing or != 1: skip step 2 (captions are schema-independent), but regenerate the report from step 3 onward, reusing cached frames if present.

### 2. Fetch captions + metadata

- Run: `python -m scripts.fetch_captions <url>`
- Parse the stdout JSON. You now have `meta_path` and `captions_path`.
- If the script exits non-zero with a "No captions available" message, hard-fail with that message. Do not attempt fallback.

### 3. Self-prompt: detect video type and write a focus prompt

- Read the metadata JSON (`meta_path`).
- Read the first ~300 lines of `captions_path`.
- Based on title, channel name, and the opening of the transcript, write a focus prompt for yourself in 2–4 sentences. Examples:
  - Trading content: *"This is a momentum-trading tutorial focused on opening-range breakouts. Look for: chart setups shown on screen, specific tickers/levels mentioned, indicator settings, entry/exit logic, risk-management notes."*
  - Coding tutorial: *"This is a hands-on coding tutorial about building a CLI in Rust. Look for: commands typed, files edited, named libraries/crates, design decisions explained, gotchas called out."*
  - Conference talk: *"This is a conference talk on distributed systems. Look for: named techniques/papers cited, slides shown, claims with evidence, questions raised in Q&A."*
- Save this focus prompt — it will appear verbatim in the report and will guide your frame captioning.

### 4. Select keyframes

- Run: `python -m scripts.select_keyframes <captions_path> <meta_path> 15 <cache>/keyframes.json`
- Verify the output JSON has at least 1 keyframe. If 0, fall back to even spacing (call the script again with a higher cap or accept the empty list and note in the report that no visual analysis was possible).

### 5. Extract frames

- Run: `python -m scripts.extract_frames <url> <cache>/keyframes.json`
- Parse stdout. You now have a list of `{timestamp, label, path, reason}` objects under `frames`.
- If `skipped` > 0, note this in the report's "Open caveats" area.

### 6. Vision-caption each frame

For each frame in the list (in chronological order):
- Use `Read` on `frame.path` to load the image.
- Generate a 2–4 sentence description, guided by the focus prompt. Describe: what's on screen, what chart/setup/code/slide is visible, any text or numbers readable. Keep descriptions concrete; avoid speculation.
- Note the frame's number (1..N), timestamp (HH:MM:SS), and the reason it was selected.

### 7. Compose section summaries

Segment the video into 5–10 logical sections based on the transcript. Identify section boundaries by topic shifts (introductions, methodology, examples, Q&A, conclusion). For each section:
- Timestamp range
- Header phrase (3–6 words)
- 3–5 sentence summary
- References to relevant keyframes by number, e.g. `[Keyframe 3]`

### 8. Extract specifics

Based on the focus prompt, populate a flat structured list. Categories adapt to video type:

- Trading: tickers/symbols, levels/numbers, indicators/settings, named strategies, tools/platforms
- Coding: commands, files/paths, libraries/packages, named techniques, configuration values
- Talks/general: named techniques/concepts, citations/references, claims, terminology

Only include items that are *actually* mentioned. Empty categories are fine.

### 9. Extract claims & evidence

Identify the claims the speaker makes. For each, tag with one of:
- **★★★ Demonstrated** — backed by something shown on screen (backtest result, citation, on-chart evidence, code that runs)
- **★★ Anecdotal** — specific but unverifiable (named example, personal track record)
- **★ Asserted** — bare claim, no evidence shown

Cite timestamps and keyframe numbers where applicable.

### 10. Generate suggested research queries

Write 3–5 explicit triangulation questions for the research skill. They should target the most load-bearing or most surprising claims. Format: imperative or interrogative one-liners.

### 11. Assemble the report

Write to the resolved output path. Use exactly this structure:

```markdown
---
video_id: <id>
title: "<title>"
channel: "<uploader>"
duration: "<HH:MM:SS or MM:SS>"
uploaded: "<YYYY-MM-DD>"
url: <url>
generated: "<ISO 8601 UTC now>"
keyframe_count: <int>
schema_version: 1
truncated: <bool>
focus_prompt: "<the prompt from step 3>"
---

# <title>

## TL;DR
<3–5 sentence thesis>

## Focus lens
<the focus prompt from step 3, verbatim>

## Specifics
<flat list, categories from step 8>

## Key visual moments
### [1] HH:MM:SS — <one-line description>
> **Why selected:** <reason from keyframe>
>
> **What's on screen:** <2–4 sentence description from step 6>

(repeat per frame)

## Section summaries
### HH:MM – HH:MM — <header> [Keyframe N]
<3–5 sentence summary>

(repeat per section)

## Claims & evidence
- **★** / **★★** / **★★★** <claim> [HH:MM:SS, Keyframe N]

(repeat per claim)

## Suggested research queries
- <query 1>
- <query 2>
...

---

## Appendix: Full transcript

[HH:MM:SS] <line>
[HH:MM:SS] <line>
...
```

The transcript appendix can be reformatted from the VTT cues — one line per cue, prefixed with `[HH:MM:SS]`.

### 12. Save and return

- Ensure the output directory exists; write the report.
- Also save a copy at `<cache>/report.md` so future runs hit the cache.
- Return to the caller a JSON object with two fields:
  - `report_path`: the absolute path of the report
  - `summary`: a single sentence (~25 words) summarizing the video. This is what the parent session will see.

## Hard rules

- Do NOT include the full transcript in your response to the caller. The caller gets the report path; the transcript is in the appendix.
- Do NOT re-process a cached video unless `force` is set.
- If captions are missing, fail with the exact spec message: *"No captions available for `<video-id>`. Skill v1 doesn't fall back to ASR; try a video with captions enabled."*
- If yt-dlp/ffmpeg are missing, fail with the install message — do not try to install them yourself.
- Cap keyframes at 15 in normal mode. If the user explicitly requests more, set `truncated: false` only if the cap was not exceeded.
```

- [ ] **Step 2: Commit**

```bash
git add agent/youtube-analyzer.md
git commit -m "feat(agent): youtube-analyzer subagent definition with full pipeline"
```

---

## Task 12: `skill/SKILL.md` — slash skill entry point

**Files:**
- Create: `skill/SKILL.md`

- [ ] **Step 1: Write the skill file**

```markdown
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
- `yt-dlp --version`
- `ffmpeg -version`

If either is missing, print this message and STOP — do **not** dispatch the agent:

```
Missing required tools. Install:
  winget install yt-dlp.yt-dlp
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
```

- [ ] **Step 2: Commit**

```bash
git add skill/SKILL.md
git commit -m "feat(skill): /yt-comprehend slash skill with URL+tool validation"
```

---

## Task 13: `install.ps1` and `uninstall.ps1`

**Files:**
- Create: `install.ps1`
- Create: `uninstall.ps1`

These are tested manually — PowerShell scripts that copy files. Failure modes are obvious (wrong paths, missing tools).

- [ ] **Step 1: Write `install.ps1`**

```powershell
# install.ps1 — copy yt-comprehend skill + agent into ~/.claude/
$ErrorActionPreference = 'Stop'

$repoRoot = $PSScriptRoot
$claudeRoot = Join-Path $env:USERPROFILE '.claude'
$skillDest = Join-Path $claudeRoot 'skills/yt-comprehend'
$agentDest = Join-Path $claudeRoot 'agents'

Write-Host '== yt-comprehend installer ==' -ForegroundColor Cyan

# 1. Tool checks
$missing = @()
foreach ($tool in @('yt-dlp', 'ffmpeg', 'python')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        $missing += $tool
    }
}
if ($missing.Count -gt 0) {
    Write-Host ''
    Write-Host "Missing tools: $($missing -join ', ')" -ForegroundColor Red
    Write-Host ''
    Write-Host 'Install commands:'
    if ($missing -contains 'yt-dlp') { Write-Host '  winget install yt-dlp.yt-dlp' }
    if ($missing -contains 'ffmpeg') { Write-Host '  winget install Gyan.FFmpeg' }
    if ($missing -contains 'python') { Write-Host '  winget install Python.Python.3.12' }
    exit 1
}

# 2. Python version check (>= 3.10)
$pyVer = (& python -c "import sys; print('.'.join(str(x) for x in sys.version_info[:2]))").Trim()
$pyMajor, $pyMinor = $pyVer -split '\.'
if ([int]$pyMajor -lt 3 -or ([int]$pyMajor -eq 3 -and [int]$pyMinor -lt 10)) {
    Write-Host "Python 3.10+ required; found $pyVer" -ForegroundColor Red
    exit 1
}

# 3. Copy files (idempotent — overwrites)
New-Item -ItemType Directory -Force -Path $skillDest | Out-Null
New-Item -ItemType Directory -Force -Path $agentDest | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $skillDest 'scripts') | Out-Null

Copy-Item -Force (Join-Path $repoRoot 'skill/SKILL.md') (Join-Path $skillDest 'SKILL.md')
Copy-Item -Force (Join-Path $repoRoot 'agent/youtube-analyzer.md') (Join-Path $agentDest 'youtube-analyzer.md')
Copy-Item -Force -Recurse (Join-Path $repoRoot 'scripts/*.py') (Join-Path $skillDest 'scripts')

Write-Host ''
Write-Host 'Installed:' -ForegroundColor Green
Write-Host "  $skillDest"
Write-Host "  $(Join-Path $agentDest 'youtube-analyzer.md')"
Write-Host ''
Write-Host 'Use /yt-comprehend <url> in any Claude Code session.'
```

- [ ] **Step 2: Write `uninstall.ps1`**

```powershell
# uninstall.ps1 — remove yt-comprehend from ~/.claude/
$ErrorActionPreference = 'Stop'

$claudeRoot = Join-Path $env:USERPROFILE '.claude'
$skillDest = Join-Path $claudeRoot 'skills/yt-comprehend'
$agentFile = Join-Path $claudeRoot 'agents/youtube-analyzer.md'

if (Test-Path $skillDest) {
    Remove-Item -Recurse -Force $skillDest
    Write-Host "Removed $skillDest"
}
if (Test-Path $agentFile) {
    Remove-Item -Force $agentFile
    Write-Host "Removed $agentFile"
}
Write-Host ''
Write-Host 'Cache at ~/.cache/yt-comprehend was NOT removed. To delete:'
Write-Host "  Remove-Item -Recurse -Force '$env:USERPROFILE\.cache\yt-comprehend'"
```

- [ ] **Step 3: Manually run install.ps1 and verify**

Run: `.\install.ps1`
Expected output: `Installed:` block listing the three paths.

Verify files exist:

```powershell
Test-Path "$env:USERPROFILE\.claude\skills\yt-comprehend\SKILL.md"      # True
Test-Path "$env:USERPROFILE\.claude\agents\youtube-analyzer.md"          # True
Test-Path "$env:USERPROFILE\.claude\skills\yt-comprehend\scripts\_lib.py" # True
```

- [ ] **Step 4: Run install.ps1 a second time (idempotency check)**

Run: `.\install.ps1`
Expected: same `Installed:` output, no errors. (Files get overwritten cleanly.)

- [ ] **Step 5: Commit**

```bash
git add install.ps1 uninstall.ps1
git commit -m "feat(install): PowerShell installer with tool checks and idempotent copy"
```

---

## Task 14: Finalize `README.md`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the placeholder README with the real one**

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: write full README with install/usage/limits"
```

---

## Task 15: Manual end-to-end smoke test

**Files:**
- Create: `tests/fixtures.txt` (with real URLs)
- Modify: `README.md` (record results)

This task is a manual validation pass. No code change other than fixtures.

- [ ] **Step 1: Pick three real fixture URLs**

Edit `tests/fixtures.txt`. Choose URLs you have permission to test against (your own bookmarks or well-known public videos). Format:

```
# tests/fixtures.txt
# Manual smoke-test URLs. Used by Task 15 of the implementation plan.

with-captions: <pick a public trading video with captions>
no-captions:   <pick a public video without captions>
region-locked: <pick a known region-locked video, or omit if unsure>
```

If you're unsure which URLs to pick: a popular conference talk on YouTube usually has captions; a small private upload often doesn't.

- [ ] **Step 2: Run the with-captions fixture**

In a fresh Claude Code session: `/yt-comprehend <with-captions URL>`

Expected:
- Skill validates URL and tools, dispatches agent.
- Agent produces a report at `docs/research/yt/<id>-<slug>.md`.
- Output includes: TL;DR, Focus lens, Specifics (with at least one populated category), Key visual moments (≥1 keyframe), Section summaries (≥3 sections), Claims & evidence, Suggested research queries, Appendix.
- Frontmatter `truncated: false`.
- `~/.cache/yt-comprehend/<id>/` contains `meta.json`, `captions.vtt`, `video.mp4`, `frames/*.png`, `report.md`.

- [ ] **Step 3: Re-run the same URL (cache-hit verification)**

`/yt-comprehend <with-captions URL>`

Expected:
- Returns the same `report_path` immediately.
- No yt-dlp or ffmpeg subprocess runs (no second download).

- [ ] **Step 4: Run the no-captions fixture**

`/yt-comprehend <no-captions URL>`

Expected:
- Skill validates URL + tools, dispatches agent.
- Agent fails cleanly with: `"No captions available for <video-id>. Skill v1 doesn't fall back to ASR; try a video with captions enabled."`
- No partial report written.

- [ ] **Step 5: Run the region-locked fixture (if available)**

`/yt-comprehend <region-locked URL>`

Expected:
- yt-dlp's region-lock error is surfaced verbatim through the skill.
- No partial report written.

- [ ] **Step 6: Append a "Validation Results" section to `README.md`**

Document the date and the three results:

```markdown
## Validation Results

Last manual smoke test: 2026-04-26

- ✅ with-captions: <id> — report generated, all sections populated
- ✅ no-captions:   <id> — failed cleanly with spec message
- ✅ region-locked: <id> — yt-dlp error surfaced
```

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures.txt README.md
git commit -m "test(smoke): record manual validation against three fixture URLs"
```

---

## Self-review

Before declaring the plan complete, the implementer should sanity-check:

1. **All spec sections covered:**
   - Architecture (skill + agent + 3 scripts) → Tasks 11, 12, 2–10
   - Pipeline (8 steps) → Task 11 (agent definition encodes the pipeline) + Tasks 3–10 (scripts that the agent invokes)
   - File layout (source + installed + runtime) → Task 1 (source scaffold), Task 13 (installer to installed layout)
   - Output schema (8 sections + appendix) → Task 11 (encoded in agent prompt)
   - Error handling (table of 7 cases) → Task 4 (URL/captions/yt-dlp errors), Task 10 (ffmpeg per-frame skip), Task 11 (agent surfaces hard-fail messages), Task 12 (skill-level tool check)
   - Caching (key, hit, force, no-eviction, schema-version) → Task 3 (cache hit short-circuit), Task 11 (force flag in agent), Task 14 (README documents cache clearing)
   - Edge cases (long videos, live/playlist) → Task 11 (agent handles), Task 12 (skill rejects non-video URLs)
   - Testing (3 fixtures, unit tests for scripts, manual for agent) → Tasks 2–10 (unit tests), Task 15 (manual fixtures)

2. **No placeholders.** Every step has the actual code or command.

3. **Type/name consistency:**
   - `extract_video_id`, `cache_dir_for`, `slugify` — same names across all consumers.
   - `fetch_captions`, `select_keyframes_to_file`, `extract_frames`, `ensure_video_downloaded` — top-level functions, consistent.
   - `parse_vtt`, `score_cue`, `select_top_n_with_distribution` — internal helpers, consistent.
   - `Cue` dataclass — used in tests and module.
   - `format_timestamp_label`, `build_ffmpeg_command` — used in tests and module.
   - JSON output keys consistent: `video_id`, `meta_path`, `captions_path`, `cached`, `count`, `keyframes_path`, `skipped`, `frames`, `report_path`, `summary`.
