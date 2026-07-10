# SPDX-License-Identifier: AGPL-3.0-or-later
"""control.py — project coordination control-file management.

When to use: use `rv control <project> <subcommand>` to initialize, read, update,
or reconcile the coordination bus for a project.

WRITE via the tooled path — `rv control post/spawn-request/return/close/edit/move`.
NEVER hand-edit control/*.md — a raw edit races other agents and can write a
schema-invalid entry. READ via `rv status` or `rv control reconcile`.

Anti-patterns this replaces:
  - Opening control/*.md and typing bullets directly (races concurrent mutators,
    skips schema validation, no auto-slug/date).
  - cat-ing / Read-ing control/*.md and parsing by eye (misses live git/DAG/task
    state; the mistaken-for-undispatched incident, 2026-07-01).

Control file structure (REQUIRED_SECTIONS):
  # CONTROL — <project>
  ## Inbox   (hub/owner → crew)
  ## Handshakes  (in-flight, needs the other side)
  ## Outbox  (crew → hub/owner)
  ## Open / blockers

All paths resolved from Config — zero hardcoded paths.
Stdlib only.
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .controllib import (
    BANNER_TEXT,
    REQUIRED_SECTIONS,
    RESOLVED_THRESHOLD,
    RETURN_REQUIRED,
    SPAWN_REQUIRED,
    NOT_YET_LEXICON,
    Violation,
    append_to_archive,
    atomic_write,
    count_resolved_markers,
    extract_id_tokens,
    locked_mutate,
    parse_control_file,
    parse_control_text,
    regenerate_archive_index,
    section_items,
    validate_control_file,
    _ENTRY_ID_RE,
    _archive_path,
    _canon_section,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Section slug → canonical display name (for --section CLI arg)
_SECTION_SLUGS: dict[str, str] = {
    "inbox": "Inbox",
    "handshakes": "Handshakes",
    "outbox": "Outbox",
    "open-blockers": "Open / blockers",
    "open-findings": "Open / blockers",
}


def _today() -> str:
    return datetime.date.today().isoformat()


def _slugify(s: str) -> str:
    """Convert a title to a slug."""
    slug = s.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:40] or "entry"


# ---------------------------------------------------------------------------
# GitHub repo detection (tier-3 CLI activation helper)
# ---------------------------------------------------------------------------

def _parse_github_slug(url: str) -> str | None:
    """Extract 'owner/repo' from a GitHub remote URL.

    Handles:
      https://github.com/owner/repo.git
      https://github.com/owner/repo
      git@github.com:owner/repo.git
      git@github.com:owner/repo
    """
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    return None


def _detect_github_repo(repo_arg: str | None, cwd: Path | None = None) -> str | None:
    """Return a GitHub repo slug ('owner/repo') from arg or git remote.

    Resolution order:
      1. ``repo_arg`` if provided (explicit wins)
      2. ``git remote get-url origin`` in ``cwd`` (or process cwd if None), parsed
         via _parse_github_slug — covers both HTTPS and SSH remotes.
      3. None — caller must surface an error.

    No gh calls here; plain git — works without authentication or a GitHub token.
    The owner/repo must NEVER be hardcoded; it is always runtime-derived (leakage rule).
    """
    if repo_arg:
        return repo_arg

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            return _parse_github_slug(url)
    except (FileNotFoundError, OSError):
        pass

    return None


# ---------------------------------------------------------------------------
# Control file rendering
# ---------------------------------------------------------------------------

def _render_control_file(project: str, note: str = "") -> str:
    """Render a blank control file skeleton with the enforcement banner."""
    intro = note or f"Created {_today()}."
    return f"""{BANNER_TEXT}

# CONTROL — {project}

The hub↔crew bus for this project: an async, durable handshake file. The hub reads it
to build the brief; crew members read it at the top of each turn. Markdown, near-free, legible.
Read via `rv status {project}` or `rv control {project} reconcile`.

> *{intro}*

## Inbox  (hub/owner → crew)
  _(none)_

## Handshakes  (in-flight, needs the other side)
  _(none)_

## Outbox  (crew → hub/owner)
  _(none)_

## Open / blockers
  _(none)_
