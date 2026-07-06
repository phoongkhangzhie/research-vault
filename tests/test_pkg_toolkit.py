"""test_pkg_toolkit.py — SR-PKG toolkit tier tests.

Three test classes:

A. TestBareImportGuard — the non-negotiable bare-import test.
   Simulates `pip install research-vault --no-deps` by hiding ALL Tier-1/2 toolkit
   modules from sys.modules and blocking import of them. Asserts that rv help,
   rv status, rv note, rv dag verbs run clean (no ImportError / AttributeError)
   with the toolkit entirely absent.

B. TestCheckTierMatrix — rv check tier coverage matrix.
   Asserts the Tier-1/2 probes are structured correctly (all _TIER1_PACKAGES present,
   result dict has tier1_missing/tier2_missing keys, bootstrap nudge appears when
   Tier-1 packages missing).

C. TestBootstrapVerb — rv bootstrap parser + logic.
   Asserts the verb is registered, parseable, and the core _run_bootstrap logic
   works (venv creation, pip install paths, --no-tier2 flag).

D. TestRegistryAndHelpCheck — bootstrap appears in _VERB_REGISTRY + rv help --check passes.
"""
from __future__ import annotations

import argparse
import importlib
import sys
import types
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# A. Bare-import guard
# ---------------------------------------------------------------------------

# Modules that MUST be absent for the bare-import test to be valid.
# These are the Tier-1 + Tier-2 package top-level import names.
_TOOLKIT_MODULES = frozenset({
    # Tier-1: core
    "anthropic", "litellm", "tiktoken", "sklearn",
    # Tier-1: analysis / data
    "datasets", "pandas", "numpy", "pyarrow", "scipy", "statsmodels",
    # Tier-1: eval
    "inspect_ai", "lm_eval", "evaluate", "sacrebleu", "rouge_score", "bert_score",
    # Tier-1: multilingual
    "sentencepiece", "sacremoses", "langdetect",
    # Tier-1: utils
    "tenacity", "tqdm", "orjson", "pydantic", "jinja2", "rich", "dotenv",
    # Tier-1: integrations
    "wandb", "weave", "pyzotero", "asta",
    # Extras: providers (opt-in, but still blocked to verify no eager import)
    "openai", "google", "google.genai", "mistralai", "cohere",
    # Extras: figures (opt-in)
    "matplotlib", "seaborn",
    # Tier-2
    "torch", "transformers", "accelerate", "huggingface_hub", "fasttext",
    "vllm", "sglang",
})


class _BlockingFinder:
    """A meta path finder that raises ImportError for any toolkit module.

    Simulates a --no-deps install: every toolkit package is absent.
    """

    def find_spec(self, fullname, path, target=None):
        # Only block top-level toolkit names (no sub-modules of research_vault)
        top = fullname.split(".")[0]
        if top in _TOOLKIT_MODULES or fullname in _TOOLKIT_MODULES:
            raise ImportError(
                f"[bare-import test] {fullname!r} is a toolkit package — "
                "blocked to simulate --no-deps install. "
                "This means a non-guarded toolkit import exists on the rv verb import path."
            )
        return None


@pytest.fixture()
def toolkit_absent():
    """Hide all Tier-1/2 toolkit modules from sys.modules and block their import.

    This fixture simulates `pip install research-vault --no-deps`.
    It is scoped to a test (not session) to avoid cross-test contamination.

    Full sys.modules snapshot + restore ensures that any research_vault.* modules
    deleted inside test bodies are correctly restored on teardown, preventing global
    state pollution (divergent module instances, stale config caches) in downstream
    tests that run after this fixture.
    """
    # Snapshot ALL of sys.modules before any mutation
    saved_all = dict(sys.modules)

    # Remove any already-imported toolkit modules (keeps research_vault.* — tests
    # delete those inside their bodies; teardown restores from the full snapshot)
    for name in list(sys.modules.keys()):
        top = name.split(".")[0]
        if top in _TOOLKIT_MODULES or name in _TOOLKIT_MODULES:
            del sys.modules[name]

    # Install the blocking finder at the FRONT of meta_path
    finder = _BlockingFinder()
    sys.meta_path.insert(0, finder)

    yield

    # Full restore: remove finder, then atomically restore the pre-test module state
    sys.meta_path.remove(finder)
    sys.modules.clear()
    sys.modules.update(saved_all)
    # Belt + suspenders: reset config cache in case a re-import left it populated
    try:
        from research_vault.config import reset_config_cache
        reset_config_cache()
    except Exception:
        pass


