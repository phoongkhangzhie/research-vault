"""test_sr_ccb.py — SR-CCB: Claude Code binding acceptance tests.

Acceptance criteria (from PUB-CCB.5, updated for SR-LENS-RM):
1. rv init -> CLAUDE.md exists + names Alfred / the hub; .claude/agents/ dir exists.
2. rv build-agents --target claude-code -> .claude/agents/{manager,engineer,
   researcher,designer,reviewer,architect}.md — 6 files, valid CC frontmatter.
3. Tool grants match the PUB-CCB.2 policy table.
4. Model values are aliases (sonnet/opus/haiku), never versioned IDs.
5. --target agents-dir (default) writes flat .agents/<role>.md (vault-level crew;
   SR-LENS-RM: no per-project subdir).
6. Body of each subagent file is non-empty and contains charter+role doctrine
   (SR-LENS-RM: replaces the CONTRACT body).
"""
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter from a markdown file.

    Returns (fields_dict, body_text).  Raises AssertionError if frontmatter
    is absent or malformed.
    """
    assert text.startswith("---\n"), f"No YAML frontmatter (no opening '---'): {text[:80]!r}"
    end = text.index("\n---\n", 4)
    fm_block = text[4:end]
    body = text[end + 5:]  # after closing ---\n
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip()
    return fields, body


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
# SR-CCB-1: CLAUDE.md scaffold via rv init
# ---------------------------------------------------------------------------

class TestClaudemdScaffold:
    def test_claude_md_exists_after_rv_init(self, tmp_vault):
        """rv init must write CLAUDE.md at the instance root."""
        assert (tmp_vault / "CLAUDE.md").is_file(), \
            "CLAUDE.md missing — rv init did not scaffold the hub bootstrap"

    def test_claude_md_names_alfred_and_hub(self, tmp_vault):
        """CLAUDE.md must identify the session as Alfred, the hub."""
        text = (tmp_vault / "CLAUDE.md").read_text(encoding="utf-8")
        lower = text.lower()
        assert "alfred" in lower, "CLAUDE.md does not mention Alfred"
        assert "hub" in lower, "CLAUDE.md does not mention the hub role"

    def test_claude_md_not_empty(self, tmp_vault):
        """CLAUDE.md must contain meaningful content (> 200 chars)."""
        text = (tmp_vault / "CLAUDE.md").read_text(encoding="utf-8")
        assert len(text) > 200, f"CLAUDE.md is suspiciously short ({len(text)} chars)"

    def test_dot_claude_agents_dir_exists(self, tmp_vault):
        """.claude/agents/ directory must exist after rv init."""
        agents_dir = tmp_vault / ".claude" / "agents"
        assert agents_dir.is_dir(), \
            ".claude/agents/ dir missing — rv init must create it (CC session-start requirement)"

    def test_claude_md_mentions_rv_status(self, tmp_vault):
        """CLAUDE.md must point to rv status as the control-plane read face."""
        text = (tmp_vault / "CLAUDE.md").read_text(encoding="utf-8")
        assert "rv status" in text, "CLAUDE.md must mention rv status as the control-plane read face"

    def test_claude_md_mentions_crew_subagents(self, tmp_vault):
        """CLAUDE.md must reference the .claude/agents/ crew location."""
        text = (tmp_vault / "CLAUDE.md").read_text(encoding="utf-8")
        assert ".claude/agents" in text, \
            "CLAUDE.md must tell Alfred where the crew subagents live"

    def test_claude_md_no_versioned_model_ids(self, tmp_vault):
        """CLAUDE.md must not contain versioned model IDs (leakage class-6)."""
        text = (tmp_vault / "CLAUDE.md").read_text(encoding="utf-8")
        # Matches: claude-sonnet-4-6, claude-3-5-sonnet-20241022, us.anthropic.claude-*
        bad = re.search(
            r"(claude-[a-z0-9-]+-[0-9]{8}|claude-[a-z]+-[0-9]+-[0-9]+|us\.anthropic\.claude)",
            text,
        )
        assert bad is None, f"CLAUDE.md contains a versioned model ID: {bad.group()!r}"


# ---------------------------------------------------------------------------
# SR-CCB-1b: COLD-PATH test (non-vacuous) — the init→auto-build wiring
#
# Root cause of the init bug: rv cli.py calls load_config() before dispatching
# to rv init (line 519: cfg = load_config()) to load instance-level verbs.
# This populates _CACHE with a stale default config (no projects, wrong
# instance_root = CWD).  When cmd_init_in_dir then calls load_config() inside
# the auto-build, _CACHE is not None → cache hit → stale config is used →
# files written to wrong instance_root → .claude/agents/ in the new instance
# is EMPTY.
#
# The conftest autouse fixture resets _CACHE before each test, which masks
# this bug in the test suite (tests always start with _CACHE = None).
#
# This test REPRODUCES the CLI scenario by injecting a stale _CACHE BEFORE
# calling cmd_init_in_dir.  It must FAIL on the current code (env-var approach
# is bypassed by cache) and PASS after the fix (direct Config construction
# ignores the cache).
# ---------------------------------------------------------------------------

class TestInitColdPathCacheResistance:
    """Non-vacuous cold-path test: rv init must emit 6 agents even with a stale cache.

    This is the test the coordinator identified as missing — it exercises the
    FULL cmd_init_in_dir path under the same conditions as the real CLI.
    """

    def test_six_agents_emitted_despite_stale_cache(self, tmp_path):
        """Full cold path: rv init emits 6 CC agents even when _CACHE is stale.

        Simulates cli.py calling load_config() (populating _CACHE with wrong
        instance_root) before rv init dispatches to cmd_init_in_dir.
        Without the fix, .claude/agents/ is empty (files written to wrong dir).
        """
        import research_vault.config as _cfg_mod
        from research_vault.config import Config, _default_config, _expand_paths
        from research_vault.init import cmd_init_in_dir

        # Simulate cli.py's pre-dispatch load_config() call:
        # inject a stale cache with wrong instance_root (the PARENT, not the instance).
        stale = _default_config()
        stale["instance_root"] = str(tmp_path)   # WRONG — the real instance is a subdir
        stale = _expand_paths(stale, tmp_path)
        _cfg_mod._CACHE = Config(stale)           # poison the cache

        # Run rv init in a subdir — must NOT be fooled by the stale cache.
        instance = tmp_path / "myvault"
        rc = cmd_init_in_dir(str(instance))
        assert rc == 0, "rv init must succeed even with a stale _CACHE"

        agents_dir = instance / ".claude" / "agents"
        assert agents_dir.is_dir(), ".claude/agents/ must exist"

        files = {f.stem for f in agents_dir.glob("*.md")}
        expected = {"manager", "engineer", "researcher", "designer", "reviewer", "architect"}
        assert files == expected, (
            f"Expected 6 agent files in instance .claude/agents/, got: {sorted(files)}.\n"
            f"Bug: stale _CACHE (instance_root={tmp_path!r}) caused auto-build to write "
            f"to wrong location instead of {str(instance)!r}."
        )

    def test_claude_md_exists_in_instance_despite_stale_cache(self, tmp_path):
        """CLAUDE.md must be written to the NEW instance, not the stale cache root."""
        import research_vault.config as _cfg_mod
        from research_vault.config import Config, _default_config, _expand_paths
        from research_vault.init import cmd_init_in_dir

        stale = _default_config()
        stale["instance_root"] = str(tmp_path)
        stale = _expand_paths(stale, tmp_path)
        _cfg_mod._CACHE = Config(stale)

        instance = tmp_path / "myvault"
        rc = cmd_init_in_dir(str(instance))
        assert rc == 0

        assert (instance / "CLAUDE.md").is_file(), \
            "CLAUDE.md missing from instance dir (init.py writes it directly, not via config)"


# ---------------------------------------------------------------------------
# SR-CCB-2: build-agents --target claude-code emits 6 CC subagent files
# ---------------------------------------------------------------------------

# Tool-grant policy table from PUB-CCB.2
_POLICY: dict[str, dict] = {
    "manager":    {"tools": {"Read", "Write", "Edit", "Glob", "Grep"}, "model": "sonnet"},
    "engineer":   {"tools": {"Read", "Write", "Edit", "Bash", "Glob", "Grep"}, "model": "sonnet"},
    "researcher": {"tools": {"Read", "Write", "Edit", "Bash", "WebSearch", "WebFetch", "Glob", "Grep"}, "model": "opus"},
    "designer":   {"tools": {"Read", "Write", "Edit", "Bash", "Glob", "Grep"}, "model": "sonnet"},
    "reviewer":   {"tools": {"Read", "Bash", "Grep", "Glob"}, "model": "opus"},
    "architect":  {"tools": {"Read", "Write", "Edit", "Glob", "Grep"}, "model": "sonnet"},
}

_ALL_ROLES = set(_POLICY.keys())
_VALID_MODEL_ALIASES = {"sonnet", "opus", "haiku", "inherit"}
_VERSIONED_ID_RE = re.compile(
    r"(claude-[a-z0-9-]+-[0-9]{8}|claude-[a-z]+-[0-9]+-[0-9]+|us\.anthropic\.claude)"
)


@pytest.fixture()
def cc_agents(tmp_vault):
    """Return the .claude/agents Path after auto-build by rv init."""
    return tmp_vault / ".claude" / "agents"


class TestClaudeCodeBackendEmit:
    def test_six_agent_files_emitted(self, cc_agents):
        """build-agents --target claude-code must emit exactly 6 .md files."""
        files = sorted(cc_agents.glob("*.md"))
        roles = {f.stem for f in files}
        assert roles == _ALL_ROLES, \
            f"Expected roles {_ALL_ROLES}, got {roles}"
        assert len(files) == 6

    def test_each_file_has_valid_frontmatter(self, cc_agents):
        """Every subagent file must have parseable YAML frontmatter."""
        for md_file in cc_agents.glob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            fm, body = _parse_frontmatter(text)
            role = md_file.stem
            # Required fields
            for field in ("name", "description", "tools", "model"):
                assert field in fm, \
                    f"{role}.md: frontmatter missing '{field}' field"

    def test_name_matches_filename(self, cc_agents):
        """The 'name' frontmatter field must match the filename stem."""
        for md_file in cc_agents.glob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            fm, _ = _parse_frontmatter(text)
            assert fm["name"] == md_file.stem, \
                f"{md_file.stem}.md: 'name' field {fm['name']!r} != filename stem"

    def test_description_non_empty(self, cc_agents):
        """Each subagent must have a non-empty 'description' field."""
        for md_file in cc_agents.glob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            fm, _ = _parse_frontmatter(text)
            role = md_file.stem
            assert fm.get("description", "").strip(), \
                f"{role}.md: 'description' is empty"

    def test_body_non_empty(self, cc_agents):
        """Body (below frontmatter) must contain the composed hat (> 50 chars)."""
        for md_file in cc_agents.glob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            _, body = _parse_frontmatter(text)
            role = md_file.stem
            assert len(body.strip()) > 50, \
                f"{role}.md: body is empty / suspiciously short"


class TestToolGrantPolicy:
    """Tool grants must match the PUB-CCB.2 least-privilege table."""

    @pytest.mark.parametrize("role", list(_POLICY.keys()))
    def test_tool_grants_match_policy(self, cc_agents, role):
        """Each role's tools must exactly match the policy table."""
        md_file = cc_agents / f"{role}.md"
        assert md_file.is_file(), f"{role}.md not emitted"
        text = md_file.read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(text)
        raw_tools = fm.get("tools", "")
        actual = {t.strip() for t in raw_tools.split(",") if t.strip()}
        expected = _POLICY[role]["tools"]
        assert actual == expected, \
            f"{role}: tools mismatch\n  expected: {sorted(expected)}\n  got:      {sorted(actual)}"

    @pytest.mark.parametrize("role", list(_POLICY.keys()))
    def test_model_is_alias(self, cc_agents, role):
        """Model value must be an alias (sonnet/opus/haiku/inherit), not a versioned ID."""
        md_file = cc_agents / f"{role}.md"
        text = md_file.read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(text)
        model = fm.get("model", "")
        # Must be an alias
        assert model in _VALID_MODEL_ALIASES, \
            f"{role}: model {model!r} is not a valid alias (must be one of {_VALID_MODEL_ALIASES})"
        # Must NOT contain a versioned string
        assert not _VERSIONED_ID_RE.search(model), \
            f"{role}: model {model!r} contains a versioned ID (leakage class-6)"

    @pytest.mark.parametrize("role", list(_POLICY.keys()))
    def test_model_matches_policy(self, cc_agents, role):
        """Model alias must match the per-role baseline in the policy table."""
        md_file = cc_agents / f"{role}.md"
        text = md_file.read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(text)
        model = fm.get("model", "")
        expected = _POLICY[role]["model"]
        assert model == expected, \
            f"{role}: model {model!r} != expected {expected!r}"


