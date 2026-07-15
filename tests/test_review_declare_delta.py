"""tests/test_review_declare_delta.py — Section F(a) of the lit-review
redesign: `rv review declare-delta`, a HUMAN-invoked convenience verb that
automates writing a DECLARED corpus-shrink deviation.

Design of record: docs/superpowers/specs/2026-07-12-rv-lit-review-search-primary-redesign-design.md
(Section F). (b) — an autonomous within-criteria curation-down — is
explicitly REJECTED there; this file covers (a) ONLY, plus a regression
guard (D2's structural invariant) proving (b) was never opened.

The freeze guard (`autonomy.check_undeclared_deviation`) already accepts a
DECLARED removal; only the AUTONOMOUS deviation kinds
(`within-criteria-append`, `within-facet-query-append`) assert
`removed == []`. `declare_delta` writes the SAME `criteria-change` kind a
human already could hand-author via `record_deviation` — it only automates
the keystrokes. No gate is loosened by this file's code under test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import autonomy as auto  # noqa: E402
from research_vault.review import corpus_freeze as cf  # noqa: E402


def _corpus_note(path: Path, citekeys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"| [NEW] | {ck} | title-{ck} |" for ck in citekeys)
    path.write_text(
        "| annotation | citekey | title |\n|---|---|---|\n" + rows + "\n",
        encoding="utf-8",
    )


def _protocol_note(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "question: does X generalize across Y?\n"
        "inclusion: RCTs only\n"
        "exclusion: non-English\n"
        "coverage_claim: all English papers 2015-2025 on X\n"
        "counter-position: a real counter-position\n"
        "sources: [semantic-scholar, arxiv]\n"
        "---\n\nProtocol.\n",
        encoding="utf-8",
    )


def _seed(tmp_path, citekeys):
    corpus = tmp_path / "_corpus.md"
    protocol = tmp_path / "_protocol.md"
    deviations = tmp_path / "_deviations.md"
    _corpus_note(corpus, citekeys)
    _protocol_note(protocol)
    meta: dict = {}
    cf.stamp_corpus_freeze(meta, corpus_path=corpus, protocol_path=protocol)
    return meta, corpus, protocol, deviations


# ===========================================================================
# (a) — the happy path: baseline - current gets declared
# ===========================================================================

class TestDeclareDelta:
    def test_declares_exactly_the_dropped_citekeys(self, tmp_path):
        meta, corpus, protocol, deviations = _seed(
            tmp_path, ["a2024", "b2024", "c2024"],
        )
        # Drop b2024 and c2024 from the corpus (a real human curation-down,
        # already reflected in _corpus.md before this verb runs).
        _corpus_note(corpus, ["a2024"])

        result = cf.declare_delta(
            meta, corpus_path=corpus, deviations_path=deviations,
            rationale="post-hoc dedup: b2024/c2024 were duplicate arXiv preprints",
        )

        assert result is not None
        assert result["removed"] == ["b2024", "c2024"]
        assert deviations.exists()
        text = deviations.read_text(encoding="utf-8")
        assert "**Kind:** criteria-change" in text
        assert "**Removed citekeys:** b2024, c2024" in text
        assert "post-hoc dedup" in text

    def test_declared_delta_satisfies_check_undeclared_deviation(self, tmp_path):
        """The acceptance pin: after declare_delta, the SAME guard the
        coverage-gate path uses no longer BLOCKs on this delta."""
        meta, corpus, protocol, deviations = _seed(
            tmp_path, ["a2024", "b2024", "c2024"],
        )
        baseline_citekeys = set(meta["corpus_freeze"]["corpus_citekeys"])
        _corpus_note(corpus, ["a2024"])
        current_citekeys = {"a2024"}

        # RED-before-GREEN proof: undeclared first.
        ok, _msg = auto.check_undeclared_deviation(
            baseline_citekeys, current_citekeys, deviations,
        )
        assert ok is False

        cf.declare_delta(
            meta, corpus_path=corpus, deviations_path=deviations,
            rationale="two duplicates removed after re-screen",
        )

        ok, msg = auto.check_undeclared_deviation(
            baseline_citekeys, current_citekeys, deviations,
        )
        assert ok is True, msg

    def test_empty_delta_writes_nothing(self, tmp_path):
        meta, corpus, protocol, deviations = _seed(tmp_path, ["a2024"])
        # No change to _corpus.md — baseline == current.
        result = cf.declare_delta(
            meta, corpus_path=corpus, deviations_path=deviations,
            rationale="should never be used",
        )
        assert result is None
        assert not deviations.exists()

    def test_requires_a_human_supplied_rationale(self, tmp_path):
        meta, corpus, protocol, deviations = _seed(tmp_path, ["a2024", "b2024"])
        _corpus_note(corpus, ["a2024"])
        with pytest.raises(ValueError, match="rationale"):
            cf.declare_delta(
                meta, corpus_path=corpus, deviations_path=deviations, rationale="",
            )
        with pytest.raises(ValueError, match="rationale"):
            cf.declare_delta(
                meta, corpus_path=corpus, deviations_path=deviations, rationale="   ",
            )
        # No deviation written on either rejected call.
        assert not deviations.exists()

    def test_blocked_absent_baseline(self, tmp_path):
        corpus = tmp_path / "_corpus.md"
        deviations = tmp_path / "_deviations.md"
        _corpus_note(corpus, ["a2024"])
        with pytest.raises(cf.RefreshBlocked, match="no corpus_freeze"):
            cf.declare_delta(
                {}, corpus_path=corpus, deviations_path=deviations,
                rationale="irrelevant — should never reach the write",
            )

    def test_does_not_mutate_the_baseline_or_call_refresh(self, tmp_path):
        """declare_delta only APPENDS to _deviations.md — it never bumps
        corpus_freeze itself (that stays `rv review refresh`'s job)."""
        meta, corpus, protocol, deviations = _seed(tmp_path, ["a2024", "b2024"])
        version_before = meta["corpus_freeze"]["version"]
        citekeys_before = list(meta["corpus_freeze"]["corpus_citekeys"])
        _corpus_note(corpus, ["a2024"])

        cf.declare_delta(
            meta, corpus_path=corpus, deviations_path=deviations,
            rationale="one dropped",
        )

        assert meta["corpus_freeze"]["version"] == version_before
        assert meta["corpus_freeze"]["corpus_citekeys"] == citekeys_before


# ===========================================================================
# D2 regression guard — the autonomous kinds STILL cannot self-author a
# removal. (b) was rejected; this proves the hole stays closed.
# ===========================================================================

class TestD2InvariantUntouched:
    @pytest.mark.parametrize(
        "kind", ["within-criteria-append", "within-facet-query-append"],
    )
    def test_autonomous_kinds_still_reject_nonempty_removed(self, tmp_path, kind):
        deviations = tmp_path / "_deviations.md"
        kwargs = dict(
            version=2, pre_criteria="p", post_criteria="p",
            removed=["sneaky2024"], added=[],
            rationale="an autonomous loop trying to smuggle a removal",
            kind=kind,
        )
        if kind == "within-facet-query-append":
            kwargs.update(
                facet_key="by-method.thesis",
                new_queries=["a new query"],
                pre_query_matrix_hash="sha256:aaa",
                post_query_matrix_hash="sha256:bbb",
            )
        with pytest.raises(ValueError, match="removed"):
            auto.record_deviation(deviations, **kwargs)
        # Nothing was written on the rejected call.
        assert not deviations.exists()

    def test_declare_delta_never_writes_an_autonomous_kind(self, tmp_path):
        """declare_delta hard-codes kind='criteria-change' — it cannot be
        made to write within-criteria-append/within-facet-query-append,
        which is the whole point (no new autonomous removal power)."""
        meta, corpus, protocol, deviations = _seed(tmp_path, ["a2024", "b2024"])
        _corpus_note(corpus, ["a2024"])
        cf.declare_delta(
            meta, corpus_path=corpus, deviations_path=deviations,
            rationale="one dropped",
        )
        text = deviations.read_text(encoding="utf-8")
        assert "**Kind:** criteria-change" in text
        assert "within-criteria-append" not in text
        assert "within-facet-query-append" not in text


# ===========================================================================
# CLI wiring
# ===========================================================================

class TestDeclareDeltaCliVerb:
    def test_declare_delta_verb_parses(self):
        from research_vault.review.verbs import build_parser

        p = build_parser()
        args = p.parse_args([
            "demo-research", "declare-delta", "scope-x", "--rationale", "dup removed",
        ])
        assert args.review_cmd == "declare-delta"
        assert args.scope == "scope-x"
        assert args.rationale == "dup removed"

    def test_declare_delta_requires_rationale_flag(self):
        from research_vault.review.verbs import build_parser

        p = build_parser()
        with pytest.raises(SystemExit):
            p.parse_args(["demo-research", "declare-delta", "scope-x"])

    def test_cmd_declare_delta_end_to_end(self, tmp_path, monkeypatch):
        """Exercises the module-level `cmd_declare_delta` (the function the
        CLI dispatch calls) directly against a fake run_state store —
        avoids re-driving the whole Phase-1 DAG (already covered for
        `cmd_refresh` in test_ng6a_refresh_remediation.py; this file's job
        is declare_delta's OWN behavior, not re-proving the DAG plumbing)."""
        from research_vault.review.corpus_freeze import cmd_declare_delta
        from research_vault.dag.store import RunStore, RunState

        review_dir = tmp_path / "reviews" / "scope-y"
        corpus = review_dir / "_corpus.md"
        protocol = review_dir / "_protocol.md"
        deviations = review_dir / "_deviations.md"
        _corpus_note(corpus, ["a2024", "b2024"])
        _protocol_note(protocol)

        meta: dict = {}
        cf.stamp_corpus_freeze(meta, corpus_path=corpus, protocol_path=protocol)

        run_id = "review-scope-y-phase1"
        run_state = RunState(run_id=run_id, manifest_path="unused", meta=meta)

        class _FakeStore:
            def __init__(self, rs):
                self._rs = rs

            def load(self, rid):
                assert rid == run_id
                return self._rs

        monkeypatch.setattr(RunStore, "from_config", lambda cfg: _FakeStore(run_state))

        import research_vault.review as review_pkg
        monkeypatch.setattr(
            review_pkg, "_review_artifact_dir",
            lambda project, scope_id, cfg: review_dir,
        )

        _corpus_note(corpus, ["a2024"])

        result = cmd_declare_delta(
            "demo-research", "scope-y", "manual dedup", config=object(),
        )
        assert result == {"removed": ["b2024"], "block": result["block"]}
        assert deviations.exists()
        assert "**Removed citekeys:** b2024" in deviations.read_text(encoding="utf-8")
