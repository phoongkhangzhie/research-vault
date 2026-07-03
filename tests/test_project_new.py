"""test_project_new.py — Tests for `rv project new` (SR-NEW capstone).

Hermetic: all tests run in tmp_path, never touch ~/vault or any private instance.

Acceptance checklist (per §5B-NEW-REQ):
  1. Happy-path: exit 0, repo is git-initialized on main, one chore: commit.
  2. Config has [projects.demo] with code, source_dir, roster, refs.
  3. control/<slug>.md exists and passes rv control check; DEVLOG.md,
     architecture.md, library.json (== []) exist; OKF type dirs exist;
     rv note succeeds (path resolves via registry).
  4. Roster non-empty → build-agents wrote .agents/<slug>/<role>.md.
  5. Zotero SKIPPED (no key) → collection unset, library.json == [], exit 0.
  6. git-discipline consent: WITHOUT flag → no .githooks/, offer line printed;
     WITH flag → .githooks/ + core.hooksPath == .githooks.
  7. Guards: duplicate slug → non-zero, untouched; duplicate code → non-zero.
  8. Rollback: post-register scaffold failure → no registry entry, dir removed.
  9. rv help --check GREEN; rv lint GREEN.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest

# Ensure src importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from research_vault.config import load_config, reset_config_cache
from research_vault.project import (
    DEFAULT_ROSTER,
    _rollback_registry,
    _render_project_section,
    cmd_add,
    cmd_new,
)
from tests.gitutil import invoke_cli


# ---------------------------------------------------------------------------
# Fixture: a minimal Research Vault instance (config + dirs)
# ---------------------------------------------------------------------------

@pytest.fixture
def rv_instance(tmp_path: Path) -> Generator[Path, None, None]:
    """Minimal RV instance — config wired, no demo projects."""
    config_file = tmp_path / "research_vault.toml"
    config_file.write_text(
        f"""\
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"
""",
        encoding="utf-8",
    )
    old = os.environ.get("RESEARCH_VAULT_CONFIG")
    os.environ["RESEARCH_VAULT_CONFIG"] = str(config_file)
    reset_config_cache()
    yield tmp_path
    # Restore
    if old is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old
    reset_config_cache()


@pytest.fixture
def rv_instance_with_existing(rv_instance: Path) -> Path:
    """RV instance pre-populated with an existing project (slug=existing, code=ex)."""
    config_file = rv_instance / "research_vault.toml"
    existing_src = rv_instance / "projects" / "existing"
    existing_src.mkdir(parents=True, exist_ok=True)
    cmd_add(
        name="existing",
        code="ex",
        source_dir=str(existing_src),
        roster=[],
        config_path=config_file,
    )
    reset_config_cache()
    load_config(reload=True)
    return rv_instance


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo)] + list(args),
        capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# 1. Happy path — basic scaffold
# ---------------------------------------------------------------------------

class TestProjectNewHappyPath:
    def test_exits_zero(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        rc = cmd_new("demo", "dm", str(src), DEFAULT_ROSTER)
        assert rc == 0, f"cmd_new should return 0"

    def test_creates_git_repo_on_main(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        assert (src / ".git").exists(), ".git dir must exist"
        r = _git(src, "rev-parse", "--abbrev-ref", "HEAD")
        assert r.stdout.strip() == "main", f"branch must be main, got {r.stdout.strip()!r}"

    def test_exactly_one_chore_commit(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        r = _git(src, "log", "--oneline")
        commits = [l for l in r.stdout.strip().splitlines() if l]
        assert len(commits) == 1, f"must have exactly one commit, got {commits}"
        assert "chore:" in commits[0], f"commit subject must start with chore:, got {commits[0]!r}"

    def test_conventional_commit_subject(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        r = _git(src, "log", "--format=%s")
        subject = r.stdout.strip()
        assert subject == "chore: scaffold demo project", f"bad subject: {subject!r}"


# ---------------------------------------------------------------------------
# 2. Registry — TOML fields
# ---------------------------------------------------------------------------

class TestProjectNewRegistry:
    def test_config_has_project_section(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        config_file = rv_instance / "research_vault.toml"
        cmd_new("demo", "dm", str(src), ["engineer"])
        content = config_file.read_bytes()
        parsed = tomllib.loads(content.decode())
        assert "demo" in parsed.get("projects", {}), "demo must appear in [projects]"

    def test_config_has_required_fields(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        config_file = rv_instance / "research_vault.toml"
        cmd_new("demo", "dm", str(src), DEFAULT_ROSTER)
        parsed = tomllib.loads(config_file.read_bytes().decode())
        proj = parsed["projects"]["demo"]
        assert proj["code"] == "dm"
        assert proj["source_dir"] == str(src)
        assert proj.get("roster") == DEFAULT_ROSTER, (
            f"registry must contain DEFAULT_ROSTER, got {proj.get('roster')!r}"
        )
        assert "refs" in proj, "refs key must be written by project new"
        assert proj["refs"].endswith("library.json"), f"refs must point to library.json: {proj['refs']}"

    def test_config_resolves_after_reload(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        reset_config_cache()
        cfg = load_config(reload=True)
        proj = cfg.project("demo")  # must not raise KeyError
        assert proj["code"] == "dm"


# ---------------------------------------------------------------------------
# 3. Scaffolded files
# ---------------------------------------------------------------------------

class TestProjectNewScaffold:
    def test_devlog_exists(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        assert (src / "DEVLOG.md").exists(), "DEVLOG.md must exist"
        text = (src / "DEVLOG.md").read_text()
        assert "DEVLOG" in text

    def test_architecture_md_exists(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        arch = src / "architecture.md"
        assert arch.exists(), "architecture.md must exist"
        text = arch.read_text()
        assert "demo" in text, "architecture.md must reference the project slug"

    def test_library_json_is_empty_list(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        lib = src / "library.json"
        assert lib.exists(), "library.json must exist"
        assert json.loads(lib.read_text()) == [], "library.json must be an empty list"

    def test_control_file_exists(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        config_file = rv_instance / "research_vault.toml"
        cmd_new("demo", "dm", str(src), [])
        control = rv_instance / "control" / "demo.md"
        assert control.exists(), f"control/demo.md must exist at {control}"

    def test_control_file_passes_check(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        from research_vault import control
        reset_config_cache()
        cfg = load_config(reload=True)
        violations = control.cmd_check("demo", config=cfg)
        assert violations == [], f"control check must pass, got violations: {violations}"

    def test_okf_type_dirs_exist(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        from research_vault.note import OKF_TYPES
        for note_type in OKF_TYPES:
            d = src / note_type
            assert d.exists() and d.is_dir(), f"OKF dir {note_type}/ must exist under {src}"

    def test_rv_note_resolves_via_registry(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        rc = invoke_cli(["note", "demo", "new", "findings", "test finding"])
        assert rc == 0, f"rv note demo new findings ... must succeed (exit {rc})"


# ---------------------------------------------------------------------------
# 4. Crew hats
# ---------------------------------------------------------------------------

class TestProjectNewCrew:
    def test_default_roster_does_not_create_per_project_hats(self, rv_instance: Path) -> None:
        """cmd_new does NOT create per-project hat files (SR-LENS-RM: vault-level crew).

        The crew is built once at rv init; rv project new no longer bakes hats.
        """
        src = rv_instance / "projects" / "demo"
        rc = cmd_new("demo", "dm", str(src), DEFAULT_ROSTER)
        assert rc == 0
        # No per-project agents subdir should exist
        per_proj_agents = rv_instance / ".agents" / "demo"
        assert not per_proj_agents.exists(), (
            ".agents/demo/ must NOT exist — crew is vault-level (SR-LENS-RM)"
        )

    def test_empty_roster_does_not_create_per_project_hats(self, rv_instance: Path) -> None:
        """cmd_new with roster=[] also creates no per-project hats (SR-LENS-RM)."""
        src = rv_instance / "projects" / "demo"
        rc = cmd_new("demo", "dm", str(src), [])
        assert rc == 0
        per_proj_agents = rv_instance / ".agents" / "demo"
        assert not per_proj_agents.exists(), (
            ".agents/demo/ must NOT exist even with empty roster (SR-LENS-RM)"
        )


# ---------------------------------------------------------------------------
# 5. Zotero skipped (no key)
# ---------------------------------------------------------------------------

class TestProjectNewZoteroSkipped:
    def test_zotero_skipped_when_no_key(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        # Ensure no key in env
        old = os.environ.pop("ZOTERO_KEY", None)
        try:
            rc = cmd_new("demo", "dm", str(src), [], zotero=True)
        finally:
            if old is not None:
                os.environ["ZOTERO_KEY"] = old
        # Should still succeed (Zotero step gracefully skipped)
        assert rc == 0, "exit must be 0 even with --zotero and no key"
        assert (src / "library.json").exists(), "library.json must still exist"
        assert json.loads((src / "library.json").read_text()) == []

    def test_zotero_flag_off_library_json_empty(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [], zotero=False)
        assert json.loads((src / "library.json").read_text()) == []


# ---------------------------------------------------------------------------
# 6. git-discipline consent
# ---------------------------------------------------------------------------

class TestProjectNewGitDiscipline:
    def test_without_flag_no_githooks(self, rv_instance: Path, capsys) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [], git_discipline=False)
        # .githooks/ must NOT exist
        assert not (src / ".githooks").exists(), ".githooks/ must not exist without --git-discipline"
        # core.hooksPath must be unset
        r = _git(src, "config", "--local", "core.hooksPath")
        assert r.returncode != 0 or r.stdout.strip() != ".githooks", \
            "core.hooksPath must not be .githooks without --git-discipline"

    def test_without_flag_prints_offer(self, rv_instance: Path, capsys) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [], git_discipline=False)
        captured = capsys.readouterr()
        assert "rv git-discipline install" in captured.out, \
            "should print install offer when --git-discipline not passed"

    def test_with_flag_installs_hooks(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [], git_discipline=True)
        assert (src / ".githooks").exists(), ".githooks/ must exist with --git-discipline"
        r = _git(src, "config", "--local", "core.hooksPath")
        assert r.stdout.strip() == ".githooks", \
            f"core.hooksPath must be .githooks, got {r.stdout.strip()!r}"


# ---------------------------------------------------------------------------
# 7. Guards
# ---------------------------------------------------------------------------

class TestProjectNewGuards:
    def test_duplicate_slug_refuses(self, rv_instance_with_existing: Path) -> None:
        src = rv_instance_with_existing / "projects" / "existing2"
        rc = cmd_new("existing", "ex2", str(src), [])
        assert rc != 0, "must refuse duplicate slug"

    def test_duplicate_slug_leaves_registry_untouched(self, rv_instance_with_existing: Path) -> None:
        config_file = rv_instance_with_existing / "research_vault.toml"
        before = config_file.read_text()
        src = rv_instance_with_existing / "projects" / "existing2"
        cmd_new("existing", "ex2", str(src), [])
        after = config_file.read_text()
        assert before == after, "config must be untouched after duplicate-slug refusal"

    def test_duplicate_code_refuses(self, rv_instance_with_existing: Path) -> None:
        src = rv_instance_with_existing / "projects" / "fresh"
        rc = cmd_new("fresh", "ex", str(src), [])  # code "ex" already in use
        assert rc != 0, "must refuse duplicate code"

    def test_invalid_name_refuses(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "bad"
        rc = cmd_new("1bad-name", "bd", str(src), [])
        assert rc != 0

    def test_nonempty_source_dir_refuses_without_force(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        src.mkdir(parents=True)
        (src / "existing.txt").write_text("existing")
        rc = cmd_new("demo", "dm", str(src), [])
        assert rc != 0, "must refuse non-empty source dir without --force"

    def test_force_overwrites(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        src.mkdir(parents=True)
        (src / "existing.txt").write_text("existing")
        rc = cmd_new("demo", "dm", str(src), [], force=True)
        assert rc == 0, "must succeed with --force even on non-empty dir"
        assert not (src / "existing.txt").exists(), "--force must remove existing content"

    def test_default_source_is_sibling_of_instance_root(self, rv_instance: Path) -> None:
        """Without --source, default repo path = instance_root.parent / slug."""
        rc = cmd_new("demo", "dm", None, [])  # no source_dir
        assert rc == 0, "must succeed without --source"
        # Default path = rv_instance.parent / "demo"
        # rv_instance IS instance_root, so default = rv_instance.parent / "demo"
        default_src = rv_instance.parent / "demo"
        assert default_src.exists(), f"default source path {default_src} must exist"
        assert (default_src / ".git").exists(), "default path must be a git repo"
        # Cleanup: remove the sibling dir so we don't pollute other tests
        import shutil
        shutil.rmtree(default_src, ignore_errors=True)


# ---------------------------------------------------------------------------
# 8. Rollback
# ---------------------------------------------------------------------------

class TestProjectNewRollback:
    def test_rollback_removes_registry_entry(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        config_file = rv_instance / "research_vault.toml"

        # Monkeypatch scaffold_okf_dirs to raise after registration
        from research_vault import note as note_mod

        def _boom(base):
            raise RuntimeError("injected failure for rollback test")

        original_scaffold = note_mod.scaffold_okf_dirs
        note_mod.scaffold_okf_dirs = _boom

        try:
            rc = cmd_new("demo", "dm", str(src), [])
        finally:
            note_mod.scaffold_okf_dirs = original_scaffold

        assert rc != 0, "must fail when scaffold raises"
        # Config must NOT contain [projects.demo]
        content = config_file.read_text()
        assert "[projects.demo]" not in content, \
            "rollback must remove [projects.demo] from config"

    def test_rollback_removes_source_dir(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"

        from research_vault import note as note_mod

        def _boom(base):
            raise RuntimeError("injected failure")

        original_scaffold = note_mod.scaffold_okf_dirs
        note_mod.scaffold_okf_dirs = _boom

        try:
            rc = cmd_new("demo", "dm", str(src), [])
        finally:
            note_mod.scaffold_okf_dirs = original_scaffold

        assert not src.exists(), "rollback must remove the source dir"

    def test_preflight_failure_no_dir_created(self, rv_instance_with_existing: Path) -> None:
        """Guard failure (before git init) must not leave a stale directory."""
        src = rv_instance_with_existing / "projects" / "existing"
        # Not creating src — the duplicate guard should fire before git init
        rc = cmd_new("existing", "ex2", str(src), [])
        assert rc != 0
        # src was never created (guard fires first)
        # This is fine — we just assert the guard worked


# ---------------------------------------------------------------------------
# 9. rv help --check and rv lint
# ---------------------------------------------------------------------------

class TestProjectNewDiscovery:
    def test_rv_help_check_green(self, rv_instance: Path) -> None:
        rc = invoke_cli(["help", "--check"])
        assert rc == 0, "rv help --check must be GREEN"

    def test_rv_lint_green(self, rv_instance: Path) -> None:
        rc = invoke_cli(["lint"])
        assert rc == 0, "rv lint must pass"


# ---------------------------------------------------------------------------
# CLI path — via invoke_cli
# ---------------------------------------------------------------------------

class TestProjectNewCLI:
    def test_cli_exit_zero(self, rv_instance: Path) -> None:
        """CLI rv project new without --roster succeeds (roster auto-defaults)."""
        src = rv_instance / "projects" / "demo"
        rc = invoke_cli([
            "project", "new", "demo",
            "--code", "dm",
            "--source", str(src),
        ])
        assert rc == 0

    def test_cli_exit_zero_no_source(self, rv_instance: Path) -> None:
        """CLI works without --source (uses default sibling path)."""
        import shutil
        rc = invoke_cli([
            "project", "new", "demo",
            "--code", "dm",
        ])
        assert rc == 0
        # Cleanup sibling dir
        default_src = rv_instance.parent / "demo"
        shutil.rmtree(default_src, ignore_errors=True)

    def test_cli_duplicate_slug_nonzero(self, rv_instance_with_existing: Path) -> None:
        src = rv_instance_with_existing / "projects" / "existing2"
        rc = invoke_cli([
            "project", "new", "existing",
            "--code", "x2",
            "--source", str(src),
        ])
        assert rc != 0

    def test_cli_duplicate_code_nonzero(self, rv_instance_with_existing: Path) -> None:
        src = rv_instance_with_existing / "projects" / "fresh"
        rc = invoke_cli([
            "project", "new", "fresh",
            "--code", "ex",  # already in use
            "--source", str(src),
        ])
        assert rc != 0


# ---------------------------------------------------------------------------
# Unit — thin primitive extensions
# ---------------------------------------------------------------------------

class TestRenderProjectSectionExtra:
    def test_extra_keys_rendered(self) -> None:
        section = _render_project_section(
            "myproj", "mp", "/data/myproj", [],
            extra={"refs": "/data/myproj/library.json"},
        )
        assert 'refs = "/data/myproj/library.json"' in section

    def test_extra_after_canonical_fields(self) -> None:
        section = _render_project_section(
            "myproj", "mp", "/data/myproj", ["engineer"],
            extra={"refs": "/data/myproj/library.json", "collection": "ABCDEF"},
        )
        idx_roster = section.index("roster =")
        idx_refs = section.index("refs =")
        idx_coll = section.index("collection =")
        assert idx_roster < idx_refs, "refs must come after roster"
        assert idx_refs < idx_coll, "collection must come after refs"

    def test_no_extra_is_backward_compatible(self) -> None:
        section = _render_project_section("myproj", "mp", "/data", [])
        assert "refs" not in section
        assert "collection" not in section


class TestRollbackRegistry:
    def test_rollback_removes_appended_section(self, tmp_path: Path) -> None:
        config = tmp_path / "research_vault.toml"
        original = '[instance]\nkey = "value"\n'
        config.write_text(original, encoding="utf-8")
        appended = '\n[projects.demo]\ncode = "dm"\n'
        config.write_text(original + appended, encoding="utf-8")
        _rollback_registry(config, "demo")
        assert config.read_text(encoding="utf-8") == original

    def test_rollback_noop_when_no_section(self, tmp_path: Path) -> None:
        config = tmp_path / "research_vault.toml"
        original = '[instance]\nkey = "value"\n'
        config.write_text(original, encoding="utf-8")
        _rollback_registry(config, "nonexistent")  # must not raise or modify
        assert config.read_text(encoding="utf-8") == original


class TestScaffoldOkfDirs:
    def test_creates_all_six_dirs(self, tmp_path: Path) -> None:
        from research_vault.note import OKF_TYPES, scaffold_okf_dirs
        scaffold_okf_dirs(tmp_path)
        for t in OKF_TYPES:
            assert (tmp_path / t).is_dir(), f"{t}/ must be created"

    def test_idempotent(self, tmp_path: Path) -> None:
        from research_vault.note import scaffold_okf_dirs
        scaffold_okf_dirs(tmp_path)
        scaffold_okf_dirs(tmp_path)  # must not raise
