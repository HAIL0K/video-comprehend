"""Unit tests for the deterministic parts of the video-comprehend pipeline.

Anything touching ffmpeg, yt-dlp or the network is out of scope here -- these cover
the logic that decides *which* frames get looked at and *how* results are found again.

Run:  python -m pytest tests -q      (from the skill root)
      python tests/test_pipeline.py  (no pytest needed)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import _lib, detect_scenes, dedup_frames, select_keyframes, search_videos

VTT = """WEBVTT

00:00:01.000 --> 00:00:04.000
Welcome back to the channel

00:00:05.000 --> 00:00:08.000
Welcome back to the channel

00:00:30.000 --> 00:00:34.000
Now look at this chart, we broke 4210 and held

00:01:10.000 --> 00:01:14.000
so anyway that is roughly the idea
"""


def _write_vtt(tmp: Path) -> Path:
    path = tmp / "captions.vtt"
    path.write_text(VTT, encoding="utf-8")
    return path


# --- source resolution -------------------------------------------------------

def test_youtube_url_resolves_to_canonical_id():
    src = _lib.resolve_source("https://youtu.be/4IJgIivp_I4?t=30")
    assert src.kind == "youtube" and src.id == "4IJgIivp_I4"


def test_non_youtube_url_gets_stable_hashed_id():
    a = _lib.resolve_source("https://www.tiktok.com/@x/video/123")
    b = _lib.resolve_source("https://www.tiktok.com/@x/video/123")
    c = _lib.resolve_source("https://www.tiktok.com/@x/video/999")
    assert a.kind == "url" and a.id == b.id and a.id != c.id


def test_missing_local_path_is_rejected():
    try:
        _lib.resolve_source("Z:/definitely/not/here.mp4")
    except ValueError:
        return
    raise AssertionError("expected ValueError for a non-existent path")


def test_parse_time_accepts_all_three_shapes():
    assert _lib.parse_time("90") == 90
    assert _lib.parse_time("1:30") == 90
    assert _lib.parse_time("01:00:30") == 3630
    assert _lib.parse_time(None) is None


# --- candidate generation ----------------------------------------------------

def test_showinfo_parsing_dedupes_and_sorts():
    stderr = "x pts_time:12.5 y\nz pts_time:3.25 w\nq pts_time:12.5 e\n"
    assert detect_scenes.parse_showinfo(stderr) == [3.25, 12.5]


def test_density_floor_starts_mid_segment_and_stays_in_bounds():
    floors = detect_scenes.density_floor(100.0, 40.0)
    assert floors == [20.0, 60.0]
    assert all(0 < f < 100 for f in floors)


def test_rolling_duplicate_captions_are_collapsed(tmp_path):
    cues = select_keyframes.parse_vtt(_write_vtt(tmp_path))
    assert len(cues) == 4
    assert len(select_keyframes.dedupe_cues(cues)) == 3


ROLLING_VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000
so if you look

00:00:03.000 --> 00:00:05.000
so if you look at this chart

00:00:05.000 --> 00:00:07.000
at this chart we broke 4210

00:00:07.000 --> 00:00:09.000
we broke 4210 and held it
"""


def test_incrementally_growing_captions_are_collapsed(tmp_path):
    # YouTube auto-captions grow rather than repeat: dropping only exact matches
    # left the corpus roughly half duplicated on the first live run.
    path = tmp_path / "rolling.vtt"
    path.write_text(ROLLING_VTT, encoding="utf-8")
    cues = select_keyframes.parse_vtt(path)
    kept = select_keyframes.dedupe_cues(cues)
    texts = [c.text for c in kept]
    assert "so if you look" not in texts, "strict prefix must be dropped"
    assert "so if you look at this chart" in texts, "longest form must survive"


def test_dedupe_keeps_multiword_deixis_intact(tmp_path):
    path = tmp_path / "rolling.vtt"
    path.write_text(ROLLING_VTT, encoding="utf-8")
    kept = select_keyframes.dedupe_cues(select_keyframes.parse_vtt(path))
    # scoring must still see the whole phrase, not a fragment of it
    assert any(select_keyframes.score_cue(c, select_keyframes.CHART_TERMS) >= 3
               for c in kept)


def test_clean_cues_removes_word_overlap_and_keeps_timestamps(tmp_path):
    path = tmp_path / "rolling.vtt"
    path.write_text(ROLLING_VTT, encoding="utf-8")
    cleaned = select_keyframes.clean_cues(select_keyframes.parse_vtt(path))
    joined = " ".join(c.text for c in cleaned)
    assert joined == "so if you look at this chart we broke 4210 and held it"
    assert cleaned[0].start_seconds == 1.0
    # the new words carry the timestamp where they were actually spoken
    assert any(c.start_seconds == 7.0 and "held" in c.text for c in cleaned)


