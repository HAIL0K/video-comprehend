# scripts/search_videos.py
"""Full-text search across every video analysed inside the current project.

Answers "which video had the VWAP-reclaim entry rule, and at what timestamp" without
re-watching anything. Reports are chunked two ways so hits are actionable:

  section    one chunk per report heading -- returns the synthesised finding
  transcript rolling windows of caption lines -- returns the exact spoken moment

The index is built in memory from the reports on every run using stdlib sqlite3 with
FTS5. That is a deliberate trade: a few hundred reports build in about a second, and
nothing on disk can go stale or need repairing. No embeddings, no network, no cost.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

from scripts._lib import research_dir, project_root, make_stdout_utf8

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_HEADING = re.compile(r"^(#{2,3})\s+(.*)$")
_TS_LINE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$")

TRANSCRIPT_WINDOW = 8


def parse_frontmatter(text: str) -> dict:
    """Flat key: value YAML subset -- enough for the report header we write."""
    match = _FRONTMATTER.search(text)
    if not match:
        return {}
    meta = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.startswith((" ", "-", "#")):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"')
    return meta


def chunk_report(text: str) -> list:
    """Split a report into searchable chunks tagged with section and timestamp."""
    body = _FRONTMATTER.sub("", text)
    chunks = []
    current_section = "(top)"
    buffer: list = []
    transcript_mode = False
    ts_buffer: list = []
    ts_start = ""

    def flush_prose():
        if buffer and any(b.strip() for b in buffer):
            chunks.append({
                "section": current_section,
                "timestamp": "",
                "text": " ".join(b.strip() for b in buffer if b.strip()),
            })
        buffer.clear()

    def flush_transcript():
        if ts_buffer:
            chunks.append({
                "section": "transcript",
                "timestamp": ts_start,
                "text": " ".join(ts_buffer),
            })
        ts_buffer.clear()

    for line in body.splitlines():
        heading = _HEADING.match(line)
        if heading:
            flush_prose()
            flush_transcript()
            current_section = heading.group(2).strip()
            transcript_mode = "transcript" in current_section.lower()
            continue
        if transcript_mode:
            ts_match = _TS_LINE.match(line.strip())
            if ts_match:
                if not ts_buffer:
                    ts_start = ts_match.group(1)
                ts_buffer.append(ts_match.group(2))
                if len(ts_buffer) >= TRANSCRIPT_WINDOW:
                    flush_transcript()
            continue
        buffer.append(line)
    flush_prose()
    flush_transcript()
    return chunks


def build_index(reports: list) -> sqlite3.Connection:
    """In-memory FTS5 index over the given report paths."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE VIRTUAL TABLE chunks USING fts5("
        "report, title, url, section, timestamp, text, tokenize='porter unicode61')"
    )
    rows = []
    for report in reports:
        try:
            text = Path(report).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta = parse_frontmatter(text)
        title = meta.get("title", Path(report).stem)
        url = meta.get("url", meta.get("source", ""))
        for chunk in chunk_report(text):
            rows.append((str(report), title, url, chunk["section"],
                         chunk["timestamp"], chunk["text"]))
    conn.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


_FTS_OPERATORS = ('"', "*", "(", ")", " OR ", " AND ", " NOT ", " NEAR")


def prepare_query(query: str) -> str:
    """Make an everyday query safe for FTS5 without disabling its syntax.

    FTS5 gives `-`, `:` and friends special meaning, so a natural search like
    `multi-horizon momentum` fails outright with "no such column: horizon". This
    domain is full of hyphenated terms — mean-reversion, walk-forward, risk-adjusted —
    so bare words get quoted into phrases, which tokenizes the punctuation normally
    and keeps the implicit AND between terms.

    A query that already uses FTS syntax (quotes, wildcards, OR/NEAR) is passed
    through untouched, so power queries still work.
    """
    if any(op in query for op in _FTS_OPERATORS):
        return query
    tokens = [t for t in query.split() if t.strip()]
    if not tokens:
        return query
    return " ".join('"{0}"'.format(t.replace('"', "")) for t in tokens)


def collect_reports(root: Path) -> list:
    """Every report markdown file in the project's research dir."""
    directory = research_dir(root)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.md") if not p.name.startswith("_"))


def search(query: str, root: Path | str | None = None, limit: int = 10,
           section: str | None = None) -> dict:
    """Return ranked hits. `section` filters to e.g. 'transcript' or 'Claims'."""
    base = Path(root) if root else project_root()
    reports = collect_reports(base)
    if not reports:
        return {"project": str(base), "reports": 0, "hits": [],
                "note": "no video reports found in {0}".format(research_dir(base))}
    conn = build_index(reports)
    sql = (
        "SELECT report, title, url, section, timestamp, "
        "snippet(chunks, 5, '>>', '<<', ' ... ', 24) AS snip, bm25(chunks) AS rank "
        "FROM chunks WHERE chunks MATCH ? "
    )
    params: list = [prepare_query(query)]
    if section:
        sql += "AND section LIKE ? "
        params.append("%{0}%".format(section))
    sql += "ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        return {"project": str(base), "reports": len(reports), "hits": [],
                "error": "bad query: {0}".format(exc)}
    hits = [
        {"report": r[0], "title": r[1], "url": r[2], "section": r[3],
         "timestamp": r[4], "snippet": r[5].replace("\n", " ")}
        for r in rows
    ]
    return {"project": str(base), "reports": len(reports), "hits": hits}


def main() -> int:
    import argparse

    make_stdout_utf8()

    ap = argparse.ArgumentParser(description="Search analysed videos in this project.")
    ap.add_argument("query", nargs="+")
    ap.add_argument("--root", default=None)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--section", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = search(" ".join(args.query), args.root, args.limit, args.section)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    if result.get("note"):
        print(result["note"])
        return 0
    if result.get("error"):
        print(result["error"], file=sys.stderr)
        return 1
    print("{0} hit(s) across {1} report(s) in {2}".format(
        len(result["hits"]), result["reports"], result["project"]))
    for i, hit in enumerate(result["hits"], 1):
        where = hit["timestamp"] or hit["section"]
        print("\n[{0}] {1}  ({2})".format(i, hit["title"], where))
        print("    {0}".format(hit["snippet"]))
        print("    {0}".format(hit["report"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
