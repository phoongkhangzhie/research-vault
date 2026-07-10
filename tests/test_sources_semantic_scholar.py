"""test_sources_semantic_scholar.py — NG-1 SemanticScholarAdapter parity.

Pure-refactor acceptance: the adapter must shell to the SAME asta subcommands
research.py used to call inline, and produce PaperHit.raw == the original S2
dict (byte-identical downstream behavior for _corpus_annotation/_print_candidates).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from research_vault.sources.base import AdapterFetchError
from research_vault.sources.semantic_scholar import SemanticScholarAdapter

S2_PAPER = {
    "title": "Attention Is All You Need",
    "year": 2017,
    "authors": [{"name": "Ashish Vaswani"}],
    "externalIds": {"DOI": "10.48550/ARXIV.1706.03762", "ArXiv": "1706.03762"},
    "citationCount": 50000,
    "abstract": "We propose a new architecture...",
}

S2_PAPER_WITH_OA = {
    **S2_PAPER,
    "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762", "status": "GREEN"},
}


def test_search_calls_asta_papers_search(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [S2_PAPER]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.search("attention", limit=10)

    assert captured["cmd"][:3] == ["asta", "papers", "search"]
    assert len(hits) == 1
    hit = hits[0]
    assert hit.title == "Attention Is All You Need"
    assert hit.year == 2017
    assert hit.authors == ["Ashish Vaswani"]
    assert hit.external_ids["doi"] == "10.48550/ARXIV.1706.03762"
    assert hit.external_ids["arxiv"] == "1706.03762"
    assert hit.citation_count == 50000
    assert hit.source == "semantic-scholar"
    assert hit.raw == S2_PAPER  # zero-behavior-change seam


def test_search_carries_venue_when_present(monkeypatch) -> None:
    paper_with_venue = {**S2_PAPER, "venue": "NeurIPS"}

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [paper_with_venue]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.search("attention", limit=10)
    assert hits[0].venue == "NeurIPS"


def test_search_venue_absent_is_none(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [S2_PAPER]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.search("attention", limit=10)
    assert hits[0].venue is None


def test_search_fields_projection_includes_venue_and_tldr(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [S2_PAPER]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    adapter.search("attention", limit=10)

    fields_idx = captured["cmd"].index("--fields") + 1
    assert "venue" in captured["cmd"][fields_idx]
    assert "tldr" in captured["cmd"][fields_idx]


def test_search_tldr_carried_in_raw_for_abstract_fallback(monkeypatch) -> None:
    paper_with_tldr = {**S2_PAPER, "tldr": {"model": "tldr@v2", "text": "Short summary."}}

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [paper_with_tldr]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.search("attention", limit=10)
    # zero-behavior-change seam: tldr lives in raw, not a bespoke new field —
    # sweep.py's evidence-snippet fallback reads it from there.
    assert hits[0].raw["tldr"]["text"] == "Short summary."


def test_cited_by_calls_asta_papers_citations(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [{"citingPaper": S2_PAPER}]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.cited_by("ARXIV:1706.03762", limit=20)

    assert captured["cmd"][:3] == ["asta", "papers", "citations"]
    assert len(hits) == 1
    assert hits[0].raw == S2_PAPER


def test_references_calls_asta_papers_get(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"references": [S2_PAPER]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.references("ARXIV:1706.03762")

    assert captured["cmd"][:3] == ["asta", "papers", "get"]
    assert len(hits) == 1
    assert hits[0].raw == S2_PAPER


def test_search_requests_openaccesspdf_field(monkeypatch) -> None:
    # OA-fulltext-enrichment: openAccessPdf must be in the --fields projection
    # or the field is never even in hit.raw for downstream OA capture.
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [S2_PAPER]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    adapter.search("attention", limit=10)

    fields_idx = captured["cmd"].index("--fields") + 1
    assert "openAccessPdf" in captured["cmd"][fields_idx]


def test_search_captures_oa_pointer_from_openaccesspdf(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [S2_PAPER_WITH_OA]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.search("attention", limit=10)

    assert hits[0].oa_url == "https://arxiv.org/pdf/1706.03762"
    assert hits[0].oa_status == "green"
    assert hits[0].oa_source == "semantic-scholar"


def test_search_no_oa_pointer_when_openaccesspdf_absent(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [S2_PAPER]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.search("attention", limit=10)

    assert hits[0].oa_url is None
    assert hits[0].oa_status is None
    assert hits[0].oa_source is None


# ---------------------------------------------------------------------------
# Substance-screening gap fix (pre-publish hardening batch, 2026-07-09): the
# snowball walk's `cited_by`/`references` calls never requested `abstract` or
# `venue` — so `_corpus_raw.md` rows had no evidence beyond title, and
# `review-curate` degraded to title-only screening (cannot verify the
# "measured human baseline" inclusion axis, which is not title-visible).
# ---------------------------------------------------------------------------

def test_cited_by_fields_projection_includes_abstract_and_venue(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [{"citingPaper": S2_PAPER}]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    adapter.cited_by("ARXIV:1706.03762", limit=20)

    fields_idx = captured["cmd"].index("--fields") + 1
    assert "abstract" in captured["cmd"][fields_idx]
    assert "venue" in captured["cmd"][fields_idx]


def test_references_fields_projection_includes_abstract_and_venue(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"references": [S2_PAPER]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    adapter.references("ARXIV:1706.03762")

    fields_idx = captured["cmd"].index("--fields") + 1
    assert "references.abstract" in captured["cmd"][fields_idx]
    assert "references.venue" in captured["cmd"][fields_idx]


def test_search_exits_on_asta_failure(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "boom"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    with pytest.raises(SystemExit):
        adapter.search("q")


# ---------------------------------------------------------------------------
# 2026-07-09 live-asta validation bug: cited_by/references must raise a
# catchable AdapterFetchError (NOT sys.exit) on a non-zero asta exit (e.g. a
# 404 for one seed id) — SystemExit is a BaseException, invisible to the
# multi-round snowball walk's `except Exception` degrade clauses, so it used
# to abort the ENTIRE walk on one unresolvable seed. `search` is unaffected
# (still sys.exit — a single-shot CLI action, no multi-item walk to degrade).
# ---------------------------------------------------------------------------

def test_cited_by_raises_adapter_fetch_error_not_systemexit(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "asta: paper not found (404)"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    with pytest.raises(AdapterFetchError):
        adapter.cited_by("ARXIV:9999.9999")
    # Explicitly NOT SystemExit — the whole point of the fix.
    try:
        adapter.cited_by("ARXIV:9999.9999")
    except AdapterFetchError:
        pass
    except SystemExit:
        pytest.fail("cited_by must not raise SystemExit — it must raise AdapterFetchError")


def test_references_raises_adapter_fetch_error_not_systemexit(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "asta: paper not found (404)"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    with pytest.raises(AdapterFetchError):
        adapter.references("ARXIV:9999.9999")
    try:
        adapter.references("ARXIV:9999.9999")
    except AdapterFetchError:
        pass
    except SystemExit:
        pytest.fail("references must not raise SystemExit — it must raise AdapterFetchError")


# ---------------------------------------------------------------------------
# PR-S1 (pre-publish fix, 2026-07-10): the BACKWARD (references) snowball
# direction ignored `per_round_limit` entirely — `asta papers get` has no
# server-side `--limit`/pagination for a nested `references.*` projection
# (unlike `cited_by`'s dedicated `asta papers citations ... --limit`
# endpoint), so a seed's FULL reference list was always returned regardless
# of the caller's `limit` kwarg. Observed on a live run: forward correctly
# bounded to 31 hits, backward returned 461 from 5 seeds.
# ---------------------------------------------------------------------------

def _fake_references_run(n: int):
    """Build a fake_run returning `n` distinct reference items, each
    identifiable by a unique DOI suffix so order/truncation is verifiable."""
    items = [
        {**S2_PAPER, "externalIds": {"DOI": f"10.48550/ARXIV.ref-{i}"}}
        for i in range(n)
    ]

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"references": items})
        r.stderr = ""
        return r

    return fake_run, items


def test_references_bound_bites_when_seed_has_more_than_limit(monkeypatch) -> None:
    """(a) acceptance: a seed with N > per_round_limit references yields
    <= per_round_limit backward candidates in that round — the knob now
    bites. Pre-fix this returned all 50 (the knob was dead)."""
    fake_run, _items = _fake_references_run(50)
    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()

    hits = adapter.references("ARXIV:1706.03762", limit=20)

    assert len(hits) == 20


def test_references_bound_preserves_as_returned_order_not_arbitrary_drop(monkeypatch) -> None:
    """The truncation must be a deterministic, documented as-returned-order
    prefix (per-round throttle), not an arbitrary/random subset — so the
    excluded tail is knowable (it's whatever asta ranked/ordered last),
    never a silent, unreproducible drop."""
    fake_run, items = _fake_references_run(10)
    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()

    hits = adapter.references("ARXIV:1706.03762", limit=4)

    assert len(hits) == 4
    kept_dois = [h.external_ids["doi"] for h in hits]
    expected_dois = [items[i]["externalIds"]["DOI"] for i in range(4)]
    assert kept_dois == expected_dois


def test_references_below_limit_unaffected(monkeypatch) -> None:
    """No behavior change when a seed has fewer references than the limit."""
    fake_run, items = _fake_references_run(3)
    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()

    hits = adapter.references("ARXIV:1706.03762", limit=20)

    assert len(hits) == 3


def test_cited_by_unaffected_by_references_bound_fix(monkeypatch) -> None:
    """(b) regression: forward direction behavior is unchanged — still
    delegates the limit to asta's own `--limit` flag, no client-side
    truncation added there."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps(
            {"data": [{"citingPaper": {**S2_PAPER, "externalIds": {"DOI": f"10.1/{i}"}}} for i in range(50)]}
        )
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.cited_by("ARXIV:1706.03762", limit=20)

    # asta's own --limit flag is what bounds this (mock returns 50 regardless
    # of the flag — proving cited_by does NOT client-side-truncate, unlike
    # the references fix above).
    assert "--limit" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--limit") + 1] == "20"
    assert len(hits) == 50
