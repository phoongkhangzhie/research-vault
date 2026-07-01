"""controllib.py — shared control-plane parser for Research Vault.

ONE parser for control-file artifacts, used by BOTH faces of the plane:
  - `rv control check`       — schema-validates well-formedness
  - `rv status`              — summarizes posted state
  - `rv control reconcile`   — semantic-currency drift check

Parses:
  1. Fixed sections of control/<project>.md:
     Inbox / Handshakes / Outbox / Open / blockers
  2. ⟦SPAWN REQUEST⟧ / ⟦RETURN⟧ delimited blocks with required fields.

──────────────────────────────────────────────────────────────────────────────
THE INVESTIGATE-BOUNDARY (read before extending the status side):

  The control plane reads what owners POSTED — the durable record. It NEVER
  investigates. It does not ssh a cluster, count run outputs, or diagnose *why*
  something failed — that is the owning manager's loop, run through its doers.
  If posted state is stale or thin, ping the manager to refresh — do not go
  look. (This boundary is also stated in `rv status --help`.)
──────────────────────────────────────────────────────────────────────────────

Stdlib only. Re-implemented fresh for research-vault; not copied byte-for-byte
from the live vault's controllib reference implementation.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import re
from collections import namedtuple
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS: list[str] = ["Inbox", "Handshakes", "Outbox", "Open / blockers"]

# Required fields on ⟦SPAWN REQUEST⟧ — 11 fields verbatim.
# The write verb keys off THIS constant so the writer and `rv control check`
# can never disagree. If this list evolves, cmd_spawn_request follows it.
SPAWN_REQUIRED: list[str] = [
    "role/lens", "why", "goal", "scope", "deliverable",
    "form", "urgency", "tier", "depends-on", "inputs", "done-when",
]

# Required fields on ⟦RETURN⟧ — 6 core fields.
# Role-extra key:val fields (pr, verdict, …) are accepted but not required here.
RETURN_REQUIRED: list[str] = [
    "did", "outcome", "confidence", "next", "provenance", "retro",
]

# Role-specific extras — recognized so multi-field lines split cleanly,
# but never *required* by the core schema (the role doc owns those).
_ROLE_EXTRAS: list[str] = [
    "pr", "ci", "verdict", "artifact", "bearing",
    "architecture", "coherence", "stack-impact", "requested",
    "merge", "self-review", "depends",
]

# All known keys (restricts splits so colons in URLs/values aren't mistaken
# for field boundaries).
KNOWN_KEYS: frozenset[str] = (
    frozenset(SPAWN_REQUIRED) | frozenset(RETURN_REQUIRED) | frozenset(_ROLE_EXTRAS)
)

# Fixed "not-yet" lexicon for R1 (reconcile).
# A line containing an id from the LIVE set AND one of these phrases is STALE.
NOT_YET_LEXICON: frozenset[str] = frozenset({
    "next dispatch",
    "undispatched",
    "pending",
    "not started",
    "to dispatch",
    "awaiting dispatch",
})

# The top-of-file banner enforcing the tooled read/write path.
# Inserted by cmd_init; checked by cmd_check; inserted by cmd_heal.
BANNER_TEXT = (
    "> ⚠ Read via `rv status <project>` or `rv control reconcile <project>` "
    "— do NOT parse by eye. "
    "Mutate via `rv control post/spawn-request/return/close/edit/move`, "
    "NEVER hand-edit this file — a raw edit races other agents and can write a "
    "schema-invalid entry. The prose below can be STALE; reconcile checks it "
    "against live git/DAG/task state."
)

BLOCK_MARKERS: dict[str, str] = {
    "SPAWN REQUEST": "SPAWN REQUEST",
    "RETURN": "RETURN",
}

# Archive sidecar index region delimiters (MEMORY.md shape).
ARCHIVE_INDEX_START = "<!-- INDEX:START -->"
ARCHIVE_INDEX_END = "<!-- INDEX:END -->"

# Resolved-but-unarchived threshold. A control file with more than this many
# resolved entries triggers the teeth check (git-hook/CI).
RESOLVED_THRESHOLD = 5

# ---------------------------------------------------------------------------
# Namedtuples
# ---------------------------------------------------------------------------

Block = namedtuple("Block", ["kind", "fields", "lineno", "raw"])
Violation = namedtuple("Violation", ["level", "lineno", "msg"])
ControlFile = namedtuple("ControlFile", ["path", "sections", "blocks", "has_banner"])

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_FENCE_RE = re.compile(r"^\s*```")
_MARKER_RE = re.compile(r"⟦\s*([^⟧]*?)\s*⟧")
_KEY_RE = re.compile(r"([A-Za-z][\w/-]*):")

# ID-token patterns: sr-N, sr-cp, sr-xp, etc. (sr- followed by alphanumerics)
_ID_TOKEN_RE = re.compile(r"\b(sr-[a-z0-9]+)\b", re.IGNORECASE)

# Resolved markers: CLOSED: prefix (any position after bullet), [x] checkboxes
# Matches: "- CLOSED: ...", "- CLOSED:**...** — ...", "- **CLOSED:...**"
_CLOSED_PREFIX_RE = re.compile(r"^[-*]\s+CLOSED[\s:*]", re.IGNORECASE)
_CHECKED_BOX_RE = re.compile(r"^[-*]\s+\[x\]", re.IGNORECASE)

# Entry id pattern in bold: **kind:slug-agent-date**
_ENTRY_ID_RE = re.compile(r"\*\*([^*]+:[^*]+)\*\*")

# ---------------------------------------------------------------------------
# Section canonicalization
# ---------------------------------------------------------------------------

_SECTION_ALIASES: dict[str, str] = {
    "open / blockers": "Open / blockers",
    "open/blockers": "Open / blockers",
    "open-findings": "Open / blockers",
    "open findings": "Open / blockers",
    "open blockers": "Open / blockers",
    "inbox": "Inbox",
    "handshakes": "Handshakes",
    "outbox": "Outbox",
}


def _canon_section(title: str) -> str | None:
    """Map a raw header title to its canonical section name, or None."""
    low = title.strip().lower()
    # Direct prefix match first
    for canon in REQUIRED_SECTIONS:
        if low.startswith(canon.lower()):
            return canon
    # Alias map
    return _SECTION_ALIASES.get(low)


# ---------------------------------------------------------------------------
# Block parsing (⟦SPAWN REQUEST⟧ / ⟦RETURN⟧)
# ---------------------------------------------------------------------------

def _split_line_fields(line: str) -> list[tuple[str, str]] | None:
    """Split one block line into [(key, value), …] on KNOWN-key boundaries.

    Returns None if the line is a continuation of the previous field
    (no recognized key at its start position).
    """
    bounds = []
    for m in _KEY_RE.finditer(line):
        key = m.group(1).lower()
        if key in KNOWN_KEYS:
            bounds.append((m.start(1), m.end(), key))
    if not bounds or bounds[0][0] != 0:
        return None
    segs = []
    for i, (start, after_colon, key) in enumerate(bounds):
        end = bounds[i + 1][0] if i + 1 < len(bounds) else len(line)
        segs.append((key, line[after_colon:end].strip()))
    return segs


def _parse_block(lines: list[str], start: int) -> tuple[Block, int]:
    """Parse a ⟦…⟧ block beginning at index `start`. Returns (Block, next_index)."""
    marker_line = lines[start]
    mm = _MARKER_RE.search(marker_line)
    raw_kind = (mm.group(1).strip() if mm else "").upper()
    kind = BLOCK_MARKERS.get(raw_kind.split("(")[0].strip())
    fields: dict[str, str] = {}
    order: list[str] = []
    cur: str | None = None
    i = start + 1
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if stripped == "" or _FENCE_RE.match(raw) or _HEADER_RE.match(raw) or "⟦" in raw:
            break
        segs = _split_line_fields(stripped)
        if segs is None:
            if cur is not None:
                fields[cur] = (fields[cur] + " " + stripped).strip()
        else:
            for key, val in segs:
                if key not in fields:
                    order.append(key)
                fields[key] = val
                cur = key
        i += 1
    return Block(kind=kind, fields=fields, lineno=start + 1, raw="\n".join(lines[start:i])), i


# ---------------------------------------------------------------------------
# Control file parsing
# ---------------------------------------------------------------------------

def parse_control_file(path: Path | str) -> ControlFile:
    """Parse a control file into its fixed sections + ⟦…⟧ blocks. Pure read."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return parse_control_text(text, path=path)