"""


# ---------------------------------------------------------------------------
# Section parsing helpers
# ---------------------------------------------------------------------------

def _find_section_bounds(text: str, canon: str) -> tuple[int, int] | None:
    """Find (start, end) char offsets of the section content in text.

    start = char after the section header line (incl. newline).
    end = char before the next ## header, or EOF.
    """
    lines = text.splitlines(keepends=True)
    header_idx = None
    for i, ln in enumerate(lines):
        m = re.match(r"^## (.+?)(?:\s+\(.*\))?$", ln.rstrip())
        if m:
            if (_canon_section(m.group(1).strip()) == canon
                    or m.group(1).strip() == canon):
                header_idx = i
                break
    if header_idx is None:
        return None
    start_offset = sum(len(ln) for ln in lines[:header_idx + 1])
    end_offset = len(text)
    for j in range(header_idx + 1, len(lines)):
        if re.match(r"^## ", lines[j]):
            end_offset = sum(len(ln) for ln in lines[:j])
            break
    return (start_offset, end_offset)


def _insert_into_section(text: str, canon: str, entry: str) -> str:
    """Insert `entry` into the section, replacing _(none)_ placeholder or appending."""
    placeholder = "  _(none)_"
    bounds = _find_section_bounds(text, canon)
    if bounds is None:
        return text.rstrip() + f"\n\n## {canon}\n{entry}\n"
    start, end = bounds
    section_content = text[start:end]
    if placeholder in section_content:
        new_content = section_content.replace(placeholder, entry, 1)
    else:
        new_content = section_content.rstrip("\n") + "\n" + entry + "\n"
    return text[:start] + new_content + text[end:]


# A next-entry bullet — NOT any "- " line (a body/prose line could start with a
# dash incidentally). Only "- **" unambiguously starts a new control-file entry.
_ENTRY_BULLET_START_RE = re.compile(r"^- \*\*")
_HEADING_START_RE = re.compile(r"^#{1,6} ")


def _find_entry_block(lines: list[str], entry_id: str) -> tuple[int, int] | None:
    """Locate the [start, end) line-index bounds of entry_id's whole block.

    The block is the bullet line matching ``**entry_id**`` plus every
    continuation/body line that follows (e.g. a ⟦RETURN⟧/⟦SPAWN REQUEST⟧
    marker line and its indented `  key: value` fields). It terminates at
    whichever comes first: the next entry's bullet (``- **...**``), a blank
    line, a markdown heading, a fenced-code delimiter, or EOF.

    Returns None if entry_id is not found.
    """
    pattern = re.compile(re.escape(f"**{entry_id}**"), re.IGNORECASE)
    start = None
    for i, ln in enumerate(lines):
        if pattern.search(ln):
            start = i
            break
    if start is None:
        return None

    end = start + 1
    while end < len(lines):
        stripped = lines[end].rstrip("\n")
        if not stripped.strip():
            break  # blank line — section-item separator
        if _ENTRY_BULLET_START_RE.match(stripped):
            break  # next entry's bullet — do not borrow into it
        if _HEADING_START_RE.match(stripped):
            break  # section boundary
        if stripped.lstrip().startswith("```"):
            break  # fenced-code delimiter
        end += 1
    return start, end


def _remove_entry(text: str, entry_id: str) -> tuple[str, str]:
    """Remove the whole block (bullet + continuation/body lines) for entry_id.

    Returns (new_text, removed_text). A multi-line ⟦RETURN⟧/⟦SPAWN REQUEST⟧
    block's body fields are removed along with the bullet — not just the
    bullet line — while an immediately-adjacent block is left untouched
    (see _find_entry_block for the termination rule).
    """
    lines = text.splitlines(keepends=True)
    bounds = _find_entry_block(lines, entry_id)
    if bounds is None:
        return text, ""
    start, end = bounds
    removed_lines = lines[start:end]
    kept_lines = lines[:start] + lines[end:]
    return "".join(kept_lines), "".join(removed_lines)


# ---------------------------------------------------------------------------
# Commands — READ face
# ---------------------------------------------------------------------------

def cmd_init(project: str, *, config: Config | None = None,
             note: str = "", overwrite: bool = False) -> Path:
    """Initialize a control file for a project.

    Raises FileExistsError if the file already exists and overwrite=False.
    Returns the path to the created control file.
    """
    cfg = config or load_config()
    control_file = cfg.project_control_file(project)
    control_file.parent.mkdir(parents=True, exist_ok=True)

    if control_file.exists() and not overwrite:
        raise FileExistsError(
            f"Control file already exists: {control_file}. Use --overwrite to replace."
        )

    control_file.write_text(_render_control_file(project, note), encoding="utf-8")
    return control_file


def cmd_view(project: str, *, config: Config | None = None) -> str:
    """Return the full content of the project's control file."""
    cfg = config or load_config()
    control_file = cfg.project_control_file(project)
    if not control_file.exists():
        raise FileNotFoundError(
            f"No control file for {project!r}. "
            f"Run `rv control {project} init` to create one."
        )
    return control_file.read_text(encoding="utf-8")


def cmd_check(project: str, *, config: Config | None = None) -> list[str]:
    """Validate the control file structure for a project.

    Returns a list of violation strings (empty = all clear).
    Checks: required sections present, banner present, block fields valid.
    """
    cfg = config or load_config()
    control_file = cfg.project_control_file(project)

    if not control_file.exists():
        return [f"Missing control file: {control_file}"]

    vios = validate_control_file(control_file)
    return [f"[{v.level.upper()}] {v.msg}" for v in vios]


