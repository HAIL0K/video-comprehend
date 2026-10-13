# scripts/dedup_frames.py
"""Perceptual deduplication of extracted frames, with a chart-aware local channel.

crv deduplicates on whole-image pixel deltas. That is right for general video and
wrong for charts: a chart that gained one trendline moves well under a whole-image
threshold and gets dropped, while every cutaway to a talking head clears it easily.

So two channels are compared, and a frame is KEPT if either fires:

  global  mean absolute difference over a 32x32 RGB thumbnail
          -- "is this a different shot?"
  tile    the single largest per-tile difference over an 8x8 grid
          -- "did one region change a lot while the rest held still?"
             (a new trendline, a fresh annotation, one changed number)

Both are compared against the last N kept frames rather than only the previous one,
so a shot the model already saw does not come back after a cutaway.

Frames anchored to an explicit verbal callout are protected: if the speaker said
"look at this", a pixel threshold does not get to overrule them.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

THUMB = 32
TILE_GRID = 8
LOOKBACK = 5


@dataclass(frozen=True)
class Profile:
    """Dedup sensitivity. Lower thresholds keep more frames."""
    name: str
    global_threshold: float
    tile_threshold: float


PROFILES = {
    # Loose global (chart frames all look alike overall) plus a very sensitive tile
    # channel, so a single new drawing survives.
    "chart": Profile("chart", global_threshold=0.020, tile_threshold=0.055),
    # crv-like: a frame must actually look different to earn a slot.
    "general": Profile("general", global_threshold=0.060, tile_threshold=0.140),
}


def _load_array(path: Path):
    from PIL import Image
    import numpy as np

    with Image.open(path) as img:
        rgb = img.convert("RGB").resize((THUMB, THUMB), Image.BILINEAR)
        return np.asarray(rgb, dtype="float32") / 255.0


def global_delta(a, b) -> float:
    """Mean absolute difference across the whole thumbnail, 0..1."""
    import numpy as np

    return float(np.abs(a - b).mean())


def max_tile_delta(a, b, grid: int = TILE_GRID) -> float:
    """Largest mean-absolute-difference of any single tile, 0..1."""
    import numpy as np

    step = max(1, THUMB // grid)
    worst = 0.0
    for y in range(0, THUMB, step):
        for x in range(0, THUMB, step):
            tile = np.abs(a[y:y + step, x:x + step] - b[y:y + step, x:x + step]).mean()
            if float(tile) > worst:
                worst = float(tile)
    return worst


def is_novel(candidate, kept: list, profile: Profile):
    """Decide whether `candidate` adds anything over the last few kept frames.

    Returns (keep, reason, closest_global, closest_tile). A frame is dropped only if
    it is near-identical to some recent kept frame on BOTH channels at once.
    """
    if not kept:
        return True, "first frame", 1.0, 1.0
    closest_global = 1.0
    closest_tile = 1.0
    for previous in kept[-LOOKBACK:]:
        g = global_delta(candidate, previous)
        t = max_tile_delta(candidate, previous)
        if g < closest_global:
            closest_global = g
        if t < closest_tile:
            closest_tile = t
        if g <= profile.global_threshold and t <= profile.tile_threshold:
            return False, "near-duplicate of a recent kept frame", g, t
    if closest_global > profile.global_threshold:
        return True, "new shot", closest_global, closest_tile
    return True, "local change within a similar shot", closest_global, closest_tile


def dedup(frames: list, profile_name: str = "chart", protect=None) -> dict:
    """Filter a chronological frame list.

    Each input frame needs at least a "path". Frames whose "source" is in `protect`
    are never dropped. Unreadable frames are kept and flagged rather than discarded.
    """
    profile = PROFILES.get(profile_name, PROFILES["chart"])
    protect = set(protect or ())
    kept_arrays: list = []
    kept_frames: list = []
    dropped: list = []

    for frame in frames:
        try:
            array = _load_array(Path(frame["path"]))
        except Exception as exc:
            kept_frames.append(
                {**frame, "dedup": "unreadable ({0}), kept".format(type(exc).__name__)}
            )
            continue
        keep, reason, g, t = is_novel(array, kept_arrays, profile)
        if not keep and frame.get("source") in protect:
            keep, reason = True, "protected: explicit verbal callout"
        annotated = {
            **frame,
            "dedup": reason,
            "global_delta": round(g, 4),
            "tile_delta": round(t, 4),
        }
        if keep:
            kept_arrays.append(array)
            kept_frames.append(annotated)
        else:
            dropped.append(annotated)

    return {
        "profile": profile.name,
        "kept": len(kept_frames),
        "dropped_count": len(dropped),
        "frames": kept_frames,
        "dropped": dropped,
    }


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: dedup_frames.py <frames.json> <profile> [out.json]", file=sys.stderr)
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = dedup(payload["frames"], sys.argv[2], protect={"semantic"})
    if len(sys.argv) > 3:
        Path(sys.argv[3]).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("frames", "dropped")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
