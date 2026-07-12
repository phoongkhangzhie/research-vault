# SPDX-License-Identifier: AGPL-3.0-or-later
"""literature.py — `rv literature list <project>`: the project's adopted
literature registry.

Design of record: internal design note (the architect, 2026-07-10);
re-derived for the overlay unwind (0.3.2 — literature became
shared-canonical; see note.py's module docstring).

**Registry = a thin pointer, NOT a new artifact.** The overlay unwind (0.3.2) dissolved the
per-project literature/ overlay — there is no longer a filesystem dir that
IS the per-project registry. Membership moved to the mechanical corpus
ledger (review/ledger.py): a project's
"adopted" set is the UNION of every citekey appearing in any
``_corpus_ledger.md`` this project has produced. This module reads that
union, resolves each citekey against the shared literature store
(``cfg.literature_root``), and enriches with the SAME ledger's canonical-
key map (resolving ids, conformance) it already read for membership — it
never recomputes or re-stores any of the ledger's provenance.

**Zero-recomputation is load-bearing, not a style preference.**
``review.ledger._k_block``/``_resolving_ids_for_note`` own the ONE
implementation of "what counts as a resolving id" and "what counts as a
conformant citekey" — at ledger-WRITE time. If this module re-derived those
facts itself (even via the same regex, copy-pasted) it would be a second,
driftable implementation of the same claim. Instead this module only
parses the markdown table ``write_corpus_ledger`` already rendered
(``## Canonical-key map``) back into a dict — pure text parsing over an
artifact someone else computed and wrote to disk.

**Role is no longer available here.** Before the overlay unwind, ``role``/``position`` were
overlay fields this module could read straight off the per-project note.
The overlay unwind (0.3.2) moved role to CURATED project MOCs (narration, not a mechanical
field) — this registry surfaces membership + conformance only; role is a
human-authored fact, read from a MOC directly, not enumerable here.

The ``state_dir/literature_index.json`` cache (citekey -> ids/title/
adopting-projects, for fast cross-project enumeration + the knowledge
layer) is explicitly OUT of scope here — fast-follow.

Stdlib only (+ intra-package imports).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .note import _parse_frontmatter

# ---------------------------------------------------------------------------
# Ledger discovery + read-only parsing (never recomputes ledger content)
# ---------------------------------------------------------------------------

_KEY_MAP_ROW_RE = re.compile(r"^\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*(yes|no)\s*\|$")


def _find_ledgers(cfg: Config, project: str) -> list[Path]:
    """Every ``_corpus_ledger.md`` this project has produced (one per
    review scope), sorted deterministically by path so a multi-scope
    project's enrichment is reproducible run to run."""
    reviews_dir = cfg.project_notes_dir(project) / "reviews"
    if not reviews_dir.exists():
        return []
    return sorted(reviews_dir.glob("*/_corpus_ledger.md"))


def _parse_key_map_table(ledger_text: str) -> dict[str, tuple[str, bool]]:
    """Parse the ``## Canonical-key map`` table a ledger already carries
    (``review.ledger._render_key_map_table``'s exact output shape) into
    ``{citekey: (resolving_ids, conformant)}``.

    Pure text parsing over an artifact ``write_corpus_ledger`` already
    computed and wrote — this function must NEVER re-derive a resolving id
    or a conformance verdict itself (see module docstring)."""
    rows: dict[str, tuple[str, bool]] = {}
    in_section = False
    for line in ledger_text.splitlines():
        stripped = line.strip()
        if stripped == "## Canonical-key map":
            in_section = True
            continue
        if in_section:
            if stripped.startswith("## "):
                break
            m = _KEY_MAP_ROW_RE.match(stripped)
            if not m:
                continue
            citekey, ids, ok = m.group(1), m.group(2), m.group(3)
            if citekey in ("Citekey", "_(no corpus rows)_"):
                continue
            rows[citekey] = (ids, ok == "yes")
    return rows


# ---------------------------------------------------------------------------
# The registry read
# ---------------------------------------------------------------------------