def cmd_heal(project: str, *, config: Config | None = None) -> Path:
    """Insert the enforcement banner if missing, and fix auto-healable issues.

    Returns the path to the (possibly updated) control file.
    """
    cfg = config or load_config()
    control_file = cfg.project_control_file(project)

    if not control_file.exists():
        raise FileNotFoundError(
            f"No control file for {project!r}. Run `rv control {project} init` first."
        )

    with locked_mutate(control_file):
        text = control_file.read_text(encoding="utf-8")
        has_banner = (
            "rv status" in text
            and "rv control reconcile" in text
            and ("hand-edit" in text or "NEVER hand-edit" in text or "⚠" in text)
        )
        if not has_banner:
            text = BANNER_TEXT + "\n\n" + text
        atomic_write(control_file, text)

    return control_file


def cmd_inbox(project: str, message: str, *,
              config: Config | None = None) -> Path:
    """Append a dated message to the Inbox section (legacy convenience).

    Returns the control file path (backward-compatible with legacy callers).
    """
    path, _ = cmd_post(project, section="inbox", title=message, config=config)
    return path


# ---------------------------------------------------------------------------
# Reconcile — R1–R4
# ---------------------------------------------------------------------------

def _build_combined_live_set(
    config: Config,
    project: str,
    *,
    git_repo: Path | None = None,
    extra_sources: list | None = None,
) -> frozenset[str]:
    """Build the combined LIVE set from all available signal sources.

    Sources that error are skipped with a stderr warning (not silently swallowed)
    so a failing git call surfaces as a visible warning rather than false GREEN.
    """
    import sys
    from .status import LocalGitSource, TaskBoardSource, DagRunSource
    sources: list[Any] = [
        LocalGitSource(repo_path=git_repo),
        TaskBoardSource(),
        DagRunSource(),
    ]
    if extra_sources:
        sources.extend(extra_sources)
    live: set[str] = set()
    errors = 0
    for src in sources:
        try:
            live.update(src.build_live_set(config, project))
        except Exception as exc:
            errors += 1
            print(
                f"[WARN] {src.__class__.__name__}.build_live_set errored: {exc}",
                file=sys.stderr,
            )
    if errors:
        print(
            f"[WARN] {errors} signal source(s) errored building live set — "
            "result may be incomplete (false GREEN possible)",
            file=sys.stderr,
        )
    return frozenset(live)


def _build_combined_terminal_set(
    config: Config,
    project: str,
    *,
    git_repo: Path | None = None,
    extra_sources: list | None = None,
) -> frozenset[str]:
    """Build the combined TERMINAL set from all available signal sources.

    Sources that error are skipped with a stderr warning so a failing git call
    surfaces rather than silently yielding an empty terminal set.
    """
    import sys
    from .status import LocalGitSource, TaskBoardSource, DagRunSource
    sources: list[Any] = [
        LocalGitSource(repo_path=git_repo),
        TaskBoardSource(),
        DagRunSource(),
    ]
    if extra_sources:
        sources.extend(extra_sources)
    terminal: set[str] = set()
    errors = 0
    for src in sources:
        try:
            terminal.update(src.get_terminal_set(config, project))
        except Exception as exc:
            errors += 1
            print(
                f"[WARN] {src.__class__.__name__}.get_terminal_set errored: {exc}",
                file=sys.stderr,
            )
    if errors:
        print(
            f"[WARN] {errors} signal source(s) errored building terminal set — "
            "result may be incomplete (false GREEN possible)",
            file=sys.stderr,
        )
    return frozenset(terminal)


def _check_r1(text: str, live_set: frozenset[str]) -> list[str]:
    """R1: 'not-yet' claim vs a live artifact.

    id-token match × fixed lexicon × live-set membership → STALE.
    """
    findings: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        tokens = extract_id_tokens(line)
        live_tokens = [t for t in tokens if t in live_set]
        if not live_tokens:
            continue
        matched_phrases = [ph for ph in NOT_YET_LEXICON if ph in low]
        if matched_phrases:
            for tok in live_tokens:
                findings.append(
                    f"[R1] STALE line {i}: control says {tok.upper()!r} is "
                    f"{matched_phrases[0]!r} but a live artifact exists "
                    f"(branch/task/run for {tok.upper()} is dispatched/active). "
                    f"Line: {line.strip()!r}"
                )
    return findings


def _check_r2(text: str) -> list[str]:
    """R2: claimed artifact missing.

    Lines with `artifact:<path>` where the file doesn't exist → flag.
    """
    findings: list[str] = []
    artifact_re = re.compile(r"artifact:([^\s,;)\"']+)", re.IGNORECASE)
    for i, line in enumerate(text.splitlines(), 1):
        for m in artifact_re.finditer(line):
            raw_path = m.group(1).strip()
            path = Path(raw_path).expanduser()
            if not path.exists():
                findings.append(
                    f"[R2] STALE line {i}: claimed artifact {raw_path!r} "
                    f"does not exist. Line: {line.strip()!r}"
                )
    return findings


