"""test_init_git_repo.py — rv init git-repo acceptance tests.

Acceptance criteria (fix/init-git-repo):
1. rv init <tmp> → the instance is a git repo (.git exists) with an initial commit.
2. .gitignore present and ignores state/ and control/.
3. Key tracked files (CLAUDE.md, doctrine/, .claude/agents/) are committed.
4. git-missing path → warns on stderr, scaffold still succeeds (rc == 0), no crash.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True, text=True, cwd=str(cwd),
    )


def _committed_files(vault: Path) -> set[str]:
    """Return the set of files in the initial git commit (relative paths)."""
    r = _git("ls-tree", "--name-only", "-r", "HEAD", cwd=vault)
    assert r.returncode == 0, f"git ls-tree failed: {r.stderr}"
    return set(r.stdout.splitlines())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_vault(tmp_path):
    """Scaffold a fresh rv init instance and return the instance root Path."""
    from research_vault.init import cmd_init_in_dir
    rc = cmd_init_in_dir(str(tmp_path))
    assert rc == 0, "rv init failed"
    return tmp_path


# ---------------------------------------------------------------------------
# 1. git repo existence + initial commit
# ---------------------------------------------------------------------------

class TestGitRepoScaffold:
    def test_git_dir_exists(self, tmp_vault):
        """.git/ directory must exist after rv init."""
        assert (tmp_vault / ".git").is_dir(), \
            ".git/ missing — rv init must git-init the vault"

    def test_initial_commit_exists(self, tmp_vault):
        """The vault must have at least one git commit."""
        r = _git("rev-list", "--count", "HEAD", cwd=tmp_vault)
        assert r.returncode == 0, \
            f"git rev-list failed (no commits?): {r.stderr.strip()}"
        count = int(r.stdout.strip())
        assert count >= 1, f"Expected at least 1 commit, got {count}"

    def test_initial_commit_message(self, tmp_vault):
        """Initial commit message must be 'initial vault scaffold'."""
        r = _git("log", "--oneline", "-1", cwd=tmp_vault)
        assert r.returncode == 0
        assert "initial vault scaffold" in r.stdout, \
            f"Unexpected commit message: {r.stdout.strip()!r}"

    def test_branch_is_main(self, tmp_vault):
        """Default branch must be 'main'."""
        r = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=tmp_vault)
        assert r.returncode == 0
        assert r.stdout.strip() == "main", \
            f"Expected branch 'main', got {r.stdout.strip()!r}"


# ---------------------------------------------------------------------------
# 2. .gitignore presence and content
# ---------------------------------------------------------------------------

class TestGitignore:
    def test_gitignore_exists(self, tmp_vault):
        """.gitignore must be written at the vault root."""
        assert (tmp_vault / ".gitignore").is_file(), \
            ".gitignore missing — rv init must write one"

    def test_gitignore_ignores_state_dir(self, tmp_vault):
        """state/* must appear in .gitignore (wildcard form to allow manifest exception)."""
        text = (tmp_vault / ".gitignore").read_text(encoding="utf-8")
        assert "state/*" in text, ".gitignore must ignore state/* (wildcard, not state/)"

    def test_gitignore_has_manifest_exception(self, tmp_vault):
        """state/compute_manifest.json must be excepted from the state/* ignore rule.

        The compute manifest is declared config (the adopter edits host/tiers) —
        it should be versioned even though the rest of state/ is volatile.
        The git '!' negation requires the parent to be ignored via state/* (not
        state/) so the exception can take effect.
        """
        text = (tmp_vault / ".gitignore").read_text(encoding="utf-8")
        assert "!state/compute_manifest.json" in text, \
            ".gitignore must un-ignore state/compute_manifest.json (declared config)"

    def test_manifest_exception_is_structurally_valid(self, tmp_vault):
        """git must actually un-ignore state/compute_manifest.json (structural check).

        Proves the '!' exception works: git check-ignore must return exit 1 (not ignored)
        for the manifest file.  This would fail if we used 'state/' (directory exclude)
        instead of 'state/*' (content exclude), because git doesn't descend into
        excluded directories to apply negation rules.
        """
        # Create the file so check-ignore has something to test against
        (tmp_vault / "state" / "compute_manifest.json").write_text("{}", encoding="utf-8")
        r = _git("check-ignore", "-q", "state/compute_manifest.json", cwd=tmp_vault)
        # exit code 1 = NOT ignored; exit code 0 = IS ignored
        assert r.returncode == 1, (
            "state/compute_manifest.json is incorrectly treated as ignored by git.\n"
            "The .gitignore must use 'state/*' (not 'state/') so the "
            "'!state/compute_manifest.json' exception can take effect."
        )

    def test_gitignore_ignores_control_dir(self, tmp_vault):
        """control/ must appear in .gitignore."""
        text = (tmp_vault / ".gitignore").read_text(encoding="utf-8")
        assert "control/" in text, ".gitignore must ignore control/"

    def test_gitignore_ignores_pycache(self, tmp_vault):
        """__pycache__/ must appear in .gitignore."""
        text = (tmp_vault / ".gitignore").read_text(encoding="utf-8")
        assert "__pycache__/" in text, ".gitignore must ignore __pycache__/"

    def test_gitignore_ignores_venv(self, tmp_vault):
        """.venv/ must appear in .gitignore."""
        text = (tmp_vault / ".gitignore").read_text(encoding="utf-8")
        assert ".venv/" in text, ".gitignore must ignore .venv/"

    def test_state_dir_is_git_ignored(self, tmp_vault):
        """git must treat a file inside state/ as ignored (structural git check)."""
        (tmp_vault / "state" / "test.txt").write_text("x", encoding="utf-8")
        r = _git("check-ignore", "-q", "state/test.txt", cwd=tmp_vault)
        assert r.returncode == 0, \
            "state/test.txt is NOT ignored by git — state/ must be in .gitignore"

    def test_control_dir_is_git_ignored(self, tmp_vault):
        """git must treat a file inside control/ as ignored (structural git check)."""
        (tmp_vault / "control" / "test.txt").write_text("x", encoding="utf-8")
        r = _git("check-ignore", "-q", "control/test.txt", cwd=tmp_vault)
        assert r.returncode == 0, \
            "control/test.txt is NOT ignored by git — control/ must be in .gitignore"

    def test_gitignore_is_committed(self, tmp_vault):
        """.gitignore must be in the initial commit (adopter clones a clean repo)."""
        committed = _committed_files(tmp_vault)
        assert ".gitignore" in committed, \
            ".gitignore must be committed in the initial vault scaffold"


# ---------------------------------------------------------------------------
# 3. Key tracked files are in the initial commit
# ---------------------------------------------------------------------------

class TestTrackedFilesCommitted:
    def test_claude_md_committed(self, tmp_vault):
        """CLAUDE.md must be in the initial commit."""
        committed = _committed_files(tmp_vault)
        assert "CLAUDE.md" in committed, "CLAUDE.md must be committed"

    def test_research_vault_toml_committed(self, tmp_vault):
        """research_vault.toml must be in the initial commit."""
        committed = _committed_files(tmp_vault)
        assert "research_vault.toml" in committed, \
            "research_vault.toml must be committed"

    def test_at_least_one_agent_file_committed(self, tmp_vault):
        """At least one .claude/agents/<role>.md must be in the initial commit."""
        committed = _committed_files(tmp_vault)
        agent_files = {f for f in committed if f.startswith(".claude/agents/")}
        assert agent_files, \
            "No .claude/agents/*.md files in initial commit — crew must be committed"

    def test_doctrine_committed(self, tmp_vault):
        """At least one file under doctrine/ must be in the initial commit."""
        committed = _committed_files(tmp_vault)
        doctrine_files = {f for f in committed if f.startswith("doctrine/")}
        assert doctrine_files, \
            "No doctrine/ files in initial commit — doctrine must be committed"

    def test_devlog_committed(self, tmp_vault):
        """DEVLOG.md must be in the initial commit."""
        committed = _committed_files(tmp_vault)
        assert "DEVLOG.md" in committed, "DEVLOG.md must be committed"

    def test_quickstart_committed(self, tmp_vault):
        """QUICKSTART.md must be in the initial commit."""
        committed = _committed_files(tmp_vault)
        assert "QUICKSTART.md" in committed, "QUICKSTART.md must be committed"

    def test_state_dir_NOT_committed(self, tmp_vault):
        """state/ contents must NOT be in the initial commit (volatile, gitignored)."""
        committed = _committed_files(tmp_vault)
        state_files = {f for f in committed if f.startswith("state/")}
        assert not state_files, \
            f"state/ files were committed: {state_files} — state/ must be gitignored"

    def test_control_dir_NOT_committed(self, tmp_vault):
        """control/ contents must NOT be in the initial commit (volatile, gitignored).

        Note: control files are created by rv init but should be gitignored so the
        coordination bus is not versioned on every operation.
        """
        committed = _committed_files(tmp_vault)
        control_files = {f for f in committed if f.startswith("control/")}
        assert not control_files, \
            f"control/ files were committed: {control_files} — control/ must be gitignored"


# ---------------------------------------------------------------------------
# 4. git-missing graceful degradation
# ---------------------------------------------------------------------------

class TestGitMissingDegradation:
    def test_scaffold_succeeds_when_git_missing(self, tmp_path, monkeypatch):
        """rv init must return 0 even when git is not on PATH (graceful degradation)."""
        import shutil as _shutil
        import research_vault.init as _init_mod

        original_which = _shutil.which

        def _no_git(name, *args, **kwargs):
            if name == "git":
                return None
            return original_which(name, *args, **kwargs)

        # Patch shutil.which inside the init module's shutil reference
        monkeypatch.setattr(_init_mod.shutil, "which", _no_git)

        from research_vault.init import cmd_init_in_dir
        rc = cmd_init_in_dir(str(tmp_path))
        assert rc == 0, \
            "rv init must return 0 even when git is missing (graceful degradation)"

    def test_scaffold_files_present_when_git_missing(self, tmp_path, monkeypatch):
        """All scaffold files must be written even when git is unavailable."""
        import shutil as _shutil
        import research_vault.init as _init_mod

        original_which = _shutil.which

        def _no_git(name, *args, **kwargs):
            if name == "git":
                return None
            return original_which(name, *args, **kwargs)

        monkeypatch.setattr(_init_mod.shutil, "which", _no_git)

        from research_vault.init import cmd_init_in_dir
        rc = cmd_init_in_dir(str(tmp_path))
        assert rc == 0

        # Key scaffold files must still be present despite no git
        assert (tmp_path / "research_vault.toml").is_file(), \
            "research_vault.toml must exist even when git is missing"
        assert (tmp_path / "CLAUDE.md").is_file(), \
            "CLAUDE.md must exist even when git is missing"
        assert (tmp_path / "QUICKSTART.md").is_file(), \
            "QUICKSTART.md must exist even when git is missing"
        assert (tmp_path / ".claude" / "agents").is_dir(), \
            ".claude/agents/ must exist even when git is missing"

        # No .git dir — git-missing means no repo
        assert not (tmp_path / ".git").exists(), \
            ".git/ must NOT exist when git was missing at init time"

    def test_warning_emitted_when_git_missing(self, tmp_path, monkeypatch, capsys):
        """A clear warning mentioning git must be printed to stderr when git is not on PATH."""
        import shutil as _shutil
        import research_vault.init as _init_mod

        original_which = _shutil.which

        def _no_git(name, *args, **kwargs):
            if name == "git":
                return None
            return original_which(name, *args, **kwargs)

        monkeypatch.setattr(_init_mod.shutil, "which", _no_git)

        from research_vault.init import cmd_init_in_dir
        rc = cmd_init_in_dir(str(tmp_path))
        assert rc == 0

        captured = capsys.readouterr()
        err_lower = captured.err.lower()
        assert "git" in err_lower, \
            "rv init must warn about missing git on stderr"
        assert "not found" in err_lower or "warning" in err_lower, \
            f"Warning must mention git is missing/not-found; got: {captured.err!r}"
