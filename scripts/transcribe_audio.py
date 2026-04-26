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


import os
from pathlib import Path

# Lazy import — faster_whisper is heavy. If unavailable, leave the symbol as None
# so transcribe_audio() can produce a friendly error message.
try:
    from faster_whisper import WhisperModel  # type: ignore
except ImportError:
    WhisperModel = None  # type: ignore


_DEFAULT_MODEL = "small"
_DEFAULT_DEVICE = "cpu"


def transcribe_audio(audio_path: Path, model_name: str | None = None) -> str:
    """Transcribe audio at `audio_path` using faster-whisper. Returns VTT.

    Model selection precedence: explicit `model_name` arg > $YT_COMPREHEND_WHISPER_MODEL > "small".
    Device selection: $YT_COMPREHEND_WHISPER_DEVICE > "cpu". Set to "cuda" for GPU.

    Raises FileNotFoundError if `audio_path` doesn't exist.
    Raises RuntimeError with install message if faster-whisper isn't importable.
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    if WhisperModel is None:
        raise RuntimeError(
            "faster-whisper is not installed. Install with: pip install faster-whisper"
        )
    chosen = model_name or os.environ.get("YT_COMPREHEND_WHISPER_MODEL") or _DEFAULT_MODEL
    device = os.environ.get("YT_COMPREHEND_WHISPER_DEVICE") or _DEFAULT_DEVICE
    compute_type = "int8" if device == "cpu" else "default"
    model = WhisperModel(chosen, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(str(audio_path))
    return segments_to_vtt(segments)
