# tests/test_fetch_captions.py
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.fetch_captions import fetch_captions


def _fake_yt_dlp_writes_files(cache: Path, video_id: str):
    """Side effect that simulates yt-dlp writing the expected files."""
    (cache / f"{video_id}.info.json").write_text(json.dumps({
        "id": video_id,
        "title": "Test Video",
        "uploader": "TestChannel",
        "duration": 300,
        "upload_date": "20260314",
    }))
    (cache / f"{video_id}.en.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello world\n"
    )
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_fetch_captions_happy_path(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    with patch("scripts.fetch_captions.subprocess.run") as mock_run:
        mock_run.side_effect = lambda *a, **kw: (
            cache.mkdir(parents=True, exist_ok=True)
            or _fake_yt_dlp_writes_files(cache, video_id)
        )
        result = fetch_captions(f"https://youtu.be/{video_id}")

    assert result["video_id"] == video_id
    assert result["cached"] is False
    assert Path(result["meta_path"]).exists()
    assert Path(result["captions_path"]).exists()
    args = mock_run.call_args[0][0]
    assert "yt_dlp" in args
    assert "--skip-download" in args
    assert "--write-auto-sub" in args
    assert "--write-info-json" in args
    assert result["transcript_source"] == "captions"
    meta = json.loads(Path(result["meta_path"]).read_text())
    assert meta["transcript_source"] == "captions"


def test_fetch_captions_cache_hit(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id
    cache.mkdir(parents=True)
    (cache / "meta.json").write_text("{}")
    (cache / "captions.vtt").write_text("WEBVTT\n")

    with patch("scripts.fetch_captions.subprocess.run") as mock_run:
        result = fetch_captions(f"https://youtu.be/{video_id}")

    assert result["cached"] is True
    mock_run.assert_not_called()


def test_fetch_captions_prefers_manual_over_auto_captions(tmp_path, monkeypatch):
    """If both manual and auto-generated VTTs exist, prefer the manual one."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    def fake_yt_dlp(*args, **kwargs):
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{video_id}.info.json").write_text(json.dumps({"id": video_id}))
        # Two VTT candidates: one manual, one auto-generated.
        (cache / f"{video_id}.en.vtt").write_text("WEBVTT\nMANUAL\n")
        (cache / f"{video_id}.en.auto.vtt").write_text("WEBVTT\nAUTO\n")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=fake_yt_dlp):
        result = fetch_captions(f"https://youtu.be/{video_id}")

    captions_text = Path(result["captions_path"]).read_text()
    assert "MANUAL" in captions_text
    assert "AUTO" not in captions_text


def test_fetch_captions_invalid_url():
    with pytest.raises(ValueError):
        fetch_captions("https://example.com")


def test_fetch_captions_yt_dlp_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    with patch("scripts.fetch_captions.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="ERROR: Video unavailable",
        )
        with pytest.raises(RuntimeError, match="yt-dlp failed"):
            fetch_captions("https://youtu.be/dQw4w9WgXcQ", asr_fallback=False)


def test_fetch_captions_no_captions_produced(tmp_path, monkeypatch):
    """yt-dlp succeeds but produces no .vtt — hard fail with the spec's message."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    def yt_dlp_writes_only_meta(*args, **kwargs):
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{video_id}.info.json").write_text("{}")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=yt_dlp_writes_only_meta):
        with pytest.raises(RuntimeError, match="No captions available"):
            fetch_captions(f"https://youtu.be/{video_id}", asr_fallback=False)


def test_meta_json_carries_schema_version_2(tmp_path, monkeypatch):
    """Newly-written meta.json must record schema_version=2 in v2."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    def fake_yt_dlp(*args, **kwargs):
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{video_id}.info.json").write_text(json.dumps({"id": video_id, "title": "T"}))
        (cache / f"{video_id}.en.vtt").write_text("WEBVTT\n")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=fake_yt_dlp):
        result = fetch_captions(f"https://youtu.be/{video_id}")

    meta = json.loads(Path(result["meta_path"]).read_text())
    assert meta["schema_version"] == 2


from scripts.fetch_captions import ensure_audio_downloaded


def test_ensure_audio_downloaded_skips_if_exists(tmp_path):
    cache = tmp_path / "id"
    cache.mkdir()
    audio = cache / "audio.m4a"
    audio.write_bytes(b"existing")
    with patch("scripts.fetch_captions.subprocess.run") as mock_run:
        result = ensure_audio_downloaded("https://youtu.be/abc", cache)
    assert result == audio
    mock_run.assert_not_called()


def test_ensure_audio_downloaded_calls_yt_dlp_with_audio_format(tmp_path):
    cache = tmp_path / "id"
    cache.mkdir()

    def fake_run(*args, **kwargs):
        (cache / "audio.m4a").write_bytes(b"downloaded")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=fake_run) as mock_run:
        result = ensure_audio_downloaded("https://youtu.be/abc", cache)

    args = mock_run.call_args[0][0]
    assert "yt_dlp" in args
    assert "--no-playlist" in args
    assert "https://youtu.be/abc" in args
    audio_flags = ["--extract-audio", "--audio-format", "m4a"]
    for flag in audio_flags:
        assert flag in args, f"missing {flag} in {args}"
    assert result == cache / "audio.m4a"


def test_ensure_audio_downloaded_yt_dlp_fail_raises(tmp_path):
    cache = tmp_path / "id"
    cache.mkdir()
    with patch("scripts.fetch_captions.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="ERROR: video private"
        )
        with pytest.raises(RuntimeError, match="audio download failed"):
            ensure_audio_downloaded("https://youtu.be/abc", cache)


def test_fetch_captions_falls_back_to_whisper_on_yt_dlp_failure(tmp_path, monkeypatch):
    """yt-dlp captions fail (e.g. 429) — Whisper takes over and produces VTT."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    def fake_run(args, **kwargs):
        # First call: caption download. Fail with 429.
        if "--write-auto-sub" in args:
            cache.mkdir(parents=True, exist_ok=True)
            (cache / f"{video_id}.info.json").write_text(json.dumps({"id": video_id, "title": "T"}))
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="",
                stderr="ERROR: HTTP Error 429: Too Many Requests",
            )
        # Second call: audio download. Succeed.
        if "--extract-audio" in args:
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "audio.m4a").write_bytes(b"fake audio")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=fake_run), \
         patch("scripts.fetch_captions.transcribe_audio", return_value="WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello\n"):
        result = fetch_captions(f"https://youtu.be/{video_id}")

    assert result["transcript_source"] == "whisper"
    captions = Path(result["captions_path"]).read_text()
    assert "hello" in captions
    meta = json.loads(Path(result["meta_path"]).read_text())
    assert meta["transcript_source"] == "whisper"
    assert meta["schema_version"] == 2


