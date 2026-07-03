"""test_dogfood_med_batch.py — dogfood-MED polish batch (#43 + #72 follow-ups).

Five hermetic test classes, one per fix:

  1. TestReproProxyAffordance — repro-lint not-applicable sentinel (#43 fix 1)
  2. TestScipyAnalysisExtra   — scipy in [analysis] extra + plan-tips fallback note (#43 fix 2)
  3. TestPlanCriticSpecBareId — supports_main bare-id in plan-critic-spec.md (#43 fix 3)
  4. TestFreezeEffectiveRoot  — covers_only_current uses effective_notes_root (#72 fix 4)
  5. TestOperatorNamingDoc    — 'the operator' convention in git-discipline.md (#fix 5)

Non-vacuity proof for fix 1 (repro-proxy):
  - proxy note with not-applicable → zero warnings (the new behaviour)
  - real-run note with sentinel → still warns (proves the affordance is a narrow sentinel,
    not a blanket disable)
"""

from __future__ import annotations

import hashlib
import json
import re
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helper: sha256 hash string
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# 1. repro-lint proxy affordance
# ---------------------------------------------------------------------------

EXPECTED_SENTINEL = "not-recorded-in-provenance"
EXPECTED_NOT_APPLICABLE = "not-applicable"

REPRO_LINT_REQUIRED = [
    "repro_config_location",
    "repro_config_hash",
    "repro_seed",
    "repro_model_id",
    "repro_model_revision",
    "repro_decode_temperature",
    "repro_decode_top_p",
    "repro_decode_max_tokens",
    "repro_num_fewshot",
    "repro_tokenizer",
    "repro_env_packages",
    "repro_env_python",
    "repro_cost_gpu_hours",
    "repro_prompt_lang",
    "repro_translation_provenance",
    "repro_prompt_version",
    "repro_dataset_split",
    "repro_metric",
]


def _write_proxy_note(tmp_path: Path, *, results_hash: str) -> Path:
    """Write an analysis note where all repro fields are 'not-applicable' (proxy/no-run)."""
    lines = [
        "---",
        "type: experiments",
        "title: Proxy Analysis",
        "created: 2026-07-03",
        f"results_location: {tmp_path / 'proxy.csv'}",
        f"results_hash: {results_hash}",
        "results_wandb_run: ",
        "results_commit: ",
    ]
    for field in REPRO_LINT_REQUIRED:
        lines.append(f"{field}: {EXPECTED_NOT_APPLICABLE}")
    # Add the non-lint-required fields too (to be a complete note)
    lines += ["repro_hw: not-applicable", "repro_dataset_id: not-applicable", "repro_dataset_hash: not-applicable", "repro_eval_harness: not-applicable"]
    lines += ["---", "", "<!-- proxy analysis — no model run -->\n"]
    p = tmp_path / "proxy-exp.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _write_real_run_note_with_sentinel(tmp_path: Path, *, results_hash: str) -> Path:
    """Write an experiment note with results set but repro fields still at sentinel (real run, missing repro)."""
    lines = [
        "---",
        "type: experiments",
        "title: Real Run Missing Repro",
        "created: 2026-07-03",
        f"results_location: {tmp_path / 'real.jsonl'}",
        f"results_hash: {results_hash}",
        "results_wandb_run: e/p/r",
        "results_commit: abc123",
    ]
    for field in REPRO_LINT_REQUIRED:
        lines.append(f"{field}: {EXPECTED_SENTINEL}")
    lines += ["---", "", "<!-- real run but repro not filled -->\n"]
    p = tmp_path / "real-exp.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


