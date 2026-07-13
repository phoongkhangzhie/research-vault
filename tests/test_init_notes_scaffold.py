"""test_init_notes_scaffold.py — instance scaffold must not create project-type dirs.

Acceptance criteria:
1. `rv init` creates ONLY the shared-canonical OKF dirs (literature/, concepts/,
   datasets/) under the instance's notes_root — never the project-scoped types
   (experiments/, findings/, gaps/, methodology/, mocs/) and never a stale
   pre-rename `methods/` dir.
2. `rv project new` (project scaffold) still creates the FULL OKF type set at
   the project's source_dir root — unaffected by the instance-side fix.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from research_vault.note import OKF_PROJECT_TYPES, OKF_SHARED_TYPES


@pytest.fixture()
def tmp_vault(tmp_path):
    """Scaffold a fresh rv init instance and return the instance root Path."""
    from research_vault.init import cmd_init_in_dir
    rc = cmd_init_in_dir(str(tmp_path))
    assert rc == 0, "rv init failed"
    return tmp_path


class TestInstanceScaffoldSharedTypesOnly:
    def test_shared_types_created_under_notes_root(self, tmp_vault):
        """literature/, concepts/, datasets/ must exist under notes/."""
        for shared_type in OKF_SHARED_TYPES:
            assert (tmp_vault / "notes" / shared_type).is_dir(), \
                f"expected notes/{shared_type}/ to exist after rv init"

    def test_no_project_type_dirs_in_instance(self, tmp_vault):
        """Project-scoped OKF types must NOT be scaffolded at the instance level."""
        stray = {
            t for t in OKF_PROJECT_TYPES
            if (tmp_vault / "notes" / t).is_dir()
        }
        assert not stray, (
            f"instance scaffold created project-type dirs it must not own: {stray} "
            f"(notes/{{{','.join(sorted(stray))}}}) — these belong under a "
            "project's source_dir, never the instance"
        )

    def test_no_stale_pre_rename_methods_dir(self, tmp_vault):
        """The pre-0.3.2 `methods/` name must never reappear anywhere in a fresh scaffold."""
        assert not (tmp_vault / "notes" / "methods").exists(), \
            "stale pre-rename `methods/` dir must never be created"


class TestProjectScaffoldUnaffected:
    def test_project_new_still_creates_full_okf_set_at_source_root(self, tmp_path, monkeypatch):
        """`rv project new` must still create ALL OKF types directly under
        the project's source_dir root (no `notes/` prefix) — this fix must
        not regress project scaffolding."""
        from research_vault.init import cmd_init_in_dir
        from research_vault import project as project_mod
        from research_vault.config import reset_config_cache
        from research_vault.note import OKF_TYPES

        instance_dir = tmp_path / "instance"
        rc = cmd_init_in_dir(str(instance_dir))
        assert rc == 0

        config_path = instance_dir / "research_vault.toml"
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_path))
        reset_config_cache()

        source_dir = tmp_path / "demo-project"
        rc = project_mod.cmd_new(
            name="demo-project",
            code="dp",
            source_dir=str(source_dir),
            roster=[],
            config_path=config_path,
        )
        reset_config_cache()
        assert rc == 0, "rv project new failed"

        for note_type in OKF_TYPES:
            assert (source_dir / note_type).is_dir(), \
                f"expected {note_type}/ directly under source_dir root, no notes/ prefix"
            assert not (source_dir / "notes" / note_type).exists(), \
                "project scaffold must never write under a notes/ prefix"
