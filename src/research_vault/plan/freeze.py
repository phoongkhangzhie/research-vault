"""plan/freeze.py — K-3 covers:-freeze-set hash (SR-PLAN-1, §5K.5.1).

PURPOSE (§5K.5.1 K-3 FIX)
  A git commit at the human-go-plan gate is a convention, not a structural
  tamper-record — it can be amended, skipped, or the repo may not commit at
  gate time.  Instead, this module hashes the frozen `covers:` set (the sorted
  child-id list + each child's `stance`/`plan_role`) into the DAG run state at
  `human-go-plan` approval, and re-verifies at `human-go-findings` (or any
  later gate).  A post-freeze edit to the confirmatory set (added / removed /
  relabeled child) is caught structurally by the run-state hash, independently
  of git.

WHAT IS HASHED
  SHA-256 of the canonical newline-joined representation of:
    "<child_id> stance=<stance_or_MISSING> plan_role=<plan_role_or_MISSING>"
  for each entry in covers: (sorted alphabetically by child_id).

  "Missing" means the child note could not be read — the sentinel value
  MISSING_SENTINEL is used so a missing note is auditable but does not crash
  the hash.  A covers: with all-present children and one with a missing child
  will produce DIFFERENT hashes (the sentinel is included in the hash input).

PUBLIC API
  compute_covers_hash(plan_note_path, notes_root=None) -> str (64-char hex)
  store_freeze_hash(run_store, run_id, plan_note_path, notes_root=None) -> None
  verify_freeze_hash(run_store, run_id, plan_note_path, notes_root=None)
      -> tuple[bool, str | None]
      Returns (True, None) on match or when no freeze is stored.
      Returns (False, error_message) on mismatch.

note.py-FREE (§5K.10): reads notes by path/frontmatter; does NOT import note.py.
Stdlib only.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..dag.store import RunStore

# Sentinel value used when a child note cannot be read.
MISSING_SENTINEL = "MISSING"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_frontmatter_flat(text: str) -> dict[str, str]:
    """Parse YAML-frontmatter from a markdown note — returns flat key→value dict.

    Mirrors the note.py contract: key = ``^(\\w[\\w_-]*)`` regex.
    Values are stripped; quoted strings are unquoted (single or double quotes).
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_block = text[3:end].strip()
    import re
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = re.match(r"^(\w[\w_-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith(("'", '"')) and val.endswith(val[0]):
                val = val[1:-1]
            fields[key] = val
    return fields


def _parse_covers_list(covers_str: str) -> list[str]:
    """Parse a flat YAML inline list like '[a, b, c]' into Python list."""
    s = covers_str.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [item.strip() for item in s.split(",") if item.strip()]


def _read_child_fields(
    child_id: str,
    notes_root: Path | None,
) -> tuple[str, str]:
    """Return (stance, plan_role) for a child note id.

    If notes_root is None or the child note file cannot be found/read,
    returns (MISSING_SENTINEL, MISSING_SENTINEL).

    Child note path: <notes_root>/<child_id>.md
    """
    if notes_root is None:
        return MISSING_SENTINEL, MISSING_SENTINEL

    candidate = notes_root / f"{child_id}.md"
    if not candidate.exists():
        return MISSING_SENTINEL, MISSING_SENTINEL

    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError:
        return MISSING_SENTINEL, MISSING_SENTINEL

    fields = _parse_frontmatter_flat(text)
    stance = fields.get("stance", MISSING_SENTINEL)
    plan_role = fields.get("plan_role", MISSING_SENTINEL)
    return stance, plan_role


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_covers_hash(
    plan_note_path: Path,
    notes_root: Path | None = None,
) -> str:
    """Compute the SHA-256 hash of the plan master's covers:-freeze-set.

    The hash input is the canonical newline-joined representation of:
      "<child_id> stance=<stance_or_MISSING> plan_role=<plan_role_or_MISSING>"
    for each entry in covers:, sorted alphabetically by child_id.

    Args:
        plan_note_path: path to the plan master note (plan_kind: preregistration).
        notes_root:     directory where child notes live (child_id.md files).
                        If None, all child fields are treated as MISSING.

    Returns:
        64-character lowercase hex SHA-256 string.
    """
    p = Path(plan_note_path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        # Return a deterministic hash of an empty covers set to avoid crashing.
        return hashlib.sha256(b"<unreadable-plan-note>").hexdigest()

    fields = _parse_frontmatter_flat(text)
    covers_str = fields.get("covers", "")
    child_ids = sorted(_parse_covers_list(covers_str))

    lines: list[str] = []
    for child_id in child_ids:
        stance, plan_role = _read_child_fields(child_id, notes_root)
        lines.append(f"{child_id} stance={stance} plan_role={plan_role}")

    canonical = "\n".join(lines)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def store_freeze_hash(
    run_store: "RunStore",
    run_id: str,
    plan_note_path: Path,
    notes_root: Path | None = None,
) -> None:
    """Compute the covers:-freeze-set hash and store it in the run state's meta.

    This is the K-3 'freeze' operation — called at human-go-plan approval time.

    Stores in run_state.meta["plan_freeze"]:
      {
        "covers_hash": "<sha256-hex>",
        "plan_note":   "<abs-path-str>",
        "frozen_at":   <unix-timestamp>,
      }

    Args:
        run_store:      the RunStore for this instance.
        run_id:         the DAG run id.
        plan_note_path: path to the plan master note to freeze.
        notes_root:     directory where child notes live.
    """
    run_state = run_store.load(run_id)
    covers_hash = compute_covers_hash(plan_note_path, notes_root=notes_root)
    run_state.meta["plan_freeze"] = {
        "covers_hash": covers_hash,
        "plan_note": str(Path(plan_note_path).resolve()),
        "frozen_at": time.time(),
    }
    run_store.save(run_state)


def verify_freeze_hash(
    run_store: "RunStore",
    run_id: str,
    plan_note_path: Path,
    notes_root: Path | None = None,
) -> tuple[bool, str | None]:
    """Re-derive the covers:-freeze-set hash and compare to the stored value.

    This is the K-3 're-verify' operation — called at human-go-findings time.

    Returns:
        (True, None)  — hash matches, or no freeze was stored (no-op).
        (False, msg)  — hash MISMATCH; msg describes the failure.

    Args:
        run_store:      the RunStore for this instance.
        run_id:         the DAG run id.
        plan_note_path: path to the plan master note to re-derive from.
        notes_root:     directory where child notes live.
    """
    run_state = run_store.load(run_id)
    plan_freeze = run_state.meta.get("plan_freeze")

    if not plan_freeze:
        # No freeze hash stored — no-op (plan freeze is optional; runs without
        # a plan master do not require K-3 verification).
        return True, None

    stored_hash = plan_freeze.get("covers_hash", "")
    current_hash = compute_covers_hash(plan_note_path, notes_root=notes_root)

    if current_hash != stored_hash:
        return False, (
            f"K-3 covers:-freeze mismatch for run {run_id!r}: "
            f"stored hash {stored_hash[:16]}… ≠ current hash {current_hash[:16]}…. "
            f"The confirmatory covers: set was edited after human-go-plan "
            f"(frozen at {plan_freeze.get('frozen_at', '?')}). "
            f"A post-freeze edit to the confirmatory set is a pre-registration "
            f"integrity violation — review the diff, then issue a new "
            f"pre-registration rather than re-approving."
        )

    return True, None