class TestBareImportGuard:
    """rv verbs must run clean with the entire toolkit absent.

    This is the non-negotiable bare-import contract from the spec:
    'the CLI + every consumer MUST import + dispatch with NONE of the toolkit
    present (pip install research-vault --no-deps)'.
    """

    def test_rv_help_runs_without_toolkit(self, toolkit_absent):
        """rv help completes without ImportError when toolkit is absent."""
        # Force re-import of cli after toolkit is blocked
        for name in list(sys.modules.keys()):
            if name.startswith("research_vault"):
                del sys.modules[name]

        from research_vault.cli import main
        rc = main(["help"])
        assert rc == 0, "rv help must exit 0 with toolkit absent"

    def test_rv_help_check_runs_without_toolkit(self, toolkit_absent):
        """rv help --check completes without ImportError when toolkit is absent."""
        for name in list(sys.modules.keys()):
            if name.startswith("research_vault"):
                del sys.modules[name]

        from research_vault.cli import main
        rc = main(["help", "--check"])
        # Either 0 or 1 is acceptable (may fail if bootstrap is unregistered),
        # but it must NOT raise an exception.
        assert rc in (0, 1), f"rv help --check returned unexpected code {rc}"

    def test_check_verb_module_imports_without_toolkit(self, toolkit_absent):
        """check.py can be imported when toolkit is absent (guarded imports only)."""
        for name in list(sys.modules.keys()):
            if name.startswith("research_vault"):
                del sys.modules[name]

        # Should not raise ImportError
        import research_vault.check as check_mod
        assert hasattr(check_mod, "run_preflight"), "run_preflight must be importable"

    def test_bootstrap_module_imports_without_toolkit(self, toolkit_absent):
        """bootstrap.py can be imported when toolkit is absent (guarded imports only)."""
        for name in list(sys.modules.keys()):
            if name.startswith("research_vault"):
                del sys.modules[name]

        import research_vault.bootstrap as boot_mod
        assert hasattr(boot_mod, "run"), "bootstrap.run must be importable"

    def test_dag_verbs_module_imports_without_toolkit(self, toolkit_absent):
        """dag/verbs.py can be imported when toolkit is absent."""
        for name in list(sys.modules.keys()):
            if name.startswith("research_vault"):
                del sys.modules[name]

        import research_vault.dag.verbs as dag_mod
        assert hasattr(dag_mod, "build_parser"), "dag build_parser must be importable"

    def test_note_module_imports_without_toolkit(self, toolkit_absent):
        """note.py can be imported when toolkit is absent."""
        for name in list(sys.modules.keys()):
            if name.startswith("research_vault"):
                del sys.modules[name]

        import research_vault.note as note_mod
        assert hasattr(note_mod, "OKF_TYPES"), "note.OKF_TYPES must be importable"

    def test_status_module_imports_without_toolkit(self, toolkit_absent):
        """status.py can be imported when toolkit is absent."""
        for name in list(sys.modules.keys()):
            if name.startswith("research_vault"):
                del sys.modules[name]

        import research_vault.status as status_mod
        assert hasattr(status_mod, "build_parser"), "status build_parser must be importable"

    def test_cli_module_imports_without_toolkit(self, toolkit_absent):
        """cli.py can be imported when toolkit is absent."""
        for name in list(sys.modules.keys()):
            if name.startswith("research_vault"):
                del sys.modules[name]

        import research_vault.cli as cli_mod
        assert hasattr(cli_mod, "main"), "cli.main must be importable"
        assert hasattr(cli_mod, "_VERB_REGISTRY"), "_VERB_REGISTRY must be importable"


