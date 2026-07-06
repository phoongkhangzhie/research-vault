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

WHAT IS HASHED (SR-PLAN-FREEZE-RETRY extension, #23; SR-HARNESS-P2)
  SHA-256 of the canonical string built from three blocks:

  Block 1 — covers block (unchanged from SR-PLAN-1):
    "<child_id> stance=<stance_or_MISSING> plan_role=<plan_role_or_MISSING>"
    for each entry in covers:, sorted alphabetically by child_id, joined by
    newlines.

  Block 2 — retries block (new in SR-PLAN-FREEZE-RETRY):
    "<node_id> max_retries=<N>"
    for each manifest node where N = node.get("max_retries", 0) > 0,
    sorted alphabetically by node_id, joined by newlines.
    Nodes with N=0 (or absent) are OMITTED — omit-defaults ruling — so an
    all-default manifest contributes an EMPTY retries block.

  Block 3 — harness block (new in SR-HARNESS-P2):
    "<scope> harness_commit=<sha>"
    for each entry in harness_commits: frontmatter field, sorted alphabetically
    by scope.  OMITTED when harness_commits: is absent or blank — so a plan
    without harness_commits: re-derives the SAME hash as before the extension.
    Format: harness_commits: [main1=<sha>, main2=<sha>] or [shared=<sha>].

  When Block 2 is EMPTY and Block 3 is EMPTY:
    canonical = covers_block           (BYTE-IDENTICAL to pre-extension SR-PLAN-1)

  When Block 2 is NON-EMPTY, Block 3 is EMPTY:
    canonical = covers_block + "\\n" + RETRIES_SENTINEL + "\\n" + retries_block

  When Block 3 is NON-EMPTY (Block 2 may be empty or non-empty):
    canonical = <covers+retries> + "\\n" + HARNESS_SENTINEL + "\\n" + harness_block

  store_freeze_hash stores BOTH covers_hash (full: blocks 1+2+3) AND
  covers_retries_hash (harness-excluded baseline: blocks 1+2 only).
  verify_freeze_hash uses covers_retries_hash to distinguish a harness-commit
  swap (kind: "harness-commit drift") from a covers:/retries edit.

  "Missing" means the child note could not be read — the sentinel value
  MISSING_SENTINEL is used so a missing note is auditable but does not crash
  the hash.  A covers: with all-present children and one with a missing child
  will produce DIFFERENT hashes (the sentinel is included in the hash input).

  An unreadable/absent manifest_path is treated as having no nodes —
  empty retries block — which yields the covers-only hash.  This is the safe
  tamper direction: deleting the manifest after freeze with a non-zero ceiling
  collapses the retries block to empty, producing a mismatch (BLOCK).

PUBLIC API
  compute_covers_hash(plan_note_path, notes_root=None, manifest_nodes=None)
      -> str (64-char hex)
      manifest_nodes=None → byte-identical to pre-extension behavior.
  store_freeze_hash(run_store, run_id, plan_note_path, notes_root=None) -> None
      Stores {covers_hash, plan_note, notes_root (abs), frozen_at} in meta.
  verify_freeze_hash(run_store, run_id, plan_note_path, notes_root=None,
                     require_frozen=True)
      -> tuple[bool, str | None]
      Returns (True, None) on hash match.
      Returns (False, error_message) on mismatch OR when no freeze is stored
        (when require_frozen=True, the default — FAIL CLOSED).
      require_frozen=False: absence of a freeze returns (True, None) — the
        no-op escape-hatch for callers that gate on presence themselves.
      The stored notes_root pin is used for re-derivation; the caller's
        notes_root arg is used ONLY as an explicit re-pin override when the
        stored pin is absent (legacy meta back-compat).
      When the stored notes_root does not exist on disk: FAIL LOUD with a
        re-pin instruction, never silently fall back to the caller's config.
      The error message distinguishes a retry-ceiling drift (covers block
        matches but retries block differs) from a covers-set edit.

note.py-FREE (§5K.10): reads notes by path/frontmatter; does NOT import note.py.
Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..dag.store import RunStore

# Sentinel value used when a child note cannot be read.
MISSING_SENTINEL = "MISSING"

# Sentinel line separating the covers block from the retries block in the
# canonical hash input.  Low-collision: node ids are constrained identifiers
# (no spaces, no "="); this string cannot appear in a valid covers-block line.
RETRIES_SENTINEL = "---max_retries---"

# Sentinel line separating the retries block from the harness block (SR-HARNESS-P2).
# Added as a third canonical block; absent when no harness_commits: field is set.
# Low-collision for the same reason as RETRIES_SENTINEL.
HARNESS_SENTINEL = "---harness_commit---"


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


def _build_retries_block(manifest_nodes: list[dict[str, Any]]) -> str:
    """Build the sorted retries block for manifest_nodes.

    Returns a newline-joined string of "<node_id> max_retries=<N>" for every
    node whose effective max_retries > 0 (omit-defaults ruling).  An empty
    string is returned when all nodes are at the default (0 / absent).

    The block is sorted by node_id for determinism — the order of nodes in the
    manifest list is irrelevant.
    """
    retries_lines: list[str] = []
    for node in manifest_nodes:
        node_id = node.get("id", "")
        n = node.get("max_retries", 0)
        if not isinstance(n, int):
            try:
                n = int(n)
            except (TypeError, ValueError):
                n = 0
        if n > 0:
            retries_lines.append(f"{node_id} max_retries={n}")
    retries_lines.sort()
    return "\n".join(retries_lines)


def _parse_harness_commits(fields: dict[str, str]) -> list[str]:
    """Parse the harness_commits frontmatter field into a list of 'scope=sha' strings.

    Parses flat inline list ``harness_commits: [main1=<sha>, main2=<sha>]``
    (or ``[shared=<sha>]``).  Reuses ``_parse_covers_list`` for the bracket/
    comma split; each item must contain ``=`` to split into ``<scope>=<sha>``.

    Malformed items (no ``=``) are included as MISSING-style sentinel entries
    of the form ``"MISSING=<original-item>"`` — auditable but never crashing.

    Returns an empty list when the field is absent or blank.
    """
    raw = fields.get("harness_commits", "").strip()
    if not raw:
        return []
    items = _parse_covers_list(raw)
    result: list[str] = []
    for item in items:
        if "=" in item:
            result.append(item)
        else:
            # Malformed: include as sentinel so the block is non-empty
            # (auditable) but clearly signals the problem.
            result.append(f"MISSING={item}")
    return result


def _build_harness_block(commits: list[str]) -> str:
    """Build the sorted harness block from a list of 'scope=sha' strings.

    Returns a newline-joined string of ``"<scope> harness_commit=<sha>"``
    lines, sorted by scope, for determinism.  Returns an empty string when
    ``commits`` is empty.

    Input entries must be ``"<scope>=<sha>"`` pairs (as returned by
    ``_parse_harness_commits``).  Malformed entries (no ``=``) are written
    as ``"MISSING harness_commit=<item>"``.
    """
    if not commits:
        return ""
    lines: list[str] = []
    for item in commits:
        if "=" in item:
            scope, sha = item.split("=", 1)
            lines.append(f"{scope} harness_commit={sha}")
        else:
            lines.append(f"MISSING harness_commit={item}")
    lines.sort()
    return "\n".join(lines)


def _load_manifest_nodes(manifest_path: str) -> list[dict[str, Any]] | None:
    """Load the 'nodes' list from a manifest JSON file.

    Returns None if the path is unreadable or not valid JSON with a 'nodes'
    key — callers treat None as 'no nodes' (empty retries block).
    """
    try:
        text = Path(manifest_path).read_text(encoding="utf-8")
        data = json.loads(text)
        nodes = data.get("nodes")
        if isinstance(nodes, list):
            return nodes
        return None
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Internal canonical builder (shared by compute_covers_hash + store_freeze_hash)
# ---------------------------------------------------------------------------

def _build_covers_canonical(
    plan_note_path: Path,
    notes_root: Path | None,
    manifest_nodes: list[dict[str, Any]] | None,
    *,
    include_harness: bool = True,
) -> str | None:
    """Build the canonical hash-input string for the covers:-freeze-set.

    Returns ``None`` if the plan note cannot be read (unreadable/absent).

    When ``include_harness=False`` the harness block is omitted regardless of
    whether ``harness_commits:`` is present in the plan frontmatter.  This
    is the harness-EXCLUDED path used to compute ``covers_retries_hash``.

    Block structure (SR-HARNESS-P2):
      Block 1 — covers block (always present):
        "<child_id> stance=... plan_role=..." sorted by child_id

      Block 2 — retries block (omit-defaults; absent when all defaults):
        RETRIES_SENTINEL + "\\n" + "<node_id> max_retries=<N>" sorted by node_id

      Block 3 — harness block (absent when no harness_commits: field or
                               when include_harness=False):
        HARNESS_SENTINEL + "\\n" + "<scope> harness_commit=<sha>" sorted by scope

    Back-compat guarantee: when Block 2 and Block 3 are both absent (either
    naturally or by setting include_harness=False with no retries), the
    returned string is the covers block only — BYTE-IDENTICAL to the
    pre-SR-PLAN-FREEZE-RETRY / pre-SR-HARNESS-P2 canonical.
    """
    p = Path(plan_note_path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None

    fields = _parse_frontmatter_flat(text)
    covers_str = fields.get("covers", "")
    child_ids = sorted(_parse_covers_list(covers_str))

    lines: list[str] = []
    for child_id in child_ids:
        stance, plan_role = _read_child_fields(child_id, notes_root)
        lines.append(f"{child_id} stance={stance} plan_role={plan_role}")

    covers_canonical = "\n".join(lines)

    # Block 2 — retries (SR-PLAN-FREEZE-RETRY)
    if manifest_nodes is not None:
        retries_block = _build_retries_block(manifest_nodes)
    else:
        retries_block = ""

    if retries_block:
        canonical = covers_canonical + "\n" + RETRIES_SENTINEL + "\n" + retries_block
    else:
        canonical = covers_canonical  # byte-identical to pre-extension

    # Block 3 — harness (SR-HARNESS-P2); omitted when include_harness=False
    if include_harness:
        harness_commits = _parse_harness_commits(fields)
        harness_block = _build_harness_block(harness_commits)
        if harness_block:
            canonical = canonical + "\n" + HARNESS_SENTINEL + "\n" + harness_block

    return canonical


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_covers_hash(
    plan_note_path: Path,
    notes_root: Path | None = None,
    manifest_nodes: list[dict[str, Any]] | None = None,
) -> str:
    """Compute the SHA-256 hash of the plan master's covers:-freeze-set.

    WHAT IS HASHED
    --------------
    Block 1 — covers block (sorted by child_id):
      "<child_id> stance=<stance_or_MISSING> plan_role=<plan_role_or_MISSING>"

    Block 2 — retries block (SR-PLAN-FREEZE-RETRY, #23; sorted by node_id):
      "<node_id> max_retries=<N>" for each node where N > 0 (omit-defaults).
      Empty when manifest_nodes is None or all nodes are at default (0/absent).

    Block 3 — harness block (SR-HARNESS-P2; sorted by scope):
      "<scope> harness_commit=<sha>" for each entry in harness_commits:.
      Empty when harness_commits: field is absent or blank in the plan note.

    When Blocks 2 and 3 are both empty, the canonical string is the covers
    block only — BYTE-IDENTICAL to the pre-extension SR-PLAN-1 hash.
    Back-compat guarantee: a plan without harness_commits: re-derives the
    SAME hash regardless of whether the retries block is populated.

    Args:
        plan_note_path: path to the plan master note (plan_kind: preregistration).
        notes_root:     directory where child notes live (child_id.md files).
                        If None, all child fields are treated as MISSING.
        manifest_nodes: list of node dicts from the manifest JSON (the "nodes"
                        array).  If None (default), the retries block is empty —
                        byte-identical to the pre-extension behavior.

    Returns:
        64-character lowercase hex SHA-256 string.
    """
    canonical = _build_covers_canonical(
        plan_note_path, notes_root, manifest_nodes, include_harness=True
    )
    if canonical is None:
        # Plan note unreadable — return deterministic hash without crashing.
        return hashlib.sha256(b"<unreadable-plan-note>").hexdigest()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def store_freeze_hash(
    run_store: "RunStore",
    run_id: str,
    plan_note_path: Path,
    notes_root: Path | None = None,
) -> None:
    """Compute the covers:-freeze-set hash and store it in the run state's meta.

    This is the K-3 'freeze' operation — called at human-go-plan approval time.
    Reads manifest_nodes from run_state.manifest_path automatically; an
    unreadable/absent manifest_path is treated as no nodes (graceful fallback).

    Stores in run_state.meta["plan_freeze"]:
      {
        "covers_hash":         "<sha256-hex>",  — covers + retries + harness block
        "covers_retries_hash": "<sha256-hex>",  — covers + retries only (harness-EXCLUDED
                                                   baseline; equals covers_hash when no
                                                   harness_commits: field is present)
        "plan_note":   "<abs-path-str>",
        "notes_root":  "<abs-path-str>", — resolution input (caller-invariant pin)
        "frozen_at":   <unix-timestamp>,
      }

    ``covers_retries_hash`` is the plan-time baseline hash (excludes the harness
    block) and is used by ``verify_freeze_hash`` to distinguish a harness-commit
    swap (only the harness block changed) from a covers:-set or retry-ceiling edit.

    The notes_root is stored as an absolute path so verify_freeze_hash can
    re-derive with the SAME resolution inputs regardless of caller/cwd.

    Args:
        run_store:      the RunStore for this instance.
        run_id:         the DAG run id.
        plan_note_path: path to the plan master note to freeze.
        notes_root:     directory where child notes live.  Stored as absolute.

    STUB-SOUNDNESS INVARIANT (#49, RunStore.create_stub):
        This function reads ONLY run_state.meta (which it writes into) and
        run_state.manifest_path — it never reads node_states or
        edge_registered_ts.  That is exactly what makes a RunStore.create_stub()
        sidecar (empty node_states/edge_registered_ts) sufficient input: a
        consumer that never ran `rv dag run` for this run_id can still freeze
        off the stub.  A future change to this function that reads node_states
        would silently break stub-based consumers — preserve this invariant.
    """
    run_state = run_store.load(run_id)
    manifest_nodes = _load_manifest_nodes(run_state.manifest_path)

    # Full hash (covers + retries + harness if present)
    covers_hash = compute_covers_hash(
        plan_note_path,
        notes_root=notes_root,
        manifest_nodes=manifest_nodes,
    )

    # Harness-EXCLUDED hash (covers + retries only — the plan-time baseline)
    canon_no_harness = _build_covers_canonical(
        plan_note_path,
        notes_root=notes_root,
        manifest_nodes=manifest_nodes,
        include_harness=False,
    )
    if canon_no_harness is None:
        covers_retries_hash = hashlib.sha256(b"<unreadable-plan-note>").hexdigest()
    else:
        covers_retries_hash = hashlib.sha256(canon_no_harness.encode("utf-8")).hexdigest()

    run_state.meta["plan_freeze"] = {
        "covers_hash": covers_hash,
        "covers_retries_hash": covers_retries_hash,
        "plan_note": str(Path(plan_note_path).resolve()),
        "notes_root": str(Path(notes_root).resolve()) if notes_root is not None else None,
        "frozen_at": time.time(),
    }
    run_store.save(run_state)


def verify_freeze_hash(
    run_store: "RunStore",
    run_id: str,
    plan_note_path: Path,
    notes_root: Path | None = None,
    *,
    require_frozen: bool = True,
) -> tuple[bool, str | None]:
    """Re-derive the covers:-freeze-set hash and compare to the stored value.

    This is the K-3 're-verify' operation — called at human-go-findings time.
    Reads manifest_nodes from run_state.manifest_path automatically; an
    unreadable/absent manifest_path is treated as no nodes (graceful fallback).

    FAIL CLOSED (SR-FREEZE-FIX hole a):
        When no freeze is stored AND require_frozen=True (the default), returns
        (False, "…not frozen…") — NEVER (True, None).  A never-frozen run must
        NOT pass the K-3 gate silently.
        Pass require_frozen=False only if the caller already gates on presence
        (e.g. rv dag approve checks plan_freeze presence before calling here).

    CALLER-INVARIANT (SR-FREEZE-FIX hole b):
        Uses the STORED notes_root pin for re-derivation, NOT the caller's arg.
        The caller's notes_root is accepted ONLY as an explicit re-pin override
        when the stored pin is absent (legacy meta back-compat path).
        When the stored notes_root does not exist: FAIL LOUD with a re-pin
        instruction — never silently fall back to the caller's config.

    Returns:
        (True, None)  — hash matches.
        (False, msg)  — hash MISMATCH, run not frozen (require_frozen=True),
                        or stored notes_root missing/unreachable.
                        The message distinguishes a retry-ceiling drift (covers
                        block matches but retries block differs) from a covers-
                        set edit, and flags relocation vs normal mismatch.

    Args:
        run_store:       the RunStore for this instance.
        run_id:          the DAG run id.
        plan_note_path:  path to the plan master note to re-derive from.
        notes_root:      explicit re-pin override (only used when the stored pin
                         is absent — legacy meta back-compat or relocation).
        require_frozen:  if True (default), absent freeze → (False, "not frozen").
                         if False, absent freeze → (True, None) — no-op.

    STUB-SOUNDNESS INVARIANT (#49, RunStore.create_stub):
        This function reads ONLY run_state.meta and run_state.manifest_path —
        never node_states or edge_registered_ts.  A RunStore.create_stub()
        sidecar therefore verifies exactly like a run created by `rv dag run`.
        Preserve this invariant if you touch this function; reading node_states
        here would silently break stub-based (foreign-engine) consumers.
    """
    import warnings

    run_state = run_store.load(run_id)
    plan_freeze = run_state.meta.get("plan_freeze")

    if not plan_freeze:
        # SR-FREEZE-FIX hole (a): fail CLOSED on absent freeze.
        if require_frozen:
            return False, (
                f"run {run_id!r} not frozen — run `rv plan freeze {run_id} "
                f"<plan-note>` first to establish the K-3 pre-registration hash."
            )
        # Escape-hatch for callers that already gate on presence.
        return True, None

    stored_hash = plan_freeze.get("covers_hash", "")
    stored_notes_root_str = plan_freeze.get("notes_root")  # may be absent (legacy)

    # --- Resolve the notes_root to use for re-derivation (SR-FREEZE-FIX hole b) ---
    if stored_notes_root_str is not None:
        # New format: use the STORED pin.
        stored_notes_root = Path(stored_notes_root_str)
        if not stored_notes_root.exists():
            # Stored pin no longer on disk — FAIL LOUD, never silent fallback.
            return False, (
                f"frozen notes_root {str(stored_notes_root)!r} not found on disk — "
                f"the notes tree may have been relocated.  Pass --notes-root to "
                f"re-pin against the moved tree, then re-run freeze before verifying."
            )
        # Caller's notes_root is IGNORED when the stored pin is present and valid.
        effective_notes_root = stored_notes_root
    else:
        # Legacy meta: no notes_root field.  Require explicit caller arg; never guess.
        if notes_root is None:
            warnings.warn(
                f"plan_freeze for run {run_id!r} has no stored notes_root "
                f"(legacy format).  Pass --notes-root explicitly to re-pin "
                f"the verification against the correct notes directory.",
                UserWarning,
                stacklevel=2,
            )
            return False, (
                f"run {run_id!r} has a legacy plan_freeze with no stored "
                f"notes_root — cannot verify caller-invariantly without it.  "
                f"Pass --notes-root explicitly to re-pin the verification."
            )
        # Explicit caller arg provided: use it as the re-pin (with a notice).
        warnings.warn(
            f"Using caller-supplied --notes-root as a re-pin for legacy "
            f"plan_freeze (run {run_id!r}).  The stored hash was computed at an "
            f"unknown notes_root; this verification assumes the supplied path "
            f"matches the original freeze-time tree.",
            UserWarning,
            stacklevel=2,
        )
        effective_notes_root = Path(notes_root)

    manifest_nodes = _load_manifest_nodes(run_state.manifest_path)
    current_hash = compute_covers_hash(
        plan_note_path,
        notes_root=effective_notes_root,
        manifest_nodes=manifest_nodes,
    )

    if current_hash == stored_hash:
        return True, None

    frozen_at = plan_freeze.get("frozen_at", "?")

    # --- SR-HARNESS-P2: harness-commit drift check ---
    # When covers_retries_hash is stored (new format), we can distinguish a
    # post-approval harness-SHA swap (only Block 3 changed) from a covers:/
    # retries edit (Blocks 1/2 changed).  Legacy plan_freeze records without
    # covers_retries_hash fall through to the existing covers/retries analysis.
    stored_retries_hash = plan_freeze.get("covers_retries_hash")
    if stored_retries_hash is not None:
        canon_no_harness = _build_covers_canonical(
            plan_note_path,
            notes_root=effective_notes_root,
            manifest_nodes=manifest_nodes,
            include_harness=False,
        )
        if canon_no_harness is not None:
            current_retries_hash = hashlib.sha256(
                canon_no_harness.encode("utf-8")
            ).hexdigest()
            if current_retries_hash == stored_retries_hash:
                # Covers + retries match the baseline; harness block alone changed.
                return False, (
                    f"K-3 freeze mismatch (harness-commit drift) for run "
                    f"{run_id!r}: covers:/retries match the pre-registration "
                    f"baseline but harness_commits: was changed after "
                    f"human-go-plan approval. "
                    f"stored hash {stored_hash[:16]}… ≠ "
                    f"current hash {current_hash[:16]}… "
                    f"(frozen at {frozen_at}). "
                    f"A post-approval harness SHA swap is a pre-registration "
                    f"integrity violation — issue a new pre-registration rather "
                    f"than re-approving."
                )

    # --- Existing covers/retries drift analysis ---
    # Determine whether it's a covers drift or a retry-ceiling drift (or both).
    covers_only_stored = plan_freeze.get("covers_hash", "")
    covers_only_current = compute_covers_hash(
        plan_note_path,
        notes_root=effective_notes_root,  # use stored pin, not caller's arg (diagnosis consistency)
        manifest_nodes=None,  # covers-only (pre-extension path)
    )

    # Check if the covers block itself changed vs a retry-only drift.
    # We recompute stored covers-only hash by re-hashing with manifest_nodes=None
    # at freeze time — we don't store it separately.  Instead: if the current
    # covers-only hash equals what we'd get with manifest_nodes=None, AND the
    # full hash differs, the drift is in the retries block only.
    # (We can't replay the stored manifest_nodes, so we check the current state:
    # if covers-only current == covers-only stored, the covers set is unchanged.)
    # To get "stored covers-only": we'd need to have stored it.  Instead, we
    # check whether the mismatch survives stripping the retries block:
    # compute covers-only for CURRENT and see if that hash == stored_hash.
    # If yes → the stored hash was covers-only → retries were added post-freeze.
    # If no  → covers block also changed (or both changed).

    covers_current_only = covers_only_current  # covers block, no retries

    if covers_current_only == stored_hash:
        # The stored hash was covers-only (manifest had all-default ceilings at
        # freeze time); retries were ADDED post-freeze.
        kind = "retry-ceiling drift"
        detail = (
            "A max_retries ceiling was added to one or more nodes after "
            "human-go-plan (the stored hash matches the covers-only baseline; "
            "the current manifest introduces a non-zero ceiling). "
            "This is a stopping-rule change after pre-registration."
        )
    else:
        # Compute current hash without retries to see if the covers block changed
        # independently.  We compare covers_current_only vs stored to see if a
        # covers-only freeze would still match; if not, the covers block drifted.
        # Since we can't fully separate, report both possibilities.
        kind = "covers:-set or retry-ceiling drift"
        detail = (
            "The confirmatory covers: set and/or a node's max_retries ceiling "
            "was edited after human-go-plan. "
            "A post-freeze edit is a pre-registration integrity violation — "
            "review the git diff for changes to the plan note, child notes, or "
            "manifest max_retries fields, then issue a new pre-registration "
            "rather than re-approving."
        )

    return False, (
        f"K-3 freeze mismatch ({kind}) for run {run_id!r}: "
        f"stored hash {stored_hash[:16]}… ≠ current hash {current_hash[:16]}… "
        f"(frozen at {frozen_at}). "
        f"{detail}"
    )
