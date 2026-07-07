"""test_orient.py — Tests for `rv orient` (the cold-context-switch orientation verb).

Hermetic: all tests run in tmp_path, never touch ~/vault or any private instance.

Acceptance:
  1. On a project WITH pointers.md + architecture.md: `rv orient` prints the
     operational `rv status` read PLUS the FULL pointers.md content (not just
     the head) PLUS the architecture.md head.
  2. On a project WITHOUT those artifacts: `rv orient` prints a graceful nudge
     naming the path to create them — never a crash / traceback.
  3. Registered in `_VERB_REGISTRY` with a non-empty `when_to_use` that names
     the cold-switch/context-switch trigger; `rv help --check` stays green.
  4. `orient` appears in the CLI help phase-grouping (discoverable via `rv help`).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Generator

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import load_config, reset_config_cache
from research_vault.project import cmd_add


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def rv_instance(tmp_path: Path) -> Generator[Path, None, None]:
    """Minimal RV instance — config wired, no projects registered."""
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


@pytest.fixture
def covered_project(rv_instance: Path) -> str:
    """A registered project WITH pointers.md + architecture.md on disk."""
    src = rv_instance / "projects" / "covered"
    src.mkdir(parents=True, exist_ok=True)
    (src / "pointers.md").write_text(
        "# Pointers — covered\n\n"
        "## Identity\n\nA covered demo project.\n\n"
        "## ★ POINTERS\n\n- design-of-record: /tmp/design.md\n\n"
        "## Roadmap\n\n- MERGED: step 1\n\n"
        "## Team\n\n- engineer\n",
        encoding="utf-8",
    )
    (src / "architecture.md").write_text(
        "# Architecture — covered\n\n"
        "## Overview\n\nThe covered demo project's architecture.\n"
        + "\n".join(f"line {i}" for i in range(100)),
        encoding="utf-8",
    )
    cmd_add(name="covered", code="cov", source_dir=str(src), roster=["engineer"],
             config_path=rv_instance / "research_vault.toml")
    reset_config_cache()
    return "covered"


@pytest.fixture
def uncovered_project(rv_instance: Path) -> str:
    """A registered project WITHOUT pointers.md / architecture.md."""
    src = rv_instance / "projects" / "uncovered"
    src.mkdir(parents=True, exist_ok=True)
    cmd_add(name="uncovered", code="unc", source_dir=str(src), roster=["engineer"],
            config_path=rv_instance / "research_vault.toml")
    reset_config_cache()
    return "uncovered"


# ---------------------------------------------------------------------------
# 1/2. cmd_orient behavior
# ---------------------------------------------------------------------------

class TestCmdOrient:
    def test_covered_project_prints_full_pointers_and_architecture_head(
        self, covered_project, rv_instance
    ):
        from research_vault.orient import cmd_orient

        cfg = load_config()
        out = cmd_orient(covered_project, config=cfg)

        # Operational read is present (reused from rv status, not re-derived).
        assert "Operational state" in out
        assert "Coordination State" in out or "coordination" in out.lower()

        # FULL pointers.md — not just a 3-5 line head. Every section present.
        assert "Identity" in out
        assert "POINTERS" in out
        assert "Roadmap" in out
        assert "Team" in out
        assert "design-of-record: /tmp/design.md" in out  # a line status's HEAD would omit

        # architecture.md head present, but capped (not the full 100+ lines).
        assert "Architecture — covered" in out
        assert "line 0" in out
        assert "line 99" not in out  # beyond the head cap
        assert "more line(s)" in out  # truncation nudge

    def test_uncovered_project_graceful_nudge_not_crash(self, uncovered_project, rv_instance):
        from research_vault.orient import cmd_orient

        cfg = load_config()
        out = cmd_orient(uncovered_project, config=cfg)

        assert "none yet — add to" in out
        assert "pointers.md" in out
        assert "architecture.md" in out

    def test_unknown_project_surfaces_error_not_silent(self, rv_instance):
        """An unknown project must never silently produce an empty/blank orient —
        the underlying rv status read surfaces "Unknown project" per-section
        (consistent with status.py's own never-crash-the-read-face design), and
        orient's own pointers/architecture sections must nudge (not error-out
        silently) since source_dir cannot be resolved."""
        from research_vault.orient import cmd_orient

        cfg = load_config()
        out = cmd_orient("does-not-exist", config=cfg)
        assert "Unknown project" in out
        assert "source_dir not set" in out


# ---------------------------------------------------------------------------
# 3/4. CLI wiring + discoverability
# ---------------------------------------------------------------------------

class TestCLIWiring:
    def test_orient_registered_with_when_to_use(self):
        from research_vault.cli import _VERB_REGISTRY

        assert "orient" in _VERB_REGISTRY
        when = _VERB_REGISTRY["orient"].get("when_to_use", "")
        assert when.strip()
        low = when.lower()
        assert "switch" in low or "cold" in low

    def test_orient_in_help_phase_map(self):
        from research_vault.cli import _HELP_PHASE_MAP

        all_verbs = [v for _, verbs in _HELP_PHASE_MAP for v in verbs]
        assert "orient" in all_verbs

    def test_orient_build_parser_parses_project_positional(self):
        from research_vault.orient import build_parser

        p = build_parser()
        args = p.parse_args(["myproject"])
        assert args.project == "myproject"

    def test_run_returns_0_on_covered_project(self, covered_project, rv_instance, capsys):
        from research_vault.orient import build_parser, run

        p = build_parser()
        args = p.parse_args([covered_project])
        rc = run(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "rv orient" in out

    def test_run_on_unknown_project_surfaces_not_silent(self, rv_instance, capsys):
        """Unknown project: consistent with `rv status`, orient does not crash —
        it returns 0 but the printed output surfaces the unknown-project error
        inline (never a silent green-and-empty)."""
        from research_vault.orient import build_parser, run

        p = build_parser()
        args = p.parse_args(["ghost-project"])
        rc = run(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Unknown project" in out

    def test_help_check_gate_stays_green(self):
        from research_vault.cli import _check_verb_docstrings, _check_example_snippets

        assert _check_verb_docstrings() == []
        assert _check_example_snippets() == []
