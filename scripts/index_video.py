# scripts/index_video.py
"""Append a finished report to the analysing project's own video index.

Memory is deliberately project-scoped rather than one global database. A repo
accumulates the corpus that is relevant to it, so any future session opened in that
repo inherits the whole video context -- including videos added later -- without
dragging in unrelated material from other projects.

The index is a JSONL file that is only ever appended to, which keeps it compatible
with the workspace rule that docs/research content is immutable, and keeps it
readable and diffable in git. It carries no content of its own: it is a pointer list,
and search_videos.py reads the reports themselves.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts._lib import research_dir, project_root, make_stdout_utf8

INDEX_NAME = "_index.jsonl"


def index_path(root: Path | str | None = None) -> Path:
    return research_dir(root) / INDEX_NAME


def read_index(root: Path | str | None = None) -> list:
    """All entries, oldest first. Malformed lines are skipped, not fatal."""
    path = index_path(root)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def append_entry(entry: dict, root: Path | str | None = None) -> dict:
    """Append one video record. Re-analysing a source supersedes rather than edits.

    Older entries for the same source_id are left in place -- the newest wins on
    read -- so the file stays strictly append-only.
    """
    path = index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "indexed": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **entry,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def latest_entries(root: Path | str | None = None) -> list:
    """Newest record per source_id, most recently indexed first."""
    by_source = {}
    for entry in read_index(root):
        by_source[entry.get("source_id", entry.get("report"))] = entry
    return sorted(by_source.values(), key=lambda e: e.get("indexed", ""), reverse=True)


def main() -> int:
    import argparse

    make_stdout_utf8()

    ap = argparse.ArgumentParser(description="Append a report to the project video index.")
    ap.add_argument("--source-id")
    ap.add_argument("--title")
    ap.add_argument("--url", default="")
    ap.add_argument("--channel", default="")
    ap.add_argument("--duration", default="")
    ap.add_argument("--profile", default="")
    ap.add_argument("--report", help="path to the written report")
    ap.add_argument("--frames", type=int, default=0)
    ap.add_argument("--transcript-source", default="captions")
    ap.add_argument("--tags", default="", help="comma-separated")
    ap.add_argument("--root", default=None)
    ap.add_argument("--list", action="store_true", help="print the index instead")
    args = ap.parse_args()

    root = Path(args.root) if args.root else project_root()
    if args.list:
        print(json.dumps(latest_entries(root), indent=2))
        return 0

    # Required only for an append; --list needs none of them.
    missing = [
        flag for flag, value in
        (("--source-id", args.source_id), ("--title", args.title), ("--report", args.report))
        if not value
    ]
    if missing:
        ap.error("the following arguments are required: {0}".format(", ".join(missing)))

    report = Path(args.report)
    try:
        stored_report = str(report.resolve().relative_to(Path(root).resolve()))
    except ValueError:
        stored_report = str(report.resolve())

    record = append_entry({
        "source_id": args.source_id,
        "title": args.title,
        "url": args.url,
        "channel": args.channel,
        "duration": args.duration,
        "profile": args.profile,
        "report": stored_report,
        "frames": args.frames,
        "transcript_source": args.transcript_source,
        "tags": [t.strip() for t in args.tags.split(",") if t.strip()],
    }, root)
    print(json.dumps({"indexed": record["indexed"], "index": str(index_path(root))}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
