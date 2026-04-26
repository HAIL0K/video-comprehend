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
