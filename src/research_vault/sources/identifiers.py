# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/identifiers.py — persist + reconstruct a paper's external-id set
against a literature note's frontmatter (identifier-persistence).

The gap this closes: ``sources/base.py``'s ``PaperHit.external_ids`` already
normalizes the full id set a live search/citation-graph hit carries (``doi``,
``arxiv``, ``pmcid``, ``openalex``, ``pmid``, ``s2``) — but a freshly-filed
literature note only ever carried a thin ``doi``/``arxiv_id`` pair (see
``note.cmd_new``'s literature scaffold), and every downstream reader that
needs the fuller set (``sources/enrich.py``'s OA fulltext waterfall reading
``hit.external_ids.get("pmcid")`` etc.) had no way to reach it from an
EXISTING filed note without re-resolving from the network.

This module is the read/write SSOT for that persistence:
  - ``write_external_ids_to_note`` — stamp the present ids into a note's
    frontmatter (id-resolution time, e.g. ``rv research add``).
  - ``read_external_ids_from_note`` — reconstruct a ``PaperHit``-shaped
    ``external_ids`` dict FROM an existing note's frontmatter (read-time,
    e.g. ``rv research fulltext`` / the relate-<key> read path) — zero
    network calls.

``FRONTMATTER_FIELD_MAP`` is the one place the PaperHit-key -> frontmatter-
key naming lives. ``arxiv`` -> ``arxiv_id`` preserves the existing note
convention (``note.cmd_new``'s literature scaffold already carries
``doi``/``arxiv_id`` placeholders — Fix #32); the other four ids have no
prior frontmatter precedent, so they use the same name PaperHit.external_ids
already uses (``pmcid``, ``openalex``, ``pmid``, ``s2``) — no new naming
scheme, no rename of the two fields already in the wild.
"""
from __future__ import annotations

import re
from pathlib import Path

# PaperHit-style key -> literature-note frontmatter field name.
# Keep in sync with sources/base.py's PaperHit.external_ids key vocabulary
# (doi, arxiv, openalex, pmid, s2, mag) plus pmcid (sources/pubmed.py).
# `arxiv` keeps its existing note-convention name `arxiv_id` (Fix #32,
# note.cmd_new); every other key matches PaperHit's own vocabulary 1:1
# since there is no prior frontmatter precedent to preserve.
FRONTMATTER_FIELD_MAP: dict[str, str] = {
    "doi": "doi",
    "arxiv": "arxiv_id",
    "pmcid": "pmcid",
    "openalex": "openalex",
    "pmid": "pmid",
    "s2": "s2",
}

# Reverse map: frontmatter field name -> PaperHit-style key.
_REVERSE_FIELD_MAP: dict[str, str] = {v: k for k, v in FRONTMATTER_FIELD_MAP.items()}


def stamp_note_frontmatter(note_path: Path, fields: dict[str, str]) -> bool:
    """Stamp/replace scalar frontmatter *fields* in *note_path* in place.

    Canonical stamp-or-inject helper (moved here from ``fulltext.py``, which
    re-exports this name for backward compatibility — reuse over duplicate,
    charter §6). Regex-replaces an existing ``key: value`` line if present;
    otherwise injects a new ``key: value`` line just before the closing
    ``---`` delimiter, rather than reserializing the whole frontmatter
    (which would risk corrupting fields this helper doesn't know about).

    Returns False (no-op) if *note_path* does not exist — the caller treats
    this as "note not filed yet, nothing to stamp" (not an error).
    """
    if not note_path.is_file():
        return False
    text = note_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False

    lines = text.split("\n")
    delim_idxs = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if len(delim_idxs) < 2:
        return False

    for key, value in fields.items():
        # [ \t]* (not \s*) after the colon — \s also matches newlines, and a
        # blank existing value ("key: \n") would let the trailing \s* eat
        # straight through the line break into the NEXT field's line,
        # corrupting it (repro: an empty-placeholder scaffold field, e.g.
        # note.cmd_new's literature `doi: `/`arxiv_id: ` placeholders —
        # Fix #32 — followed by another field on the very next line).
        pattern = re.compile(rf"^({re.escape(key)}:[ \t]*).*$", re.MULTILINE)
        if pattern.search(text) is not None:
            # Existing field — replace in place (never a string-equality
            # check: the new value can legitimately equal the old one, which
            # would falsely read as "no match" and duplicate-inject).
            text = pattern.sub(lambda m, v=value: f"{m.group(1)}{v}", text, count=1)
        else:
            lines = text.split("\n")
            delim_idxs = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
            close_idx = delim_idxs[1]
            lines.insert(close_idx, f"{key}: {value}")
            text = "\n".join(lines)

    note_path.write_text(text, encoding="utf-8")
    return True


def write_external_ids_to_note(note_path: Path, external_ids: dict[str, str]) -> bool:
    """Persist the present entries of *external_ids* (PaperHit-style keys)
    into *note_path*'s frontmatter, via ``FRONTMATTER_FIELD_MAP``.

    Only keys present in ``FRONTMATTER_FIELD_MAP`` with a truthy value are
    written — an absent id is an absent field, never an empty-string
    placeholder (mirrors ``PaperHit.external_ids``'s own contract).

    Returns False when there is nothing to write (empty/all-unmapped
    *external_ids*) OR the note does not exist yet (delegates to
    ``stamp_note_frontmatter``'s no-op contract).
    """
    fields = {
        FRONTMATTER_FIELD_MAP[k]: v
        for k, v in external_ids.items()
        if k in FRONTMATTER_FIELD_MAP and v
    }
    if not fields:
        return False
    return stamp_note_frontmatter(note_path, fields)


def read_external_ids_from_note(note_path: Path) -> dict[str, str]:
    """Reconstruct a ``PaperHit``-shaped ``external_ids`` dict from an
    existing literature note's frontmatter — the read-then-pass path: no
    network re-resolution, no adapter call.

    Returns {} if the note does not exist or carries none of the known id
    fields. Blank/whitespace-only field values are treated as absent (the
    scaffolded literature template ships empty ``doi:``/``arxiv_id:``
    placeholders — Fix #32 — an unfilled placeholder must not round-trip as
    a real id).
    """
    if not note_path.is_file():
        return {}
    from ..note import _parse_frontmatter

    text = note_path.read_text(encoding="utf-8")
    fields, _ = _parse_frontmatter(text)

    external_ids: dict[str, str] = {}
    for fm_key, hit_key in _REVERSE_FIELD_MAP.items():
        raw = fields.get(fm_key)
        if isinstance(raw, str) and raw.strip():
            external_ids[hit_key] = raw.strip()
    return external_ids