# ---------------------------------------------------------------------------
# B. Check tier matrix
# ---------------------------------------------------------------------------

class TestCheckTierMatrix:
    """rv check extends to report Tier-1/2 coverage matrix."""

    def test_tier1_packages_registry_count(self):
        """_TIER1_PACKAGES has exactly 28 packages (SR-MODEL-SEAM: weave promoted to core)."""
        from research_vault.check import _TIER1_PACKAGES
        assert len(_TIER1_PACKAGES) == 28, (
            f"Expected exactly 28 Tier-1 core packages, got {len(_TIER1_PACKAGES)}"
        )

    def test_tier1_packages_registry_is_non_empty(self):
        """_TIER1_PACKAGES has all major groups (core, analysis, eval, etc.)."""
        from research_vault.check import _TIER1_PACKAGES
        groups = {entry[2] for entry in _TIER1_PACKAGES}
        for expected_group in ("core", "analysis", "eval", "multilingual", "integrations", "utils"):
            assert expected_group in groups, (
                f"Expected group {expected_group!r} in Tier-1 registry; got groups: {groups}"
            )

    def test_tier1_registry_includes_litellm(self):
        """litellm (the primary model seam) is in the Tier-1 registry."""
        from research_vault.check import _TIER1_PACKAGES
        pip_names = [p[0] for p in _TIER1_PACKAGES]
        assert "litellm" in pip_names, "litellm must be in Tier-1 registry"

    def test_tier1_registry_includes_scipy(self):
        """scipy (formerly [analysis] extra) is now in Tier-1 defaults."""
        from research_vault.check import _TIER1_PACKAGES
        pip_names = [p[0] for p in _TIER1_PACKAGES]
        assert "scipy" in pip_names, "scipy must be in Tier-1 (folded from [analysis])"

    def test_tier1_registry_excludes_provider_sdks(self):
        """openai/google-genai/mistralai/cohere are NOT in Tier-1 (not shipped)."""
        from research_vault.check import _TIER1_PACKAGES
        pip_names = [p[0] for p in _TIER1_PACKAGES]
        for pkg in ("openai", "google-genai", "google-generativeai", "mistralai", "cohere"):
            assert pkg not in pip_names, (
                f"{pkg!r} must NOT be in Tier-1 (per-provider SDKs are not shipped)"
            )

    def test_tier1_registry_excludes_figure_libs(self):
        """matplotlib/seaborn are NOT in Tier-1 (not shipped)."""
        from research_vault.check import _TIER1_PACKAGES
        pip_names = [p[0] for p in _TIER1_PACKAGES]
        for pkg in ("matplotlib", "seaborn"):
            assert pkg not in pip_names, (
                f"{pkg!r} must NOT be in Tier-1 (figure libs are not shipped)"
            )

    def test_tier1_registry_includes_keyring(self):
        """keyring is declared in Tier-1 (newly added — was an undeclared guarded import)."""
        from research_vault.check import _TIER1_PACKAGES
        pip_names = [p[0] for p in _TIER1_PACKAGES]
        assert "keyring" in pip_names, "keyring must be in Tier-1 integrations"

    def test_tier2_packages_registry_includes_gpu_stack(self):
        """_TIER2_PACKAGES covers the GPU-fragile stack."""
        from research_vault.check import _TIER2_PACKAGES
        pip_names = [p[0] for p in _TIER2_PACKAGES]
        for expected in ("torch", "transformers", "accelerate"):
            assert expected in pip_names, f"{expected} must be in Tier-2 registry"

    def test_run_preflight_returns_tier_keys(self):
        """run_preflight result dict has tier1_missing and tier2_missing keys."""
        from research_vault.check import run_preflight
        result = run_preflight()
        assert "tier1_missing" in result, "result must have tier1_missing key"
        assert "tier2_missing" in result, "result must have tier2_missing key"
        assert isinstance(result["tier1_missing"], list)
        assert isinstance(result["tier2_missing"], list)

    def test_report_contains_tier1_section(self):
        """rv check report includes a Tier-1 section."""
        from research_vault.check import run_preflight
        result = run_preflight()
        assert "Tier-1" in result["report"], "report must contain Tier-1 section"

    def test_report_contains_tier2_section(self):
        """rv check report includes a Tier-2 section."""
        from research_vault.check import run_preflight
        result = run_preflight()
        assert "Tier-2" in result["report"], "report must contain Tier-2 section"

    def test_bootstrap_nudge_present_when_tier1_missing(self):
        """When Tier-1 packages are missing, report nudges to run rv bootstrap."""
        from research_vault.check import run_preflight, _TIER1_PACKAGES

        # Patch _probe_import to report all Tier-1 as absent
        with patch("research_vault.check._probe_import", return_value=False):
            result = run_preflight()

        assert result["tier1_missing"], "tier1_missing should be non-empty"
        assert "rv bootstrap" in result["report"], (
            "report must suggest 'rv bootstrap' when Tier-1 packages are missing"
        )

    def test_no_bootstrap_nudge_when_tier1_present(self):
        """When Tier-1 packages are all present, no bootstrap nudge in the report."""
        from research_vault.check import run_preflight

        # Patch _probe_import to report all as present
        with patch("research_vault.check._probe_import", return_value=True):
            result = run_preflight()

        assert result["tier1_missing"] == [], "tier1_missing should be empty"
        # The bootstrap nudge should NOT appear
        assert "Run `rv bootstrap`" not in result["report"], (
            "report must NOT nudge rv bootstrap when all Tier-1 packages are present"
        )

    def test_tier2_missing_is_warn_not_fail(self):
        """Tier-2 missing packages appear as WARN, never as a blocking FAIL."""
        from research_vault.check import run_preflight

        # All imports fail (both Tier-1 and Tier-2 absent)
        with patch("research_vault.check._probe_import", return_value=False):
            result = run_preflight()

        # Tier-2 missing should be WARN, not required-FAIL
        assert "WARN" in result["report"] or "INFO" in result["report"], (
            "report must show WARN/INFO for Tier-2 missing packages, not FAIL"
        )
        # all_required_ok is governed ONLY by claude_cli + api_key, not toolkit
        # (it may be False due to missing Claude CLI / key, that's fine)

    def test_probe_import_returns_bool_never_raises(self):
        """_probe_import never raises — always returns bool."""
        from research_vault.check import _probe_import
        # Known-absent module
        result = _probe_import("__nonexistent_module_xyz_12345__")
        assert result is False

    def test_fmt_tier_section_groups_correctly(self):
        """_fmt_tier_section groups by group label and counts ok/total."""
        from research_vault.check import _fmt_tier_section
        sample = [
            ("pkg-a", "purpose A", "group1", True),
            ("pkg-b", "purpose B", "group1", False),
            ("pkg-c", "purpose C", "group2", True),
        ]
        lines, missing = _fmt_tier_section(sample, warn_missing=False)
        assert "group1" in "\n".join(lines)
        assert "group2" in "\n".join(lines)
        assert "pkg-b" in missing
        assert "pkg-a" not in missing
        assert "pkg-c" not in missing


