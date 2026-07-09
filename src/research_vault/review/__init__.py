# SPDX-License-Identifier: AGPL-3.0-or-later
"""review — SR-LR-1: staged, pre-registered, saturation-gated literature review loop.

The review loop is the manuscript loop's sibling — it composes SR-3 (DAG) with zero
new walker/schema mechanism.  The fan-out over a runtime-discovered corpus is resolved
at the ``coverage-gate`` phase boundary via a second manifest emitted by
``rv review expand`` (§5L.4 ruling).

★ Option C hybrid (review-loop-nodekind-drift-fix, 2026-07-09): Phase-1's
review-search/review-snowball are each split into a deterministic TOOL node (the
mechanical fetch/graph-walk — ``review.autonomy``'s ``sweep``/``snowball`` ops) followed
by a thin AGENT node (the judgment layer — inclusion/exclusion screening, concept-
tagging, honest residue prose). This replaces the pre-2026-07-09 shape where
review-search/review-snowball were themselves agent nodes whose specs instructed the
agent to shell ``rv research sweep``/``cited-by``/``references`` — verbs D1
(verb-consolidation) hard-removed. See the spec for the full rationale (§2-3).

Provides:
  - cmd_new:    scaffold a review OKF note + reviews/<scope>/ dir + Phase-1 DAG manifest
  - cmd_list:   list review notes for a project
  - cmd_expand: emit Phase-2 manifest from a frozen _corpus.md (post coverage-gate)

The ``review_tips`` config seam (§5L.6, ``review/style.py``) is the content socket:
six keys (review_scope_tips, review_screen_tips, review_curate_tips,
per_paper_relate_tips, review_synthesize_tips, review_critic_tips) drive each agent
node's spec string.  Adopters override via ``[review_style]`` in research_vault.toml.

Corpus helpers (_corpus_annotation) are imported directly from
research_vault.research — NOT scraped from stdout (§5L.11 prereq-composition rule).

Stdlib only.
sr: SR-LR-1
"""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any

from research_vault.config import Config, load_config
from research_vault.note import (
    OKF_TYPES,
    _parse_frontmatter,
    _render_frontmatter,
    scaffold_okf_dirs,
)
from research_vault.review.style import (
    get_review_tips,
    get_review_style_preamble,
    get_saturation_backstop_waves,
)