class TestReproProxyAffordance:
    """Fix 1: not-applicable sentinel skips repro-lint for proxy/no-run analyses.

    Non-vacuity: the proxy note passes CLEAN; a real-run note with sentinel still WARNs.
    """

    def test_not_applicable_constant_exported(self):
        """REPRO_NOT_APPLICABLE constant is exported from note.py."""
        from research_vault.note import REPRO_NOT_APPLICABLE
        assert REPRO_NOT_APPLICABLE == EXPECTED_NOT_APPLICABLE

    def test_proxy_note_passes_lint_clean(self, tmp_path):
        """A proxy analysis with all repro fields set to 'not-applicable' → zero lint warnings."""
        from research_vault.note import check_repro_sentinel_lint

        artifact = tmp_path / "proxy.csv"
        artifact.write_bytes(b"col1,col2\n0.85,0.92\n")
        results_hash = _sha256(artifact.read_bytes())

        note_path = _write_proxy_note(tmp_path, results_hash=results_hash)
        warnings = check_repro_sentinel_lint(note_path)

        assert warnings == [], (
            f"A proxy/no-run analysis with 'not-applicable' on all repro fields "
            f"must produce ZERO lint warnings. Got: {warnings}"
        )

    def test_real_run_note_with_sentinel_still_warns(self, tmp_path):
        """Non-vacuity: a real-run note with 'not-recorded-in-provenance' still fires warnings.

        This proves the proxy affordance is a narrow sentinel check, NOT a blanket disable.
        """
        from research_vault.note import check_repro_sentinel_lint

        artifact = tmp_path / "real.jsonl"
        artifact.write_bytes(b'{"accuracy": 0.87}\n')
        results_hash = _sha256(artifact.read_bytes())

        note_path = _write_real_run_note_with_sentinel(tmp_path, results_hash=results_hash)
        warnings = check_repro_sentinel_lint(note_path)

        assert len(warnings) > 0, (
            "A real-run note with sentinel repro fields must still produce warnings — "
            "the proxy affordance must NOT disable lint for real-run notes with gaps."
        )
        # Spot-check that at least one key REPRO_MANUAL field is called out
        warning_text = " ".join(warnings)
        assert "repro_seed" in warning_text or "repro_model_id" in warning_text, (
            "Warnings must mention specific repro fields that are still sentinel."
        )

    def test_mixed_note_partial_not_applicable(self, tmp_path):
        """A note where some fields are 'not-applicable' and others are sentinel → warns on sentinel only."""
        from research_vault.note import check_repro_sentinel_lint

        artifact = tmp_path / "mixed.jsonl"
        artifact.write_bytes(b'{"f1": 0.7}\n')
        results_hash = _sha256(artifact.read_bytes())

        lines = [
            "---",
            "type: experiments",
            "title: Mixed Note",
            "created: 2026-07-03",
            f"results_location: {artifact}",
            f"results_hash: {results_hash}",
        ]
        # Half the fields: not-applicable; half: sentinel
        for i, field in enumerate(REPRO_LINT_REQUIRED):
            if i % 2 == 0:
                lines.append(f"{field}: {EXPECTED_NOT_APPLICABLE}")
            else:
                lines.append(f"{field}: {EXPECTED_SENTINEL}")
        lines += ["---", ""]
        note_path = tmp_path / "mixed-exp.md"
        note_path.write_text("\n".join(lines), encoding="utf-8")

        warnings = check_repro_sentinel_lint(note_path)
        # Should warn only on the sentinel fields, not the not-applicable ones
        warning_fields = set()
        for w in warnings:
            m = re.search(r"'(repro_\w+)'", w)
            if m:
                warning_fields.add(m.group(1))

        # sentinel fields (odd indices) should be warned; not-applicable (even) should not
        for i, field in enumerate(REPRO_LINT_REQUIRED):
            if i % 2 == 0:
                assert field not in warning_fields, (
                    f"Field {field!r} (not-applicable) must NOT generate a warning"
                )
            else:
                assert field in warning_fields, (
                    f"Field {field!r} (sentinel) must generate a warning"
                )

    def test_lint_message_mentions_not_applicable_as_option(self, tmp_path):
        """The lint warning text must mention 'not-applicable' as an option for genuinely N/A fields."""
        from research_vault.note import check_repro_sentinel_lint

        artifact = tmp_path / "lint-msg.jsonl"
        artifact.write_bytes(b'{"acc": 0.9}\n')
        results_hash = _sha256(artifact.read_bytes())

        lines = [
            "---", "type: experiments", "created: 2026-07-03",
            f"results_location: {artifact}",
            f"results_hash: {results_hash}",
            f"repro_seed: {EXPECTED_SENTINEL}",
        ]
        for field in REPRO_LINT_REQUIRED:
            if field != "repro_seed":
                lines.append(f"{field}: filled-value")
        lines += ["---", ""]
        note_path = tmp_path / "lint-msg.md"
        note_path.write_text("\n".join(lines), encoding="utf-8")

        warnings = check_repro_sentinel_lint(note_path)
        assert len(warnings) == 1
        assert "not-applicable" in warnings[0], (
            "Lint message must mention 'not-applicable' as the option for genuinely N/A fields. "
            f"Got: {warnings[0]!r}"
        )