def _check_r3(text: str, config: Config, project: str) -> list[str]:
    """R3: task-board done but listed as open blocker."""
    from .task import cmd_list as task_list
    findings: list[str] = []
    try:
        cards = task_list(project, config=config)
    except Exception:
        return findings
    done_slugs = frozenset(
        c["path"].stem.lower()
        for c in cards
        if c["fields"].get("status") == "done"
    )
    if not done_slugs:
        return findings
    cf = parse_control_text(text)
    for item in section_items(cf, "Open / blockers"):
        if item.get("resolved"):
            continue
        line_text = item["text"].lower()
        for slug in done_slugs:
            if slug in line_text or _slugify(slug) in line_text:
                findings.append(
                    f"[R3] STALE: task {slug!r} is `done` on the board "
                    f"but still in Open / blockers: {item['text'][:80]!r}"
                )
    return findings


def _check_r4(text: str, terminal_set: frozenset[str]) -> list[str]:
    """R4: merged/terminal id still in Handshakes as in-flight."""
    findings: list[str] = []
    cf = parse_control_text(text)
    for item in section_items(cf, "Handshakes"):
        if item.get("resolved"):
            continue
        tokens = extract_id_tokens(item["text"])
        entry_id = item.get("entry_id", "")
        if entry_id:
            tokens.extend(extract_id_tokens(entry_id))
        for tok in tokens:
            if tok in terminal_set:
                findings.append(
                    f"[R4] STALE: id {tok.upper()!r} is terminal (merged/done) "
                    f"but still in Handshakes as in-flight: {item['text'][:80]!r}"
                )
    return findings


def cmd_reconcile(
    project: str,
    *,
    config: Config | None = None,
    git_repo: Path | None = None,
    extra_sources: list | None = None,
    archive: bool = False,
) -> list[str]:
    """Deterministic posted-vs-live drift check (R1–R4). Returns list of finding strings.

    Returns empty list if no drift found (the green case).
    If archive=True, auto-archives entries whose id maps to a terminal live signal.

    PR/CI SignalSource absent by default (tier-3). Pass adapters via extra_sources.
    """
    cfg = config or load_config()
    ctl_path = cfg.project_control_file(project)

    if not ctl_path.exists():
        return [f"[setup] No control file for {project!r}"]

    text = ctl_path.read_text(encoding="utf-8")
    live_set = _build_combined_live_set(
        cfg, project, git_repo=git_repo, extra_sources=extra_sources
    )
    terminal_set = _build_combined_terminal_set(
        cfg, project, git_repo=git_repo, extra_sources=extra_sources
    )

    findings: list[str] = []
    findings.extend(_check_r1(text, live_set))
    findings.extend(_check_r2(text))
    findings.extend(_check_r3(text, cfg, project))
    findings.extend(_check_r4(text, terminal_set))

    # R5: resolved-but-unarchived bloat check.
    # A control file with > RESOLVED_THRESHOLD closed entries that were never
    # archived is a maintenance debt signal — the teeth check.
    resolved_count = count_resolved_markers(text)
    if resolved_count > RESOLVED_THRESHOLD:
        findings.append(
            f"[R5] BLOAT: {resolved_count} resolved-but-unarchived entries exceed "
            f"threshold ({RESOLVED_THRESHOLD}). Run `rv control reconcile --archive` "
            "or manually close + archive stale entries."
        )

    if archive and terminal_set:
        _auto_archive_terminal(project, ctl_path, terminal_set, cfg)

    return findings


def _auto_archive_terminal(
    project: str,
    ctl_path: Path,
    terminal_set: frozenset[str],
    cfg: Config,
) -> None:
    """Auto-archive entries whose id maps to a terminal live signal."""
    with locked_mutate(ctl_path):
        text = ctl_path.read_text(encoding="utf-8")
        cf = parse_control_text(text)
        for sec_name in REQUIRED_SECTIONS:
            for item in section_items(cf, sec_name):
                entry_id = item.get("entry_id")
                if not entry_id:
                    continue
                tokens = (extract_id_tokens(entry_id)
                          + extract_id_tokens(item.get("text", "")))
                if any(t in terminal_set for t in tokens) and not item.get("resolved"):
                    # Full block (bullet + continuation/body lines) — not just
                    # the bullet — so the archive captures what _remove_entry
                    # actually strips from the live file (see cmd_close, which
                    # derives entry_text the same way).
                    lines_now = text.splitlines(keepends=True)
                    bounds = _find_entry_block(lines_now, entry_id)
                    entry_text = (
                        "".join(lines_now[bounds[0]:bounds[1]]).strip()
                        if bounds is not None
                        else item["raw_line"]
                    )
                    _do_archive_entry(
                        ctl_path=ctl_path,
                        entry_id=entry_id,
                        entry_text=entry_text,
                        text=text,
                        already_locked=True,
                    )
                    # Re-read after each mutation
                    text = ctl_path.read_text(encoding="utf-8")
                    cf = parse_control_text(text)


def count_resolved_unarchived(project: str, *, config: Config | None = None) -> int:
    """Count resolved-but-unarchived markers in the control file.

    Over RESOLVED_THRESHOLD → the teeth check flags it.
    """
    cfg = config or load_config()
    ctl_path = cfg.project_control_file(project)
    if not ctl_path.exists():
        return 0
    text = ctl_path.read_text(encoding="utf-8")
    return count_resolved_markers(text)


# ---------------------------------------------------------------------------
# Write face helpers
# ---------------------------------------------------------------------------

