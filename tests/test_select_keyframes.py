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


from scripts.select_keyframes import score_cue


def _cue(text, start=10.0):
    return Cue(start_seconds=start, end_seconds=start + 4.0, text=text)


def test_score_cue_plain_text_low_score():
    assert score_cue(_cue("This is generic narration.")) == 0


def test_score_cue_deictic_phrase_scores():
    assert score_cue(_cue("As you can see, the volume spiked.")) >= 2
    assert score_cue(_cue("Look at this chart.")) >= 2
    assert score_cue(_cue("Here's the setup we want.")) >= 2


def test_score_cue_numeric_plus_deixis_scores_highest():
    plain_deixis = score_cue(_cue("As you can see, it works."))
    deixis_plus_number = score_cue(_cue("As you can see, the level is 512.40."))
    assert deixis_plus_number > plain_deixis


def test_score_cue_chart_keyword_scores():
    assert score_cue(_cue("Notice the candlestick pattern on this chart.")) >= 1
