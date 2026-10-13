# scripts/select_keyframes.py
"""Union keyframe selection: what the speaker points at + what the picture does.

Three candidate channels are merged:

  semantic  caption cues carrying deixis ("look at this"), domain terms, or numbers
            -- catches the moments the speaker explicitly draws attention to
  scene     ffmpeg scene-change timestamps (detect_scenes.py)
            -- catches slides, silent screen shares, code appearing mid-pause
  floor     evenly spaced timestamps
            -- guarantees no long stretch goes unlooked-at

Candidates within `min_gap` seconds collapse into one, and a cluster that several
channels agree on scores higher than any single channel could -- a scene cut that
lands exactly on "here is the setup" is the single best frame in the video.

The old caption-only selector had a structural blind spot: anything shown without
being narrated was invisible to it, and its keyword list was hard-tuned to trading,
so non-trading video silently degraded to even spacing. Both are fixed here --
scene detection is content-agnostic, and domain terms are injected per run.
"""
from __future__ import annotations

import json
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_TIMESTAMP_LINE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})\.\d{3}\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})\.\d{3}"
)
_HTML_TAG = re.compile(r"<[^>]+>")
_NUMERIC_LEVEL = re.compile(r"\b\d{2,5}(?:\.\d{1,4})?\b")


@dataclass
class Cue:
    start_seconds: float
    end_seconds: float
    text: str


# Verbal pointing. Domain-independent -- these mean "something is on screen right now"
# whether the subject is a chart, a slide, or an IDE.
DEIXIS_PHRASES = [
    "as you can see", "look at this", "look at the", "notice the", "notice how",
    "here's the", "here is the", "take a look", "you can see", "watch what",
    "see this", "see how", "right here", "over here", "on the screen",
    "this is what", "check this", "pay attention to", "if you look",
]

# Injected when profile=chart, or extended per run from the agent's focus prompt.
CHART_TERMS = [
    "chart", "candle", "candlestick", "volume", "vwap", "ema", "sma", "rsi",
    "macd", "level", "support", "resistance", "breakout", "breakdown", "wick",
    "liquidity", "order block", "fair value gap", "fvg", "range", "trendline",
    "timeframe", "entry", "exit", "stop", "target", "position size", "backtest",
    "drawdown", "equity curve", "sharpe", "win rate", "expectancy", "indicator",
]

GENERAL_TERMS = [
    "diagram", "slide", "graph", "table", "architecture", "example", "code",
    "function", "output", "result", "step", "config", "command", "error",
]


@dataclass(frozen=True)
class SelectionProfile:
    """Frame budget shape. `per_minute` is how fast the budget grows with length."""
    name: str
    floor_budget: int
    ceiling_budget: int
    per_minute: float
    floor_interval: float
    terms: tuple


PROFILES = {
    "chart": SelectionProfile(
        name="chart", floor_budget=12, ceiling_budget=40,
        per_minute=0.45, floor_interval=45.0, terms=tuple(CHART_TERMS),
    ),
    "general": SelectionProfile(
        name="general", floor_budget=10, ceiling_budget=25,
        per_minute=0.28, floor_interval=75.0, terms=tuple(GENERAL_TERMS),
    ),
}

MIN_GAP_SECONDS = 3.0
AGREEMENT_BONUS = 1.5
SOURCE_BASE_SCORE = {"semantic": 3.0, "scene": 2.0, "floor": 0.0}


def _hms_to_seconds(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s)


def _unescape_entities(text: str, rounds: int = 3) -> str:
    """Decode HTML entities, including the double-escaped kind.

    Publisher-uploaded caption tracks (as opposed to YouTube auto-captions) routinely
    arrive with `&amp;nbsp;` -- an entity that was escaped a second time somewhere in
    the publishing chain. One unescape pass leaves a literal `&nbsp;` sitting in the
    transcript. Unescape until the text stops changing, then fold the non-breaking
    spaces the last pass produced into ordinary ones so the caller's whitespace
    collapse can see them.
    """
    for _ in range(rounds):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text.replace(" ", " ").replace("​", "")


