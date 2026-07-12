#!/usr/bin/env python3
"""migrate_okf_pr2_central_bundle.py — one-off migration for the OKF
central-store-as-bundle + cross-bundle backbone change.

Two idempotent, in-place rewrites over a (overlay_dir, core_dir) pair — a
project's literature overlay directory (``project_notes_dir/literature/``)
and the two-layer store's central core (``cfg.literature_root``):

  (a) ``central: <slug>`` -> ``central: [<slug>](okf:literature/<slug>.md)``
      — the bare-slug pointer becomes rv's cross-bundle backbone link
      (see note-conventions.md's OKF-extension section). A value already in
      the link form is left untouched (idempotent).

  (b) Relocate any ``## Related papers`` section found in an overlay file
      into the matching central core (dedupe against edges the core
      already carries; drop the section from the overlay entirely). Only
      the two-layer store's central core is a valid home for a paper->paper
      edge (``note.check_two_layer_invariants`` BLOCKs a '## Related
      papers' section left in an overlay) — this is the corresponding data
      migration for that invariant's promotion to a hard gate.

No file MOVES — every file stays at its existing path; only frontmatter/
body TEXT is rewritten. Every write is unconditional-on-change only (an
overlay/core pair with nothing to migrate is left byte-identical) — safe to
re-run.

Usage:
    python scripts/migrate_okf_pr2_central_bundle.py <overlay_dir> <core_dir>
    python scripts/migrate_okf_pr2_central_bundle.py --dry-run <overlay_dir> <core_dir>

Exits 0 always; prints a summary of files touched and edges relocated.
Stdlib only (no research_vault import — this must run standalone against a
raw checkout, including the shipped package data before it's importable).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_OKF_RESERVED_FILENAMES = frozenset({"index.md", "log.md"})

_CENTRAL_BARE_RE = re.compile(r"^central:\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*$")
_RELATED_PAPERS_HEADING_RE = re.compile(
    r"^#{2,3}\s+Related papers\s*$", re.IGNORECASE | re.MULTILINE
)


def migrate_central_pointer(text: str) -> tuple[str, int]:
    """Rewrite ``central: <slug>`` -> the ``okf:`` backbone link form, one
    line at a time. A line not matching the bare-slug shape (already
    migrated, or no ``central:`` field at all) is left untouched. Returns
    ``(new_text, lines_rewritten)``."""
    out_lines: list[str] = []
    count = 0
    for line in text.splitlines(keepends=True):
        stripped_end = line[:-1] if line.endswith("\n") else line
        trailing = line[len(stripped_end):]
        m = _CENTRAL_BARE_RE.match(stripped_end)
        if m is None:
            out_lines.append(line)
            continue
        slug = m.group(1)
        out_lines.append(f"central: [{slug}](okf:literature/{slug}.md){trailing}")
        count += 1
    return "".join(out_lines), count


def _section_span(text: str, heading_re: "re.Pattern[str]") -> tuple[int, int, int] | None:
    """Return (heading_start, body_start, body_end) for the first match of
    ``heading_re`` in ``text``, or None if absent. ``body_end`` is the start
    of the next ``#``-heading, or EOF."""
    m = heading_re.search(text)
    if m is None:
        return None
    heading_start, body_start = m.start(), m.end()
    next_m = re.search(r"^#{1,3}\s+\S", text[body_start:], re.MULTILINE)
    body_end = body_start + next_m.start() if next_m else len(text)
    return heading_start, body_start, body_end


def relocate_related_papers(overlay_text: str, core_text: str) -> tuple[str, str, int]:
    """Move any ``## Related papers`` section out of ``overlay_text`` and
    append its edge lines (deduped against ``core_text``, which already has
    one) into ``core_text``. Returns ``(new_overlay_text, new_core_text,
    edges_relocated)`` — ``(overlay_text, core_text, 0)`` unchanged if the
    overlay carries no such section (the common case once PR-2's edge-write
    retarget has been live for a while — nothing to relocate)."""
    span = _section_span(overlay_text, _RELATED_PAPERS_HEADING_RE)
    if span is None:
        return overlay_text, core_text, 0
    heading_start, body_start, body_end = span

    section_body = overlay_text[body_start:body_end]
    edge_lines = [
        line.rstrip() for line in section_body.splitlines() if line.strip().startswith("- [")
    ]

    new_overlay = overlay_text[:heading_start] + overlay_text[body_end:]

    # Dedupe: an edge line already present verbatim anywhere in the core
    # (exact-line match — matches incremental_relate's own append shape)
    # is not relocated a second time.
    new_lines = [line for line in edge_lines if line.strip() not in core_text]
    if not new_lines:
        return new_overlay, core_text, 0

    new_core = core_text if core_text.endswith("\n") else core_text + "\n"
    if _RELATED_PAPERS_HEADING_RE.search(new_core):
        if not new_core.endswith("\n\n"):
            new_core += "\n" if new_core.endswith("\n") else "\n\n"
        new_core += "\n".join(new_lines) + "\n"
    else:
        new_core += "\n## Related papers\n\n" + "\n".join(new_lines) + "\n"
    return new_overlay, new_core, len(new_lines)


