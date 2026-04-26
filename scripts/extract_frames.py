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


def ensure_video_downloaded(url: str, cache: Path) -> Path:
    """Download video to cache/video.mp4 unless it already exists."""
    video_path = cache / "video.mp4"
    if video_path.exists():
        return video_path
    cache.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "yt_dlp",
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