class TestPolicyConstraints:
    """Structural policy invariants: coordinator-class vs doer-class."""

    def test_manager_has_no_bash(self, cc_agents):
        """Manager (coordinator-class) must NOT have Bash."""
        text = (cc_agents / "manager.md").read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(text)
        tools = {t.strip() for t in fm.get("tools", "").split(",")}
        assert "Bash" not in tools, "manager must NOT have Bash (coordinator-class)"

    def test_architect_has_no_bash(self, cc_agents):
        """Architect (coordinator-class) must NOT have Bash."""
        text = (cc_agents / "architect.md").read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(text)
        tools = {t.strip() for t in fm.get("tools", "").split(",")}
        assert "Bash" not in tools, "architect must NOT have Bash (coordinator-class)"

    def test_reviewer_has_no_write_or_edit(self, cc_agents):
        """Reviewer (read-only verify) must NOT have Write or Edit."""
        text = (cc_agents / "reviewer.md").read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(text)
        tools = {t.strip() for t in fm.get("tools", "").split(",")}
        assert "Write" not in tools, "reviewer must NOT have Write"
        assert "Edit" not in tools, "reviewer must NOT have Edit"

    def test_researcher_has_websearch_and_webfetch(self, cc_agents):
        """Researcher must have WebSearch + WebFetch (retrieval-backed citations)."""
        text = (cc_agents / "researcher.md").read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(text)
        tools = {t.strip() for t in fm.get("tools", "").split(",")}
        assert "WebSearch" in tools, "researcher must have WebSearch"
        assert "WebFetch" in tools, "researcher must have WebFetch"


