# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/annotate.py — corpus annotation for cross-source DedupedHits (NG-2).

Reuses the EXISTING dedup/annotate layer's index (``research._load_notes_index``
/ ``research._load_notes_title_index`` — §4.3 "reuse the existing dedup/
annotate layer") rather than a second parallel implementation; this module
only adapts the LOOKUP KEY shape (a ``DedupedHit``'s normalized
``external_ids``/title) to those indices' expectations, mirroring
``research._corpus_annotation``'s own doi/arxiv/title-fallback tiers.
"""
from __future__ import annotations

import re

from .dedup import DedupedHit

_NORM_RE = re.compile(r"[^a-z0-9]")


def _norm_title(title: str) -> str:
    return _NORM_RE.sub("", (title or "").lower())


def annotate_deduped(
    d: DedupedHit,
    *,
    notes_index: dict[str, str] | None = None,
    notes_title_index: dict[str, list[tuple[str, str]]] | None = None,
) -> str:
    """Return ``[IN-CORPUS:<citekey>]`` or ``[NEW]`` for a deduped cross-
    source hit — the same annotation contract ``research._corpus_annotation``
    uses for single-source S2 candidates, extended to read the UNIONED
    ``external_ids`` a multi-source dedup produces."""
    ni = notes_index or {}
    doi = (d.external_ids.get("doi") or "").strip().lower()
    if doi and doi in ni:
        return f"[IN-CORPUS:{ni[doi]}]"
    arxiv = re.sub(r"v\d+$", "", (d.external_ids.get("arxiv") or "").strip().lower())
    if arxiv and arxiv in ni:
        return f"[IN-CORPUS:{ni[arxiv]}]"

    nti = notes_title_index or {}
    if nti and d.hit.authors:
        fam = d.hit.authors[0].rsplit(" ", 1)[-1].lower()
        title_norm = _norm_title(d.hit.title)
        if fam and len(title_norm) >= 20:
            for ck, note_title in nti.get(fam, []):
                if title_norm == note_title or (
                    (title_norm in note_title or note_title in title_norm)
                    and min(len(title_norm), len(note_title)) / max(len(title_norm), len(note_title)) >= 0.9
                ):
                    return f"[IN-CORPUS:{ck}]"
    return "[NEW]"
