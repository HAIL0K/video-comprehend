# scripts/extract_frames.py
"""Materialise the selected keyframes, then deduplicate what came out.

Differences from the old YouTube-only extractor:
  * any yt-dlp source or a local file, no re-download for local input
  * video-only download capped at 1080p (audio is fetched separately when needed)
  * frames rendered at the profile's width -- 1280px for charts, because 640px
    destroys axis labels, indicator settings and price levels
  * JPEG instead of PNG: same legibility to a vision model, a fraction of the bytes
  * perceptual dedup after extraction, so near-identical frames never reach the model
  * the downloaded video is pruned once frames exist -- the old pipeline left a
    176 MB mp4 per video sitting in the cache forever
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts._lib import (
    resolve_source, cache_dir_for, format_timestamp_label, CLIENT_ATTEMPTS,
)
from scripts.dedup_frames import dedup

PROFILE_WIDTH = {"chart": 1280, "general": 768}
MAX_HEIGHT = 1080


def build_ffmpeg_command(video_path: Path, timestamp: float, out_path: Path, width: int):
    """Fast-seek single-frame extraction. -ss before -i keeps this O(1) per frame."""
    return [
        "ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error",
        "-ss", str(timestamp),
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", "scale={0}:-2:flags=lanczos".format(width),
        "-q:v", "2",
        str(out_path),
    ]


def ensure_video(target: str, cache: Path, verbose: bool = False) -> tuple:
    """Return (path_to_video, downloaded_by_us).

    Local input is read in place. Remote input downloads video-only at <=1080p --
    the audio track is dead weight for frame extraction and doubles the transfer --
    retrying across player clients until one is not blocked.
    """
    source = resolve_source(target)
    if source.is_local:
        return Path(source.target), False

    video_path = cache / "video.mp4"
    if video_path.exists():
        return video_path, True
    cache.mkdir(parents=True, exist_ok=True)
    fmt = (
        "bestvideo[height<={h}][ext=mp4]/bestvideo[height<={h}]/"
        "best[height<={h}]/best"
    ).format(h=MAX_HEIGHT)

    failures = []
    for name, extra in CLIENT_ATTEMPTS:
        proc = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--no-playlist",
             "--format", fmt, "--merge-output-format", "mp4",
             "--output", str(video_path), *extra, source.target],
            capture_output=True, text=True, errors="replace",
        )
        if proc.returncode == 0 and video_path.exists():
            if verbose:
                print("downloaded via player_client={0}".format(name), file=sys.stderr)
            return video_path, True
        tail = (proc.stderr or "").strip().splitlines()
        failures.append("{0}: {1}".format(name, tail[-1][:160] if tail else "no output"))

    raise RuntimeError(
        "video download failed on every player client:\n  " + "\n  ".join(failures)
    )


def extract(
    target: str,
    keyframes_path: Path,
    profile: str = "chart",
    width: int | None = None,
    keep_video: bool = False,
    run_dedup: bool = True,
) -> dict:
    """Extract one JPEG per selected keyframe, dedup, and report provenance."""
    source = resolve_source(target)
    cache = cache_dir_for(source.id)
    payload = json.loads(Path(keyframes_path).read_text(encoding="utf-8"))
    keyframes = payload.get("keyframes", [])
    profile = payload.get("profile", profile)
    frame_width = width or PROFILE_WIDTH.get(profile, 1024)

    if not keyframes:
        return {"count": 0, "skipped": 0, "frames": [], "profile": profile,
                "note": "no keyframes selected"}

    video_path, downloaded = ensure_video(target, cache)
    frames_dir = cache / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    extracted = []
    skipped = 0
    for kf in keyframes:
        ts = float(kf["timestamp"])
        out = frames_dir / "{0}_{1}.jpg".format(format_timestamp_label(ts), frame_width)
        if not out.exists():
            proc = subprocess.run(
                build_ffmpeg_command(video_path, ts, out, frame_width),
                capture_output=True, text=True, errors="replace",
            )
            if proc.returncode != 0 or not out.exists():
                skipped += 1
                continue
        extracted.append({
            "timestamp": ts,
            "label": format_timestamp_label(ts),
            "path": str(out),
            "reason": kf.get("reason", ""),
            "source": kf.get("source", "unknown"),
            "score": kf.get("score"),
        })

    result = {"profile": profile, "width": frame_width, "skipped": skipped,
              "extracted": len(extracted)}
    if run_dedup:
        # An explicit verbal callout outranks any pixel threshold.
        deduped = dedup(extracted, profile, protect={"semantic"})
        result.update({
            "count": deduped["kept"],
            "deduped_out": deduped["dropped_count"],
            "frames": deduped["frames"],
        })
    else:
        result.update({"count": len(extracted), "deduped_out": 0, "frames": extracted})

    # Reclaim the download once frames exist. Never touches a user's own file.
    if downloaded and not keep_video and result["count"] > 0 and video_path.exists():
        try:
            freed = video_path.stat().st_size
            video_path.unlink()
            result["pruned_video_bytes"] = freed
        except OSError:
            pass

    frames_json = cache / "frames.json"
    frames_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["frames_json"] = str(frames_json)
    return result


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Extract and deduplicate keyframes.")
    ap.add_argument("target", help="URL or local file path")
    ap.add_argument("keyframes", help="keyframes.json from select_keyframes.py")
    ap.add_argument("--profile", default="chart")
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--keep-video", action="store_true")
    ap.add_argument("--no-dedup", action="store_true")
    args = ap.parse_args()

    result = extract(
        args.target, Path(args.keyframes), profile=args.profile,
        width=args.width, keep_video=args.keep_video, run_dedup=not args.no_dedup,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
