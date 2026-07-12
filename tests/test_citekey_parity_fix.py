"""test_citekey_parity_fix.py — _parse_new_citekeys_from_text convergence.

Regression pin for a missed consumer of the shared corpus-row annotation
grammar (``review.relevance._annotation_is_new`` /
``corpus_row_annotation_tags``). ``_parse_new_citekeys_from_text`` still
used an exact-match ``annotation.upper() == "[NEW]"`` check (misses the
compound ``[LEG-N][NEW]`` shape real ``review-curate`` emits) and a
``/``-less citekey charset (misses DOI-form citekeys). Both silently
DROP real accepted rows from the Phase-2 fan-out — no error, no warning,
just fewer ``relate-<key>`` nodes than accepted papers.

Acceptance:
  1. Parity: bare [NEW], compound [LEG-N][NEW], and DOI-form citekeys all
     round-trip; [IN-CORPUS:*] stays excluded.
  2. Curate -> Phase-2 count-exact: N accepted rows -> N relate-<key> nodes
     (closes the count-exact blind spot F15's total-zero guard misses).
  3. Real-fixture pin: a sanitized slice of real curate output round-trips
     with the correct count.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import _parse_new_citekeys_from_text


# ---------------------------------------------------------------------------
# 1. Parser-parity unit test
# ---------------------------------------------------------------------------


def test_parity_bare_compound_doi_and_in_corpus_exclusion():
    """Bare [NEW], compound [LEG-N][NEW], and DOI-form citekeys all parse;
    [IN-CORPUS:*] (bare or compound) is excluded."""
    text = """\
| Annotation | Citekey | Title |
|---|---|---|
| [NEW] | smith2020 | Bare NEW |
| [LEG-1][NEW] {tag-a,tag-b} | jones2021 | Compound NEW with trailing tags |
| [IN-CORPUS:old2019] | old2019 | Already vetted |
| [NEW] | 10.1016/j.knosys.2026.116598 | DOI-form NEW citekey |
"""
    result = _parse_new_citekeys_from_text(text)
    assert set(result) == {
        "smith2020",
        "jones2021",
        "10.1016/j.knosys.2026.116598",
    }
    assert "old2019" not in result


# ---------------------------------------------------------------------------
# 2. Curate -> Phase-2 round-trip: count-exact guard
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg(tmp_instance):
    from research_vault.config import load_config

    return load_config(reload=True)


def _relate_node_ids(manifest: dict) -> set[str]:
    return {
        node["id"]
        for node in manifest.get("nodes", [])
        if node.get("id", "").startswith("relate-")
    }


def _relate_node_count(manifest: dict) -> int:
    return len(_relate_node_ids(manifest))


def test_curate_to_phase2_count_exact_roundtrip(cfg, tmp_instance):
    """N accepted rows (mixing bare NEW, compound NEW, DOI-form citekey,
    and IN-CORPUS) -> exactly N relate-<key> nodes in phase2-dag.json.

    This is the count-exact guard: it fails on ANY partial silent drop, not
    just the total-zero case the earlier green-but-vacuous guard covers.
    """
    from research_vault.review import cmd_new, cmd_expand, _review_artifact_dir

    note_path, review_dir, manifest = cmd_new(
        "demo-research",
        "scope-parity",
        question="Test citekey parity round-trip",
        config=cfg,
    )

    corpus = review_dir / "_corpus.md"
    corpus.write_text(
        """\
| Annotation | Citekey | Title |
|---|---|---|
| [NEW] | smith2020 | Bare NEW |
| [LEG-1][NEW] {arith} | jones2021 | Compound NEW |
| [NEW] | 10.1016/j.knosys.2026.116598 | DOI-form NEW citekey |
| [NEW] | 10.48550/arXiv.2210.01240 | Another DOI-form NEW citekey |
| [IN-CORPUS:old2019] | old2019 | Already vetted, excluded |
""",
        encoding="utf-8",
    )
    accepted_new_rows = 4  # smith2020, jones2021, and the two DOI-form rows

    phase2 = cmd_expand("demo-research", "scope-parity", corpus_path=corpus, config=cfg)

    relate_ids = _relate_node_ids(phase2)
    assert len(relate_ids) == accepted_new_rows, (
        f"expected {accepted_new_rows} relate-<key> nodes, got "
        f"{len(relate_ids)}: {sorted(relate_ids)}"
    )
    for key in ("smith2020", "jones2021", "10.1016/j.knosys.2026.116598", "10.48550/arXiv.2210.01240"):
        assert f"relate-{key}" in relate_ids, f"missing relate node for {key!r}"
    assert "relate-old2019" not in relate_ids


# ---------------------------------------------------------------------------
# 3. Real-fixture pin
# ---------------------------------------------------------------------------


def test_real_shape_fixture_roundtrip(cfg, tmp_instance):
    """A sanitized slice of real review-curate output round-trips to the
    exact expected relate-node count — the regression pin against a future
    fifth divergent corpus-row parser."""
    from research_vault.review import cmd_new, cmd_expand

    fixture_path = Path(__file__).parent / "fixtures" / "corpus_real_shape_slice.md"
    fixture_text = fixture_path.read_text(encoding="utf-8")

    # Sanity: confirm the fixture actually carries the shapes this test
    # claims to guard — a fixture that silently lost its DOI/compound rows
    # would make this test pass vacuously.
    assert "[LEG-1][NEW]" in fixture_text
    assert "[IN-CORPUS:" in fixture_text
    assert "10.48550/arXiv." in fixture_text or "10.1016/" in fixture_text

    note_path, review_dir, manifest = cmd_new(
        "demo-research",
        "scope-real-slice",
        question="Test real-shape fixture round-trip",
        config=cfg,
    )
    corpus = review_dir / "_corpus.md"
    corpus.write_text(fixture_text, encoding="utf-8")

    expected_new = len(_parse_new_citekeys_from_text(fixture_text))
    assert expected_new == 19, (
        "fixture drifted — expected 19 NEW rows (18 bare + 1 compound), "
        f"got {expected_new}"
    )

    phase2 = cmd_expand(
        "demo-research", "scope-real-slice", corpus_path=corpus, config=cfg
    )
    assert _relate_node_count(phase2) == expected_new
