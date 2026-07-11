# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/semantic_scholar.py — SemanticScholarAdapter (NG-1, pure refactor).

Wraps the exact asta subprocess calls ``research.py``'s ``cmd_find`` /
``cmd_cited_by`` / ``cmd_references`` shelled out to inline, same asta
subcommands, same ``--fields`` projections. ``research.py`` now calls this
adapter instead of shelling out directly; ``hit.raw`` carries the original
S2 dict so the existing ``_corpus_annotation`` / ``_print_candidates``
pipeline is untouched.

Error handling — ``search`` still calls ``sys.exit`` on a non-zero asta exit
(a single-shot CLI action with no multi-item walk to degrade). ``cited_by``/
``references`` instead raise ``AdapterFetchError`` (a normal, catchable
``Exception`` — see ``sources/base.py``): a live-asta 404 on one seed id used
to ``sys.exit`` the WHOLE ``review-snowball`` walk (``SystemExit`` is a
``BaseException``, invisible to the walk's ``except Exception`` degrade
clauses) — this is the fix (2026-07-09, a downstream project's live-asta
validation run).
``research.py``'s ``cmd_cited_by``/``cmd_references`` catch
``AdapterFetchError`` and re-raise as ``sys.exit`` themselves, so the
single-lookup CLI UX is unchanged; ``sources/snowball.py``'s multi-round
walk catches it per-(paper,direction) and degrades instead.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from .base import AdapterFetchError, PaperHit


def _authors_to_names(authors_raw: Any) -> list[str]:
    """Normalize an S2 ``authors`` field (list of {"name": ...} dicts) to names."""
    if not authors_raw:
        return []
    out: list[str] = []
    for a in authors_raw:
        if isinstance(a, dict):
            name = (a.get("name") or "").strip()
        elif isinstance(a, str):
            name = a.strip()
        else:
            continue
        if name:
            out.append(name)
    return out


