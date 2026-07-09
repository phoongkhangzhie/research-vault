# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/pubmed.py — PubMedAdapter (NG-2, opt-in source per D4 — bio/medical RQs).

NCBI E-utilities (esearch + esummary), stdlib ``urllib``/``json`` only — no
forced third-party dependency (§11). PubMed exposes no citation graph via
esummary/esearch alone (NCBI's citation-linking API is a separate, heavier
integration) — ``cited_by``/``references`` raise ``NotSupported``, matching
arXiv's shape; PubMed is a SEARCH-breadth source, not a depth-snowball anchor
(the citation graph stays Semantic-Scholar/OpenAlex-anchored, §4.1).

Opt-in per D4: only added to a protocol's ``sources:`` list when the RQ
warrants biomedical literature (never in the default-on 3-source set).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from .base import NotSupported, PaperHit

_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


def _fetch_json(url: str) -> dict[str, Any]:
    """Perform the HTTP GET against NCBI E-utilities. Separated from the
    parse step so tests can monkeypatch just this network call."""
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 (fixed https scheme)
        return json.loads(resp.read().decode("utf-8"))


def _esearch_ids(query: str, *, limit: int) -> list[str]:
    params = urllib.parse.urlencode({
        "db": "pubmed", "term": query, "retmode": "json", "retmax": limit,
    })
    data = _fetch_json(f"{_ESEARCH}?{params}")
    return list((data.get("esearchresult") or {}).get("idlist") or [])


def _esummary_docs(pmids: list[str]) -> dict[str, Any]:
    if not pmids:
        return {}
    params = urllib.parse.urlencode({
        "db": "pubmed", "id": ",".join(pmids), "retmode": "json",
    })
    data = _fetch_json(f"{_ESUMMARY}?{params}")
    result = data.get("result") or {}
    uids = result.get("uids") or []
    return {uid: result[uid] for uid in uids if uid in result}


def _doc_to_hit(pmid: str, doc: dict[str, Any]) -> PaperHit:
    external_ids: dict[str, str] = {"pmid": pmid}
    for aid in doc.get("articleids") or []:
        if aid.get("idtype") == "doi" and aid.get("value"):
            external_ids["doi"] = aid["value"]
        # OA-fulltext-enrichment: PMCID is what the `pmc` provider needs
        # (EuropePMC/NCBI OA full-text JATS XML) — surface it when esummary
        # carries one (not every PubMed record has a PMC deposit).
        if aid.get("idtype") == "pmc" and aid.get("value"):
            external_ids["pmcid"] = aid["value"]

    authors = [
        a.get("name", "").strip()
        for a in (doc.get("authors") or [])
        if a.get("name")
    ]

    pubdate = doc.get("pubdate") or ""
    year = int(pubdate[:4]) if pubdate[:4].isdigit() else None

    venue = (doc.get("fulljournalname") or doc.get("source") or "").strip() or None

    return PaperHit(
        title=doc.get("title") or "",
        year=year,
        authors=authors,
        external_ids=external_ids,
        abstract="",  # esummary does not carry the abstract text
        citation_count=0,  # PubMed esummary does not expose citation counts
        source="pubmed",
        raw=doc,
        venue=venue,
    )


class PubMedAdapter:
    """Adapter over NCBI E-utilities. Search only — opt-in, biomedical-domain
    source (no citation graph via this lightweight integration)."""

    name = "pubmed"

    def search(self, query: str, *, limit: int = 20) -> list[PaperHit]:
        pmids = _esearch_ids(query, limit=limit)
        docs = _esummary_docs(pmids)
        return [_doc_to_hit(pmid, docs[pmid]) for pmid in pmids if pmid in docs]

    def cited_by(self, paper_id: str, *, limit: int = 20) -> list[PaperHit]:
        raise NotSupported("pubmed adapter (esearch/esummary) has no citation graph")

    def references(self, paper_id: str, *, limit: int = 20) -> list[PaperHit]:
        raise NotSupported("pubmed adapter (esearch/esummary) has no citation graph")
