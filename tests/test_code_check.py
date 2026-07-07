"""test_code_check.py — tests for `rv code check <project>` (PR-CC-5).

Hermetic (tmp_instance). Drives the repo-plane checks directly against a
project tree built under `demo-research`'s `source_dir` — the same flat
convention `rv project new` scaffolds.

Acceptance (design §8 PR-CC-5):
  - green on a fresh scaffold (no HARD violations, exit 0)
  - a planted absolute path trips CHECK-8a
  - a planted .ipynb in code/src trips CHECK-3b
  - a planted duplicated CSV trips CHECK-6a
  - WARN checks don't flip exit
  - the release subset (CHECK-8b/c) flips exit in --release mode
"""
from __future__ import annotations

from pathlib import Path

import pytest

from research_vault.config import load_config
from research_vault import code_check
from research_vault import scaffold
from tests.gitutil import invoke_cli


@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


@pytest.fixture
def repo_root(cfg) -> Path:
    """The demo-research repo root (flat convention: source_dir IS repo root)."""
    root = cfg.project_repo_root("demo-research")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _scaffold_fresh(root: Path) -> None:
    """Materialize the same tree `rv project new` would (code-conventions dirs
    + release stubs + the framework .gitignore) — a "fresh scaffold"."""
    scaffold.scaffold_project_dirs(root)
    scaffold.scaffold_release_stubs(root, slug="demo-research")
    (root / ".gitignore").write_text(scaffold.FRAMEWORK_GITIGNORE, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Fresh scaffold — green (no HARD violations)
# ---------------------------------------------------------------------------

class TestFreshScaffold:
    def test_fresh_scaffold_no_hard_violations(self, cfg, repo_root):
        _scaffold_fresh(repo_root)
        violations = code_check.cmd_check("demo-research", config=cfg)
        hard = [v for v in violations if not v.startswith(code_check._WARN_PREFIXES)]
        assert hard == [], f"fresh scaffold must have zero HARD violations, got: {hard}"

    def test_fresh_scaffold_cli_exit_zero(self, tmp_instance, repo_root, capsys):
        _scaffold_fresh(repo_root)
        rc = invoke_cli(["code", "check", "demo-research"])
        assert rc == 0, capsys.readouterr().out

    def test_fresh_scaffold_may_still_warn(self, cfg, repo_root):
        """A fresh scaffold has no lockfile + an unfilled LICENSE — WARN, not HARD."""
        _scaffold_fresh(repo_root)
        violations = code_check.cmd_check("demo-research", config=cfg)
        warn = [v for v in violations if v.startswith(code_check._WARN_PREFIXES)]
        assert any("env-pin" in v for v in warn)
        assert any("releasability" in v for v in warn)


# ---------------------------------------------------------------------------
# 2. Planted failures — each trips the right check
# ---------------------------------------------------------------------------

class TestPlantedFailures:
    def test_notebook_in_src_trips_check_3b(self, cfg, repo_root):
        _scaffold_fresh(repo_root)
        nb = repo_root / "code" / "src" / "explore.ipynb"
        nb.write_text("{}", encoding="utf-8")
        violations = code_check.cmd_check("demo-research", config=cfg)
        assert any("notebook in code/src/" in v and str(nb) in v for v in violations)
        # HARD — no WARN prefix
        hits = [v for v in violations if "notebook in code/src/" in v]
        assert hits and not hits[0].startswith(code_check._WARN_PREFIXES)

    def test_absolute_path_trips_check_8a(self, cfg, repo_root):
        _scaffold_fresh(repo_root)
        leaky = repo_root / "code" / "src" / "paths.py"
        leaky.write_text('DATA_DIR = "/Users/researcher/private/data"\n', encoding="utf-8")
        violations = code_check.cmd_check("demo-research", config=cfg)
        hits = [v for v in violations if "absolute-personal path" in v]
        assert hits, f"expected an absolute-path violation, got: {violations}"
        assert not hits[0].startswith(code_check._WARN_PREFIXES)

    def test_home_path_also_trips_check_8a(self, cfg, repo_root):
        _scaffold_fresh(repo_root)
        leaky = repo_root / "code" / "src" / "paths2.py"
        leaky.write_text('CACHE = "/home/alice/.cache/thing"\n', encoding="utf-8")
        violations = code_check.cmd_check("demo-research", config=cfg)
        assert any("absolute-personal path" in v for v in violations)

    def test_duplicated_csv_trips_check_6a(self, cfg, repo_root):
        _scaffold_fresh(repo_root)
        (repo_root / "data").mkdir(parents=True, exist_ok=True)
        (repo_root / "results" / "scores").mkdir(parents=True, exist_ok=True)
        content = "a,b,c\n1,2,3\n"
        (repo_root / "data" / "scores.csv").write_text(content, encoding="utf-8")
        (repo_root / "results" / "scores" / "scores.csv").write_text(content, encoding="utf-8")
        violations = code_check.cmd_check("demo-research", config=cfg)
        hits = [v for v in violations if "data/results duplication" in v]
        assert hits, f"expected a dup violation, got: {violations}"
        assert not hits[0].startswith(code_check._WARN_PREFIXES)

    def test_non_duplicate_files_do_not_trip_check_6a(self, cfg, repo_root):
        """Non-vacuity: two DIFFERENT files under data/ and results/ never false-positive."""
        _scaffold_fresh(repo_root)
        (repo_root / "data").mkdir(parents=True, exist_ok=True)
        (repo_root / "results" / "scores").mkdir(parents=True, exist_ok=True)
        (repo_root / "data" / "raw.csv").write_text("x,y\n1,2\n", encoding="utf-8")
        (repo_root / "results" / "scores" / "summary.csv").write_text("m,v\n3,4\n", encoding="utf-8")
        violations = code_check.cmd_check("demo-research", config=cfg)
        assert not any("data/results duplication" in v for v in violations)


# ---------------------------------------------------------------------------
# 3. WARN never flips exit; --release flips the CHECK-8b/c subset
# ---------------------------------------------------------------------------

class TestSeveritySplit:
    def test_warn_only_violations_exit_zero(self, tmp_instance, repo_root):
        """A scaffold with only WARN-class gaps (no lockfile, placeholder LICENSE
        with the *required CFF keys present*) exits 0."""
        _scaffold_fresh(repo_root)
        rc = invoke_cli(["code", "check", "demo-research"])
        assert rc == 0

    def test_release_flag_flips_exit_on_license_placeholder(self, tmp_instance, repo_root):
        _scaffold_fresh(repo_root)
        rc_local = invoke_cli(["code", "check", "demo-research"])
        rc_release = invoke_cli(["code", "check", "demo-research", "--release"])
        assert rc_local == 0, "local mode: LICENSE placeholder is WARN, not HARD"
        assert rc_release == 1, "release mode: LICENSE placeholder must be HARD"

    def test_release_flag_flips_exit_on_missing_citation_cff(self, cfg, repo_root):
        _scaffold_fresh(repo_root)
        (repo_root / "CITATION.cff").unlink()
        violations_local = code_check.cmd_check("demo-research", config=cfg, release=False)
        violations_release = code_check.cmd_check("demo-research", config=cfg, release=True)
        local_hits = [v for v in violations_local if "CITATION.cff missing" in v]
        release_hits = [v for v in violations_release if "CITATION.cff missing" in v]
        assert local_hits and local_hits[0].startswith(code_check._WARN_PREFIXES)
        assert release_hits and not release_hits[0].startswith(code_check._WARN_PREFIXES)

    def test_valid_license_no_violation_even_in_release(self, cfg, repo_root):
        _scaffold_fresh(repo_root)
        (repo_root / "LICENSE").write_text(
            "MIT License\n\nPermission is hereby granted, free of charge, to any person...\n",
            encoding="utf-8",
        )
        violations = code_check.cmd_check("demo-research", config=cfg, release=True)
        assert not any("LICENSE" in v for v in violations)


# ---------------------------------------------------------------------------
# 4. Individual check units
# ---------------------------------------------------------------------------

class TestCheckUnits:
    def test_env_pinned_uv_lock_present(self, repo_root):
        (repo_root / "uv.lock").write_text("", encoding="utf-8")
        assert code_check.check_env_pinned(repo_root) == []

    def test_env_pinned_environment_yml_unpinned(self, repo_root):
        (repo_root / "environment.yml").write_text(
            "dependencies:\n  - numpy\n  - pandas\n", encoding="utf-8"
        )
        violations = code_check.check_env_pinned(repo_root)
        assert violations and violations[0].startswith("[env-pin]")

    def test_env_pinned_environment_yml_pinned(self, repo_root):
        (repo_root / "environment.yml").write_text(
            "dependencies:\n  - numpy=1.26.0\n", encoding="utf-8"
        )
        assert code_check.check_env_pinned(repo_root) == []

    def test_runs_scores_policy_missing_gitignore(self, repo_root):
        violations = code_check.check_runs_scores_git_policy(repo_root)
        assert violations and violations[0].startswith("[repo-policy]")

    def test_runs_scores_policy_scores_wrongly_ignored(self, repo_root):
        (repo_root / ".gitignore").write_text("results/runs/*\nresults/scores/\n", encoding="utf-8")
        violations = code_check.check_runs_scores_git_policy(repo_root)
        assert any("ignores results/scores" in v for v in violations)

    def test_runs_scores_policy_clean(self, repo_root):
        (repo_root / ".gitignore").write_text(scaffold.FRAMEWORK_GITIGNORE, encoding="utf-8")
        assert code_check.check_runs_scores_git_policy(repo_root) == []

    def test_science_critical_marker_no_test_warns(self, repo_root):
        src = repo_root / "code" / "src"
        tests_dir = repo_root / "code" / "tests"
        src.mkdir(parents=True, exist_ok=True)
        tests_dir.mkdir(parents=True, exist_ok=True)
        (src / "estimator.py").write_text(
            "# science-critical\ndef compute_effect(x):\n    return x\n",
            encoding="utf-8",
        )
        violations = code_check.check_science_critical_tests(repo_root / "code")
        hits = [v for v in violations if "compute_effect" in v]
        assert hits and hits[0].startswith("[science-path]")

    def test_science_critical_marker_with_test_clean(self, repo_root):
        src = repo_root / "code" / "src"
        tests_dir = repo_root / "code" / "tests"
        src.mkdir(parents=True, exist_ok=True)
        tests_dir.mkdir(parents=True, exist_ok=True)
        (src / "estimator.py").write_text(
            "# science-critical\ndef compute_effect(x):\n    return x\n",
            encoding="utf-8",
        )
        (tests_dir / "test_estimator.py").write_text(
            "from estimator import compute_effect\n\ndef test_compute_effect():\n    assert compute_effect(1) == 1\n",
            encoding="utf-8",
        )
        violations = code_check.check_science_critical_tests(repo_root / "code")
        assert violations == []

    def test_citation_cff_missing_required_key(self, repo_root):
        (repo_root / "CITATION.cff").write_text(
            'cff-version: 1.2.0\nmessage: "cite me"\ntitle: "x"\n',  # no authors
            encoding="utf-8",
        )
        violations = code_check.check_citation_cff(repo_root)
        assert violations and "authors" in violations[0]

    def test_citation_cff_valid_stub_clean(self, repo_root):
        scaffold.scaffold_release_stubs(repo_root, slug="demo-research")
        assert code_check.check_citation_cff(repo_root) == []
