"""build_agents.py — agent hat file generation for Research Vault.

When to use: ``rv build-agents [--target {agents-dir,claude-code}]``
to regenerate agent hat files from the role registry and role-doc templates.

``--target agents-dir`` (default) writes prose hat files to
``agents_dir/<role>.md`` — the target-neutral, harness-agnostic
source-of-record for the ONE vault-level crew (5 roles, flat).

``--target claude-code`` writes Claude Code subagent files to
``.claude/agents/<role>.md`` at the instance root (CC YAML frontmatter +
composed hat body). Run this to make a fresh ``claude`` session discover
Alfred's crew as subagents.

Both targets build the SAME general vault-level crew (charter + role
doctrine) — no per-project lens is baked into hats.  Project context is
read fresh from ``rv status``, ``pointers.md``, and the control board.

Both targets can coexist: ``.agents/`` is the neutral source;
``.claude/agents/`` is the CC-rendered projection.  When codex/cursor/generic
backends arrive (v1.1), they render from the same ``.agents/`` source — see the
AgentBackend seam below for where they slot in.

All path resolution goes through Config — zero hardcoded paths or codenames.
Stdlib only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Config, load_config
from .project import DEFAULT_ROSTER

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Hat composition — charter + role (SR-LENS-RM)
# ---------------------------------------------------------------------------

# Map from functional role name → role-doc filename under doctrine/roles/.
# Files use role-based names so the shipped framework carries no crew narrative-names.
_ROLE_DOC: dict[str, str] = {
    "engineer":   "engineer.md",
    "researcher": "researcher.md",
    "designer":   "designer.md",
    "reviewer":   "reviewer.md",
    "architect":  "architect.md",
    "hub":        "alfred.md",
}

# Read-fresh footer appended to every hat (not a baked lens).
_READ_FRESH_FOOTER = (
    "\n\n---\n\n"
    "**Project context is not baked into this hat.**\n"
    "Read it fresh when you pick up work: "
    "`rv status --project <slug>`, the project's `pointers.md`, "
    "`architecture.md`, and its notes/control board."
)


def _compose_hat(role: str, doctrine_dir: Path) -> str:
    """Compose the hat body = charter + role doctrine + read-fresh footer.

    Reads:
      - ``doctrine_dir/agent-charter.md`` (universal values)
      - ``doctrine_dir/roles/<personal>.md`` (role doctrine) via _ROLE_DOC map

    Falls back gracefully if a file is absent (logs a warning, includes what
    it can) — never returns an empty body silently.
    """
    parts: list[str] = []

    # Charter (universal values)
    charter_path = doctrine_dir / "agent-charter.md"
    if charter_path.is_file():
        parts.append(charter_path.read_text(encoding="utf-8"))
    else:
        print(
            f"rv build-agents: WARNING — charter doc not found at {charter_path}. "
            "Hat will lack the charter layer.",
            file=sys.stderr,
        )
        parts.append(
            f"# Agent charter\n\n"
            f"*(Charter not found at {charter_path} — run `rv init` to ensure doctrine/ is present.)*\n"
        )

    # Role doctrine
    role_filename = _ROLE_DOC.get(role, f"{role}.md")
    role_path = doctrine_dir / "roles" / role_filename
    if role_path.is_file():
        parts.append(role_path.read_text(encoding="utf-8"))
    else:
        print(
            f"rv build-agents: WARNING — role doc not found at {role_path}. "
            f"Hat for {role!r} will lack the role layer.",
            file=sys.stderr,
        )
        parts.append(
            f"# Role — {role}\n\n"
            f"*(Role doc not found at {role_path} — run `rv init` to ensure doctrine/ is present.)*\n"
        )

    return "\n\n".join(parts) + _READ_FRESH_FOOTER


# ---------------------------------------------------------------------------
# AgentBackend seam
#
# A minimal strategy with one method:
#   render(role, composed_body) -> list[tuple[str, str]]
#
# Returns a list of (relpath_from_base, file_contents) pairs.
# The caller writes each pair to the filesystem.
#
# Two backends ship in v1:
#   agents-dir    → prose hat files (default)
#   claude-code   → CC subagent format (.claude/agents/<role>.md)
#
# v1.1 slot: add "codex", "cursor", "generic" backends here — each renders
# from the same composed_body, only the output path + format differ.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Claude Code tool-grant policy (PUB-CCB.2 — least-privilege, principled)
#
# coordinator-class: no Bash (structural, not disciplinary)
# doer-class: Bash + role-specific tools
# reviewer: read-only verify — no Write/Edit
# researcher: WebSearch + WebFetch for retrieval-backed citations; opus baseline
# ---------------------------------------------------------------------------

_CC_ROLE_DESCRIPTIONS: dict[str, str] = {
    "engineer": (
        "Code, tests, and CI. Delegate for feature implementation, bug fixes, "
        "refactoring, and running the authorized merge."
    ),
    "researcher": (
        "Methodology, lit-review synthesis, and retrieval-backed citations. "
        "Delegate for experiment design, literature distillation, and any "
        "research-methodology judgement."
    ),
    "designer": (
        "Visual figures, surfaces, and deploy-and-judge-live. Delegate for "
        "plot design, figure DAG runs, and any visual-quality work."
    ),
    "reviewer": (
        "Independent verification. Delegate for adversarial code review, "
        "test-suite audits, and gate verdicts before merges."
    ),
    "architect": (
        "Stack coherence and architecture. Delegate for stack-fit reads, "
        "new-dependency assessments, and cross-project design questions."
    ),
}

# (tools_string, model_alias)
# Model alias only — never a full versioned ID (leakage class-6).
_CC_GRANTS: dict[str, tuple[str, str]] = {
    "engineer":   ("Read, Write, Edit, Bash, Glob, Grep",                      "sonnet"),
    "researcher": ("Read, Write, Edit, Bash, WebSearch, WebFetch, Glob, Grep", "opus"),
    "designer":   ("Read, Write, Edit, Bash, Glob, Grep",                      "sonnet"),
    "reviewer":   ("Read, Bash, Grep, Glob",                                    "opus"),
    "architect":  ("Read, Write, Edit, Glob, Grep",                             "sonnet"),
}


class ClaudeCodeBackend:
    """Emit CC-format subagent files to .claude/agents/<role>.md.

    Output: YAML frontmatter (name / description / tools / model) followed by
    the composed hat body verbatim.  The body IS the subagent system prompt.

    Tool-grant policy from PUB-CCB.2 (coordinator-class vs doer-class split).
    Model values are aliases only (sonnet/opus/haiku) — never versioned IDs.
    """

    def render(
        self,
        role: str,
        composed_body: str,
    ) -> list[tuple[str, str]]:
        """Render one CC subagent file.

        Returns:
            [(relpath, contents)] where relpath is relative to the instance root
            (always ``.claude/agents/<role>.md``).
        """
        tools, model = _CC_GRANTS.get(
            role,
            ("Read, Write, Edit, Glob, Grep", "sonnet"),  # safe default
        )
        description = _CC_ROLE_DESCRIPTIONS.get(role, f"Research Vault {role} agent.")
        contents = (
            f"---\n"
            f"name: {role}\n"
            f"description: {description}\n"
            f"tools: {tools}\n"
            f"model: {model}\n"
            f"---\n"
            f"\n"
            f"{composed_body}"
        )
        relpath = f".claude/agents/{role}.md"
        return [(relpath, contents)]


class AgentsDirBackend:
    """Emit prose hat files to agents_dir/<role>.md (flat, vault-level).

    This is the target-neutral, harness-agnostic source-of-record.
    Future backends (codex/cursor/generic — v1.1) render from this same source.
    """

    def render(
        self,
        role: str,
        composed_body: str,
    ) -> list[tuple[str, str]]:
        """Render one prose hat file.

        Returns:
            [(relpath, contents)] where relpath is relative to agents_dir
            (``<role>.md`` — flat, vault-level, no per-project subdir).
        """
        relpath = f"{role}.md"
        return [(relpath, composed_body)]


# Registry: the two v1 backends.
# v1.1 slot: add "codex", "cursor", "generic" entries here.
_BACKENDS: dict[str, AgentsDirBackend | ClaudeCodeBackend] = {
    "agents-dir":  AgentsDirBackend(),
    "claude-code": ClaudeCodeBackend(),
}

# The 5 vault roles emitted for both targets:
# DEFAULT_ROSTER (4 project roles) + architect (vault-level coordinator).
_VAULT_ROLES = list(DEFAULT_ROSTER) + ["architect"]


# ---------------------------------------------------------------------------
# Core build logic
# ---------------------------------------------------------------------------


def cmd_build(
    cfg: Config,
    *,
    agents_dir: Path | None = None,
    dry_run: bool = False,
    target: str = "agents-dir",
) -> int:
    """Generate agent hat files from the vault-level crew doctrine.

    Builds the ONE general crew (5 roles) from charter + role doctrine.
    No per-project lens is baked; project context is read fresh at work time.

    target:
      ``agents-dir`` (default) — write prose hats to
        ``cfg.agents_dir/<role>.md`` (flat, vault-level).
      ``claude-code`` — write CC subagent files to
        ``.claude/agents/<role>.md`` at the instance root.
    """
    if target not in _BACKENDS:
        print(
            f"rv build-agents: unknown target {target!r}. "
            f"Valid targets: {sorted(_BACKENDS)}",
            file=sys.stderr,
        )
        return 1

    doctrine_dir = cfg.instance_root / "doctrine"

    if target == "claude-code":
        return _cmd_build_cc(cfg, doctrine_dir=doctrine_dir, dry_run=dry_run)

    # agents-dir target (default)
    return _cmd_build_agents_dir(
        cfg=cfg,
        agents_dir=agents_dir,
        doctrine_dir=doctrine_dir,
        dry_run=dry_run,
    )


def _cmd_build_agents_dir(
    cfg: Config,
    agents_dir: Path | None,
    doctrine_dir: Path,
    dry_run: bool,
) -> int:
    """Write flat prose hat files to agents_dir/<role>.md."""
    backend = _BACKENDS["agents-dir"]
    target_dir = agents_dir or cfg.agents_dir

    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    for role in _VAULT_ROLES:
        composed_body = _compose_hat(role, doctrine_dir)
        pairs = backend.render(role, composed_body)
        for relpath, contents in pairs:
            hat_path = target_dir / relpath
            if dry_run:
                print(f"  (dry-run) would write: {hat_path}")
            else:
                hat_path.write_text(contents, encoding="utf-8")
                print(f"  Written: {hat_path}")
            generated += 1

    verb = "would generate" if dry_run else "generated"
    print(f"\nbuild-agents: {verb} {generated} hat file(s).")
    return 0


def _cmd_build_cc(cfg: Config, doctrine_dir: Path, dry_run: bool) -> int:
    """Write CC subagent files to .claude/agents/<role>.md at the instance root."""
    backend = _BACKENDS["claude-code"]
    cc_dir = cfg.instance_root / ".claude" / "agents"

    if not dry_run:
        cc_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    for role in _VAULT_ROLES:
        composed_body = _compose_hat(role, doctrine_dir)
        pairs = backend.render(role, composed_body)
        for relpath, contents in pairs:
            out_path = cfg.instance_root / relpath
            if dry_run:
                print(f"  (dry-run) would write: {out_path}")
            else:
                out_path.write_text(contents, encoding="utf-8")
                print(f"  Written: {out_path}")
            generated += 1

    verb = "would generate" if dry_run else "generated"
    print(f"\nbuild-agents (claude-code): {verb} {generated} subagent file(s).")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``build-agents`` verb.

    When to use: ``rv build-agents [--target {agents-dir,claude-code}]`` to
    regenerate agent hat files from the charter + role doctrine.
    Use ``--target agents-dir`` (default) for the prose source-of-record hats.
    Use ``--target claude-code`` to emit CC subagent files to ``.claude/agents/``.

    Anti-pattern: do not use ``--target claude-code`` if you haven't run
    ``rv init`` first — the ``.claude/agents/`` dir is created by ``rv init``
    to satisfy Claude Code's session-start requirement.  The one vault crew is
    built once at ``rv init``; there is no per-project re-bake.
    """
    desc = (
        "Regenerate agent hat files from the charter + role doctrine. "
        "Builds the ONE vault-level crew (5 roles); no per-project lens. "
        "Default (--target agents-dir): writes flat hat files to agents_dir/<role>.md. "
        "With --target claude-code: writes CC subagent files to "
        ".claude/agents/<role>.md at the instance root."
    )
    if parent is not None:
        p = parent.add_parser("build-agents", help="Regenerate agent hat files.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv build-agents", description=desc)

    p.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be written without writing.",
    )
    p.add_argument(
        "--target",
        default="agents-dir",
        choices=["agents-dir", "claude-code"],
        help=(
            "Output target (default: agents-dir). "
            "'agents-dir' writes flat prose hats to agents_dir/<role>.md. "
            "'claude-code' writes CC subagent files to .claude/agents/<role>.md."
        ),
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Run the build-agents command. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv build-agents: config error: {e}", file=sys.stderr)
        return 1

    return cmd_build(
        cfg=cfg,
        dry_run=args.dry_run,
        target=getattr(args, "target", "agents-dir"),
    )
