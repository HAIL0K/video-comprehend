# scripts/fetch_captions.py
"""Wrap yt-dlp to download metadata + captions for a YouTube URL into the cache."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from scripts._lib import extract_video_id, cache_dir_for
from scripts.transcribe_audio import transcribe_audio

SCHEMA_VERSION = 2


def fetch_captions(url: str, asr_fallback: bool = True) -> dict:
    """Ensure cache dir for the video has meta.json + captions.vtt.

    v2: tries yt-dlp captions first; on failure (429, no captions, etc.) falls back
    to downloading audio and transcribing with faster-whisper. Set asr_fallback=False
    to disable the fallback and restore v1 fail-fast behavior.

    Returns: {video_id, meta_path, captions_path, cached, transcript_source}.

    Raises ValueError for non-YouTube URLs.
    Raises RuntimeError if yt-dlp fails and (asr_fallback is False, or audio download fails).
    """
    video_id = extract_video_id(url)
    cache = cache_dir_for(video_id)
    meta_path = cache / "meta.json"
    captions_path = cache / "captions.vtt"

    if meta_path.exists() and captions_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return {
            "video_id": video_id,
            "meta_path": str(meta_path),
            "captions_path": str(captions_path),
            "cached": True,
            "transcript_source": meta.get("transcript_source", "captions"),
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

    captions_error: str | None = None
    if proc.returncode != 0:
        captions_error = f"yt-dlp failed (exit {proc.returncode}): {proc.stderr.strip()}"

    # Move info.json -> meta.json regardless of caption outcome — we still want metadata.
    info_json = cache / f"{video_id}.info.json"
    meta_data: dict = {}
    if info_json.exists():
        meta_data = json.loads(info_json.read_text(encoding="utf-8"))
        info_json.unlink()

    transcript_source = "captions"
    if captions_error is None:
        vtt_candidates = list(cache.glob(f"{video_id}*.vtt"))
        if not vtt_candidates:
            captions_error = (
                f"No captions available for {video_id}. "
                f"Skill v1 doesn't fall back to ASR; try a video with captions enabled."
            )
        else:
            vtt_candidates.sort(key=lambda p: ("auto" in p.name.lower(), p.name))
            shutil.move(str(vtt_candidates[0]), str(captions_path))

    if captions_error is not None:
        if not asr_fallback:
            raise RuntimeError(captions_error)
        # Fall back to Whisper.
        audio_path = ensure_audio_downloaded(url, cache)
        vtt = transcribe_audio(audio_path)
        captions_path.write_text(vtt, encoding="utf-8")
        transcript_source = "whisper"

    meta_data["schema_version"] = SCHEMA_VERSION
    meta_data["transcript_source"] = transcript_source
    meta_path.write_text(json.dumps(meta_data, indent=2), encoding="utf-8")

    return {
        "video_id": video_id,
        "meta_path": str(meta_path),
        "captions_path": str(captions_path),
        "cached": False,
        "transcript_source": transcript_source,
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
    args = sys.argv[1:]
    asr_fallback = True
    if "--no-asr-fallback" in args:
        args = [a for a in args if a != "--no-asr-fallback"]
        asr_fallback = False
    if len(args) != 1:
        print("usage: fetch_captions.py [--no-asr-fallback] <youtube-url>", file=sys.stderr)
        return 2
    try:
        result = fetch_captions(args[0], asr_fallback=asr_fallback)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
