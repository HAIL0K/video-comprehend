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
