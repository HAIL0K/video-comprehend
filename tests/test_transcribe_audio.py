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


# ---------------------------------------------------------------------------
# Task 3: transcribe_audio orchestrator
# ---------------------------------------------------------------------------
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.transcribe_audio import transcribe_audio


def _make_fake_model(segments: list):
    """Build a MagicMock that quacks like faster_whisper.WhisperModel."""
    info = MagicMock()
    info.language = "en"
    info.duration = sum(s.end - s.start for s in segments) or 1.0
    model = MagicMock()
    model.transcribe.return_value = (iter(segments), info)
    return model


def test_transcribe_audio_returns_vtt_string(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake audio")
    fake_segments = [
        FakeSegment(0.0, 2.0, "hello"),
        FakeSegment(2.0, 4.0, "world"),
    ]
    fake_model = _make_fake_model(fake_segments)

    with patch("scripts.transcribe_audio.WhisperModel", return_value=fake_model) as ctor:
        vtt = transcribe_audio(audio)

    assert vtt.startswith("WEBVTT\n")
    assert "hello" in vtt
    assert "world" in vtt
    # Default model is "small"
    args, kwargs = ctor.call_args
    assert args[0] == "small" or kwargs.get("model_size_or_path") == "small"


def test_transcribe_audio_respects_model_arg(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    with patch("scripts.transcribe_audio.WhisperModel", return_value=_make_fake_model([])) as ctor:
        transcribe_audio(audio, model_name="base")
    args, kwargs = ctor.call_args
    assert args[0] == "base" or kwargs.get("model_size_or_path") == "base"


def test_transcribe_audio_respects_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_WHISPER_MODEL", "tiny")
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    with patch("scripts.transcribe_audio.WhisperModel", return_value=_make_fake_model([])) as ctor:
        transcribe_audio(audio)
    args, kwargs = ctor.call_args
    assert args[0] == "tiny" or kwargs.get("model_size_or_path") == "tiny"


def test_transcribe_audio_explicit_arg_beats_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_WHISPER_MODEL", "tiny")
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    with patch("scripts.transcribe_audio.WhisperModel", return_value=_make_fake_model([])) as ctor:
        transcribe_audio(audio, model_name="medium")
    args, kwargs = ctor.call_args
    assert args[0] == "medium" or kwargs.get("model_size_or_path") == "medium"


def test_transcribe_audio_missing_file_raises(tmp_path):
    nope = tmp_path / "missing.m4a"
    with pytest.raises(FileNotFoundError):
        transcribe_audio(nope)


def test_transcribe_audio_missing_module_raises_clear_error(tmp_path):
    """If faster_whisper isn't installed, raise a friendly RuntimeError."""
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    with patch("scripts.transcribe_audio.WhisperModel", None):
        with pytest.raises(RuntimeError, match="faster-whisper"):
            transcribe_audio(audio)
