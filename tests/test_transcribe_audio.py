# tests/test_transcribe_audio.py
from dataclasses import dataclass

import pytest

from scripts.transcribe_audio import segments_to_vtt


@dataclass
class FakeSegment:
    """Duck-types faster_whisper.Segment for testing."""
    start: float
    end: float
    text: str


def test_segments_to_vtt_emits_webvtt_header():
    vtt = segments_to_vtt([FakeSegment(0.0, 1.5, "hi")])
    assert vtt.startswith("WEBVTT\n")


def test_segments_to_vtt_formats_timestamps_with_milliseconds():
    vtt = segments_to_vtt([FakeSegment(83.0, 87.25, "test")])
    assert "00:01:23.000 --> 00:01:27.250" in vtt


def test_segments_to_vtt_formats_hours():
    vtt = segments_to_vtt([FakeSegment(3725.0, 3730.0, "later")])
    assert "01:02:05.000 --> 01:02:10.000" in vtt


def test_segments_to_vtt_strips_text_whitespace():
    vtt = segments_to_vtt([FakeSegment(0.0, 1.0, "  padded   text  ")])
    assert "padded   text" in vtt
    # Leading/trailing trimmed at minimum:
    assert "  padded" not in vtt.split("\n")[2]


def test_segments_to_vtt_skips_empty_segments():
    """Whisper sometimes emits empty/whitespace-only segments — drop them."""
    vtt = segments_to_vtt([
        FakeSegment(0.0, 1.0, "real"),
        FakeSegment(1.0, 2.0, "   "),
        FakeSegment(2.0, 3.0, ""),
        FakeSegment(3.0, 4.0, "more"),
    ])
    cue_lines = [ln for ln in vtt.splitlines() if "-->" in ln]
    assert len(cue_lines) == 2


def test_segments_to_vtt_round_trips_through_parse_vtt(tmp_path):
    """Sanity: VTT we emit must be parseable by our own parse_vtt."""
    from scripts.select_keyframes import parse_vtt
    vtt = segments_to_vtt([
        FakeSegment(0.0, 5.0, "first cue"),
        FakeSegment(5.0, 10.0, "second cue"),
    ])
    f = tmp_path / "out.vtt"
    f.write_text(vtt, encoding="utf-8")
    cues = parse_vtt(f)
    assert len(cues) == 2
    assert cues[0].text == "first cue"
    assert cues[0].start_seconds == 0.0
    assert cues[1].start_seconds == 5.0
