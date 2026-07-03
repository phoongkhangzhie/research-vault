"""test_task22_wheel_audit.py — Task #22 part 2: wheel __file__ audit.

Scope: verifies the one user-reachable __file__-repo-root usage found by the audit:
  wait_for.py _get_package_path() — the path injected into the background poller
  subprocess's sys.path so it can ``import research_vault.wait_for``.

Audit summary (each usage adjudicated):
  lint.py _FRAMEWORK_ROOT / _TESTS_DIR / _SRC_DIR  → DEV-ONLY: scan framework's own
      tests/ and src/; wheel context returns 0 files (no-op, no crash); annotated.
  lint.py cmd_lint src_dir = Path(__file__).parent  → DEV-ONLY: leakage scan targets
      the framework's own source (framework-dev CI tool); annotated.
  git_discipline.py scripts/leakage_scan.sh path    → DEV-ONLY: graceful fallback
      already handles wheel (fails-open with warning); comments already describe
      dev vs installed intent; no change needed.
  wait_for.py _get_package_path()                   → USER-REACHABLE: rv wait-for
      launches a background poller subprocess that needs to import research_vault.
      Old code: Path(__file__).parent.parent.parent (3 levels = repo root or lib/
      python3.x/ in wheel — neither is the correct sys.path entry for the package).
      Fix: Path(__file__).parent.parent (2 levels = src/ in dev, site-packages/ in
      wheel — both are the correct sys.path parent for ``import research_vault``).

TDD proof: test_poller_package_path_contains_research_vault is RED on the old 3-parent
code and GREEN on the 2-parent fix. Verified by checking that the directory returned
by _get_package_path() contains a ``research_vault/`` subdirectory.
"""
from __future__ import annotations

from pathlib import Path


class TestPollerPackagePath:
    """Background-poller package_path correctness (task #22 fix)."""

    def test_poller_package_path_contains_research_vault(self):
        """_get_package_path() must return the parent dir of the research_vault package.

        This is the path injected into sys.path of the background poller subprocess
        so it can ``import research_vault.wait_for``. The correct value is:
          - ``src/`` in dev/editable install (Path(__file__).parent.parent)
          - ``site-packages/`` in a wheel install (Path(__file__).parent.parent)
        Both always contain a ``research_vault/`` subdirectory.

        RED with old 3-parent code (repo root / lib/ dir — no research_vault/ there).
        GREEN with the 2-parent fix.
        """
        from research_vault.wait_for import _get_package_path

        pkg_path = Path(_get_package_path())
        assert (pkg_path / "research_vault").is_dir(), (
            f"_get_package_path() returned {pkg_path!r} which does NOT contain "
            f"research_vault/. The poller subprocess would fail to import "
            f"research_vault.wait_for via sys.path.insert(0, package_path). "
            f"Expected src/ (dev) or site-packages/ (wheel) — one parent level up "
            f"from the research_vault/ package directory, NOT two levels up."
        )

    def test_old_three_parent_path_does_not_contain_research_vault(self):
        """Regression guard: the old Path(__file__).parent.parent.parent (3-up) does
        NOT contain research_vault/, confirming the original bug.

        In dev: 3 levels above src/research_vault/wait_for.py is the repo root.
        The repo root contains src/, not research_vault/ directly.
        In wheel: 3 levels above site-packages/research_vault/wait_for.py is
        something like lib/python3.x/ — also wrong.

        This assertion proves that the 3-parent form was incorrect; the 2-parent fix
        is the right solution.
        """
        import research_vault.wait_for as wf_module

        wait_for_file = Path(wf_module.__file__)
        old_path = wait_for_file.parent.parent.parent  # the pre-fix calculation
        assert not (old_path / "research_vault").is_dir(), (
            f"Old 3-parent path {old_path!r} / research_vault DOES exist. "
            f"This means the dev environment has an unusual structure. "
            f"The invariant is: repo_root/research_vault/ does not exist "
            f"(the package lives at repo_root/src/research_vault/, not directly at root)."
        )