# ---------------------------------------------------------------------------
# SR-CCB-3: --target agents-dir (default) is non-breaking
# ---------------------------------------------------------------------------

class TestAgentsDirBackwardCompat:
    """--target agents-dir (default) writes flat .agents/<role>.md (SR-LENS-RM)."""

    def test_agents_dir_target_writes_flat_vault_crew(self, tmp_vault):
        """build-agents --target agents-dir writes flat vault crew to .agents/."""
        from research_vault.config import load_config
        from research_vault.build_agents import cmd_build, _VAULT_ROLES
        import os

        config_path = tmp_vault / "research_vault.toml"
        os.environ["RESEARCH_VAULT_CONFIG"] = str(config_path)
        try:
            cfg = load_config()
            rc = cmd_build(cfg=cfg, target="agents-dir")
        finally:
            del os.environ["RESEARCH_VAULT_CONFIG"]

        assert rc == 0
        agents_dir = tmp_vault / ".agents"
        assert agents_dir.is_dir()
        # Flat vault-level files (SR-LENS-RM: no per-project subdir)
        for role in _VAULT_ROLES:
            hat = agents_dir / f"{role}.md"
            assert hat.exists(), f"Flat hat {role}.md must be written to .agents/"

    def test_default_target_is_agents_dir(self, tmp_vault):
        """cmd_build without explicit target defaults to agents-dir behaviour."""
        from research_vault.config import load_config
        from research_vault.build_agents import cmd_build
        import os

        config_path = tmp_vault / "research_vault.toml"
        os.environ["RESEARCH_VAULT_CONFIG"] = str(config_path)
        try:
            cfg = load_config()
            rc = cmd_build(cfg=cfg)  # no target kwarg → agents-dir default
        finally:
            del os.environ["RESEARCH_VAULT_CONFIG"]

        assert rc == 0
        # Flat files must exist in .agents/
        agents_dir = tmp_vault / ".agents"
        hats = list(agents_dir.glob("*.md"))
        assert len(hats) > 0, "No flat hat files written to .agents/ by default target"


