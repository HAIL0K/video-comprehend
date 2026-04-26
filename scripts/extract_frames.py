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
