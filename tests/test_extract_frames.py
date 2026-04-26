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