def _make_entry_id(kind: str, title: str, by: str) -> str:
    """Generate a slug id: kind:slug-by-date."""
    slug = _slugify(title)
    date = _today().replace("-", "")[:8]
    by_slug = _slugify(by) if by else "anon"
    return f"{kind}:{slug}-{by_slug}-{date}"


def _make_bullet(entry_id: str, body: str) -> str:
    """Format a section bullet: `- **entry_id** — body`."""
    if body:
        return f"- **{entry_id}** — {body}"
    return f"- **{entry_id}**"


# Matches the bracketed gate token: [PASS] or [BLOCK] (case-insensitive, full value).
# Bare words like "PASS", "BLOCK", "FAIL" in prose do NOT match — by design, so that
# narrative mentions cannot be confused with the structured gate verdict.
_GATE_TOKEN_RE = re.compile(r"^\[(PASS|BLOCK)\]$", re.IGNORECASE)


def _extract_gate_verdict(verdict_val: str) -> str | None:
    """Return 'PASS' or 'BLOCK' if *verdict_val* is exactly the bracketed gate token.

    Recognized forms: ``[PASS]`` and ``[BLOCK]`` (case-insensitive, full value).
    A bare word — 'PASS', 'BLOCK', 'FAIL', 'approve' — does NOT match.
    This ensures prose mentions in narrative fields can never false-trigger the gate.
    """
    m = _GATE_TOKEN_RE.match(verdict_val.strip())
    return m.group(1).upper() if m else None


def _make_block(marker: str, fields: dict[str, str]) -> str:
    """Render a ⟦MARKER⟧ block with the given fields.

    RETURN blocks with a ``[PASS]`` or ``[BLOCK]`` verdict field get a gate-clean
    verdict header as the first line of the block body (TOOL-D3).  Shape:

        VERDICT: [PASS]
          did: …
          outcome: … (may mention bare BLOCK / FAIL in prose — bracket decouples)

    The bracket delimiter is the key: the approve-gate matches ``[PASS]`` /
    ``[BLOCK]``; a bare "BLOCK" or "FAIL" anywhere in prose cannot false-match.
    The verdict field is suppressed from the indented list — the header IS the
    verdict field, readable by the controllib parser via the known-key path.
    """
    lines = [f"⟦{marker}⟧"]

    if marker == "RETURN":
        verdict_val = fields.get("verdict", "")
        gate_token = _extract_gate_verdict(verdict_val)
        if gate_token:
            # Gate-clean header: unindented, first line after the marker.
            # Bracketed token: controllib parser reads "verdict: [PASS]".
            lines.append(f"VERDICT: [{gate_token}]")
        for key, val in fields.items():
            if key == "verdict" and gate_token:
                continue  # already emitted as header; skip to avoid duplication
            lines.append(f"  {key}: {val}")
    else:
        for key, val in fields.items():
            lines.append(f"  {key}: {val}")

    return "\n".join(lines)


def _validate_required_fields(
    fields: dict[str, str],
    required: list[str],
    block_kind: str,
) -> None:
    """Raise ValueError if any required field is missing or empty."""
    for f in required:
        if f not in fields:
            raise ValueError(
                f"⟦{block_kind}⟧ missing required field: {f!r}. "
                f"All required: {required}"
            )
        if not str(fields[f]).strip():
            raise ValueError(
                f"⟦{block_kind}⟧ empty required field: {f!r}. "
                f"Required fields must not be blank."
            )


# ---------------------------------------------------------------------------
# Write face commands
# ---------------------------------------------------------------------------

def cmd_post(
    project: str,
    *,
    section: str,
    title: str,
    body: str = "",
    kind: str = "",
    by: str = "",
    config: Config | None = None,
) -> tuple[Path, str]:
    """Append a bullet entry to a control file section.

    Schema-valid-by-construction: emits the canonical bullet shape.
    Concurrency-safe: advisory lock around read-mutate-write.

    Returns (control_file_path, entry_id).
    """
    cfg = config or load_config()
    ctl_path = cfg.project_control_file(project)

    if not ctl_path.exists():
        cmd_init(project, config=cfg)

    canon = _SECTION_SLUGS.get(section.lower())
    if canon is None:
        canon = _canon_section(section)
    if canon is None:
        raise ValueError(
            f"Unknown section {section!r}. Valid: {', '.join(_SECTION_SLUGS)}"
        )

    effective_kind = kind or section.lower().replace("-", "").replace(" ", "")
    entry_id = _make_entry_id(effective_kind, title, by)
    bullet = _make_bullet(entry_id, body or title)
    entry = f"{bullet}\n"

    with locked_mutate(ctl_path):
        text = ctl_path.read_text(encoding="utf-8")
        new_text = _insert_into_section(text, canon, entry)
        atomic_write(ctl_path, new_text)

    return ctl_path, entry_id