def migrate_pair(overlay_dir: Path, core_dir: Path, *, dry_run: bool = False) -> dict[str, int]:
    """Run both rewrites over every overlay file (skipping OKF-reserved
    filenames) matched to its central core by filename stem. A core file
    absent for an overlay's slug is surfaced (never silently skipped) —
    rewrite (a) still applies to the overlay (the pointer text itself), but
    rewrite (b) is a no-op for that pair (nothing to relocate INTO)."""
    stats = {
        "overlays_scanned": 0,
        "central_pointers_migrated": 0,
        "edges_relocated": 0,
        "overlay_files_written": 0,
        "core_files_written": 0,
        "cores_missing": 0,
    }
    if not overlay_dir.exists():
        print(f"skip (overlay dir does not exist): {overlay_dir}", file=sys.stderr)
        return stats

    for overlay_path in sorted(overlay_dir.glob("*.md")):
        if overlay_path.name in _OKF_RESERVED_FILENAMES:
            continue
        stats["overlays_scanned"] += 1
        overlay_text = overlay_path.read_text(encoding="utf-8")

        migrated_text, pointer_count = migrate_central_pointer(overlay_text)
        stats["central_pointers_migrated"] += pointer_count

        core_path = core_dir / overlay_path.name
        if not core_path.exists():
            stats["cores_missing"] += 1
            print(
                f"WARN: no central core for overlay {overlay_path} "
                f"(expected {core_path}) — pointer migration still applies, "
                f"'## Related papers' relocation skipped for this pair",
                file=sys.stderr,
            )
            if migrated_text != overlay_text:
                stats["overlay_files_written"] += 1
                if not dry_run:
                    overlay_path.write_text(migrated_text, encoding="utf-8")
                print(f"{'would rewrite' if dry_run else 'rewrote'} central: pointer in {overlay_path}")
            continue

        core_text = core_path.read_text(encoding="utf-8")
        final_overlay_text, new_core_text, relocated = relocate_related_papers(
            migrated_text, core_text
        )
        stats["edges_relocated"] += relocated

        if final_overlay_text != overlay_text:
            stats["overlay_files_written"] += 1
            if not dry_run:
                overlay_path.write_text(final_overlay_text, encoding="utf-8")
            verb = "would rewrite" if dry_run else "rewrote"
            print(f"{verb} {overlay_path} (central pointer: {pointer_count}, edges relocated OUT: {relocated})")

        if new_core_text != core_text:
            stats["core_files_written"] += 1
            if not dry_run:
                core_path.write_text(new_core_text, encoding="utf-8")
            verb = "would rewrite" if dry_run else "rewrote"
            print(f"{verb} {core_path} (edges relocated IN: {relocated})")

    return stats


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    positional = [a for a in argv if a != "--dry-run"]
    if len(positional) != 2:
        print(__doc__, file=sys.stderr)
        return 1
    overlay_dir, core_dir = Path(positional[0]), Path(positional[1])

    stats = migrate_pair(overlay_dir, core_dir, dry_run=dry_run)

    print(
        f"\n{'DRY RUN — ' if dry_run else ''}"
        f"overlays scanned: {stats['overlays_scanned']}; "
        f"central pointers migrated: {stats['central_pointers_migrated']}; "
        f"edges relocated: {stats['edges_relocated']}; "
        f"overlay files written: {stats['overlay_files_written']}; "
        f"core files written: {stats['core_files_written']}; "
        f"cores missing: {stats['cores_missing']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
