# scripts/detect_scenes.py
"""Content-agnostic frame candidates: ffmpeg scene-change detection + a density floor.

This is the half of the pipeline that does not care what the speaker says. It catches
slides, silent screen shares, code appearing mid-pause and chart annotations drawn
without narration -- exactly what caption-anchored selection misses.

Detection runs on a downscaled, low-fps decode so a one-hour video costs seconds, not
minutes. It is a *candidate generator*: ranking and the final budget live in
select_keyframes.py.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_PTS_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")

DETECT_FPS = 4
DETECT_WIDTH = 320


def build_scene_command(
    video_path: Path,
    threshold: float = 0.30,
    fps: int = DETECT_FPS,
    width: int = DETECT_WIDTH,
) -> list[str]:
    """ffmpeg args for a scene-change scan. Decodes to null; only showinfo is parsed."""
    vf = "scale={w}:-2,fps={f},select='gt(scene,{t})',showinfo".format(
        w=width, f=fps, t=threshold
    )
    return [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(video_path),
        "-vf", vf,
        "-an", "-f", "null", "-",
    ]


def parse_showinfo(stderr: str) -> list[float]:
    """Pull pts_time values out of ffmpeg showinfo output, sorted and deduplicated."""
    seen: set[float] = set()
    for match in _PTS_RE.finditer(stderr or ""):
        seen.add(round(float(match.group(1)), 3))
    return sorted(seen)


def density_floor(duration: float, interval: float) -> list[float]:
    """Evenly spaced timestamps so long static stretches still get looked at.

    Starts at interval/2 to land mid-segment rather than on a hard cut.
    """
    if duration <= 0 or interval <= 0:
        return []
    out: list[float] = []
    t = interval / 2.0
    while t < duration:
        out.append(round(t, 3))
        t += interval
    return out


def detect_scenes(
    video_path: Path,
    duration: float,
    threshold: float = 0.30,
    floor_interval: float = 60.0,
) -> dict:
    """Return scene-change and density-floor timestamp candidates, in seconds.

    A failing ffmpeg scan degrades to floor-only rather than killing the run -- the
    semantic channel alone is still a usable pipeline.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError("Video not found: {0}".format(video_path))
    cmd = build_scene_command(video_path, threshold=threshold)
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    scenes = parse_showinfo(proc.stderr) if proc.returncode == 0 else []
    return {
        "scene": scenes,
        "floor": density_floor(duration, floor_interval),
        "scene_detect_ok": proc.returncode == 0,
        "threshold": threshold,
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "usage: detect_scenes.py <video-path> <duration-seconds> "
            "[threshold] [floor-interval] [out.json]",
            file=sys.stderr,
        )
        return 2
    video = Path(sys.argv[1])
    duration = float(sys.argv[2])
    threshold = float(sys.argv[3]) if len(sys.argv) > 3 else 0.30
    floor_interval = float(sys.argv[4]) if len(sys.argv) > 4 else 60.0
    result = detect_scenes(video, duration, threshold, floor_interval)
    if len(sys.argv) > 5:
        Path(sys.argv[5]).write_text(json.dumps(result, indent=2), encoding="utf-8")
        result = {**result, "scene": len(result["scene"]), "floor": len(result["floor"])}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
