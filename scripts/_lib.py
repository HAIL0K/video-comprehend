# scripts/_lib.py
"""Shared utilities for video-comprehend.

Source resolution, cache paths, and project-scoped research paths.

Three source kinds are supported:
  youtube  -- canonical 11-char video id (cache-compatible with the old yt-comprehend)
  url      -- any other yt-dlp-supported URL (Instagram, TikTok, Vimeo, ...)
  local    -- a file on disk
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_VIDEO_ID_PATTERNS = [
    re.compile(r"(?:youtube\.com/watch\?(?:[^&]*&)*v=)([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/embed/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/shorts/([A-Za-z0-9_-]{11})"),
]

_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)

LEGACY_CACHE_NAME = "yt-comprehend"
CACHE_NAME = "video-comprehend"


def extract_video_id(url: str) -> str:
    """Extract the 11-char YouTube video ID from a watch/short/embed URL.

    Raises ValueError for non-YouTube URLs, playlists, channels, or empty input.
    Kept for backward compatibility with the yt-comprehend scripts.
    """
    if not url or not isinstance(url, str):
        raise ValueError(f"Empty or non-string URL: {url!r}")
    for pattern in _VIDEO_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    raise ValueError(f"Not a YouTube video URL: {url!r}")


@dataclass(frozen=True)
class Source:
    """A resolved input. `id` is stable and filesystem-safe."""
    kind: str          # youtube | url | local
    id: str
    target: str        # the URL, or the absolute path for local files
    is_local: bool


def _short_hash(text: str, n: int = 11) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def resolve_source(arg: str) -> Source:
    """Classify an input as a YouTube video, a generic URL, or a local file.

    Local files are identified by absolute path + size so the same file at the same
    path reuses its cache, while a re-encode of different length does not.

    Raises ValueError on empty input or a path that does not exist.
    """
    if not arg or not isinstance(arg, str):
        raise ValueError(f"Empty or non-string source: {arg!r}")
    candidate = arg.strip().strip('"')

    if _URL_RE.match(candidate):
        try:
            return Source("youtube", extract_video_id(candidate), candidate, False)
        except ValueError:
            return Source("url", "u_" + _short_hash(candidate, 9), candidate, False)

    path = Path(candidate).expanduser()
    if not path.exists():
        raise ValueError(
            f"Not a URL and not an existing file: {arg!r}"
        )
    resolved = path.resolve()
    size = resolved.stat().st_size
    return Source("local", "f_" + _short_hash(f"{resolved}|{size}", 9), str(resolved), True)


def cache_root() -> Path:
    """Root of the scratch cache. $VIDEO_COMPREHEND_CACHE overrides.

    $YT_COMPREHEND_CACHE is still honoured so existing setups keep working.
    """
    base = os.environ.get("VIDEO_COMPREHEND_CACHE") or os.environ.get("YT_COMPREHEND_CACHE")
    if base:
        return Path(base)
    return Path.home() / ".cache" / CACHE_NAME


def cache_dir_for(source_id: str) -> Path:
    """Per-source scratch dir.

    If a legacy ~/.cache/yt-comprehend/<id> dir already holds this video, keep using
    it — over a hundred videos are cached there and re-downloading them is pure waste.
    """
    explicit = os.environ.get("VIDEO_COMPREHEND_CACHE") or os.environ.get("YT_COMPREHEND_CACHE")
    if explicit:
        return Path(explicit) / source_id
    legacy = Path.home() / ".cache" / LEGACY_CACHE_NAME / source_id
    if legacy.exists():
        return legacy
    return Path.home() / ".cache" / CACHE_NAME / source_id


def project_root(start: Path | str | None = None) -> Path:
    """Nearest ancestor containing .git, else the starting directory itself."""
    here = Path(start).resolve() if start else Path.cwd().resolve()
    if here.is_file():
        here = here.parent
    for candidate in [here, *here.parents]:
        if (candidate / ".git").exists():
            return candidate
    return here


def research_dir(root: Path | str | None = None) -> Path:
    """Where reports and the index live, inside the project being analysed.

    Prefers an existing docs/research/yt so old reports and new ones stay together;
    otherwise uses docs/research/video.
    """
    base = Path(root) if root else project_root()
    legacy = base / "docs" / "research" / "yt"
    if legacy.is_dir():
        return legacy
    return base / "docs" / "research" / "video"


# YouTube gates most player clients behind PO tokens, and which client works changes
# over time -- yt-dlp's own default pick returns HTTP 403 on plenty of videos. So we
# try clients in order rather than trusting one. web_embedded needs yt-dlp's JS
# solver, which `--remote-components ejs:github` fetches from GitHub on first use
# (free, cached afterwards); it is the only step in this pipeline that touches the
# network for anything other than the media itself.
CLIENT_ATTEMPTS = [
    ("default", []),
    ("web_embedded", ["--extractor-args", "youtube:player_client=web_embedded",
                      "--remote-components", "ejs:github"]),
    ("tv_simply", ["--extractor-args", "youtube:player_client=tv_simply"]),
    ("android_vr", ["--extractor-args", "youtube:player_client=android_vr"]),
    ("web", ["--extractor-args", "youtube:player_client=web",
             "--remote-components", "ejs:github"]),
]


def make_stdout_utf8() -> None:
    """Stop the Windows cp1252 console from killing a run over one character.

    Report snippets carry star ratings, arrows and non-ASCII titles; printing those
    to a default Windows console raises UnicodeEncodeError and loses the whole result.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def slugify(title: str, max_len: int = 60) -> str:
    """Lowercase, ASCII-only, hyphen-separated. Truncated to max_len chars."""
    ascii_only = "".join(c for c in title if ord(c) < 128)
    lowered = ascii_only.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if len(cleaned) <= max_len:
        return cleaned or "untitled"
    truncated = cleaned[:max_len].rsplit("-", 1)[0]
    return truncated or "untitled"