def cmd_list(project: str, *, config: Config | None = None) -> list[dict[str, Any]]:
    """Enumerate this project's adopted literature.

    the overlay unwind (0.3.2): "adopted" is now MECHANICAL membership
    — the union of every citekey appearing in any ``_corpus_ledger.md`` this
    project has produced (the ledger's own canonical-key map — the SAME
    artifact this function already reads for enrichment). There is no more
    per-project literature/ overlay dir to glob.

    Returns one dict per adopted citekey:
      citekey       — the ledger's canonical-key-map key.
      title         — read directly off the shared literature note
                       (``cfg.literature_root/<citekey>.md``); ``None`` when
                       no such note exists yet (adopted-but-not-materialized
                       — an honest gap, not a crash).
      role          — always ``None`` — the overlay unwind (0.3.2) moved role to CURATED
                       project MOCs (RQ-relative narration), not a
                       mechanical field this registry can enumerate. Kept as
                       a key (rather than dropped) so existing callers don't
                       KeyError; read the project's MOCs directly for role.
      resolving_ids — from the ledger's canonical-key map.
      conformant    — True/False from the ledger.
      in_ledger     — always True here (every row comes FROM a ledger) —
                       kept for shape back-compat with pre-unwind callers.
      error         — set (title left None) when the shared note doesn't
                       exist yet — surfaced, never silently dropped
                       (charter §2); the citekey still appears in the
                       returned list.
    """
    cfg = config or load_config()

    key_map: dict[str, tuple[str, bool]] = {}
    for ledger_path in _find_ledgers(cfg, project):
        try:
            text = ledger_path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Later (path-sorted) ledgers win on a citekey collision — the
        # most-recently-touched review scope's verdict is the freshest one.
        key_map.update(_parse_key_map_table(text))

    if not key_map:
        return []

    rows: list[dict[str, Any]] = []
    for citekey in sorted(key_map):
        resolving_ids, conformant = key_map[citekey]
        note_path = cfg.literature_root / f"{citekey}.md"
        if not note_path.is_file():
            rows.append({
                "citekey": citekey,
                "title": None,
                "role": None,
                "resolving_ids": resolving_ids,
                "conformant": conformant,
                "in_ledger": True,
                "error": (
                    f"no shared literature note at {note_path} — adopted "
                    "(in this project's corpus ledger) but not yet "
                    "materialized."
                ),
            })
            continue
        try:
            note_fields, _ = _parse_frontmatter(note_path.read_text(encoding="utf-8"))
        except OSError as e:
            rows.append({
                "citekey": citekey,
                "title": None,
                "role": None,
                "resolving_ids": resolving_ids,
                "conformant": conformant,
                "in_ledger": True,
                "error": str(e),
            })
            continue
        rows.append({
            "citekey": citekey,
            "title": note_fields.get("title", ""),
            "role": None,
            "resolving_ids": resolving_ids,
            "conformant": conformant,
            "in_ledger": True,
            "error": None,
        })
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(parent: argparse._SubParsersAction | None = None) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Build the argument parser for the `literature` verb.

    When to use: `rv literature list <project>` to see the papers a project
    has adopted (its corpus ledger's membership), enriched with the
    resolving ids + conformance verdict recorded the last time a review's
    `_corpus_ledger.md` was written for this project. Anti-pattern: do NOT
    hand-glob the shared literature store and eyeball frontmatter to guess
    which papers belong to a project — membership is mechanical (the
    ledger), not derivable from the shared store alone; this verb resolves
    through the ledger + the shared note for you.
    """
    desc = "The project's adopted-literature registry (ledger membership, shared-store-enriched)."
    if parent is not None:
        p = parent.add_parser("literature", help="Literature registry.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv literature", description=desc)

    sub = p.add_subparsers(dest="literature_cmd", required=True)

    list_p = sub.add_parser("list", help="List this project's adopted literature.")
    list_p.add_argument("project", help="Project slug.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch literature subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv literature: config error: {e}", file=sys.stderr)
        return 1

    if args.literature_cmd == "list":
        rows = cmd_list(args.project, config=cfg)
        if not rows:
            print(f"No adopted literature for {args.project!r}.")
            return 0
        print(f"Adopted literature for {args.project!r}:")
        for row in rows:
            if row.get("error"):
                print(f"  [ERROR] {row['citekey']}: {row['error']}")
                continue
            conformant = row["conformant"]
            conf_str = "yes" if conformant is True else ("no" if conformant is False else "?")
            ledger_str = "in-ledger" if row["in_ledger"] else "not-in-any-ledger"
            role = f" [{row['role']}]" if row.get("role") else ""
            print(f"  {row['citekey']:<28} conformant={conf_str:<3} ({ledger_str}){role} {row['title']}")
        return 0

    return 1
