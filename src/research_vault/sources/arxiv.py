"""sources/arxiv.py — ArxivAdapter (NG-2, breadth source #2).

arXiv's public API (export.arxiv.org Atom feed) has no citation graph — a
paper's forward/backward citations are not exposed. ``cited_by``/``references``
raise ``NotSupported`` so callers (the width sweep) skip this adapter for
those ops rather than reading an empty list as "zero citations" (§4.1).

Stdlib only (``urllib.request`` + the stdlib ``xml.etree`` Atom parse) — no
forced third-party dependency (§11).
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .base import NotSupported, PaperHit

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"
_API_BASE = "http://export.arxiv.org/api/query"


def _fetch_atom(query: str, *, limit: int) -> str:
    """Perform the HTTP GET against arXiv's export API. Separated from the
    parse step so tests can monkeypatch just this network call."""
    params = urllib.parse.urlencode({
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": limit,
    })
    url = f"{_API_BASE}?{params}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 (fixed http(s) scheme)
        return resp.read().decode("utf-8")


def _parse_atom_entry(entry: ET.Element) -> PaperHit:
    title = (entry.findtext(f"{_ATOM_NS}title") or "").strip()
    title = re.sub(r"\s+", " ", title)
    summary = (entry.findtext(f"{_ATOM_NS}summary") or "").strip()
    summary = re.sub(r"\s+", " ", summary)

    published = entry.findtext(f"{_ATOM_NS}published") or ""
    year = int(published[:4]) if published[:4].isdigit() else None

    authors = []
    for a in entry.findall(f"{_ATOM_NS}author"):
        name = (a.findtext(f"{_ATOM_NS}name") or "").strip()
        if name:
            authors.append(name)

    entry_id = entry.findtext(f"{_ATOM_NS}id") or ""
    m = re.search(r"arxiv\.org/abs/([^v]+)(v\d+)?", entry_id)
    arxiv_id = m.group(1) if m else ""

    doi = entry.findtext(f"{_ARXIV_NS}doi") or ""

    external_ids: dict[str, str] = {}
    if arxiv_id:
        external_ids["arxiv"] = arxiv_id
    if doi:
        external_ids["doi"] = doi.strip()

    return PaperHit(
        title=title,
        year=year,
        authors=authors,
        external_ids=external_ids,
        abstract=summary,
        citation_count=0,  # arXiv does not expose citation counts
        source="arxiv",
        raw={"entry_id": entry_id},
    )


class ArxivAdapter:
    """Adapter over arXiv's public export API. Search only — no citation
    graph (raises NotSupported for cited_by/references)."""

    name = "arxiv"

    def search(self, query: str, *, limit: int = 20) -> list[PaperHit]:
        xml_text = _fetch_atom(query, limit=limit)
        root = ET.fromstring(xml_text)  # noqa: S314 (trusted arxiv.org response)
        return [_parse_atom_entry(e) for e in root.findall(f"{_ATOM_NS}entry")]

    def cited_by(self, paper_id: str, *, limit: int = 20) -> list[PaperHit]:
        raise NotSupported("arXiv has no citation graph — use semantic-scholar/openalex")

    def references(self, paper_id: str, *, limit: int = 20) -> list[PaperHit]:
        raise NotSupported("arXiv has no citation graph — use semantic-scholar/openalex")
