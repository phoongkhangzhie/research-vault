"""gap_scan.py — SR-LR-2 + SR-GAP-ROUTE: gap-driven pass + gap-loop router.

SR-LR-2: The gap-driven pass is Part-1 (SR-LR-1) invoked with a scope protocol
AUTO-AUTHORED from a detected gap record — no new DAG mechanism.

SR-GAP-ROUTE (SR-LR-3): the gap-loop ROUTER makes ``cmd_gap_scope`` route-aware
(read-vs-run by error-asymmetry).  A ``suggested_route:`` frontmatter field is
written at scan time; ``cmd_gap_scope --target`` (default = suggested_route) authors
either a literature scope (SR-LR-1) or an experiment plan (SR-PLAN-1).

Architecture:
  - gap-detect = a rejects-only SCREEN (no auto-fire; human authorizes each pass).
  - ``rv review gap-scan`` is the surface: a cheap OKF graph query over findings/,
    concepts/, mocs/, and an optional support_matcher meta dict.
  - The screen emits typed ``gaps/<id>.md`` notes (first-class OKF type,
    SR-LR-2 §5L.8 D-GAP-1) with a ``suggested_route:`` field (§5L.14–5L.15).
  - ``rv review gap-scope <gap-id> <scope> [--target {literature|experiment}]``
    auto-authors either a Part-1 review scope (literature, unchanged) or a
    SR-PLAN-1 pre-registration plan (experiment, new).
  - ``rv review gap-route`` is a thin alias for ``gap-scope`` (discoverability).
  - ``rv review gap-close <gap-id> --status <status>`` stamps closure.
  - ``rv review gap-list [--status <status>]`` lists gap records.
  - ``rv status`` surfaces the OPEN gap count AND the proven-open run-candidate
    count (D-GAP-4); records are never written inline into the control bus.

Four gap types (§5L.7 — attribution: type names AND identification procedure from
Müller-Bloch & Kranz (2015, ICIS) six-gap framework; Miles (2017) and
Robinson et al. (2011) as related secondary taxonomies):
  knowledge_void    — finding with support-degree < threshold (D-GAP-2)
  contradictory     — concept with both supported_by AND contradicted_by edges
  evaluation_void   — finding asserting an effect with no comparator edge
  absent_row        — support_matcher [ABSENT]/[CONTRADICTS] verdict
                      (the loop-closer that makes manuscript↔lit-review a cycle, §5L.10)

Support-degree (D-GAP-2): count of entries in a finding's ``backed_by:`` frontmatter
field (the citekeys of literature/ notes that support the finding, as authored
by the ``relate-<key>`` Phase-2 fan-out nodes). Default threshold = 1.

Closure statuses (§5L.8):
  open              — gap detected, not yet addressed
  closed-supported  — manuscript matcher flipped [ABSENT]→[SUPPORTS]/[PARTIAL]
  closed-filled     — support-degree crossed threshold / MOC region filled
  proven-open       — targeted pass saturated without closing → candidate contribution

Suggested routes (§5L.14–5L.15, SR-GAP-ROUTE):
  literature  — read-first (knowledge_void, contradictory, absent_row in intro/background)
  experiment  — run-first fast-path (evaluation_void, absent_row in results section)
  triage      — human decides (absent_row with unknown/absent section)

The router SUGGESTS; it never auto-fires a run.  cmd_gap_scope is the human-authorized
step; the experiment route additionally rides SR-PLAN-1's own human-go-plan gate.

Stdlib only.
sr: SR-LR-2, SR-GAP-ROUTE
"""
from __future__ import annotations

import datetime
import hashlib
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Suggested-route tokens (SR-GAP-ROUTE §5L.14)
ROUTE_LITERATURE = "literature"   # read-first (low-regret default)
ROUTE_EXPERIMENT = "experiment"   # run-first fast-path (evaluation_void + our-result absent_row)
ROUTE_TRIAGE = "triage"           # human decides (absent_row with unknown/absent section)

# Tier-B section-to-route maps (§5L.15 — case-insensitive match after lowering tex.stem)
# READ sections: claim is about FIELD / prior work → find the cite in lit
_READ_SECTIONS: frozenset[str] = frozenset({
    "introduction", "related-work", "related_work", "background",
    "gather-scope", "gather_scope", "literature", "prior-work", "prior_work",
})
# RUN sections: claim is about OUR OWN result → experiment or capture
_RUN_SECTIONS: frozenset[str] = frozenset({
    "results", "results-discussion", "results_discussion",
    "findings", "our-approach", "our_approach",
    "evaluation", "experiments", "experiment",
})

# Gap type tokens (§5L.7 — type names AND identification procedure from
# Müller-Bloch & Kranz (2015, ICIS) six-gap framework; Miles (2017) and
# Robinson et al. (2011) are related secondary taxonomies).
GAP_TYPE_KNOWLEDGE_VOID = "knowledge_void"
GAP_TYPE_CONTRADICTORY = "contradictory"
GAP_TYPE_EVALUATION_VOID = "evaluation_void"
GAP_TYPE_ABSENT_ROW = "absent_row"

GAP_TYPES: frozenset[str] = frozenset({
    GAP_TYPE_KNOWLEDGE_VOID,
    GAP_TYPE_CONTRADICTORY,
    GAP_TYPE_EVALUATION_VOID,
    GAP_TYPE_ABSENT_ROW,
})

# Valid closure statuses (§5L.8)
GAP_STATUSES: frozenset[str] = frozenset({
    "open",
    "closed-supported",
    "closed-filled",
    "proven-open",
})

# Default support-degree threshold (D-GAP-2)
DEFAULT_SUPPORT_THRESHOLD = 1

