"""tests/test_lint_rules.py — hermetic tests for SR-LINT test-hygiene rules.

Two rules, both grounded in real bugs from this session:

1. Vacuous-assertion rule — flags ``assert True`` / ``assert … or True`` in
   test files.  A trailing ``or True`` makes any assertion unconditionally
   pass; this shipped SR-CP's headline auto-archive check unverified.

2. Unpinned-git-init rule — flags ``git init`` WITHOUT ``--initial-branch``
   in test files.  An unpinned initial branch passes locally (init.defaultBranch
   = main) but fails on master-default CI runners; this red-CI'd SR-CP.

Each test:
  - Creates a temp .py file with a planted bad (or clean) pattern.
  - Calls the rule function directly (hermetic, no subprocess, no config).
  - Asserts the finding count.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from research_vault.lint import (
    check_vacuous_assertions,
    check_unpinned_git_init,
)


# ---------------------------------------------------------------------------
# Rule 1 — Vacuous assertions
# ---------------------------------------------------------------------------


class TestVacuousAssertions:
    """check_vacuous_assertions flags assert True / or True in test files."""

    def test_assert_true_bare_is_flagged(self, tmp_path: Path) -> None:
        """A bare 'assert True' is unconditionally vacuous — must be flagged."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            "def test_something():\n"
            "    assert True\n"
        )
        findings = check_vacuous_assertions([f])
        assert len(findings) == 1, findings

    def test_assert_true_with_message_is_flagged(self, tmp_path: Path) -> None:
        """'assert True, \"msg\"' is still vacuous — flagged."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            'def test_something():\n'
            '    assert True, "this always passes"\n'
        )
        findings = check_vacuous_assertions([f])
        assert len(findings) == 1, findings

    def test_or_true_in_assertion_is_flagged(self, tmp_path: Path) -> None:
        """'assert result or True' short-circuits to True — flagged."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            "def test_something():\n"
            "    assert result or True\n"
        )
        findings = check_vacuous_assertions([f])
        assert len(findings) == 1, findings

    def test_complex_or_true_is_flagged(self, tmp_path: Path) -> None:
        """'assert (x and y) or True' is vacuously true — flagged."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            "def test_something():\n"
            "    assert (x and y) or True\n"
        )
        findings = check_vacuous_assertions([f])
        assert len(findings) == 1, findings

    def test_multiple_vacuous_lines_all_reported(self, tmp_path: Path) -> None:
        """Every vacuous line in a file is reported, not just the first."""
        f = tmp_path / "test_bar.py"
        f.write_text(
            "def test_a():\n"
            "    assert True\n"
            "\n"
            "def test_b():\n"
            "    assert result or True\n"
        )
        findings = check_vacuous_assertions([f])
        assert len(findings) == 2, findings

    def test_multiple_files_all_scanned(self, tmp_path: Path) -> None:
        """Findings are collected across all provided files."""
        f1 = tmp_path / "test_a.py"
        f1.write_text("def test_x():\n    assert True\n")
        f2 = tmp_path / "test_b.py"
        f2.write_text("def test_y():\n    assert result or True\n")
        findings = check_vacuous_assertions([f1, f2])
        assert len(findings) == 2, findings

    def test_clean_real_assertion_passes(self, tmp_path: Path) -> None:
        """A genuine assertion with a real condition is not flagged."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            "def test_something():\n"
            "    assert result == expected\n"
            "    assert x > 0\n"
            "    assert items, 'list must not be empty'\n"
        )
        findings = check_vacuous_assertions([f])
        assert len(findings) == 0, findings

    def test_assert_false_not_flagged(self, tmp_path: Path) -> None:
        """'assert False' is not vacuous (it always fails); not flagged."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            "def test_something():\n"
            "    assert False, 'unreachable'\n"
        )
        findings = check_vacuous_assertions([f])
        assert len(findings) == 0, findings

    def test_or_false_not_flagged(self, tmp_path: Path) -> None:
        """'assert x or False' is not vacuous — not flagged."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            "def test_something():\n"
            "    assert x or False\n"
        )
        findings = check_vacuous_assertions([f])
        assert len(findings) == 0, findings

    def test_empty_file_passes(self, tmp_path: Path) -> None:
        """An empty file has no findings."""
        f = tmp_path / "test_empty.py"
        f.write_text("")
        findings = check_vacuous_assertions([f])
        assert len(findings) == 0, findings

    def test_empty_file_list_passes(self) -> None:
        """An empty file list yields no findings."""
        findings = check_vacuous_assertions([])
        assert len(findings) == 0, findings

    def test_findings_include_file_and_lineno(self, tmp_path: Path) -> None:
        """Each finding is a (file_path, lineno, label, line) tuple."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            "# line 1\n"
            "def test_x():\n"
            "    assert True\n"  # line 3
        )
        findings = check_vacuous_assertions([f])
        assert len(findings) == 1
        fpath, lineno, label, line_text = findings[0]
        assert str(f) in fpath
        assert lineno == 3
        assert "True" in label or "assert True" in label


# ---------------------------------------------------------------------------
# Rule 2 — Unpinned git init
# ---------------------------------------------------------------------------


class TestUnpinnedGitInit:
    """check_unpinned_git_init flags git init without --initial-branch."""

    def test_bare_git_init_is_flagged(self, tmp_path: Path) -> None:
        """A bare ['git', 'init', str(repo)] is flagged — branch is unpinned."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            'subprocess.run(["git", "init", str(repo)], check=True)\n'
        )
        findings = check_unpinned_git_init([f])
        assert len(findings) == 1, findings

    def test_git_init_with_extra_args_but_no_branch_is_flagged(
        self, tmp_path: Path
    ) -> None:
        """git init with --quiet but no --initial-branch is still flagged."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            'subprocess.run(["git", "init", "--quiet", str(repo)], check=True)\n'
        )
        findings = check_unpinned_git_init([f])
        assert len(findings) == 1, findings

    def test_git_init_with_initial_branch_eq_form_passes(
        self, tmp_path: Path
    ) -> None:
        """['git', 'init', '--initial-branch=main', ...] is correct — not flagged."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            'subprocess.run(\n'
            '    ["git", "init", "--initial-branch=main", str(repo)],\n'
            '    check=True,\n'
            ')\n'
        )
        findings = check_unpinned_git_init([f])
        assert len(findings) == 0, findings

    def test_git_init_with_initial_branch_space_form_passes(
        self, tmp_path: Path
    ) -> None:
        """['git', 'init', '--initial-branch', 'main', ...] is correct — not flagged."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            'subprocess.run(\n'
            '    ["git", "init", "--initial-branch", branch, str(path)],\n'
            '    check=True,\n'
            ')\n'
        )
        findings = check_unpinned_git_init([f])
        assert len(findings) == 0, findings

    def test_git_commit_with_init_message_not_flagged(
        self, tmp_path: Path
    ) -> None:
        """git commit -m 'init' is NOT a git init call — must not be flagged."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            'subprocess.run(\n'
            '    ["git", "-C", str(repo), "commit", "-m", "init"],\n'
            '    check=True,\n'
            ')\n'
        )
        findings = check_unpinned_git_init([f])
        assert len(findings) == 0, findings

    def test_git_commit_chore_init_message_not_flagged(
        self, tmp_path: Path
    ) -> None:
        """git commit -m 'chore: init' is not flagged."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            'subprocess.run(\n'
            '    ["git", "-C", str(repo), "commit", "-m", "chore: init"],\n'
            '    check=True,\n'
            ')\n'
        )
        findings = check_unpinned_git_init([f])
        assert len(findings) == 0, findings

    def test_multiple_bad_inits_all_reported(self, tmp_path: Path) -> None:
        """Every unpinned git init line in a file is reported."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            'subprocess.run(["git", "init", str(repo1)], check=True)\n'
            'subprocess.run(["git", "init", str(repo2)], check=True)\n'
        )
        findings = check_unpinned_git_init([f])
        assert len(findings) == 2, findings

    def test_multiple_files_all_scanned(self, tmp_path: Path) -> None:
        """Findings are collected across all provided files."""
        f1 = tmp_path / "test_a.py"
        f1.write_text('subprocess.run(["git", "init", str(r1)], check=True)\n')
        f2 = tmp_path / "test_b.py"
        f2.write_text('subprocess.run(["git", "init", str(r2)], check=True)\n')
        findings = check_unpinned_git_init([f1, f2])
        assert len(findings) == 2, findings

    def test_clean_file_passes(self, tmp_path: Path) -> None:
        """A file with only pinned git inits produces no findings."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            'subprocess.run(\n'
            '    ["git", "init", "--initial-branch=main", str(repo)],\n'
            '    check=True,\n'
            ')\n'
        )
        findings = check_unpinned_git_init([f])
        assert len(findings) == 0, findings

    def test_empty_file_list_passes(self) -> None:
        """An empty file list yields no findings."""
        findings = check_unpinned_git_init([])
        assert len(findings) == 0, findings

    def test_findings_include_file_and_lineno(self, tmp_path: Path) -> None:
        """Each finding is a (file_path, lineno, line) tuple."""
        f = tmp_path / "test_foo.py"
        f.write_text(
            "# setup\n"
            'subprocess.run(["git", "init", str(repo)], check=True)\n'  # line 2
        )
        findings = check_unpinned_git_init([f])
        assert len(findings) == 1
        fpath, lineno, line_text = findings[0]
        assert str(f) in fpath
        assert lineno == 2


# ---------------------------------------------------------------------------
# Integration: rv lint cmd_lint picks up test-hygiene rules
# ---------------------------------------------------------------------------


class TestCmdLintIntegration:
    """cmd_lint calls both test-hygiene checks and non-zeroes on any finding."""

    def test_cmd_lint_fails_on_vacuous_assertion_in_tests(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cmd_lint exits 1 when a test file contains a vacuous assertion."""
        from research_vault.lint import cmd_lint
        from research_vault.config import Config

        # Point tests_dir at a tmp tests/ directory with a bad file
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_bad.py").write_text(
            "def test_x():\n    assert True\n"
        )

        # Patch the module-level _TESTS_DIR to our temp location
        import research_vault.lint as lint_mod
        monkeypatch.setattr(lint_mod, "_TESTS_DIR", tests_dir)

        cfg = _make_minimal_config(tmp_path)
        rc = cmd_lint(cfg)
        assert rc == 1

    def test_cmd_lint_fails_on_unpinned_git_init_in_tests(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cmd_lint exits 1 when a test file contains an unpinned git init."""
        from research_vault.lint import cmd_lint

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_bad.py").write_text(
            'subprocess.run(["git", "init", str(r)], check=True)\n'
        )

        import research_vault.lint as lint_mod
        monkeypatch.setattr(lint_mod, "_TESTS_DIR", tests_dir)

        cfg = _make_minimal_config(tmp_path)
        rc = cmd_lint(cfg)
        assert rc == 1

    def test_cmd_lint_passes_on_clean_tests(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cmd_lint exits 0 when test files are hygiene-clean."""
        from research_vault.lint import cmd_lint

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_good.py").write_text(
            "def test_x():\n"
            "    assert result == expected\n"
        )
        (tests_dir / "test_git.py").write_text(
            'subprocess.run(\n'
            '    ["git", "init", "--initial-branch=main", str(repo)],\n'
            '    check=True,\n'
            ')\n'
        )

        import research_vault.lint as lint_mod
        monkeypatch.setattr(lint_mod, "_TESTS_DIR", tests_dir)

        cfg = _make_minimal_config(tmp_path)
        rc = cmd_lint(cfg)
        assert rc == 0

    def test_cmd_lint_passes_when_tests_dir_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cmd_lint exits 0 (not error) when the tests directory doesn't exist."""
        from research_vault.lint import cmd_lint

        import research_vault.lint as lint_mod
        monkeypatch.setattr(lint_mod, "_TESTS_DIR", tmp_path / "no-tests-here")

        cfg = _make_minimal_config(tmp_path)
        rc = cmd_lint(cfg)
        assert rc == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_config(root: Path):
    """Return a minimal Config object pointing at root."""
    from research_vault.config import Config
    raw: dict = {
        "instance_root": str(root),
        "notes_root": str(root / "notes"),
        "state_dir": str(root / "state"),
        "agents_dir": str(root / ".agents"),
        "tasks_dir": str(root / "tasks"),
        "control_dir": str(root / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {},
    }
    return Config(raw)