# ---------------------------------------------------------------------------
# SR-CCB-4: ClaudeCodeBackend unit test (isolated, no rv init required)
# ---------------------------------------------------------------------------

class TestClaudeCodeBackendUnit:
    """Unit tests for the ClaudeCodeBackend strategy object.

    SR-LENS-RM: render() no longer takes a 'project' param (vault-level crew).
    """

    def test_render_returns_list_of_tuples(self):
        """render() must return a list of (relpath, contents) tuples."""
        from research_vault.build_agents import ClaudeCodeBackend
        backend = ClaudeCodeBackend()
        result = backend.render(
            role="manager",
            composed_body="# Hat content\n\nsome body text",
        )
        assert isinstance(result, list)
        assert len(result) == 1
        relpath, contents = result[0]
        assert isinstance(relpath, str)
        assert isinstance(contents, str)

    def test_render_emits_to_dot_claude_agents(self):
        """render() must return a relpath inside .claude/agents/."""
        from research_vault.build_agents import ClaudeCodeBackend
        backend = ClaudeCodeBackend()
        result = backend.render("engineer", "# body")
        relpath, _ = result[0]
        assert ".claude/agents/engineer.md" in relpath or relpath == ".claude/agents/engineer.md", \
            f"relpath {relpath!r} should be .claude/agents/engineer.md"

    def test_render_produces_yaml_frontmatter(self):
        """render() output must begin with '---' YAML frontmatter."""
        from research_vault.build_agents import ClaudeCodeBackend
        backend = ClaudeCodeBackend()
        _, contents = backend.render("manager", "# Body")[0]
        assert contents.startswith("---\n"), \
            f"Expected YAML frontmatter, got: {contents[:60]!r}"

    def test_render_no_versioned_model_id(self):
        """render() must not embed versioned model IDs."""
        from research_vault.build_agents import ClaudeCodeBackend
        backend = ClaudeCodeBackend()
        for role in _POLICY:
            _, contents = backend.render(role, "# Body\n\nsome text")[0]
            assert not _VERSIONED_ID_RE.search(contents), \
                f"render({role!r}) contains a versioned model ID"

    def test_render_body_is_embedded(self):
        """The composed_body passed to render() must appear in the output."""
        from research_vault.build_agents import ClaudeCodeBackend
        backend = ClaudeCodeBackend()
        marker = "UNIQUE-MARKER-FOR-BODY-CHECK-12345"
        _, contents = backend.render("manager", f"# Hat\n\n{marker}")[0]
        assert marker in contents, \
            "Composed body not embedded in the render output"