# ---------------------------------------------------------------------------
# C. Bootstrap verb
# ---------------------------------------------------------------------------

class TestBootstrapVerb:
    """rv bootstrap: parser shape + core logic."""

    def test_bootstrap_parser_registers(self):
        """bootstrap verb has a registered module and when_to_use."""
        from research_vault.cli import _VERB_REGISTRY
        assert "bootstrap" in _VERB_REGISTRY, "bootstrap must be in _VERB_REGISTRY"
        entry = _VERB_REGISTRY["bootstrap"]
        assert entry.get("module") == "research_vault.bootstrap"
        assert entry.get("when_to_use"), "bootstrap must have a when_to_use"

    def test_bootstrap_build_parser_returns_parser(self):
        """bootstrap.build_parser returns a valid ArgumentParser."""
        from research_vault.bootstrap import build_parser
        p = build_parser()
        assert isinstance(p, argparse.ArgumentParser)

    def test_bootstrap_parser_accepts_venv_flag(self):
        """bootstrap parser accepts --venv DIR."""
        from research_vault.bootstrap import build_parser
        p = build_parser()
        args = p.parse_args(["--venv", "/tmp/myvenv"])
        assert args.venv == "/tmp/myvenv"

    def test_bootstrap_parser_accepts_no_tier2(self):
        """bootstrap parser accepts --no-tier2."""
        from research_vault.bootstrap import build_parser
        p = build_parser()
        args = p.parse_args(["--no-tier2"])
        assert args.no_tier2 is True

    def test_bootstrap_parser_accepts_serve_vllm(self):
        """bootstrap parser accepts --serve vllm."""
        from research_vault.bootstrap import build_parser
        p = build_parser()
        args = p.parse_args(["--serve", "vllm"])
        assert args.serve == "vllm"

    def test_bootstrap_parser_accepts_serve_sglang(self):
        """bootstrap parser accepts --serve sglang."""
        from research_vault.bootstrap import build_parser
        p = build_parser()
        args = p.parse_args(["--serve", "sglang"])
        assert args.serve == "sglang"

    def test_bootstrap_parser_verbose_flag(self):
        """bootstrap parser accepts --verbose."""
        from research_vault.bootstrap import build_parser
        p = build_parser()
        args = p.parse_args(["--verbose"])
        assert args.verbose is True

    def test_bootstrap_run_bootstrap_reports_venv_fail(self, tmp_path):
        """_run_bootstrap returns tier1_ok=False when venv creation fails."""
        from research_vault.bootstrap import _run_bootstrap

        # Patch subprocess.run to simulate venv creation failure
        def _fake_run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 1
            m.stdout = ""
            m.stderr = "venv creation error"
            return m

        with patch("research_vault.bootstrap.subprocess.run", side_effect=_fake_run):
            result = _run_bootstrap(tmp_path / "newvenv")

        assert result["tier1_ok"] is False
        assert "FAIL" in result["report"]

    def test_bootstrap_no_tier2_skips_tier2(self, tmp_path):
        """_run_bootstrap with tier2=False does not attempt tier2 install."""
        from research_vault.bootstrap import _run_bootstrap

        call_specs: list[str] = []

        def _fake_run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 0
            m.stdout = "Successfully installed"
            m.stderr = ""
            # Record the install spec
            if "pip" in str(cmd[0]):
                call_specs.append(cmd[2] if len(cmd) > 2 else "")
            return m

        venv_path = tmp_path / "venv"
        # Pre-create the venv dir so the creation step is skipped
        venv_path.mkdir()
        # Create fake pip binary
        bin_dir = venv_path / "bin"
        bin_dir.mkdir()
        fake_pip = bin_dir / "pip"
        fake_pip.write_text("#!/bin/sh\necho ok\n")
        fake_pip.chmod(0o755)

        with patch("research_vault.bootstrap.subprocess.run", side_effect=_fake_run):
            result = _run_bootstrap(venv_path, tier2=False)

        # Must not install the Tier-2 spec
        for spec in call_specs:
            assert "[local]" not in spec, (
                f"Tier-2 [local] spec was called despite tier2=False: {spec!r}"
            )

    def test_bootstrap_result_dict_has_expected_keys(self, tmp_path):
        """_run_bootstrap result dict has all required keys."""
        from research_vault.bootstrap import _run_bootstrap

        def _fake_run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 1
            m.stdout = ""
            m.stderr = "error"
            return m

        result = _run_bootstrap(tmp_path / "venv_test")
        expected_keys = {
            "tier1_ok",
            "tier2_ok", "serve_ok", "tier2_reason",
            "serve_reason", "venv_dir", "report",
        }
        assert expected_keys.issubset(result.keys()), (
            f"Missing keys: {expected_keys - result.keys()}"
        )

    def test_bootstrap_report_always_contains_result_line(self, tmp_path):
        """_run_bootstrap report always contains a 'Result:' line."""
        from research_vault.bootstrap import _run_bootstrap

        def _fake_run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 1
            m.stdout = ""
            m.stderr = "err"
            return m

        result = _run_bootstrap(tmp_path / "venv_result")
        assert "Result:" in result["report"], "report must contain a Result: line"

    def test_bootstrap_run_exit_code_1_on_tier1_fail(self, tmp_path):
        """run() returns exit code 1 when Tier-1 install fails."""
        from research_vault.bootstrap import run

        def _fake_run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 1
            m.stdout = ""
            m.stderr = "pip error"
            return m

        args = argparse.Namespace(
            venv=str(tmp_path / "venv_exit"),
            no_tier2=True,
            serve=None,
            verbose=False,
        )
        with patch("research_vault.bootstrap.subprocess.run", side_effect=_fake_run):
            rc = run(args)
        assert rc == 1, "run() must return 1 when Tier-1 fails"


