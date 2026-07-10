"""test_facet_matrix_breadth.py — PR-2: facet-matrix query generator + nested
stance-tagged schema (Q breadth), the counter-facet strength guard, and the
`approve-protocol` D-7 empty-counter-pole structural BLOCK.

Design: docs/superpowers/specs/2026-07-10-search-breadth-autonomous-remediation-design.md
Method:  docs/superpowers/specs/2026-07-10-principled-query-planning-research.md
Greenlit-by: PR-0's retrospective bake-off (the corpus_freeze hash-stability +
multi-frame-union prerequisites this PR's acceptance criteria re-check).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.sweep import (
    DEFAULT_FETCH_BUDGET,
    count_distinct_queries,
    dedupe_near_duplicate_queries,
    group_facet_stances,
    parse_angle_matrix,
    run_width_sweep,
    validate_matrix_band,
    MATRIX_BAND_HI,
    MATRIX_BAND_LO,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LEGACY_SCALAR_PROTOCOL = """---
type: review-protocol
question: "Does X improve Y?"
seed_queries:
  by-method:     "transformer attention mechanism"
  by-outcome:    "translation quality improvement"
sources: [semantic-scholar, arxiv]
---

# Protocol
"""

NESTED_PROTOCOL = """---
type: review-protocol
question: "Do LLM personas drift over multi-turn conversation?"
seed_queries:
  by-temporal:
    thesis:
      - "cultural drift multi-turn LLM persona"
      - "homogenization LLM roleplay over turns"
    counter:
      - "persona stability multi-turn LLM"
      - "value persistence long-horizon dialogue agent"
  by-method: "transformer attention mechanism"
sources: [semantic-scholar, arxiv, openalex]
counter-position: "persona-consistency / multi-turn-RL-persistence literature"
---

# Protocol
"""

MIXED_MULTI_FRAME_PROTOCOL = """---
type: review-protocol
question: "Does an intervention change cultural values, and do those values persist over time?"
seed_queries:
  by-population:
    thesis:
      - "LLM agents simulated population cultural values"
      - "multi-agent society value alignment"
    counter:
      - "human population baseline cultural values"
  by-temporal:
    thesis:
      - "value drift over simulated time"
    counter:
      - "value persistence stability over simulated time"
