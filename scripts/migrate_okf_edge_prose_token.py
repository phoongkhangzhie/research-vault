#!/usr/bin/env python3
"""migrate_okf_edge_prose_token.py — one-off migration for the OKF edge
relationship-type grammar change: link-prefix tag -> prose token.

Old (non-conformant): `- [TAG] [display](/dir/slug.md) — reason`
New (OKF-conformant):  `- [display](/dir/slug.md) — TAG: reason`

Rewrites every `.md` file under the given path(s) in place. A line is
rewritten only if it matches the OLD grammar exactly (a bracket-tag
immediately after the bullet, followed by a markdown link to `/literature/`
or `/concepts/`); every other line — including a line already in the new
form — is left untouched (idempotent, safe to re-run).

Usage:
    python scripts/migrate_okf_edge_prose_token.py <path> [<path> ...]
    python scripts/migrate_okf_edge_prose_token.py --dry-run <path>

Exits 0 always; prints a summary of files touched and lines rewritten.
Stdlib only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_OLD_EDGE_LINE_RE = re.compile(
    r"^(?P<prefix>-\s*)\[(?P<tag>SUPPORTS|CONTRADICTS|PARTIAL|EXTENDS)\]\s+"
    r"(?P<link>\[[^\]]+\]\(/(?:literature|concepts)/[A-Za-z0-9][A-Za-z0-9_.\-]*\.md\))"
    r"(?P<sep>\s*(?:—|-)\s*)(?P<rest>.+)$"
)


def migrate_text(text: str) -> tuple[str, int]:
    """Rewrite every OLD-grammar edge line in ``text``. Returns (new_text,
    count_of_lines_rewritten)."""
    out_lines: list[str] = []
    count = 0
    for line in text.splitlines(keepends=True):
        stripped_end = line[:-1] if line.endswith("\n") else line
        trailing_newline = line[len(stripped_end):]
        m = _OLD_EDGE_LINE_RE.match(stripped_end)
        if m is None:
            out_lines.append(line)
            continue
        new_line = (
            f"{m.group('prefix')}{m.group('link')}{m.group('sep')}"
            f"{m.group('tag')}: {m.group('rest')}{trailing_newline}"
        )
        out_lines.append(new_line)
        count += 1
    return "".join(out_lines), count


def migrate_file(path: Path, *, dry_run: bool = False) -> int:
    text = path.read_text(encoding="utf-8")
    new_text, count = migrate_text(text)
    if count and not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return count


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    paths = [Path(a) for a in argv if a != "--dry-run"]
    if not paths:
        print(__doc__, file=sys.stderr)
        return 1

    total_files = 0
    total_lines = 0
    for root in paths:
        if not root.exists():
            print(f"skip (does not exist): {root}", file=sys.stderr)
            continue
        candidates = [root] if root.is_file() else sorted(root.rglob("*.md"))
        for md_path in candidates:
            count = migrate_file(md_path, dry_run=dry_run)
            if count:
                total_files += 1
                total_lines += count
                verb = "would rewrite" if dry_run else "rewrote"
                print(f"{verb} {count} edge line(s) in {md_path}")

    print(
        f"\n{'DRY RUN — ' if dry_run else ''}"
        f"{total_lines} edge line(s) rewritten across {total_files} file(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
