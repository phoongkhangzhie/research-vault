"""brief.py — SR-DAG-BRIEF: deterministic crew dispatch brief emitter.

Every agent node in a DAG run needs a dispatch brief: a grounded, role-framing
prompt that tells the subagent exactly what to do, what to read, and what to
produce.  Today the hub HAND-WRITES this brief, re-transcribing the node's
own spec:/reads: fields (which `rv dag status` already prints) → drift,
omitted constraints, robustness risk.

This module makes the brief a DETERMINISTIC FUNCTION of (node, node_state, cfg,
run_id, project_root, manifest_project) — no I/O beyond path-resolution helpers,
so the output is byte-identical given the same inputs.

BRIEF_PREAMBLE
--------------
Authored ONCE here — role framing, instance boundary, anti-fabrication, and the
STRUCTURED-RETURN contract. Modelled on RETRY_DIAGNOSIS_DIRECTIVE (dag/verbs.py):
a fixed, unremovable structural layer that every dispatch carries.

build_brief(node, node_state, cfg, run_id, project_root, manifest_project) -> str
----------------------------------------------------------------------------------
Pure (no I/O beyond path resolution via reads.py + verbs.resolve_produces_paths).
Composes:
  preamble
  + diagnose-first block (IF attempts > 0, REUSING RETRY_DIAGNOSIS_DIRECTIVE)
  + node spec: VERBATIM
  + resolved-context block (run/node ids, resolved ABSOLUTE reads: paths,
    resolved produces: output path(s) or "none declared", project source_dir)

Stdlib only (plus intra-package imports).
sr: SR-DAG-BRIEF
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .reads import resolve_reads_paths


# ---------------------------------------------------------------------------
# BRIEF_PREAMBLE — authored once; structural, unremovable
# ---------------------------------------------------------------------------
#
# Modelled on RETRY_DIAGNOSIS_DIRECTIVE: fixed text that every dispatch carries.
# The teeth: role framing + instance/scratchpad boundary + anti-fabrication +
# the STRUCTURED-RETURN contract (so rv dag complete is meaningful).
#
# Compose: charter §1 (grounding), §2 (surface-never-silently-drop), §5 (irreversible),
# and the ⟦RETURN⟧ schema (how the hub reads your output).

BRIEF_PREAMBLE = """\
=== RESEARCH VAULT — DISPATCH BRIEF ===

ROLE FRAMING
You are a crew subagent dispatched to execute ONE node in a pre-registered
research DAG.  Your scope is exactly what this brief says — nothing more.
Do not expand scope, do not modify other DAG nodes, do not re-run completed
upstream nodes.

INSTANCE BOUNDARY
  • Work ONLY inside the project source_dir (shown in CONTEXT below).
  • Your scratchpad is your own tmp space — never write to ~/vault.
  • Never touch another project's source_dir.
  • Commit incrementally as you go (commit-as-you-go discipline).

ANTI-FABRICATION (charter §1)
  • Every specific claim — a number, a path, a metric, a citekey — must trace
    to a real source (a file you Read, a tool output, recorded run state).
  • If something is not grounded, say so; do NOT invent specifics to appear concrete.
  • Citations need a real retrieval this session, support-checked; memory is UNVERIFIED.

STRUCTURED-RETURN CONTRACT
When you finish, return the following block so `rv dag complete` is meaningful:

  ⟦RETURN⟧
    did:        <what you did, against this node's spec>
    outcome:    <the deliverable + where: path / note citekey / artifact>
    confidence: <how solid — caveats, what could be wrong, what you are unsure of>
    next:       <proposed next step · a decision the hub must make · or blocked-on <x>>
    provenance: <traceable: git SHA / file path / run id>
    retro:      <one honest lesson from this node, or — if none>

  Then: run `rv dag complete <run_id> <node_id>` (SUCCEEDED path) or
        `rv dag complete <run_id> <node_id> --status failed --error "<summary>"`
        (FAILED path — summary is REQUIRED for retriable nodes, D-RETRY-9).\
