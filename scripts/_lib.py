# scripts/_lib.py
"""Shared utilities for yt-comprehend scripts."""
from __future__ import annotations

import os
import re
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
    # Only keep characters that are already ASCII in the original string;
    # non-ASCII chars (accented, etc.) are dropped entirely rather than
    # being decomposed to their ASCII base letters.
    ascii_only = "".join(c for c in title if ord(c) < 128)
    lowered = ascii_only.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if len(cleaned) <= max_len:
        return cleaned or "untitled"
    truncated = cleaned[:max_len].rsplit("-", 1)[0]
    return truncated or "untitled"