def test_clean_cues_drops_pure_repeats(tmp_path):
    path = _write_vtt(tmp_path)
    cleaned = select_keyframes.clean_cues(select_keyframes.parse_vtt(path))
    joined = " ".join(c.text for c in cleaned).lower()
    assert joined.count("welcome back to the channel") == 1


def test_render_transcript_emits_parseable_appendix(tmp_path):
    from scripts import render_transcript
    path = tmp_path / "rolling.vtt"
    path.write_text(ROLLING_VTT, encoding="utf-8")
    body = render_transcript.render(path)
    assert body.startswith("[00:00:01] so if you look")
    # every line must match the format search_videos.py indexes on
    for line in body.splitlines():
        assert search_videos._TS_LINE.match(line), line
    st = render_transcript.stats(path)
    assert st["clean_words"] < st["raw_words"]
    assert st["redundancy_removed"] > 0.3


def test_deixis_plus_number_outscores_filler(tmp_path):
    cues = select_keyframes.parse_vtt(_write_vtt(tmp_path))
    terms = select_keyframes.CHART_TERMS
    pointed = next(c for c in cues if "look at this chart" in c.text)
    filler = next(c for c in cues if "anyway" in c.text)
    assert select_keyframes.score_cue(pointed, terms) > select_keyframes.score_cue(filler, terms)


def test_generic_video_still_scores_without_chart_terms():
    cue = select_keyframes.Cue(0.0, 2.0, "notice how the diagram splits into 3 services")
    general = select_keyframes.PROFILES["general"].terms
    assert select_keyframes.score_cue(cue, general) > 0


# --- budget ------------------------------------------------------------------

def test_budget_grows_with_length_and_respects_ceiling():
    chart = select_keyframes.PROFILES["chart"]
    short = select_keyframes.adaptive_budget(180, 10, chart)
    long = select_keyframes.adaptive_budget(3600, 200, chart)
    assert chart.floor_budget <= short < long <= chart.ceiling_budget
    assert long == chart.ceiling_budget


def test_budget_never_drops_below_floor_for_a_tiny_clip():
    general = select_keyframes.PROFILES["general"]
    assert select_keyframes.adaptive_budget(15, 0, general) == general.floor_budget


# --- merging and distribution ------------------------------------------------

def test_channel_agreement_beats_any_single_channel():
    candidates = [
        {"timestamp": 10.0, "source": "semantic", "score": 4.0, "reason": "look at this"},
        {"timestamp": 11.0, "source": "scene", "score": 2.0, "reason": "scene change"},
        {"timestamp": 90.0, "source": "semantic", "score": 4.0, "reason": "solo callout"},
    ]
    merged = select_keyframes.merge_candidates(candidates)
    agreed = next(m for m in merged if m["timestamp"] == 10.0)
    solo = next(m for m in merged if m["timestamp"] == 90.0)
    assert "+" in agreed["source"]
    assert agreed["score"] > solo["score"]
    # the speaker's callout anchors the cluster, not the cut
    assert agreed["reason"] == "look at this"


def test_distribute_covers_the_timeline_and_respects_budget():
    merged = [
        {"timestamp": float(t), "source": "floor", "score": 1.0, "reason": "x"}
        for t in range(0, 600, 10)
    ]
    picked = select_keyframes.distribute(merged, 6, 600.0)
    assert len(picked) == 6
    assert picked == sorted(picked, key=lambda c: c["timestamp"])
    assert picked[-1]["timestamp"] - picked[0]["timestamp"] > 400


def test_leftover_budget_goes_to_high_scoring_moments():
    # All candidates crammed into the first minute of a ten-minute video: bins are
    # mostly empty, so the slack must be spent rather than silently lost.
    merged = [
        {"timestamp": float(t), "source": "semantic", "score": float(t), "reason": "x"}
        for t in range(0, 60, 5)
    ]
    picked = select_keyframes.distribute(merged, 8, 600.0)
    assert len(picked) == 8


def test_end_to_end_selection_prefers_the_pointed_moment(tmp_path):
    captions = _write_vtt(tmp_path)
    meta = tmp_path / "meta.json"
    meta.write_text('{"duration": 90}', encoding="utf-8")
    scenes = tmp_path / "scenes.json"
    scenes.write_text('{"scene": [31.0], "floor": [45.0]}', encoding="utf-8")
    out = select_keyframes.select(captions, meta, scenes, "chart", budget=2)
    stamps = [k["timestamp"] for k in out["keyframes"]]
    assert 30.0 in stamps            # the "look at this chart" moment survives
    assert out["counts"]["selected"] == 2
    assert out["profile"] == "chart"


def test_window_excludes_out_of_range_candidates(tmp_path):
    captions = _write_vtt(tmp_path)
    meta = tmp_path / "meta.json"
    meta.write_text('{"duration": 90}', encoding="utf-8")
    out = select_keyframes.select(captions, meta, None, "chart", start=60.0, end=90.0)
    assert all(60.0 <= k["timestamp"] <= 90.0 for k in out["keyframes"])


