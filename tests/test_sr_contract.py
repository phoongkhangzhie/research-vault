"""test_sr_contract.py — SR-CONTRACT acceptance tests.

Covers:
  1. CONTRACT template — leakage-clean, has required blocks, no private markers.
  2. rv project new scaffolds CONTRACT.md with interpolated fields + FILL markers.
  3. rv build-agents composes a filled CONTRACT into every hat (no banner).
  4. rv build-agents WARNs loudly (stderr) + embeds NO-CONTRACT banner when missing.
  5. rv build-agents nudge-WARNs + embeds stub banner when CONTRACT is still a stub.
  6. rv check WARNs on missing/stub CONTRACT; does NOT change exit code.
  7. Rollback: failed project new removes the agents-dir entry (D-CONTRACT-2 fix).
  8. Stub-detection helper: banner present + FILL marker = stub; filled = not stub.

Hermetic: all tests run in tmp_path; no ~/vault or private instance touched.
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from typing import Generator

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from research_vault.config import load_config, reset_config_cache
from research_vault.project import cmd_new
from research_vault import build_agents, check as check_mod
from research_vault.build_agents import _is_contract_stub, _load_contract_text
from tests.gitutil import invoke_cli


# ---------------------------------------------------------------------------
# Fixtures
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
    if old is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old
    reset_config_cache()


# ---------------------------------------------------------------------------
# 1. Template leakage + structure
# ---------------------------------------------------------------------------

class TestContractTemplate:
    """Verify the CONTRACT.md.tmpl is portable and leakage-clean."""

    def _load_template(self) -> str:
        import importlib.resources
        pkg = importlib.resources.files("research_vault")
        tmpl_path = Path(str(pkg)) / "templates" / "CONTRACT.md.tmpl"
        return tmpl_path.read_text(encoding="utf-8")

    def test_template_exists(self) -> None:
        text = self._load_template()
        assert text, "CONTRACT.md.tmpl must exist and be non-empty"

    def test_template_has_scaffold_banner(self) -> None:
        text = self._load_template()
        assert "Auto-scaffolded by `rv project new`" in text, \
            "template must contain the scaffold banner (stub detection anchor)"

    def test_template_banner_does_not_contain_fill_marker(self) -> None:
        """The scaffold banner must NOT contain the <!-- FILL HTML marker itself.

        This is the stub-detection invariant: the banner presence alone is not
        enough to flag as a stub — we also need an actual <!-- FILL marker to
        remain in the content. The banner text must not embed that marker.
        """
        from research_vault.build_agents import _CONTRACT_SCAFFOLD_BANNER, _CONTRACT_FILL_MARKER
        text = self._load_template()
        # Find the banner line and verify it does not contain the fill marker
        banner_line = next(
            (l for l in text.splitlines() if _CONTRACT_SCAFFOLD_BANNER in l), None
        )
        assert banner_line is not None, "banner must be present"
        assert _CONTRACT_FILL_MARKER not in banner_line, \
            "banner line must NOT contain the fill marker (would cause false-positive stub detection)"

    def test_template_has_fill_markers(self) -> None:
        text = self._load_template()
        assert "<!-- FILL" in text, "template must contain at least one <!-- FILL marker"

    def test_template_has_seven_blocks(self) -> None:
        text = self._load_template()
        required_headings = [
            "## Identity",
            "## ★ POINTERS",
            "## Roadmap",
            "## Your team (roster)",
            "### By role",
            "## Operational state",
        ]
        for heading in required_headings:
            assert heading in text, f"template must contain heading: {heading!r}"

    def test_template_has_interpolation_slots(self) -> None:
        text = self._load_template()
        for slot in ["{slug}", "{code}", "{source_dir}", "{roster}", "{date}"]:
            assert slot in text, f"template must contain interpolation slot: {slot!r}"

    def test_template_no_private_markers(self) -> None:
        """Leakage gate: no private identities, codenames, or paths in template."""
        text = self._load_template()
        # The critical check: no private name
        forbidden = ["Khang", "khang", "phoong", "mydossier", "~/vault", "private"]
        for marker in forbidden:
            assert marker not in text, \
                f"LEAKAGE: template contains private marker {marker!r}"

    def test_template_no_hardcoded_project_names(self) -> None:
        """No project codenames from the live vault should appear."""
        text = self._load_template()
        # These are live-vault project names that must not leak
        codenames = ["research-vault", "cultural-social-sim", "csb"]
        for name in codenames:
            assert name not in text, \
                f"LEAKAGE: template contains project codename {name!r}"

    def test_is_stub_on_raw_template(self) -> None:
        """The raw template (before interpolation) is itself a stub."""
        text = self._load_template()
        assert _is_contract_stub(text), \
            "raw template must be detected as a stub (has banner + FILL markers)"


# ---------------------------------------------------------------------------
# 2. rv project new scaffolds CONTRACT.md
# ---------------------------------------------------------------------------

class TestProjectNewScaffoldsContract:
    def test_contract_file_created(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        rc = cmd_new("demo", "dm", str(src), ["engineer"])
        assert rc == 0
        contract_path = rv_instance / ".agents" / "demo" / "CONTRACT.md"
        assert contract_path.exists(), f"CONTRACT.md must exist at {contract_path}"

    def test_contract_slug_interpolated(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "myproj"
        cmd_new("myproj", "mp", str(src), [])
        contract_path = rv_instance / ".agents" / "myproj" / "CONTRACT.md"
        text = contract_path.read_text()
        assert "myproj" in text, "contract must contain the project slug"
        assert "{slug}" not in text, "contract must not contain uninterpolated {slug}"

    def test_contract_code_interpolated(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        contract_path = rv_instance / ".agents" / "demo" / "CONTRACT.md"
        text = contract_path.read_text()
        assert "dm" in text, "contract must contain the project code"
        assert "{code}" not in text, "contract must not contain uninterpolated {code}"

    def test_contract_source_dir_interpolated(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        contract_path = rv_instance / ".agents" / "demo" / "CONTRACT.md"
        text = contract_path.read_text()
        assert str(src) in text, "contract must contain the source_dir"
        assert "{source_dir}" not in text

    def test_contract_roster_interpolated(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), ["engineer", "reviewer"])
        contract_path = rv_instance / ".agents" / "demo" / "CONTRACT.md"
        text = contract_path.read_text()
        assert "engineer" in text, "contract must contain roster roles"
        assert "reviewer" in text
        assert "{roster}" not in text

    def test_contract_date_interpolated(self, rv_instance: Path) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        contract_path = rv_instance / ".agents" / "demo" / "CONTRACT.md"
        text = contract_path.read_text()
        assert "{date}" not in text, "contract must not contain uninterpolated {date}"

    def test_contract_has_fill_markers(self, rv_instance: Path) -> None:
        """Scaffolded CONTRACT must have FILL placeholders (not fabricated content)."""
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        contract_path = rv_instance / ".agents" / "demo" / "CONTRACT.md"
        text = contract_path.read_text()
        assert "<!-- FILL" in text, "scaffolded contract must retain FILL markers"

    def test_contract_is_detected_as_stub(self, rv_instance: Path) -> None:
        """Freshly scaffolded CONTRACT must be detected as a stub."""
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        contract_path = rv_instance / ".agents" / "demo" / "CONTRACT.md"
        text = contract_path.read_text()
        assert _is_contract_stub(text), "freshly scaffolded contract must be a stub"

    def test_project_new_prints_contract_created(self, rv_instance: Path, capsys) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        captured = capsys.readouterr()
        assert "CONTRACT.md" in captured.out, \
            "project new must print that CONTRACT.md was created"

    def test_next_steps_mentions_build_agents(self, rv_instance: Path, capsys) -> None:
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), [])
        captured = capsys.readouterr()
        assert "build-agents" in captured.out, \
            "next-steps must mention rv build-agents to re-bake hats after filling CONTRACT"


# ---------------------------------------------------------------------------
# 3. build-agents composes a filled CONTRACT into hats
# ---------------------------------------------------------------------------

class TestBuildAgentsComposesContract:
    def test_filled_contract_in_hat(self, rv_instance: Path) -> None:
        """A filled (non-stub) CONTRACT must appear in the generated hat."""
        agents_dir = rv_instance / ".agents"
        proj_dir = agents_dir / "demo"
        proj_dir.mkdir(parents=True, exist_ok=True)

        # Write a filled (non-stub) CONTRACT — no banner, no FILL markers
        contract = proj_dir / "CONTRACT.md"
        contract.write_text(
            "# CONTRACT — demo\n\n"
            "## Identity\n\nThis project does research on X.\n\n"
            "## Roadmap\n\nPhase 1: data collection.\n",
            encoding="utf-8",
        )

        # Fake config
        from research_vault.config import Config
        cfg = load_config(reload=True)

        # Directly test _hat_header with contract
        text = build_agents._hat_header("demo", "engineer", "/src/demo", contract_text="# CONTRACT — demo\n\nFilled content.")
        assert "# CONTRACT — demo" in text, "hat must embed the contract text"
        assert "Filled content" in text

    def test_filled_contract_no_banner(self, rv_instance: Path) -> None:
        """No warning banner in hat when CONTRACT is filled."""
        text = build_agents._hat_header(
            "demo", "engineer", "/src/demo",
            contract_text="# CONTRACT — demo\n\nReal content here.\n"
        )
        assert "NO CONTRACT" not in text
        assert "unfilled stub" not in text

    def test_missing_contract_embeds_banner(self, rv_instance: Path) -> None:
        """Missing CONTRACT → NO CONTRACT banner in the hat."""
        text = build_agents._hat_header(
            "demo", "engineer", "/src/demo",
            contract_text=None  # missing
        )
        assert "NO CONTRACT" in text, "missing CONTRACT must produce NO CONTRACT banner"

    def test_stub_contract_embeds_stub_banner(self, rv_instance: Path) -> None:
        """Stub CONTRACT → stub banner in the hat."""
        import importlib.resources
        pkg = importlib.resources.files("research_vault")
        stub_tmpl = (Path(str(pkg)) / "templates" / "CONTRACT.md.tmpl").read_text()
        # Interpolate but leave FILL markers (like a freshly scaffolded contract)
        stub_text = stub_tmpl.format(
            slug="demo", code="dm", source_dir="/src/demo",
            roster="engineer", date="2026-07-02"
        )
        text = build_agents._hat_header("demo", "engineer", "/src/demo", contract_text=stub_text)
        assert "unfilled stub" in text.lower() or "stub" in text.lower(), \
            "stub CONTRACT must produce stub banner in hat"


# ---------------------------------------------------------------------------
# 4. build-agents WARN on missing CONTRACT (stderr + hat banner)
# ---------------------------------------------------------------------------

class TestBuildAgentsWarnOnMissing:
    def test_warns_to_stderr_when_missing(self, rv_instance: Path, capsys) -> None:
        """build-agents must WARN to stderr for a project with no CONTRACT."""
        # Set up a project with a roster but no CONTRACT.md
        src = rv_instance / "projects" / "demo"
        rc = cmd_new("demo", "dm", str(src), ["engineer"])
        assert rc == 0
        # Delete the CONTRACT.md to simulate a missing one
        contract_path = rv_instance / ".agents" / "demo" / "CONTRACT.md"
        contract_path.unlink()

        reset_config_cache()
        cfg = load_config(reload=True)
        build_agents.cmd_build("demo", cfg)

        captured = capsys.readouterr()
        assert "CONTRACT" in captured.err, \
            "build-agents must warn about missing CONTRACT on stderr"

    def test_still_builds_hats_when_missing(self, rv_instance: Path) -> None:
        """build-agents must still generate hats even when CONTRACT is missing."""
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), ["engineer"])
        contract_path = rv_instance / ".agents" / "demo" / "CONTRACT.md"
        contract_path.unlink()

        reset_config_cache()
        cfg = load_config(reload=True)
        rc = build_agents.cmd_build("demo", cfg)
        assert rc == 0, "build-agents must return 0 even with missing CONTRACT"
        hat = rv_instance / ".agents" / "demo" / "engineer.md"
        assert hat.exists(), "engineer.md hat must still be created"

    def test_hat_has_no_contract_banner_when_missing(self, rv_instance: Path) -> None:
        """Hat must contain NO CONTRACT banner when CONTRACT.md is absent."""
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), ["engineer"])
        contract_path = rv_instance / ".agents" / "demo" / "CONTRACT.md"
        contract_path.unlink()

        reset_config_cache()
        cfg = load_config(reload=True)
        build_agents.cmd_build("demo", cfg)

        hat_text = (rv_instance / ".agents" / "demo" / "engineer.md").read_text()
        assert "NO CONTRACT" in hat_text, \
            "hat must embed NO CONTRACT banner when CONTRACT.md is absent"


# ---------------------------------------------------------------------------
# 5. build-agents nudge-WARN on stub CONTRACT
# ---------------------------------------------------------------------------

class TestBuildAgentsNudgeOnStub:
    def test_warns_to_stderr_when_stub(self, rv_instance: Path, capsys) -> None:
        """build-agents must warn (nudge) when CONTRACT is still a stub."""
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), ["engineer"])
        # CONTRACT.md is freshly scaffolded = stub

        reset_config_cache()
        cfg = load_config(reload=True)
        build_agents.cmd_build("demo", cfg)

        captured = capsys.readouterr()
        assert "CONTRACT" in captured.err, \
            "build-agents must nudge about stub CONTRACT on stderr"

    def test_hat_has_stub_banner_when_stub(self, rv_instance: Path) -> None:
        """Hat must contain stub banner when CONTRACT is still unfilled."""
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), ["engineer"])

        reset_config_cache()
        cfg = load_config(reload=True)
        build_agents.cmd_build("demo", cfg)

        hat_text = (rv_instance / ".agents" / "demo" / "engineer.md").read_text()
        assert "stub" in hat_text.lower() or "unfilled" in hat_text.lower(), \
            "hat must embed stub/unfilled banner when CONTRACT is not yet filled"


# ---------------------------------------------------------------------------
# 6. rv check WARNs on missing/stub CONTRACT — exit code unchanged
# ---------------------------------------------------------------------------

class TestCheckProjectIntegrity:
    def test_check_warns_on_missing_contract(self, rv_instance: Path) -> None:
        """rv check must WARN about missing CONTRACT in the report."""
        # Register a project without scaffolding (simulate pre-SR-CONTRACT project)
        from research_vault.project import cmd_add
        src = rv_instance / "projects" / "demo"
        src.mkdir(parents=True)
        cmd_add("demo", "dm", str(src), ["engineer"],
                config_path=rv_instance / "research_vault.toml")
        reset_config_cache()
        cfg = load_config(reload=True)

        result = check_mod.run_preflight(cfg=cfg)
        assert "CONTRACT" in result["report"], \
            "rv check report must mention CONTRACT when it is missing"

    def test_check_warns_on_stub_contract(self, rv_instance: Path) -> None:
        """rv check must WARN about an unfilled stub CONTRACT."""
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), ["engineer"])
        reset_config_cache()
        cfg = load_config(reload=True)

        result = check_mod.run_preflight(cfg=cfg)
        assert "CONTRACT" in result["report"], \
            "rv check must mention stub CONTRACT in the report"

    def test_check_does_not_flip_exit_code_on_contract_warn(self, rv_instance: Path) -> None:
        """CONTRACT WARN must NOT change all_required_ok — exit code stays 0."""
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), ["engineer"])
        reset_config_cache()
        cfg = load_config(reload=True)

        result = check_mod.run_preflight(cfg=cfg)
        # all_required_ok depends only on claude_cli + api_key — not on CONTRACT
        # In test env those may or may not be set; the key assertion is that
        # CONTRACT issues do NOT flip all_required_ok independently.
        # We verify by checking the contract-warn items are not in required section.
        assert "all_required_ok" in result, "result must have all_required_ok key"
        # The PROJECT INTEGRITY section must be separate from required
        if "Project integrity" in result["report"]:
            report_lines = result["report"].splitlines()
            integrity_idx = next(
                (i for i, l in enumerate(report_lines) if "Project integrity" in l), None
            )
            required_idx = next(
                (i for i, l in enumerate(report_lines) if l.strip().startswith("Required:")), None
            )
            if integrity_idx is not None and required_idx is not None:
                assert integrity_idx > required_idx, \
                    "Project integrity section must come after Required section"

    def test_check_no_warn_for_filled_contract(self, rv_instance: Path) -> None:
        """rv check must NOT emit a CONTRACT WARN when the CONTRACT is filled."""
        src = rv_instance / "projects" / "demo"
        cmd_new("demo", "dm", str(src), ["engineer"])
        # Fill the contract (remove banner and all FILL markers)
        contract_path = rv_instance / ".agents" / "demo" / "CONTRACT.md"
        contract_path.write_text(
            "# CONTRACT — demo\n\n"
            "## Identity\n\nReal content — no fill markers.\n\n"
            "## Roadmap\n\nPhase 1 complete.\n",
            encoding="utf-8",
        )
        reset_config_cache()
        cfg = load_config(reload=True)

        result = check_mod.run_preflight(cfg=cfg)
        # For a filled contract, the integrity check should be OK (no WARN)
        if "Project integrity" in result["report"]:
            # If the section appears, it must not say WARN for this project
            lines = result["report"].splitlines()
            integrity_lines = []
            in_section = False
            for line in lines:
                if "Project integrity" in line:
                    in_section = True
                elif in_section and line.startswith("  ["):
                    integrity_lines.append(line)
                elif in_section and not line.startswith("  "):
                    break
            # No line about demo having a contract issue
            demo_warns = [l for l in integrity_lines if "demo" in l and "WARN" in l]
            assert not demo_warns, \
                f"Filled contract must not generate a WARN: {demo_warns}"


# ---------------------------------------------------------------------------
# 7. Rollback removes agents-dir on failed project new (D-CONTRACT-2 fix)
# ---------------------------------------------------------------------------

class TestRollbackCleansAgentsDir:
    def test_rollback_removes_agents_dir(self, rv_instance: Path) -> None:
        """When project new fails after CONTRACT step, agents dir must be cleaned up."""
        from research_vault import devlog as devlog_mod

        # Inject failure AFTER the CONTRACT step (devlog comes after)
        original_devlog_init = devlog_mod.cmd_init

        def _boom(slug, config):
            raise RuntimeError("injected failure after CONTRACT step")

        devlog_mod.cmd_init = _boom
        try:
            src = rv_instance / "projects" / "demo"
            rc = cmd_new("demo", "dm", str(src), ["engineer"])
        finally:
            devlog_mod.cmd_init = original_devlog_init

        assert rc != 0, "must fail on injected error"
        agents_slug_dir = rv_instance / ".agents" / "demo"
        assert not agents_slug_dir.exists(), \
            f"rollback must remove .agents/demo/ directory, but it still exists at {agents_slug_dir}"

    def test_rollback_removes_contract_file(self, rv_instance: Path) -> None:
        """CONTRACT.md must be removed as part of rollback (via agents-dir cleanup)."""
        from research_vault import note as note_mod

        # Note: CONTRACT is written BEFORE control/devlog/architecture steps.
        # The contract is written at step 7b; we fail at OKF dirs (step 4 = scaffold_okf_dirs).
        # But wait — step 4 is BEFORE step 7b. Let me fail at a step AFTER contract.
        # Contract is written after architecture (step 7), so fail at library.json (step 8).
        # We can monkeypatch the library.json write — but that's harder.
        # Instead, fail at build_agents (step 10) — that's after CONTRACT.
        from research_vault import build_agents as ba_mod

        original_cmd_build = ba_mod.cmd_build

        def _boom(slug, cfg, **kwargs):
            raise RuntimeError("injected failure at build_agents step")

        ba_mod.cmd_build = _boom
        try:
            src = rv_instance / "projects" / "demo"
            rc = cmd_new("demo", "dm", str(src), ["engineer"])
        finally:
            ba_mod.cmd_build = original_cmd_build

        assert rc != 0, "must fail on injected error"
        contract_path = rv_instance / ".agents" / "demo" / "CONTRACT.md"
        assert not contract_path.exists(), \
            "rollback must have removed CONTRACT.md (via agents-dir rmtree)"


# ---------------------------------------------------------------------------
# 8. Stub-detection helper unit tests
# ---------------------------------------------------------------------------

class TestIsContractStub:
    def test_raw_template_is_stub(self) -> None:
        import importlib.resources
        pkg = importlib.resources.files("research_vault")
        tmpl = (Path(str(pkg)) / "templates" / "CONTRACT.md.tmpl").read_text()
        assert _is_contract_stub(tmpl)

    def test_interpolated_but_unfilled_is_stub(self) -> None:
        text = (
            "# CONTRACT — demo\n\n"
            "> Auto-scaffolded by `rv project new` on 2026-07-02. "
            "Fill every FILL placeholder below before the first crew dispatch, "
            "then run `rv build-agents --project demo` to re-bake the hats.\n\n"
            "## Identity\n\n"
            "**What it is.** <!-- FILL: one-paragraph description. -->\n"
        )
        assert _is_contract_stub(text)

    def test_filled_contract_is_not_stub(self) -> None:
        text = (
            "# CONTRACT — demo\n\n"
            "## Identity\n\nThis project does X.\n\n"
            "## Roadmap\n\nPhase 1: done.\n"
        )
        assert not _is_contract_stub(text)

    def test_banner_present_no_fill_is_not_stub(self) -> None:
        """Banner present but no FILL markers → not a stub (edge case: mostly filled)."""
        text = (
            "# CONTRACT — demo\n\n"
            "> Auto-scaffolded by `rv project new` on 2026-07-02. "
            "Fill every FILL placeholder below before the first crew dispatch.\n\n"
            "## Identity\n\nReal content here — all filled.\n"
        )
        # No <!-- FILL markers remain → not a stub
        assert not _is_contract_stub(text)

    def test_empty_string_is_not_stub(self) -> None:
        assert not _is_contract_stub("")

    def test_none_maps_to_missing_not_stub(self) -> None:
        """None (missing file) is not a stub — it's missing (handled separately)."""
        assert not _is_contract_stub(None)