sources: [semantic-scholar, arxiv]
counter-position: "stability + baseline-population sub-literatures"
---
"""


# ---------------------------------------------------------------------------
# Parser extension: nested thesis/counter facets FLATTEN into distinct keys,
# legacy scalar keys are untouched — mixed legacy+nested in one protocol.
# ---------------------------------------------------------------------------

class TestParseAngleMatrixNested:
    def test_legacy_scalar_form_still_parses_unchanged(self) -> None:
        matrix = parse_angle_matrix(LEGACY_SCALAR_PROTOCOL)
        assert matrix == {
            "by-method": "transformer attention mechanism",
            "by-outcome": "translation quality improvement",
        }

    def test_nested_facet_flattens_to_distinct_keys(self) -> None:
        matrix = parse_angle_matrix(NESTED_PROTOCOL)
        assert matrix["by-temporal.thesis.0"] == "cultural drift multi-turn LLM persona"
        assert matrix["by-temporal.thesis.1"] == "homogenization LLM roleplay over turns"
        assert matrix["by-temporal.counter.0"] == "persona stability multi-turn LLM"
        assert matrix["by-temporal.counter.1"] == "value persistence long-horizon dialogue agent"

    def test_mixed_legacy_and_nested_in_same_protocol(self) -> None:
        matrix = parse_angle_matrix(NESTED_PROTOCOL)
        # The legacy scalar `by-method` angle survives alongside the nested
        # `by-temporal` facet — a to-be-migrated protocol is never forced to
        # rewrite every angle in one pass.
        assert matrix["by-method"] == "transformer attention mechanism"

    def test_run_width_sweep_still_consumes_flat_dict_unchanged(self, monkeypatch) -> None:
        """`run_width_sweep`'s (angle-key x source) cross-product needs ZERO
        changes to consume the wider matrix — same concurrency machinery,
        more/richer keys (charter §6 reuse)."""
        from research_vault.sources import sweep as sweep_mod

        calls = []

        def fake_fetch_cell(angle, query, source, *, limit, **_ignored):
            calls.append((angle, source))
            from research_vault.sources.sweep import SweepCell
            return SweepCell(angle=angle, query=query, source=source, hits=[])

        monkeypatch.setattr(sweep_mod, "_fetch_cell", fake_fetch_cell)
        matrix = parse_angle_matrix(NESTED_PROTOCOL)
        cells = run_width_sweep(matrix, ["semantic-scholar"])
        assert len(cells) == len(matrix)  # one cell per flattened query x 1 source


# ---------------------------------------------------------------------------
# group_facet_stances — reconstructs the stance-tagged facet structure the
# D-7 gate and the cold counter-facet guard both need.
# ---------------------------------------------------------------------------

class TestGroupFacetStances:
    def test_groups_nested_facet_by_stance_in_order(self) -> None:
        matrix = parse_angle_matrix(NESTED_PROTOCOL)
        facets = group_facet_stances(matrix)
        assert facets["by-temporal"]["thesis"] == [
            "cultural drift multi-turn LLM persona",
            "homogenization LLM roleplay over turns",
        ]
        assert facets["by-temporal"]["counter"] == [
            "persona stability multi-turn LLM",
            "value persistence long-horizon dialogue agent",
        ]

    def test_legacy_scalar_keys_are_not_facets(self) -> None:
        """A legacy scalar angle never declared a counter-pole — it must be
        ABSENT from the stance grouping, not appear as an empty facet (that
        would wrongly make it eligible for the D-7 empty-counter BLOCK)."""
        matrix = parse_angle_matrix(NESTED_PROTOCOL)
        facets = group_facet_stances(matrix)
        assert "by-method" not in facets

    def test_multi_frame_union_retains_both_frames_crux_facets(self) -> None:
        """A multi-frame RQ (PICO population-frame + SPIDER temporal-frame)
        must retain BOTH frames' facets — the union rule (friction i). This
        is the exact downstream-project defect: the temporal/stability facet must survive
        alongside a population facet, neither picked over the other under
        'default to PICO'."""
        matrix = parse_angle_matrix(MIXED_MULTI_FRAME_PROTOCOL)
        facets = group_facet_stances(matrix)
        assert set(facets) == {"by-population", "by-temporal"}
        assert facets["by-population"]["counter"]
        assert facets["by-temporal"]["counter"]


# ---------------------------------------------------------------------------
# Near-dup query filter (D-4) + post-dedup distinct-query count (D-1, friction
# iii) — the 40-100 band assertion must hold on the POST-dedup count.
# ---------------------------------------------------------------------------

class TestQueryDedupAndBand:
    def test_near_duplicate_queries_collapse(self) -> None:
        queries = [
            "cultural drift over multi-turn LLM persona interaction",
            "cultural drift over multi turn LLM persona interactions",  # near-literal restatement
            "persona stability multi-turn LLM",
        ]
        kept = dedupe_near_duplicate_queries(queries, threshold=0.7)
        assert len(kept) == 2

    def test_distinct_queries_are_not_collapsed(self) -> None:
        queries = [
            "cultural drift over multi-turn LLM persona interaction",
            "persona stability multi-turn LLM value persistence",
        ]
        kept = dedupe_near_duplicate_queries(queries, threshold=0.9)
        assert len(kept) == 2

    def test_count_distinct_queries_derives_from_matrix_post_dedup(self) -> None:
        matrix = {
            "a": "cultural drift over multi-turn LLM persona",
            "b": "cultural drift over multi turn LLM personas",  # near-dup of a
            "c": "value persistence long horizon dialogue",
        }
        assert count_distinct_queries(matrix, near_dup_threshold=0.7) == 2

    def test_validate_matrix_band_flags_too_narrow(self) -> None:
        matrix = {f"q{i}": f"unique distinct query terms {i} alpha beta gamma" for i in range(5)}
        ok, msg = validate_matrix_band(matrix)
        assert ok is False
        assert "5" in msg

    def test_validate_matrix_band_passes_in_range(self) -> None:
        matrix = {f"q{i}": f"unique distinct query terms number {i} zulu yankee xray" for i in range(55)}
        ok, msg = validate_matrix_band(matrix)
        assert ok is True
        assert MATRIX_BAND_LO <= 55 <= MATRIX_BAND_HI

    def test_validate_matrix_band_flags_over_cap(self) -> None:
        matrix = {f"q{i}": f"unique distinct query terms number {i} whiskey tango foxtrot" for i in range(120)}
        ok, msg = validate_matrix_band(matrix)
        assert ok is False
        assert "120" in msg


# ---------------------------------------------------------------------------
# Budget raise (D-1) — HR's diminishing-returns range extends to ~100 planned
# searches; the old 65-cap under-provisions the new facet-matrix breadth.
# ---------------------------------------------------------------------------

def test_default_fetch_budget_raised_past_65() -> None:
    assert DEFAULT_FETCH_BUDGET > 65


# ---------------------------------------------------------------------------
# Pinned-decoding / stable-hash proof (acceptance criterion 1): same RQ -> same
# matrix -> same canonical criteria hash, run twice.
# ---------------------------------------------------------------------------

def test_canonicalize_criteria_hash_is_stable_across_repeated_calls() -> None:
    from research_vault.review.corpus_freeze import canonicalize_criteria

    a = canonicalize_criteria(NESTED_PROTOCOL)
    b = canonicalize_criteria(NESTED_PROTOCOL)
    assert a == b


def test_canonicalize_criteria_hash_changes_only_on_declared_amendment() -> None:
    from research_vault.review.corpus_freeze import canonicalize_criteria

    baseline = canonicalize_criteria(NESTED_PROTOCOL)
    amended = NESTED_PROTOCOL.replace(
        "persona stability multi-turn LLM",
        "persona stability multi-turn LLM AMENDED",
    )
    assert canonicalize_criteria(amended) != baseline


def test_legacy_protocol_still_hashes_without_crashing() -> None:
    from research_vault.review.corpus_freeze import canonicalize_criteria

    canon = canonicalize_criteria(LEGACY_SCALAR_PROTOCOL)
    assert "by-method" in canon


# ---------------------------------------------------------------------------
# D-7 structural gate: a declared thesis facet with no frozen counter-side
# query BLOCKs approve-protocol. Mirrors check_protocol_gate's empty
# counter-position field block, one level more mechanical.
# ---------------------------------------------------------------------------

EMPTY_COUNTER_POLE_PROTOCOL = """---
type: review-protocol
question: "Does X improve Y?"
seed_queries:
  by-temporal:
    thesis:
      - "cultural drift multi-turn LLM persona"