def parse_control_text(text: str, *, path: Path | str = Path("<memory>")) -> ControlFile:
    """Parse control-file text (without requiring a real file on disk)."""
    path = Path(path)
    lines = text.splitlines()
    sections: dict[str, dict] = {}
    blocks: list[Block] = []
    has_banner = BANNER_TEXT[:40] in text or (
        "rv status" in text and "rv control reconcile" in text
        and ("hand-edit" in text or "NEVER hand-edit" in text)
    )
    cur_canon: str | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        hm = _HEADER_RE.match(line)
        if hm and len(hm.group(1)) == 2:  # ## section header
            canon = _canon_section(hm.group(2))
            cur_canon = canon
            if canon and canon not in sections:
                sections[canon] = {"title": hm.group(2), "lineno": i + 1, "lines": []}
            i += 1
            continue
        if "⟦" in line and _MARKER_RE.search(line):
            block, nxt = _parse_block(lines, i)
            blocks.append(block)
            i = nxt
            continue
        if cur_canon and cur_canon in sections:
            sections[cur_canon]["lines"].append(line)
        i += 1
    return ControlFile(path=path, sections=sections, blocks=blocks, has_banner=has_banner)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_control_file(path: Path | str) -> list[Violation]:
    """Schema-validate well-formedness. Returns list of Violation (level ∈ {error, warn}).

    Checks:
      - every required fixed section is present;
      - the top-of-file banner is present (warn if missing);
      - every ⟦…⟧ marker resolves to a known kind;
      - every ⟦SPAWN REQUEST⟧ / ⟦RETURN⟧ carries its required fields, non-empty.
    """
    vios: list[Violation] = []
    cf = parse_control_file(path)

    if not cf.has_banner:
        vios.append(Violation("warn", 0,
                               "missing tooled-path banner (run `rv control heal` to insert)"))

    for sec in REQUIRED_SECTIONS:
        if sec not in cf.sections:
            vios.append(Violation("error", 0, f"missing required section: ## {sec}"))

    for blk in cf.blocks:
        if blk.kind is None:
            vios.append(Violation("error", blk.lineno,
                                   "unrecognized ⟦…⟧ block marker "
                                   "(expected SPAWN REQUEST or RETURN)"))
            continue
        required = SPAWN_REQUIRED if blk.kind == "SPAWN REQUEST" else RETURN_REQUIRED
        for field in required:
            if field not in blk.fields:
                vios.append(Violation("error", blk.lineno,
                                       f"⟦{blk.kind}⟧ missing required field: {field}"))
            elif not blk.fields[field]:
                vios.append(Violation("error", blk.lineno,
                                       f"⟦{blk.kind}⟧ field is empty: {field}"))
    return vios


