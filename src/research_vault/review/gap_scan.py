"""gap_scan.py — SR-LR-2: gap-driven pass (§5L.7–5L.10).

The gap-driven pass is Part-1 (SR-LR-1) invoked with a scope protocol
AUTO-AUTHORED from a detected gap record — no new DAG mechanism.

Architecture:
  - gap-detect = a rejects-only SCREEN (no auto-fire; human authorizes each pass).
  - ``rv review gap-scan`` is the surface: a cheap OKF graph query over findings/,
    concepts/, mocs/, and an optional manuscript critic report.
  - The screen emits typed ``gaps/<id>.md`` notes (first-class OKF type,
    SR-LR-2 §5L.8 D-GAP-1).
  - ``rv review gap-scope <gap-id> <scope>`` auto-authors the Part-1 scope protocol
    from the gap record (question ← claim; seed-queries ← type template;
    snowball-seeds ← anchor citekeys) and emits the Phase-1 manifest.
  - ``rv review gap-close <gap-id> --status <status>`` stamps closure.
  - ``rv status`` surfaces the OPEN gap count (D-GAP-4); records are never
    written inline into the control bus.

Four gap types (§5L.7 — attribution: type names from Miles 2017 and
Robinson et al. 2011; identification procedure from Müller-Bloch & Kranz 2015):
  knowledge_void    — finding with support-degree < threshold (D-GAP-2)
  contradictory     — concept with both supported_by AND contradicted_by edges
  evaluation_void   — finding asserting an effect with no comparator edge
  absent_row        — manuscript critic report [ABSENT]/[CONTRADICTS] row
                      (the loop-closer that makes manuscript↔lit-review a cycle, §5L.10)

Support-degree (D-GAP-2): count of entries in a finding's ``backed_by:`` frontmatter
field (the citekeys of literature/ notes that support the finding, as authored
by the ``relate-<key>`` Phase-2 fan-out nodes). Default threshold = 1.

Closure statuses (§5L.8):
  open              — gap detected, not yet addressed
  closed-supported  — manuscript matcher flipped [ABSENT]→[SUPPORTS]/[PARTIAL]
  closed-filled     — support-degree crossed threshold / MOC region filled
  proven-open       — targeted pass saturated without closing → candidate contribution

Stdlib only.
sr: SR-LR-2
"""
from __future__ import annotations

