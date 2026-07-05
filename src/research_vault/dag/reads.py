"""reads.py — SR-SCOPE: reads: pointer resolution for Research Vault DAG.

This module is the I/O-touching, filesystem-aware RESOLUTION pass for the reads:
field on DAG agent nodes. It MUST NOT be imported by schema.py or walker.py —
those are deliberately pure/in-memory/stdlib-only. This module is called by
verbs (dag run / tick) AFTER the pure validate_manifest pass.

Purity boundary (established by SR-DISP and honoured here):
  - validate_manifest (dag/schema.py): pure, in-memory, no I/O, ManifestError only.
  - resolve_reads_pointers (this module): I/O-touching, called at run/tick time.

Pointer grammar (typed by form):
  bare path string      → FILE: resolves relative to project_root; must exist.
  <file>#<anchor>       → DOC/TASK SECTION: file exists AND markdown anchor found.
  control/<p>.md#<slug> → BUS REF: same as doc#anchor — file + section exist.
  path:symbol           → SYMBOL: file resolves HARD (error if absent);
                          symbol is SOFT (warn if not found; no AST coupling).

Resolution reuses the filesystem-access pattern of wait_for.resolve_watch
(the SR-2 seam) for file-existence checks, plus a thin anchor-search helper
(not in resolve_watch — anchor lookup is new).

Stdlib only (plus intra-package config import for project_root fallback).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class ReadsError(ValueError):
    """Raised when a reads: pointer fails hard resolution (file/anchor missing)."""


# ---------------------------------------------------------------------------
# Pointer item normalization
# ---------------------------------------------------------------------------

def _pointer_ref(item: Any) -> str:
    """Extract the ref string from a reads: item (bare str or {ref:...} dict).

    Returns empty string on malformed input (structural errors caught earlier
    by validate_manifest; here we just defensively normalize).
    """
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("ref", "")).strip()
    return ""


# ---------------------------------------------------------------------------
# Anchor helper — thin, no AST, markdown-heading search only
# ---------------------------------------------------------------------------

# Matches a markdown heading of any level that CONTAINS the anchor text.
# Examples matched for anchor "5B-SCOPE":
#   ## 5B-SCOPE. BOUND the reading-scope …
#   ## 5B-SCOPE  (exact heading)
#   # … 5B-SCOPE …
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)


def _anchor_found(text: str, anchor: str) -> bool:
    """Return True if ANY markdown heading in text contains anchor (case-sensitive).

    This is the thin anchor-search helper the spec calls for. We match headings
    that START WITH or CONTAIN the anchor text — covering both:
      ## 5B-SCOPE. Some long title …
      ## 5B-SCOPE
    No AST parsing; no coupling to Python or any language toolchain.
    """
    if not anchor:
        return False
    anchor_stripped = anchor.strip()
    for line in text.splitlines():
        stripped = line.strip()
        # Is it a heading?
        if not re.match(r"^#{1,6}\s+", stripped):
            continue
        # Strip the leading hashes and whitespace to get heading text
        heading_text = re.sub(r"^#{1,6}\s+", "", stripped)
        # Check if the heading text starts with or equals the anchor, or
        # contains the anchor followed by punctuation/space (e.g. "5B-SCOPE.")
        if (
            heading_text == anchor_stripped
            or heading_text.startswith(anchor_stripped + ".")
            or heading_text.startswith(anchor_stripped + " ")
            or anchor_stripped in heading_text
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Single-pointer resolution
# ---------------------------------------------------------------------------

def resolve_reads_pointer(
    ptr: str,
    *,
    project_root: Path,
) -> tuple[str | None, str | None]:
    """Resolve a single reads: pointer string.

    Returns (error_msg | None, warn_msg | None):
      - (None, None)   → pointer resolved successfully.
      - (error, None)  → hard fail (file/anchor missing).
      - (None, warn)   → file resolved; symbol is soft warn.
      - (error, warn)  → both (should not happen in current grammar).

    Grammar handled:
      bare path            → file must exist (relative to project_root if not absolute).
      file#anchor          → file must exist AND anchor found in markdown headings.
      path:symbol          → file must exist (hard); symbol existence is soft WARN.
                             Detected heuristically: ':' in the path that is NOT a
                             scheme prefix (http:// etc.) and the left part looks like
                             a file path (.py, .md, or contains /).
    """
    ptr = ptr.strip()
    if not ptr:
        return "empty pointer", None

    # ── Detect path:symbol form ───────────────────────────────────────────────
    # Heuristic: has ':' not preceded by common scheme prefixes,
    # and left side looks like a file path.
    symbol: str | None = None
    ptr_for_resolution = ptr

    if ":" in ptr:
        left, _, right = ptr.partition(":")
        is_scheme = left.lower() in ("http", "https", "artifact", "sacct", "pr", "cmd", "url", "note")
        looks_like_path = (
            not is_scheme
            and bool(left)
            and bool(right)
            and ("/" in left or "." in left)
        )
        if looks_like_path:
            ptr_for_resolution = left.strip()
            symbol = right.strip()

    # ── Split off anchor ──────────────────────────────────────────────────────
    anchor: str | None = None
    if "#" in ptr_for_resolution:
        file_part, _, anchor_part = ptr_for_resolution.partition("#")
        file_part = file_part.strip()
        anchor = anchor_part.strip() or None
    else:
        file_part = ptr_for_resolution

    # ── Resolve file ──────────────────────────────────────────────────────────
    p = Path(file_part)
    if not p.is_absolute():
        p = project_root / file_part

    if not p.exists():
        return (
            f"reads pointer {ptr!r}: file '{file_part}' not found "
            f"(resolved to: {p})",
            None,
        )

    # ── Check anchor if present ───────────────────────────────────────────────
    if anchor:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            return f"reads pointer {ptr!r}: cannot read '{file_part}': {exc}", None

        if not _anchor_found(text, anchor):
            return (
                f"reads pointer {ptr!r}: anchor '{anchor}' not found in {p}",
                None,
            )

    # ── Soft symbol WARN (if symbol form detected) ───────────────────────────
    warn: str | None = None
    if symbol is not None:
        # Soft check: look for the symbol name as a literal string in the file
        try:
            src = p.read_text(encoding="utf-8")
        except OSError:
            src = ""
        if symbol not in src:
            warn = (
                f"reads pointer {ptr!r}: symbol '{symbol}' not found in "
                f"'{file_part}' (soft check — no AST coupling)"
            )

    return None, warn


# ---------------------------------------------------------------------------
# Manifest-level resolution pass
# ---------------------------------------------------------------------------

def resolve_reads_pointers(
    manifest: dict[str, Any],
    *,
    project_root: Path,
) -> tuple[list[str], list[str]]:
    """Resolve all reads: pointers in a manifest at run/tick time.

    This is the RESOLUTION pass — called by cmd_run / cmd_tick AFTER the pure
    validate_manifest structural check. It walks all agent nodes' reads: lists,
    resolves each pointer, and accumulates hard errors and soft warns.

    Returns:
      (errors, warns) — lists of strings.
      errors: non-empty → manifest should fail hard at run/tick.
      warns:  non-empty → soft issues (symbols not found etc.), surfaced non-fatally.

    human-go nodes are skipped (they carry no reads: and are decision gates).
    Nodes with no reads: field are skipped (optional field).
    """
    from .schema import DEFAULT_NODE_TYPE  # no circular dep: schema is pure, reads is I/O

    errors: list[str] = []
    warns: list[str] = []

    for node in manifest.get("nodes", []):
        node_type = node.get("type", DEFAULT_NODE_TYPE)
        if node_type == "human-go":
            continue  # exempt

        reads = node.get("reads")
        if reads is None:
            continue  # optional — no reads: field, skip

        nid = node.get("id", "<unknown>")
        if not isinstance(reads, list):
            # Structural error — already caught by validate_manifest; skip here.
            continue

        for item in reads:
            ref = _pointer_ref(item)
            if not ref:
                continue  # malformed — structural error already flagged

            err, warn = resolve_reads_pointer(ref, project_root=project_root)
            if err is not None:
                errors.append(f"node {nid!r}: {err}")
            if warn is not None:
                warns.append(f"node {nid!r}: {warn}")

    return errors, warns


# ---------------------------------------------------------------------------
# SR-DAG-BRIEF: resolve_reads_paths — returns resolved ABSOLUTE path strings
# ---------------------------------------------------------------------------

def resolve_reads_paths(
    node: dict[str, Any],
    project_root: Path,
) -> list[str]:
    """Resolve a single node's reads: list to ABSOLUTE path strings.

    Returns one entry per reads: item.  Items that resolve successfully are
    the absolute path.  Items that fail resolution are included as
    ``"<ref> (unresolved)"`` so the caller (build_brief) can surface them
    rather than silently dropping them (charter §2: surface-never-silently-drop).

    human-go nodes carry no reads: field — returns [] for them.
    SSOT for per-node reads→abs-path resolution used by build_brief.
    """
    reads = node.get("reads")
    if not reads or not isinstance(reads, list):
        return []

    result: list[str] = []
    for item in reads:
        ref = _pointer_ref(item)
        if not ref:
            continue

        # Determine the file portion (strip symbol and anchor for path resolution)
        path_part = ref
        symbol: str | None = None
        if ":" in ref:
            left, _, right = ref.partition(":")
            is_scheme = left.lower() in (
                "http", "https", "artifact", "sacct", "pr", "cmd", "url", "note"
            )
            if not is_scheme and ("/" in left or "." in left):
                path_part = left.strip()
                symbol = right.strip()

        anchor: str | None = None
        if "#" in path_part:
            file_part, _, anchor_part = path_part.partition("#")
            path_part = file_part.strip()
            anchor = anchor_part.strip() or None

        p = Path(path_part)
        if not p.is_absolute():
            p = project_root / path_part

        if p.exists():
            result.append(str(p))
        else:
            result.append(f"{ref} (unresolved)")

    return result