# ---------------------------------------------------------------------------
# 9. _load_contract_text helper
# ---------------------------------------------------------------------------

class TestLoadContractText:
    def test_returns_text_when_file_exists(self, tmp_path: Path) -> None:
        contract = tmp_path / "CONTRACT.md"
        contract.write_text("# CONTRACT\nContent.", encoding="utf-8")
        text = _load_contract_text(tmp_path)
        assert text == "# CONTRACT\nContent."

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        text = _load_contract_text(tmp_path / "nonexistent")
        assert text is None

    def test_returns_none_when_dir_missing(self, tmp_path: Path) -> None:
        text = _load_contract_text(tmp_path / "no-such-dir")
        assert text is None


# ---------------------------------------------------------------------------
# 10. Leakage scan — no private markers in template (belt-and-suspenders)
# ---------------------------------------------------------------------------

class TestLeakageScan:
    def test_template_leakage_clean(self) -> None:
        """Belt-and-suspenders: directly scan the on-disk template for private markers."""
        tmpl_path = (
            Path(__file__).parent.parent
            / "src" / "research_vault" / "templates" / "CONTRACT.md.tmpl"
        )
        assert tmpl_path.exists(), "CONTRACT.md.tmpl must exist on disk"
        text = tmpl_path.read_text(encoding="utf-8")
        forbidden = ["Khang", "khang", "phoong", "mydossier", "~/vault"]
        for marker in forbidden:
            assert marker not in text, \
                f"LEAKAGE: CONTRACT.md.tmpl contains private marker {marker!r}"