def test_fetch_captions_falls_back_to_whisper_on_no_captions_produced(tmp_path, monkeypatch):
    """yt-dlp succeeds but produces no VTT (caption-less video) — Whisper takes over."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    def fake_run(args, **kwargs):
        cache.mkdir(parents=True, exist_ok=True)
        if "--write-auto-sub" in args:
            (cache / f"{video_id}.info.json").write_text(json.dumps({"id": video_id, "title": "T"}))
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        if "--extract-audio" in args:
            (cache / "audio.m4a").write_bytes(b"fake audio")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=fake_run), \
         patch("scripts.fetch_captions.transcribe_audio", return_value="WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nasr\n"):
        result = fetch_captions(f"https://youtu.be/{video_id}")

    assert result["transcript_source"] == "whisper"


def test_fetch_captions_audio_download_failure_raises(tmp_path, monkeypatch):
    """If captions fail AND audio download also fails, surface the audio error."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "dQw4w9WgXcQ"
    cache = tmp_path / video_id

    def fake_run(args, **kwargs):
        cache.mkdir(parents=True, exist_ok=True)
        if "--write-auto-sub" in args:
            (cache / f"{video_id}.info.json").write_text("{}")
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="429")
        if "--extract-audio" in args:
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="ERROR: video unavailable"
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("scripts.fetch_captions.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="audio download failed"):
            fetch_captions(f"https://youtu.be/{video_id}")


def test_main_passes_no_asr_fallback_flag(tmp_path, monkeypatch, capsys):
    """Smoke test: invoking main() with --no-asr-fallback propagates to fetch_captions."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    monkeypatch.setattr(
        "sys.argv",
        ["fetch_captions.py", "--no-asr-fallback", "https://example.com"],
    )
    from scripts.fetch_captions import main
    rc = main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "Not a YouTube video URL" in err
