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
import sys
from pathlib import Path

from scripts._lib import pick_whisper_device

# Lazy import — faster_whisper is heavy. If unavailable, leave the symbol as None
# so transcribe_audio() can produce a friendly error message.
try:
    from faster_whisper import WhisperModel  # type: ignore
except ImportError:
    WhisperModel = None  # type: ignore

try:
    from faster_whisper import BatchedInferencePipeline  # type: ignore
except ImportError:  # faster-whisper < 1.1
    BatchedInferencePipeline = None  # type: ignore


# On CPU, model size is the whole cost, so stay small. On GPU, large-v3-turbo is
# both better and faster than `small` was on CPU -- it is a distilled 4-layer
# decoder over the full large-v3 encoder, so accented and non-English speech
# survives, which `small` mangles badly (this pipeline once rendered "Vektor-Kelly"
# as "Vector-Caddy" and "White's Reality Check" as "Wide Reality Check").
_DEFAULT_MODEL_CPU = "small"
_DEFAULT_MODEL_GPU = "large-v3-turbo"
_DEFAULT_BATCH = 8


def _default_model(device: str) -> str:
    return _DEFAULT_MODEL_GPU if device == "cuda" else _DEFAULT_MODEL_CPU


def transcribe_audio(
    audio_path: Path,
    model_name: str | None = None,
    language: str | None = None,
) -> str:
    """Transcribe audio at `audio_path` using faster-whisper. Returns VTT.

    Model precedence: model_name arg > $VIDEO_COMPREHEND_WHISPER_MODEL >
    $YT_COMPREHEND_WHISPER_MODEL > large-v3-turbo on GPU, small on CPU.

    `language` is an ISO code hint ("de"). Passing it is worth real accuracy on
    non-English audio: auto-detection runs on the first window only and a call
    that opens in English loses the rest of the file to the wrong language model.

    Batched inference is used on GPU (several windows per forward pass, 3-4x
    faster). Both the batch and the GPU itself degrade gracefully: an OOM retries
    unbatched, and a CUDA failure retries on CPU, because a slow transcript beats
    no transcript.

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
    device, compute_type = pick_whisper_device()
    chosen = (
        model_name
        or os.environ.get("VIDEO_COMPREHEND_WHISPER_MODEL")
        or os.environ.get("YT_COMPREHEND_WHISPER_MODEL")
        or _default_model(device)
    )
    kwargs = {"language": language} if language else {}

    try:
        return _run_whisper(chosen, device, compute_type, audio_path, kwargs)
    except Exception as exc:  # noqa: BLE001 - any CUDA/driver failure is a fallback trigger
        if device != "cuda":
            raise
        print(
            f"[transcribe] GPU pass failed ({type(exc).__name__}: {exc}); "
            f"falling back to CPU",
            file=sys.stderr,
        )
        cpu_model = model_name or os.environ.get("VIDEO_COMPREHEND_WHISPER_MODEL") or _DEFAULT_MODEL_CPU
        return _run_whisper(cpu_model, "cpu", "int8", audio_path, kwargs)


def _run_whisper(name: str, device: str, compute_type: str, audio_path: Path, kwargs: dict) -> str:
    model = WhisperModel(name, device=device, compute_type=compute_type)
    if device == "cuda" and BatchedInferencePipeline is not None:
        batch = int(os.environ.get("VIDEO_COMPREHEND_WHISPER_BATCH", _DEFAULT_BATCH))
        try:
            pipeline = BatchedInferencePipeline(model=model)
            segments, _info = pipeline.transcribe(str(audio_path), batch_size=batch, **kwargs)
            return segments_to_vtt(segments)
        except RuntimeError as exc:
            if "out of memory" not in str(exc).lower():
                raise
            print("[transcribe] batch OOM; retrying unbatched", file=sys.stderr)
    segments, _info = model.transcribe(str(audio_path), **kwargs)
    return segments_to_vtt(segments)