# --- dedup -------------------------------------------------------------------

def _canvas(value=0.5):
    import numpy as np
    return np.full((dedup_frames.THUMB, dedup_frames.THUMB, 3), value, dtype="float32")


def test_identical_frames_are_dropped():
    base = _canvas()
    keep, _reason, _g, _t = dedup_frames.is_novel(
        base.copy(), [base], dedup_frames.PROFILES["chart"])
    assert keep is False


def test_chart_profile_keeps_a_single_new_trendline():
    # One horizontal line drawn across an otherwise unchanged chart -- a support
    # level being marked up. Global delta is ~1.6%, far under any whole-image gate,
    # which is exactly why crv-style dedup throws this frame away.
    base = _canvas()
    drawn = base.copy()
    drawn[8, :] = 1.0
    chart_keep, _r, _g, _t = dedup_frames.is_novel(
        drawn, [base], dedup_frames.PROFILES["chart"])
    general_keep, _r2, _g2, _t2 = dedup_frames.is_novel(
        drawn, [base], dedup_frames.PROFILES["general"])
    assert chart_keep is True, "chart profile must survive a drawn level"
    assert general_keep is False, "general profile is intentionally stricter"


def test_general_profile_still_keeps_a_genuine_cut():
    base = _canvas(0.2)
    cut = _canvas(0.8)
    keep, _r, _g, _t = dedup_frames.is_novel(
        cut, [base], dedup_frames.PROFILES["general"])
    assert keep is True


def test_dedup_compares_against_several_recent_frames():
    base = _canvas(0.2)
    other = _canvas(0.9)
    # a cutaway, then a return to the original shot
    keep, _r, _g, _t = dedup_frames.is_novel(
        base.copy(), [base, other], dedup_frames.PROFILES["chart"])
    assert keep is False, "a shot already seen must not come back after a cutaway"


# --- project memory ----------------------------------------------------------

REPORT = """---
title: "Trading the Open"
url: https://youtu.be/abc
---

# Trading the Open

## Claims & evidence
- Wait for the VWAP reclaim before entering

## Appendix: Full transcript

[00:12:30] we wait for the vwap reclaim
[00:12:33] and then the first pullback
"""


def test_report_chunking_splits_sections_and_transcript(tmp_path):
    chunks = search_videos.chunk_report(REPORT)
    sections = {c["section"] for c in chunks}
    assert "Claims & evidence" in sections
    transcript = [c for c in chunks if c["section"] == "transcript"]
    assert transcript and transcript[0]["timestamp"] == "00:12:30"


def test_frontmatter_is_parsed():
    meta = search_videos.parse_frontmatter(REPORT)
    assert meta["title"] == "Trading the Open"
    assert meta["url"] == "https://youtu.be/abc"


def test_search_finds_the_moment_and_returns_a_timestamp(tmp_path):
    research = tmp_path / "docs" / "research" / "video"
    research.mkdir(parents=True)
    (research / "abc-trading-the-open.md").write_text(REPORT, encoding="utf-8")
    result = search_videos.search("vwap reclaim", tmp_path)
    assert result["reports"] == 1
    assert result["hits"], "expected at least one hit"
    assert any(h["timestamp"] == "00:12:30" for h in result["hits"])
    assert any(h["title"] == "Trading the Open" for h in result["hits"])


def test_hyphenated_query_does_not_break_fts(tmp_path):
    # FTS5 treats '-' and ':' as syntax; an everyday query like "multi-horizon
    # momentum" failed with "no such column: horizon" until queries were quoted.
    research = tmp_path / "docs" / "research" / "video"
    research.mkdir(parents=True)
    (research / "x-report.md").write_text(REPORT, encoding="utf-8")
    result = search_videos.search("multi-horizon vwap", tmp_path)
    assert "error" not in result, result.get("error")


def test_prepare_query_quotes_bare_terms_and_passes_syntax_through():
    assert search_videos.prepare_query("multi-horizon momentum") == '"multi-horizon" "momentum"'
    assert search_videos.prepare_query('"exact phrase"') == '"exact phrase"'
    assert search_videos.prepare_query("vwap OR twap") == "vwap OR twap"


def test_search_on_empty_project_is_not_an_error(tmp_path):
    result = search_videos.search("anything", tmp_path)
    assert result["hits"] == [] and "note" in result


if __name__ == "__main__":
    import tempfile, traceback

    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            if "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                with tempfile.TemporaryDirectory() as td:
                    fn(Path(td))
            else:
                fn()
            passed += 1
        except Exception:
            failed += 1
            print("FAIL {0}".format(name))
            traceback.print_exc()
    print("\n{0} passed, {1} failed".format(passed, failed))
    sys.exit(1 if failed else 0)
