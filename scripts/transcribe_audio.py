# scripts/transcribe_audio.py
"""Wrap faster-whisper for ASR fallback. Output is WebVTT-shaped to match yt-dlp captions."""
from __future__ import annotations

from typing import Iterable, Protocol


class _Segment(Protocol):
    """Duck type for faster_whisper.Segment (or any compatible object)."""
    start: float
    end: float
    text: str


def _format_ts(seconds: float) -> str:
    """Format as HH:MM:SS.mmm (WebVTT-compliant)."""
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def segments_to_vtt(segments: Iterable[_Segment]) -> str:
    """Render an iterable of Whisper-style segments as a WebVTT string.

    Empty / whitespace-only segments are dropped. Whitespace inside text is preserved
    except for leading/trailing strip.
    """
    parts: list[str] = ["WEBVTT", ""]
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        parts.append(f"{_format_ts(seg.start)} --> {_format_ts(seg.end)}")
        parts.append(text)
        parts.append("")
    return "\n".join(parts) + "\n"