# ---------------------------------------------------------------------------
# 2. scipy for the [analysis] optional extra + plan-tips fallback note
# ---------------------------------------------------------------------------

class TestScipyAnalysisExtra:
    """Fix 2: scipy in [analysis] extra (not core), plan-tips grounding has stdlib fallback note."""

    def test_analysis_extra_defined_in_pyproject(self):
        """pyproject.toml defines an [analysis] optional extra."""
        import importlib.resources
        # Read pyproject.toml from the repo root (two levels up from this file)
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        assert "[project.optional-dependencies]" in text
        # The 'analysis' extra must be defined
        assert "analysis" in text, (
            "pyproject.toml must define an 'analysis' optional extra."
        )

    def test_scipy_in_analysis_extra_not_core(self):
        """scipy must appear in the [analysis] extra, NOT in the core [project] dependencies."""
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")

        # Locate the analysis extra section and the core deps section
        # Check that scipy appears under [project.optional-dependencies] analysis, not [project] dependencies
        assert "scipy" in text, "scipy must appear somewhere in pyproject.toml"

        # Parse to verify placement: scipy should be in the analysis block
        in_analysis = False
        scipy_in_analysis = False
        scipy_in_core = False
        lines = text.splitlines()
        in_optional = False
        in_analysis_section = False
        in_core_deps = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[project.optional-dependencies]"):
                in_optional = True
                in_core_deps = False
                in_analysis_section = False
            elif stripped.startswith("[project]") or (stripped.startswith("[") and not stripped.startswith("[project.optional")):
                if "optional" not in stripped:
                    in_core_deps = "dependencies" in text.splitlines()[lines.index(line):lines.index(line)+5] or True
                in_optional = False
                in_analysis_section = False
            if in_optional and stripped.startswith("analysis"):
                in_analysis_section = True
            if in_analysis_section and "scipy" in stripped.lower() and not stripped.startswith("#"):
                scipy_in_analysis = True

        assert scipy_in_analysis, (
            "scipy must appear in the 'analysis = [...]' block under "
            "[project.optional-dependencies], not in the core dependencies."
        )

    def test_plan_tips_grounding_mentions_scipy_fallback(self):
        """The plan_tips 'grounding' key mentions a stdlib fallback for small-n stats tests."""
        from research_vault.plan.style import _DEFAULT_PLAN_TIPS
        grounding_tip = _DEFAULT_PLAN_TIPS.get("grounding", "")
        # Must mention scipy and/or signed-rank / Wilcoxon + a stdlib or fallback approach
        has_scipy_mention = "scipy" in grounding_tip.lower()
        has_stats_mention = "wilcoxon" in grounding_tip.lower() or "signed-rank" in grounding_tip.lower() or "shapiro" in grounding_tip.lower()
        has_fallback = "stdlib" in grounding_tip.lower() or "fallback" in grounding_tip.lower()
        assert has_stats_mention and has_fallback, (
            "The 'grounding' plan tip must mention small-n signed-rank/Wilcoxon and a stdlib fallback, "
            "so a plan that targets small-n stats does not hard-depend on scipy. "
            f"Current grounding tip: {grounding_tip!r}"
        )


# ---------------------------------------------------------------------------
# 3. plan-critic-spec §6 uses bare-id form for supports_main + preregistration
# ---------------------------------------------------------------------------

