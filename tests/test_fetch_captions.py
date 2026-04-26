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
            fetch_captions("https://youtu.be/dQw4w9WgXcQ")


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
            fetch_captions(f"https://youtu.be/{video_id}")