def cmd_spawn_request(
    project: str,
    *,
    fields: dict[str, str],
    config: Config | None = None,
) -> tuple[Path, str]:
    """Post a ⟦SPAWN REQUEST⟧ block, enforcing SPAWN_REQUIRED at write-time.

    Refuses (raises ValueError) on any missing/empty required field.
    Keyed off controllib.SPAWN_REQUIRED — the same constant rv control check uses.

    Returns (control_file_path, entry_id).
    """
    cfg = config or load_config()
    ctl_path = cfg.project_control_file(project)

    if not ctl_path.exists():
        cmd_init(project, config=cfg)

    _validate_required_fields(fields, SPAWN_REQUIRED, "SPAWN REQUEST")

    by = fields.get("role/lens", "anon").split()[0].lower()
    entry_id = _make_entry_id("spawn-request", fields.get("goal", "request"), by)
    block = _make_block("SPAWN REQUEST", fields)
    entry = f"- **{entry_id}**\n{block}\n"

    with locked_mutate(ctl_path):
        text = ctl_path.read_text(encoding="utf-8")
        new_text = _insert_into_section(text, "Outbox", entry)
        atomic_write(ctl_path, new_text)

    return ctl_path, entry_id


def cmd_return_entry(
    project: str,
    *,
    fields: dict[str, str],
    config: Config | None = None,
) -> tuple[Path, str]:
    """Post a ⟦RETURN⟧ block, enforcing RETURN_REQUIRED at write-time.

    Accepts arbitrary role-extra key:val fields beyond the 6 required.
    Refuses (raises ValueError) on any missing/empty required field.

    Returns (control_file_path, entry_id).
    """
    cfg = config or load_config()
    ctl_path = cfg.project_control_file(project)

    if not ctl_path.exists():
        cmd_init(project, config=cfg)

    _validate_required_fields(fields, RETURN_REQUIRED, "RETURN")

    by = "agent"
    entry_id = _make_entry_id("return", fields.get("did", "return"), by)
    block = _make_block("RETURN", fields)
    entry = f"- **{entry_id}**\n{block}\n"

    with locked_mutate(ctl_path):
        text = ctl_path.read_text(encoding="utf-8")
        new_text = _insert_into_section(text, "Outbox", entry)
        atomic_write(ctl_path, new_text)

    return ctl_path, entry_id


def _do_archive_entry(
    *,
    ctl_path: Path,
    entry_id: str,
    entry_text: str,
    text: str,
    already_locked: bool = False,
) -> str:
    """Archive one entry. Returns new file text."""
    date_tag = _today()
    one_liner = f"{date_tag} · {entry_id} — {entry_text[:60].strip()}"

    def _do(t: str) -> str:
        new_t, removed = _remove_entry(t, entry_id)
        if removed.strip():
            atomic_write(ctl_path, new_t)
            append_to_archive(ctl_path, entry_text.strip(), one_liner=one_liner)
        return new_t

    if already_locked:
        return _do(text)
    else:
        with locked_mutate(ctl_path):
            t = ctl_path.read_text(encoding="utf-8")
            return _do(t)


def cmd_close(
    project: str,
    entry_id: str,
    *,
    config: Config | None = None,
) -> Path:
    """Set the resolved marker AND archive the entry — one motion, one call.

    The entry leaves the live file and appears in .archive.md with a one-liner
    in the sidecar index. One invocation; no separate archive call.

    Returns the path to the control file.
    """
    cfg = config or load_config()
    ctl_path = cfg.project_control_file(project)

    if not ctl_path.exists():
        raise FileNotFoundError(f"No control file for {project!r}")

    with locked_mutate(ctl_path):
        text = ctl_path.read_text(encoding="utf-8")

        lines = text.splitlines(keepends=True)
        bounds = _find_entry_block(lines, entry_id)
        if bounds is None:
            raise KeyError(f"Entry {entry_id!r} not found in control file")

        # Full block (bullet + continuation/body lines) — not just the bullet.
        start, end = bounds
        entry_text = "".join(lines[start:end]).strip()

        _do_archive_entry(
            ctl_path=ctl_path,
            entry_id=entry_id,
            entry_text=entry_text,
            text=text,
            already_locked=True,
        )

    return ctl_path


def cmd_edit(
    project: str,
    entry_id: str,
    *,
    body: str | None = None,
    append: str | None = None,
    config: Config | None = None,
) -> Path:
    """Amend one entry's body in place (TARGETED — never touches other lines).

    Returns the path to the updated control file.
    """
    if body is None and append is None:
        raise ValueError("cmd_edit: provide --body or --append")

    cfg = config or load_config()
    ctl_path = cfg.project_control_file(project)

    if not ctl_path.exists():
        raise FileNotFoundError(f"No control file for {project!r}")

    with locked_mutate(ctl_path):
        text = ctl_path.read_text(encoding="utf-8")
        pattern = re.compile(
            r"(- \*\*" + re.escape(entry_id) + r"\*\*)\s*(?:—\s*)?(.*)$",
            re.IGNORECASE | re.MULTILINE,
        )
        m = pattern.search(text)
        if not m:
            raise KeyError(f"Entry {entry_id!r} not found in control file")

        if body is not None:
            new_line = f"{m.group(1)} — {body}"
        else:
            existing_body = m.group(2).strip()
            new_line = f"{m.group(1)} — {existing_body} {append}".rstrip()

        new_text = text[:m.start()] + new_line + text[m.end():]
        atomic_write(ctl_path, new_text)

    return ctl_path


