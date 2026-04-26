# tests/test_select_keyframes.py
from pathlib import Path
import pytest

from scripts.select_keyframes import parse_vtt, Cue

FIXTURES = Path(__file__).parent / "recorded"


def test_parse_vtt_returns_cues_in_order():
    cues = parse_vtt(FIXTURES / "sample.vtt")
    assert len(cues) == 9
    assert isinstance(cues[0], Cue)
    assert cues[0].start_seconds == 1.0
    assert cues[0].end_seconds == 5.0
    assert "Welcome back" in cues[0].text
    starts = [c.start_seconds for c in cues]
    assert starts == sorted(starts)


def test_parse_vtt_handles_empty_file(tmp_path):
    empty = tmp_path / "empty.vtt"
    empty.write_text("WEBVTT\n")
    assert parse_vtt(empty) == []


def test_parse_vtt_strips_html_tags(tmp_path):
    f = tmp_path / "tags.vtt"
    f.write_text(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:05.000\n"
        "<c.colorE5E5E5>Hello</c> <i>world</i>\n"
    )
    cues = parse_vtt(f)
    assert cues[0].text == "Hello world"
