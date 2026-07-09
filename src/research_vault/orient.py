# SPDX-License-Identifier: AGPL-3.0-or-later
"""orient.py — one-shot cold-switch orientation for a project (`rv orient`).

When to use: use `rv orient <project>` when you're switching to or cold-orienting
to a project — picking up a project you (or a dispatched crew member) haven't been
actively working in. One call bundles the FULL strategic orient: `rv status`'s
operational read PLUS the full `pointers.md` content and the `architecture.md`
head — the two read-fresh artifacts a cold switch needs that `rv status`
deliberately withholds (it is a coordination read — Inbox/Handshakes/task-board/
DEVLOG/git/DAG — not an orient primitive; see status.py's own docstring).

Anti-pattern: do NOT hand-assemble a cold switch as `rv status --project X` +
a manual Read of pointers.md + a manual Read of architecture.md — that 3-step
ritual is exactly what this verb collapses into one call.

If a project lacks `pointers.md` / `architecture.md`, this prints a graceful
nudge (not a crash) naming the path to create it. Orientation *amplifies*
existing read-fresh artifacts; it does not manufacture them — author
`pointers.md`/`architecture.md` first (see doctrine/project-structure.md and
doctrine/coordination.md's "Project context — read fresh" section).

Stdlib only. Reuses status.cmd_status for the operational section — no
duplicated read logic (charter §6, reuse over create).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config, load_config, resolve_repo_root
from .status import cmd_status

# Cap the architecture.md echo — it can carry large Mermaid diagrams; the head
# is enough for a cold orient, and the nudge below points at the full file.
_ARCHITECTURE_HEAD_LINES = 60


def _read_architecture_head(source_dir: str, n: int = _ARCHITECTURE_HEAD_LINES) -> str:
    """Return the head of architecture.md, or a graceful nudge if absent.

    `architecture.md` lives at the project's repo root, not necessarily under
    `source_dir` — see `config.resolve_repo_root` for the two conventions.
    """
    path = resolve_repo_root(source_dir) / "architecture.md"
    if not path.is_file():
        return f"  none yet — add to `{path}`"
    lines = path.read_text(encoding="utf-8").splitlines()
    head = lines[:n]
    body = "\n".join(f"  {ln}" for ln in head)
    if len(lines) > n:
        body += f"\n  …({len(lines) - n} more line(s) — read `{path}` in full for the rest)"
    return body


def _read_full_pointers(source_dir: str) -> str:
    """Return the FULL pointers.md content, or a graceful nudge if absent.

    `pointers.md` lives at the project's repo root, not necessarily under
    `source_dir` — see `config.resolve_repo_root` for the two conventions.
    """
    path = resolve_repo_root(source_dir) / "pointers.md"
    if not path.is_file():
        return f"  none yet — add to `{path}`"
    content = path.read_text(encoding="utf-8").rstrip("\n")
    return "\n".join(f"  {ln}" for ln in content.splitlines())


def cmd_orient(project: str, *, config: Config | None = None) -> str:
    """Return the full cold-switch orientation for a project.

    Bundles, in order: the operational `rv status` read, the FULL `pointers.md`
    content (not just its head — that's what `rv status` already echoes), and
    the `architecture.md` head. This is the "minimal set a one-shot orient
    should bundle" from the multi-project context-switch investigation:
    identity + where-things-live + roadmap + team (all in pointers.md),
    structure (architecture.md), plus the operational bundle status already
    assembles.
    """
    cfg = config or load_config()
    lines: list[str] = [
        f"# rv orient — {project}",
        "",
        "## Operational state  (rv status — control/tasks/devlog/git/DAG)",
        "",
        cmd_status(project, config=cfg),
        "",
    ]

    try:
        source_dir = cfg.project(project).get("source_dir")
    except (KeyError, Exception):
        source_dir = None

    lines.append("## pointers.md  (full — identity/POINTERS/roadmap/team/operational-state)")
    lines.append("")
    if source_dir:
        lines.append(_read_full_pointers(source_dir))
    else:
        lines.append("  (source_dir not set for this project — cannot locate pointers.md)")
    lines.append("")

    lines.append(f"## architecture.md  (head, first {_ARCHITECTURE_HEAD_LINES} line(s))")
    lines.append("")
    if source_dir:
        lines.append(_read_architecture_head(source_dir))
    else:
        lines.append("  (source_dir not set for this project — cannot locate architecture.md)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: argparse._SubParsersAction | None = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the `orient` verb.

    When to use: use `rv orient <project>` for a one-shot cold-context-switch
    orientation — the operational `rv status` read PLUS the full `pointers.md`
    content PLUS the `architecture.md` head, in one call.
    """
    desc = (
        "One-shot cold-switch orientation for a project: `rv status`'s operational "
        "read + the FULL `pointers.md` + the `architecture.md` head. "
        "Use when switching to / cold-orienting to a project. "
        "Anti-pattern: do NOT hand-assemble `rv status` + a manual pointers.md read "
        "+ a manual architecture.md read — this verb collapses that ritual."
    )
    if parent is not None:
        p = parent.add_parser(
            "orient",
            help="One-shot cold-switch orientation (status + full pointers.md + architecture.md head).",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv orient", description=desc)

    p.add_argument("project", help="Project slug.")
    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch the orient command. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv orient: config error: {e}", file=sys.stderr)
        return 1

    project = getattr(args, "project", None)
    if not project:
        print("rv orient: provide a project slug", file=sys.stderr)
        return 1

    try:
        print(cmd_orient(project, config=cfg))
        return 0
    except (KeyError, FileNotFoundError) as e:
        print(f"rv orient: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv orient: unexpected error: {e}", file=sys.stderr)
        return 1
