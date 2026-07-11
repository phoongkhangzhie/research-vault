# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/base.py — the SourceAdapter protocol + the normalized PaperHit record.

NG-1 (breadth-then-depth). One narrow interface every source-adapter
implements; a normalized hit record so cross-source dedup/ranking/discounting
(NG-2/NG-3/NG-9) never has to branch on which source produced a paper.

``NotSupported`` is the explicit signal an adapter raises for an operation it
cannot perform (e.g. arXiv/PubMed have no forward-citation graph) — the width
sweep and the depth snowball both treat it as "skip this adapter for this op",
never a crash, never a silent empty list masquerading as a real zero (charter
 surface, never silently drop; the caller can tell "not supported" apart
from "supported, zero results").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class NotSupported(Exception):
    """Raised by an adapter method it does not implement for its source.

    e.g. ``ArxivAdapter.cited_by(...)`` — arXiv has no citation graph.
    Callers (the width-sweep, the depth snowball) must catch this and skip
    the adapter for that operation, never treat it as a zero-result search.
    """


class AdapterFetchError(Exception):
    """Raised by an adapter method when the underlying lookup fails (a
    non-zero exit / a 404 / any transport error) for a SPECIFIC paper id.

    This is a normal, catchable ``Exception`` — deliberately NOT ``sys.exit``
    (``SystemExit`` is a ``BaseException`` and is invisible to a caller's
    ``except Exception`` clause, which is exactly the bug this class exists
    to close: a single unresolvable seed id used to abort an entire
    multi-round snowball walk, live-asta-observed 2026-07-09). Direct
    single-lookup CLI callers (``rv research cited-by``/``references``, via
    ``research.py``'s ``cmd_cited_by``/``cmd_references``) catch this and
    re-raise as ``sys.exit`` themselves, preserving their existing
    fail-fast CLI UX; a multi-seed walk (``sources/snowball.py``) catches it
    per-(paper,direction) and degrades gracefully instead — SKIP this one
    lookup, continue the walk.
    """


@dataclass
class PaperHit:
    """A normalized, source-agnostic search/citation-graph result.

    ``external_ids`` keys are normalized lowercase: ``doi``, ``arxiv``,
    ``openalex``, ``pmid``, ``s2`` (Semantic Scholar corpus id/paper id),
    ``mag``. Only keys the adapter actually resolved are present — never a
    key with an empty-string placeholder (an absent id is an absent key).

    ``raw`` carries the adapter's native payload for that hit (e.g. the S2
    paper dict downstream `_corpus_annotation`/`_print_candidates` already
    consume) — this is the zero-behavior-change seam: NG-1 refactors the S2
    path to *produce* PaperHit, but existing dict-consuming code keeps
    reading ``hit.raw`` unchanged.

    ``source`` is the adapter's ``name`` (e.g. ``"semantic-scholar"``,
    ``"arxiv"``, ``"openalex"``, ``"pubmed"``) — the provenance NG-2's dedup
    unions and NG-3's utility ranker (source-diversity dim) counts.
    """

    title: str
    year: int | None
    authors: list[str]
    external_ids: dict[str, str]
    abstract: str
    citation_count: int
    source: str
    raw: dict[str, Any] = field(default_factory=dict)

    # NG-9: stamped by derivative.mark_derivatives — the id (in this hit's own
    # dedup-identity space) of the paper this one is a >60%-overlap derivative
    # of. None = not flagged (independent). Discount, never delete: the hit
    # stays in the list, just annotated + counted differently by saturation.
    derivative_of: str | None = field(default=None, compare=False)

    # NG-3: stamped by ranker.rank_and_select when a candidate's independent-
    # source count is below the floor at selection time — "boundary item,
    # needs more sources / snowball attention", never "drop it".
    below_floor: bool = field(default=False, compare=False)

    # OA-fulltext-enrichment (tier 1, 0.3.0): optional, small, provenance-only
    # OA pointers an adapter captured at search time but did not previously
    # keep. Never the full-text body itself (that is large — 100 KB-1 MB per
    # paper — and lives in the sources/enrich.py cache + the note, not on the
    # hit; see sources/enrich.py FetchResult).
    oa_url: str | None = field(default=None, compare=False)
    oa_status: str | None = field(default=None, compare=False)
    oa_source: str | None = field(default=None, compare=False)

    # review-screen evidence enrichment (0.3.x, a downstream project's
    # validation-run finding 2026-07-09): the journal/conference/preprint-
    # server name, when the
    # adapter's payload carries one. None (never "") when an adapter has no
    # such concept (e.g. arXiv preprints with no ``journal_ref``) — carry
    # what's there, never fabricate a venue.
    venue: str | None = field(default=None, compare=False)


@runtime_checkable
class SourceAdapter(Protocol):
    """The narrow interface every literature-search source implements.

    Not every source supports every operation — arXiv/PubMed/web have no
    citation graph. An adapter that cannot perform an op raises
    ``NotSupported`` rather than returning an empty list (which would read as
    "supported, zero hits").
    """

    name: str

    def search(self, query: str, *, limit: int = 20) -> list[PaperHit]:
        """Keyword/topic search. Every adapter must support this."""
        ...

    def cited_by(self, paper_id: str, *, limit: int = 20) -> list[PaperHit]:
        """Forward snowball: papers that cite ``paper_id``.

        Raise ``NotSupported`` if this source has no citation graph.
        """
        ...

    def references(self, paper_id: str, *, limit: int = 20) -> list[PaperHit]:
        """Backward snowball: papers ``paper_id`` itself cites.

        Raise ``NotSupported`` if this source has no citation graph.
        """
        ...
