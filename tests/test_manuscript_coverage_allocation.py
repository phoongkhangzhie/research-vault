"""test_manuscript_coverage_allocation.py — PR-A: the full-corpus coverage
contract enforced at the framework stage.

The verified 0.3.0 drop mechanism: a real corpus (47 papers) routed to the lossy
per-branch fallback with NO gate blocking an unallocated paper (~20/47 silently
dropped). This PR makes full-corpus coverage a framework-stage contract:
`_coverage-map.md` allocates EVERY frozen-corpus citekey, and
`check_coverage_allocation_gate` fail-closed BLOCKs any unallocated / reasonless /
non-corpus citekey BEFORE any section is drafted.

sr: PR-A (coverage contract + single-pass ceiling fix)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.manuscript.check_gates import check_coverage_allocation_gate


# ---------------------------------------------------------------------------
# helpers — write a frozen _corpus.md + a _coverage-map.md
# ---------------------------------------------------------------------------

def _write_corpus(path: Path, citekeys: list[str]) -> None:
    """Write a minimal frozen _corpus.md (the [NEW]/[IN-CORPUS] table shape
    ``review._parse_corpus_citekeys`` reads)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Corpus\n", "| Status | Citekey | Title |", "| --- | --- | --- |"]
    for ck in citekeys:
        lines.append(f"| [NEW] | {ck} | Some title |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_map(path: Path, *, used=None, clustered=None, deferred=None) -> None:
    """Write a _coverage-map.md with the D8 mapping-list frontmatter format.

    used:      list of (citekey, branch)
    clustered: list of (citekey, group, reason)
    deferred:  list of (citekey, reason)
    """
    fm = ["---", "coverage_map: true"]
    if used is not None:
        fm.append("used:")
        for ck, branch in used:
            fm.append(f"  - citekey: {ck}")
            fm.append(f"    branch: {branch}")
    if clustered is not None:
        fm.append("clustered:")
        for ck, group, reason in clustered:
            fm.append(f"  - citekey: {ck}")
            fm.append(f"    group: {group}")
            fm.append(f"    reason: {reason}")
    if deferred is not None:
        fm.append("deferred:")
        for ck, reason in deferred:
            fm.append(f"  - citekey: {ck}")
            fm.append(f"    reason: {reason}")
    fm.append("---")
    fm.append("")
    fm.append("## Allocation rationale\n\nprose here.\n")
    path.write_text("\n".join(fm), encoding="utf-8")


# ---------------------------------------------------------------------------
# no-op / fail-closed edges
# ---------------------------------------------------------------------------

def test_no_corpus_is_honest_noop(tmp_path: Path):
    # No frozen corpus yet — nothing to allocate, honest no-op (never a BLOCK).
    res = check_coverage_allocation_gate(
        tmp_path / "reviews" / "s" / "_corpus.md", tmp_path / "_coverage-map.md"
    )
    assert res["ok"] is True
    assert res["errors"] == []


def test_empty_corpus_is_honest_noop(tmp_path: Path):
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    _write_corpus(corpus, [])
    res = check_coverage_allocation_gate(corpus, tmp_path / "_coverage-map.md")
    assert res["ok"] is True


def test_corpus_but_no_map_blocks(tmp_path: Path):
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    _write_corpus(corpus, ["a2020", "b2021"])
    res = check_coverage_allocation_gate(corpus, tmp_path / "_coverage-map.md")
    assert res["ok"] is False
    assert any("_coverage-map.md" in e for e in res["errors"])


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------

def test_all_allocated_passes(tmp_path: Path):
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    _write_corpus(corpus, ["a2020", "b2021", "c2022"])
    cmap = tmp_path / "_coverage-map.md"
    _write_map(
        cmap,
        used=[("a2020", "mechanism-A"), ("b2021", "mechanism-B")],
        deferred=[("c2022", "out of scope — different domain")],
    )
    res = check_coverage_allocation_gate(corpus, cmap)
    assert res["ok"] is True, res["errors"]
    assert res["errors"] == []


# ---------------------------------------------------------------------------
# the load-bearing BLOCK cases
# ---------------------------------------------------------------------------

def test_unallocated_citekey_blocks_naming_it(tmp_path: Path):
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    _write_corpus(corpus, ["a2020", "b2021", "dropped2099"])
    cmap = tmp_path / "_coverage-map.md"
    _write_map(cmap, used=[("a2020", "br-A"), ("b2021", "br-B")])
    res = check_coverage_allocation_gate(corpus, cmap)
    assert res["ok"] is False
    assert any("dropped2099" in e for e in res["errors"])


def test_clustered_without_reason_blocks(tmp_path: Path):
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    _write_corpus(corpus, ["a2020"])
    cmap = tmp_path / "_coverage-map.md"
    _write_map(cmap, clustered=[("a2020", "grp", "")])
    res = check_coverage_allocation_gate(corpus, cmap)
    assert res["ok"] is False
    assert any("a2020" in e and "reason" in e.lower() for e in res["errors"])


def test_clustered_without_group_blocks(tmp_path: Path):
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    _write_corpus(corpus, ["a2020"])
    cmap = tmp_path / "_coverage-map.md"
    _write_map(cmap, clustered=[("a2020", "", "a real reason")])
    res = check_coverage_allocation_gate(corpus, cmap)
    assert res["ok"] is False
    assert any("a2020" in e and "group" in e.lower() for e in res["errors"])


def test_deferred_without_reason_blocks(tmp_path: Path):
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    _write_corpus(corpus, ["a2020"])
    cmap = tmp_path / "_coverage-map.md"
    _write_map(cmap, deferred=[("a2020", "")])
    res = check_coverage_allocation_gate(corpus, cmap)
    assert res["ok"] is False
    assert any("a2020" in e and "reason" in e.lower() for e in res["errors"])


def test_used_without_branch_blocks(tmp_path: Path):
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    _write_corpus(corpus, ["a2020"])
    cmap = tmp_path / "_coverage-map.md"
    _write_map(cmap, used=[("a2020", "")])
    res = check_coverage_allocation_gate(corpus, cmap)
    assert res["ok"] is False
    assert any("a2020" in e and "branch" in e.lower() for e in res["errors"])


def test_non_corpus_citekey_blocks(tmp_path: Path):
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    _write_corpus(corpus, ["a2020"])
    cmap = tmp_path / "_coverage-map.md"
    _write_map(cmap, used=[("a2020", "br-A"), ("ghost2099", "br-B")])
    res = check_coverage_allocation_gate(corpus, cmap)
    assert res["ok"] is False
    assert any("ghost2099" in e and "non-corpus" in e.lower() for e in res["errors"])


def test_cross_cutting_multi_bucket_allocation_passes(tmp_path: Path):
    # The coverage contract is SURJECTIVE (allocated >= once), not a bijective
    # partition — a citekey load-bearing in two branches (or e.g. a
    # method-family member AND a tension exemplar) is legitimate cross-cutting
    # allocation, not a contradiction. This must PASS, not BLOCK.
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    _write_corpus(corpus, ["a2020", "b2021"])
    cmap = tmp_path / "_coverage-map.md"
    _write_map(
        cmap,
        used=[("a2020", "br-A"), ("a2020", "br-B"), ("b2021", "br-B")],
    )
    res = check_coverage_allocation_gate(corpus, cmap)
    assert res["ok"] is True, res["errors"]
    assert res["errors"] == []


def test_cross_cutting_across_different_buckets_passes(tmp_path: Path):
    # Same property, but spanning two DIFFERENT buckets (used + clustered) —
    # also legitimate: a paper can be both cited in a named branch AND folded
    # into a cross-cutting named group.
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    _write_corpus(corpus, ["a2020"])
    cmap = tmp_path / "_coverage-map.md"
    _write_map(
        cmap,
        used=[("a2020", "br-A")],
        clustered=[("a2020", "grp-tension", "also a tension exemplar")],
    )
    res = check_coverage_allocation_gate(corpus, cmap)
    assert res["ok"] is True, res["errors"]
    assert res["errors"] == []


def test_no_allocated_twice_block_remains(tmp_path: Path):
    # Regression pin: the old "allocated twice" BLOCK text must be GONE from
    # the gate's error vocabulary entirely — never emitted for any input.
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    _write_corpus(corpus, ["a2020"])
    cmap = tmp_path / "_coverage-map.md"
    _write_map(
        cmap,
        used=[("a2020", "br-A")],
        deferred=[("a2020", "also cross-cutting")],
    )
    res = check_coverage_allocation_gate(corpus, cmap)
    assert res["ok"] is True, res["errors"]
    assert not any("twice" in e.lower() or "duplicate" in e.lower() for e in res["errors"])


def test_malformed_entry_missing_citekey_blocks(tmp_path: Path):
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    _write_corpus(corpus, ["a2020"])
    cmap = tmp_path / "_coverage-map.md"
    # A used entry with a bare scalar (no citekey: key) — parser yields a str,
    # not a dict — malformed.
    cmap.write_text(
        "---\ncoverage_map: true\nused:\n  - a2020\n---\n\nprose\n",
        encoding="utf-8",
    )
    res = check_coverage_allocation_gate(corpus, cmap)
    assert res["ok"] is False
    assert any("malformed" in e.lower() for e in res["errors"])


def test_corpus_schema_error_is_failclosed_block(tmp_path: Path):
    corpus = tmp_path / "reviews" / "s" / "_corpus.md"
    corpus.parent.mkdir(parents=True, exist_ok=True)
    # A bracket-shaped annotation that is neither [NEW] nor [IN-CORPUS:*] —
    # _parse_corpus_citekeys raises CorpusSchemaError; the gate must catch it
    # and surface a fail-closed BLOCK, never crash.
    corpus.write_text(
        "| [BOGUS] | a2020 | title |\n",
        encoding="utf-8",
    )
    cmap = tmp_path / "_coverage-map.md"
    _write_map(cmap, used=[("a2020", "br-A")])
    res = check_coverage_allocation_gate(corpus, cmap)
    assert res["ok"] is False
    assert any("corpus" in e.lower() for e in res["errors"])