# ---------------------------------------------------------------------------
# SR-LENS-RM: No demo CONTRACTs (deleted; crew carries charter+role directly)
# ---------------------------------------------------------------------------

class TestNoDemoContracts:
    """SR-LENS-RM: demo CONTRACT.md files must NOT exist anywhere."""

    def test_no_demo_contracts_after_init(self, tmp_vault):
        """rv init must NOT write any CONTRACT.md files (SR-LENS-RM)."""
        contract_files = list((tmp_vault / ".agents").rglob("CONTRACT.md"))
        assert not contract_files, (
            f"rv init still writes CONTRACT.md files: {contract_files}. "
            "SR-LENS-RM: the CONTRACT mechanism is deleted."
        )

    def test_no_contract_template_in_data(self):
        """CONTRACT.md.tmpl must NOT exist in the package data (SR-LENS-RM)."""
        from pathlib import Path
        tmpl_path = (
            Path(__file__).parent.parent
            / "src" / "research_vault" / "data" / "templates" / "CONTRACT.md.tmpl"
        )
        assert not tmpl_path.exists(), (
            f"CONTRACT.md.tmpl still exists at {tmpl_path}. SR-LENS-RM: delete it."
        )


# ---------------------------------------------------------------------------
# SR-CCB-7: Shipped docs must only reference real package verbs
# (Wren gate — non-vacuous: fails on current files, passes only after fix)
# ---------------------------------------------------------------------------

