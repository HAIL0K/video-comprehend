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
    assert "yt-dlp" in args[0]
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