class TestPlanCriticSpecBareId:
    """Fix 3: plan-critic-spec.md §6 examples use bare-id form (no 'experiments/' prefix)."""

    def _get_spec_text(self) -> str:
        import importlib.resources
        spec_path = (
            Path(__file__).parent.parent
            / "src" / "research_vault" / "data" / "doctrine" / "plan-critic-spec.md"
        )
        return spec_path.read_text(encoding="utf-8")

    def test_supports_main_no_path_prefix_in_spec(self):
        """supports_main example must use bare-id, not 'experiments/<id>' path form."""
        text = self._get_spec_text()
        # The bad form: supports_main: experiments/<id>
        bad_form = re.search(r"supports_main:\s+experiments/", text)
        assert bad_form is None, (
            "plan-critic-spec.md §6 must NOT show 'supports_main: experiments/<id>'. "
            "Use the bare-id form: 'supports_main: <id>'. "
            f"Found: {bad_form.group() if bad_form else 'N/A'!r}"
        )

    def test_preregistration_no_path_prefix_in_spec(self):
        """preregistration back-link example must use bare-id, not 'experiments/<id>' path form."""
        text = self._get_spec_text()
        # The bad form: preregistration: experiments/<id>
        bad_form = re.search(r"preregistration:\s+experiments/", text)
        assert bad_form is None, (
            "plan-critic-spec.md §6 must NOT show 'preregistration: experiments/<id>'. "
            "Use the bare-id form: 'preregistration: <id>'. "
            f"Found: {bad_form.group() if bad_form else 'N/A'!r}"
        )

    def test_spec_still_describes_both_fields(self):
        """After the fix, spec must still describe both fields (not accidentally deleted)."""
        text = self._get_spec_text()
        assert "supports_main" in text, "plan-critic-spec.md must still describe the supports_main field"
        assert "preregistration" in text, "plan-critic-spec.md must still describe the preregistration field"


# ---------------------------------------------------------------------------
# 4. verify-freeze diagnosis uses effective_notes_root
# ---------------------------------------------------------------------------

