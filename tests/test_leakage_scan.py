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
# Staged-mode helpers (B3)
# ---------------------------------------------------------------------------

def _make_staged_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo for staged-mode tests."""
    repo = tmp_path / "staged-repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(repo)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t.invalid"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "T"],
        check=True, capture_output=True,
    )
    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "chore: init"],
        check=True, capture_output=True,
    )
    return repo


def _stage_file(repo: Path, relative_path: str, content: str) -> None:
    """Write a file at *relative_path* in *repo* and stage it."""
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(
        ["git", "-C", str(repo), "add", relative_path],
        check=True, capture_output=True,
    )


def run_staged_scan(repo: Path) -> subprocess.CompletedProcess:
    """Run leakage_scan.sh --staged from within *repo*."""
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), "--staged"],
        capture_output=True,
        text=True,
        cwd=str(repo),
    )


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
# Class 8: Real citekeys (Pandoc inline-citation format)
# ---------------------------------------------------------------------------


def test_red_on_pandoc_citekey_citation(tmp_path):
    # author-year form citekey in Pandoc citation syntax
    write_doc(tmp_path, "The method follows [@smith2023survey] closely.\n")
    assert_red(run_scan(tmp_path))


def test_red_on_pandoc_citekey_camelcase(tmp_path):
    # Zotero-style camelCase citekey in Pandoc citation syntax
    write_doc(tmp_path, "See [@abdulhaiSimulatingPersonas2025] for details.\n")
    assert_red(run_scan(tmp_path))


def test_green_on_scrubbed_citation(tmp_path):
    write_doc(
        tmp_path,
        "The method follows prior work closely.\n"
        "See earlier studies for details.\n",
    )
    assert_green(run_scan(tmp_path))


# ---------------------------------------------------------------------------
# Class 9: Real projects.json entries
# ---------------------------------------------------------------------------


def test_red_on_projects_json_hub_slug(tmp_path):
    # "_hub" is the hub-infrastructure slug in the vault's project registry
    write_doc(tmp_path, 'The "_hub" entry manages the vault root directory.\n')
    assert_red(run_scan(tmp_path))


def test_red_on_projects_json_dsr_code(tmp_path):
    # "dsr" is the dossier project's registry code — distinct from the word "dossier"
    write_doc(tmp_path, '{ "code": "dsr", "sourceDir": "~/dossier" }\n')
    assert_red(run_scan(tmp_path))


def test_green_on_scrubbed_projects_json(tmp_path):
    write_doc(
        tmp_path,
        "Each project entry has a code, sourceDir, and roster.\n"
        "Private projects must not appear in portable doctrine.\n",
    )
    assert_green(run_scan(tmp_path))


# ---------------------------------------------------------------------------
# Staged-mode self-exclusion (B3 — cross-mode parity)
# ---------------------------------------------------------------------------
# These tests verify that the four self-excluded files are skipped in STAGED
# mode as well as in directory mode.  Before the B3 fix (missing -H), staged
# grep emitted "linenum:content" with no filename prefix, so SKIP_PATTERN
# could never match and the scanner false-positived on these files.


class TestStagedSelfExclusion:
    """Staged-mode self-exclusion must match directory-mode behaviour (B3)."""

    def test_staged_self_exclusion_leakage_scan_sh(self, tmp_path):
        """scripts/leakage_scan.sh staged with a private marker → exit 0 (self-excluded)."""
        repo = _make_staged_repo(tmp_path)
        # Plant a private marker inside the self-excluded file
        _stage_file(repo, "scripts/leakage_scan.sh", "cultural-social-sim marker\n")
        assert_green(run_staged_scan(repo))

    def test_staged_self_exclusion_ci_yml(self, tmp_path):
        """.github/workflows/ci.yml staged with a private marker → exit 0 (self-excluded)."""
        repo = _make_staged_repo(tmp_path)
        _stage_file(repo, ".github/workflows/ci.yml", "cultural-social-sim marker\n")
        assert_green(run_staged_scan(repo))

    def test_staged_self_exclusion_test_leakage_scan(self, tmp_path):
        """tests/test_leakage_scan.py staged with a private marker → exit 0 (self-excluded)."""
        repo = _make_staged_repo(tmp_path)
        _stage_file(repo, "tests/test_leakage_scan.py", "cultural-social-sim marker\n")
        assert_green(run_staged_scan(repo))

    def test_staged_self_exclusion_test_git_discipline(self, tmp_path):
        """tests/test_git_discipline.py staged with a private marker → exit 0 (self-excluded)."""
        repo = _make_staged_repo(tmp_path)
        _stage_file(repo, "tests/test_git_discipline.py", "cultural-social-sim marker\n")
        assert_green(run_staged_scan(repo))

    def test_staged_non_excluded_file_still_detected(self, tmp_path):
        """A private marker in a non-excluded staged file must still trigger exit 1."""
        repo = _make_staged_repo(tmp_path)
        _stage_file(repo, "doctrine/charter.md", "cultural-social-sim marker\n")
        assert_red(run_staged_scan(repo))

    def test_staged_no_staged_files_exits_clean(self, tmp_path):
        """Staged scan with no matching staged files exits 0."""
        repo = _make_staged_repo(tmp_path)
        assert_green(run_staged_scan(repo))


# ---------------------------------------------------------------------------
# Directory-mode self-exclusion parity (explicit regression)
# ---------------------------------------------------------------------------


class TestDirModeSelfExclusion:
    """Directory-mode self-exclusion covers all four excluded paths."""

    def test_dir_self_exclusion_leakage_scan_sh(self, tmp_path):
        """Private marker inside scripts/leakage_scan.sh → exit 0 in dir mode."""
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "leakage_scan.sh").write_text(
            "cultural-social-sim marker\n"
        )
        assert_green(run_scan(tmp_path))

    def test_dir_self_exclusion_ci_yml(self, tmp_path):
        """Private marker in .github/workflows/ci.yml → exit 0 in dir mode."""
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
            "cultural-social-sim marker\n"
        )
        assert_green(run_scan(tmp_path))

    def test_dir_self_exclusion_test_leakage_scan(self, tmp_path):
        """Private marker in tests/test_leakage_scan.py → exit 0 in dir mode."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_leakage_scan.py").write_text(
            "cultural-social-sim marker\n"
        )
        assert_green(run_scan(tmp_path))

    def test_dir_self_exclusion_test_git_discipline(self, tmp_path):
        """Private marker in tests/test_git_discipline.py → exit 0 in dir mode."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_git_discipline.py").write_text(
            "cultural-social-sim marker\n"
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


# ---------------------------------------------------------------------------
# Class 10: Crew narrative-names in Python source
# ---------------------------------------------------------------------------
# Session-narrative agent names (Ada, Wren, Mason, Argus, Iris, Atlas) must not
# appear in shipped Python source.  Role docs (*.md) are the only legitimate
# location — they define the product crew, and class 10 is .py-scoped.


def _write_py(tmp_path: Path, content: str, filename: str = "module.py") -> Path:
    """Write a Python source file to *tmp_path*."""
    f = tmp_path / filename
    f.write_text(textwrap.dedent(content))
    return f


def test_red_on_ada_in_py_docstring(tmp_path):
    """'Ada' as crew-name attribution in a Python docstring must be flagged."""
    _write_py(tmp_path, '''\
        """module.py — something.

        Ada's rubric ships as the default.
        """
        def fn(): pass
    ''')
    assert_red(run_scan(tmp_path))


def test_red_on_argus_in_py_comment(tmp_path):
    """'Argus' in a Python comment must be flagged."""
    _write_py(tmp_path, '''\
        # Argus hardening: post-build assertion
        def fn(): pass
    ''')
    assert_red(run_scan(tmp_path))


def test_red_on_iris_in_py_comment(tmp_path):
    """'Iris' in a Python comment must be flagged."""
    _write_py(tmp_path, '''\
        # Iris replaces this stub
        STUB = None
    ''')
    assert_red(run_scan(tmp_path))


def test_red_on_wren_in_py_comment(tmp_path):
    """'Wren' in a Python comment must be flagged."""
    _write_py(tmp_path, '''\
        # FLAG-A (Wren addendum)
        def fn(): pass
    ''')
    assert_red(run_scan(tmp_path))


def test_red_on_mason_in_py_comment(tmp_path):
    """'Mason' in a Python comment must be flagged."""
    _write_py(tmp_path, '''\
        # Mason/engineer label
        ROSTER = ["engineer"]  # Mason
    ''')
    assert_red(run_scan(tmp_path))


def test_red_on_atlas_in_py_comment(tmp_path):
    """'Atlas' in a Python comment must be flagged."""
    _write_py(tmp_path, '''\
        ROSTER = ["manager"]  # Atlas
    ''')
    assert_red(run_scan(tmp_path))


def test_green_on_role_terms_in_py(tmp_path):
    """Python source using role terms (not crew names) passes class 10."""
    _write_py(tmp_path, '''\
        """module.py — something.

        The researcher's rubric ships as the default.
        The designer replaces this stub with the real aesthetic.
        The reviewer role checks semantic completeness.
        The architect addendum applies Flag-A.
        """
        ROSTER = ["manager", "engineer", "researcher", "designer", "reviewer"]
        def fn(): pass
    ''')
    assert_green(run_scan(tmp_path))


def test_green_on_md_with_crew_name_not_py(tmp_path):
    """A .md file with a crew name is NOT flagged — class 10 is .py-only."""
    doc = tmp_path / "roles" / "ada.md"
    doc.parent.mkdir()
    doc.write_text("# Role — Ada (Researcher)\n\nAda is the researcher.\n")
    assert_green(run_scan(tmp_path))


# ---------------------------------------------------------------------------
# Publish-metadata allowlist (fix/leakage-publish-identity)
# These tests prove the allowlist is URL/context-scoped, NOT a blanket exemption.
# ---------------------------------------------------------------------------


def test_red_on_bare_phoongkhangzhie_in_content_file(tmp_path):
    """Bare @phoongkhangzhie (no canonical URL context) still fails — teeth intact."""
    # Structural proof: the handle appears WITHOUT the canonical repo URL.
    content = "CODEOWNERS: @phoongkhangzhie\n"
    assert "github.com/phoongkhangzhie/research-vault" not in content  # confirms bare context
    write_doc(tmp_path, content)
    assert_red(run_scan(tmp_path))


def test_red_on_phoongkhangzhie_non_canonical_url(tmp_path):
    """github.com/phoongkhangzhie/<other-repo> still fails — allowlist is research-vault-specific."""
    write_doc(tmp_path, "See https://github.com/phoongkhangzhie/other-project for details.\n")
    assert_red(run_scan(tmp_path))


def test_green_on_canonical_repo_url_in_content_file(tmp_path):
    """The canonical public URL github.com/phoongkhangzhie/research-vault passes in any content file."""
    write_doc(
        tmp_path,
        "Install: pip install research-vault\n"
        "Source:  https://github.com/phoongkhangzhie/research-vault\n"
        "Issues:  https://github.com/phoongkhangzhie/research-vault/issues\n",
    )
    assert_green(run_scan(tmp_path))


def test_green_on_pyproject_urls_block(tmp_path):
    """A pyproject.toml-style [project.urls] block with the canonical URL passes."""
    write_doc(
        tmp_path,
        '[project.urls]\n'
        'Homepage = "https://github.com/phoongkhangzhie/research-vault"\n'
        'Repository = "https://github.com/phoongkhangzhie/research-vault"\n'
        'Issues = "https://github.com/phoongkhangzhie/research-vault/issues"\n',
        filename="pyproject.toml",
    )
    assert_green(run_scan(tmp_path))


def test_red_on_khang_word_still_flagged(tmp_path):
    """The 'khang' whole-word class-2 check is independent and remains active."""
    write_doc(tmp_path, "Khang's design decisions are documented below.\n")
    assert_red(run_scan(tmp_path))


# ---------------------------------------------------------------------------
# Co-occurrence regression (mask-then-recheck fix)
# These are the exact cases Argus proved were GREEN under the old line-DROP
# approach — they must now be RED.
# ---------------------------------------------------------------------------


def test_red_on_canonical_url_plus_path_same_line(tmp_path):
    """Canonical URL + hardcoded path on ONE line must be RED.

    The old grep -Ev "$allow_ere" dropped the entire line because it matched the
    canonical URL, hiding the co-occurring private path.  After the mask-then-recheck
    fix, the URL is masked out and the /Users/phoongkhangzhie/... path still
    contains the bare literal → RED.

    Structural proof: both markers appear on the same line.
    """
    content = (
        "github.com/phoongkhangzhie/research-vault"
        " and /Users/phoongkhangzhie/secret\n"
    )
    # Confirm co-occurrence: both the canonical URL and the private path on one line.
    assert "github.com/phoongkhangzhie/research-vault" in content
    assert "/Users/phoongkhangzhie/" in content
    write_doc(tmp_path, content)
    assert_red(run_scan(tmp_path))


def test_red_on_canonical_url_plus_bare_handle_same_line(tmp_path):
    """Canonical URL + bare @phoongkhangzhie handle on ONE line must be RED.

    Under the old approach the line was dropped because it matched the canonical URL
    allowlist, so the bare handle was silently hidden (GREEN).  After the fix, the
    URL is masked and the bare handle survives the re-check → RED.

    The bare handle has NO other backstop (unlike 'khang'/'phoong' which have their
    own class-2 whole-word checks); this is the real hole Argus reported.

    Structural proof: canonical URL and bare handle co-occur on one line.
    """
    content = (
        "Source: github.com/phoongkhangzhie/research-vault"
        " — authored by @phoongkhangzhie\n"
    )
    assert "github.com/phoongkhangzhie/research-vault" in content
    # Confirm the bare handle is present AND not preceded by 'github.com/.../':
    assert "@phoongkhangzhie" in content
    write_doc(tmp_path, content)
    assert_red(run_scan(tmp_path))