# Bracketed verdict tokens that trigger absent_row detection (§5L.10)
# These are the BLOCK-class verdicts from SupportVerdict.verdict.
_ABSENT_ROW_TOKENS = frozenset({"ABSENT", "CONTRADICTS"})

# Seed query templates per gap type (§5L.7 — targeted frontier)
_SEED_QUERY_TEMPLATES: dict[str, list[str]] = {
    GAP_TYPE_KNOWLEDGE_VOID: [
        '"{concept}" recent empirical evidence',
        '"{concept}" systematic review',
        '"{concept}" survey',
    ],
    GAP_TYPE_CONTRADICTORY: [
        '"{concept}" AND (limitation OR "fails to" OR contradicts OR replication)',
        '"{concept}" inconsistent results',
        '"{concept}" conflicting evidence',
    ],
    GAP_TYPE_EVALUATION_VOID: [
        '"{effect}" baseline comparison',
        '"{effect}" evaluation comparator',
        '"{effect}" benchmark ablation',
    ],
    GAP_TYPE_ABSENT_ROW: [
        '"{claim_terms}" evidence',
        '"{claim_terms}" empirical',
        '"{claim_terms}" study',
    ],
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class GapRecord:
    """Typed gap record (§5L.7 D-GAP-1).

    Fields match the gaps/<id>.md frontmatter schema:
      type    — one of GAP_TYPES
      anchor  — OKF path (relative to project_notes_dir) of the anchoring artifact
      claim   — verbatim claim text from the anchor (becomes the review question)
      why     — brief reason the detector flagged this (honest surface, charter §2)
      status  — one of GAP_STATUSES
    """

    type: str       # noqa: A003  (shadowing builtin is intentional here — matches frontmatter key)
    anchor: str
    claim: str
    why: str
    status: str = "open"
    # SR-GAP-ROUTE: suggested_route is written to the gap note frontmatter at scan time.
    # Set by suggest_route() in cmd_gap_scan; "" means not yet computed.
    suggested_route: str = ""
    # Optional: extra context from the detector (not written to frontmatter by default)
    _meta: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.type not in GAP_TYPES:
            raise ValueError(f"GapRecord.type must be one of {sorted(GAP_TYPES)!r}; got {self.type!r}")
        if self.status not in GAP_STATUSES:
            raise ValueError(
                f"GapRecord.status must be one of {sorted(GAP_STATUSES)!r}; got {self.status!r}"
            )


# ---------------------------------------------------------------------------
# Router: suggest_route pure function (SR-GAP-ROUTE §5L.14–5L.15)
# ---------------------------------------------------------------------------

def suggest_route(gap_type: str, meta: dict[str, Any]) -> str:
    """Compute the suggested route for a gap — a PRIOR, not a decision.

    Pure function (no I/O, no side effects).  The output is written as
    ``suggested_route:`` in the gap note at scan time and used as the default
    target in ``cmd_gap_scope``.  The router SUGGESTS; the run never auto-fires.

    Per-type routing map (§5L.14):
      knowledge_void   → literature  (detection ≠ truth: corpus void ≠ field void)
      contradictory    → literature  (reconcile via abstraction / moderators first)
      evaluation_void  → experiment  (RUN fast-path: lit pass can only return proven-open)
      absent_row       → Tier-A: triage (no section) / Tier-B: section-split

    Tier-B section split for absent_row (§5L.15 D-ROUTE-2):
      section in _READ_SECTIONS  → literature  (field/prior-work claim → find the cite)
      section in _RUN_SECTIONS   → experiment  (our-own-result claim → run or capture)
      else                       → triage       (ambiguous → human decides)

    Back-compat: if ``meta`` has no ``section`` key (old detections) → triage (Tier-A).

    Args:
        gap_type: one of GAP_TYPES
        meta:     the GapRecord._meta dict (may contain 'section' for absent_row Tier-B)

    Returns:
        ROUTE_LITERATURE | ROUTE_EXPERIMENT | ROUTE_TRIAGE
    """
    if gap_type == GAP_TYPE_KNOWLEDGE_VOID:
        return ROUTE_LITERATURE
    if gap_type == GAP_TYPE_CONTRADICTORY:
        return ROUTE_LITERATURE
    if gap_type == GAP_TYPE_EVALUATION_VOID:
        return ROUTE_EXPERIMENT
    if gap_type == GAP_TYPE_ABSENT_ROW:
        # Tier-B: split by section context if available
        section = meta.get("section", "").lower().strip()
        if section in _READ_SECTIONS:
            return ROUTE_LITERATURE
        if section in _RUN_SECTIONS:
            return ROUTE_EXPERIMENT
        # Tier-A fallback: section absent or ambiguous → triage
        return ROUTE_TRIAGE
    # Unknown type → safe default
    return ROUTE_TRIAGE


# ---------------------------------------------------------------------------
# Gap detectors (cheap OKF graph queries — §5L.7)
# ---------------------------------------------------------------------------

def _parse_frontmatter_gap(text: str) -> dict[str, Any]:
    """Parse YAML-like frontmatter between --- delimiters for gap_scan.

    Handles both scalar and YAML list values (``  - item`` lines).  This is a
    LOCAL list-aware parser because ``note._parse_frontmatter`` is deliberately
    scalar-only: extending it to return lists would break numerous callers that
    do ``.strip()`` on expected-scalar fields (e.g. ``check_gates.py:synthesized_okf``,
    ``review/__init__.py``, ``manuscript/__init__.py``).  The convergence fix (§6)
    would require updating all those callers — deferred; this local parser is the
    justified fork, documented in ``note._parse_frontmatter``'s docstring.

    gap_scan specifically needs list support for: ``backed_by:`` (finding notes),
    ``supported_by:`` / ``contradicted_by:`` (concept notes).

    Returns: fields dict only (body is discarded — gap_scan callers don't need it).
    """
    lines = text.splitlines()
    in_block = False
    fm: dict[str, Any] = {}
    i = 0
    current_list_key: str | None = None
    while i < len(lines):
        ln = lines[i]
        if not in_block:
            if ln.strip() == "---":
                in_block = True
        elif ln.strip() == "---":
            break
        else:
            # List continuation: "  - item" lines
            if ln.startswith("  - "):
                if current_list_key is not None:
                    fm[current_list_key].append(ln[4:].strip())
                i += 1
                continue
            current_list_key = None
            if ":" in ln:
                key, _, val = ln.partition(":")
                key = key.strip()
                val = val.strip()
                if val == "":
                    # Empty value: start collecting YAML list items
                    current_list_key = key
                    fm[key] = []
                else:
                    # Strip inline comments (# ...) and quotes
                    val = val.split(" #")[0].strip() if " #" in val else val
                    if val.startswith('"') and val.endswith('"'):
                        val = val[1:-1]
                    elif val.startswith("'") and val.endswith("'"):
                        val = val[1:-1]
                    fm[key] = val
        i += 1
    return fm


def _extract_claim(fm: dict[str, Any]) -> str:
    """Extract a claim string from frontmatter.  Falls back to empty string."""
    for key in ("claim", "statement", "summary", "title"):
        v = fm.get(key, "")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _scan_notes_dir(notes_dir: Path, note_type: str) -> list[tuple[Path, dict[str, Any]]]:
    """Return (path, frontmatter) pairs for all notes of given type."""
    type_dir = notes_dir / note_type
    if not type_dir.is_dir():
        return []
    results = []
    for p in sorted(type_dir.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
            fm = _parse_frontmatter_gap(text)
            results.append((p, fm))
        except OSError:
            continue
    return results


def _detect_knowledge_void(
    notes_dir: Path,
    threshold: int = DEFAULT_SUPPORT_THRESHOLD,
) -> list[GapRecord]:
    """Detect Knowledge Void gaps: findings with support-degree < threshold.

    Support-degree = count of entries in the finding's ``backed_by:`` frontmatter
    field (the citekeys of literature/ notes that support this finding).
    Default threshold = 1 (a finding with zero backing literature is a void).

    Reference: Müller-Bloch & Kranz (2015, ICIS) — Knowledge Void gap type and
    identification procedure; Miles (2017) and Robinson et al. (2011) as
    related secondary taxonomies.
    """
    gaps: list[GapRecord] = []
    for p, fm in _scan_notes_dir(notes_dir, "findings"):
        backed_by = fm.get("backed_by", [])
        if isinstance(backed_by, str):
            backed_by = [backed_by] if backed_by.strip() else []
        support_degree = len(backed_by)
        if support_degree < threshold:
            claim = _extract_claim(fm)
            if not claim:
                # Try body first line
                try:
                    text = p.read_text(encoding="utf-8")
                    after_fm = text.split("---", 2)[-1].strip() if "---" in text else text
                    first_content = [ln.lstrip("#").strip() for ln in after_fm.splitlines() if ln.strip()]
                    claim = first_content[0] if first_content else p.stem
                except OSError:
                    claim = p.stem
            gaps.append(GapRecord(
                type=GAP_TYPE_KNOWLEDGE_VOID,
                anchor=f"findings/{p.stem}",
                claim=claim,
                why=(
                    f"support-degree={support_degree} < threshold={threshold}; "
                    f"backed_by is empty or below the required count"
                ),
                status="open",
            ))
    return gaps


def _detect_contradictory(notes_dir: Path) -> list[GapRecord]:
    """Detect Contradictory Evidence gaps: concepts with both supported_by AND contradicted_by.

    A concept node that has edges from both confirming and disconfirming literature/
    notes signals a contested evidential state — the gap-detector proposes a targeted
    review to resolve the contradiction.

    Reference: Müller-Bloch & Kranz (2015, ICIS) — Contradictory Evidence gap
    type and identification procedure; Miles (2017) and Robinson et al. (2011)
    as related secondary taxonomies.
    """
    gaps: list[GapRecord] = []
    for p, fm in _scan_notes_dir(notes_dir, "concepts"):
        supported_by = fm.get("supported_by", [])
        contradicted_by = fm.get("contradicted_by", [])
        if isinstance(supported_by, str):
            supported_by = [s for s in [supported_by.strip()] if s]
        if isinstance(contradicted_by, str):
            contradicted_by = [s for s in [contradicted_by.strip()] if s]
        if supported_by and contradicted_by:
            label = fm.get("label", fm.get("title", p.stem))
            gaps.append(GapRecord(
                type=GAP_TYPE_CONTRADICTORY,
                anchor=f"concepts/{p.stem}",
                claim=str(label),
                why=(
                    f"concept has {len(supported_by)} supporting and "
                    f"{len(contradicted_by)} contradicting literature edges — "
                    f"contested evidential state"
                ),
                status="open",
                _meta={"supported_by": list(supported_by), "contradicted_by": list(contradicted_by)},
            ))
    return gaps


def _detect_evaluation_void(notes_dir: Path) -> list[GapRecord]:
    """Detect Evaluation Void gaps: findings asserting an effect with no comparator.

    A finding that claims an effect (via the ``effect:`` frontmatter field) but
    records no ``comparator:`` baseline is an evaluation void — no comparison was
    made and the claimed improvement cannot be assessed.

    Reference: Müller-Bloch & Kranz (2015, ICIS) — Evaluation Void gap type and
    identification procedure; Miles (2017) and Robinson et al. (2011) as
    related secondary taxonomies.
    """
    gaps: list[GapRecord] = []
    for p, fm in _scan_notes_dir(notes_dir, "findings"):
        effect = fm.get("effect", "")
        if not (isinstance(effect, str) and effect.strip()):
            continue  # No effect field → not an evaluation void
        comparator = fm.get("comparator", "")
        if isinstance(comparator, str) and comparator.strip():
            continue  # Has comparator → not a void
        claim = _extract_claim(fm) or p.stem
        gaps.append(GapRecord(
            type=GAP_TYPE_EVALUATION_VOID,
            anchor=f"findings/{p.stem}",
            claim=claim,
            why=(
                f"finding asserts effect='{effect.strip()}' but has no comparator field; "
                f"cannot assess the claimed improvement without a baseline"
            ),
            status="open",
            _meta={"effect": effect.strip()},
        ))
    return gaps


def _detect_absent_rows(
    matcher_meta: dict[str, Any],
    *,
    run_id: str = "",
) -> list[GapRecord]:
    """Detect Absent Row gaps from the support_matcher structured verdicts (the loop-closer, §5L.10).

    Consumes ``RunState.meta['support_matcher']`` — the structured output of
    ``SupportMatchSummary.meta_dict()`` — instead of grepping prose (D-GAP-3 fix).

    Filters verdicts where:
      - ``verdict`` in {ABSENT, CONTRADICTS}  (BLOCK-class), OR
      - ``j2_escalation`` is True (J-2 stance-mismatch BLOCK)

    Builds each GapRecord from:
      - ``claim``   ← verdict['claim_snippet']  (the guaranteed field)
      - ``anchor``  ← ``literature/<citekey>``  (the cited note reference)
      - ``citekey`` ← verdict['citekey']

    [PARTIAL] without j2_escalation is WARN-only — NOT surfaced as a gap here.

    Charter §2 guard: if the meta dict is non-empty but the ``verdicts`` key is
    absent or None (indicating a missing or incomplete matcher run), emits a
    ``warnings.warn`` at UserWarning level so the operator knows the gate may
    have fired without data — never silently returns [] in that case.
    """
    verdicts_raw = matcher_meta.get("verdicts")

    # §2 guard: non-empty meta but no verdicts list → likely an incomplete run
    if verdicts_raw is None:
        # The meta came from somewhere (caller passed it) but verdicts are absent.
        k_block = matcher_meta.get("k_block", 0)
        run_label = f" (run: {run_id!r})" if run_id else ""
        warnings.warn(
            f"support_matcher meta has no 'verdicts' key{run_label}; "
            f"k_block={k_block} in meta — did the manuscript critic node complete? "
            f"absent_row detection is skipped; review the run-state manually.",
            UserWarning,
            stacklevel=2,
        )
        return []

    gaps: list[GapRecord] = []
    for v in verdicts_raw:
        verdict_str = str(v.get("verdict", "")).upper()
        j2 = bool(v.get("j2_escalation", False))
        blocks = verdict_str in _ABSENT_ROW_TOKENS or j2
        if not blocks:
            continue
        claim_snippet = v.get("claim_snippet", "").strip()
        citekey = v.get("citekey", "unknown")
        anchor = f"literature/{citekey}"
        run_label = f" run={run_id!r}" if run_id else ""
        # SR-GAP-ROUTE Tier B: read section from verdict meta (SupportVerdict.to_meta_dict
        # emits 'section' when check_support_tally threads tex.stem through match_support).
        # Back-compat: old verdicts without 'section' key → "" → triage fallback.
        section = v.get("section", "")
        gaps.append(GapRecord(
            type=GAP_TYPE_ABSENT_ROW,
            anchor=anchor,
            claim=claim_snippet or f"[no claim_snippet; citekey={citekey}]",
            why=(
                f"support_matcher verdict [{verdict_str}] on citekey={citekey!r}"
                + (f" (J-2 escalation)" if j2 and verdict_str not in _ABSENT_ROW_TOKENS else "")
                + f"{run_label} — drafted claim has no backing literature/ note "
                f"(the loop-closer gap, §5L.10)"
            ),
            status="open",
            _meta={
                "verdict": verdict_str,
                "citekey": citekey,
                "j2_escalation": j2,
                "run_id": run_id,
                "section": section,  # Tier-B: manuscript section stem for absent_row routing
            },
        ))
    return gaps


# ---------------------------------------------------------------------------
# Gap ID generation
# ---------------------------------------------------------------------------

def _gap_id(gap_type: str, anchor: str, claim: str) -> str:
    """Generate a stable, slug-form gap id from type + anchor + claim.

    Uses a 6-char SHA-256 prefix to ensure uniqueness while staying readable.
    Format: ``gap-<type_prefix>-<sha6>``
    """
    raw = f"{gap_type}:{anchor}:{claim}"
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]
    type_prefix = gap_type.replace("_", "-")[:8]
    return f"gap-{type_prefix}-{sha}"


# ---------------------------------------------------------------------------
# Seed query builder (per-type templates, §5L.7)
# ---------------------------------------------------------------------------

def _build_seed_queries(gap_type: str, claim: str) -> list[str]:
    """Return seed query strings for a targeted lit-review pass, per gap type.

    Templates are filled with key terms extracted from the claim string.
    Targeted frontier → fast saturation (§5L.7): the queries are scoped to
    the gap's neighborhood, not a broad survey.
    """
    # Extract the most meaningful term from the claim (first ≤4 non-stop words)
    _STOP = frozenset({
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "to", "of", "on", "in", "at", "and", "or", "but", "for",
        "with", "that", "this", "we", "it", "i", "our", "their",
        "has", "have", "had", "by", "from", "as", "up", "no", "not",
    })
    words = [w.strip(".,;:\"'()[]") for w in claim.split()]
    key_terms = [w for w in words if w.lower() not in _STOP and len(w) > 2][:4]
    concept = " ".join(key_terms) if key_terms else claim[:40]
    effect = concept  # for evaluation_void template
    claim_terms = concept

    templates = _SEED_QUERY_TEMPLATES.get(gap_type, [f'"{claim_terms}" evidence'])
    queries = []
    for tmpl in templates:
        q = tmpl.replace("{concept}", concept)
        q = q.replace("{effect}", effect)
        q = q.replace("{claim_terms}", claim_terms)
        queries.append(q)
    return queries


# ---------------------------------------------------------------------------
# Gap record I/O (gaps/<id>.md notes)
# ---------------------------------------------------------------------------

def _gap_note_path(project_notes_dir: Path, gap_id: str) -> Path:
    """Return the path for a gap note: project_notes_dir/gaps/<id>.md."""
    return project_notes_dir / "gaps" / f"{gap_id}.md"


def _render_gap_note(rec: GapRecord, gap_id: str) -> str:
    """Render a gap record to OKF note markdown (frontmatter + body)."""
    today = datetime.date.today().isoformat()
    # SR-GAP-ROUTE: compute suggested_route if not already set on the record
    route = rec.suggested_route or suggest_route(rec.type, rec._meta)
    lines = [
        "---",
        f"type: gaps",
        f"id: {gap_id}",
        f"gap_type: {rec.type}",
        f"anchor: {rec.anchor}",
        f"claim: \"{rec.claim.replace(chr(34), chr(39))}\"",
        f"why: \"{rec.why.replace(chr(34), chr(39))}\"",
        f"status: {rec.status}",
        f"suggested_route: {route}",
        f"detected: {today}",
        "---",
        "",
        f"# Gap: {gap_id}",
        "",
        f"**Type:** {rec.type}",
        f"**Anchor:** {rec.anchor}",
        f"**Status:** {rec.status}",
        "",
        "## Claim (verbatim)",
        "",
        f"> {rec.claim}",
        "",
        "## Why it is a gap",
        "",
        rec.why,
        "",
        "## Seed queries (auto-authored from gap type)",
        "",
    ]
    for q in _build_seed_queries(rec.type, rec.claim):
        lines.append(f"- {q}")
    lines.append("")
    lines.append(
        "## Attribution\n\n"
        "Gap types AND identification procedure: Müller-Bloch & Kranz (2015, ICIS) "
        "six-gap framework. Related secondary taxonomies: Miles (2017); "
        "Robinson et al. (2011)."
    )
    return "\n".join(lines)


def _write_gap_note(rec: GapRecord, gap_id: str, project_notes_dir: Path) -> Path:
    """Write gaps/<id>.md for a gap record. Returns the path."""
    gap_path = _gap_note_path(project_notes_dir, gap_id)
    gap_path.parent.mkdir(parents=True, exist_ok=True)
    gap_path.write_text(_render_gap_note(rec, gap_id), encoding="utf-8")
    return gap_path


def _existing_gap_ids(project_notes_dir: Path) -> dict[str, str]:
    """Return {gap_id: status} for all existing gap notes."""
    gaps_dir = project_notes_dir / "gaps"
    if not gaps_dir.is_dir():
        return {}
    result: dict[str, str] = {}
    for p in gaps_dir.glob("*.md"):
        try:
            fm = _parse_frontmatter_gap(p.read_text(encoding="utf-8"))
            result[p.stem] = fm.get("status", "open")
        except OSError:
            result[p.stem] = "open"
    return result


# ---------------------------------------------------------------------------
# Public commands
# ---------------------------------------------------------------------------

def cmd_gap_scan(
    project: str,
    *,
    config: Any = None,
    threshold: int = DEFAULT_SUPPORT_THRESHOLD,
    matcher_meta: dict[str, Any] | None = None,
    run_id: str = "",
) -> list[GapRecord]:
    """Scan the project's OKF corpus for typed research gaps.

    Runs four typed detectors (§5L.7):
      1. Knowledge Void: findings with support-degree < threshold
      2. Contradictory Evidence: concepts with both supported_by + contradicted_by
      3. Evaluation Void: findings with effect but no comparator
      4. Absent Row: support_matcher structured verdicts [ABSENT]/[CONTRADICTS]
                     (the loop-closer gap, §5L.10; D-GAP-3 structured binding)

    The ``matcher_meta`` parameter accepts ``RunState.meta['support_matcher']`` —
    the structured output of ``SupportMatchSummary.meta_dict()``.  It is NOT a
    prose file path (the old --critic-report pattern was removed because it silently
    returned [] on real matcher output — charter §2 violation).

    Writes gaps/<id>.md for each *new* gap found (idempotent: existing gaps
    with the same anchor+claim are NOT re-created; closed gaps are preserved).

    ``rv review gap-scan`` surfaces a COUNT in ``rv status`` (D-GAP-4) — the
    records are never inlined; manual invocation only.

    Returns the list of GapRecord for all newly-written gaps (not including
    pre-existing ones).
    """
    from research_vault.config import load_config as _load_config

    cfg = config or _load_config()
    pnd = cfg.project_notes_dir(project)

    all_gaps: list[GapRecord] = []
    all_gaps.extend(_detect_knowledge_void(pnd, threshold=threshold))
    all_gaps.extend(_detect_contradictory(pnd))
    all_gaps.extend(_detect_evaluation_void(pnd))
    if matcher_meta is not None:
        all_gaps.extend(_detect_absent_rows(matcher_meta, run_id=run_id))

    existing = _existing_gap_ids(pnd)

    new_gaps: list[GapRecord] = []
    for rec in all_gaps:
        gid = _gap_id(rec.type, rec.anchor, rec.claim)
        if gid in existing:
            # Gap already recorded — do NOT overwrite (preserves closed status)
            continue
        # SR-GAP-ROUTE: stamp suggested_route on the record before writing
        rec.suggested_route = suggest_route(rec.type, rec._meta)
        _write_gap_note(rec, gid, pnd)
        new_gaps.append(rec)

    return new_gaps


def cmd_gap_scope(
    project: str,
    gap_id: str,
    scope: str,
    *,
    config: Any = None,
    target: str | None = None,
) -> "dict[str, Any]":
    """Auto-author a targeted scope from a gap record (SR-LR-2 §5L.7 + SR-GAP-ROUTE §5L.16).

    SR-GAP-ROUTE: the ``target`` parameter (``literature`` | ``experiment``) controls
    which arm is taken.  Default = the gap note's ``suggested_route:`` field (computed
    at scan time by ``suggest_route()``), with a ``literature`` fallback for back-compat.

    ``--target literature`` (unchanged from SR-LR-2):
      - question ← gap.claim (exact words, the anti-fabrication spine)
      - seed_queries ← per-type templates derived from the claim
      - snowball_seeds ← anchor's citekeys (backed_by/supported_by)
      - inclusion ← 'resolves this gap'
      - Creates the Phase-1 DAG via cmd_new and writes ``_gap-context.md`` into
        reviews/<scope>/ with the auto-authored protocol content.
      - Returns the Phase-1 manifest dict.

    ``--target experiment`` (new, §5L.16):
      - Mirrors the literature path move-for-move via note.cmd_new.
      - Creates experiments/<gap_id>-plan.md with plan_kind: preregistration,
        research question ← gap.claim verbatim (anti-fabrication spine),
        covers: skeleton, and a diagnosis-table stub (D-ROUTE-3).
      - Writes _gap-context.md adjacent to the plan note with SR-PLAN-1 next-step chain.
      - Prints the next-step chain: rv plan check → human-go-plan → rv plan freeze.
      - Returns a dict with 'plan_note_path' key.
      - ZERO new DAG mechanism — the operator then drives the existing SR-PLAN-1 loop.
    """
    from research_vault.config import load_config as _load_config

    cfg = config or _load_config()
    pnd = cfg.project_notes_dir(project)

    # Load the gap record
    gap_path = _gap_note_path(pnd, gap_id)
    if not gap_path.exists():
        raise FileNotFoundError(f"Gap note not found: {gap_path}")
    fm = _parse_frontmatter_gap(gap_path.read_text(encoding="utf-8"))
    gap_type = fm.get("gap_type", "knowledge_void")
    claim = fm.get("claim", "").strip().strip('"\'')
    anchor = fm.get("anchor", "")

    if not claim:
        raise ValueError(f"Gap note {gap_id!r} has no claim field")

    # Resolve target: explicit arg > gap note's suggested_route > "literature" fallback
    if target is None:
        target = fm.get("suggested_route", "").strip()
    if not target or target not in (ROUTE_LITERATURE, ROUTE_EXPERIMENT, ROUTE_TRIAGE):
        target = ROUTE_LITERATURE  # back-compat default

    if target == ROUTE_EXPERIMENT:
        return _cmd_gap_scope_experiment(
            project=project,
            gap_id=gap_id,
            claim=claim,
            gap_type=gap_type,
            anchor=anchor,
            pnd=pnd,
            config=cfg,
        )

    # target == ROUTE_LITERATURE (or ROUTE_TRIAGE treated as literature)
    return _cmd_gap_scope_literature(
        project=project,
        gap_id=gap_id,
        scope=scope,
        claim=claim,
        gap_type=gap_type,
        anchor=anchor,
        pnd=pnd,
        config=cfg,
    )


def _cmd_gap_scope_literature(
    project: str,
    gap_id: str,
    scope: str,
    claim: str,
    gap_type: str,
    anchor: str,
    pnd: Path,
    config: Any,
) -> "dict[str, Any]":
    """Literature arm of cmd_gap_scope (SR-LR-2 behavior, unchanged)."""
    from research_vault.review import cmd_new

    # Derive snowball seeds from the anchor note (backed_by / supported_by)
    snowball_seeds: list[str] = []
    anchor_path = pnd / anchor if not Path(anchor).is_absolute() else Path(anchor)
    if anchor_path.exists():
        try:
            a_fm = _parse_frontmatter_gap(anchor_path.read_text(encoding="utf-8"))
            for fkey in ("backed_by", "supported_by", "contradicted_by"):
                vals = a_fm.get(fkey, [])
                if isinstance(vals, list):
                    snowball_seeds.extend(vals)
                elif isinstance(vals, str) and vals.strip():
                    snowball_seeds.append(vals.strip())
        except OSError:
            pass
    # Deduplicate preserving order
    seen: set[str] = set()
    snowball_seeds = [s for s in snowball_seeds if not (s in seen or seen.add(s))]  # type: ignore[func-returns-value]

    # Build seed queries for this gap type
    seed_queries = _build_seed_queries(gap_type, claim)

    # Create the Phase-1 review DAG via cmd_new
    note_path, review_dir, manifest = cmd_new(
        project,
        scope,
        question=claim,
        config=config,
    )

    # Write _gap-context.md with the auto-authored protocol seed
    context_lines = [
        "# Gap-context (auto-authored — SR-LR-2 §5L.7)",
        "",
        f"**Gap ID:** {gap_id}",
        f"**Gap type:** {gap_type}",
        f"**Anchor:** {anchor}",
        f"**Suggested route:** {ROUTE_LITERATURE}",
        "",
        "## Research question (verbatim from gap claim)",
        "",
        f"> {claim}",
        "",
        "## Seed queries (per-type templates)",
        "",
    ]
    for q in seed_queries:
        context_lines.append(f"- {q}")
    context_lines.append("")
    context_lines.append("## Snowball seeds (anchor citekeys)")
    context_lines.append("")
    if snowball_seeds:
        for s in snowball_seeds:
            context_lines.append(f"- {s}")
    else:
        context_lines.append("(none — anchor has no backed_by / supported_by entries)")
    context_lines.append("")
    context_lines.append("## Inclusion criteria (gap-scoped)")
    context_lines.append("")
    context_lines.append(
        "Include only sources that **directly address or resolve** this gap. "
        "The targeted scope → bounded frontier → fast saturation (§5L.7). "
        "The review-scope agent must populate `_protocol.md` using this content."
    )
    context_lines.append("")
    context_lines.append(
        "## Attribution\n\n"
        "Gap types AND identification procedure: Müller-Bloch & Kranz (2015, ICIS) "
        "six-gap framework. Related secondary taxonomies: Miles (2017); "
        "Robinson et al. (2011)."
    )
    context_path = review_dir / "_gap-context.md"
    context_path.write_text("\n".join(context_lines), encoding="utf-8")

    return manifest


def _cmd_gap_scope_experiment(
    project: str,
    gap_id: str,
    claim: str,
    gap_type: str,
    anchor: str,
    pnd: Path,
    config: Any,
) -> "dict[str, Any]":
    """Experiment arm of cmd_gap_scope (SR-GAP-ROUTE §5L.16 — new, mirrors lit path).

    Creates experiments/<gap_id>-plan.md with:
      - plan_kind: preregistration
      - research question ← gap.claim verbatim (anti-fabrication spine)
      - covers: skeleton (empty, no path-prefix violations for K-2)
      - diagnosis-table stub that passes K-2 shape-lint (D-ROUTE-3)
    Writes _gap-context.md adjacent to the plan note with SR-PLAN-1 next-step chain.

    ZERO new mechanism: mirrors note.cmd_new via direct file write (same pattern as
    _render_gap_note); the operator then drives the SR-PLAN-1 loop externally.
    """
    today = datetime.date.today().isoformat()
    plan_id = f"{gap_id}-plan"
    exp_dir = pnd / "experiments"
    exp_dir.mkdir(parents=True, exist_ok=True)
    plan_path = exp_dir / f"{plan_id}.md"

    # Build the pre-registration plan note content.
    # K-2 requirements (plan/check.py):
    #   (a) plan_kind: preregistration in frontmatter
    #   (b) Diagnosis table: no empty cells, no 'fallback', no 'TBD'
    #   (c) No multi-component 'Component manipulated:' lines
    #   (d) covers: entries must be bare IDs (empty [] passes)
    claim_safe = claim.replace('"', "'")
    plan_content_lines = [
        "---",
        "type: experiments",
        "plan_kind: preregistration",
        f"id: {plan_id}",
        f'research_question: "{claim_safe}"',
        "covers: []",
        f"gap_source: {gap_id}",
        f"gap_type: {gap_type}",
        f"created: {today}",
        "---",
        "",
        f"# Pre-registration Plan: {plan_id}",
        "",
        "<!-- SR-GAP-ROUTE: auto-authored from gap record. Fill in before rv plan freeze. -->",
        "<!-- Driven by the SR-PLAN-1 loop: rv plan check → human-go-plan → rv plan freeze -->",
        "",
        "## Research Question (verbatim from gap claim — anti-fabrication spine)",
        "",
        f"> {claim}",
        "",
        "## Gap Source",
        "",
        f"- **Gap ID:** {gap_id}",
        f"- **Gap type:** {gap_type}",
        f"- **Anchor:** {anchor}",
        f"- **Suggested route:** {ROUTE_EXPERIMENT}",
        "",
        "## Hypothesis",
        "",
        "<!-- State the directional prediction: if X then Y (fill in before freeze). -->",
        "The effect described in the research question can be measured at tested scale.",
        "",
        "## Covers (supporting ablation IDs — fill in bare IDs, e.g. q1-ablation1)",
        "",
        "<!-- Add experiment child IDs to covers: in the frontmatter (bare IDs only). -->",
        "",
        "## Diagnosis Table",
        "",
        "<!-- Outcome rows must be complete (no empty cells, no 'fallback', no 'TBD'). -->",
        "<!-- This is the D-ROUTE-3 stub: replace with your actual experimental conditions. -->",
        "",
        "| Outcome | Conclusion | Action |",
        "|---------|------------|--------|",
        "| Effect confirmed at tested scale | Gap claim supported by our result | Document in manuscript contribution and freeze |",
        "| Effect absent or reversed | Gap claim unconfirmed | Revise research question and re-scope |",
        "",
        "## Attribution",
        "",
        "Gap types AND identification procedure: Müller-Bloch & Kranz (2015, ICIS) "
        "six-gap framework. Related secondary taxonomies: Miles (2017); "
        "Robinson et al. (2011).",
    ]
    plan_path.write_text("\n".join(plan_content_lines), encoding="utf-8")

    # Write _gap-context.md adjacent to the plan note with SR-PLAN-1 next-step chain
    context_lines = [
        "# Gap-context (auto-authored — SR-GAP-ROUTE §5L.16)",
        "",
        f"**Gap ID:** {gap_id}",
        f"**Gap type:** {gap_type}",
        f"**Anchor:** {anchor}",
        f"**Suggested route:** {ROUTE_EXPERIMENT}",
        "",
        "## Research Question (verbatim from gap claim)",
        "",
        f"> {claim}",
        "",
        "## Error-asymmetry rationale",
        "",
        "This gap is routed to an **experiment** because reading can only return",
        f"``proven-open`` for a ``{gap_type}`` — a literature pass is pure waste here.",
        "The read-first default was skipped: the error-asymmetry (Chalmers & Glasziou",
        "avoidable-waste) favors running. (If this result already exists but was never",
        "captured, capture it rather than re-run.)",
        "",
        "## SR-PLAN-1 next steps",
        "",
        f"1. Fill in the plan note: `{plan_path}`",
        "2. Add supporting ablation IDs to `covers:` in the frontmatter (bare IDs only).",
        "3. Complete the diagnosis table (all cells must be non-empty, no 'fallback', no 'TBD').",
        f"4. Run: `rv plan check {plan_path}`",
        "5. Approve: `rv dag approve <run-id> human-go-plan`  ← this is the human-go gate",
        f"6. Freeze: `rv plan freeze <run-id> {plan_path}`",
        "",
        "The run is authorized only after the human-go-plan gate. No auto-fire.",
        "",
        "## Attribution",
        "",
        "Gap types AND identification procedure: Müller-Bloch & Kranz (2015, ICIS) "
        "six-gap framework. Related secondary taxonomies: Miles (2017); "
        "Robinson et al. (2011).",
    ]
    context_path = exp_dir / "_gap-context.md"
    context_path.write_text("\n".join(context_lines), encoding="utf-8")

    return {"plan_note_path": str(plan_path), "gap_context_path": str(context_path)}


def cmd_gap_close(
    project: str,
    gap_id: str,
    status: str,
    *,
    config: Any = None,
) -> Path:
    """Stamp a gap's closure status (§5L.8).

    ``status`` must be one of: closed-supported, closed-filled, proven-open.
    A ``proven-open`` gap is a first-class research object — it means the
    targeted pass saturated WITHOUT closing → candidate contribution for
    the manuscript's contribution framing.

    Returns the updated gap note path.
    """
    from research_vault.config import load_config as _load_config

    cfg = config or _load_config()
    pnd = cfg.project_notes_dir(project)

    if status not in GAP_STATUSES:
        raise ValueError(
            f"Gap status must be one of {sorted(GAP_STATUSES)!r}; got {status!r}"
        )

    gap_path = _gap_note_path(pnd, gap_id)
    if not gap_path.exists():
        raise FileNotFoundError(f"Gap note not found: {gap_path}")

    text = gap_path.read_text(encoding="utf-8")
    fm = _parse_frontmatter_gap(text)
    old_status = fm.get("status", "open")

    # Rewrite the status: line in the frontmatter block
    # Using a targeted regex to avoid re-serializing the whole FM
    new_text = re.sub(
        r"^(status:\s*)(.*)$",
        lambda m: f"{m.group(1)}{status}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if new_text == text and old_status != status:
        # status: line not found — shouldn't happen with our writer, but be safe
        new_text = text.replace(
            f"\nstatus: {old_status}\n",
            f"\nstatus: {status}\n",
            1,
        )
    gap_path.write_text(new_text, encoding="utf-8")
    return gap_path


def cmd_gap_list(
    project: str,
    *,
    config: Any = None,
    status_filter: str | None = None,
) -> list[dict[str, str]]:
    """List gap records for the project, optionally filtered by status.

    Returns list of {id, type, claim, status, anchor} dicts.
    """
    from research_vault.config import load_config as _load_config

    cfg = config or _load_config()
    pnd = cfg.project_notes_dir(project)
    gaps_dir = pnd / "gaps"
    if not gaps_dir.is_dir():
        return []

    results: list[dict[str, str]] = []
    for p in sorted(gaps_dir.glob("*.md")):
        try:
            fm = _parse_frontmatter_gap(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        s = fm.get("status", "open")
        if status_filter is not None and s != status_filter:
            continue
        results.append({
            "id": p.stem,
            "type": fm.get("gap_type", ""),
            "claim": fm.get("claim", "").strip().strip('"\'')[:80],
            "status": s,
            "anchor": fm.get("anchor", ""),
        })
    return results


def open_gap_count(project: str, *, config: Any = None) -> int:
    """Return the count of open (unresolved) gaps for a project.

    Used by ``rv status`` to surface the open-gaps count (D-GAP-4) — never
    inlines the records, only the count.
    """
    from research_vault.config import load_config as _load_config

    cfg = config or _load_config()
    pnd = cfg.project_notes_dir(project)
    gaps_dir = pnd / "gaps"
    if not gaps_dir.is_dir():
        return 0
    count = 0
    for p in gaps_dir.glob("*.md"):
        try:
            fm = _parse_frontmatter_gap(p.read_text(encoding="utf-8"))
            if fm.get("status", "open") == "open":
                count += 1
        except OSError:
            continue
    return count


def proven_open_count(project: str, *, config: Any = None) -> int:
    """Return the count of proven-open gaps for a project (SR-GAP-ROUTE §5L.16).

    A proven-open gap is a first-class run-candidate: the targeted lit pass
    saturated without closing the gap, confirming it is a candidate contribution.
    Used by ``rv status`` to surface the run-candidate count alongside the
    open-gap count — never inlines the records, only the count.
    """
    from research_vault.config import load_config as _load_config

    cfg = config or _load_config()
    pnd = cfg.project_notes_dir(project)
    gaps_dir = pnd / "gaps"
    if not gaps_dir.is_dir():
        return 0
    count = 0
    for p in gaps_dir.glob("*.md"):
        try:
            fm = _parse_frontmatter_gap(p.read_text(encoding="utf-8"))
            if fm.get("status", "") == "proven-open":
                count += 1
        except OSError:
            continue
    return count
