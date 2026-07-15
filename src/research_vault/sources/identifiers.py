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
  - ``resolve_missing_id`` / ``backfill_missing_ids`` — id-resolution must
    not silently drop a canonical paper on a missing-id technicality (a
    3000-cite paper with messy source metadata is exactly as real as one
    with a clean DOI). Before a caller flags a candidate ``[NO-ID]`` and
    drops it from further consideration, it gets ONE targeted title/year
    lookup across the available adapters — a match backfills the id and
    the candidate is kept; a genuine miss is still unresolved, but the
    caller can now report *how many* were attempted, backfilled, and left
    unresolved (a counted drop, never a silent one).

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
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from .base import PaperHit, SourceAdapter
    from .dedup import DedupedHit

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


# ---------------------------------------------------------------------------
# Id-resolution must not silently drop a candidate on a missing-id
# technicality — a real paper with messy source metadata (no doi/arxiv/
# openalex/s2 on its search hit) is exactly as canonical as one with a
# clean id, and should not be excluded from the corpus purely because its
# id never resolved.
# ---------------------------------------------------------------------------

_TITLE_NORM_RE = re.compile(r"[^a-z0-9]")

# The keys `_paper_id_of_hit`/`_paper_id_of` (sources/sweep.py,
# sources/snowball.py) both already check, in this same priority order.
# Kept as a local tuple (not imported) — cheap, no cross-module coupling for
# a 4-item membership check.
_BACKFILL_ID_KEYS: tuple[str, ...] = ("doi", "arxiv", "openalex", "s2")


def _norm_title_for_match(title: str) -> str:
    return _TITLE_NORM_RE.sub("", (title or "").lower())


def _default_backfill_adapters() -> list["SourceAdapter"]:
    """The default title-lookup chain for a missing-id backfill attempt.

    No standalone crossref client exists in this codebase — OpenAlex and
    Semantic Scholar both resolve DOIs from title/author/year metadata
    (the same backfill need crossref would serve), so the fix reuses the
    already-registered adapters rather than adding a new one (charter §6).
    OpenAlex first (strongest DOI-backed title-match coverage in practice),
    then Semantic Scholar, then arXiv (covers preprint-only papers neither
    of the first two indexes).
    """
    from .registry import get_adapter

    return [get_adapter(name) for name in ("openalex", "semantic-scholar", "arxiv")]


def resolve_missing_id(
    title: str,
    year: int | None,
    *,
    adapters: Sequence["SourceAdapter"] | None = None,
) -> dict[str, str] | None:
    """Attempt to backfill a canonical external id for a candidate that has
    none of doi/arxiv/openalex/s2, from its title (+ year, when known)
    alone.

    Tries each adapter's ``search(title)`` in turn, taking the FIRST hit
    whose normalized title matches the target exactly and whose year (when
    both sides carry one) is within +-1 (tolerates a preprint-vs-camera-
    ready year skew) — returns that hit's ``external_ids``, filtered to the
    known backfillable keys. Returns ``None`` if no adapter resolves a
    confident match, or if ``title`` is blank (nothing to search on — no
    adapter is even called).

    Degrades gracefully per adapter (the same discipline
    ``sweep.py::_fetch_cell`` applies to the width sweep): an adapter that
    raises (dead OAuth session, network blip, rate limit) is skipped and
    the NEXT adapter in the chain is tried — one adapter down must never
    abort the whole backfill attempt, let alone the caller's render pass.

    ``adapters`` defaults to ``_default_backfill_adapters()`` — a caller
    (test or otherwise) that wants a hermetic, no-network call must pass an
    explicit (possibly empty) adapter list.
    """
    if not title or not title.strip():
        return None
    target = _norm_title_for_match(title)
    if not target:
        return None
    chain: Sequence["SourceAdapter"] = (
        adapters if adapters is not None else _default_backfill_adapters()
    )
    for adapter in chain:
        try:
            hits = adapter.search(title, limit=3)
        except Exception:  # noqa: BLE001 — one adapter down must not abort the chain
            continue
        for hit in hits:
            if _norm_title_for_match(hit.title) != target:
                continue
            if year is not None and hit.year is not None and abs(hit.year - year) > 1:
                continue
            resolved = {
                k: v for k, v in hit.external_ids.items()
                if k in _BACKFILL_ID_KEYS and v
            }
            if resolved:
                return resolved
    return None


def backfill_missing_ids(
    kept: Sequence["DedupedHit"],
    *,
    adapters: Sequence["SourceAdapter"] | None = None,
) -> dict[str, int]:
    """Backfill missing ids across a composed/deduped candidate set IN
    PLACE, and return the resolution-rate a caller MUST surface (never
    silently filter on) — id-resolution must not silently drop a
    canonical paper on a missing-id technicality.

    For every ``DedupedHit`` in *kept* that carries none of
    doi/arxiv/openalex/s2 (the same priority set ``_paper_id_of_hit``/
    ``_paper_id_of`` check), attempts ``resolve_missing_id`` off its
    title/year; a resolved id is merged into ``d.external_ids`` (never
    overwriting an id already present — ``setdefault``, though there
    should be none since this only fires on candidates with zero of the
    four keys).

    Returns ``{"missing": N, "backfilled": M, "unresolved": N - M}`` — N is
    how many candidates had no id BEFORE this pass; M is how many now do;
    the remainder is what a downstream ``[NO-ID]`` flag legitimately still
    fires on — a COUNTED drop, not a silent one. A candidate that already
    carries an id is never re-resolved (zero lookup cost for the common
    case; the backfill attempt only fires for genuinely missing-id
    candidates, bounding the added cost to the size of the gap).

    ``adapters=None`` (default) resolves each attempt against the real
    default adapter chain — a caller that wants a hermetic, no-network
    pass (unit tests; a caller that hasn't opted into backfill) must pass
    an explicit list, including ``[]`` to count-only with zero lookups.
    """
    chain: Sequence["SourceAdapter"] = (
        adapters if adapters is not None else _default_backfill_adapters()
    )
    missing = 0
    backfilled = 0
    for d in kept:
        if any(d.external_ids.get(k) for k in _BACKFILL_ID_KEYS):
            continue
        missing += 1
        resolved = resolve_missing_id(d.hit.title, d.hit.year, adapters=chain)
        if resolved:
            for k, v in resolved.items():
                d.external_ids.setdefault(k, v)
            backfilled += 1
    return {"missing": missing, "backfilled": backfilled, "unresolved": missing - backfilled}
