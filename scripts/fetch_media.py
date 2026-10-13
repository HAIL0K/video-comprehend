# scripts/fetch_media.py
"""Get metadata + a timestamped transcript for any source into the cache.

Transcript precedence, cheapest first:
  1. platform captions via yt-dlp (URLs)
  2. a sidecar .vtt/.srt next to a local file
  3. an embedded subtitle track, pulled out with ffmpeg
  4. local faster-whisper ASR on extracted audio -- OPT-IN ONLY (--asr-fallback)

Captions are the default transcript channel and local ASR never runs unless asked
for explicitly: Whisper is slow, hallucinates on music and crosstalk, and loses the
real spoken timestamps that make a report searchable. Without --asr-fallback a
source with no caption channel raises instead of being transcribed.

Everything here is free and local: yt-dlp, ffmpeg/ffprobe and faster-whisper. No
paid API is called at any point.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from scripts._lib import resolve_source, cache_dir_for, Source, CLIENT_ATTEMPTS
from scripts.transcribe_audio import transcribe_audio

SCHEMA_VERSION = 3


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


def ffprobe_meta(path: Path) -> dict:
    """Duration, dimensions and container tags for a local file."""
    proc = _run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if proc.returncode != 0:
        return {"title": path.stem, "duration": 0}
    probe = json.loads(proc.stdout or "{}")
    fmt = probe.get("format", {})
    tags = fmt.get("tags", {}) or {}
    video_stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {}
    )
    return {
        "title": tags.get("title") or path.stem,
        "uploader": tags.get("artist") or tags.get("encoder") or "local file",
        "duration": float(fmt.get("duration") or 0),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "upload_date": None,
        "webpage_url": str(path),
        "_streams": [
            {"index": s.get("index"), "codec_type": s.get("codec_type"),
             "codec_name": s.get("codec_name"), "tags": s.get("tags", {})}
            for s in probe.get("streams", [])
        ],
    }


def sidecar_subtitles(path: Path) -> Path | None:
    """A .vtt or .srt sitting next to the video, if the user has one."""
    for ext in (".vtt", ".srt", ".en.vtt", ".en.srt"):
        candidate = path.with_suffix(ext)
        if candidate.exists():
            return candidate
    return None


def extract_embedded_subs(video: Path, out_vtt: Path) -> bool:
    """Pull the first subtitle track out of the container as WebVTT."""
    proc = _run(["ffmpeg", "-y", "-i", str(video), "-map", "0:s:0", str(out_vtt)])
    return proc.returncode == 0 and out_vtt.exists() and out_vtt.stat().st_size > 0


def srt_to_vtt(srt_path: Path, out_vtt: Path) -> bool:
    proc = _run(["ffmpeg", "-y", "-i", str(srt_path), str(out_vtt)])
    return proc.returncode == 0 and out_vtt.exists()


def ensure_audio(source: Source, cache: Path) -> Path:
    """Audio-only copy in the cache, for the ASR fallback.

    For URLs this uses a different yt-dlp endpoint than caption download, so it
    frequently succeeds when captions are rate-limited.
    """
    audio_path = cache / "audio.m4a"
    if audio_path.exists():
        return audio_path
    cache.mkdir(parents=True, exist_ok=True)
    if source.is_local:
        proc = _run(["ffmpeg", "-y", "-i", source.target, "-vn",
                     "-c:a", "aac", "-b:a", "96k", str(audio_path)])
        if proc.returncode != 0 or not audio_path.exists():
            raise RuntimeError("audio extraction failed: {0}".format(proc.stderr.strip()[:400]))
        return audio_path
    failures = []
    for name, extra in CLIENT_ATTEMPTS:
        proc = _run([
            sys.executable, "-m", "yt_dlp", "--no-playlist",
            "--extract-audio", "--audio-format", "m4a",
            "--output", str(audio_path), *extra, source.target,
        ])
        if proc.returncode == 0 and audio_path.exists():
            return audio_path
        tail = (proc.stderr or "").strip().splitlines()
        failures.append("{0}: {1}".format(name, tail[-1][:160] if tail else "no output"))
    raise RuntimeError(
        "audio download failed on every player client:\n  " + "\n  ".join(failures))


def _is_rate_limited(stderr: str) -> bool:
    low = (stderr or "").lower()
    return "429" in low or "too many requests" in low


def _cookie_args() -> list:
    """Optional cookies. A logged-in session is the biggest single 429 mitigation.

    Set VIDEO_COMPREHEND_YTDLP_COOKIES_FROM_BROWSER=chrome (or firefox/edge), or
    VIDEO_COMPREHEND_YTDLP_COOKIES=<path to a cookies.txt>.
    """
    browser = os.environ.get("VIDEO_COMPREHEND_YTDLP_COOKIES_FROM_BROWSER")
    if browser:
        return ["--cookies-from-browser", browser]
    jar = os.environ.get("VIDEO_COMPREHEND_YTDLP_COOKIES")
    if jar and Path(jar).exists():
        return ["--cookies", jar]
    return []


# yt-dlp's own retry knobs. --sleep-requests spaces the calls within a single run,
# which is what actually keeps a multi-request caption fetch under the limit.
_POLITE = ["--retries", "5", "--extractor-retries", "3", "--sleep-requests", "1"]

# Backoff between player-client attempts, but ONLY on 429. A 429 is a rate limit:
# rotating to another client without waiting just spends the next client's budget
# against the same wall, which is how this fetcher previously burned all five in a
# few seconds and fell through to ASR every time.
_BACKOFF_SECONDS = [5, 15, 45]


def _caption_langs(meta: dict) -> list:
    """Caption language codes worth trying, best first.

    Bug this replaces: the fetcher hardcoded --sub-lang "en.*", so every
    non-English source reported "no caption track published" even when a perfectly
    good track existed in the video's own language.
    """
    manual = list((meta.get("subtitles") or {}).keys())
    auto = list((meta.get("automatic_captions") or {}).keys())
    native = meta.get("language")

    ordered = []
    for pool in (manual, auto):
        for pref in ([native] if native else []) + ["en"]:
            if not pref:
                continue
            for code in pool:
                if code == pref or code.startswith(pref + "-"):
                    ordered.append(code)
        ordered.extend(pool)

    seen = set()
    out = []
    for code in ordered:
        if code and code not in seen and not code.startswith("live_chat"):
            seen.add(code)
            out.append(code)
    return out


def _fetch_metadata(source: Source, cache: Path):
    """Pass 1: info.json only. Returns (meta_dict, error_or_None).

    Split from caption download deliberately: metadata succeeds far more often
    than a caption fetch, and its subtitles / automatic_captions keys are what
    tell pass 2 which languages to ask for.
    """
    output_template = str(cache / "{0}.%(ext)s".format(source.id))
    error = None
    for idx, (name, extra) in enumerate(CLIENT_ATTEMPTS):
        proc = _run([
            sys.executable, "-m", "yt_dlp", "--skip-download", "--write-info-json",
            "--no-playlist", *_POLITE, *_cookie_args(),
            "--output", output_template, *extra, source.target,
        ])
        info_json = cache / "{0}.info.json".format(source.id)
        if proc.returncode == 0 and info_json.exists():
            meta = json.loads(info_json.read_text(encoding="utf-8"))
            info_json.unlink()
            return meta, None
        tail = (proc.stderr or "").strip().splitlines()
        error = "yt-dlp metadata failed on {0} (exit {1}): {2}".format(
            name, proc.returncode, tail[-1][:200] if tail else "no output")
        if _is_rate_limited(proc.stderr) and idx < len(CLIENT_ATTEMPTS) - 1:
            wait = _BACKOFF_SECONDS[min(idx, len(_BACKOFF_SECONDS) - 1)]
            print("[fetch] rate-limited on {0}; waiting {1}s".format(name, wait), file=sys.stderr)
            time.sleep(wait)
    return {}, error


def _fetch_captions(source: Source, cache: Path, captions_path: Path, meta: dict):
    """Pass 2: download one caption track, in the best language actually available."""
    langs = _caption_langs(meta)
    if not langs:
        return "no caption track published for this video"

    output_template = str(cache / "{0}.%(ext)s".format(source.id))
    error = None
    for idx, (name, extra) in enumerate(CLIENT_ATTEMPTS):
        proc = _run([
            sys.executable, "-m", "yt_dlp",
            "--skip-download", "--write-auto-sub", "--write-sub",
            "--sub-langs", ",".join(langs[:4]), "--sub-format", "vtt",
            "--no-playlist", *_POLITE, *_cookie_args(),
            "--output", output_template, *extra, source.target,
        ])
        vtts = list(cache.glob("{0}*.vtt".format(source.id)))
        if vtts:
            def rank(path: Path):
                stem = path.name.lower()
                lang_rank = next(
                    (i for i, c in enumerate(langs) if c.lower() in stem), len(langs))
                return ("auto" in stem, lang_rank, stem)

            vtts.sort(key=rank)
            shutil.move(str(vtts[0]), str(captions_path))
            for leftover in vtts[1:]:
                leftover.unlink()
            return None
        tail = (proc.stderr or "").strip().splitlines()
        error = "caption download failed on {0} (exit {1}): {2}".format(
            name, proc.returncode, tail[-1][:200] if tail else "no output")
        if _is_rate_limited(proc.stderr) and idx < len(CLIENT_ATTEMPTS) - 1:
            wait = _BACKOFF_SECONDS[min(idx, len(_BACKOFF_SECONDS) - 1)]
            print("[fetch] rate-limited on {0}; waiting {1}s".format(name, wait), file=sys.stderr)
            time.sleep(wait)
    return error


def _fetch_remote(source: Source, cache: Path, captions_path: Path):
    """yt-dlp metadata + captions. Returns (meta_dict, captions_error_or_None)."""
    meta_data, meta_error = _fetch_metadata(source, cache)
    if meta_error is not None and not meta_data:
        return {}, meta_error
    return meta_data, _fetch_captions(source, cache, captions_path, meta_data)


def _fetch_local(source: Source, cache: Path, captions_path: Path):
    """ffprobe metadata + sidecar/embedded subtitles for a file on disk."""
    path = Path(source.target)
    meta_data = ffprobe_meta(path)
    sidecar = sidecar_subtitles(path)
    if sidecar and sidecar.suffix == ".vtt":
        shutil.copy(str(sidecar), str(captions_path))
        return meta_data, None
    if sidecar and srt_to_vtt(sidecar, captions_path):
        return meta_data, None
    if extract_embedded_subs(path, captions_path):
        return meta_data, None
    return meta_data, "no sidecar or embedded subtitle track"


def fetch_media(target: str, asr_fallback: bool = False, whisper_model: str | None = None) -> dict:
    """Ensure the cache holds meta.json + captions.vtt for `target`.

    Returns source id/kind, both paths, whether it was cached, and which transcript
    channel won. Raises RuntimeError only when every channel fails.
    """
    source = resolve_source(target)
    cache = cache_dir_for(source.id)
    meta_path = cache / "meta.json"
    captions_path = cache / "captions.vtt"

    if meta_path.exists() and captions_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return {
            "source_id": source.id, "kind": source.kind, "target": source.target,
            "meta_path": str(meta_path), "captions_path": str(captions_path),
            "cached": True,
            "transcript_source": meta.get("transcript_source", "captions"),
            "duration": meta.get("duration", 0),
        }

    cache.mkdir(parents=True, exist_ok=True)
    if source.is_local:
        meta_data, captions_error = _fetch_local(source, cache, captions_path)
    else:
        meta_data, captions_error = _fetch_remote(source, cache, captions_path)

    transcript_source = "captions"
    if captions_error is not None:
        if not asr_fallback:
            raise RuntimeError(
                f"{captions_error} -- local ASR is opt-in; captions are the only "
                "transcript channel by default. Re-run with --asr-fallback to "
                "transcribe locally with faster-whisper."
            )
        audio_path = ensure_audio(source, cache)
        captions_path.write_text(transcribe_audio(audio_path, whisper_model), encoding="utf-8")
        transcript_source = "whisper"

    meta_data["schema_version"] = SCHEMA_VERSION
    meta_data["transcript_source"] = transcript_source
    meta_data["source_kind"] = source.kind
    meta_data["source_id"] = source.id
    meta_data.pop("_streams", None)
    meta_path.write_text(json.dumps(meta_data, indent=2), encoding="utf-8")

    return {
        "source_id": source.id, "kind": source.kind, "target": source.target,
        "meta_path": str(meta_path), "captions_path": str(captions_path),
        "cached": False, "transcript_source": transcript_source,
        "duration": meta_data.get("duration", 0),
    }


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Fetch metadata + transcript for a video.")
    ap.add_argument("target", help="URL or local file path")
    ap.add_argument(
        "--asr-fallback",
        action="store_true",
        help="transcribe locally with faster-whisper when no captions exist (opt-in)",
    )
    # Accepted and ignored: ASR is off by default now, this flag was the old way
    # of asking for that.
    ap.add_argument("--no-asr-fallback", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--whisper-model", default=None)
    args = ap.parse_args()
    try:
        result = fetch_media(
            args.target,
            asr_fallback=args.asr_fallback and not args.no_asr_fallback,
            whisper_model=args.whisper_model,
        )
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