import datetime
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Gap type tokens (§5L.7 — Miles 2017 + Robinson et al. 2011 type names;
# Müller-Bloch & Kranz 2015 identification procedure).
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
_ABSENT_ROW_TOKENS = frozenset({"ABSENT", "CONTRADICTS"})

# Regex for critic report findings: FINDING N: [TOKEN] — description
_FINDING_RE = re.compile(
    r"FINDING\s+\d+\s*:\s*\[(?P<token>[A-Z]+)\]\s*[—\-]+\s*(?P<desc>.+?)(?=FINDING\s+\d+\s*:|SUMMARY\s*:|$)",
    re.DOTALL | re.IGNORECASE,
)

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
# Gap detectors (cheap OKF graph queries — §5L.7)
# ---------------------------------------------------------------------------

def _parse_frontmatter_simple(text: str) -> dict[str, Any]:
    """Parse YAML-like frontmatter between --- delimiters.

    Simplified parser for gap_scan: handles scalar and list values.
    Avoids importing note._parse_frontmatter to keep this module standalone.
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
            # List continuation
            if ln.startswith("  - "):
                if current_list_key:
                    fm[current_list_key].append(ln[4:].strip())
                i += 1
                continue
            current_list_key = None
            if ":" in ln:
                key, _, val = ln.partition(":")
                key = key.strip()
                val = val.strip()
                if val == "":
                    # Possibly a list follows
                    current_list_key = key
                    fm[key] = []
                else:
                    # Strip inline comments (# ...)
                    val = val.split(" #")[0].strip() if " #" in val else val
                    # Strip quotes
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
            fm = _parse_frontmatter_simple(text)
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

    Reference: Miles (2017) — Knowledge Void gap type.
    Identification procedure: Müller-Bloch & Kranz (2015).
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

    Reference: Robinson et al. (2011) — Contradictory Evidence gap type.
    Identification procedure: Müller-Bloch & Kranz (2015).
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

    Reference: Miles (2017) — Evaluation Void gap type.
    Identification procedure: Müller-Bloch & Kranz (2015).
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


def _detect_absent_rows(critic_report_path: Path) -> list[GapRecord]:
    """Detect Absent Row gaps from a manuscript critic report (the loop-closer, §5L.10).

    Greps the report for FINDING lines bearing [ABSENT] or [CONTRADICTS] verdicts —
    a drafted manuscript claim with no backing literature note is exactly the
    loop-closer gap that makes manuscript↔lit-review a cycle.

    Only [ABSENT] and [CONTRADICTS] trigger gaps (BLOCK-class verdicts).
    [PARTIAL] is WARN-only and is NOT surfaced as a gap by this detector.

    Uses the same \\[(.+?)\\] extractor convention that SR-CI and SR-MS-2 use (D-GAP-3).
    """
    if not critic_report_path.exists():
        return []
    try:
        text = critic_report_path.read_text(encoding="utf-8")
    except OSError:
        return []
    gaps: list[GapRecord] = []
    for m in _FINDING_RE.finditer(text):
        token = m.group("token").strip().upper()
        if token not in _ABSENT_ROW_TOKENS:
            continue
        desc = m.group("desc").strip()
        # Extract the claim text from the description (strip trailing newlines/spaces)
        desc = re.sub(r"\s+", " ", desc).strip()
        # Strip a trailing SUMMARY fragment if regex was greedy
        desc = re.sub(r"\s*(SUMMARY\s*:.*)$", "", desc, flags=re.IGNORECASE).strip()
        gaps.append(GapRecord(
            type=GAP_TYPE_ABSENT_ROW,
            anchor=str(critic_report_path),
            claim=desc,
            why=(
                f"manuscript critic report: FINDING [{token}] — "
                f"drafted claim has no backing literature/ note "
                f"(the loop-closer gap, §5L.10)"
            ),
            status="open",
            _meta={"verdict": token, "report": str(critic_report_path)},
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
    lines = [
        "---",
        f"type: gaps",
        f"id: {gap_id}",
        f"gap_type: {rec.type}",
        f"anchor: {rec.anchor}",
        f"claim: \"{rec.claim.replace(chr(34), chr(39))}\"",
        f"why: \"{rec.why.replace(chr(34), chr(39))}\"",
        f"status: {rec.status}",
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
        "Gap *types* (Knowledge Void, Evaluation Void, Contradictory Evidence): "
        "Miles (2017); Robinson et al. (2011). "
        "Gap identification *procedure*: Müller-Bloch & Kranz (2015)."
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
            fm = _parse_frontmatter_simple(p.read_text(encoding="utf-8"))
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
    critic_report: Path | None = None,
) -> list[GapRecord]:
    """Scan the project's OKF corpus for typed research gaps.

    Runs four typed detectors (§5L.7):
      1. Knowledge Void: findings with support-degree < threshold
      2. Contradictory Evidence: concepts with both supported_by + contradicted_by
      3. Evaluation Void: findings with effect but no comparator
      4. Absent Row: manuscript critic report [ABSENT]/[CONTRADICTS] rows

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
    if critic_report is not None:
        all_gaps.extend(_detect_absent_rows(critic_report))

    existing = _existing_gap_ids(pnd)

    new_gaps: list[GapRecord] = []
    for rec in all_gaps:
        gid = _gap_id(rec.type, rec.anchor, rec.claim)
        if gid in existing:
            # Gap already recorded — do NOT overwrite (preserves closed status)
            continue
        _write_gap_note(rec, gid, pnd)
        new_gaps.append(rec)

    return new_gaps


def cmd_gap_scope(
    project: str,
    gap_id: str,
    scope: str,
    *,
    config: Any = None,
) -> "dict[str, Any]":
    """Auto-author a Part-1 (SR-LR-1) review scope from a gap record (§5L.7).

    - question ← gap.claim (exact words, the anti-fabrication spine)
    - seed_queries ← per-type templates derived from the claim
    - snowball_seeds ← anchor's citekeys (backed_by/supported_by)
    - inclusion ← 'resolves this gap'

    Creates the Phase-1 DAG via cmd_new and writes ``_gap-context.md`` into
    reviews/<scope>/ with the auto-authored protocol content.
    """
    from research_vault.config import load_config as _load_config
    from research_vault.review import cmd_new

    cfg = config or _load_config()
    pnd = cfg.project_notes_dir(project)

    # Load the gap record
    gap_path = _gap_note_path(pnd, gap_id)
    if not gap_path.exists():
        raise FileNotFoundError(f"Gap note not found: {gap_path}")
    fm = _parse_frontmatter_simple(gap_path.read_text(encoding="utf-8"))
    gap_type = fm.get("gap_type", "knowledge_void")
    claim = fm.get("claim", "").strip().strip('"\'')
    anchor = fm.get("anchor", "")

    if not claim:
        raise ValueError(f"Gap note {gap_id!r} has no claim field")

    # Derive snowball seeds from the anchor note (backed_by / supported_by)
    snowball_seeds: list[str] = []
    anchor_path = pnd / anchor if not Path(anchor).is_absolute() else Path(anchor)
    if anchor_path.exists():
        try:
            a_fm = _parse_frontmatter_simple(anchor_path.read_text(encoding="utf-8"))
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
        config=cfg,
    )

    # Write _gap-context.md with the auto-authored protocol seed
    context_lines = [
        "# Gap-context (auto-authored — SR-LR-2 §5L.7)",
        "",
        f"**Gap ID:** {gap_id}",
        f"**Gap type:** {gap_type}",
        f"**Anchor:** {anchor}",
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
        "Gap types: Miles (2017); Robinson et al. (2011). "
        "Identification procedure: Müller-Bloch & Kranz (2015)."
    )
    context_path = review_dir / "_gap-context.md"
    context_path.write_text("\n".join(context_lines), encoding="utf-8")

    return manifest


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
    fm = _parse_frontmatter_simple(text)
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
            fm = _parse_frontmatter_simple(p.read_text(encoding="utf-8"))
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
            fm = _parse_frontmatter_simple(p.read_text(encoding="utf-8"))
            if fm.get("status", "open") == "open":
                count += 1
        except OSError:
            continue
    return count
