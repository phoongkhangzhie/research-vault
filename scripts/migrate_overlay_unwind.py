#!/usr/bin/env python3
"""migrate_overlay_unwind.py — the 0.3.2 overlay-unwind migration: collapse a
two-layer literature note (a central core + a per-project overlay glued
by a ``central:`` pointer) into ONE shared-canonical note.

Per pair (matched by filename stem, ``overlay_dir/<citekey>.md`` <->
``core_dir/<citekey>.md``):

  (a) The core's own frontmatter + body (intrinsic paper facts, ``##
      Result``/``## Key equations``/``## Related papers``) is kept
      UNCHANGED except for one drop: any ``central:`` field on the core
      (there should never be one, but a hand-authored or partially-
      migrated fixture might carry it) is stripped — nothing left to
      point at.
  (b) The overlay's ``## Concept edges`` section (paper->concept typed
      edges) is appended onto the core's body — it is now intra-shared
      content, the same shared note.
  (c) The overlay's ``role``/``position`` fields (RQ-relative narration)
      relocate to a CURATED entry in the adopting project's MOC
      (``moc_dir/<moc_slug>.md``) — appended under a "## Literature roles"
      heading, one bullet per paper, never silently dropped. If the MOC
      file does not exist yet, it is created with a minimal frontmatter
      block.
  (d) The overlay's ``in_corpus_of:`` field is DROPPED — membership is
      the mechanical corpus ledger's job (``review/ledger.py``), never a
      field on the note itself, pre- or post-unwind.
  (e) The overlay file is DELETED — there is nothing left in it once (b)/
      (c)/(d) have relocated its only real content; the file itself was
      never anything but that content plus the (a-adjacent) ``central:``
      pointer + intrinsic-field placeholders.

Idempotent: an overlay file already gone (or absent to begin with) for a
given stem is a correct no-op — a re-run after a partial migration only
processes whatever pairs remain. A core file with no matching overlay is
left completely untouched (there is nothing to migrate for it — it may
already be single-note, or migrated in a prior run). Every write is
unconditional-on-change (a pair with nothing to migrate never gets its
bytes rewritten) — safe to re-run against an already-migrated tree.

Usage:
    python scripts/migrate_overlay_unwind.py <overlay_dir> <core_dir> <moc_dir> [--moc-slug NAME]
    python scripts/migrate_overlay_unwind.py --dry-run <overlay_dir> <core_dir> <moc_dir>

Exits 0 always; prints a summary of files touched, and the full text of
every write under ``--dry-run`` so a human can eyeball the diff before
committing to the real run. Stdlib only (no research_vault import — this
must run standalone against a raw checkout, including the shipped package
data before it's importable).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_OKF_RESERVED_FILENAMES = frozenset({"index.md", "log.md"})

_CONCEPT_EDGES_HEADING_RE = re.compile(
    r"^#{2,3}\s+Concept edges\s*$", re.IGNORECASE | re.MULTILINE
)
_LITERATURE_ROLES_HEADING_RE = re.compile(
    r"^##\s+Literature roles\s*$", re.IGNORECASE | re.MULTILINE
)

# The stock "CENTRAL CORE / two-layer store" HTML-comment preamble every
# pre-unwind core note carries (note.py's old ``_literature_core_body``
# template). Purely explanatory — never load-bearing — but leaving it in a
# freshly-merged shared-canonical note would misleadingly describe a model
# that no longer exists. Scrubbed as a block (matched loosely, tolerant of
# the exact wording drifting slightly across older fixtures): every
# ``<!-- ... -->`` line up to (not including) the first ``##`` heading.
_STALE_PREAMBLE_RE = re.compile(
    r"(?:^<!--.*-->\n)+(?=\n*##)", re.MULTILINE
)


def _scrub_stale_two_layer_preamble(body: str) -> str:
    """Drop the leading run of HTML-comment lines (the old two-layer
    explainer block) at the top of a core note's body, if present. A body
    with no such block (already migrated, or authored fresh) is returned
    unchanged."""
    return _STALE_PREAMBLE_RE.sub("", body, count=1)

# A minimal, permissive frontmatter parser — mirrors note._parse_frontmatter's
# flat-scalar contract closely enough for this one-off migration's needs
# (this script is intentionally standalone / stdlib-only, no research_vault
# import — see module docstring).
_FIELD_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s?(.*)$")


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Flat-scalar frontmatter parse (no list support — this migration only
    reads scalar fields: role/position/in_corpus_of/central/title/type).
    Returns (fields, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip("\n")
    body = text[end + 4:]
    if body.startswith("\n"):
        body = body[1:]
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = _FIELD_LINE_RE.match(line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields, body


def _section_span(text: str, heading_re: "re.Pattern[str]") -> tuple[int, int, int] | None:
    """Return (heading_start, body_start, body_end) for the first match of
    ``heading_re`` in ``text``, or None if absent."""
    m = heading_re.search(text)
    if m is None:
        return None
    heading_start, body_start = m.start(), m.end()
    next_m = re.search(r"^#{1,3}\s+\S", text[body_start:], re.MULTILINE)
    body_end = body_start + next_m.start() if next_m else len(text)
    return heading_start, body_start, body_end


def _strip_field_line(fm_block: str, key: str) -> str:
    """Remove a ``key: ...`` line from a raw frontmatter block (used to
    drop ``central:``/``in_corpus_of:`` before re-rendering)."""
    out = []
    for line in fm_block.splitlines():
        m = _FIELD_LINE_RE.match(line)
        if m and m.group(1) == key:
            continue
        out.append(line)
    return "\n".join(out)


def merge_core_and_overlay(core_text: str, overlay_text: str) -> tuple[str, dict[str, str]]:
    """Merge a two-layer pair into ONE shared-canonical note's text.

    Returns ``(new_core_text, relocation)`` where ``relocation`` carries
    whatever needs to move OUT of this note entirely — ``role``/
    ``position`` (-> the adopting project's MOC, handled by the caller;
    never written here) so nothing is silently dropped.
    """
    core_fields_raw = core_text[3:core_text.find("\n---", 3)] if core_text.startswith("---") else ""
    _core_fields, core_body = _parse_frontmatter(core_text)
    overlay_fields, overlay_body = _parse_frontmatter(overlay_text)

    # (a) Drop a stray `central:` on the core, if present (there shouldn't
    # be one on a genuine core, but be defensive — never leave a dangling
    # pointer with nothing to point at).
    new_fm_block = _strip_field_line(core_fields_raw.strip("\n"), "central")

    # Scrub the stale "CENTRAL CORE / two-layer store" explainer comment —
    # never load-bearing, but misleading once the note is single-note.
    new_core_body = _scrub_stale_two_layer_preamble(core_body)

    # (b) Relocate the overlay's '## Concept edges' section onto the core body.
    span = _section_span(overlay_body, _CONCEPT_EDGES_HEADING_RE)
    if span is not None:
        heading_start, _body_start, body_end = span
        concept_section = overlay_body[heading_start:body_end].rstrip() + "\n"
        if not new_core_body.endswith("\n"):
            new_core_body += "\n"
        if not new_core_body.endswith("\n\n"):
            new_core_body += "\n"
        new_core_body += concept_section

    new_core_text = "---\n" + new_fm_block.strip("\n") + "\n---\n\n" + new_core_body.lstrip("\n")

    relocation = {
        "role": overlay_fields.get("role", ""),
        "position": overlay_fields.get("position", ""),
        # (d) in_corpus_of is DROPPED entirely — membership lives in the
        # corpus ledger, never a note field. Surfaced here (never silent)
        # so the caller can report it, even though nothing writes it anywhere.
        "in_corpus_of_dropped": overlay_fields.get("in_corpus_of", ""),
    }
    return new_core_text, relocation


def render_moc_role_bullet(citekey: str, role: str, position: str) -> str:
    role_part = f" **[{role}]**" if role else ""
    position_part = f" — {position}" if position else ""
    return f"- [{citekey}](/literature/{citekey}.md){role_part}{position_part}\n"


def append_role_to_moc(moc_path: Path, citekey: str, role: str, position: str) -> str:
    """Return the NEW moc text with this paper's role/position appended
    under a '## Literature roles' heading — creates the heading (and the
    file, with a minimal frontmatter block, if it doesn't exist yet) when
    absent. Idempotent: a citekey already bulleted under the heading is
    left untouched (never duplicated) — matched by the exact link target
    ``(/literature/<citekey>.md)``, the same identity every OKF edge uses.
    """
    if moc_path.exists():
        text = moc_path.read_text(encoding="utf-8")
    else:
        text = "---\ntype: mocs\ntitle: Literature roles\n---\n\n"

    marker = f"(/literature/{citekey}.md)"
    if marker in text:
        return text  # already recorded — idempotent no-op

    bullet = render_moc_role_bullet(citekey, role, position)
    span = _section_span(text, _LITERATURE_ROLES_HEADING_RE)
    if span is None:
        if not text.endswith("\n"):
            text += "\n"
        if not text.endswith("\n\n"):
            text += "\n"
        text += "## Literature roles\n\n" + bullet
    else:
        _heading_start, _body_start, body_end = span
        text = text[:body_end].rstrip("\n") + "\n" + bullet + text[body_end:]
    return text


def migrate_pair(
    overlay_dir: Path, core_dir: Path, moc_dir: Path, *,
    moc_slug: str = "literature-roles",
    dry_run: bool = False,
) -> dict[str, int]:
    """Run the full overlay-unwind migration over every overlay file
    (skipping OKF-reserved filenames) matched to its shared note by
    filename stem."""
    stats = {
        "overlays_scanned": 0,
        "notes_merged": 0,
        "concept_edges_relocated": 0,
        "roles_relocated_to_moc": 0,
        "in_corpus_of_dropped": 0,
        "overlay_files_deleted": 0,
        "cores_missing": 0,
    }
    if not overlay_dir.exists():
        print(f"skip (overlay dir does not exist — nothing to migrate): {overlay_dir}", file=sys.stderr)
        return stats

    moc_path = moc_dir / f"{moc_slug}.md"
    moc_text_current = moc_path.read_text(encoding="utf-8") if moc_path.exists() else None

    for overlay_path in sorted(overlay_dir.glob("*.md")):
        if overlay_path.name in _OKF_RESERVED_FILENAMES:
            continue
        stats["overlays_scanned"] += 1
        citekey = overlay_path.stem
        overlay_text = overlay_path.read_text(encoding="utf-8")

        core_path = core_dir / overlay_path.name
        if not core_path.exists():
            stats["cores_missing"] += 1
            print(
                f"WARN: no shared literature note for overlay {overlay_path} "
                f"(expected {core_path}) — nothing to merge into; overlay left "
                f"in place (never silently deleted with content unmigrated).",
                file=sys.stderr,
            )
            continue

        core_text = core_path.read_text(encoding="utf-8")
        new_core_text, relocation = merge_core_and_overlay(core_text, overlay_text)
        concept_edges_moved = (
            "## Concept edges" not in core_text and "## Concept edges" in new_core_text
        )

        if new_core_text != core_text:
            stats["notes_merged"] += 1
            if concept_edges_moved:
                stats["concept_edges_relocated"] += 1
            if not dry_run:
                core_path.write_text(new_core_text, encoding="utf-8")
            verb = "would rewrite" if dry_run else "rewrote"
            print(f"{verb} {core_path} (merged overlay content)")
            if dry_run:
                print("--- new content ---")
                print(new_core_text)
                print("--- end ---\n")

        if relocation["role"] or relocation["position"]:
            stats["roles_relocated_to_moc"] += 1
            new_moc_text = append_role_to_moc(
                moc_path, citekey, relocation["role"], relocation["position"],
            )
            if new_moc_text != (moc_text_current or ""):
                if not dry_run:
                    moc_path.parent.mkdir(parents=True, exist_ok=True)
                    moc_path.write_text(new_moc_text, encoding="utf-8")
                verb = "would write" if dry_run else "wrote"
                print(f"{verb} {moc_path} (role/position for {citekey})")
                moc_text_current = new_moc_text

        if relocation["in_corpus_of_dropped"]:
            stats["in_corpus_of_dropped"] += 1
            print(
                f"NOTE: dropped in_corpus_of={relocation['in_corpus_of_dropped']!r} "
                f"from {overlay_path.name} — membership is the corpus ledger's "
                f"job now (review/ledger.py), not a note field."
            )

        stats["overlay_files_deleted"] += 1
        verb = "would delete" if dry_run else "deleted"
        print(f"{verb} {overlay_path} (content fully relocated)")
        if not dry_run:
            overlay_path.unlink()

    return stats


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    positional = [a for a in argv if a != "--dry-run" and not a.startswith("--moc-slug")]
    moc_slug = "literature-roles"
    for a in argv:
        if a.startswith("--moc-slug="):
            moc_slug = a.split("=", 1)[1]
    if len(positional) != 3:
        print(__doc__, file=sys.stderr)
        return 1
    overlay_dir, core_dir, moc_dir = Path(positional[0]), Path(positional[1]), Path(positional[2])

    stats = migrate_pair(overlay_dir, core_dir, moc_dir, moc_slug=moc_slug, dry_run=dry_run)

    print(
        f"\n{'DRY RUN — ' if dry_run else ''}"
        f"overlays scanned: {stats['overlays_scanned']}; "
        f"notes merged: {stats['notes_merged']}; "
        f"roles relocated to MOC: {stats['roles_relocated_to_moc']}; "
        f"in_corpus_of dropped: {stats['in_corpus_of_dropped']}; "
        f"overlay files deleted: {stats['overlay_files_deleted']}; "
        f"cores missing (left unmigrated): {stats['cores_missing']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
