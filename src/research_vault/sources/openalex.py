"""sources/openalex.py — OpenAlexAdapter (NG-2, breadth source #3, default-on per D4).

OpenAlex exposes both directions of the citation graph (``cited_by_api_url``
for forward citations; the work's own ``referenced_works`` for backward), so
— alongside Semantic Scholar — it can also anchor depth snowballing, not just
breadth search (§4.1: "the citation graph stays Semantic-Scholar/OpenAlex-
anchored").

Stdlib only (``urllib.request`` + ``json``) — no forced third-party
dependency (§11).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from .base import PaperHit

_API_BASE = "https://api.openalex.org/works"


def _fetch_json(url: str) -> dict[str, Any]:
    """Perform the HTTP GET against the OpenAlex works API. Separated from
    the parse step so tests can monkeypatch just this network call."""
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 (fixed https scheme)
        return json.loads(resp.read().decode("utf-8"))


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """OpenAlex ships abstracts as an inverted index (word -> [positions]) to
    dodge publisher copyright on the plain text — reconstruct it."""
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))


def _oa_pointer_from_work(work: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract (oa_url, oa_status) from an OpenAlex work's ``open_access``
    block, falling back to ``primary_location.pdf_url`` when the OA block
    has no url (a work can be OA-flagged without a resolved pdf_url).

    OA-fulltext-enrichment: both fields already exist in the raw work JSON
    (already in ``hit.raw``) — zero extra request, just stop discarding them.
    """
    oa = work.get("open_access") or {}
    oa_status = (oa.get("oa_status") or None) if oa else None
    oa_url = (oa.get("oa_url") or None) if oa else None
    # Only fall back to primary_location.pdf_url when the work is actually
    # flagged OA — a closed work's primary_location can still carry a
    # publisher pdf_url that is NOT open access (paywalled).
    if not oa_url and oa.get("is_oa"):
        primary = work.get("primary_location") or {}
        oa_url = primary.get("pdf_url") or None
    return oa_url, oa_status


def _work_to_hit(work: dict[str, Any]) -> PaperHit:
    external_ids: dict[str, str] = {}
    oa_id = (work.get("id") or "").rsplit("/", 1)[-1]
    if oa_id:
        external_ids["openalex"] = oa_id
    doi = work.get("doi") or ""
    if doi:
        # OpenAlex DOIs are full URLs (https://doi.org/10.xxxx/...)
        external_ids["doi"] = doi.rsplit("doi.org/", 1)[-1]
    ids = work.get("ids") or {}
    mag = ids.get("mag")
    if mag:
        external_ids["mag"] = str(mag)
    pmid = ids.get("pmid") or ""
    if pmid:
        external_ids["pmid"] = pmid.rsplit("/", 1)[-1]

    authors = []
    for a in work.get("authorships") or []:
        name = ((a.get("author") or {}).get("display_name") or "").strip()
        if name:
            authors.append(name)

    oa_url, oa_status = _oa_pointer_from_work(work)

    return PaperHit(
        title=work.get("title") or work.get("display_name") or "",
        year=work.get("publication_year"),
        authors=authors,
        external_ids=external_ids,
        abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
        citation_count=work.get("cited_by_count") or 0,
        source="openalex",
        raw=work,
        oa_url=oa_url,
        oa_status=oa_status,
        oa_source="openalex" if oa_url else None,
    )


class OpenAlexAdapter:
    """Adapter over the OpenAlex Works API. Supports search + both citation-
    graph directions."""

    name = "openalex"

    def search(self, query: str, *, limit: int = 20) -> list[PaperHit]:
        params = urllib.parse.urlencode({"search": query, "per_page": limit})
        data = _fetch_json(f"{_API_BASE}?{params}")
        return [_work_to_hit(w) for w in (data.get("results") or [])]

    def cited_by(self, paper_id: str, *, limit: int = 20) -> list[PaperHit]:
        """Forward snowball via OpenAlex's ``cites:<id>`` filter."""
        oa_id = paper_id.rsplit("/", 1)[-1]
        params = urllib.parse.urlencode({"filter": f"cites:{oa_id}", "per_page": limit})
        data = _fetch_json(f"{_API_BASE}?{params}")
        return [_work_to_hit(w) for w in (data.get("results") or [])]

    def references(self, paper_id: str, *, limit: int = 20) -> list[PaperHit]:
        """Backward snowball: fetch the work itself, resolve its referenced_works."""
        oa_id = paper_id.rsplit("/", 1)[-1]
        work = _fetch_json(f"{_API_BASE}/{oa_id}")
        refs = (work.get("referenced_works") or [])[:limit]
        out: list[PaperHit] = []
        for ref_url in refs:
            ref = _fetch_json(ref_url)
            out.append(_work_to_hit(ref))
        return out
