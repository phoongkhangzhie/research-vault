"""test_sr_lens_rm.py — SR-LENS-RM acceptance tests.

One general vault-level crew; project context read fresh.

Acceptance criteria:
1. Hat body is NON-EMPTY and carries charter + role doctrine (latent-bug fix).
2. No CONTRACT anywhere: rv init produces no CONTRACT.md, no CONTRACT.md.tmpl.
3. rv project new: produces pointers.md skeleton, NO CONTRACT, no per-project hats.
4. Vault-level crew: build-agents writes flat .agents/<role>.md (not per-project).
5. rv build-agents --project flag is gone.
7. Shipped doctrine contains NO removed-mechanism terms (Wren grep-guard).
6. rv status echoes "Pointers:" line when pointers.md is present.

All tests are hermetic (tmp_path only; zero ~/vault).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import load_config, reset_config_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_vault(tmp_path):
    """Scaffold a fresh rv init instance; return instance root Path."""
    from research_vault.init import cmd_init_in_dir
    rc = cmd_init_in_dir(str(tmp_path))
    assert rc == 0, "rv init failed"
    return tmp_path


@pytest.fixture()
def rv_instance(tmp_path):
    """Minimal RV instance — config wired, no demo projects registered."""
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
    if old is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old
    reset_config_cache()


# ---------------------------------------------------------------------------
# 1. Hat body contains charter + role (the latent-bug fix)
# ---------------------------------------------------------------------------

class TestHatBodyCharterAndRole:
    """Hats must be non-empty and carry both charter text and role doctrine.

    Today's code (pre-fix) composes only the CONTRACT — the charter and role
    docs are NEVER read.  These tests are RED on the current code.
    """

    def _cc_agents(self, vault: Path) -> Path:
        return vault / ".claude" / "agents"

    def test_engineer_hat_body_non_empty(self, tmp_vault):
        """engineer.md body must be substantive (> 100 chars)."""
        body = self._get_body(tmp_vault, "engineer")
        assert len(body.strip()) > 100, f"engineer hat body suspiciously short: {len(body.strip())} chars"

    def test_engineer_hat_contains_charter_phrase(self, tmp_vault):
        """engineer.md must contain a recognisable charter phrase."""
        body = self._get_body(tmp_vault, "engineer")
        # "Grounding — never fabricate." is in agent-charter.md
        assert "never fabricate" in body.lower(), (
            "engineer hat does NOT contain a charter phrase ('never fabricate'). "
            "This confirms the latent bug: the hat body was just a CONTRACT, "
            "never the charter+role doc."
        )

    def test_engineer_hat_contains_role_phrase(self, tmp_vault):
        """engineer.md must contain a phrase from the engineer (mason.md) role doc."""
        body = self._get_body(tmp_vault, "engineer")
        # "mode is to build" is from mason.md
        assert "mode is to build" in body.lower(), (
            "engineer hat does NOT contain the mason role-doc phrase ('mode is to build'). "
            "Charter+role composition is missing."
        )

    def test_architect_hat_contains_role_phrase(self, tmp_vault):
        """architect.md must contain a phrase from the wren role doc."""
        body = self._get_body(tmp_vault, "architect")
        assert "architect" in body.lower() or "stack" in body.lower() or "coherence" in body.lower(), \
            "architect hat does not contain wren role-doc content"

    def test_hat_body_contains_read_fresh_footer(self, tmp_vault):
        """Every hat must contain the read-fresh footer (not baked context)."""
        body = self._get_body(tmp_vault, "engineer")
        # The footer references rv status and pointers.md
        assert "rv status" in body and "pointers.md" in body, (
            "hat body missing the read-fresh footer "
            "(should mention 'rv status' and 'pointers.md')"
        )

    def test_hat_body_contains_no_contract_block(self, tmp_vault):
        """No hat should contain a CONTRACT block."""
        for role in ("engineer", "researcher", "designer", "reviewer", "architect"):
            body = self._get_body(tmp_vault, role)
            assert "# Current project lens (CONTRACT)" not in body, \
                f"{role}.md hat contains a CONTRACT block — should carry charter+role only"
            assert "CONTRACT.md" not in body, \
                f"{role}.md hat references CONTRACT.md"

    def _get_body(self, vault: Path, role: str) -> str:
        md = self._cc_agents(vault) / f"{role}.md"
        assert md.is_file(), f"{role}.md not found in .claude/agents/"
        text = md.read_text(encoding="utf-8")
        # Strip YAML frontmatter
        assert text.startswith("---\n"), f"{role}.md missing frontmatter"
        end = text.index("\n---\n", 4)
        return text[end + 5:]


# ---------------------------------------------------------------------------
# 2. No CONTRACT anywhere after rv init
# ---------------------------------------------------------------------------

class TestNoContractAfterInit:
    """rv init must produce ZERO contract files."""

    def test_no_contract_md_in_agents_dir(self, tmp_vault):
        """.agents/ must not contain any CONTRACT.md file."""
        contract_files = list((tmp_vault / ".agents").rglob("CONTRACT.md"))
        assert not contract_files, (
            f"rv init wrote CONTRACT.md files: {contract_files}\n"
            "SR-LENS-RM: no CONTRACTs should exist after init."
        )

    def test_no_contract_template_in_data(self):
        """CONTRACT.md.tmpl must NOT exist in the package data dir."""
        tmpl_path = (
            Path(__file__).parent.parent
            / "src" / "research_vault" / "data" / "templates" / "CONTRACT.md.tmpl"
        )
        assert not tmpl_path.exists(), (
            f"CONTRACT.md.tmpl still exists at {tmpl_path}. "
            "SR-LENS-RM: delete this file."
        )

    def test_no_demo_contracts_in_examples(self):
        """Demo example dirs must NOT contain pre-filled CONTRACT.md files."""
        examples_data = (
            Path(__file__).parent.parent
            / "src" / "research_vault" / "data" / "examples"
        )
        contract_files = list(examples_data.rglob("CONTRACT.md"))
        assert not contract_files, (
            f"Demo CONTRACT.md files still exist: {contract_files}. "
            "SR-LENS-RM: delete these files."
        )

    def test_no_demo_contracts_in_vault_instance(self, tmp_vault):
        """No CONTRACT.md anywhere in the scaffolded instance tree."""
        contract_files = list(tmp_vault.rglob("CONTRACT.md"))
        assert not contract_files, (
            f"CONTRACT.md files found in scaffolded instance: {contract_files}"
        )


# ---------------------------------------------------------------------------
# 3. rv project new: pointers.md yes, CONTRACT no, no per-project hats
# ---------------------------------------------------------------------------

class TestProjectNewPointersMdAndNoContract:
    """rv project new must scaffold pointers.md; must NOT scaffold CONTRACT.md."""

    def test_pointers_md_created(self, rv_instance):
        """rv project new must create pointers.md in the project source dir."""
        from research_vault.project import cmd_new
        src = rv_instance / "projects" / "testproj"
        rc = cmd_new("testproj", "tp", str(src), [])
        assert rc == 0
        pointers_path = src / "pointers.md"
        assert pointers_path.exists(), (
            f"pointers.md not found at {pointers_path}. "
            "SR-LENS-RM: rv project new must scaffold pointers.md."
        )

    def test_pointers_md_is_non_empty_skeleton(self, rv_instance):
        """pointers.md must be a non-empty skeleton (not a fill-gate)."""
        from research_vault.project import cmd_new
        src = rv_instance / "projects" / "testproj"
        cmd_new("testproj", "tp", str(src), [])
        text = (src / "pointers.md").read_text(encoding="utf-8")
        assert len(text.strip()) > 10, "pointers.md is empty or near-empty"

    def test_pointers_md_has_no_fill_gate(self, rv_instance):
        """pointers.md must NOT have FILL-gate markers (no authoring burden)."""
        from research_vault.project import cmd_new
        src = rv_instance / "projects" / "testproj"
        cmd_new("testproj", "tp", str(src), [])
        text = (src / "pointers.md").read_text(encoding="utf-8")
        assert "<!-- FILL" not in text, \
            "pointers.md must not have FILL markers — no authoring burden."

    def test_no_contract_md_after_project_new(self, rv_instance):
        """rv project new must NOT scaffold CONTRACT.md anywhere."""
        from research_vault.project import cmd_new
        src = rv_instance / "projects" / "testproj"
        rc = cmd_new("testproj", "tp", str(src), [])
        assert rc == 0
        contract_files = list(rv_instance.rglob("CONTRACT.md"))
        assert not contract_files, (
            f"CONTRACT.md was created by rv project new: {contract_files}. "
            "SR-LENS-RM: no CONTRACTs should be created."
        )

    def test_no_per_project_agents_dir_after_project_new(self, rv_instance):
        """rv project new must NOT create .agents/<slug>/ directory."""
        from research_vault.project import cmd_new
        src = rv_instance / "projects" / "testproj"
        rc = cmd_new("testproj", "tp", str(src), [])
        assert rc == 0
        per_proj_agents = rv_instance / ".agents" / "testproj"
        assert not per_proj_agents.exists(), (
            f".agents/testproj/ was created by rv project new at {per_proj_agents}. "
            "SR-LENS-RM: crew is vault-level; no per-project hat bake."
        )

    def test_next_steps_mentions_pointers_md(self, rv_instance, capsys):
        """Next steps output must mention pointers.md (not CONTRACT)."""
        from research_vault.project import cmd_new
        src = rv_instance / "projects" / "testproj"
        cmd_new("testproj", "tp", str(src), [])
        captured = capsys.readouterr()
        assert "CONTRACT" not in captured.out, \
            "next steps must not mention CONTRACT (it's gone)"
        assert "pointers.md" in captured.out, \
            "next steps must mention pointers.md as the project-context file"


# ---------------------------------------------------------------------------
# 4. Vault-level crew: flat build, no --project flag
# ---------------------------------------------------------------------------

class TestVaultLevelCrew:
    """build-agents writes flat .agents/<role>.md; no --project flag."""

    def test_build_agents_has_no_project_flag(self):
        """build-agents argparse must NOT have a --project flag."""
        from research_vault.build_agents import build_parser
        p = build_parser()
        # If --project exists, parse_known_args will succeed; if not, it's unknown
        _, unknown = p.parse_known_args(["--project", "demo"])
        assert "--project" in unknown or any("project" in u for u in unknown), (
            "build-agents still has a --project flag. SR-LENS-RM: remove it."
        )

    def test_build_agents_cc_writes_flat_files(self, tmp_vault):
        """build-agents --target claude-code writes .claude/agents/<role>.md (flat)."""
        # The cc agents must already exist from rv init auto-build
        cc_dir = tmp_vault / ".claude" / "agents"
        assert cc_dir.is_dir(), ".claude/agents/ must exist"
        roles = {f.stem for f in cc_dir.glob("*.md")}
        expected = {"engineer", "researcher", "designer", "reviewer", "architect"}
        assert roles == expected, f"Expected flat 5-role crew, got: {sorted(roles)}"

    def test_build_agents_dir_writes_flat_hats(self, tmp_vault):
        """build-agents (agents-dir target) writes flat .agents/<role>.md."""
        import os
        from research_vault.build_agents import cmd_build
        config_path = tmp_vault / "research_vault.toml"
        os.environ["RESEARCH_VAULT_CONFIG"] = str(config_path)
        try:
            cfg = load_config(reload=True)
            rc = cmd_build(cfg=cfg, target="agents-dir")
            assert rc == 0
        finally:
            del os.environ["RESEARCH_VAULT_CONFIG"]
            reset_config_cache()

        agents_dir = tmp_vault / ".agents"
        roles = {f.stem for f in agents_dir.glob("*.md")}
        expected = {"engineer", "researcher", "designer", "reviewer", "architect"}
        assert roles == expected, (
            f"agents-dir build must write flat 5-role files, got: {sorted(roles)}"
        )


# ---------------------------------------------------------------------------
# 5. rv status echoes "Pointers:" line
# ---------------------------------------------------------------------------

class TestRvStatusPointersEcho:
    """rv status must echo a 'Pointers:' line when pointers.md is present."""

    def test_status_echoes_pointers_line_when_present(self, rv_instance):
        """When a project has pointers.md, rv status must echo a 'Pointers:' line."""
        from research_vault.project import cmd_new
        from research_vault.status import cmd_status

        src = rv_instance / "projects" / "testproj"
        rc = cmd_new("testproj", "tp", str(src), [])
        assert rc == 0
        # pointers.md is now in the source dir
        pointers_path = src / "pointers.md"
        assert pointers_path.exists()

        reset_config_cache()
        cfg = load_config(reload=True)
        output = cmd_status("testproj", config=cfg)
        assert "Pointers:" in output, (
            "rv status must echo a 'Pointers:' line when pointers.md is present.\n"
            f"Output was:\n{output}"
        )

    def test_status_shows_no_pointers_line_when_absent(self, rv_instance):
        """When pointers.md is absent, rv status echoes 'Pointers: none yet'."""
        from research_vault.project import cmd_add
        from research_vault.status import cmd_status

        src = rv_instance / "projects" / "testproj"
        src.mkdir(parents=True, exist_ok=True)
        cmd_add("testproj", "tp", str(src), [],
                config_path=rv_instance / "research_vault.toml")
        # Do NOT create pointers.md

        reset_config_cache()
        cfg = load_config(reload=True)
        output = cmd_status("testproj", config=cfg)
        # Should still echo the Pointers line (with a "none yet" note)
        assert "Pointers:" in output, (
            "rv status must always echo a 'Pointers:' line, "
            "even when no pointers.md exists (to tell the crew where to put it)."
        )


# ---------------------------------------------------------------------------
# 6. rv check has no CONTRACT integrity surface
# ---------------------------------------------------------------------------

class TestCheckNoContractIntegrity:
    """rv check must have no CONTRACT integrity check."""

    def test_check_report_no_contract_section(self, rv_instance):
        """rv check report must NOT mention 'Project integrity' CONTRACT section."""
        from research_vault.check import run_preflight
        reset_config_cache()
        cfg = load_config(reload=True)
        result = run_preflight(cfg=cfg)
        report = result["report"]
        # The old "Project integrity" / "CONTRACT" section must be gone
        assert "Project integrity" not in report, \
            "rv check still emits a 'Project integrity' (CONTRACT) section — delete it."
        assert "CONTRACT" not in report, \
            "rv check report still mentions CONTRACT — the integrity check must be removed."


# ---------------------------------------------------------------------------
# 7. Shipped doctrine grep-guard: no removed-mechanism terms (Wren fit-check)
# ---------------------------------------------------------------------------

class TestDoctrineNoContractTerms:
    """Shipped data/doctrine/**/*.md must not document the removed CONTRACT lens.

    Non-vacuous: FAILS on current coordination.md and atlas.md (before fixes),
    PASSES after doctrine is rewritten to the read-fresh model (SR-LENS-RM).

    This is the doctrine-drift guard — the same class Wren flagged: tests grepped
    code and one hat body, but never the shipped doctrine prose.  A mechanism
    removal without a doctrine update self-contradicts in the generated hats.
    """

    _DOCTRINE_DIR = (
        Path(__file__).parent.parent
        / "src" / "research_vault" / "data" / "doctrine"
    )

    # Terms that must NOT appear in the shipped doctrine after SR-LENS-RM.
    # These are the removed-mechanism markers:
    _FORBIDDEN_TERMS: list[tuple[str, str]] = [
        # (term, reason)
        ("CONTRACT.md", "per-project CONTRACT file is deleted"),
        ("build-agents --project", "the --project flag is removed"),
        (".agents/<slug>/CONTRACT", "per-project agents-dir structure is gone"),
        ("CONTRACT roadmap", "CONTRACT-specific roadmap concept is removed"),
        ("CONTRACT — the project lens", "the lens section heading is removed"),
    ]

    def _scan_doctrine(self) -> list[tuple[str, int, str, str]]:
        """Scan all doctrine .md files for forbidden terms.

        Returns [(relpath, lineno, term, line)] for each hit.
        """
        hits = []
        doctrine_dir = self._DOCTRINE_DIR
        for md_file in sorted(doctrine_dir.rglob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                for term, reason in self._FORBIDDEN_TERMS:
                    if term in line:
                        rel = str(md_file.relative_to(doctrine_dir))
                        hits.append((rel, lineno, term, line.strip()))
        return hits

    def test_no_contract_terms_in_shipped_doctrine(self):
        """No removed CONTRACT mechanism terms in shipped data/doctrine/**/*.md.

        RED before doctrine fix (coordination.md + atlas.md still reference
        CONTRACT); GREEN after the doctrine is rewritten to the read-fresh model.
        """
        assert self._DOCTRINE_DIR.is_dir(), \
            f"Doctrine dir not found: {self._DOCTRINE_DIR}"

        hits = self._scan_doctrine()
        if hits:
            lines = []
            for rel, lineno, term, line in hits:
                lines.append(f"  doctrine/{rel}:{lineno}: term={term!r}")
                lines.append(f"    → {line[:120]}")
            raise AssertionError(
                f"\n{len(hits)} removed-mechanism term(s) found in shipped doctrine.\n"
                "These terms document the deleted CONTRACT lens and must be removed:\n"
                + "\n".join(lines)
            )

    def test_coordination_md_has_read_fresh_section(self):
        """coordination.md must document the read-fresh project-context model."""
        coord_md = self._DOCTRINE_DIR / "coordination.md"
        assert coord_md.is_file(), f"coordination.md not found at {coord_md}"
        text = coord_md.read_text(encoding="utf-8")
        # After SR-LENS-RM the section must describe pointers.md and rv status
        assert "pointers.md" in text, \
            "coordination.md must document pointers.md as the project-context file"
        assert "rv status" in text, \
            "coordination.md must reference rv status as the read-fresh path"

    def test_manager_role_doc_deleted(self):
        """manager.md must NOT exist — manager role removed; hub coordinates directly."""
        manager_md = self._DOCTRINE_DIR / "roles" / "manager.md"
        assert not manager_md.is_file(), \
            "manager.md still exists — it should have been deleted (hub coordinates directly)"
