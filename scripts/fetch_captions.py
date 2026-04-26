# scripts/fetch_captions.py
"""Wrap yt-dlp to download metadata + captions for a YouTube URL into the cache."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from scripts._lib import extract_video_id, cache_dir_for

SCHEMA_VERSION = 2


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
    # Prefer manually-transcribed captions over auto-generated.
    # yt-dlp marks auto-generated subs with "auto" in the language tag (e.g., a.en.vtt or en.auto.vtt).
    vtt_candidates.sort(key=lambda p: ("auto" in p.name.lower(), p.name))
    shutil.move(str(vtt_candidates[0]), str(captions_path))

    return {
        "video_id": video_id,
        "meta_path": str(meta_path),
        "captions_path": str(captions_path),
        "cached": False,
    }


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
