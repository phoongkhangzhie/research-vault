"""tests/test_lint_rules.py — hermetic tests for SR-LINT test-hygiene rules.

Three rules, all grounded in real bugs from this session:

1. Vacuous-assertion rule — flags ``assert True`` / ``assert … or True`` in
   test files.  A trailing ``or True`` makes any assertion unconditionally
   pass; this shipped SR-CP's headline auto-archive check unverified.

2. Unpinned-git-init rule — flags ``git init`` WITHOUT ``--initial-branch``
   in test files.  An unpinned initial branch passes locally (init.defaultBranch
   = main) but fails on master-default CI runners; this red-CI'd SR-CP.

3. Redefined-while-unused rule (F811) — flags a ``def``/``class`` name that is
   redefined in the same scope before the first definition is used.  A shadowed
   duplicate ``check_manuscript`` shipped through ``rv lint`` + CI in SR-MS-2
   and was caught only by the Architect during code review.

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
    check_redefined_while_unused,
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
# Rule 3 — Redefined-while-unused (F811)
# ---------------------------------------------------------------------------


class TestRedefinedWhileUnused:
    """check_redefined_while_unused flags F811 shadowed def/class in same scope."""

    def test_duplicate_function_at_module_level_is_flagged(
        self, tmp_path: Path
    ) -> None:
        """Two ``def foo`` at module level with no use between — must be flagged."""
        f = tmp_path / "bad.py"
        f.write_text(
            "def check_manuscript(path):\n"
            "    return True\n"
            "\n"
            "def check_manuscript(path):  # duplicate — shadows first\n"
            "    return False\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 1, findings
        _, lineno, name, prev_lineno, _ = findings[0]
        assert name == "check_manuscript"
        assert lineno == 4
        assert prev_lineno == 1

    def test_duplicate_class_at_module_level_is_flagged(
        self, tmp_path: Path
    ) -> None:
        """Two ``class Foo`` at module level — must be flagged."""
        f = tmp_path / "bad.py"
        f.write_text(
            "class Runner:\n"
            "    pass\n"
            "\n"
            "class Runner:  # duplicate\n"
            "    pass\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 1, findings
        assert findings[0][2] == "Runner"

    def test_duplicate_method_in_class_is_flagged(
        self, tmp_path: Path
    ) -> None:
        """Two ``def run`` in the same class body — must be flagged."""
        f = tmp_path / "bad.py"
        f.write_text(
            "class MyClass:\n"
            "    def run(self):\n"
            "        return 1\n"
            "\n"
            "    def run(self):  # duplicate\n"
            "        return 2\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 1, findings
        assert findings[0][2] == "run"

    def test_duplicate_nested_function_is_flagged(
        self, tmp_path: Path
    ) -> None:
        """Two ``def inner`` inside another function — must be flagged."""
        f = tmp_path / "bad.py"
        f.write_text(
            "def outer():\n"
            "    def inner():\n"
            "        return 1\n"
            "    def inner():  # duplicate\n"
            "        return 2\n"
            "    return inner()\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 1, findings
        assert findings[0][2] == "inner"

    def test_overload_decorated_functions_not_flagged(
        self, tmp_path: Path
    ) -> None:
        """@overload chains are NOT flagged — they are the intended typing pattern."""
        f = tmp_path / "good.py"
        f.write_text(
            "from typing import overload\n"
            "\n"
            "@overload\n"
            "def process(x: int) -> int: ...\n"
            "\n"
            "@overload\n"
            "def process(x: str) -> str: ...\n"
            "\n"
            "def process(x):  # real implementation\n"
            "    return x\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    def test_typing_overload_attr_form_not_flagged(
        self, tmp_path: Path
    ) -> None:
        """``@typing.overload`` (attribute form) is also exempted."""
        f = tmp_path / "good.py"
        f.write_text(
            "import typing\n"
            "\n"
            "@typing.overload\n"
            "def fn(x: int) -> int: ...\n"
            "\n"
            "@typing.overload\n"
            "def fn(x: str) -> str: ...\n"
            "\n"
            "def fn(x):\n"
            "    return x\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    def test_try_except_import_fallback_not_flagged(
        self, tmp_path: Path
    ) -> None:
        """try/except ImportError fallback defines in separate branches — not flagged."""
        f = tmp_path / "good.py"
        f.write_text(
            "try:\n"
            "    from foo import Bar\n"
            "except ImportError:\n"
            "    def Bar():\n"
            "        pass\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    def test_same_name_different_scopes_not_flagged(
        self, tmp_path: Path
    ) -> None:
        """Same name in different scopes (module + function) is NOT flagged."""
        f = tmp_path / "good.py"
        f.write_text(
            "def helper():\n"
            "    return 1\n"
            "\n"
            "def outer():\n"
            "    def helper():  # different scope — fine\n"
            "        return 2\n"
            "    return helper()\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    def test_clean_file_passes(self, tmp_path: Path) -> None:
        """A file with unique names in each scope produces no findings."""
        f = tmp_path / "good.py"
        f.write_text(
            "def alpha():\n"
            "    pass\n"
            "\n"
            "def beta():\n"
            "    pass\n"
            "\n"
            "class Gamma:\n"
            "    def run(self):\n"
            "        pass\n"
            "    def stop(self):\n"
            "        pass\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    def test_empty_file_passes(self, tmp_path: Path) -> None:
        """An empty file has no findings."""
        f = tmp_path / "empty.py"
        f.write_text("")
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    def test_empty_file_list_passes(self) -> None:
        """An empty file list yields no findings."""
        findings = check_redefined_while_unused([])
        assert len(findings) == 0, findings

    def test_multiple_files_all_scanned(self, tmp_path: Path) -> None:
        """Findings are collected across all provided files."""
        f1 = tmp_path / "a.py"
        f1.write_text("def foo(): pass\ndef foo(): pass\n")
        f2 = tmp_path / "b.py"
        f2.write_text("def bar(): pass\ndef bar(): pass\n")
        findings = check_redefined_while_unused([f1, f2])
        assert len(findings) == 2, findings

    def test_findings_include_file_and_linenos(self, tmp_path: Path) -> None:
        """Each finding is a (file_path, lineno, name, prev_lineno, scope) tuple."""
        f = tmp_path / "bad.py"
        f.write_text(
            "# comment\n"
            "def foo():\n"
            "    pass\n"
            "\n"
            "def foo():  # line 5 — duplicate\n"
            "    pass\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 1
        fpath, lineno, name, prev_lineno, scope = findings[0]
        assert str(f) in fpath
        assert name == "foo"
        assert lineno == 5
        assert prev_lineno == 2
        assert scope == "<module>"

    def test_syntax_error_file_skipped_gracefully(
        self, tmp_path: Path
    ) -> None:
        """A file with a SyntaxError is skipped without raising."""
        f = tmp_path / "broken.py"
        f.write_text("def foo(\n")  # incomplete — SyntaxError
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    # ------------------------------------------------------------------
    # Decorator exemptions: @property / @setter / @singledispatch (task #16)
    # ------------------------------------------------------------------

    def test_property_setter_pair_not_flagged(self, tmp_path: Path) -> None:
        """@property + @x.setter on the same name must NOT be flagged.

        This is the canonical Python property pattern and is NOT a bug.
        """
        f = tmp_path / "good.py"
        f.write_text(
            "class Config:\n"
            "    @property\n"
            "    def value(self):\n"
            "        return self._value\n"
            "\n"
            "    @value.setter\n"
            "    def value(self, v):\n"
            "        self._value = v\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    def test_property_deleter_pair_not_flagged(self, tmp_path: Path) -> None:
        """@property + @x.deleter on the same name must NOT be flagged."""
        f = tmp_path / "good.py"
        f.write_text(
            "class Config:\n"
            "    @property\n"
            "    def value(self):\n"
            "        return self._value\n"
            "\n"
            "    @value.deleter\n"
            "    def value(self):\n"
            "        del self._value\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    def test_property_getter_form_not_flagged(self, tmp_path: Path) -> None:
        """@x.getter (unusual but valid) on same name must NOT be flagged."""
        f = tmp_path / "good.py"
        f.write_text(
            "class Config:\n"
            "    @property\n"
            "    def value(self):\n"
            "        return self._value\n"
            "\n"
            "    @value.getter\n"
            "    def value(self):\n"
            "        return self._value + 1\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    def test_property_full_chain_not_flagged(self, tmp_path: Path) -> None:
        """@property + @x.setter + @x.deleter three-def chain must NOT be flagged."""
        f = tmp_path / "good.py"
        f.write_text(
            "class Config:\n"
            "    @property\n"
            "    def value(self):\n"
            "        return self._value\n"
            "\n"
            "    @value.setter\n"
            "    def value(self, v):\n"
            "        self._value = v\n"
            "\n"
            "    @value.deleter\n"
            "    def value(self):\n"
            "        del self._value\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    def test_singledispatch_register_chain_not_flagged(
        self, tmp_path: Path
    ) -> None:
        """@singledispatch + @fn.register chain must NOT be flagged."""
        f = tmp_path / "good.py"
        f.write_text(
            "from functools import singledispatch\n"
            "\n"
            "@singledispatch\n"
            "def process(arg):\n"
            "    raise NotImplementedError\n"
            "\n"
            "@process.register\n"
            "def process(arg: int):\n"
            "    return arg * 2\n"
            "\n"
            "@process.register\n"
            "def process(arg: str):\n"
            "    return arg.upper()\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    def test_functools_singledispatch_attr_form_not_flagged(
        self, tmp_path: Path
    ) -> None:
        """@functools.singledispatch (attribute form) + @fn.register not flagged."""
        f = tmp_path / "good.py"
        f.write_text(
            "import functools\n"
            "\n"
            "@functools.singledispatch\n"
            "def dispatch(arg):\n"
            "    raise NotImplementedError\n"
            "\n"
            "@dispatch.register\n"
            "def dispatch(arg: int):\n"
            "    return arg + 1\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    def test_genuine_dup_with_no_exempt_decorator_still_flagged(
        self, tmp_path: Path
    ) -> None:
        """A plain duplicate def with NO exemption decorator is still flagged.

        The new exemptions must not create a false-negative for real shadows.
        """
        f = tmp_path / "bad.py"
        f.write_text(
            "def render(path):\n"
            "    return 'v1'\n"
            "\n"
            "def render(path):  # bare duplicate — no exemption decorator\n"
            "    return 'v2'\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 1, findings
        assert findings[0][2] == "render"

    # ------------------------------------------------------------------
    # Control-flow block recursion (task #16)
    # ------------------------------------------------------------------

    def test_dup_def_inside_single_if_branch_is_flagged(
        self, tmp_path: Path
    ) -> None:
        """Two defs of the same name inside ONE if-branch must be flagged.

        Both defs are in the same statement-list (the branch body), so this
        IS a same-scope shadow even though it's nested in an if block.
        """
        f = tmp_path / "bad.py"
        f.write_text(
            "def outer():\n"
            "    if condition:\n"
            "        def helper():\n"
            "            return 1\n"
            "        def helper():  # duplicate in same branch body\n"
            "            return 2\n"
            "        return helper()\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 1, findings
        assert findings[0][2] == "helper"

    def test_try_except_fallback_still_not_flagged_after_recurse(
        self, tmp_path: Path
    ) -> None:
        """try/except import-fallback with same name in different branches NOT flagged.

        Regression guard: the block-body recursion must preserve this exemption —
        the two defs are in DIFFERENT statement-lists (try.body vs handler.body).
        """
        f = tmp_path / "good.py"
        f.write_text(
            "try:\n"
            "    from fast_lib import Bar\n"
            "except ImportError:\n"
            "    def Bar():\n"
            "        pass\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings

    def test_dup_def_inside_nested_compound_stmt_is_flagged(
        self, tmp_path: Path
    ) -> None:
        """Duplicate def inside a nested for-loop body must be flagged."""
        f = tmp_path / "bad.py"
        f.write_text(
            "for item in items:\n"
            "    def process():\n"
            "        return 1\n"
            "    def process():  # duplicate within same for-body\n"
            "        return 2\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 1, findings
        assert findings[0][2] == "process"

    def test_dup_def_split_across_if_else_not_flagged(
        self, tmp_path: Path
    ) -> None:
        """Same name in if-branch vs else-branch is NOT flagged — different lists."""
        f = tmp_path / "good.py"
        f.write_text(
            "if condition:\n"
            "    def handler():\n"
            "        return 'a'\n"
            "else:\n"
            "    def handler():\n"
            "        return 'b'\n"
        )
        findings = check_redefined_while_unused([f])
        assert len(findings) == 0, findings


# ---------------------------------------------------------------------------
# Integration: rv lint cmd_lint picks up F811 rule
# ---------------------------------------------------------------------------


class TestCmdLintF811Integration:
    """cmd_lint runs the F811 check over src/ and non-zeroes on any finding."""

    def test_cmd_lint_fails_on_f811_in_src(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cmd_lint exits 1 when src/ contains a shadowed function definition."""
        from research_vault.lint import cmd_lint
        import research_vault.lint as lint_mod

        # Build a temp src tree with a duplicate def
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "bad.py").write_text(
            "def check_ms(path):\n"
            "    return True\n"
            "\n"
            "def check_ms(path):  # duplicate\n"
            "    return False\n"
        )
        monkeypatch.setattr(lint_mod, "_SRC_DIR", src_dir)
        # Also point _TESTS_DIR at an empty dir so hygiene rules don't trip
        empty_tests = tmp_path / "tests"
        empty_tests.mkdir()
        monkeypatch.setattr(lint_mod, "_TESTS_DIR", empty_tests)

        cfg = _make_minimal_config(tmp_path)
        rc = cmd_lint(cfg)
        assert rc == 1

    def test_cmd_lint_passes_when_src_has_no_f811(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cmd_lint exits 0 when src/ has no redefined-while-unused violations."""
        from research_vault.lint import cmd_lint
        import research_vault.lint as lint_mod

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "good.py").write_text(
            "def alpha():\n    pass\n\ndef beta():\n    pass\n"
        )
        monkeypatch.setattr(lint_mod, "_SRC_DIR", src_dir)
        empty_tests = tmp_path / "tests"
        empty_tests.mkdir()
        monkeypatch.setattr(lint_mod, "_TESTS_DIR", empty_tests)

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