counter-position: "stability sub-literature"
---
"""


class TestCheckCounterFacetGate:
    def test_empty_counter_pole_blocks(self, tmp_path) -> None:
        from research_vault.review import check_counter_facet_gate

        p = tmp_path / "_protocol.md"
        p.write_text(EMPTY_COUNTER_POLE_PROTOCOL, encoding="utf-8")
        ok, msg = check_counter_facet_gate(p)
        assert ok is False
        assert "by-temporal" in msg

    def test_full_thesis_counter_facet_passes(self, tmp_path) -> None:
        from research_vault.review import check_counter_facet_gate

        p = tmp_path / "_protocol.md"
        p.write_text(NESTED_PROTOCOL, encoding="utf-8")
        ok, msg = check_counter_facet_gate(p)
        assert ok is True

    def test_legacy_scalar_only_matrix_has_nothing_to_check(self, tmp_path) -> None:
        """A purely-legacy scalar matrix never declared a facet split at
        all — D-7 has nothing to gate there (forward-only requirement)."""
        from research_vault.review import check_counter_facet_gate

        p = tmp_path / "_protocol.md"
        p.write_text(LEGACY_SCALAR_PROTOCOL, encoding="utf-8")
        ok, msg = check_counter_facet_gate(p)
        assert ok is True

    def test_missing_file_blocks(self, tmp_path) -> None:
        from research_vault.review import check_counter_facet_gate

        ok, msg = check_counter_facet_gate(tmp_path / "nope" / "_protocol.md")
        assert ok is False
        assert "_protocol.md" in msg


# ---------------------------------------------------------------------------
# Architect fit-check finding: a `seed_queries:` block DECLARED but that
# parses to ZERO usable queries (malformed nesting/indentation) must never
# silently clear D-7 or D-6 — an empty facet-iteration loop looks identical
# to "nothing to check" unless explicitly guarded against.
# ---------------------------------------------------------------------------

# `by-temporal:` sits at indent 0 (mis-indented — should be nested under
# `seed_queries:`), so `parse_angle_matrix` sees `seed_queries:` end the
# block immediately and returns {} — a garbage/malformed block, not a
# legitimate "no facets" case.
MALFORMED_ZERO_FACET_PROTOCOL = """---
type: review-protocol
question: "Does X improve Y?"
seed_queries:
by-temporal:
  thesis:
    - "cultural drift multi-turn LLM persona"
