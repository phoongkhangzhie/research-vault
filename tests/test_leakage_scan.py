"""
tests/test_leakage_scan.py — hermetic tests for scripts/leakage_scan.sh.

Each test:
  - Plants a specific private marker in a temp doctrine/ directory.
  - Runs the scanner against it.
  - Asserts EXIT 1 (RED) on the planted version.
  - Asserts EXIT 0 (GREEN) on the scrubbed version.

Self-exclusion: The scanner already skips itself and ci.yml.
These test files are also excluded — their job is to contain the markers
for the purpose of testing the scanner's detection, not to be detected.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCRIPT = Path(__file__).parent.parent / "scripts" / "leakage_scan.sh"


def run_scan(directory: Path) -> subprocess.CompletedProcess:
    """Run leakage_scan.sh against *directory*; return the result."""
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), str(directory)],
        capture_output=True,
        text=True,
    )


def write_doc(tmp_path: Path, content: str, filename: str = "test.md") -> Path:
    """Write *content* to *tmp_path/filename* and return the file path."""
    f = tmp_path / filename
    f.write_text(textwrap.dedent(content))
    return f


def assert_red(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 1, (
        f"Expected scanner to RED (exit 1) but got exit {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def assert_green(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, (
        f"Expected scanner to GREEN (exit 0) but got exit {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Class 1: Private project codenames
# ---------------------------------------------------------------------------


def test_red_on_cultural_social_sim(tmp_path):
    write_doc(tmp_path, "This repo relates to cultural-social-sim research.\n")
    assert_red(run_scan(tmp_path))


def test_red_on_csb_codename(tmp_path):
    write_doc(tmp_path, "The CSB benchmark evaluates cross-cultural scores.\n")
    assert_red(run_scan(tmp_path))


def test_red_on_dossier_codename(tmp_path):
    write_doc(tmp_path, "See the dossier project for details.\n")
    assert_red(run_scan(tmp_path))


def test_green_on_scrubbed_codename(tmp_path):
    write_doc(
        tmp_path,
        "This framework is portable and contains no project-specific codenames.\n",
    )
    assert_green(run_scan(tmp_path))


# ---------------------------------------------------------------------------
# Class 2: Private identity strings
# ---------------------------------------------------------------------------


def test_red_on_khang_name(tmp_path):
    write_doc(tmp_path, "Khang's own decisions are always final.\n")
    assert_red(run_scan(tmp_path))


def test_red_on_phoong_name(tmp_path):
    write_doc(tmp_path, "Authored by Phoong in 2025.\n")
    assert_red(run_scan(tmp_path))


def test_red_on_phoongkz_handle(tmp_path):
    write_doc(tmp_path, "SSH to phoongkz@login.cluster.edu\n")
    assert_red(run_scan(tmp_path))


def test_red_on_phoongkhangzhie_handle(tmp_path):
    write_doc(tmp_path, "CODEOWNERS: @phoongkhangzhie\n")
    assert_red(run_scan(tmp_path))


def test_red_on_stanford_affiliation(tmp_path):
    write_doc(tmp_path, "The operator is affiliated with Stanford University.\n")
    assert_red(run_scan(tmp_path))


def test_green_on_scrubbed_affiliation(tmp_path):
    write_doc(tmp_path, "The operator is affiliated with a research institution.\n")
    assert_green(run_scan(tmp_path))


def test_green_on_scrubbed_identity(tmp_path):
    write_doc(
        tmp_path,
        "The operator's own decisions are always final.\n"
        "CODEOWNERS: @<your-github-handle>\n",
    )
    assert_green(run_scan(tmp_path))


# ---------------------------------------------------------------------------
# Class 3: Private site / URLs
# ---------------------------------------------------------------------------


def test_red_on_khangzhie_io(tmp_path):
    write_doc(tmp_path, "Published at https://khangzhie.io/blog/my-post.\n")
    assert_red(run_scan(tmp_path))


def test_green_on_scrubbed_url(tmp_path):
    write_doc(tmp_path, "Published at https://your-site.example.com/blog/my-post.\n")
    assert_green(run_scan(tmp_path))


# ---------------------------------------------------------------------------
# Class 4: Private cluster paths
# ---------------------------------------------------------------------------


def test_red_on_juice2_path(tmp_path):
    write_doc(tmp_path, "Data lives at /juice2/scr2/phoongkz/data/\n")
    assert_red(run_scan(tmp_path))


def test_red_on_scr2_path(tmp_path):
    write_doc(tmp_path, "Scratch at /scr2/results/\n")
    assert_red(run_scan(tmp_path))


def test_green_on_scrubbed_cluster_path(tmp_path):
    write_doc(tmp_path, "Data lives at /path/to/your/cluster/scratch/\n")
    assert_green(run_scan(tmp_path))


# ---------------------------------------------------------------------------
# Class 5: Secret-shaped strings
# ---------------------------------------------------------------------------


def test_red_on_drain_secret(tmp_path):
    write_doc(tmp_path, "Auth is gated by DRAIN_SECRET in KV.\n")
    assert_red(run_scan(tmp_path))


def test_red_on_webhook_secret(tmp_path):
    write_doc(tmp_path, "All endpoints require WEBHOOK_SECRET verification.\n")
    assert_red(run_scan(tmp_path))


def test_red_on_anthropic_key_prefix(tmp_path):
    write_doc(tmp_path, "API_KEY=sk-ant-api03-xxxxxxxxxxxx\n")
    assert_red(run_scan(tmp_path))


def test_green_on_scrubbed_secrets(tmp_path):
    write_doc(
        tmp_path,
        "Auth is gated by a shared secret stored in KV.\n"
        "Set API_KEY=<your-anthropic-api-key> in your env.\n",
    )
    assert_green(run_scan(tmp_path))


# ---------------------------------------------------------------------------
# Class 6: Versioned model IDs (per-role model roster)
# ---------------------------------------------------------------------------


def test_red_on_versioned_claude_id_dash(tmp_path):
    # e.g. claude-sonnet-4-6
    write_doc(tmp_path, "Mason spawns as claude-sonnet-4-6 by default.\n")
    assert_red(run_scan(tmp_path))


def test_red_on_versioned_claude_id_date(tmp_path):
    # e.g. claude-3-5-sonnet-20241022
    write_doc(tmp_path, "Model: claude-3-5-sonnet-20241022\n")
    assert_red(run_scan(tmp_path))


def test_red_on_bedrock_model_path(tmp_path):
    write_doc(tmp_path, "ARN: us.anthropic.claude-opus-4-20250514-v1:0\n")
    assert_red(run_scan(tmp_path))


def test_green_on_abstract_model_tier(tmp_path):
    # Abstract tier names (Sonnet/Opus/Haiku) are fine — they're policy, not pinned IDs.
    write_doc(
        tmp_path,
        "Most roles baseline Sonnet; quality-critical roles baseline Opus.\n"
        "Bump to Opus for high-stakes work; drop to Haiku for mechanical tasks.\n",
    )
    assert_green(run_scan(tmp_path))


# ---------------------------------------------------------------------------
# Class 7: Placeholder-template lint (memory.md files)
# ---------------------------------------------------------------------------


def test_red_on_private_memory_slug_in_memory_md(tmp_path):
    write_doc(
        tmp_path,
        "See [[khang-qa]] for dispatch Q&A history.\n",
        filename="memory.md",
    )
    assert_red(run_scan(tmp_path))


def test_red_on_keeper_journal_slug(tmp_path):
    write_doc(
        tmp_path,
        "keeper-journal: private narrative journal for the narrator role.\n",
        filename="memory.md",
    )
    assert_red(run_scan(tmp_path))


def test_green_on_template_memory_md(tmp_path):
    write_doc(
        tmp_path,
        "# Agent memory (template)\n\n"
        "Write craft lessons here as you learn them.\n"
        "This file is owned solely by this agent role.\n",
        filename="memory.md",
    )
    assert_green(run_scan(tmp_path))


# ---------------------------------------------------------------------------
# Composite: clean scrubbed doctrine directory greens
# ---------------------------------------------------------------------------


def test_green_on_empty_doctrine_dir(tmp_path):
    """An empty doctrine/ directory should always pass."""
    assert_green(run_scan(tmp_path))


def test_green_on_realistic_scrubbed_doc(tmp_path):
    """A realistic scrubbed charter excerpt should pass."""
    write_doc(
        tmp_path,
        """\
        # Agent charter

        Every subagent in this system wears this charter.
        It is the values and epistemics layer: *how we know things, and what we never do.*

        ## The values

        1. **Grounding — never fabricate.** Every specific traces to a real source.
        2. **Not a yes-man — this is a collaboration.** The operator stays principal.
           If a call looks suboptimal, say so before it's committed.

        ## Memory

        You are a stateless spawn. A standing agent owns a private `memory.md`.

        ## The hub is the sole spawner

        The spawn tree is exactly one level deep: `hub → {manager, engineer, reviewer, ...}`.
        Most roles baseline Sonnet; quality-critical roles (researcher, reviewer) baseline Opus.
        """,
    )
    assert_green(run_scan(tmp_path))