def format_timestamp_label(seconds: float) -> str:
    """Format seconds as 'HH-MM-SS' (filesystem-safe)."""
    total = int(seconds)
    return f"{total // 3600:02d}-{(total % 3600) // 60:02d}-{total % 60:02d}"


def format_hms(seconds: float) -> str:
    """Format seconds as 'HH:MM:SS'."""
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def parse_time(value: str | float | int | None) -> float | None:
    """Parse 'SS', 'MM:SS', 'HH:MM:SS' or a raw number into seconds. None passes through."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).strip().split(":")
    if not all(p.replace(".", "", 1).isdigit() for p in parts if p != ""):
        raise ValueError(f"Unparseable timestamp: {value!r}")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part or 0)
    return total


# --- CUDA runtime wiring -------------------------------------------------
# faster-whisper reaches CUDA through ctranslate2, which loads cuBLAS and cuDNN
# by name at import time. On Windows the pip wheels (nvidia-cublas-cu12,
# nvidia-cudnn-cu12) drop those DLLs under site-packages/nvidia/*/bin, which is
# NOT on PATH -- so ctranslate2 raises "cublas64_12.dll not found" and the whole
# pipeline silently falls back to CPU int8, which is ~15x slower and materially
# worse on non-English audio. Registering the directories fixes it with no
# system-level install. Best-effort: never raises, so CPU still works.

_CUDA_DLLS_REGISTERED = False


def enable_cuda_dlls() -> bool:
    """Make pip-installed CUDA runtime DLLs loadable. Returns True if any were found."""
    global _CUDA_DLLS_REGISTERED
    if _CUDA_DLLS_REGISTERED:
        return True
    import glob
    import site
    import sys

    if not sys.platform.startswith("win"):
        _CUDA_DLLS_REGISTERED = True
        return False

    roots = []
    try:
        roots.extend(site.getsitepackages())
    except Exception:
        pass
    try:
        roots.append(site.getusersitepackages())
    except Exception:
        pass

    found = []
    for root in roots:
        pattern = os.path.join(root, "nvidia", "*", "bin")
        for d in glob.glob(pattern):
            if glob.glob(os.path.join(d, "*.dll")):
                found.append(d)

    for d in sorted(set(found)):
        try:
            os.add_dll_directory(d)
        except Exception:
            pass
        if d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

    _CUDA_DLLS_REGISTERED = True
    return bool(found)


def pick_whisper_device():
    """Return (device, compute_type) preferring CUDA when it actually loads.

    int8_float16 rather than float16: a 4 GB laptop GPU cannot hold large-v3 in
    float16 alongside a display, and the quality difference on speech is small.
    """
    enable_cuda_dlls()
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "int8_float16"
    except Exception:
        pass
    return "cpu", "int8"