# ---------------------------------------------------------------------------
# D. Registry + help check
# ---------------------------------------------------------------------------

class TestRegistryAndHelpCheck:
    """bootstrap verb appears in _VERB_REGISTRY with correct shape + help --check passes."""

    def test_bootstrap_in_verb_registry(self):
        """bootstrap is in _VERB_REGISTRY."""
        from research_vault.cli import _VERB_REGISTRY
        assert "bootstrap" in _VERB_REGISTRY

    def test_bootstrap_verb_has_sr_pkg_tag(self):
        """bootstrap entry has SR-PKG sr tag."""
        from research_vault.cli import _VERB_REGISTRY
        assert _VERB_REGISTRY["bootstrap"].get("sr") == "SR-PKG"

    def test_bootstrap_in_setup_phase(self):
        """bootstrap appears in the Setup help phase."""
        from research_vault.cli import _HELP_PHASE_MAP
        setup_verbs = next(
            (verbs for phase, verbs in _HELP_PHASE_MAP if phase == "Setup"), []
        )
        assert "bootstrap" in setup_verbs, "bootstrap must be in Setup phase of help map"

    def test_rv_help_check_passes(self):
        """rv help --check exits 0 — all verbs have when_to_use including bootstrap."""
        from research_vault.cli import main
        rc = main(["help", "--check"])
        assert rc == 0, (
            "rv help --check must pass after adding bootstrap — "
            "check that bootstrap has a non-empty when_to_use in _VERB_REGISTRY"
        )

    def test_check_verb_when_to_use_mentions_tier_matrix(self):
        """check verb when_to_use mentions Tier-1/Tier-2 (updated from SR-5)."""
        from research_vault.cli import _VERB_REGISTRY
        wtu = _VERB_REGISTRY["check"]["when_to_use"]
        assert "Tier-1" in wtu or "tier" in wtu.lower(), (
            "check when_to_use must mention Tier-1/Tier-2 (updated for SR-PKG)"
        )

    def test_pyproject_tier1_not_empty(self):
        """pyproject.toml [project].dependencies has exactly 28 Tier-1 core packages.

        SR-MODEL-SEAM: weave promoted from [observability] extra to core — count is 28.
        """
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        # Count only real package entries (skip comment-only lines — toml strips those)
        deps = data["project"]["dependencies"]
        assert len(deps) == 28, (
            f"Expected exactly 28 Tier-1 default dependencies (SR-MODEL-SEAM: weave core), got {len(deps)}"
        )

    def test_pyproject_has_local_extra(self):
        """pyproject.toml has [local] optional dependency extra."""
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        optional = data["project"].get("optional-dependencies", {})
        assert "local" in optional, "[local] extra must exist in pyproject.toml"
        assert "torch" in optional["local"][0], "torch must be first in [local] extra"

    def test_pyproject_has_serve_vllm_extra(self):
        """pyproject.toml has [serve-vllm] optional dependency extra."""
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        optional = data["project"].get("optional-dependencies", {})
        assert "serve-vllm" in optional, "[serve-vllm] extra must exist"

    def test_pyproject_has_serve_sglang_extra(self):
        """pyproject.toml has [serve-sglang] optional dependency extra."""
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        optional = data["project"].get("optional-dependencies", {})
        assert "serve-sglang" in optional, "[serve-sglang] extra must exist"

    def test_pyproject_no_analysis_extra(self):
        """pyproject.toml no longer has [analysis] extra (folded into Tier-1)."""
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        optional = data["project"].get("optional-dependencies", {})
        assert "analysis" not in optional, (
            "[analysis] extra must be removed — scipy is now a Tier-1 default dep"
        )

    def test_pyproject_no_observability_extra(self):
        """pyproject.toml must NOT have [observability] extra (weave promoted to core).

        SR-MODEL-SEAM: weave is now a CORE dependency (framework observability
        guarantee). The [observability] extra is removed. This test is the regression
        pin: prevents accidentally re-adding it and re-demoting weave to opt-in.
        """
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        optional = data["project"].get("optional-dependencies", {})
        assert "observability" not in optional, (
            "[observability] extra must be removed — weave is now a Tier-1 core dep"
        )

    def test_pyproject_weave_in_core_deps(self):
        """weave is in pyproject.toml core dependencies (SR-MODEL-SEAM framework guarantee).

        Weave was moved from [observability] extra to core deps so that observability
        ships by default. This test pins that — moving weave back to an extra would
        silently break the observability guarantee.
        """
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        deps = data["project"]["dependencies"]
        assert any("weave" in d for d in deps), (
            "weave must be in Tier-1 core dependencies (not an extra) — "
            "SR-MODEL-SEAM observability is a framework guarantee"
        )

    def test_tier1_registry_includes_weave(self):
        """_TIER1_PACKAGES includes weave (promoted to core in SR-MODEL-SEAM)."""
        from research_vault.check import _TIER1_PACKAGES
        pip_names = [entry[0] for entry in _TIER1_PACKAGES]
        assert "weave" in pip_names, (
            "weave must be in _TIER1_PACKAGES — it is now a core dep (SR-MODEL-SEAM)"
        )

    def test_keyring_declared(self):
        """keyring is in pyproject.toml Tier-1 dependencies (newly declared)."""
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        deps = data["project"]["dependencies"]
        assert any("keyring" in d for d in deps), (
            "keyring must be in Tier-1 default dependencies (was undeclared; now explicit)"
        )

    def test_no_provider_sdks_in_core(self):
        """openai/google-genai/mistralai/cohere are NOT in pyproject.toml core deps."""
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        deps = data["project"]["dependencies"]
        for sdk in ("openai", "google-generativeai", "google-genai", "mistralai", "cohere"):
            assert not any(sdk in d for d in deps), (
                f"{sdk!r} must NOT be in core Tier-1 deps (it belongs in [providers] extra)"
            )

    def test_no_figure_libs_in_core(self):
        """matplotlib/seaborn are NOT in pyproject.toml core deps (moved to [figures])."""
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        deps = data["project"]["dependencies"]
        for lib in ("matplotlib", "seaborn"):
            assert not any(lib in d for d in deps), (
                f"{lib!r} must NOT be in core Tier-1 deps (it belongs in [figures] extra)"
            )

    def test_no_google_generativeai_anywhere_in_pyproject(self):
        """google-generativeai must not appear anywhere in pyproject.toml (grep-zero)."""
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        content = pyproject_path.read_text(encoding="utf-8")
        assert "google-generativeai" not in content, (
            "google-generativeai must be purged from pyproject.toml"
        )

    def test_no_provider_sdks_shipped(self):
        """Per-provider SDKs must not appear ANYWHERE in pyproject.toml (not core, not extras).

        openai/google-genai/google-generativeai/mistralai/cohere are NOT shipped.
        The adopter installs them directly; litellm covers most providers without them.
        """
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        content = pyproject_path.read_text(encoding="utf-8")
        for sdk in ("openai", "google-genai", "google-generativeai", "mistralai", "cohere"):
            assert sdk not in content, (
                f"{sdk!r} must not appear ANYWHERE in pyproject.toml "
                "(per-provider SDKs are not shipped; adopter installs directly)"
            )

    def test_no_figure_libs_shipped(self):
        """Figure libs must not appear ANYWHERE in pyproject.toml (not core, not extras).

        matplotlib/seaborn are NOT shipped. The adopter installs them directly.
        """
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        content = pyproject_path.read_text(encoding="utf-8")
        for lib in ("matplotlib", "seaborn"):
            assert lib not in content, (
                f"{lib!r} must not appear ANYWHERE in pyproject.toml "
                "(figure libs are not shipped; adopter installs directly)"
            )

    def test_pyproject_no_providers_extra(self):
        """pyproject.toml must NOT have [providers] optional dependency extra."""
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        optional = data["project"].get("optional-dependencies", {})
        assert "providers" not in optional, (
            "[providers] extra must be removed from pyproject.toml"
        )

    def test_pyproject_no_figures_extra(self):
        """pyproject.toml must NOT have [figures] optional dependency extra."""
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        optional = data["project"].get("optional-dependencies", {})
        assert "figures" not in optional, (
            "[figures] extra must be removed from pyproject.toml"
        )

    def test_pyproject_no_all_extra(self):
        """pyproject.toml must NOT have [all] optional dependency extra."""
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        optional = data["project"].get("optional-dependencies", {})
        assert "all" not in optional, (
            "[all] extra must be removed from pyproject.toml"
        )

    def test_pyproject_tier1_contains_litellm(self):
        """pyproject.toml Tier-1 deps contains litellm."""
        import tomllib
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        deps = data["project"]["dependencies"]
        assert any("litellm" in d for d in deps), (
            "litellm must be in Tier-1 default dependencies"
        )

    def test_bootstrap_module_has_no_toolkit_top_level_import(self):
        """bootstrap.py has no toolkit imports at module top-level.

        This verifies the bare-import contract at the source level: all toolkit
        imports in bootstrap.py are inside functions (guarded), never at module level.
        """
        import ast
        bootstrap_path = (
            Path(__file__).parent.parent
            / "src" / "research_vault" / "bootstrap.py"
        )
        source = bootstrap_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Collect top-level import names
        top_level_imports: list[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_level_imports.append(node.module.split(".")[0])

        toolkit_top = [m for m in top_level_imports if m in _TOOLKIT_MODULES]
        assert toolkit_top == [], (
            f"bootstrap.py has toolkit imports at module top-level: {toolkit_top}\n"
            "All toolkit imports must be inside functions (guarded lazy imports)."
        )

    def test_check_module_has_no_toolkit_top_level_import(self):
        """check.py has no toolkit imports at module top-level."""
        import ast
        check_path = (
            Path(__file__).parent.parent
            / "src" / "research_vault" / "check.py"
        )
        source = check_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        top_level_imports: list[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_level_imports.append(node.module.split(".")[0])

        toolkit_top = [m for m in top_level_imports if m in _TOOLKIT_MODULES]
        assert toolkit_top == [], (
            f"check.py has toolkit imports at module top-level: {toolkit_top}"
        )