"""


# ---------------------------------------------------------------------------
# build_brief — pure, deterministic
# ---------------------------------------------------------------------------

def build_brief(
    node: dict[str, Any],
    node_state: dict[str, Any],
    cfg: Any,
    run_id: str,
    project_root: Path,
    manifest_project: str | None = None,
) -> str:
    """Build a deterministic dispatch brief for a DAG agent node.

    Parameters
    ----------
    node:              The node dict from the manifest (id, spec, reads, produces, …).
    node_state:        The node's run-state dict (attempts, last_failure, …).
                       Pass {} for a first-attempt node.
    cfg:               The loaded Config object (for project_notes_dir resolution).
    run_id:            The DAG run_id (used in the CONTEXT block + complete command).
    project_root:      The manifest file's parent directory (used by reads: resolver).
    manifest_project:  The manifest-level ``project`` slug, if any.

    Returns
    -------
    A string: the full dispatch brief.  Pure — same inputs → byte-identical output.
    No I/O beyond path-resolution helpers (reads.py + resolve_produces_paths).
    """
    from .verbs import RETRY_DIAGNOSIS_DIRECTIVE, resolve_produces_paths

    node_id: str = node.get("id", "<unknown>")
    spec: str = node.get("spec", "")
    attempts: int = node_state.get("attempts", 0)
    last_failure: str | None = node_state.get("last_failure")
    max_retries: int = node.get("max_retries", 0)

    parts: list[str] = []

    # ── Preamble (always) ─────────────────────────────────────────────────────
    parts.append(BRIEF_PREAMBLE)

    # ── Diagnose-first block (retry only) ─────────────────────────────────────
    if attempts > 0:
        failure_summary = last_failure or "(no summary captured)"
        directive = RETRY_DIAGNOSIS_DIRECTIVE.format(
            attempt_k=attempts + 1,
            total_attempts=max_retries + 1,
            last_failure=failure_summary,
        )
        tips = node.get("retry_diagnosis_tips")
        if tips:
            if isinstance(tips, str):
                directive += f"\nDOMAIN TIPS: {tips}"
            elif isinstance(tips, list):
                directive += "\nDOMAIN TIPS:\n" + "\n".join(f"  - {t}" for t in tips)
        parts.append("=== DIAGNOSE FIRST (RETRY) ===\n" + directive)

    # ── Spec block (verbatim) ─────────────────────────────────────────────────
    parts.append("=== SPEC (VERBATIM — your task) ===\n" + (spec or "(no spec declared)"))

    # ── Resolved context block ────────────────────────────────────────────────
    ctx_lines: list[str] = [
        "=== CONTEXT ===",
        f"run_id:   {run_id}",
        f"node_id:  {node_id}",
    ]

    # project slug + source_dir
    if manifest_project and cfg is not None:
        ctx_lines.append(f"project:  {manifest_project}")
        try:
            source_dir = cfg.project_notes_dir(manifest_project)
            ctx_lines.append(f"source_dir: {source_dir}")
        except Exception:
            ctx_lines.append("source_dir: (unknown — project not in config)")
    elif cfg is not None:
        # No manifest-level project: show the first registered project as a hint
        try:
            projects = list(cfg.projects.keys())
            if projects:
                first = projects[0]
                ctx_lines.append(f"project:  {first} (inferred — no manifest project field)")
                try:
                    source_dir = cfg.project_notes_dir(first)
                    ctx_lines.append(f"source_dir: {source_dir}")
                except Exception:
                    pass
        except Exception:
            pass

    # Resolved absolute reads: paths (SSOT: reads.resolve_reads_paths)
    abs_paths = resolve_reads_paths(node, project_root)
    if abs_paths:
        ctx_lines.append("reads (resolved absolute paths):")
        for p in abs_paths:
            ctx_lines.append(f"  {p}")
    else:
        ctx_lines.append("reads: (none declared)")

    # Resolved produces: output path(s) (SSOT: verbs.resolve_produces_paths)
    produces_paths: list[Path] = []
    if cfg is not None:
        try:
            produces_paths = resolve_produces_paths(
                node, cfg, manifest_project=manifest_project
            )
        except Exception:
            pass

    if produces_paths:
        ctx_lines.append("produces (expected output path(s)):")
        for p in produces_paths:
            ctx_lines.append(f"  {p}")
    else:
        ctx_lines.append("produces: (none declared)")

    parts.append("\n".join(ctx_lines))

    return "\n\n".join(parts) + "\n"