def cmd_move(
    project: str,
    entry_id: str,
    *,
    to: str,
    config: Config | None = None,
) -> Path:
    """Relocate a bullet/block between the four sections.

    Returns the path to the updated control file.
    """
    cfg = config or load_config()
    ctl_path = cfg.project_control_file(project)

    if not ctl_path.exists():
        raise FileNotFoundError(f"No control file for {project!r}")

    dest_canon = _SECTION_SLUGS.get(to.lower()) or _canon_section(to)
    if dest_canon is None:
        raise ValueError(f"Unknown target section {to!r}")

    with locked_mutate(ctl_path):
        text = ctl_path.read_text(encoding="utf-8")
        new_text, removed = _remove_entry(text, entry_id)
        if not removed.strip():
            raise KeyError(f"Entry {entry_id!r} not found in control file")
        new_text = _insert_into_section(new_text, dest_canon, removed.strip() + "\n")
        atomic_write(ctl_path, new_text)

    return ctl_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,
) -> argparse.ArgumentParser:
    """Build the argument parser for the `control` verb.

    When to use: use `rv control <project> <subcommand>` to initialize, read,
    validate, reconcile, or update the coordination control file for a project.
    READ via `rv status` or `rv control reconcile` — do NOT cat/Read control/*.md
    by eye (parses stale prose, misses live git/DAG/task state; the mistaken-for-undispatched incident).
    MUTATE via post/spawn-request/return/close/edit/move — do NOT hand-edit
    control/*.md (races other agents, can author schema-invalid entries).

    Anti-pattern: do NOT open control/*.md and hand-type entries or read them raw.
    """
    desc = "Manage the project coordination control file (the crew-hub bus)."
    if parent is not None:
        p = parent.add_parser("control", help="Project coordination control file.",
                               description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv control", description=desc)

    p.add_argument("project", help="Project slug.")
    sub = p.add_subparsers(dest="control_cmd", required=True)

    # init
    init_p = sub.add_parser("init", help="Create the control file for a project.")
    init_p.add_argument("--note", default="", help="Optional creation note.")
    init_p.add_argument("--overwrite", action="store_true")

    # view
    sub.add_parser("view", help="Print the control file.")

    # check
    sub.add_parser("check", help="Validate control file structure.")

    # heal
    sub.add_parser("heal", help="Insert missing banner and fix auto-healable issues.")

    # inbox (legacy convenience)
    inbox_p = sub.add_parser("inbox", help="Append a message to the Inbox section.")
    inbox_p.add_argument("message", help="Message text.")

    # reconcile
    rec_p = sub.add_parser(
        "reconcile",
        help="Semantic-currency drift check (posted claims vs live git/DAG/task state).",
    )
    rec_p.add_argument("--archive", action="store_true",
                        help="Auto-archive entries whose id maps to a terminal live signal.")
    rec_p.add_argument(
        "--gh-pr",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Tier-3: fetch GitHub Actions CI state for PR #N and include in reconcile. "
            "Constructs a GitHubActionsSource and passes it via extra_sources. "
            "Anti-pattern: do NOT hand-type 'CI green' into a merge decision — "
            "use --gh-pr so rv reconcile fetches the Actions state and the gate "
            "refuses to record a pass on red/unverified CI."
        ),
    )
    rec_p.add_argument(
        "--repo",
        default=None,
        metavar="OWNER/REPO",
        help=(
            "GitHub repo slug (e.g. 'owner/repo'). Required with --gh-pr if "
            "auto-detection from git remote origin fails. Never hardcoded — "
            "always runtime-derived from the adopter's repo."
        ),
    )

    # post
    post_p = sub.add_parser("post", help="Append a bullet entry to a section.")
    post_p.add_argument("--section", required=True,
                         help="Target section (inbox|handshakes|outbox|open-blockers).")
    post_p.add_argument("--title", required=True, help="Entry title (used for slug).")
    post_p.add_argument("--body", default="", help="Entry body text.")
    post_p.add_argument("--kind", default="", help="Entry kind tag.")
    post_p.add_argument("--by", default="", help="Author agent name.")

    # spawn-request
    sr_p = sub.add_parser("spawn-request",
                           help="Post a ⟦SPAWN REQUEST⟧ block (schema-valid-by-construction).")
    for field in SPAWN_REQUIRED:
        sr_p.add_argument(
            f"--{field}",
            required=True,
            dest=field.replace("/", "_").replace("-", "_"),
            help=f"Required: {field}",
        )

    # return
    ret_p = sub.add_parser("return",
                            help="Post a ⟦RETURN⟧ block (schema-valid-by-construction).")
    for field in RETURN_REQUIRED:
        ret_p.add_argument(
            f"--{field}",
            required=True,
            dest=field.replace("/", "_").replace("-", "_"),
            help=f"Required: {field}",
        )
    ret_p.add_argument("--extra", nargs="*", metavar="key:val",
                       help="Role-extra fields (e.g. verdict:approve pr:#5).")

    # close
    close_p = sub.add_parser("close", help="Set resolved marker + archive in one motion.")
    close_p.add_argument("entry_id", help="The **kind:slug-by-date** entry id.")

    # edit
    edit_p = sub.add_parser("edit", help="Amend one entry's body in place (targeted).")
    edit_p.add_argument("entry_id", help="The entry id to edit.")
    edit_g = edit_p.add_mutually_exclusive_group(required=True)
    edit_g.add_argument("--body", help="Replace body.")
    edit_g.add_argument("--append", help="Append to body.")

    # move
    move_p = sub.add_parser("move", help="Relocate an entry between sections.")
    move_p.add_argument("entry_id", help="The entry id to move.")
    move_p.add_argument("--to", required=True, help="Destination section slug.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch control subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv control: config error: {e}", file=sys.stderr)
        return 1

    try:
        cmd = args.control_cmd

        if cmd == "init":
            path = cmd_init(args.project, config=cfg,
                            note=args.note, overwrite=args.overwrite)
            print(f"Created: {path}")
            return 0

        elif cmd == "view":
            print(cmd_view(args.project, config=cfg), end="")
            return 0

        elif cmd == "check":
            violations = cmd_check(args.project, config=cfg)
            if not violations:
                print(f"rv control check: OK — {args.project!r}")
                return 0
            for v in violations:
                print(f"  VIOLATION: {v}")
            return 1

        elif cmd == "heal":
            path = cmd_heal(args.project, config=cfg)
            print(f"Healed: {path}")
            return 0

        elif cmd == "inbox":
            path = cmd_inbox(args.project, args.message, config=cfg)
            print(f"Updated inbox: {path}")
            return 0

        elif cmd == "reconcile":
            # Tier-3 activation: --gh-pr N [--repo owner/repo]
            extra_sources = None
            gh_pr = getattr(args, "gh_pr", None)
            if gh_pr is not None:
                repo = _detect_github_repo(
                    getattr(args, "repo", None),
                    cwd=cfg.instance_root if hasattr(cfg, "instance_root") else None,
                )
                if repo is None:
                    print(
                        "rv control reconcile: --gh-pr requires a repo slug. "
                        "Pass --repo owner/repo or ensure git remote origin points "
                        "to a GitHub repository.",
                        file=sys.stderr,
                    )
                    return 1
                from .adapters.github_ci import GitHubActionsSource
                src = GitHubActionsSource(repo=repo, pr_number=gh_pr)
                extra_sources = [src]
                # Advisory line: human-facing CI truth (never used for gate logic)
                advisory = src.get_ci_advisory()
                print(advisory)

            findings = cmd_reconcile(
                args.project, config=cfg, archive=args.archive,
                extra_sources=extra_sources,
            )
            if not findings:
                print(f"rv control reconcile: OK — {args.project!r} (no drift detected)")
                return 0
            print(f"rv control reconcile: DRIFT — {len(findings)} finding(s):")
            for f in findings:
                print(f"  {f}")
            return 1

        elif cmd == "post":
            path, entry_id = cmd_post(
                args.project,
                section=args.section,
                title=args.title,
                body=args.body,
                kind=args.kind,
                by=args.by,
                config=cfg,
            )
            print(f"Posted: {entry_id}")
            return 0

        elif cmd == "spawn-request":
            fields = {
                f: getattr(args, f.replace("/", "_").replace("-", "_"), "")
                for f in SPAWN_REQUIRED
            }
            path, entry_id = cmd_spawn_request(args.project, fields=fields, config=cfg)
            print(f"Spawn request posted: {entry_id}")
            return 0

        elif cmd == "return":
            fields = {
                f: getattr(args, f.replace("/", "_").replace("-", "_"), "")
                for f in RETURN_REQUIRED
            }
            if hasattr(args, "extra") and args.extra:
                for item in args.extra:
                    if ":" in item:
                        k, v = item.split(":", 1)
                        fields[k.strip()] = v.strip()
            path, entry_id = cmd_return_entry(args.project, fields=fields, config=cfg)
            print(f"Return posted: {entry_id}")
            return 0

        elif cmd == "close":
            path = cmd_close(args.project, args.entry_id, config=cfg)
            print(f"Closed and archived: {args.entry_id}")
            return 0

        elif cmd == "edit":
            path = cmd_edit(
                args.project, args.entry_id,
                body=args.body, append=args.append,
                config=cfg,
            )
            print(f"Edited: {args.entry_id}")
            return 0

        elif cmd == "move":
            path = cmd_move(args.project, args.entry_id, to=args.to, config=cfg)
            print(f"Moved {args.entry_id!r} to {args.to!r}")
            return 0

    except (KeyError, FileNotFoundError, FileExistsError) as e:
        print(f"rv control: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"rv control: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv control: unexpected error: {e}", file=sys.stderr)
        return 1

    return 0
