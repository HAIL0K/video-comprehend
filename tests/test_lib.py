# tests/test_lib.py
import pytest
from pathlib import Path
from scripts._lib import extract_video_id, cache_dir_for, slugify


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
])
def test_extract_video_id_valid(url, expected):
    assert extract_video_id(url) == expected


@pytest.mark.parametrize("url", [
    "https://example.com",
    "https://www.youtube.com/playlist?list=PL123",
    "not a url",
    "",
])
def test_extract_video_id_invalid(url):
    with pytest.raises(ValueError):
        extract_video_id(url)


def test_cache_dir_for_returns_path_under_user_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_COMPREHEND_CACHE", str(tmp_path))
    result = cache_dir_for("dQw4w9WgXcQ")
    assert result == tmp_path / "dQw4w9WgXcQ"


def test_cache_dir_for_default_location(monkeypatch):
    monkeypatch.delenv("YT_COMPREHEND_CACHE", raising=False)
    result = cache_dir_for("dQw4w9WgXcQ")
    assert result.name == "dQw4w9WgXcQ"
    assert "yt-comprehend" in str(result)


@pytest.mark.parametrize("title,expected", [
    ("How I trade ORB", "how-i-trade-orb"),
    ("Trading: 10x Returns!!!", "trading-10x-returns"),
    ("  spaces   everywhere  ", "spaces-everywhere"),
    ("UPPER lower MiXeD", "upper-lower-mixed"),
    ("é unicode ñ", "unicode"),
])
def test_slugify(title, expected):
    assert slugify(title) == expected


def test_slugify_truncates_long_titles():
    long_title = "word " * 50
    result = slugify(long_title)
    assert len(result) <= 60


@pytest.mark.parametrize("title", [
    "",
    "日本語テスト",
    "!!!",
    "   ",
])
def test_slugify_empty_or_all_filtered_returns_untitled(title):
    assert slugify(title) == "untitled"
