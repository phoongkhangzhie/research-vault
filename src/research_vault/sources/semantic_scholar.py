# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/semantic_scholar.py — SemanticScholarAdapter (NG-1, pure refactor).

Wraps the exact asta subprocess calls ``research.py``'s ``cmd_find`` /
``cmd_cited_by`` / ``cmd_references`` shelled out to inline, with ZERO
behavior change: same asta subcommands, same ``--fields`` projections, same
error handling (a non-zero exit still calls ``sys.exit`` with the same
message shape). ``research.py`` now calls this adapter instead of shelling
out directly; ``hit.raw`` carries the original S2 dict so the existing
``_corpus_annotation`` / ``_print_candidates`` pipeline is untouched.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from .base import PaperHit


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

    OA-fulltext-enrichment: previously discarded (§1 of the design doc) —
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
    )


class SemanticScholarAdapter:
    """Adapter over the asta CLI (Semantic Scholar). Supports search + both
    citation-graph directions — this is the source the depth snowball anchors
    on (§4.1)."""

    name = "semantic-scholar"

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        fields: str = "title,year,authors,externalIds,abstract,citationCount,openAccessPdf",
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
        fields: str = "title,year,authors,externalIds,citationCount,openAccessPdf",
    ) -> list[PaperHit]:
        cmd = [
            "asta", "papers", "citations", paper_id,
            "--format", "json", "--limit", str(limit),
            "--fields", fields,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"asta papers citations failed:\n{r.stderr}")
        raw = json.loads(r.stdout)
        items = [item.get("citingPaper", item) for item in (raw.get("data") or [])]
        return [_s2_item_to_hit(p) for p in items]

    def references(
        self,
        paper_id: str,
        *,
        limit: int = 20,
        fields: str = (
            "references.title,references.year,references.authors,"
            "references.externalIds,references.citationCount"
        ),
    ) -> list[PaperHit]:
        cmd = [
            "asta", "papers", "get", paper_id,
            "--fields", fields,
            "--format", "json",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"asta papers get failed:\n{r.stderr}")
        raw = json.loads(r.stdout)
        items = raw.get("references") or []
        return [_s2_item_to_hit(p) for p in items]