def format_violations(path: Path | str, vios: list[Violation]) -> str:
    """Render violations clearly: file:line prefixed."""
    name = Path(path).name
    if not vios:
        return f"  ✓ {name}: well-formed"
    out = [f"  ✗ {name}: {len(vios)} violation(s)"]
    for v in sorted(vios, key=lambda x: x.lineno):
        loc = f"line {v.lineno}" if v.lineno else "file"
        out.append(f"      [{v.level.upper()}] {loc}: {v.msg}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Section helpers (for status/reconcile)
# ---------------------------------------------------------------------------

def section_items(cf: ControlFile, canon: str) -> list[dict]:
    """Return classified bullet lines in a section.

    Returns list of dicts: {text, open, checkbox, entry_id}.
    - open=True for unchecked `- [ ]` items.
    - checkbox=True if the bullet had a `- [ ]`/`- [x]`.
    - entry_id: the bold **kind:slug** id if present, else None.
    """
    items = []
    sec = cf.sections.get(canon)
    if not sec:
        return items
    for ln in sec["lines"]:
        s = ln.strip()
        if not s.startswith("- ") and not s.startswith("* "):
            continue
        body = s[2:]
        # Checkbox?
        cm = re.match(r"\[([ xX])\]\s*(.*)$", body)
        if cm:
            entry_id_m = _ENTRY_ID_RE.search(cm.group(2))
            items.append({
                "text": cm.group(2).strip(),
                "open": cm.group(1) == " ",
                "checkbox": True,
                "resolved": cm.group(1).lower() == "x",
                "entry_id": entry_id_m.group(1) if entry_id_m else None,
                "raw_line": s,
            })
        else:
            entry_id_m = _ENTRY_ID_RE.search(body)
            closed = bool(_CLOSED_PREFIX_RE.match(s))
            items.append({
                "text": body.strip(),
                "open": False,
                "checkbox": False,
                "resolved": closed,
                "entry_id": entry_id_m.group(1) if entry_id_m else None,
                "raw_line": s,
            })
    return items


def extract_id_tokens(text: str) -> list[str]:
    """Extract SR id tokens from a line of text (case-normalized to lowercase)."""
    return [m.group(1).lower() for m in _ID_TOKEN_RE.finditer(text)]


def count_resolved_markers(text: str) -> int:
    """Count resolved-but-potentially-unarchived markers in a control file text.

    Counts: CLOSED: prefix lines, [x] checked lines.
    This is the resolved-count teeth numerator.
    """
    count = 0
    for line in text.splitlines():
        s = line.strip()
        if _CLOSED_PREFIX_RE.match(s) or _CHECKED_BOX_RE.match(s):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Atomic write (no partial-write)
# ---------------------------------------------------------------------------

def atomic_write(path: Path, text: str) -> None:
    """Write `text` to `path` atomically: write to .tmp then rename.

    Prevents torn reads. Does NOT prevent concurrent read-modify-write clobbers —
    for that, use locked_mutate() which wraps this function under an advisory lock.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Advisory lock (prevents concurrent read-modify-write clobbers)
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def locked_mutate(control_path: Path) -> Iterator[None]:
    """Context manager: hold a POSIX advisory flock on <control>.lock for the duration.

    Usage:
        with locked_mutate(control_path):
            text = control_path.read_text()
            text = _mutate(text)
            atomic_write(control_path, text)

    The lock is a .lock sidecar file (not the control file itself) so readers
    can always open the control file without waiting for the lock.

    POSIX only (macOS + Linux). A Windows fallback is a noted seam, out of v1 scope.
    """
    lock_path = control_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Archive sidecar helpers
# ---------------------------------------------------------------------------

def _archive_path(control_path: Path) -> Path:
    """Return the archive sidecar path for a control file."""
    return control_path.parent / (control_path.stem + ".archive.md")


def _parse_archive_index(archive_text: str) -> list[str]:
    """Parse the one-liner index lines from an archive sidecar."""
    lines = []
    in_index = False
    for ln in archive_text.splitlines():
        if ln.strip() == ARCHIVE_INDEX_START:
            in_index = True
            continue
        if ln.strip() == ARCHIVE_INDEX_END:
            break
        if in_index and ln.strip().startswith("- "):
            lines.append(ln.strip())
    return lines


def append_to_archive(
    control_path: Path,
    entry_text: str,
    *,
    one_liner: str,
) -> None:
    """Append an archived entry to the sidecar, maintaining the index region.

    - one_liner: the index summary line (e.g. "2026-07-01 · handshake:foo — title")
    - entry_text: the full entry text to append to the archive body.

    The sidecar format:
        <!-- INDEX:START -->
        - <date> · <id> — <title>
        <!-- INDEX:END -->
        ---
        ## ARCHIVED ENTRIES
        <full entry>
    """
    archive = _archive_path(control_path)
    with locked_mutate(archive):
        if archive.exists():
            existing = archive.read_text(encoding="utf-8")
        else:
            existing = ""

        # Build the new index lines
        old_index = _parse_archive_index(existing)
        new_index = old_index + [f"- {one_liner}"]

        # Rebuild the file
        index_block = (
            ARCHIVE_INDEX_START + "\n"
            + "\n".join(new_index) + "\n"
            + ARCHIVE_INDEX_END
        )

        # Extract existing body (below INDEX:END)
        if ARCHIVE_INDEX_END in existing:
            after_index = existing.split(ARCHIVE_INDEX_END, 1)[1]
        elif existing.strip():
            # Legacy: no index yet, treat all as body
            after_index = "\n\n---\n\n## ARCHIVED ENTRIES\n\n" + existing
        else:
            after_index = "\n\n---\n\n## ARCHIVED ENTRIES\n"

        # Append the new entry
        new_body = after_index.rstrip("\n") + "\n\n" + entry_text.strip() + "\n"

        new_text = index_block + new_body
        atomic_write(archive, new_text)


def regenerate_archive_index(control_path: Path) -> None:
    """Regenerate the archive sidecar index from its full entry bodies.

    Idempotent: regenerating twice produces no diff.
    """
    archive = _archive_path(control_path)
    if not archive.exists():
        return
    with locked_mutate(archive):
        text = archive.read_text(encoding="utf-8")
        # Extract body section
        if ARCHIVE_INDEX_END in text:
            body_part = text.split(ARCHIVE_INDEX_END, 1)[1]
        else:
            body_part = text

        # Parse entries: each starts with ## or a bold entry id
        one_liners = []
        current_date = ""
        for ln in body_part.splitlines():
            s = ln.strip()
            # Date header
            dm = re.match(r"^## (\d{4}-\d{2}-\d{2})", s)
            if dm:
                current_date = dm.group(1)
                continue
            # Entry id line
            em = _ENTRY_ID_RE.search(s)
            if em and s.startswith("-"):
                entry_id = em.group(1)
                # Extract title from body
                after_id = s[s.index("**", s.index("**") + 2) + 2:].strip()
                if after_id.startswith("—"):
                    after_id = after_id[1:].strip()
                title = after_id[:60] or entry_id
                date_tag = current_date or "unknown"
                one_liners.append(f"- {date_tag} · {entry_id} — {title}")

        if not one_liners:
            return

        index_block = (
            ARCHIVE_INDEX_START + "\n"
            + "\n".join(one_liners) + "\n"
            + ARCHIVE_INDEX_END
        )

        if ARCHIVE_INDEX_END in text:
            body_part_raw = text.split(ARCHIVE_INDEX_END, 1)[1]
        else:
            body_part_raw = "\n\n---\n\n## ARCHIVED ENTRIES\n\n" + text

        new_text = index_block + body_part_raw
        atomic_write(archive, new_text)