# Corpus helpers imported directly (not scraping stdout — §5L.11)
from research_vault.research import (
    _corpus_annotation,  # noqa: F401
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.date.today().isoformat()


def _review_note_dir(project: str, cfg: Config) -> Path:
    """OKF note directory for review notes: project_notes_dir/reviews/."""
    return cfg.project_notes_dir(project) / "reviews"


def _review_artifact_dir(project: str, scope_id: str, cfg: Config) -> Path:
    """Artifact working dir for a review: project_notes_dir/reviews/<scope>/."""
    return cfg.project_notes_dir(project) / "reviews" / scope_id


# ---------------------------------------------------------------------------
# L-2 anti-fishing structural gate (task #33) — counter-position enforcement
# ---------------------------------------------------------------------------

def check_protocol_gate(protocol_path: Path) -> tuple[bool, str]:
    """Structural L-2 anti-fishing check: ``_protocol.md`` must carry a
    non-empty ``counter-position`` frontmatter field.

    This is the native rv enforcement of the L-2 gate that was previously
    ``review_scope_tips``/``review_critic_tips`` prose only (agent-instructed,
    never mechanically checked). Wired into ``rv dag approve`` at the
    ``approve-protocol`` node (§5L.3) so the gate refuses structurally,
    not just by prompt convention.

    Uses ``note._parse_frontmatter`` (the canonical parser) — no re-rolled
    YAML/regex logic (charter §6: reuse over create).

    Args:
        protocol_path: path to the review's ``_protocol.md`` artifact.

    Returns:
        (ok, message) — ok is False when the file is missing, or the
        ``counter-position`` field is absent/empty/whitespace-only.

    sr: SR-LR-1 (task #33)
    """
    if not protocol_path.exists():
        return False, (
            f"rv dag approve: L-2 gate BLOCKED — _protocol.md not found at "
            f"{protocol_path}. The review-scope node must produce this file "
            f"before approve-protocol can pass."
        )

    text = protocol_path.read_text(encoding="utf-8")
    fields, _ = _parse_frontmatter(text)
    counter = fields.get("counter-position", "")
    if isinstance(counter, list):
        counter = " ".join(str(item) for item in counter)
    if not str(counter).strip():
        return False, (
            f"rv dag approve: L-2 gate BLOCKED — {protocol_path} has an "
            f"empty or missing 'counter-position' frontmatter field.\n"
            f"The anti-fishing gate (§5L.3) requires the protocol to name a "
            f"falsifying/opposing sub-literature BEFORE search executes.\n"
            f"Fix: edit {protocol_path.name} to add a non-empty "
            f"'counter-position: <...>' field, then re-run "
            f"`rv dag approve <run_id> approve-protocol`."
        )

    return True, "OK"


# ---------------------------------------------------------------------------
# approve-review structural gate (single-human-gate design, 2026-07-09) —
# parse review-coverage-critic's [PASS]/[BLOCK] verdict artifact
# ---------------------------------------------------------------------------

# Matches the bracketed gate token anywhere WITHIN a candidate verdict line
# (used only to count tokens on that line for the ambiguity check, below).
_COVERAGE_CRITIC_VERDICT_RE = re.compile(r"\[(PASS|BLOCK)\]", re.IGNORECASE)
# Matches only when the bracket token OPENS the line (mod. leading whitespace)
# — the critic's own template (``review_critic_tips``'s "Honest output
# template") always OPENS a line with ``[PASS]: ...`` or ``[BLOCK]: ...``.
# Anchoring at line-start (not an anywhere-search) is the fail-open fix
# (2026-07-09, reviewer-confirmed on PR #201): free prose that merely
# MENTIONS the other token mid-sentence (e.g. "...does not merit [PASS] on
# axis 4.") is never mistaken for the real verdict, because the mention is
# not at the start of its line.
_COVERAGE_CRITIC_VERDICT_LINE_RE = re.compile(r"^\s*\[(PASS|BLOCK)\]", re.IGNORECASE)
_COVERAGE_CRITIC_BULLET_RE = re.compile(r"^\s*-\s+(.+?)\s*$")


def check_coverage_critic_verdict(critic_note_path: Path) -> dict[str, Any]:
    """Parse ``review-coverage-critic``'s ``[PASS]``/``[BLOCK]`` verdict note
    into the ``{"blocking": [...], "not_run": [...]}`` structural-payload
    shape ``review.autonomy.evaluation_from_structural_payload`` consumes —
    the SAME adapter ``approve-framework``/``approve-manuscript`` already use
    (charter §6 reuse-over-create; no new disposition path invented).

    - Missing artifact -> ``not_run`` (a floor gate that never ran must never
      look like a pass, §1.2 priority 2 / explore-rl #3).
    - No line that OPENS with a ``[PASS]``/``[BLOCK]`` token -> ``not_run``
      (an unparseable verdict is untrustworthy, not a silent PASS — charter
      §2 whitelist-not-blacklist). A bracket token mentioned mid-sentence
      elsewhere in the note is NEVER treated as the verdict.
    - The verdict LINE carries more than one recognized token (e.g. a legend
      line ``[PASS] = clean, [BLOCK] = holes`` that happens to open with a
      bracket, or a malformed ``[PASS][BLOCK]: ...`` line) -> ``not_run``
      (ambiguous, fail-closed — never guess which token is the real verdict).
    - ``[PASS]`` (sole token, line-opening) -> ``blocking: []`` (GO).
    - ``[BLOCK]`` (sole token, line-opening) -> ``blocking`` is every
      ``- <reason>`` bullet line immediately following the verdict line (the
      critic's own "list each" template); an empty bullet list still counts
      as one generic blocking reason (never a BLOCK verdict silently
      downgraded to a pass because no bullets were parsed).

    sr: PR #201 review delta (fail-open fix) — 2026-07-09
    """
    if not critic_note_path.exists():
        return {"blocking": [], "not_run": [str(critic_note_path)]}

    text = critic_note_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    verdict_idx = None
    for i, line in enumerate(lines):
        if _COVERAGE_CRITIC_VERDICT_LINE_RE.match(line):
            verdict_idx = i
            break

    if verdict_idx is None:
        return {
            "blocking": [],
            "not_run": [
                f"{critic_note_path}: no line opens with a [PASS]/[BLOCK] "
                f"verdict token — the critic's template requires the "
                f"verdict at the START of a line; a mid-sentence mention "
                f"elsewhere in the note is never treated as the verdict"
            ],
        }

    verdict_line = lines[verdict_idx]
    tokens = _COVERAGE_CRITIC_VERDICT_RE.findall(verdict_line)
    if len(tokens) != 1:
        return {
            "blocking": [],
            "not_run": [
                f"{critic_note_path}: verdict line is ambiguous — carries "
                f"{len(tokens)} [PASS]/[BLOCK] tokens, not exactly one: "
                f"{verdict_line.strip()!r}"
            ],
        }

    verdict = tokens[0].upper()
    if verdict == "PASS":
        return {"blocking": [], "not_run": []}

    # BLOCK: collect every "- <reason>" bullet line contiguous with the
    # verdict line (skipping blank lines), per the critic's own template.
    reasons: list[str] = []
    for line in lines[verdict_idx + 1:]:
        if not line.strip():
            continue
        bullet_m = _COVERAGE_CRITIC_BULLET_RE.match(line)
        if bullet_m is None:
            break
        reasons.append(bullet_m.group(1))
    if not reasons:
        reasons = ["[BLOCK] verdict with no itemized reason bullets found"]
    return {"blocking": reasons, "not_run": []}


# ---------------------------------------------------------------------------
# Saturation backstop (SR-LR-1-BACKSTOP) — coverage-gate surfacing
# ---------------------------------------------------------------------------

def check_saturation_backstop(saturation_path: Path) -> dict[str, Any]:
    """Read the snowball loop's ``stop_reason:`` off ``_saturation.md``.

    The saturation loop is the deterministic ``snowball`` tool op (an
    INTERNAL loop inside the ``review-snowball`` TOOL node — no per-round DAG
    nodes, §5L.2; review-loop-nodekind-drift-fix Option C). It stamps flat
    frontmatter at the top of ``_saturation.md`` recording which stop rule
    fired:
      - ``stop_reason: saturated``          — PRIMARY rule: 2-consecutive-zero
        rounds (genuine saturation plateau).
      - ``stop_reason: backstop:<N>-waves`` — BACKSTOP rule (SR-LR-1-BACKSTOP):
        the wave cap (``saturation_backstop_waves``, default 3) fired WITHOUT
        the primary rule converging first. The corpus is bounded, NOT
        saturated.
      - ``stop_reason: no-seeds-resolved``   — every seed id failed to
        resolve on both directions (e.g. all-404) and zero hits were ever
        obtained (2026-07-09 live-asta fix) — NOT genuine saturation; falls
        through the whitelist below to HALT-DECLARE, same as any other
        non-canonical value.

    Uses ``note._parse_frontmatter`` (the canonical parser) — no re-rolled
    YAML/regex logic (charter §6: reuse over create), mirroring
    ``check_protocol_gate``'s use of the same parser on ``_protocol.md``.

    Args:
        saturation_path: path to the review's ``_saturation.md`` artifact.

    Returns:
        dict with keys:
          exists:      bool       — whether _saturation.md was found.
          stop_reason: str        — the raw stamped value ("" if absent/missing).
          is_backstop: bool       — True iff stop_reason starts with "backstop:".
          wave_count:  int | None — parsed N from "backstop:N-waves", else None.

    charter §2 (surface, never silently drop): this function never fabricates
    ``"saturated"`` for anything it can't confirm. A MISSING field returns
    ``stop_reason == ""``; a NON-CANONICAL value (e.g. ``"backstop-3-waves"``
    with a dash, free prose, or garbage) is returned VERBATIM, not blanked —
    ``is_backstop`` is simply False for it, same as for an empty string. It is
    the CALLER's job (the ``coverage-gate`` human-go wiring in ``dag/verbs.py``)
    to treat "anything that isn't the exact string 'saturated'" as needing a
    loud SIGNAL — a WHITELIST on the one recognized-good value, not a
    blacklist on the one recognized-bad prefix. A blacklist here would fail
    OPEN: agent-stamped free prose has no fixed vocabulary, so every
    non-``backstop:``-prefixed spelling of a non-saturated outcome would sail
    through silently and look identical to genuine saturation at the gate.

    sr: SR-LR-1-BACKSTOP
    """
    if not saturation_path.exists():
        return {"exists": False, "stop_reason": "", "is_backstop": False, "wave_count": None}

    text = saturation_path.read_text(encoding="utf-8")
    fields, _ = _parse_frontmatter(text)
    stop_reason = fields.get("stop_reason", "")
    if isinstance(stop_reason, list):
        stop_reason = " ".join(str(item) for item in stop_reason)
    stop_reason = str(stop_reason).strip()

    is_backstop = stop_reason.lower().startswith("backstop:")
    wave_count: int | None = None
    if is_backstop:
        m = re.match(r"^backstop:(\d+)-waves?$", stop_reason, re.IGNORECASE)
        if m:
            wave_count = int(m.group(1))

    return {
        "exists": True,
        "stop_reason": stop_reason,
        "is_backstop": is_backstop,
        "wave_count": wave_count,
    }


# ---------------------------------------------------------------------------
# Coverage report (F16+F17) — deterministic, keyed by citekey
# ---------------------------------------------------------------------------

class CorpusSchemaError(ValueError):
    """A row inside the ``_corpus.md`` table has a bracket-shaped annotation
    (column 0 looks like ``[...]``) that is NEITHER ``[NEW]`` nor
    ``[IN-CORPUS:*]`` — a malformed hand-written or remediation-appended row.

    NG-6a §3 (the green-but-stale fix, explore-rl #2): the pre-NG-6a parser
    silently ``continue``d past such a row, so a malformed remediation
    append vanished from the corpus set and ``coverage_report``/the
    coverage-gate audited a stale subset while reporting green. This is a
    loud reject instead (charter §2 — surface, never silently drop).

    Narrow structural signal, not "any unrecognized row": a bullet/prose
    line or a header/separator row (whose column-0 is NOT bracket-shaped)
    is still a correct, silent skip — only a row that LOOKS like a tagged
    corpus row (``[...]``-shaped column 0) but isn't one of the two
    recognized tags is a schema violation.
    """


def _parse_corpus_citekeys(corpus_path: Path) -> list[str]:
    """Return ALL citekeys in _corpus.md (both [NEW] and [IN-CORPUS:*]).

    Used by coverage_report as the source-of-truth key set.
    The corpus is the frozen manifest — it is always right.

    Raises ``CorpusSchemaError`` (loud reject) on a bracket-shaped column-0
    annotation that is neither ``[NEW]`` nor ``[IN-CORPUS:*]`` — see
    ``CorpusSchemaError`` (NG-6a §3). Non-table prose and header/separator
    rows (column-0 is NOT bracket-shaped) are still a correct, silent skip.

    sr: SR-LR-1, NG-6a
    """
    if not corpus_path.exists():
        return []
    text = corpus_path.read_text(encoding="utf-8")
    citekeys: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cols = [c.strip() for c in stripped.split("|") if c.strip()]
        if len(cols) < 2:
            continue
        annotation = cols[0]
        # Both [NEW] and [IN-CORPUS:*] rows carry a citekey in column 2
        if annotation.upper() == "[NEW]" or re.match(r"^\[IN-CORPUS:", annotation, re.IGNORECASE):
            citekey = cols[1]
            if re.match(r"^[A-Za-z0-9_:\-\.]+$", citekey):
                citekeys.append(citekey)
            continue
        if re.match(r"^\[.*\]$", annotation):
            # Bracket-shaped but not a recognized tag — a schema violation.
            # Loud reject, never a silent skip (NG-6a §3).
            raise CorpusSchemaError(
                f"{corpus_path}:{lineno}: malformed corpus row annotation "
                f"{annotation!r} — expected '[NEW]' or '[IN-CORPUS:<citekey>]'. "
                f"Row: {stripped!r}"
            )
        # Not bracket-shaped at all (header row, separator row, free prose
        # bullet inside the table region) — a correct, silent skip.
    return citekeys


def _index_literature_notes_by_citekey(literature_dir: Path) -> dict[str, Path]:
    """Build a citekey → note-path index from the project's literature/ OKF dir.

    F17: identity is the ``citekey:`` frontmatter field (filename-agnostic).
    Falls back to the filename stem ONLY if the field is absent or empty.
    This allows descriptive filenames like ``zheng2023-pride-mc-selectors.md``
    while matching the corpus citekey ``zheng2023-pride`` without false-orphaning.

    Returns:
        dict mapping citekey (str) → Path of the corresponding literature note.
        If literature_dir does not exist, returns {}.
    """
    if not literature_dir.exists():
        return {}

    index: dict[str, Path] = {}
    for note_path in sorted(literature_dir.glob("*.md")):
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fields, _ = _parse_frontmatter(text)
        # F17: prefer the citekey: field over the filename stem
        citekey = (fields.get("citekey") or "").strip()
        if not citekey:
            citekey = note_path.stem
        index[citekey] = note_path
    return index


def _collect_moc_citekey_mentions(mocs_dir: Path) -> set[str]:
    """Collect all citekeys mentioned in any mocs/*.md file.

    Used for orphan detection: a citekey that appears in at least one MOC
    region is NOT an orphan.  A cheap text scan — we look for citekey-like
    tokens (matching the same pattern as corpus citekeys) anywhere in the
    MOC body.  A mention anywhere in the file counts.

    Returns a frozenset of mentioned citekeys (strings).
    """
    mentions: set[str] = set()
    if not mocs_dir.exists():
        return mentions
    for moc_path in sorted(mocs_dir.glob("*.md")):
        try:
            text = moc_path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Scan for citekey-like tokens: alphanumeric + _ : - .
        # A citekey appears when it follows a [ or is preceded by whitespace/pipe
        for token in re.findall(r"\b([A-Za-z][A-Za-z0-9_:\-\.]{2,})\b", text):
            mentions.add(token)
    return mentions


def coverage_report(
    project: str,
    scope: str,
    *,
    config: Config | None = None,
) -> dict[str, Any]:
    """Deterministic corpus-coverage check keyed by citekey (F16+F17).

    Source-of-truth: the frozen ``_corpus.md`` manifest.  Identity of literature
    notes is the ``citekey:`` frontmatter field (filename-agnostic — F17 fix).

    Reports per citekey:
      - ``materialized``:   a ``literature/`` note with matching citekey: field exists.
      - ``unmaterialized``: corpus citekey with no matching note (gap — F16).
      - ``orphan``:         materialized but absent from every ``mocs/`` region.
      - ``mention_only``:   (placeholder — detection not yet implemented; [] always).
      - ``counts``:         summary counts for all four categories.

    Why filename-agnostic?
      Descriptive filenames like ``zheng2023-pride-mc-selectors.md`` carrying
      ``citekey: zheng2023-pride`` must match corpus entry ``zheng2023-pride``
      without being flagged orphan.  Stem-based matching was the original bug (F17).

    Args:
        project: project slug (must be in config registry).
        scope:   review scope identifier (used to locate ``_corpus.md``).
        config:  optional Config (loaded if None).

    Returns:
        dict with keys:
          corpus_citekeys:   list[str]   — all citekeys in _corpus.md
          materialized:      list[str]   — corpus citekeys with a matching lit note
          unmaterialized:    list[str]   — corpus citekeys with no matching lit note
          orphan:            list[str]   — materialized citekeys absent from all MOCs
          mention_only:      list[str]   — placeholder (always [])
          counts:            dict        — summary counts

    surface, never green-and-empty: returns structured data always;
    empty corpus → empty lists, not None.

    sr: SR-LR-1
    """
    cfg = config or load_config()
    project_notes_dir = cfg.project_notes_dir(project)
    review_dir = _review_artifact_dir(project, scope, cfg)
    corpus_path = review_dir / "_corpus.md"
    literature_dir = project_notes_dir / "literature"
    mocs_dir = project_notes_dir / "mocs"

    # Source-of-truth: corpus citekeys (all annotated rows)
    corpus_citekeys: list[str] = _parse_corpus_citekeys(corpus_path)

    # Index literature notes by citekey: field (F17 — filename-agnostic)
    lit_index: dict[str, Path] = _index_literature_notes_by_citekey(literature_dir)

    # Index MOC mentions for orphan detection
    moc_mentions: set[str] = _collect_moc_citekey_mentions(mocs_dir)

    materialized: list[str] = []
    unmaterialized: list[str] = []
    orphan: list[str] = []

    for ck in corpus_citekeys:
        if ck in lit_index:
            materialized.append(ck)
            # Orphan: materialized but absent from all MOC files
            if ck not in moc_mentions:
                orphan.append(ck)
        else:
            unmaterialized.append(ck)

    return {
        "corpus_citekeys": corpus_citekeys,
        "materialized": materialized,
        "unmaterialized": unmaterialized,
        "orphan": orphan,
        "mention_only": [],  # placeholder for future MENTION-ONLY detection
        "counts": {
            "corpus": len(corpus_citekeys),
            "materialized": len(materialized),
            "unmaterialized": len(unmaterialized),
            "orphan": len(orphan),
            "mention_only": 0,
        },
    }


# ---------------------------------------------------------------------------
# Wave 0 (Reading) PR-2 — paper→paper typed-edge aggregation (the "consume" seam)
# ---------------------------------------------------------------------------

def relations_report(
    project: str,
    scope: str,
    *,
    config: Config | None = None,
) -> dict[str, Any]:
    """Deterministic, corpus-wide paper→paper typed-edge listing (PR-2).

    This is the mechanical "consume" seam the design doc calls for: instead of
    `review-synthesize` (and `review-coverage-critic`) RE-DERIVING the
    comparative spine from prose each run, they traverse the edges the
    relate-<key> fan-out already emitted (`## Related papers` body sections,
    parsed by ``relate_check.parse_paper_relations``). Mirrors
    ``coverage_report``'s pattern exactly — same anti-pattern this closes
    ("do NOT hand-stem-match... run the deterministic command").

    Reuse-over-create (charter §6): zero new edge mechanism — this is a
    corpus-wide fold of the SAME parser the presence check uses per-note.

    Returns:
        dict with keys:
          edges:      list[dict] — {source, target, tag, type, reason,
                      kind_mismatch} per edge, source = the citing literature
                      note's citekey.
          by_pair:    dict[(source, target)] → the edge dict (for traversal
                      lookups by the synthesize/critic agent nodes).
          malformed:  list[dict] — {source, line} for every '- ['-shaped
                      line under a note's '## Related papers' section that
                      did NOT parse to a valid edge (architect review, the
                      load-bearing fix — NEVER silently dropped; surface
                      this, don't just log the well-formed edges).
          dangling:   list[dict] — edges whose target citekey does not match
                      any literature note in this project (SIGNAL, mirrors
                      coverage_report's orphan reporting) — a candidate
                      typo/uningested-paper flag, not a hard error.
          counts:     summary counts by relation type + malformed + dangling.

    surface, never green-and-empty: an empty corpus returns empty lists, not None.

    sr: NG-lit-review-wave0 (PR-2)
    """
    from .relate_check import parse_paper_relations

    cfg = config or load_config()
    project_notes_dir = cfg.project_notes_dir(project)
    literature_dir = project_notes_dir / "literature"

    edges: list[dict[str, Any]] = []
    malformed: list[dict[str, str]] = []
    known_citekeys: set[str] = set()
    if literature_dir.exists():
        for note_path in sorted(literature_dir.glob("*.md")):
            try:
                text = note_path.read_text(encoding="utf-8")
            except OSError:
                continue
            fields, body = _parse_frontmatter(text)
            source_citekey = (fields.get("citekey") or "").strip() or note_path.stem
            known_citekeys.add(source_citekey)
            parsed = parse_paper_relations(body)
            for edge in parsed.edges:
                edges.append({
                    "source": source_citekey,
                    "target": edge["target"],
                    "tag": edge["tag"],
                    "type": edge["type"],
                    "reason": edge["reason"],
                    "kind_mismatch": edge["kind_mismatch"],
                })
            for bad_line in parsed.malformed:
                malformed.append({"source": source_citekey, "line": bad_line})

    by_pair: dict[tuple[str, str], dict[str, Any]] = {
        (e["source"], e["target"]): e for e in edges
    }

    # Dangling-edge check (recommended, architect review): a target citekey
    # this project has no matching literature/ note for. Computed AFTER the
    # full corpus scan so a forward-declared target (any note, any file
    # order) is never mistaken for dangling.
    dangling: list[dict[str, Any]] = [
        e for e in edges if e["target"] not in known_citekeys
    ]

    counts: dict[str, int] = {"reciprocal": 0, "refutational": 0, "line-of-argument": 0}
    for e in edges:
        if e["type"] in counts:
            counts[e["type"]] += 1

    return {
        "edges": edges,
        "by_pair": by_pair,
        "malformed": malformed,
        "dangling": dangling,
        "counts": {
            **counts,
            "total": len(edges),
            "malformed": len(malformed),
            "dangling": len(dangling),
        },
    }


# ---------------------------------------------------------------------------
# Phase-1 DAG manifest builder
# ---------------------------------------------------------------------------

def _build_phase1_manifest(
    project: str,
    scope_id: str,
    question: str,
    review_dir: Path,
    project_notes_dir: Path,
    *,
    tip_override: dict[str, str] | None = None,
    config: Any = None,
) -> dict[str, Any]:
    """Build the Phase-1 DAG manifest (§5L.1 shape; Option C hybrid,
    review-loop-nodekind-drift-fix, 2026-07-09).

    Phase-1 nodes (7):
      review-scope → [HG:approve-protocol] → review-search (tool) → review-screen (agent)
          → review-snowball (tool) → review-curate (agent) → coverage-gate (auto-resolved)

    Topology:
      - review-scope:     agent; produces _protocol.md
      - approve-protocol: human-go (Gate 1 — cheap screen before expensive search)
      - review-search:    TOOL (op "sweep"); needs afterok+watch on _protocol.md
                          (anti-fishing gate); produces _search_hits.md
                          (deterministic width-sweep — no LLM)
      - review-screen:    agent (thin judgment layer); reads _search_hits.md,
                          applies inclusion/exclusion, produces _screen.md
                          (the accepted seed frontier)
      - review-snowball:  TOOL (op "snowball"); needs afterok+watch on _screen.md;
                          produces _corpus_raw.md + _saturation.md (the both-
                          direction multi-round saturation walk — no LLM)
      - review-curate:    agent (thin judgment layer); reads _corpus_raw.md +
                          _saturation.md, concept-tags + applies inclusion/
                          exclusion, produces _corpus.md (+ _coverage-gaps.md
                          on backstop-termination)
      - coverage-gate:    human-go (phase boundary — Phase-2 static fan-out
                          authorized here)

    Why split each of review-search/review-snowball into tool+agent: the
    mechanical fraction (fetch/dedup/derivative-discount/rank/graph-walk/
    saturation-curve) is large and fully deterministic — it lives in
    ``review.autonomy``'s tool-op registry, invoked IN-PROCESS by the DAG
    runner (D4, verb-consolidation). The irreducibly-LLM fraction (applying
    inclusion/exclusion judgment, concept-tagging, honest residue prose) is
    thin — it stays an agent node. An agent node cannot invoke an in-process
    tool op (it can only shell CLI verbs, and the mechanical verbs this used
    to shell — ``rv research sweep``/``cited-by``/``references`` — are D1
    hard-removed), so the op runs FIRST and hands the agent a written
    artifact to judge, never the reverse.

    The artifact-watch on _protocol.md makes the anti-fishing structural: search
    physically cannot fire until the protocol note is filed (§5L.3 ruling).

    Zero new walker/schema mechanism — all edges are standard afterok/artifact-watch.

    sr: SR-LR-1
    """
    tips = get_review_tips(config=config)
    preamble = get_review_style_preamble(config=config)

    def _spec(key: str) -> str:
        tip = tips.get(key, f"Execute the {key} step.")
        return preamble.rstrip() + "\n\n---\n\n" + tip

    def _afterok(from_id: str) -> dict[str, Any]:
        return {"from": from_id, "edge": "afterok"}

    # Absolute OKF type-dir pointers (Fix #34: emit absolute paths so the
    # reads:-grounding resolver finds the real OKF dirs regardless of what
    # project_root=manifest_path.parent is at run/tick time).
    # Previously this returned a bare name like "literature" which resolved
    # relative to the manifest dir (reviews/<scope>/) — always wrong.
    def _rel(okf_type: str) -> str:
        return str(project_notes_dir / okf_type)

    # Absolute path to review artifact dir (for produces: and watch: expressions)
    protocol_path = str(review_dir / "_protocol.md")
    search_hits_path = str(review_dir / "_search_hits.md")
    screen_path = str(review_dir / "_screen.md")
    corpus_raw_path = str(review_dir / "_corpus_raw.md")
    saturation_path = str(review_dir / "_saturation.md")
    corpus_path = str(review_dir / "_corpus.md")

    nodes: list[dict[str, Any]] = []

    # 1. review-scope — freeze question + seed queries + inclusion/exclusion +
    #    coverage claim + REQUIRED counter-position (L-2 gate) before any search.
    # produces: uses filename as key (schema ignores unknown keys; key is the
    # discovery surface for tests + the walker's artifact-watch resolver).
    nodes.append({
        "id": "review-scope",
        "type": "agent",
        "label": f"Freeze review protocol (question + seeds + counter-position): {question[:60]}",
        "spec": _spec("review_scope_tips"),
        "reads": [
            _rel("concepts"),
            _rel("findings"),
            _rel("mocs"),
            _rel("literature"),
        ],
        "produces": {"_protocol.md": protocol_path},
        "needs": [],
    })

    # 2. approve-protocol — human-go Gate 1 (cheap screen before expensive search)
    nodes.append({
        "id": "approve-protocol",
        "type": "human-go",
        "label": "Gate 1: Approve review protocol (question + counter-position frozen)",
        "needs": [_afterok("review-scope")],
    })

    # 3. review-search — TOOL node (D4 op "sweep"): the deterministic
    #    parallel width-sweep over the frozen protocol's angle matrix.
    #    Gated by afterok AND artifact-watch on _protocol.md — the watch
    #    makes anti-fishing structural: search cannot fire without a fresh
    #    protocol (§5L.3).
    nodes.append({
        "id": "review-search",
        "type": "tool",
        "op": "sweep",
        "label": "Width-sweep the frozen protocol's angle matrix (deterministic; protocol-gated)",
        "args": {
            "protocol": protocol_path,
            "out": search_hits_path,
            "project": project,
        },
        "produces": {"_search_hits.md": search_hits_path},
        "needs": [
            {
                "from": "approve-protocol",
                "edge": "afterok",
                "watch": f"artifact:{protocol_path}+fresh",
            }
        ],
    })

    # 4. review-screen — thin AGENT judgment layer: apply the frozen
    #    protocol's inclusion/exclusion to _search_hits.md and accept a seed
    #    frontier for the snowball tool op (Option C hybrid).
    nodes.append({
        "id": "review-screen",
        "type": "agent",
        "label": "Screen search hits against inclusion/exclusion; accept seed frontier",
        "spec": _spec("review_screen_tips"),
        "reads": [
            _rel("literature"),
            protocol_path,
            search_hits_path,
        ],
        "produces": {"_screen.md": screen_path},
        "needs": [
            {
                "from": "review-search",
                "edge": "afterok",
                "watch": f"artifact:{search_hits_path}+fresh",
            }
        ],
    })

    # 5. review-snowball — TOOL node (D4 op "snowball"): the deterministic
    #    both-direction, multi-round saturation walk (§5L.2's mechanical
    #    half — see review.autonomy.run_snowball_to_saturation's declared
    #    concept-tag-half caveat). Produces _corpus_raw.md (raw candidates)
    #    + _saturation.md (the plateau curve, stop_reason:).
    nodes.append({
        "id": "review-snowball",
        "type": "tool",
        "op": "snowball",
        "label": "Both-direction multi-round snowball walk to saturation (deterministic)",
        "args": {
            "seed": screen_path,
            "out_dir": str(review_dir),
            "backstop_waves": get_saturation_backstop_waves(config=config),
            "project": project,
        },
        "produces": {"_corpus_raw.md": corpus_raw_path, "_saturation.md": saturation_path},
        "needs": [
            {
                "from": "review-screen",
                "edge": "afterok",
                "watch": f"artifact:{screen_path}+fresh",
            }
        ],
    })

    # 6. review-curate — thin AGENT judgment layer: concept-tag the raw
    #    corpus, apply inclusion/exclusion, emit the FINAL _corpus.md (+
    #    _coverage-gaps.md on backstop-termination or a tag-under-counting
    #    concern — Option C hybrid's declared concept-tag-half caveat).
    nodes.append({
        "id": "review-curate",
        "type": "agent",
        "label": "Concept-tag + curate the raw corpus into the final _corpus.md",
        "spec": _spec("review_curate_tips"),
        "reads": [
            _rel("literature"),
            _rel("concepts"),
            _rel("mocs"),
            protocol_path,
            corpus_raw_path,
            saturation_path,
        ],
        "produces": {"_corpus.md": corpus_path},
        "needs": [
            {
                "from": "review-snowball",
                "edge": "afterok",
                "watch": f"artifact:{saturation_path}+fresh",
            }
        ],
    })

    # 7. coverage-gate — human-go Phase BOUNDARY (§5L.4)
    #    Operator confirms "these are the papers" before N parallel relates dispatch.
    #    On approval → rv review expand emits Phase-2.
    nodes.append({
        "id": "coverage-gate",
        "type": "human-go",
        "label": (
            "Gate 2: Approve discovered corpus (_corpus.md + _saturation.md); "
            "assert every [NEW] citekey has a relate slot or is recorded MENTION-ONLY. "
            "assert coverage via `rv review <project> coverage <scope>` — do not eyeball. "
            "Then run: rv review <project> expand <scope> to emit Phase-2."
        ),
        "needs": [_afterok("review-curate")],
    })

    manifest: dict[str, Any] = {
        "run_id": f"review-{scope_id}-phase1",
        "project": project,          # BLOCK-2 fix: explicit field → build_brief uses it
        "name": (
            f"Lit-review Phase-1 ({scope_id}): "
            f"{question[:60]}{'...' if len(question) > 60 else ''}"
        ),
        "global_cap": 1,  # Phase-1 is sequential by design
        "nodes": nodes,
    }
    return manifest


# ---------------------------------------------------------------------------
# Phase-2 DAG manifest builder
# ---------------------------------------------------------------------------

def _count_corpus_data_rows(text: str) -> int:
    """Count annotation-bearing table rows in a _corpus.md text.

    A data row is a markdown table row whose first non-empty column starts with
    ``[`` — i.e. it carries an annotation like ``[NEW]`` or ``[IN-CORPUS:...]``.
    Header rows (e.g. ``| Annotation | Citekey | Title |``) and separator rows
    (``| --- | --- | --- |``) are excluded by this definition.

    Used by cmd_expand to detect a rows-present-but-none-parseable mismatch.

    sr: SR-LR-1
    """
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        # Split on pipes and get non-empty columns
        cols = [c.strip() for c in stripped.split("|") if c.strip()]
        if not cols:
            continue
        annotation_col = cols[0]
        # Data rows start their annotation column with [
        if annotation_col.startswith("["):
            count += 1
    return count


def _parse_new_citekeys(corpus_path: Path) -> list[str]:
    """Parse _corpus.md and return citekeys annotated [NEW] (not [IN-CORPUS:*]).

    The corpus table format (written by review-snowball):
      | [NEW] | citekey | title |
      | [IN-CORPUS:old2019] | old2019 | ... |

    Only rows with annotation exactly ``[NEW]`` (case-insensitive; tolerates
    extra whitespace and table-pipe variants) are returned as citekeys for the
    Phase-2 fan-out.  ``[IN-CORPUS:*]`` rows are deliberately excluded — do NOT
    widen this to include them.

    sr: SR-LR-1
    """
    text = corpus_path.read_text(encoding="utf-8")
    return _parse_new_citekeys_from_text(text)


def _parse_new_citekeys_from_text(text: str) -> list[str]:
    """Parse a _corpus.md text and return citekeys annotated [NEW].

    Tolerates: extra whitespace in columns, mixed case ``[new]``/``[NEW]``,
    leading/trailing spaces around the pipe delimiters, and varying column count.

    Strict: only ``[NEW]`` (or case variant) passes — ``[IN-CORPUS:*]`` is excluded.

    sr: SR-LR-1
    """
    citekeys: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        # Split on pipes, strip each column, skip empty
        cols = [c.strip() for c in stripped.split("|") if c.strip()]
        if len(cols) < 2:
            continue
        annotation = cols[0]
        # Case-insensitive exact match on [NEW]
        if annotation.upper() == "[NEW]":
            citekey = cols[1]
            # Validate: citekey must look like a valid identifier
            if re.match(r"^[A-Za-z0-9_:\-\.]+$", citekey):
                citekeys.append(citekey)
    return citekeys


def _build_phase2_manifest(
    project: str,
    scope_id: str,
    new_citekeys: list[str],
    project_notes_dir: Path,
    review_dir: Path,
    *,
    tip_override: dict[str, str] | None = None,
    config: Any = None,
) -> dict[str, Any]:
    """Build the Phase-2 DAG manifest (§5L.4 two-phase fan-out ruling).

    Phase-2 nodes:
      relate-<key1> ─┐
      relate-<key2> ─┤ → review-synthesize → review-coverage-critic → approve-review (auto-resolved)
      ...            ─┘

    Each relate-<key> is a static node over the frozen corpus approved at coverage-gate.
    The parallelism cut is (a) two-phase parallel fan-out (D-LR-3 recommended option).

    The coverage-critic (§5L.5) is a REJECTS-ONLY agent: [PASS]/[BLOCK] convention.
    It enforces L-2 (counter-position): [BLOCK] on missing/empty counter-position
    OR corpus that ignored the declared opposing sub-literature.

    sr: SR-LR-1
    """
    tips = get_review_tips(config=config)
    preamble = get_review_style_preamble(config=config)

    def _spec(key: str) -> str:
        tip = tips.get(key, f"Execute the {key} step.")
        return preamble.rstrip() + "\n\n---\n\n" + tip

    def _afterok(from_id: str) -> dict[str, Any]:
        return {"from": from_id, "edge": "afterok"}

    # Absolute OKF type-dir pointers (Fix #34 — same as Phase-1; see comment there)
    def _rel(okf_type: str) -> str:
        return str(project_notes_dir / okf_type)

    protocol_path = str(review_dir / "_protocol.md")

    nodes: list[dict[str, Any]] = []

    # One relate-<key> node per [NEW] citekey from the approved corpus
    relate_ids: list[str] = []
    for citekey in new_citekeys:
        node_id = f"relate-{citekey}"
        relate_ids.append(node_id)
        lit_note_path = str(
            (project_notes_dir / "literature" / f"{citekey}.md")
        )
        nodes.append({
            "id": node_id,
            "type": "agent",
            "label": f"Relate {citekey}: distill into literature/ OKF note + verified edges",
            "spec": _spec("per_paper_relate_tips"),
            "reads": [
                _rel("literature"),
                _rel("concepts"),
                _rel("mocs"),
            ],
            "produces": {"note": f"literature/{citekey}.md"},
            "needs": [],  # all parallel; no upstream within Phase-2
        })

    # review-synthesize — joins all relate- nodes
    synthesize_needs = [_afterok(rid) for rid in relate_ids] if relate_ids else []
    nodes.append({
        "id": "review-synthesize",
        "type": "agent",
        "label": "Synthesize corpus: update concepts/ + mocs/ (soft-coupled; orphans flagged)",
        "spec": _spec("review_synthesize_tips"),
        "reads": [
            _rel("literature"),
            _rel("concepts"),
            _rel("mocs"),
            protocol_path,
        ],
        "needs": synthesize_needs,
    })

    # review-coverage-critic — rejects-only, reviewer role (§5L.5)
    # Judges: saturation-real vs premature, orphan concepts, protocol-adherence,
    # AND L-2 counter-position PRESENT and SOUGHT (hard [BLOCK] if absent or ignored).
    nodes.append({
        "id": "review-coverage-critic",
        "type": "agent",
        "label": (
            "Coverage critic (rejects-only): plateau-reality + protocol-adherence + "
            "counter-position present-and-sought (L-2) → [PASS]/[BLOCK]"
        ),
        "spec": _spec("review_critic_tips"),
        "reads": [
            _rel("literature"),
            _rel("concepts"),
            _rel("mocs"),
            str(review_dir / "_saturation.md"),
            protocol_path,
        ],
        # Single-human-gate design (2026-07-09): approve-review reads this
        # note structurally (review.check_coverage_critic_verdict) to
        # auto-resolve Gate 3 — the critic MUST write its [PASS]/[BLOCK]
        # verdict here, not just reply in prose.
        "produces": {"_coverage-critic.md": str(review_dir / "_coverage-critic.md")},
        "needs": [_afterok("review-synthesize")],
    })

    # approve-review — terminal gate, resolved AUTONOMOUSLY (single-human-
    # gate design, 2026-07-09: only approve-protocol is a human gate). The
    # DAG node "type" stays "human-go" (the schema/runner shape is unchanged;
    # see dag/catalog.py's grounding test) but `rv dag approve --auto` /
    # the self-advancing runner resolve it via review.autonomy's
    # gate-policy engine reading review-coverage-critic's verdict — never a
    # human keypress.
    nodes.append({
        "id": "approve-review",
        "type": "human-go",
        "label": "Gate 3: Approve review — [BLOCK] count + counter-position verdict",
        "needs": [_afterok("review-coverage-critic")],
    })

    # Handle empty corpus gracefully (no [NEW] papers → direct path to synthesize)
    manifest: dict[str, Any] = {
        "run_id": f"review-{scope_id}-phase2",
        "project": project,          # BLOCK-2 fix: explicit field → build_brief uses it
        "name": (
            f"Lit-review Phase-2 ({scope_id}): "
            f"{len(new_citekeys)} paper(s) → relate fan-out + synthesize + critic"
        ),
        "global_cap": 4,  # relate- nodes run in parallel (D-LR-3a)
        "nodes": nodes,
    }
    return manifest


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cmd_new(
    project: str,
    scope_id: str,
    *,
    question: str,
    config: Config | None = None,
    tip_override: dict[str, str] | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Scaffold a review OKF note + reviews/<scope>/ dir + Phase-1 DAG manifest.

    When to use: use ``rv review new <project> <scope> --question '...'`` to
    start a pre-registered, saturation-gated literature review.

    This is the ONLY path that creates the protocol-freeze + saturation-curve +
    coverage-critic framework. A hand-run literature scan gets no ``_protocol.md``
    freeze, no saturation curve, and no rejects-only critic.

    Anti-pattern: do NOT hand-collect papers and hand-write a literature section —
    run ``rv review new`` so every paper traces to the corpus index, the saturation
    is measured, and the coverage gate is structural.

    Args:
        project:     project slug (must be registered in config).
        scope_id:    review identifier slug (e.g. 'scope-llm-eval').
        question:    the review research question (frozen in _protocol.md).
        config:      optional Config (loaded if None).
        tip_override: optional per-key tip override (testing / venue customization).

    Returns:
        (note_path, review_dir, manifest) where:
          note_path:  path to the OKF review note (reviews/<scope>.md)
          review_dir: path to the review artifact directory (reviews/<scope>/)
          manifest:   the Phase-1 DAG manifest dict (also saved as phase1-dag.json)

    sr: SR-LR-1
    """
    cfg = config or load_config()
    project_notes_dir = cfg.project_notes_dir(project)

    # Ensure OKF type dirs exist (idempotent)
    scaffold_okf_dirs(project_notes_dir)

    # Create the review artifact directory
    review_dir = _review_artifact_dir(project, scope_id, cfg)
    review_dir.mkdir(parents=True, exist_ok=True)

    # Write the OKF review note (type: "literature" note repurposed as a
    # review pointer — the review itself lives in reviews/<scope>/)
    note_dir = _review_note_dir(project, cfg)
    note_dir.mkdir(parents=True, exist_ok=True)

    note_path = note_dir / f"{scope_id}.md"
    if note_path.exists():
        note_path = note_dir / f"{scope_id}-{_today()}.md"

    fields: dict[str, str] = {
        "type": "literature",  # closest OKF type for a review pointer note
        "title": f"Review: {question[:100]}",
        "created": _today(),
        "review_scope": scope_id,
        "review_question": question,
        "review_dir": str(review_dir),
        "dag_run": f"review-{scope_id}-phase1",
    }

    body = (
        "\n"
        "<!-- Literature review pointer note (SR-LR-1) -->\n"
        "<!-- Use `rv review new <project> <scope> --question '...'` for creation. -->\n"
        "<!-- The review artifacts live in reviews/<scope>/: -->\n"
        "<!--   _protocol.md  — frozen search protocol (pre-registration) -->\n"
        "<!--   _corpus.md    — discovered [NEW] citekey list (snowball output) -->\n"
        "<!--   _saturation.md — saturation curve (rounds × new citekeys) -->\n"
        "<!-- Drive Phase-1 with: rv dag run reviews/<scope>/phase1-dag.json -->\n"
        "<!-- After coverage-gate: rv review <project> expand <scope> → Phase-2 -->\n"
        "\n"
        "## Review question\n\n"
        f"<!-- {question} -->\n\n"
        "## Protocol\n\n"
        f"<!-- Pre-registration: {review_dir}/_protocol.md -->\n\n"
        "## Phase-2 manifest\n\n"
        "<!-- Emitted by rv review expand after coverage-gate approval. -->\n"
        "<!-- phase2-dag.json path set here after expand runs. -->\n"
    )

    note_path.write_text(_render_frontmatter(fields) + "\n" + body, encoding="utf-8")

    # Build and save the Phase-1 manifest
    manifest = _build_phase1_manifest(
        project=project,
        scope_id=scope_id,
        question=question,
        review_dir=review_dir,
        project_notes_dir=project_notes_dir,
        tip_override=tip_override,
        config=cfg,
    )

    manifest_path = review_dir / "phase1-dag.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return note_path, review_dir, manifest


def cmd_list(
    project: str,
    *,
    config: Config | None = None,
) -> list[dict[str, Any]]:
    """List review notes for the given project.

    When to use: ``rv review list <project>`` to enumerate all staged reviews.

    Returns:
        List of dicts with keys: scope, question, review_dir, dag_run, path.
        Empty list when no review notes exist.

    sr: SR-LR-1
    """
    cfg = config or load_config()
    note_dir = _review_note_dir(project, cfg)
    if not note_dir.exists():
        return []

    results: list[dict[str, Any]] = []
    for p in sorted(note_dir.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        fields, _ = _parse_frontmatter(text)
        # Only review pointer notes (have review_scope frontmatter field)
        scope = fields.get("review_scope")
        if scope:
            results.append({
                "scope": scope,
                "question": fields.get("review_question", ""),
                "review_dir": fields.get("review_dir", ""),
                "dag_run": fields.get("dag_run", ""),
                "path": p,
                "fields": fields,
            })
    return results


def cmd_expand(
    project: str,
    scope_id: str,
    *,
    corpus_path: Path | None = None,
    config: Config | None = None,
    tip_override: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Emit the Phase-2 DAG manifest from the frozen _corpus.md.

    Run after the ``coverage-gate`` human-go approval.  Parses ``_corpus.md`` for
    ``[NEW]`` citekeys and emits one ``relate-<key>`` node per paper, joining into
    ``review-synthesize → review-coverage-critic → approve-review (auto-resolved)``.

    When to use: ``rv review <project> expand <scope>`` immediately after the
    operator approves the coverage-gate.  The Phase-2 manifest is saved as
    ``reviews/<scope>/phase2-dag.json``.

    Anti-pattern: do NOT hand-write a Phase-2 manifest — the ``[NEW]`` citekeys
    come from the snowball output; hand-writing would miss papers or mis-annotate them.

    Args:
        project:     project slug.
        scope_id:    review scope identifier (same as passed to cmd_new).
        corpus_path: path to ``_corpus.md`` (default: reviews/<scope>/_corpus.md).
        config:      optional Config (loaded if None).
        tip_override: optional per-key tip override.

    Returns:
        The Phase-2 manifest dict (also saved as phase2-dag.json).

    sr: SR-LR-1
    """
    cfg = config or load_config()
    project_notes_dir = cfg.project_notes_dir(project)
    review_dir = _review_artifact_dir(project, scope_id, cfg)

    if corpus_path is None:
        corpus_path = review_dir / "_corpus.md"

    if not corpus_path.exists():
        raise FileNotFoundError(
            f"rv review expand: _corpus.md not found at {corpus_path}. "
            f"Run the Phase-1 review-snowball node first, then approve coverage-gate."
        )

    corpus_text = corpus_path.read_text(encoding="utf-8")
    total_data_rows = _count_corpus_data_rows(corpus_text)
    new_citekeys = _parse_new_citekeys_from_text(corpus_text)

    # F15: green-but-vacuous guard — if the corpus has annotation rows but none
    # parsed as [NEW], there is a format mismatch.  Do NOT write a 0-relate
    # phase2-dag.json; raise loud so the operator can fix the corpus format.
    # A truly empty corpus (0 annotation rows) still degrades gracefully (direct
    # path to synthesize with no relate nodes — existing behavior preserved).
    if total_data_rows > 0 and not new_citekeys:
        raise ValueError(
            f"rv review expand: _corpus.md has {total_data_rows} annotation row(s) "
            f"at {corpus_path} but none parsed as [NEW] citekeys.\n"
            f"Expected row format:  | [NEW] | citekey | title |\n"
            f"Rows annotated [IN-CORPUS:*] are excluded from the Phase-2 fan-out by design.\n"
            f"If all papers are already in-corpus, the corpus is fully covered — "
            f"run review-synthesize directly rather than expanding."
        )

    manifest = _build_phase2_manifest(
        project=project,
        scope_id=scope_id,
        new_citekeys=new_citekeys,
        project_notes_dir=project_notes_dir,
        review_dir=review_dir,
        tip_override=tip_override,
        config=cfg,
    )

    # Ensure review_dir exists (cmd_expand may be called without a prior cmd_new
    # in tests that provide corpus_path directly)
    review_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = review_dir / "phase2-dag.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # F16+F17: one-line coverage summary so the operator sees state immediately
    # (non-fatal — if _corpus.md is absent or literature/ doesn't exist yet,
    # we just print zeros rather than failing the expand step)
    import sys as _sys
    try:
        cov = coverage_report(project, scope_id, config=cfg)
        c = cov["counts"]
        _sys.stdout.write(
            f"rv review expand: coverage — "
            f"{c['materialized']}/{c['corpus']} materialized, "
            f"{c['unmaterialized']} unmaterialized, "
            f"{c['orphan']} orphan. "
            f"Run `rv review {project} coverage {scope_id}` for the full report.\n"
        )
    except Exception:
        pass  # coverage summary is advisory only; never block the expand

    return manifest
