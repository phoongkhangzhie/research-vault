# SPDX-License-Identifier: AGPL-3.0-or-later
"""literature.py — `rv literature list <project>`: the per-project two-layer
literature registry (PR-B, pre-publish #68 storage contract).

Design of record: docs/superpowers/specs/2026-07-10-central-note-store-
cross-project-design.md §0.5 PR-B (Wren, 2026-07-10).

**Registry = a thin pointer, NOT a new artifact.** Per-project corpus
membership is exactly the set of overlay files under
``project_notes_dir(project)/literature/`` — the resolver's own
``note.iter_literature_notes`` already treats this dir as the per-project
registry (filesystem-is-registry, §3.3/§5). This module globs it and
enriches each entry via the project's ALREADY-WRITTEN ``_corpus_ledger.md``
(``review/ledger.py``, PR-5) — it never recomputes or re-stores any of the
ledger's provenance (the canonical-key map, resolving ids, accepted/
in_corpus/new counts).

**Zero-recomputation is load-bearing, not a style preference.**
``review.ledger._k_block``/``_resolving_ids_for_note`` own the ONE
implementation of "what counts as a resolving id" and "what counts as a
conformant citekey" — at ledger-WRITE time. If this module re-derived those
facts itself (even via the same regex, copy-pasted) it would be a second,
driftable implementation of the same claim. Instead this module only
parses the markdown table ``write_corpus_ledger`` already rendered
(``## Canonical-key map``) back into a dict — pure text parsing over an
artifact someone else computed and wrote to disk.

The ``state_dir/literature_index.json`` cache (citekey -> ids/title/
adopting-projects, for fast cross-project enumeration + the knowledge
layer) is explicitly OUT of scope here — fast-follow (§0.5 PR-B).

Stdlib only (+ intra-package imports).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .note import DanglingCentralPointerError, _parse_frontmatter, load_literature_note

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
    """Enumerate this project's adopted literature — the overlay dir, which
    IS the per-project registry (§3.3/§5) — enriched via any
    ``_corpus_ledger.md`` this project has already produced.

    Returns one dict per adopted paper (overlay file):
      citekey       — the resolved core's ``citekey:`` field if set, else
                       the overlay's ``central:`` pointer (the filename
                       slug) — the same fallback the ledger's own
                       ``_k_block``/bulk consumers use.
      title, role   — read through the resolver (``load_literature_note``):
                       title from the merged core+overlay, role from the
                       overlay only (RQ-relative, never core content).
      resolving_ids — from the ledger's canonical-key map; "" if this
                       citekey never appeared in any ledger this project
                       has written (e.g. adopted outside a review loop, or
                       the ledger predates this paper — an honest gap, not
                       a fabricated value).
      conformant    — True/False from the ledger; None if not found there.
      in_ledger     — whether this citekey was found in ANY ledger — lets a
                       caller distinguish "checked, non-conformant" from
                       "never checked".
      error         — set (title/role/etc. left None) when the overlay
                       carries a dangling ``central:`` pointer or has no
                       resolvable core — surfaced, never silently dropped
                       (charter §2); the paper still appears in the
                       returned list.
    """
    cfg = config or load_config()
    overlay_dir = cfg.project_notes_dir(project) / "literature"
    if not overlay_dir.exists():
        return []

    key_map: dict[str, tuple[str, bool]] = {}
    for ledger_path in _find_ledgers(cfg, project):
        try:
            text = ledger_path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Later (path-sorted) ledgers win on a citekey collision — the
        # most-recently-touched review scope's verdict is the freshest one.
        key_map.update(_parse_key_map_table(text))

    rows: list[dict[str, Any]] = []
    for overlay_file in sorted(overlay_dir.glob("*.md")):
        overlay_slug = overlay_file.stem
        try:
            assembled = load_literature_note(cfg, project, overlay_slug)
        except (FileNotFoundError, DanglingCentralPointerError) as e:
            rows.append({
                "citekey": overlay_slug,
                "overlay_slug": overlay_slug,
                "title": None,
                "role": None,
                "resolving_ids": "",
                "conformant": None,
                "in_ledger": False,
                "error": str(e),
            })
            continue

        core_citekey = str(assembled.fields.get("citekey") or "").strip()
        lookup_key = core_citekey or overlay_slug
        resolving_ids, conformant = key_map.get(lookup_key, ("", None))
        rows.append({
            "citekey": lookup_key,
            "overlay_slug": overlay_slug,
            "title": assembled.fields.get("title", ""),
            "role": assembled.fields.get("role", ""),
            "resolving_ids": resolving_ids,
            "conformant": conformant,
            "in_ledger": lookup_key in key_map,
            "error": None,
        })
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(parent: argparse._SubParsersAction | None = None) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Build the argument parser for the `literature` verb.

    When to use: `rv literature list <project>` to see the papers a project
    has adopted (its literature/ overlay dir), enriched with the resolving
    ids + conformance verdict recorded the last time a review's
    `_corpus_ledger.md` was written for this project. Anti-pattern: do NOT
    hand-glob `literature/*.md` and eyeball frontmatter — the overlay alone
    is thin (no ids, no conformance) by design (PR-A two-layer split); this
    verb resolves through the core + the ledger for you.
    """
    desc = "The per-project two-layer literature registry (adopted papers, ledger-enriched)."
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