class TestShippedDocVerbAudit:
    """Every rv <verb> in shipped data docs must be a real package verb.

    This is a STRUCTURAL gate: a reference to a non-existent verb (rv identity,
    rv gh, rv launch, rv approve, rv route, ...) in a shipped doc misleads adopters
    who will type those commands and get "rv: error: argument verb: invalid choice".

    The test is non-vacuous: it FAILS on the current shipped docs (which contain
    fabricated vault-OS verbs from the ported method doctrine) and passes only
    after ALL fabricated rv-verb patterns are replaced with real package equivalents.
    """

    _DATA_DIR = (
        Path(__file__).parent.parent / "src" / "research_vault" / "data"
    )
    # argparse built-in; not in _VERB_REGISTRY but valid for docs
    _EXTRA_ALLOWED = {"help"}

    @classmethod
    def _iter_audit_files(cls, data_dir: Path) -> list[Path]:
        """Return sorted list of all files the doc-verb audit must scan.

        Covers *.md (doctrine, QUICKSTART, examples) AND *.tmpl (CLAUDE.md.tmpl,
        CONTRACT.md.tmpl) — the templates are the highest-value shipped docs because
        adopters copy-type their rv commands directly.
        """
        return sorted(data_dir.rglob("*.md")) + sorted(data_dir.rglob("*.tmpl"))

    @staticmethod
    def _collect_rv_verbs(path: Path) -> list[tuple[str, int, str]]:
        """Return [(verb, lineno, raw_line)] for rv <verb> in CODE contexts only.

        Only scans:
        - Backtick-quoted inline code: `rv <verb>...`
        - Fenced code block lines (between ``` markers)

        Deliberately skips bare prose so that English phrases like
        "the rv verbs appropriate to their role" are not flagged as commands.
        The gate lints COMMANDS adopters will type, not English text.
        """
        backtick_pat = re.compile(r'`rv\s+([a-z][a-z0-9-]+)[^`]*`')
        cmd_pat = re.compile(r'^\s*rv\s+([a-z][a-z0-9-]+)')
        hits: list[tuple[str, int, str]] = []
        in_fence = False
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                for m in cmd_pat.finditer(line):
                    hits.append((m.group(1), lineno, stripped))
            else:
                for m in backtick_pat.finditer(line):
                    hits.append((m.group(1), lineno, stripped))
        return hits

    def test_no_fabricated_rv_verbs_in_shipped_docs(self):
        """Every rv <verb> in shipped data/ docs (*.md and *.tmpl) must be real.

        Scans backtick-quoted and fenced-code commands only — prose mentions like
        'the rv verbs appropriate to their role' are not flagged.

        The gate covers CLAUDE.md.tmpl and CONTRACT.md.tmpl (the .tmpl coverage
        hole closed in the SR-CCB fast-follow) in addition to the original *.md files.
        """
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from research_vault.cli import _VERB_REGISTRY  # noqa: PLC0415

        real_verbs: set[str] = set(_VERB_REGISTRY.keys()) | self._EXTRA_ALLOWED
        data_dir = self._DATA_DIR
        assert data_dir.is_dir(), f"Data dir not found: {data_dir}"

        fabricated: list[str] = []
        for f in self._iter_audit_files(data_dir):
            for verb, lineno, line in self._collect_rv_verbs(f):
                if verb not in real_verbs:
                    rel = f.relative_to(data_dir)
                    fabricated.append(
                        f"  {rel}:{lineno}: rv {verb!r}  — {line[:100]}"
                    )

        assert not fabricated, (
            f"\n{len(fabricated)} fabricated rv <verb> reference(s) in shipped docs"
            " (adopters will type these and get 'invalid choice'):\n"
            + "\n".join(fabricated)
        )

    # ------------------------------------------------------------------
    # SR-CCB fast-follow: close the .tmpl coverage hole
    # ------------------------------------------------------------------

    def test_tmpl_files_included_in_audit_scan(self):
        """CLAUDE.md.tmpl must be in the set of files the audit scans.

        The original gate used rglob("*.md") which misses .tmpl files.
        CLAUDE.md.tmpl is the highest-value shipped doc — a stranger types its
        rv commands directly.  This test proves the hole is permanently closed.

        Verified via _iter_audit_files — the single source-of-truth for which
        files the audit covers, shared with the main gate test.
        """
        claude_tmpl = self._DATA_DIR / "templates" / "CLAUDE.md.tmpl"
        assert claude_tmpl.is_file(), f"Fixture missing: {claude_tmpl}"

        scanned = set(self._iter_audit_files(self._DATA_DIR))
        assert claude_tmpl in scanned, (
            "CLAUDE.md.tmpl is NOT in the audit scan set.\n"
            "_iter_audit_files must include *.tmpl files so this doc is permanently guarded."
        )

    def test_prose_rv_verbs_not_flagged_as_fabricated(self):
        """'rv verbs' in prose (e.g. 'the rv verbs appropriate to their role')
        must NOT be reported as a fabricated command by _collect_rv_verbs.

        CLAUDE.md.tmpl lines 14 and 44 use 'rv verbs' as an English phrase, not
        a shell command.  The gate must lint COMMANDS (backtick/fenced code), not
        prose — so this phrase is invisible to the scanner.

        RED: the current broad regex r'\\brv\\s+([a-z][a-z0-9-]+)' extracts 'verbs'
             from prose → assertion fails.
        GREEN: after restricting _collect_rv_verbs to backtick/fenced contexts → PASSES.
        """
        claude_tmpl = self._DATA_DIR / "templates" / "CLAUDE.md.tmpl"
        assert claude_tmpl.is_file(), f"Fixture missing: {claude_tmpl}"

        hits = self._collect_rv_verbs(claude_tmpl)
        verbs_hit = {verb for verb, _, _ in hits}
        assert "verbs" not in verbs_hit, (
            "The scanner flagged 'rv verbs' as a command in CLAUDE.md.tmpl.\n"
            "'verbs' is an English word here, not an rv subcommand.\n"
            "Restrict _collect_rv_verbs to backtick-quoted and fenced-code contexts\n"
            "so prose mentions of 'rv verbs' are not flagged as fabricated commands."
        )

    def test_tmpl_files_present_and_covered(self):
        """All shipped .tmpl files must appear in _iter_audit_files.

        SR-LENS-RM: CONTRACT.md.tmpl was deleted; only CLAUDE.md.tmpl ships now.
        The non-vacuous proof: CLAUDE.md.tmpl must exist and be in the audit set.
        """
        data_dir = self._DATA_DIR
        tmpl_files = set(data_dir.rglob("*.tmpl"))
        assert tmpl_files, (
            "No .tmpl files found under data/ — fixture assumption broken. "
            "This test exists to guard CLAUDE.md.tmpl."
        )

        # Confirm CONTRACT.md.tmpl is gone (SR-LENS-RM deletion)
        contract_tmpl = data_dir / "templates" / "CONTRACT.md.tmpl"
        assert not contract_tmpl.exists(), (
            f"CONTRACT.md.tmpl still exists at {contract_tmpl}. "
            "SR-LENS-RM: this file must be deleted."
        )

        scanned = set(self._iter_audit_files(data_dir))
        for t in sorted(tmpl_files):
            assert t in scanned, (
                f"{t.name} is NOT in _iter_audit_files — the audit silently skips it."
            )
