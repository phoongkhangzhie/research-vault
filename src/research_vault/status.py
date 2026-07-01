"""status.py — the control plane's structured READ face (`rv status`).

When to use: use `rv status <project>` (or `rv status --all`) to read a project's
coordination state via the tooled path. Do NOT raw-`Read` / `cat` / open control/*.md
by eye — that parses stale prose and misses live git/DAG/task state that reconcile
checks against. This IS the read tool.

`rv status <project>` prints:
  - the control-file fixed sections (Inbox / Handshakes / Outbox / Open / blockers)
  - the task board (active / blocked count + assignees)
  - the DEVLOG tail (latest dated entry)
  - local git state (recent branches, merged status — plain git, NO gh)
  - DAG run state (from SR-3 run store)
  - a needs-attention roll-up

`rv status --all` iterates all registered projects.

──────────────────────────────────────────────────────────────────────────────
THE INVESTIGATE-BOUNDARY:

  This command reads what owners POSTED — the durable record. It NEVER
  investigates. It does not ssh a cluster, count run outputs, or diagnose *why*
  something failed. Reading cheap LOCAL git state (branch names, recent commits)
  is fine. Reaching past the record into a live remote system is not.
  NO `gh` calls in core. The PR/CI SignalSource is a tier-3 adapter (SR-9).
──────────────────────────────────────────────────────────────────────────────

Stdlib only. No gh. No network.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

from .config import Config, load_config
from .controllib import parse_control_file, section_items, REQUIRED_SECTIONS

# ---------------------------------------------------------------------------
# SignalSource Protocol (tier-3 seam)
# ---------------------------------------------------------------------------

@runtime_checkable
class SignalSource(Protocol):
    """Protocol for signal sources that enrich reconcile + status.

    Core sources (local-git, task-board, DAG-run, artifact-freshness) ship
    zero-infra. A PR/CI SignalSource is contributed by the tier-3 vcs/github
    adapter (SR-9) and is absent by default.

    Methods return frozenset of normalized id tokens (lowercase).
    """

    def build_live_set(self, config: Config, project: str) -> frozenset[str]:
        """Return ids with a live artifact (dispatched/started)."""
        ...

    def get_terminal_set(self, config: Config, project: str) -> frozenset[str]:
        """Return ids with a terminal signal (merged/done/succeeded)."""
        ...


# ---------------------------------------------------------------------------
# Core (zero-infra) signal sources
# ---------------------------------------------------------------------------

class LocalGitSource:
    """Signal source: local git branches (no remote, no gh).

    Live set: local branches whose name contains an id token.
    Terminal set: branches that are ancestors of HEAD (fast-forward merged locally).
    """

    def __init__(self, repo_path: Path | None = None) -> None:
        self._repo = repo_path

    def _repo_for(self, config: Config, project: str) -> Path | None:
        if self._repo:
            return self._repo
        try:
            proj = config.project(project)
            src = proj.get("source_dir")
            if src:
                return Path(src)
        except KeyError:
            pass
        return config.instance_root

    def _git(self, args: list[str], repo: Path) -> str:
        r = subprocess.run(
            ["git", "-C", str(repo)] + args,
            capture_output=True, text=True,
        )
        return r.stdout.strip() if r.returncode == 0 else ""

    def build_live_set(self, config: Config, project: str) -> frozenset[str]:
        repo = self._repo_for(config, project)
        if not repo or not repo.exists():
            return frozenset()
        raw = self._git(["branch", "--format=%(refname:short)"], repo)
        ids: set[str] = set()
        for branch in raw.splitlines():
            from .controllib import _ID_TOKEN_RE
            for m in _ID_TOKEN_RE.finditer(branch):
                ids.add(m.group(1).lower())
        return frozenset(ids)

    def get_terminal_set(self, config: Config, project: str) -> frozenset[str]:
        """Branches that were developed AND are now merged into main.

        Primary signal: merge commit messages on main (e.g. "Merge branch 'feat/sr-x'")
        — this reliably captures --no-ff merges without false-positives on empty branches.

        Secondary signal: branches in `--merged main` whose tip is NOT at main's current
        tip (fast-forward merges that don't appear in merge commit messages).

        A freshly created branch (no commits, tip == main tip) is NOT terminal.
        """
        repo = self._repo_for(config, project)
        if not repo or not repo.exists():
            return frozenset()

        # Determine base branch
        base = "main"
        main_tip = self._git(["rev-parse", "main"], repo)
        if not main_tip:
            base = "master"
            main_tip = self._git(["rev-parse", "master"], repo)
        if not main_tip:
            return frozenset()

        merged_branches: set[str] = set()

        # Primary: parse merge commit messages (catches --no-ff merges)
        merge_log = self._git(
            ["log", base, "--merges", "--pretty=format:%s"], repo
        )
        for line in merge_log.splitlines():
            # "Merge branch 'feat/sr-x'" or "Merge branch 'feat/sr-x' into main"
            m = re.match(r"Merge (?:branch|pull request) '([^']+)'", line)
            if m:
                merged_branches.add(m.group(1))

        # Secondary: git branch --merged base, branch tip differs from main tip
        # (fast-forward merges where main advanced but branch points to an old commit)
        raw_merged = self._git(
            ["branch", "--merged", base, "--format=%(refname:short)"], repo
        )
        for branch in raw_merged.splitlines():
            branch = branch.strip()
            if not branch or branch in ("main", "master", "HEAD"):
                continue
            if branch in merged_branches:
                continue
            branch_tip = self._git(["rev-parse", branch], repo)
            if branch_tip and branch_tip != main_tip:
                # Branch tip is not at main's current position → was developed
                # AND is now merged (since it's in --merged list)
                merged_branches.add(branch)

        # Extract id tokens from merged branch names
        ids: set[str] = set()
        for branch in merged_branches:
            from .controllib import _ID_TOKEN_RE
            for m in _ID_TOKEN_RE.finditer(branch):
                ids.add(m.group(1).lower())
        return frozenset(ids)

    def recent_branches(self, config: Config, project: str, n: int = 5) -> list[str]:
        """Return recent local branches (for status display)."""
        repo = self._repo_for(config, project)
        if not repo or not repo.exists():
            return []
        raw = self._git([
            "branch", "--sort=-committerdate",
            "--format=%(refname:short) %(objectname:short)",
            "-l",
        ], repo)
        return raw.splitlines()[:n]

    def recent_commits(self, config: Config, project: str, n: int = 3) -> list[str]:
        """Return recent commit summaries (for status display)."""
        repo = self._repo_for(config, project)
        if not repo or not repo.exists():
            return []
        raw = self._git([
            "log", f"-{n}", "--oneline", "--no-walk=sorted",
        ], repo)
        return raw.splitlines()


class TaskBoardSource:
    """Signal source: project task board (rv task).

    Live set: task slugs with status in_progress, blocked (dispatched/active).
    Terminal set: task slugs with status done.
    """

    def _load_tasks(self, config: Config, project: str) -> list[dict]:
        from .task import cmd_list
        try:
            return cmd_list(project, config=config)
        except Exception:
            return []

    def build_live_set(self, config: Config, project: str) -> frozenset[str]:
        active_statuses = {"in_progress", "blocked", "ready", "active"}
        ids: set[str] = set()
        for card in self._load_tasks(config, project):
            status = card["fields"].get("status", "")
            if status in active_statuses:
                slug = card["path"].stem.lower()
                ids.add(slug)
                # Also add sr-N tokens from slug
                from .controllib import _ID_TOKEN_RE
                for m in _ID_TOKEN_RE.finditer(slug):
                    ids.add(m.group(1).lower())
        return frozenset(ids)

    def get_terminal_set(self, config: Config, project: str) -> frozenset[str]:
        ids: set[str] = set()
        for card in self._load_tasks(config, project):
            if card["fields"].get("status") == "done":
                slug = card["path"].stem.lower()
                ids.add(slug)
                from .controllib import _ID_TOKEN_RE
                for m in _ID_TOKEN_RE.finditer(slug):
                    ids.add(m.group(1).lower())
        return frozenset(ids)

    def summary(self, config: Config, project: str) -> dict:
        """Return a summary dict for status display."""
        cards = self._load_tasks(config, project)
        counts: dict[str, int] = {}
        for card in cards:
            s = card["fields"].get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return {
            "total": len(cards),
            "counts": counts,
            "active": [
                f"{c['path'].stem} ({c['fields'].get('assigned', '?')})"
                for c in cards
                if c["fields"].get("status") in ("in_progress", "blocked")
            ],
        }


class DagRunSource:
    """Signal source: DAG run store (SR-3).

    Live set: run_ids with non-terminal status.
    Terminal set: run_ids where all nodes are succeeded/failed/blocked.
    """

    def _load_runs(self, config: Config) -> list:
        try:
            from .dag.store import RunStore
            store = RunStore.from_config(config)
            run_ids = store.list_runs()
            return [(rid, store.load(rid)) for rid in run_ids]
        except Exception:
            return []

    def _is_terminal(self, run_state) -> bool:
        terminal = {"succeeded", "failed", "blocked", "awaiting-go"}
        if not run_state.node_states:
            return False
        return all(ns.get("status") in terminal for ns in run_state.node_states.values())

    def build_live_set(self, config: Config, project: str) -> frozenset[str]:
        ids: set[str] = set()
        for run_id, rs in self._load_runs(config):
            if not self._is_terminal(rs):
                ids.add(run_id.lower())
                from .controllib import _ID_TOKEN_RE
                for m in _ID_TOKEN_RE.finditer(run_id):
                    ids.add(m.group(1).lower())
        return frozenset(ids)

    def get_terminal_set(self, config: Config, project: str) -> frozenset[str]:
        ids: set[str] = set()
        for run_id, rs in self._load_runs(config):
            if self._is_terminal(rs):
                ids.add(run_id.lower())
                from .controllib import _ID_TOKEN_RE
                for m in _ID_TOKEN_RE.finditer(run_id):
                    ids.add(m.group(1).lower())
        return frozenset(ids)

    def summary(self, config: Config) -> list[dict]:
        """Return run summaries for status display."""
        result = []
        for run_id, rs in self._load_runs(config):
            node_statuses = {nid: ns.get("status", "?")
                             for nid, ns in rs.node_states.items()}
            result.append({
                "run_id": run_id,
                "nodes": node_statuses,
                "terminal": self._is_terminal(rs),
            })
        return result


# ---------------------------------------------------------------------------
# DEVLOG tail reader
# ---------------------------------------------------------------------------

_DATE_HEAD_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})", re.MULTILINE)


def _devlog_tail(devlog_path: Path, max_lines: int = 8) -> str | None:
    """Return a short tail of the latest dated DEVLOG entry."""
    if not devlog_path.exists():
        return None
    text = devlog_path.read_text(encoding="utf-8")
    heading = None
    body_lines: list[str] = []
    started = False
    for ln in text.splitlines():
        if ln.startswith("## "):
            if started:
                break
            cand = ln[3:].strip()
            if not _DATE_HEAD_RE.match("## " + cand):
                continue
            heading = cand
            started = True
            continue
        if started:
            body_lines.append(ln)
    if heading is None:
        return None
    body = "\n".join(body_lines[:max_lines])
    truncated = len(body_lines) > max_lines
    tail = f"### {heading}\n{body}"
    if truncated:
        tail += "\n  …(truncated)"
    return tail


# ---------------------------------------------------------------------------
# Core status command
# ---------------------------------------------------------------------------

def cmd_status(
    project: str,
    *,
    config: Config | None = None,
    extra_sources: list | None = None,
) -> str:
    """Return a formatted status string for one project.

    Reads: control sections, task board, DEVLOG tail, local git, DAG runs.
    NO gh / PR / CI in core.
    """
    cfg = config or load_config()
    lines: list[str] = [f"# rv status — {project}", ""]

    # --- Control file ---
    try:
        ctl_path = cfg.project_control_file(project)
        if ctl_path.exists():
            cf = parse_control_file(ctl_path)
            lines.append("## Coordination State")
            for sec_name in REQUIRED_SECTIONS:
                items = section_items(cf, sec_name)
                non_empty = [it for it in items if it["text"] and "_(none)_" not in it["text"]]
                count = len(non_empty)
                lines.append(f"  {sec_name}: {count} item(s)")
                for it in non_empty[:3]:  # show first 3
                    marker = "[x] " if it.get("resolved") else ""
                    lines.append(f"    - {marker}{it['text'][:80]}")
                if count > 3:
                    lines.append(f"    … and {count - 3} more")
            if not cf.has_banner:
                lines.append("  ⚠ banner missing — run `rv control heal`")
        else:
            lines.append("## Coordination State")
            lines.append(f"  ⚠ No control file. Run `rv control {project} init`.")
    except Exception as e:
        lines.append(f"  [control read error: {e}]")

    lines.append("")

    # --- Task board ---
    try:
        tb = TaskBoardSource()
        summary = tb.summary(cfg, project)
        lines.append("## Task Board")
        lines.append(f"  Total: {summary['total']}")
        for status, cnt in summary["counts"].items():
            lines.append(f"    {status}: {cnt}")
        if summary["active"]:
            lines.append(f"  Active: {', '.join(summary['active'][:5])}")
    except Exception as e:
        lines.append(f"  [task board error: {e}]")

    lines.append("")

    # --- DEVLOG tail ---
    try:
        devlog_path = cfg.project_devlog(project)
        tail = _devlog_tail(devlog_path)
        if tail:
            lines.append("## DEVLOG (latest entry)")
            for ln in tail.splitlines():
                lines.append(f"  {ln}")
        else:
            lines.append("## DEVLOG (latest entry)")
            lines.append("  (none or missing)")
    except Exception as e:
        lines.append(f"  [devlog error: {e}]")

    lines.append("")

    # --- Local git state ---
    try:
        git_src = LocalGitSource()
        branches = git_src.recent_branches(cfg, project, n=5)
        commits = git_src.recent_commits(cfg, project, n=3)
        lines.append("## Local Git State  (NO gh — posted local only)")
        if branches:
            lines.append("  Recent branches:")
            for b in branches:
                lines.append(f"    {b}")
        else:
            lines.append("  (no branches)")
        if commits:
            lines.append("  Recent commits:")
            for c in commits:
                lines.append(f"    {c}")
    except Exception as e:
        lines.append(f"  [git error: {e}]")

    lines.append("")

    # --- DAG run state ---
    try:
        dag_src = DagRunSource()
        runs = dag_src.summary(cfg)
        lines.append("## DAG Runs")
        if runs:
            for r in runs[:5]:
                status_str = "terminal" if r["terminal"] else "in-flight"
                lines.append(f"  {r['run_id']}: {status_str}")
        else:
            lines.append("  (none)")
    except Exception as e:
        lines.append(f"  [dag error: {e}]")

    lines.append("")

    # --- Needs-attention roll-up ---
    attention: list[str] = []
    try:
        ctl_path = cfg.project_control_file(project)
        if ctl_path.exists():
            cf = parse_control_file(ctl_path)
            inbox_items = [it for it in section_items(cf, "Inbox")
                           if it["text"] and "_(none)_" not in it["text"]]
            if inbox_items:
                attention.append(f"Inbox has {len(inbox_items)} item(s) — act or acknowledge")
            if not cf.has_banner:
                attention.append("Control file missing tooled-path banner — run `rv control heal`")
    except Exception:
        pass

    if attention:
        lines.append("## Needs Attention")
        for a in attention:
            lines.append(f"  ! {a}")
    else:
        lines.append("## Needs Attention")
        lines.append("  — nothing flagged")

    return "\n".join(lines)


def cmd_status_all(*, config: Config | None = None) -> str:
    """Return status for all registered projects."""
    cfg = config or load_config()
    slugs = cfg.all_project_slugs()
    if not slugs:
        return "rv status --all: no projects registered."
    parts = []
    for slug in slugs:
        parts.append(cmd_status(slug, config=cfg))
        parts.append("\n" + "─" * 60 + "\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: argparse._SubParsersAction | None = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the `status` verb.

    When to use: use `rv status <project>` to read a project's coordination state
    via the tooled path — control sections, task board, DEVLOG tail, local git,
    DAG run state. Do NOT cat/Read control/*.md by eye — that parses stale prose
    and misses live git/DAG/task state. This IS the read tool.

    Anti-pattern: raw-reading or catting control/*.md directly is the failure mode
    this verb exists to prevent (the SR-4-mistaken-for-undispatched incident).
    """
    desc = (
        "Print structured coordination state for a project (or all with --all). "
        "Reads: control-file sections + task board + DEVLOG tail + local git + DAG runs. "
        "NO gh/PR/CI in core (tier-3 seam, absent by default). "
        "THE INVESTIGATE-BOUNDARY: this reads what owners POSTED, never investigates live systems."
    )
    if parent is not None:
        p = parent.add_parser("status", help="Project coordination state (tooled read face).",
                               description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv status", description=desc)

    group = p.add_mutually_exclusive_group()
    group.add_argument("project", nargs="?", help="Project slug.")
    group.add_argument("--all", action="store_true", help="Show status for all registered projects.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch status subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv status: config error: {e}", file=sys.stderr)
        return 1

    try:
        if getattr(args, "all", False):
            print(cmd_status_all(config=cfg))
            return 0

        project = getattr(args, "project", None)
        if not project:
            print("rv status: provide a project slug or --all", file=sys.stderr)
            return 1

        print(cmd_status(project, config=cfg))
        return 0

    except (KeyError, FileNotFoundError) as e:
        print(f"rv status: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv status: unexpected error: {e}", file=sys.stderr)
        return 1