counter-position: "stability sub-literature"
---
"""


class TestSeedQueriesDeclaredButUnparsed:
    def test_malformed_nested_block_is_declared_but_unparsed(self) -> None:
        from research_vault.sources.sweep import seed_queries_declared_but_unparsed

        assert seed_queries_declared_but_unparsed(MALFORMED_ZERO_FACET_PROTOCOL) is True

    def test_legacy_bare_list_is_declared_but_unparsed(self) -> None:
        """The pre-angle-matrix bare-list shape is ALSO declared-but-empty —
        it already hard-fails at `run_sweep_from_protocol` (ValueError); this
        check correctly flags it too rather than treating it as benign."""
        from research_vault.sources.sweep import seed_queries_declared_but_unparsed

        LEGACY_LIST = (
            '---\ntype: review-protocol\nseed_queries:\n  - "a"\n  - "b"\n---\n'
        )
        assert seed_queries_declared_but_unparsed(LEGACY_LIST) is True

    def test_absent_seed_queries_key_is_not_flagged(self) -> None:
        """A protocol with NO `seed_queries:` key at all is a DIFFERENT case
        (handled elsewhere, e.g. run_sweep_from_protocol's own ValueError) —
        this check must not fire on absence, only on declared-but-empty."""
        from research_vault.sources.sweep import seed_queries_declared_but_unparsed

        NO_SEED_QUERIES = '---\ntype: review-protocol\nquestion: "X?"\n---\n'
        assert seed_queries_declared_but_unparsed(NO_SEED_QUERIES) is False

    def test_well_formed_matrix_is_not_flagged(self) -> None:
        from research_vault.sources.sweep import seed_queries_declared_but_unparsed

        assert seed_queries_declared_but_unparsed(NESTED_PROTOCOL) is False
        assert seed_queries_declared_but_unparsed(LEGACY_SCALAR_PROTOCOL) is False


class TestZeroFacetsFailOpenBlock:
    def test_d7_gate_blocks_on_malformed_zero_facet_protocol(self, tmp_path) -> None:
        from research_vault.review import check_counter_facet_gate

        p = tmp_path / "_protocol.md"
        p.write_text(MALFORMED_ZERO_FACET_PROTOCOL, encoding="utf-8")
        ok, msg = check_counter_facet_gate(p)
        assert ok is False
        assert "ZERO usable queries" in msg

    def test_d6_guard_blocks_on_malformed_zero_facet_protocol_no_judge(self, monkeypatch) -> None:
        """The zero-facets BLOCK is judge-INDEPENDENT — it must fire even
        with no judge configured at all (the no-judge SIGNAL direction is
        untouched; this is a structural BLOCK that takes priority over it)."""
        from research_vault.review.counter_facet_guard import check_counter_facet_strength

        monkeypatch.delenv("RV_JUDGE_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = check_counter_facet_strength(MALFORMED_ZERO_FACET_PROTOCOL, judge_fn=None)
        assert result["ok"] is False
        assert result["canary_aborted"] is False
        assert result["not_run"] == []
        assert any("ZERO usable queries" in b for b in result["blocking"])

    def test_d6_guard_blocks_on_malformed_zero_facet_protocol_with_judge(self) -> None:
        """Also blocks with a judge configured — the malformed-input BLOCK
        fires BEFORE the canary/judging loop ever runs (nothing to judge is
        never reached; the earlier structural defect wins)."""
        from research_vault.review.counter_facet_guard import check_counter_facet_strength

        never_called = lambda prompt: (_ for _ in ()).throw(
            AssertionError("judge must not be invoked when input is malformed")
        )
        result = check_counter_facet_strength(MALFORMED_ZERO_FACET_PROTOCOL, judge_fn=never_called)
        assert result["ok"] is False
        assert result["canary_aborted"] is False

    def test_mutation_neutralize_zero_facet_check_lets_it_sail_through(self, monkeypatch) -> None:
        """Mutation test: with `seed_queries_declared_but_unparsed` neutralized
        to always return False, the malformed protocol now sails through
        BOTH gates (ok=True) — proving the fix above is load-bearing, not
        some other unrelated block."""
        import research_vault.sources.sweep as sweep_mod

        # review/__init__.py's check_counter_facet_gate does
        # `from ..sources.sweep import ... seed_queries_declared_but_unparsed`
        # INLINE inside the function body, so patching the sweep module
        # attribute re-binds it on every call (same pattern as the existing
        # check_protocol_gate mutation test in test_review_protocol_gate.py).
        monkeypatch.setattr(sweep_mod, "seed_queries_declared_but_unparsed", lambda text: False)

        from research_vault.review import check_counter_facet_gate
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "_protocol.md"
            p.write_text(MALFORMED_ZERO_FACET_PROTOCOL, encoding="utf-8")
            ok, msg = check_counter_facet_gate(p)
            assert ok is True, (
                "with the zero-facets check neutralized, the malformed "
                "protocol must sail through — confirms the real check is "
                "what blocks it"
            )


# ---------------------------------------------------------------------------
# D-7 wiring — real DAG path (cmd_approve), non-vacuous.
# ---------------------------------------------------------------------------

def _cfg_file(tmp_path: Path) -> Path:
    f = tmp_path / "research_vault.toml"
    f.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{tmp_path / "notes"}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
        '[approval]\nenforce = true\n'
        'token_fingerprint = "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"\n'
        'enforce_sig = ""\n',
        encoding="utf-8",
    )
    return f


def _review_manifest(run_id: str, protocol_path: Path) -> dict:
    return {
        "run_id": run_id,
        "name": "test review",
        "global_cap": 1,
        "nodes": [
            {
                "id": "review-scope",
                "type": "agent",
                "spec": "task://demo#scope",
                "produces": {"_protocol.md": str(protocol_path)},
                "needs": [],
            },
            {
                "id": "approve-protocol",
                "type": "human-go",
                "label": "Gate 1",
                "needs": [{"from": "review-scope", "edge": "afterok"}],
            },
        ],
    }


def _set_run_env(tmp_path: Path):
    cfg_file = _cfg_file(tmp_path)
    old = os.environ.get("RESEARCH_VAULT_CONFIG")
    os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)
    return old


def _restore_env(old):
    if old is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old


def _make_awaiting_run(tmp_path: Path, run_id: str, protocol_path: Path):
    from research_vault.dag.store import RunState, RunStore

    manifest = _review_manifest(run_id, protocol_path)
    manifest_path = tmp_path / f"{run_id}-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    store = RunStore(tmp_path / "state")
    rs = RunState(run_id=run_id, manifest_path=str(manifest_path))
    rs.init_nodes(manifest)
    rs.set_node_status("review-scope", "succeeded")
    rs.set_node_status("approve-protocol", "awaiting-go")
    store.create(rs)
    return store


class TestApproveProtocolD7Wiring:
    def test_empty_counter_pole_refuses_approval_no_state_mutation(self, tmp_path):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            protocol_path = tmp_path / "reviews" / "scope-a" / "_protocol.md"
            protocol_path.parent.mkdir(parents=True, exist_ok=True)
            protocol_path.write_text(EMPTY_COUNTER_POLE_PROTOCOL, encoding="utf-8")
            store = _make_awaiting_run(tmp_path, "review-d7-empty", protocol_path)

            args = argparse.Namespace(run_id="review-d7-empty", node_id="approve-protocol")
            rc = cmd_approve(args)

            assert rc != 0, "approve-protocol must refuse on an empty counter-facet"
            rs = store.load("review-d7-empty")
            assert rs.node_status("approve-protocol") == "awaiting-go"
        finally:
            _restore_env(old)

    def test_full_thesis_counter_facet_approves_cleanly(self, tmp_path):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            protocol_path = tmp_path / "reviews" / "scope-b" / "_protocol.md"
            protocol_path.parent.mkdir(parents=True, exist_ok=True)
            protocol_path.write_text(NESTED_PROTOCOL, encoding="utf-8")
            store = _make_awaiting_run(tmp_path, "review-d7-full", protocol_path)

            args = argparse.Namespace(run_id="review-d7-full", node_id="approve-protocol")
            rc = cmd_approve(args)

            assert rc == 0
            rs = store.load("review-d7-full")
            assert rs.node_status("approve-protocol") == "succeeded"
        finally:
            _restore_env(old)

    def test_reject_bypasses_the_d7_gate(self, tmp_path):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            protocol_path = tmp_path / "reviews" / "scope-c" / "_protocol.md"
            protocol_path.parent.mkdir(parents=True, exist_ok=True)
            protocol_path.write_text(EMPTY_COUNTER_POLE_PROTOCOL, encoding="utf-8")
            store = _make_awaiting_run(tmp_path, "review-d7-reject", protocol_path)

            args = argparse.Namespace(
                run_id="review-d7-reject", node_id="approve-protocol", reject=True
            )
            rc = cmd_approve(args)

            assert rc == 0
            rs = store.load("review-d7-reject")
            assert rs.node_status("approve-protocol") == "blocked"
        finally:
            _restore_env(old)

    def test_mutation_neutralize_d7_check_lets_empty_pole_sail_through(self, tmp_path, monkeypatch):
        """Mutation test: with check_counter_facet_gate neutralized to always
        pass, the empty-counter-pole case sails through — proving the
        refusal above is load-bearing on THIS gate, not some unrelated
        block (the same still-required check_protocol_gate counter-position
        field is non-empty in this fixture, so only the D-7 gate can be
        blocking)."""
        import research_vault.review as review_mod

        old = _set_run_env(tmp_path)
        try:
            monkeypatch.setattr(
                review_mod, "check_counter_facet_gate", lambda p: (True, "OK")
            )
            from research_vault.dag.verbs import cmd_approve

            protocol_path = tmp_path / "reviews" / "scope-d" / "_protocol.md"
            protocol_path.parent.mkdir(parents=True, exist_ok=True)
            protocol_path.write_text(EMPTY_COUNTER_POLE_PROTOCOL, encoding="utf-8")
            store = _make_awaiting_run(tmp_path, "review-d7-neutered", protocol_path)

            args = argparse.Namespace(run_id="review-d7-neutered", node_id="approve-protocol")
            rc = cmd_approve(args)

            assert rc == 0, (
                "with the D-7 gate neutralized, the empty-counter-pole case "
                "must sail through — confirms the real gate is what blocks it"
            )
            rs = store.load("review-d7-neutered")
            assert rs.node_status("approve-protocol") == "succeeded"
        finally:
            _restore_env(old)


# ---------------------------------------------------------------------------
# D-6: cold, rejects-only, canary-verified counter-facet strength guard.
# ---------------------------------------------------------------------------

def _mock_judge_substance_aware(prompt: str) -> str:
    """A faithful mock: classifies on the SAME substance signal a real judge
    would use — presence of a specific named mechanism/phenomenon (STRONG)
    vs. a bare negation with nothing behind it (STRAWMAN). Scoped to the
    queries block only (never the rubric's own instructional examples —
    the SR-MS-2 rubric-contamination lesson)."""
    import re as _re
    m = _re.search(r"=== COUNTER-FACET QUERIES ===\n(.*?)\n=== END ===", prompt, _re.DOTALL)
    queries_block = m.group(1) if m else ""
    strong_markers = (
        "backfire effect", "boomerang effect", "motivated reasoning",
        "persona stability", "value persistence", "persistence", "stability",
        "meta-analysis", "replication",
    )
    strawman_markers = ("does ", "not always", "sometimes unsuccessful", "ever fail", "not seem to")
    has_strong = any(m in queries_block.lower() for m in strong_markers)
    has_strawman_only = any(m in queries_block.lower() for m in strawman_markers) and not has_strong
    if has_strawman_only:
        return "[STRAWMAN] token negation, no named mechanism."
    if has_strong:
        return "[STRONG] names a specific refuting mechanism."
    return "[STRAWMAN] vague, nothing specific named."


STRAWMAN_COUNTER_PROTOCOL = """---
type: review-protocol
question: "Do LLM personas drift over multi-turn conversation?"
seed_queries:
  by-temporal:
    thesis:
      - "cultural drift multi-turn LLM persona"
    counter:
      - "does LLM persona drift not always happen"
sources: [semantic-scholar, arxiv]
counter-position: "stability"
---
"""

STRONG_COUNTER_PROTOCOL = """---
type: review-protocol
question: "Do LLM personas drift over multi-turn conversation?"
seed_queries:
  by-temporal:
    thesis:
      - "cultural drift multi-turn LLM persona"
    counter:
      - "persona stability multi-turn LLM value persistence"
sources: [semantic-scholar, arxiv]
counter-position: "stability"
---
"""


class TestCounterFacetStrengthGuard:
    def test_halt_declare_when_no_judge(self, monkeypatch) -> None:
        # PR-F unified HALT (deliverable #3): no judge is no longer the old
        # SIGNAL (ok=True) — it is a HALT-DECLARE (ok=False, halt=True). The
        # direct-API judge path was deleted; the guard runs via the emit/ingest
        # fan-out, and a relied-on cold gate that cannot run HALTs.
        from research_vault.review.counter_facet_guard import check_counter_facet_strength

        monkeypatch.delenv("RV_JUDGE_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = check_counter_facet_strength(STRONG_COUNTER_PROTOCOL, judge_fn=None)
        assert result["ok"] is False
        assert result["halt"] is True
        assert result["not_run"]
        assert "D-6" in result["not_run"][0]
        assert "HALT-DECLARE" in result["not_run"][0]

    def test_canary_passes_then_rejects_planted_strawman(self) -> None:
        from research_vault.review.counter_facet_guard import check_counter_facet_strength

        result = check_counter_facet_strength(
            STRAWMAN_COUNTER_PROTOCOL, judge_fn=_mock_judge_substance_aware,
        )
        assert result["canary_aborted"] is False
        assert result["ok"] is False
        assert result["blocking"]
        assert "by-temporal" in result["blocking"][0]

    def test_canary_passes_then_accepts_real_refuting_facet(self) -> None:
        from research_vault.review.counter_facet_guard import check_counter_facet_strength

        result = check_counter_facet_strength(
            STRONG_COUNTER_PROTOCOL, judge_fn=_mock_judge_substance_aware,
        )
        assert result["canary_aborted"] is False
        assert result["ok"] is True
        assert result["blocking"] == []

    def test_canary_aborts_loudly_when_judge_is_blind(self) -> None:
        """A judge that ALWAYS says [STRONG] (rubber-stamp) must fail the
        STRAWMAN canary probe and abort — never silently pass every real
        facet through."""
        from research_vault.review.counter_facet_guard import check_counter_facet_strength

        blind_judge = lambda prompt: "[STRONG] looks fine to me."
        result = check_counter_facet_strength(STRONG_COUNTER_PROTOCOL, judge_fn=blind_judge)
        assert result["canary_aborted"] is True
        assert result["ok"] is False

    def test_facet_with_empty_counter_is_skipped_not_judged(self) -> None:
        """D-7 already BLOCKs the empty-counter case; D-6 has nothing to
        judge there and must not crash or fabricate a verdict."""
        from research_vault.review.counter_facet_guard import check_counter_facet_strength

        result = check_counter_facet_strength(
            EMPTY_COUNTER_POLE_PROTOCOL, judge_fn=_mock_judge_substance_aware,
        )
        assert result["canary_aborted"] is False
        assert result["ok"] is True
        assert result["blocking"] == []

    def test_canary_probes_are_substance_only_distinguishable(self) -> None:
        """★ The canary bank's two probes must be from the SAME general
        domain (title/topic not distinguishing) — only substance (a named
        mechanism vs. a bare negation) tells them apart. Regression guard
        against a title-obvious canary silently degrading the guard to
        title-triage."""
        from research_vault.review.counter_facet_guard import _counter_facet_canary_bank

        bank = _counter_facet_canary_bank()
        assert len(bank) == 2
        strong_queries, strong_verdict = bank[0]
        strawman_queries, strawman_verdict = bank[1]
        assert strong_verdict == "STRONG"
        assert strawman_verdict == "STRAWMAN"
        # Same general domain signal: at least one shared substantive token
        # family (misinformation-correction) across both probes' text.
        strong_text = " ".join(strong_queries).lower()
        strawman_text = " ".join(strawman_queries).lower()
        assert "correct" in strong_text and "correct" in strawman_text
        # Neither probe is self-labeled.
        assert "strong" not in strong_text and "strawman" not in strong_text
        assert "strong" not in strawman_text and "strawman" not in strawman_text