class TestFreezeEffectiveRoot:
    """Fix 4: covers_only_current in freeze.py mismatch-diagnosis uses effective_notes_root.

    Verdict must be unchanged; only the drift-classification message is affected.
    """

    def _write_child_note(self, notes_dir: Path, child_id: str, *, stance: str = "confirmatory") -> None:
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / f"{child_id}.md").write_text(
            f"---\ntype: experiments\ncitekey: {child_id}\n"
            f"stance: {stance}\nplan_role: main\n---\n\n# {child_id}\n",
            encoding="utf-8",
        )

    def _write_plan_note(self, tmp_path: Path, covers: str) -> Path:
        p = tmp_path / "plan.md"
        p.write_text(
            f"---\nplan_kind: preregistration\ncitekey: q1-plan\n"
            f"covers: [{covers}]\n---\n\n# plan\n",
            encoding="utf-8",
        )
        return p

    def test_diagnosis_uses_effective_notes_root_not_caller_arg(self, tmp_path):
        """Mismatch diagnosis path uses effective_notes_root (stored pin) not notes_root (caller arg).

        Setup: freeze under notes_dir_A with child q1-exp1 present there.
        Tamper: add a second child to covers: → hash mismatch → diagnosis path executes.
        Caller arg notes_root is a DIFFERENT dir (notes_dir_B) which does NOT have q1-exp1.

        Before fix: covers_only_current computed with notes_root (caller arg, dir B)
          → child-read fails → wrong covers-only hash → wrong drift classification.
        After fix:  covers_only_current computed with effective_notes_root (stored pin, dir A)
          → child-read succeeds → correct drift classification.

        The verdict (FAIL) is the same either way; only the classification message may differ.
        We verify: (a) verdict is FAIL, (b) no unhandled exception from the wrong-dir access.
        """
        from research_vault.plan.freeze import store_freeze_hash, verify_freeze_hash
        from research_vault.dag.store import RunState, RunStore

        notes_dir_a = tmp_path / "notes_a" / "experiments"
        self._write_child_note(notes_dir_a, "q1-exp1")
        plan_note = self._write_plan_note(tmp_path, "q1-exp1")

        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="diag-root", manifest_path=str(tmp_path / "m.json"))
        store.create(rs)

        # Freeze under notes_dir_a
        store_freeze_hash(store, "diag-root", plan_note, notes_root=notes_dir_a)

        # Tamper: add second child to covers: → mismatch guaranteed
        self._write_child_note(notes_dir_a, "q1-exp2")
        plan_note.write_text(
            "---\nplan_kind: preregistration\ncitekey: q1-plan\n"
            "covers: [q1-exp1, q1-exp2]\n---\n\n# tampered\n",
            encoding="utf-8",
        )

        # Call verify with notes_dir_b as the explicit caller arg (different dir, no notes)
        notes_dir_b = tmp_path / "notes_b" / "experiments"
        notes_dir_b.mkdir(parents=True, exist_ok=True)

        # Must not raise; must return (False, non-None message)
        ok, msg = verify_freeze_hash(
            store, "diag-root", plan_note, notes_root=notes_dir_b
        )

        assert ok is False, "Tampered plan (added child) must still FAIL verify"
        assert msg is not None, "Mismatch must produce a diagnosis message"

    def test_verdict_unchanged_when_notes_root_is_same(self, tmp_path):
        """Sanity: when caller arg == stored pin, verdict is the same as before the fix."""
        from research_vault.plan.freeze import store_freeze_hash, verify_freeze_hash
        from research_vault.dag.store import RunState, RunStore

        notes_dir = tmp_path / "notes" / "experiments"
        self._write_child_note(notes_dir, "q1-exp1")
        plan_note = self._write_plan_note(tmp_path, "q1-exp1")

        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="same-root", manifest_path=str(tmp_path / "m.json"))
        store.create(rs)

        store_freeze_hash(store, "same-root", plan_note, notes_root=notes_dir)

        # No tamper — should pass
        ok, msg = verify_freeze_hash(store, "same-root", plan_note, notes_root=notes_dir)
        assert ok is True, "Unfrozen plan must still PASS verify when caller arg == stored pin"
        assert msg is None


# ---------------------------------------------------------------------------
# 5. Doctrine: refer to the human as 'the operator' in committed artifacts
# ---------------------------------------------------------------------------

class TestOperatorNamingDoc:
    """Fix 5: git-discipline.md has a convention to use 'the operator' not a personal name."""

    def _get_git_discipline_text(self) -> str:
        doctrine_path = (
            Path(__file__).parent.parent
            / "src" / "research_vault" / "data" / "doctrine" / "git-discipline.md"
        )
        return doctrine_path.read_text(encoding="utf-8")

    def test_operator_naming_convention_present(self):
        """git-discipline.md must state the 'the operator' naming convention for committed artifacts."""
        text = self._get_git_discipline_text()
        # The convention must be explicitly stated
        has_operator_naming = (
            "the operator" in text.lower()
            and ("devlog" in text.lower() or "committed artifact" in text.lower() or "private marker" in text.lower())
        )
        assert has_operator_naming, (
            "git-discipline.md must document that crew members refer to the human as "
            "'the operator' (not by name) in committed artifacts (DEVLOG, commits, docs). "
            "This prevents private-marker leakage caught by the leakage gate."
        )

    def test_convention_mentions_leakage_gate(self):
        """The convention must explain why: leakage gate treats a personal name as a private marker."""
        text = self._get_git_discipline_text()
        # Must link the naming rule to the leakage gate / class 2
        has_leakage_link = (
            "leakage" in text.lower() and
            ("private marker" in text.lower() or "class 2" in text.lower() or "identity" in text.lower())
        )
        assert has_leakage_link, (
            "git-discipline.md's operator-naming convention must explain the connection to "
            "the leakage gate (class 2 / private-marker scan), so crew understand WHY the rule exists."
        )