def _oa_pointer_from_item(item: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract (oa_url, oa_status) from an S2 ``openAccessPdf`` field.

    OA-fulltext-enrichment: previously discarded (of the design doc) —
    ``openAccessPdf`` was never in the ``--fields`` projection at all.
    S2's ``status`` values are uppercase (GOLD/GREEN/HYBRID/BRONZE/CLOSED);
    normalize to the lowercase vocabulary ``oa_status`` uses elsewhere.
    """
    oap = item.get("openAccessPdf") or None
    if not oap:
        return None, None
    url = (oap.get("url") or "").strip() or None
    status = (oap.get("status") or "").strip().lower() or None
    return url, status


def _s2_item_to_hit(item: dict[str, Any]) -> PaperHit:
    ext = item.get("externalIds") or {}
    external_ids: dict[str, str] = {}
    if ext.get("DOI"):
        external_ids["doi"] = str(ext["DOI"])
    if ext.get("ArXiv"):
        external_ids["arxiv"] = str(ext["ArXiv"])
    if ext.get("CorpusId"):
        external_ids["s2"] = str(ext["CorpusId"])
    if ext.get("MAG"):
        external_ids["mag"] = str(ext["MAG"])
    if ext.get("PMID"):
        external_ids["pmid"] = str(ext["PMID"])

    oa_url, oa_status = _oa_pointer_from_item(item)
    venue = (item.get("venue") or "").strip() or None

    return PaperHit(
        title=item.get("title") or "",
        year=item.get("year"),
        authors=_authors_to_names(item.get("authors")),
        external_ids=external_ids,
        abstract=item.get("abstract") or "",
        citation_count=item.get("citationCount") or 0,
        source="semantic-scholar",
        raw=item,
        oa_url=oa_url,
        oa_status=oa_status,
        oa_source="semantic-scholar" if oa_url else None,
        venue=venue,
    )


class SemanticScholarAdapter:
    """Adapter over the asta CLI (Semantic Scholar). Supports search + both
    citation-graph directions — this is the source the depth snowball anchors
    on."""

    name = "semantic-scholar"

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        fields: str = "title,year,authors,externalIds,abstract,citationCount,openAccessPdf,venue,tldr",
    ) -> list[PaperHit]:
        cmd = [
            "asta", "papers", "search", query,
            "--format", "json", "--limit", str(limit),
            "--fields", fields,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"asta papers search failed:\n{r.stderr}")
        raw = json.loads(r.stdout)
        return [_s2_item_to_hit(p) for p in (raw.get("data") or [])]

    def cited_by(
        self,
        paper_id: str,
        *,
        limit: int = 20,
        # abstract/venue added (pre-publish hardening batch, 2026-07-09,
        # substance-screening gap fix): the snowball walk's raw pool
        # (`_corpus_raw.md`) is read by `review-curate` to apply the frozen
        # inclusion/exclusion criteria — without abstract/venue on the hit,
        # curation degrades to title-only screening (cannot verify a
        # substance-level axis like "measured human baseline", never
        # title-visible). Mirrors `search`'s fields projection.
        fields: str = "title,year,authors,externalIds,abstract,citationCount,openAccessPdf,venue",
    ) -> list[PaperHit]:
        cmd = [
            "asta", "papers", "citations", paper_id,
            "--format", "json", "--limit", str(limit),
            "--fields", fields,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise AdapterFetchError(f"asta papers citations failed:\n{r.stderr}")
        raw = json.loads(r.stdout)
        items = [item.get("citingPaper", item) for item in (raw.get("data") or [])]
        return [_s2_item_to_hit(p) for p in items]

    def get(
        self,
        paper_id: str,
        *,
        fields: str = "title,year,authors,externalIds,abstract,citationCount,openAccessPdf,venue",
    ) -> PaperHit | None:
        """Fetch a single paper's own metadata (top-level ``externalIds``),
        e.g. to enrich a doi/arXiv id with S2's fuller id set (s2 corpus id,
        MAG, PMID) at identifier-persistence write time (``rv research add``).

        Best-effort, deliberately NOT ``sys.exit`` on failure (unlike
        ``search``/``cited_by``/``references``, which are primary
        user-facing actions): this is an optional enrichment call a caller
        already has a fallback for (the doi/arXiv id it started with), so a
        transient asta failure must degrade gracefully, never abort the
        caller's whole operation. Returns None on any failure (non-zero
        exit, unparseable JSON, empty body).
        """
        cmd = ["asta", "papers", "get", paper_id, "--fields", fields, "--format", "json"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return None
        try:
            raw = json.loads(r.stdout)
        except json.JSONDecodeError:
            return None
        if not raw or not isinstance(raw, dict):
            return None
        return _s2_item_to_hit(raw)

    def references(
        self,
        paper_id: str,
        *,
        limit: int = 20,
        # abstract/venue added — see `cited_by`'s comment (same
        # substance-screening gap fix, same fields-projection rationale).
        fields: str = (
            "references.title,references.year,references.authors,"
            "references.externalIds,references.citationCount,"
            "references.abstract,references.venue"
        ),
    ) -> list[PaperHit]:
        """Backward snowball: papers ``paper_id`` itself cites.

        ``limit`` is accepted (kept for ``SourceAdapter`` Protocol parity
        with ``search``/``cited_by``, and because ``snowball.py`` calls this
        generically across whatever adapter it's given) but is a DELIBERATE
        NO-OP here — see the REWRITE note below. Do not re-add a
        client-side ``items[:limit]`` truncation; that was tried and
        reverted (recall-regressive, see git history for the original
         commit + its revert).

         REWRITE (pre-publish fit-check, 2026-07-10): an earlier
        version of this fix bound backward references client-side to
        ``per_round_limit``, on the reasoning that ``asta papers get`` has
        no server-side pagination for a nested ``references.*`` projection
        (confirmed via `asta papers get --help`) so `limit` was otherwise
        silently unused. The corpus architect flagged that as
        RECALL-REGRESSIVE on fit-check: backward-snowball exists precisely
        to catch UNIQUE, PERIPHERAL citations a paper's own bibliography
        carries — unlike the forward direction (rediscoverable from many
        citing papers), a truncated backward reference is not "delayed to
        a later round," it is GONE for good the moment ``visited_pids``
        marks this seed visited (each paper is fetched at most once per
        direction across the whole walk). A real downstream project's
        corpus-build run relied on this exact unbounded behavior (888
        backward hits in one round). Total
        walk work is ALREADY bounded — not by a per-call reference cap, but
        at the WALK level by ``fetch_budget`` (hard ceiling on total calls)
        and ``frontier_cap`` (bounds how many discovered papers re-seed the
        next round) — see ``snowball.py``. A per-call backward cap would
        cost recall for zero work-bound benefit, since those two knobs
        already do the bounding job. Decision: backward stays fully
        unbounded per-call; ``per_round_limit`` is FORWARD-only (see its
        docstring in ``snowball.py::run_snowball``). Precision on the
        resulting pool is the relevance gate's job downstream, not
        truncation here.
        """
        cmd = [
            "asta", "papers", "get", paper_id,
            "--fields", fields,
            "--format", "json",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise AdapterFetchError(f"asta papers get failed:\n{r.stderr}")
        raw = json.loads(r.stdout)
        items = raw.get("references") or []
        return [_s2_item_to_hit(p) for p in items]
