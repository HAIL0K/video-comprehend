# scripts/render_transcript.py
"""Render captions.vtt as the report's transcript appendix.

YouTube auto-captions are a rolling window: each cue repeats the previous one with a
few words appended. Pasting them straight into a report roughly doubles its length
and makes search snippets stutter. This collapses the overlap at word level while
keeping each line's real spoken timestamp.

Output is exactly the appendix format the report expects and search_videos.py parses:

    [HH:MM:SS] the words spoken from that moment

Emitting this from one place means the agent never has to clean cues by hand, and
every report is chunked identically for search.
"""
from __future__ import annotations

import sys
from pathlib import Path

from scripts._lib import format_hms, make_stdout_utf8
from scripts.select_keyframes import parse_vtt, clean_cues


def render(captions_path: Path, start: float | None = None,
           end: float | None = None) -> str:
    """Return the appendix body: one cleaned line per cue, timestamp-prefixed."""
    cues = parse_vtt(Path(captions_path))
    lo = start if start is not None else 0.0
    hi = end if end is not None else float("inf")
    cues = [c for c in cues if lo <= c.start_seconds <= hi]
    return "\n".join(
        "[{0}] {1}".format(format_hms(c.start_seconds), c.text)
        for c in clean_cues(cues)
    )


def stats(captions_path: Path) -> dict:
    """How much redundancy the cleanup removed -- worth reporting in caveats."""
    raw = parse_vtt(Path(captions_path))
    cleaned = clean_cues(raw)
    raw_words = sum(len(c.text.split()) for c in raw)
    clean_words = sum(len(c.text.split()) for c in cleaned)
    return {
        "raw_cues": len(raw),
        "clean_lines": len(cleaned),
        "raw_words": raw_words,
        "clean_words": clean_words,
        "redundancy_removed": (
            round(1 - clean_words / raw_words, 3) if raw_words else 0.0
        ),
    }


def main() -> int:
    import argparse
    import json

    make_stdout_utf8()
    ap = argparse.ArgumentParser(description="Render a clean transcript appendix.")
    ap.add_argument("captions")
    ap.add_argument("--out", default=None, help="write here instead of stdout")
    ap.add_argument("--from", dest="start", type=float, default=None)
    ap.add_argument("--to", dest="end", type=float, default=None)
    ap.add_argument("--stats", action="store_true", help="print stats only")
    args = ap.parse_args()

    if args.stats:
        print(json.dumps(stats(Path(args.captions))))
        return 0

    body = render(Path(args.captions), args.start, args.end)
    if args.out:
        Path(args.out).write_text(body + "\n", encoding="utf-8")
        print(json.dumps({"out": args.out, **stats(Path(args.captions))}))
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
