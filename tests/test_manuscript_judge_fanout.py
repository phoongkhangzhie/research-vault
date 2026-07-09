"""tests/test_manuscript_judge_fanout.py — NG-4: the cold-agent-judge
fan-out emit/ingest seam for support-matcher (design §1.9).

Support-matcher-ONLY — the cold-read self-containment critic that
originally shared this seam was removed (SIGNAL-only, non-actionable
under hands-off autonomy, redundant with the review board + RD-6;
the operator's call, see DEVLOG).

Covers:
  1. emit_support_tasks: batched task shape, canaries interleaved unmarked
     (no citekey-level tell — PR #180 BLOCK fix), deterministic re-emit.
  2. ingest_support_verdicts: id-join happy path (matches check_support_tally
     shape), fixed vocab (do NOT widen), rejects-only semantics.
  3. Fail-closed: a task present in tasks but missing from verdicts ->
     defaults to ABSENT, surfaced via missing_ids — never a silent pass.
  4. A verdicts file entirely missing/empty -> halt=True (§1.8 floor-gate
     NOT RUN, HALT-DECLARE disposition), never ok:True.
  5. A planted bad-canary verdict -> CanaryAbortError -> caller HALTs.
  6. Draft<->tasks binding (PR #180 Finding C): a citation added to the
     draft after emit -> ingest HALTs on the stale citation_set_hash.
  7. build_approve_payload wires the cold-fanout path when judge/*  files
     exist and no live judge is configured — existing not_run path is
     UNCHANGED (regression guard) when no judge/* dir exists at all.

All hermetic — no live LLM calls; verdicts files are hand-authored fixtures
standing in for a completed hub fan-out.
sr: NG-4
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from research_vault.gates.judge_seam import CanaryAbortError


def _make_ms_tree(tmp_path: Path) -> Path:
    tree_root = tmp_path / "manuscripts" / "ms-test"
    (tree_root / "sections").mkdir(parents=True, exist_ok=True)
    return tree_root


def _literature_note(notes_root: Path, citekey: str, *, fields: dict | None = None) -> Path:
    lit_dir = notes_root / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)
    ffields = {"type": "literature", "tldr": "This paper demonstrates X.", "findings": "Finding A: X is true."}
    if fields:
        ffields.update(fields)
    fm = "---\n" + "\n".join(f"{k}: {v}" for k, v in ffields.items()) + "\n---\n"
    path = lit_dir / f"{citekey}.md"
    path.write_text(fm, encoding="utf-8")
    return path


def _write_md_with_cites(tree_root: Path, n: int) -> None:
    lines = []
    for i in range(n):
        lines.append(f"This is claim number {i} about the topic. [[paper{i}]]")
    (tree_root / "sections" / "intro.md").write_text("\n\n".join(lines), encoding="utf-8")


# ===========================================================================
# emit_support_tasks
# ===========================================================================

class TestEmitSupportTasks:
    def test_emits_tasks_schema_and_batches(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import emit_support_tasks

        tree_root = _make_ms_tree(tmp_path)
        notes_root = tmp_path
        _write_md_with_cites(tree_root, 5)
        for i in range(5):
            _literature_note(notes_root, f"paper{i}")

        result = emit_support_tasks(
            tree_root, notes_root=notes_root, manuscript_slug="ms-test", batch_size=2,
        )
        tasks_doc = result["tasks_doc"]
        assert tasks_doc["schema"] == "rv-judge-tasks/v1"
        assert tasks_doc["gate"] == "support-matcher"
        assert tasks_doc["manuscript"] == "ms-test"
        assert tasks_doc["judge_kind"] == "cold"
        assert "created" in tasks_doc

        # 5 real pairs + canaries (>= 2), batched into groups of <= 2.
        n_tasks = len(tasks_doc["tasks"])
        assert n_tasks >= 5
        for t in tasks_doc["tasks"]:
            assert set(t.keys()) >= {"id", "kind", "claim", "citekey", "source"}
            assert t["kind"] == "support"

        batches = tasks_doc["batches"]
        assert sum(len(b["task_ids"]) for b in batches) == n_tasks
        for b in batches:
            assert len(b["task_ids"]) <= 2

        # No task can be read to reveal it's a canary.
        for t in tasks_doc["tasks"]:
            assert not any("canary" in k.lower() for k in t.keys())

    def test_no_tell_value_level_serialized_doc(self, tmp_path):
        """PR #180 BLOCK regression: the original canary bank used
        self-labeling citekeys (``canary-known-supported`` etc.) — a
        dict-KEYS-only "no marker" check (see the test above) missed this
        entirely because the tell was in a VALUE, not a key. Assert over
        the FULL serialized ``_judge-tasks.json`` bytes: neither the word
        "canary" nor any fixed-vocab expected-verdict token may appear
        anywhere in the public tasks doc — a cold judge must not be able
        to ace the canaries (or infer real-task expectations) by reading
        the file, only by actually judging the claim/source pair.
        """
        import json

        from research_vault.manuscript.fidelity_gates import emit_support_tasks

        tree_root = _make_ms_tree(tmp_path)
        notes_root = tmp_path
        _write_md_with_cites(tree_root, 5)
        for i in range(5):
            _literature_note(notes_root, f"paper{i}")

        result = emit_support_tasks(
            tree_root, notes_root=notes_root, manuscript_slug="ms-test",
        )
        tasks_doc = result["tasks_doc"]
        serialized_full = json.dumps(tasks_doc).lower()
        assert "canary" not in serialized_full

        # No fixed-vocab verdict token may be derivable from any public
        # TASK field (a citekey like "canary-known-absent" leaks
        # "absent"). Scoped to the ``tasks`` list only — the ``rubric``
        # field LEGITIMATELY spells out the verdict vocabulary (that's
        # the judge's instructions, shared identically for every task,
        # not a tell about which specific task is a canary).
        serialized_tasks = json.dumps(tasks_doc["tasks"]).lower()
        for verdict in ("supports", "partial", "absent", "contradicts"):
            assert verdict not in serialized_tasks, (
                f"verdict token {verdict!r} is present somewhere in a "
                f"public task's fields — a cold judge could read it off "
                f"without actually judging the claim/source pair"
            )

    def test_canary_key_is_separate_and_private_shaped(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import emit_support_tasks

        tree_root = _make_ms_tree(tmp_path)
        notes_root = tmp_path
        _write_md_with_cites(tree_root, 3)
        for i in range(3):
            _literature_note(notes_root, f"paper{i}")

        result = emit_support_tasks(tree_root, notes_root=notes_root, manuscript_slug="ms-test")
        canary_key_doc = result["canary_key_doc"]
        assert canary_key_doc["schema"] == "rv-judge-canary-key/v1"
        assert len(canary_key_doc["canaries"]) >= 2
        task_ids = {t["id"] for t in result["tasks_doc"]["tasks"]}
        for cid, expected in canary_key_doc["canaries"].items():
            assert cid in task_ids
            assert expected in {"SUPPORTS", "PARTIAL", "ABSENT", "CONTRADICTS"}

    def test_no_cites_is_honest_noop(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import emit_support_tasks

        tree_root = _make_ms_tree(tmp_path)
        result = emit_support_tasks(tree_root, notes_root=tmp_path, manuscript_slug="ms-test")
        assert result["tasks_doc"]["tasks"] == []
        assert result["canary_key_doc"]["canaries"] == {}

    def test_deterministic_across_calls(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import emit_support_tasks

        tree_root = _make_ms_tree(tmp_path)
        notes_root = tmp_path
        _write_md_with_cites(tree_root, 4)
        for i in range(4):
            _literature_note(notes_root, f"paper{i}")

        r1 = emit_support_tasks(tree_root, notes_root=notes_root, manuscript_slug="ms-test")
        r2 = emit_support_tasks(tree_root, notes_root=notes_root, manuscript_slug="ms-test")
        # created timestamps may legitimately differ; strip before compare.
        t1 = dict(r1["tasks_doc"]); t1.pop("created")
        t2 = dict(r2["tasks_doc"]); t2.pop("created")
        assert t1 == t2
        assert r1["canary_key_doc"] == r2["canary_key_doc"]


# ===========================================================================
# ingest_support_verdicts
# ===========================================================================

class TestIngestSupportVerdicts:
    def _emit(self, tmp_path, n=3):
        from research_vault.manuscript.fidelity_gates import emit_support_tasks

        tree_root = _make_ms_tree(tmp_path)
        notes_root = tmp_path
        _write_md_with_cites(tree_root, n)
        for i in range(n):
            _literature_note(notes_root, f"paper{i}")
        return emit_support_tasks(tree_root, notes_root=notes_root, manuscript_slug="ms-test")

    def test_happy_path_all_supports_zero_block(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import ingest_support_verdicts

        emitted = self._emit(tmp_path, n=3)
        tasks_doc = emitted["tasks_doc"]
        canary_key_doc = emitted["canary_key_doc"]

        verdicts = []
        for t in tasks_doc["tasks"]:
            expected = canary_key_doc["canaries"].get(t["id"])
            verdicts.append({"id": t["id"], "verdict": expected or "SUPPORTS"})
        verdicts_doc = {"schema": "rv-judge-verdicts/v1", "gate": "support-matcher",
                         "manuscript": "ms-test", "verdicts": verdicts}

        result = ingest_support_verdicts(tasks_doc, canary_key_doc, verdicts_doc)
        assert result["halt"] is False
        assert result["canary_aborted"] is False
        assert result["errors"] == []
        assert result["missing_ids"] == []
        assert re.match(r"\d+ sentences, \d+ citations, \d+ BLOCK, \d+ WARN", result["honest_report"])

    def test_id_join_not_prompt_text(self, tmp_path):
        """Verdicts are matched purely by id — a verdict doc that carries no
        prompt/claim text at all (only ids) must still ingest correctly."""
        from research_vault.manuscript.fidelity_gates import ingest_support_verdicts

        emitted = self._emit(tmp_path, n=2)
        tasks_doc = emitted["tasks_doc"]
        canary_key_doc = emitted["canary_key_doc"]
        verdicts = [{"id": t["id"], "verdict": canary_key_doc["canaries"].get(t["id"], "ABSENT")}
                    for t in tasks_doc["tasks"]]
        verdicts_doc = {"verdicts": verdicts}
        result = ingest_support_verdicts(tasks_doc, canary_key_doc, verdicts_doc)
        assert result["canary_aborted"] is False
        assert result["halt"] is False

    def test_fixed_vocab_not_widened(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import ingest_support_verdicts

        emitted = self._emit(tmp_path, n=1)
        tasks_doc = emitted["tasks_doc"]
        canary_key_doc = emitted["canary_key_doc"]
        real_id = next(t["id"] for t in tasks_doc["tasks"] if t["id"] not in canary_key_doc["canaries"])
        verdicts = [{"id": t["id"], "verdict": canary_key_doc["canaries"].get(t["id"], "SUPPORTED")}
                    for t in tasks_doc["tasks"]]
        # "SUPPORTED" (not "SUPPORTS") is NOT in the fixed vocab -> fail-closed.
        verdicts_doc = {"verdicts": verdicts}
        result = ingest_support_verdicts(tasks_doc, canary_key_doc, verdicts_doc)
        assert real_id in result["unrecognized_ids"]
        # fail-closed default for support is ABSENT -> BLOCK.
        assert len(result["errors"]) >= 1

    def test_missing_real_task_defaults_to_absent_surfaced(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import ingest_support_verdicts

        emitted = self._emit(tmp_path, n=2)
        tasks_doc = emitted["tasks_doc"]
        canary_key_doc = emitted["canary_key_doc"]
        real_ids = [t["id"] for t in tasks_doc["tasks"] if t["id"] not in canary_key_doc["canaries"]]
        dropped = real_ids[0]

        verdicts = []
        for t in tasks_doc["tasks"]:
            if t["id"] == dropped:
                continue
            expected = canary_key_doc["canaries"].get(t["id"])
            verdicts.append({"id": t["id"], "verdict": expected or "SUPPORTS"})
        verdicts_doc = {"verdicts": verdicts}

        result = ingest_support_verdicts(tasks_doc, canary_key_doc, verdicts_doc)
        assert result["halt"] is False  # partial, not wholesale-missing
        assert dropped in result["missing_ids"]
        assert any(dropped in e or "ABSENT" in e for e in result["errors"]) or result["k_block"] >= 1

    def test_entirely_missing_verdicts_file_halts(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import ingest_support_verdicts

        emitted = self._emit(tmp_path, n=2)
        result = ingest_support_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"], None)
        assert result["halt"] is True
        assert result["ok"] is False if "ok" in result else True
        assert "halt_reason" in result and result["halt_reason"]

    def test_empty_verdicts_list_halts(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import ingest_support_verdicts

        emitted = self._emit(tmp_path, n=2)
        result = ingest_support_verdicts(
            emitted["tasks_doc"], emitted["canary_key_doc"], {"verdicts": []},
        )
        assert result["halt"] is True

    def test_bad_canary_verdict_raises_canary_abort_error(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import ingest_support_verdicts

        emitted = self._emit(tmp_path, n=2)
        tasks_doc = emitted["tasks_doc"]
        canary_key_doc = emitted["canary_key_doc"]
        bad_canary_id = next(iter(canary_key_doc["canaries"]))
        wrong_expected = canary_key_doc["canaries"][bad_canary_id]
        # Plant the OPPOSITE-of-expected verdict on the canary.
        planted = "CONTRADICTS" if wrong_expected != "CONTRADICTS" else "SUPPORTS"

        verdicts = []
        for t in tasks_doc["tasks"]:
            if t["id"] == bad_canary_id:
                verdicts.append({"id": t["id"], "verdict": planted})
            else:
                expected = canary_key_doc["canaries"].get(t["id"])
                verdicts.append({"id": t["id"], "verdict": expected or "SUPPORTS"})
        verdicts_doc = {"verdicts": verdicts}

        with pytest.raises(CanaryAbortError):
            ingest_support_verdicts(tasks_doc, canary_key_doc, verdicts_doc)

    def test_zero_tasks_is_honest_noop(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import emit_support_tasks, ingest_support_verdicts

        tree_root = _make_ms_tree(tmp_path)
        emitted = emit_support_tasks(tree_root, notes_root=tmp_path, manuscript_slug="ms-test")
        result = ingest_support_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"], None)
        assert result["halt"] is False
        assert result["k_block"] == 0


class TestDraftTasksBinding:
    """PR #180 Finding C: ``ingest`` trusted ``_judge-tasks.json`` as the
    citation universe without checking it still matches the CURRENT draft
    — a citation added to the draft AFTER emit was never judged and
    ``ingest`` reported ok (a silent floor-skip under hands-off autonomy).

    Fix: emit stamps a citation-set hash into ``tasks_doc``; ingest
    recomputes it from the live draft and HALTs on mismatch (fail-closed
    — stale tasks are exactly as untrustworthy as missing verdicts).
    """

    def test_emit_stamps_citation_set_hash(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import emit_support_tasks

        tree_root = _make_ms_tree(tmp_path)
        notes_root = tmp_path
        _write_md_with_cites(tree_root, 3)
        for i in range(3):
            _literature_note(notes_root, f"paper{i}")

        result = emit_support_tasks(tree_root, notes_root=notes_root, manuscript_slug="ms-test")
        assert "citation_set_hash" in result["tasks_doc"]
        assert result["tasks_doc"]["citation_set_hash"]

    def test_stale_tasks_halts_when_draft_gains_a_citation(self, tmp_path):
        """A citation added to the draft AFTER emit must HALT ingest, not
        silently pass judgment on a stale citation universe."""
        from research_vault.gates.judge_seam import write_json
        from research_vault.manuscript.fidelity_gates import (
            emit_support_tasks_to_dir,
            ingest_support_verdicts_from_dir,
        )

        tree_root = _make_ms_tree(tmp_path)
        notes_root = tmp_path
        _write_md_with_cites(tree_root, 2)
        for i in range(2):
            _literature_note(notes_root, f"paper{i}")

        judge_dir = tree_root / "judge" / "support-matcher"
        emitted = emit_support_tasks_to_dir(
            judge_dir, tree_root, notes_root=notes_root, manuscript_slug="ms-test",
        )

        # Fully answer every emitted task correctly, including canaries —
        # a well-behaved fan-out for the tasks it WAS given.
        tasks_doc = emitted["tasks_doc"]
        canary_key_doc = emitted["canary_key_doc"]
        verdicts = [
            {"id": t["id"], "verdict": canary_key_doc["canaries"].get(t["id"], "SUPPORTS")}
            for t in tasks_doc["tasks"]
        ]
        write_json(judge_dir / "_judge-verdicts.json", {"verdicts": verdicts})

        # Now the draft gains a NEW citation after the tasks were emitted
        # (and after the fan-out already ran over the old set).
        _write_md_with_cites(tree_root, 3)
        _literature_note(notes_root, "paper2")

        result = ingest_support_verdicts_from_dir(judge_dir)
        assert result["halt"] is True
        assert "stale" in result["halt_reason"].lower() or "mismatch" in result["halt_reason"].lower()
        assert not result.get("canary_aborted")

    def test_unchanged_draft_does_not_halt(self, tmp_path):
        """The regression guard for the fix above: an UNCHANGED draft must
        NOT spuriously halt — the hash check only fires on a real drift."""
        from research_vault.gates.judge_seam import write_json
        from research_vault.manuscript.fidelity_gates import (
            emit_support_tasks_to_dir,
            ingest_support_verdicts_from_dir,
        )

        tree_root = _make_ms_tree(tmp_path)
        notes_root = tmp_path
        _write_md_with_cites(tree_root, 2)
        for i in range(2):
            _literature_note(notes_root, f"paper{i}")

        judge_dir = tree_root / "judge" / "support-matcher"
        emitted = emit_support_tasks_to_dir(
            judge_dir, tree_root, notes_root=notes_root, manuscript_slug="ms-test",
        )
        tasks_doc = emitted["tasks_doc"]
        canary_key_doc = emitted["canary_key_doc"]
        verdicts = [
            {"id": t["id"], "verdict": canary_key_doc["canaries"].get(t["id"], "SUPPORTS")}
            for t in tasks_doc["tasks"]
        ]
        write_json(judge_dir / "_judge-verdicts.json", {"verdicts": verdicts})

        result = ingest_support_verdicts_from_dir(judge_dir)
        assert result["halt"] is False
        assert result["k_block"] == 0


# ===========================================================================
# build_approve_payload wiring — cold-fanout path + regression guard
# ===========================================================================

class TestBuildApprovePayloadColdFanout:
    def test_no_judge_dir_unchanged_not_run_path(self, tmp_path):
        """Regression guard: with no judge/ directory present at all (the
        status quo before this PR), the payload must land in not_run exactly
        as before — no behavior change for existing callers."""
        from research_vault.manuscript.check_gates import build_approve_payload

        tree_root = _make_ms_tree(tmp_path)
        project_notes_dir = tmp_path

        class _FakeType:
            key = "lit-review"
            equation_sources = ()

        payload = build_approve_payload(tree_root, project_notes_dir, _FakeType())
        assert any("NOT RUN" in n or "not_run" in n.lower() or "support-matcher" in n
                    for n in payload["not_run"])

    def test_judge_dir_present_drives_cold_fanout_path(self, tmp_path):
        from research_vault.manuscript.check_gates import build_approve_payload
        from research_vault.manuscript.fidelity_gates import emit_support_tasks
        from research_vault.gates.judge_seam import write_json

        tree_root = _make_ms_tree(tmp_path)
        project_notes_dir = tmp_path
        _write_md_with_cites(tree_root, 2)
        for i in range(2):
            _literature_note(project_notes_dir, f"paper{i}")

        emitted = emit_support_tasks(tree_root, notes_root=project_notes_dir, manuscript_slug="ms-test")
        judge_dir = tree_root / "judge" / "support-matcher"
        write_json(judge_dir / "_judge-tasks.json", emitted["tasks_doc"])
        write_json(judge_dir / "_judge-canary-key.json", emitted["canary_key_doc"])

        verdicts = []
        for t in emitted["tasks_doc"]["tasks"]:
            expected = emitted["canary_key_doc"]["canaries"].get(t["id"])
            verdicts.append({"id": t["id"], "verdict": expected or "SUPPORTS"})
        write_json(judge_dir / "_judge-verdicts.json", {"verdicts": verdicts})

        class _FakeType:
            key = "lit-review"
            equation_sources = ()

        payload = build_approve_payload(tree_root, project_notes_dir, _FakeType())
        # Cold-fanout path was consulted — no more "NOT RUN" for support-matcher.
        assert not any("support-matcher" in n and "NOT RUN" in n for n in payload["not_run"])
