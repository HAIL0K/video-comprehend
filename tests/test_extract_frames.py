# tests/test_extract_frames.py
from pathlib import Path
import pytest

from scripts.extract_frames import format_timestamp_label, build_ffmpeg_command


@pytest.mark.parametrize("seconds,expected", [
    (0, "00-00-00"),
    (5, "00-00-05"),
    (83, "00-01-23"),
    (3725, "01-02-05"),
    (3661.7, "01-01-01"),
])
def test_format_timestamp_label(seconds, expected):
    assert format_timestamp_label(seconds) == expected


def test_build_ffmpeg_command_uses_fast_seek_before_input():
    out_path = Path("/cache/frames/00-01-23.png")
    cmd = build_ffmpeg_command(
        video_path=Path("/cache/video.mp4"),
        timestamp_seconds=83.0,
        out_path=out_path,
    )
    assert cmd[0] == "ffmpeg"
    ss_idx = cmd.index("-ss")
    i_idx = cmd.index("-i")
    assert ss_idx < i_idx, "Fast seek requires -ss before -i"
    assert cmd[ss_idx + 1] == "83.0" or cmd[ss_idx + 1] == "83"
    assert "-frames:v" in cmd
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert "-y" in cmd
    assert cmd[-1] == str(out_path)


import subprocess
from unittest.mock import patch, call

from scripts.extract_frames import ensure_video_downloaded, extract_frames


def test_ensure_video_downloaded_skips_if_exists(tmp_path):
    cache = tmp_path / "id"
    cache.mkdir()
    video = cache / "video.mp4"
    video.write_bytes(b"existing")
    with patch("scripts.extract_frames.subprocess.run") as mock_run:
        result = ensure_video_downloaded("https://youtu.be/abc", cache)
    assert result == video
    mock_run.assert_not_called()


def test_ensure_video_downloaded_calls_yt_dlp(tmp_path):
    cache = tmp_path / "id"
    cache.mkdir()

    def fake_run(*args, **kwargs):
        (cache / "video.mp4").write_bytes(b"downloaded")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("scripts.extract_frames.subprocess.run", side_effect=fake_run) as mock_run:
        result = ensure_video_downloaded("https://youtu.be/abc", cache)

    args = mock_run.call_args[0][0]
    assert "yt-dlp" in args[0]
    assert "--no-playlist" in args
    assert "https://youtu.be/abc" in args
    assert result == cache / "video.mp4"


def test_extract_frames_runs_ffmpeg_per_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "abcdefghij1"
    cache = tmp_path / video_id
    cache.mkdir()
    (cache / "video.mp4").write_bytes(b"fake")
    keyframes_path = cache / "keyframes.json"
    import json as _json
    keyframes_path.write_text(_json.dumps({
        "keyframes": [
            {"timestamp": 1.0, "reason": "intro"},
            {"timestamp": 83.0, "reason": "ORB chart"},
        ]
    }))

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[0] == "ffmpeg":
            out = Path(args[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"PNG")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("scripts.extract_frames.subprocess.run", side_effect=fake_run):
        result = extract_frames(f"https://youtu.be/{video_id}", keyframes_path)

    assert result["count"] == 2
    assert (cache / "frames" / "00-00-01.png").exists()
    assert (cache / "frames" / "00-01-23.png").exists()
    ffmpeg_calls = [c for c in calls if c[0] == "ffmpeg"]
    assert len(ffmpeg_calls) == 2


def test_extract_frames_skips_failing_frame_continues(tmp_path, monkeypatch):
    """One ffmpeg call fails — other frames still extracted, no exception bubbles."""
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    video_id = "abcdefghij1"
    cache = tmp_path / video_id
    cache.mkdir()
    (cache / "video.mp4").write_bytes(b"fake")
    keyframes_path = cache / "keyframes.json"
    import json as _json
    keyframes_path.write_text(_json.dumps({
        "keyframes": [
            {"timestamp": 1.0, "reason": "ok"},
            {"timestamp": 99.0, "reason": "will-fail"},
            {"timestamp": 200.0, "reason": "ok"},
        ]
    }))

    def fake_run(args, **kwargs):
        if args[0] == "ffmpeg" and "99.0" in args:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="bad seek")
        if args[0] == "ffmpeg":
            out = Path(args[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"PNG")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("scripts.extract_frames.subprocess.run", side_effect=fake_run):
        result = extract_frames(f"https://youtu.be/{video_id}", keyframes_path)

    assert result["count"] == 2
    assert result["skipped"] == 1