def parse_vtt(path: Path) -> list:
    """Parse a WebVTT file into a list of Cue. HTML/styling tags stripped."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    cues = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        ts_idx = next(
            (i for i, ln in enumerate(lines) if _TIMESTAMP_LINE.search(ln)), None
        )
        if ts_idx is None:
            continue
        m = _TIMESTAMP_LINE.search(lines[ts_idx])
        start = _hms_to_seconds(m.group(1), m.group(2), m.group(3))
        end = _hms_to_seconds(m.group(4), m.group(5), m.group(6))
        text = " ".join(_HTML_TAG.sub("", ln).strip() for ln in lines[ts_idx + 1:])
        text = _unescape_entities(text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            cues.append(Cue(float(start), float(end), text))
    return cues


LOOKAHEAD = 3
OVERLAP_MEMORY = 60


def dedupe_cues(cues: list) -> list:
    """Drop the rolling repeats YouTube auto-captions emit, for scoring purposes.

    Auto-caption VTT does not merely repeat lines — it grows them: a cue's text
    reappears in the next cue with more words tacked on. Dropping only exact matches
    leaves the corpus roughly half duplicated, and every partial scores again, so the
    same moment wins several bins.

    Two rules, both keeping the LONGEST form of a phrase so multi-word deixis like
    "as you can see" is never split apart:
      * exact repeat within a short window -> drop
      * text that is a strict prefix of a cue just ahead -> drop the shorter one
    """
    out = []
    seen_recent: list = []
    for i, cue in enumerate(cues):
        norm = cue.text.lower().strip()
        if norm in seen_recent:
            continue
        subsumed = False
        for ahead in cues[i + 1:i + 1 + LOOKAHEAD]:
            ahead_norm = ahead.text.lower().strip()
            if len(ahead_norm) > len(norm) and ahead_norm.startswith(norm):
                subsumed = True
                break
        if subsumed:
            continue
        out.append(cue)
        seen_recent.append(norm)
        if len(seen_recent) > 8:
            seen_recent.pop(0)
    return out


def clean_cues(cues: list) -> list:
    """Rebuild a non-redundant transcript from rolling captions, timestamps intact.

    Word-level: for each cue, find the longest overlap between what has already been
    emitted and the start of this cue, and keep only the remainder. Exact repeats
    collapse to nothing and disappear; incremental growth contributes just its new
    words, timestamped where they were actually spoken.

    This is for rendering the transcript appendix, NOT for scoring -- it deliberately
    fragments phrases across cue boundaries, which is the opposite of what
    dedupe_cues wants.
    """
    out = []
    emitted: list = []
    for cue in cues:
        words = cue.text.split()
        if not words:
            continue
        overlap = 0
        for k in range(min(len(emitted), len(words)), 0, -1):
            if [w.lower() for w in emitted[-k:]] == [w.lower() for w in words[:k]]:
                overlap = k
                break
        new_words = words[overlap:]
        if not new_words:
            continue
        out.append(Cue(cue.start_seconds, cue.end_seconds, " ".join(new_words)))
        emitted.extend(new_words)
        if len(emitted) > OVERLAP_MEMORY:
            del emitted[:-OVERLAP_MEMORY]
    return out


def score_cue(cue: Cue, terms) -> float:
    """How likely is this cue to coincide with a frame worth looking at?"""
    text = cue.text.lower()
    has_deixis = any(p in text for p in DEIXIS_PHRASES)
    has_term = any(t in text for t in terms)
    has_number = bool(_NUMERIC_LEVEL.search(text))
    score = 0.0
    if has_deixis:
        score += 2.0
    if has_term:
        score += 1.0
    if has_deixis and has_number:
        score += 2.0
    if has_term and has_number:
        score += 1.0
    return score


def adaptive_budget(duration: float, scene_count: int, profile: SelectionProfile) -> int:
    """Frame budget from video length and how much the picture actually moves.

    A 3-minute clip and a 90-minute chart walkthrough should not get the same 15
    frames -- that was the old hard cap's real failure.
    """
    minutes = max(0.0, duration) / 60.0
    density_bonus = min(8.0, scene_count / 12.0)
    raw = profile.floor_budget + minutes * profile.per_minute + density_bonus
    return int(max(profile.floor_budget, min(profile.ceiling_budget, round(raw))))


def build_candidates(cues: list, scenes: list, floors: list, terms) -> list:
    """One flat candidate list across all three channels, sorted by timestamp."""
    candidates = []
    for cue in cues:
        candidates.append({
            "timestamp": cue.start_seconds,
            "source": "semantic",
            "score": SOURCE_BASE_SCORE["semantic"] + score_cue(cue, terms),
            "reason": cue.text,
        })
    for ts in scenes:
        candidates.append({
            "timestamp": float(ts),
            "source": "scene",
            "score": SOURCE_BASE_SCORE["scene"],
            "reason": "scene change",
        })
    for ts in floors:
        candidates.append({
            "timestamp": float(ts),
            "source": "floor",
            "score": SOURCE_BASE_SCORE["floor"],
            "reason": "coverage floor",
        })
    candidates.sort(key=lambda c: c["timestamp"])
    return candidates


def merge_candidates(candidates: list, min_gap: float = MIN_GAP_SECONDS) -> list:
    """Collapse candidates that land within min_gap into single, better-scored ones.

    Multi-channel agreement is the strongest signal available, so a cluster seen by
    two or more channels gets a bonus above the best single score in it.
    """
    merged = []
    cluster: list = []

    def flush(group):
        if not group:
            return
        sources = {c["source"] for c in group}
        # Prefer the semantically-anchored timestamp; the speaker's callout is a
        # better anchor than the cut, which often precedes what is being discussed.
        for preferred in ("semantic", "scene", "floor"):
            picked = next((c for c in group if c["source"] == preferred), None)
            if picked:
                break
        best_score = max(c["score"] for c in group)
        if len(sources) > 1:
            best_score += AGREEMENT_BONUS
        semantic = next((c for c in group if c["source"] == "semantic"), None)
        merged.append({
            "timestamp": round(picked["timestamp"], 3),
            "source": "+".join(sorted(sources)),
            "score": round(best_score, 3),
            "reason": semantic["reason"] if semantic else picked["reason"],
        })

    for cand in candidates:
        if cluster and cand["timestamp"] - cluster[0]["timestamp"] <= min_gap:
            cluster.append(cand)
        else:
            flush(cluster)
            cluster = [cand]
    flush(cluster)
    return merged


def distribute(merged: list, budget: int, duration: float) -> list:
    """Pick `budget` frames: best per time bin first, then best-scoring leftovers.

    Bin-first guarantees temporal coverage; the leftover pass spends the slack from
    empty bins on the highest-signal moments anywhere in the video, so a dense
    ten-minute stretch is not capped at its share of the timeline.
    """
    if not merged or budget <= 0 or duration <= 0:
        return []
    bin_size = duration / budget
    bins: list = [[] for _ in range(budget)]
    for cand in merged:
        idx = min(int(cand["timestamp"] // bin_size), budget - 1)
        bins[idx].append(cand)

    chosen = []
    chosen_ids = set()
    for bin_cands in bins:
        if not bin_cands:
            continue
        best = max(bin_cands, key=lambda c: (c["score"], -c["timestamp"]))
        chosen.append(best)
        chosen_ids.add(id(best))

    if len(chosen) < budget:
        leftovers = [c for c in merged if id(c) not in chosen_ids]
        leftovers.sort(key=lambda c: (-c["score"], c["timestamp"]))
        for cand in leftovers:
            if len(chosen) >= budget:
                break
            if any(abs(cand["timestamp"] - c["timestamp"]) < MIN_GAP_SECONDS for c in chosen):
                continue
            chosen.append(cand)

    chosen.sort(key=lambda c: c["timestamp"])
    return chosen[:budget]


def select(
    captions_path: Path,
    meta_path: Path,
    scenes_path: Path | None = None,
    profile_name: str = "chart",
    extra_terms=(),
    budget: int | None = None,
    start: float | None = None,
    end: float | None = None,
) -> dict:
    """End-to-end selection. Returns the payload written to keyframes.json."""
    profile = PROFILES.get(profile_name, PROFILES["chart"])
    terms = tuple(profile.terms) + tuple(t.lower().strip() for t in extra_terms if t.strip())

    cues = dedupe_cues(parse_vtt(Path(captions_path))) if Path(captions_path).exists() else []
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8")) if Path(meta_path).exists() else {}
    duration = float(meta.get("duration") or 0)
    if duration <= 0 and cues:
        duration = max(c.end_seconds for c in cues)

    scenes: list = []
    floors: list = []
    if scenes_path and Path(scenes_path).exists():
        scene_data = json.loads(Path(scenes_path).read_text(encoding="utf-8"))
        scenes = scene_data.get("scene", [])
        floors = scene_data.get("floor", [])
    if not floors:
        from scripts.detect_scenes import density_floor
        floors = density_floor(duration, profile.floor_interval)

    window_start = start if start is not None else 0.0
    window_end = end if end is not None else (duration or float("inf"))

    cues = [c for c in cues if window_start <= c.start_seconds <= window_end]
    scenes = [s for s in scenes if window_start <= s <= window_end]
    floors = [f for f in floors if window_start <= f <= window_end]

    effective_duration = min(duration or window_end, window_end) - window_start
    if effective_duration <= 0:
        effective_duration = duration

    final_budget = budget or adaptive_budget(effective_duration, len(scenes), profile)
    candidates = build_candidates(cues, scenes, floors, terms)
    merged = merge_candidates(candidates)
    keyframes = distribute(merged, final_budget, effective_duration or duration)
    # distribute() bins from t=0; shift bins into the window by re-binning on offset.
    if window_start > 0 and not keyframes:
        keyframes = sorted(merged, key=lambda c: -c["score"])[:final_budget]
        keyframes.sort(key=lambda c: c["timestamp"])

    channel_counts = {}
    for kf in keyframes:
        for src in kf["source"].split("+"):
            channel_counts[src] = channel_counts.get(src, 0) + 1

    return {
        "profile": profile.name,
        "budget": final_budget,
        "duration": duration,
        "window": [window_start, None if window_end == float("inf") else window_end],
        "counts": {
            "cues": len(cues),
            "scene_candidates": len(scenes),
            "floor_candidates": len(floors),
            "merged_candidates": len(merged),
            "selected": len(keyframes),
        },
        "channels": channel_counts,
        "keyframes": keyframes,
    }


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Union keyframe selection.")
    ap.add_argument("captions")
    ap.add_argument("meta")
    ap.add_argument("out")
    ap.add_argument("--scenes", default=None)
    ap.add_argument("--profile", default="chart", choices=sorted(PROFILES))
    ap.add_argument("--terms", default="", help="comma-separated extra domain terms")
    ap.add_argument("--budget", type=int, default=None)
    ap.add_argument("--from", dest="start", type=float, default=None)
    ap.add_argument("--to", dest="end", type=float, default=None)
    args = ap.parse_args()

    result = select(
        Path(args.captions), Path(args.meta),
        scenes_path=Path(args.scenes) if args.scenes else None,
        profile_name=args.profile,
        extra_terms=args.terms.split(",") if args.terms else (),
        budget=args.budget,
        start=args.start, end=args.end,
    )
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "keyframes"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
