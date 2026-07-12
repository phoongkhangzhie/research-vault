# SPDX-License-Identifier: AGPL-3.0-or-later
"""manuscript/check_gates.py — the INTEGRATION PR: assemble the manuscript-loop
gates the parallel wave (M2/M3/M4/M6) built but never wired together.

``build_approve_payload`` is the single entry point ``rv dag approve`` calls at
the ``approve-manuscript`` node (mirrors ``check_framework_gate``'s wiring at
``approve-framework``) — and the future per-round review-revise
re-fire is designed to import THIS function rather than duplicate the gate
assembly (single-sourced, per the integration-PR brief).

Assembles the four gates by HONESTY-CLASS, per the operator's LOCKED
judge-guard policy (the resolved call carried in the dispatching brief — see
``manuscript/equations.py``'s D-MS-2 note for the parallel precedent: an
explicit operator override on a design doc's own recommendation is followed,
and the divergence is documented, not silently applied):

  - ``check_citation_resolve`` (``manuscript/bib.py``)     -> hard BLOCK,
    deterministic, ALWAYS runs (no judge dependency at all).
  - ``check_equation_fidelity`` (``manuscript/equations.py``) -> SIGNAL
    ONLY (D-MS-2 — never BLOCK, even marked-critical). Deterministic; ALWAYS
    runs (no judge dependency — the LLM-judge fallback inside the gate itself
    is a separate, optional refinement not wired here).
  - ``check_support_tally`` (``manuscript/fidelity_gates.py``) -> BLOCK
    on ``[ABSENT]``/``[CONTRADICTS]`` (the citation-fidelity FLOOR) — BEHIND
    the judge guard. Support-matcher is the ONE judge-gated LLM check now
    (the former ``check_cold_read_tally`` self-containment critic was
    removed — SIGNAL-only, non-actionable under hands-off autonomy,
    redundant with the review board's coherence axis + RD-6's hard
    term-definition gate. The operator's call; see DEVLOG).

**The judge guard** (design doctrine: ``honesty-gates.md`` fail-closed
discipline, applied honestly in the OTHER direction here). The env-var
half of the guard was DELETED — the inline LLM gate runs ONLY when an
explicit ``judge_fn`` is injected (tests). In PRODUCTION ``judge_fn`` is
always None, so the gate routes to the cold-agent-judge emit/ingest fan-out
(the ``_cold_fanout_dirs_present`` branch — the only production judge path)
or, when nothing was emitted, lands in the payload's ``not_run`` list with a
LOUD message surfaced at the human-go (charter §2: surface, never silently
drop; never green-and-empty). This is NOT a hard block on the deterministic
gates: a manuscript with no fan-out emitted can still reach
``approve-manuscript`` on the deterministic bib gate alone, but the human
sees, unmistakably, that the citation-fidelity floor was never checked.

The coverage gate (gate-4) LANDS HERE — deterministic,
ALWAYS runs, hard BLOCK. ``check_coverage_gate`` re-derives the frozen
corpus's citekey set from ``reviews/<slug>/_corpus.md`` (the same
``review._parse_corpus_citekeys`` source-of-truth ``review.coverage_report()``
uses) and BLOCKs on either (a) the stamped ``corpus_hash`` no longer matching
the frozen ``_corpus.md`` bytes (the corpus mutated since the Phase-1 freeze
— the stale-corpus guard, .5), or (b) the draft's own rendered
PRISMA-ledger corpus count reading SMALLER than the true frozen corpus count
(a revise that narrows scope to shrink the denominator, gate-4's
literal example). A manuscript with no ``corpus_hash`` stamped yet (no
frozen corpus to check against — a type with no Phase-1, or a lit-review
whose framework isn't approved yet) is a correct, honest no-op — never a
BLOCK for absence, mirroring the ``doi``/``arxiv_id`` precedent elsewhere.

Doctrine: data/doctrine/honesty-gates.md.

Stdlib only. Hermetic in tests — the judge guard means a bare call with no
env vars and no judge_fn never reaches out to a live LLM.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from research_vault.manuscript.bib import check_citation_resolve
from research_vault.manuscript import equations as _equations
from research_vault.manuscript import fidelity_gates as _fidelity_gates
from research_vault.note import _parse_frontmatter as _pfm_gates
from research_vault.review import _parse_corpus_citekeys


# ---------------------------------------------------------------------------
# Judge-guard predicate
# ---------------------------------------------------------------------------

def _judge_configured(judge_fn: Callable[[str], str] | None) -> bool:
    """True iff an explicit ``judge_fn`` was injected (the test seam).

    the env-var half (a judge-model + API-key read) was DELETED — no rv
    code reads an env var to decide a judge is "configured" for a live API
    call. In production ``judge_fn`` is always None and this returns False, so
    ``build_approve_payload`` routes to the cold-agent-judge emit/ingest
    fan-out (the ``_cold_fanout_dirs_present`` branch) or the ``not_run``
    HALT — never an in-process API judge. An injected ``judge_fn`` (tests
    only) exercises the inline ``check_support_tally`` branch hermetically.
    """
    return judge_fn is not None


# ---------------------------------------------------------------------------
# Draft-text assembly (shared by the equation gate — the whole draft, not
# just one section — the same report.md+sections resolution pattern used
# elsewhere in this loop).
# ---------------------------------------------------------------------------

def _read_draft_text(tree_root: Path) -> str:
    """Join every draft file (``_report.md`` + ``sections/*.md`` — see
    ``draft_files.py``) into one draft-text blob.

    this is always the internal ``[[citekey]]`` SOURCE
    (``draft_files.resolve_draft_files``), never rendered
    reader-facing ``report.md`` — every gate that calls this (equation
    fidelity, citation-resolve via ``bib.py``, reader-hygiene, the board's
    ``coverage_diff``) reads the SOURCE, which is a strict superset of the
    render's leak surface.

    Best-effort, never raises: an unreadable/missing file simply contributes
    nothing (a fresh manuscript folder with no draft yet -> empty string,
    which every gate treats as "nothing to check yet", never an error).
    """
    from research_vault.manuscript.draft_files import resolve_draft_files

    parts: list[str] = []
    for draft_file in resolve_draft_files(tree_root):
        try:
            parts.append(draft_file.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# check_reader_hygiene — RD-5, next-gen lit-review (deterministic,
# ALWAYS runs, hard BLOCK, no judge dependency — the presentation program's
# most transferable HR mechanic, rv's biggest packaging gap before this PR).
# ---------------------------------------------------------------------------

# Internal pipeline-vocabulary handles that must never leak into reader prose.
# \bCP\d+\b / \bQ\d+\b require a digit immediately after the letter(s) so
# ordinary prose ("Q&A", "CParser", "Quarter") never false-positives — only
# the exact counter-position/question handle shape trips this.
_LEAK_CP_HANDLE_RE = re.compile(r"\bCP\d+\b")
_LEAK_Q_HANDLE_RE = re.compile(r"\bQ\d+\b")
_LEAK_SHA256_RE = re.compile(r"\bsha256:[0-9a-fA-F]+\b")
# Loop/control-artifact filenames (the review/manuscript control notes) —
# never a reader-facing citation; a real citekey never starts with '_'.
_LEAK_ARTIFACT_FILENAME_RE = re.compile(r"\b_[a-z][a-z-]*\.md\b")
# Literal epistemic edge type markers (review/relate paper<->paper and
# paper<->concept edges, e.g. `[SUPPORTS] concepts/x.md — reason` (an
# earlier link-prefix-tag convention) or `[x](/literature/y.md) — SUPPORTS:
# reason` (the current OKF-conformant prose-token convention)) — these are
# OKF-internal relation vocabulary, never reader-facing prose (a prior audit
# missed this class in a downstream project's manuscript). Both the earlier
# bracket-prefix shape AND the current trailing `TYPE:` prose-token shape
# are matched — a leaked edge line from either note vintage must still trip
# this gate.
_LEAK_EDGE_TAG_RE = re.compile(
    r"\[(?:SUPPORTS|CONTRADICTS|PARTIAL|EXTENDS)\]"
    r"|\b(?:SUPPORTS|CONTRADICTS|PARTIAL|EXTENDS):"
)
# OKF note-path fragments (a path SEGMENT shape, `word/...` — NOT the bare
# word) — the bare words "literature"/"concepts"/"reviews"/"gaps"/"mocs" are
# ordinary English and must never trip this; only the literal path-fragment
# shape (the word immediately followed by a slash and more path characters)
# does. Distinct from _LEAK_ARTIFACT_FILENAME_RE, which only matches
# underscore-prefixed control filenames, not OKF note-tree paths (
# missed class 2/3).
_LEAK_OKF_PATH_RE = re.compile(
    r"\b(?:concepts|literature|mocs|gaps|reviews)/[\w.\-]+"
)
# The literal reader-facing label "source notes:" — an internal
# provenance/control heading, never reader prose (missed class 3/3).
_LEAK_SOURCE_NOTES_LABEL_RE = re.compile(r"(?i)\bsource notes:")
# Tool/verb/node-vocabulary tokens leaking loop internals into reader prose.
_LEAK_TOOL_TOKENS: tuple[str, ...] = (
    "rv research",
    "rv review",
    "rv manuscript",
    "rv dag",
    "review-snowball",
    "review-search",
    "review-synthesize",
    "review-coverage-critic",
    "coverage-gate",
    "coverage-critic",
    "approve-protocol",
    "approve-framework",
    "approve-manuscript",
)


def check_reader_hygiene(reader_body: str) -> dict[str, Any]:
    """The reader-hygiene leak-gate (RD-5) — BLOCK on pipeline vocabulary
    leaking into reader-facing prose.

    When to use: run over the ASSEMBLED reader body (the joined, rendered
    survey text a reader will actually see — never the internal control
    artifacts like ``_framework-candidates.md``/``_walk.md``, which are
    ALLOWED to carry these handles). Fail-closed, rv-style: any hit BLOCKs
    declare-final; a clean body passes with zero errors.

    Deterministic and independent of every other gate — no judge, no network,
    no dependency on markdown vs. tex render target. Every hit is surfaced
    (never truncated to the first match, charter §2 — a `.strip()`/`[:1]`
    shortcut here would silently hide every leak after the first).

    Args:
        reader_body: the assembled reader-facing text to scan.

    Returns:
        {"ok": bool, "errors": list[str]} — ok is False iff errors is non-empty.

    """
    errors: list[str] = []

    for m in _LEAK_CP_HANDLE_RE.finditer(reader_body):
        errors.append(
            f"reader-hygiene BLOCK: counter-position handle {m.group(0)!r} leaked "
            f"into reader prose — name the counter-position inline (RD-6), never "
            f"by its internal handle."
        )
    for m in _LEAK_Q_HANDLE_RE.finditer(reader_body):
        errors.append(
            f"reader-hygiene BLOCK: internal question handle {m.group(0)!r} leaked "
            f"into reader prose — this is a loop-control artifact, never reader-facing."
        )
    for m in _LEAK_SHA256_RE.finditer(reader_body):
        errors.append(
            f"reader-hygiene BLOCK: a corpus hash {m.group(0)!r} leaked into reader "
            f"prose — route hashes to the control note / DEVLOG (RD-3), never the "
            f"manuscript body."
        )
    for m in _LEAK_ARTIFACT_FILENAME_RE.finditer(reader_body):
        errors.append(
            f"reader-hygiene BLOCK: internal artifact filename {m.group(0)!r} leaked "
            f"into reader prose — this is a loop-control artifact name, not a citation."
        )
    for m in _LEAK_EDGE_TAG_RE.finditer(reader_body):
        errors.append(
            f"reader-hygiene BLOCK: epistemic edge tag {m.group(0)!r} leaked into "
            f"reader prose — this is OKF relation-vocabulary, not a citation; state "
            f"the relationship in reader-facing terms instead."
        )
    for m in _LEAK_OKF_PATH_RE.finditer(reader_body):
        errors.append(
            f"reader-hygiene BLOCK: OKF note-path fragment {m.group(0)!r} leaked "
            f"into reader prose — this is an internal note-tree path, not a citation."
        )
    for m in _LEAK_SOURCE_NOTES_LABEL_RE.finditer(reader_body):
        errors.append(
            f"reader-hygiene BLOCK: internal label {m.group(0)!r} leaked into "
            f"reader prose — this is a provenance/control heading, never reader text."
        )
    for token in _LEAK_TOOL_TOKENS:
        if token in reader_body:
            errors.append(
                f"reader-hygiene BLOCK: tool/loop vocabulary {token!r} leaked into "
                f"reader prose — the reader never needs to know which rv verb "
                f"produced this survey."
            )

    return {"ok": not errors, "errors": errors}


# ---------------------------------------------------------------------------
# check_heading_order — HR-craft rec 5, NG-7's structural-mirror
# H2-order diff (deterministic, ALWAYS runs, SIGNAL only — no judge dependency)
# ---------------------------------------------------------------------------

def check_heading_order(draft_text: str, expected_order: "list[str] | tuple[str, ...]") -> dict[str, Any]:
    """HR-craft rec 5: a deterministic H2-heading-order diff.

    HR's instruction-critic diffs the draft's ordered H2 list element-wise
    against a frozen heading contract; NG-7's single-pass outline already
    freezes a reading-order spine (``lit_review.READING_ORDER``, RD-2) — this
    is the cheap, mechanical cross-check confirming the draft actually
    delivered the frozen frame.

    SIGNAL only, never BLOCK (design table): a structural drift is
    informative — the writer may have deliberately merged/split sections —
    never a hard stop on its own.

    Headings not among ``expected_order`` (e.g. a sub-heading, a figure
    caption) are ignored — this only orders the INTERSECTION of found H2s
    against the frozen contract, never penalizes extra structure.

    Args:
        draft_text: the assembled reader body (or the whole draft blob).
        expected_order: the frozen heading contract, e.g.
            ``manuscript.types.lit_review.READING_ORDER``.

    Returns:
        {"ok": bool, "warnings": list[str]} — ok is True when the found H2
        order (filtered to the expected set) matches the expected order, or
        when fewer than 2 matching headings are found (nothing to compare).

    """
    import re

    found = re.findall(r"^\s*#{1,2}\s+(.+?)\s*$", draft_text, re.MULTILINE)
    expected_norm = [str(e).strip().lower() for e in expected_order]

    def _norm(h: str) -> str:
        return h.strip().lower().lstrip("#").strip()

    found_norm = [_norm(h) for h in found]
    filtered_found = [h for h in found_norm if any(e in h or h in e for e in expected_norm)]

    if len(filtered_found) < 2:
        return {"ok": True, "warnings": []}

    # Build the expected sub-order restricted to headings actually found.
    def _matches(found_h: str, exp: str) -> bool:
        return exp in found_h or found_h in exp

    expected_restricted = [e for e in expected_norm if any(_matches(h, e) for h in filtered_found)]

    if filtered_found == expected_restricted:
        return {"ok": True, "warnings": []}

    return {
        "ok": False,
        "warnings": [
            f"heading-order diff SIGNAL: the draft's H2 order {filtered_found!r} "
            f"does not match the frozen reading-order contract "
            f"{expected_restricted!r} — check whether this is a deliberate "
            f"merge/split or an assembly drift."
        ],
    }


# ---------------------------------------------------------------------------
# check_coverage_gate gate-4, scope (deterministic,
# ALWAYS runs, hard BLOCK — no judge dependency)
# ---------------------------------------------------------------------------

# PRISMA-ledger corpus-count line, rendered by
# manuscript.types.lit_review.render_prisma_ledger: "| Corpus (frozen
# citekeys) | N |". Parsed to detect a revise that narrows scope by
# re-stating a smaller denominator than the true frozen corpus.
_PRISMA_CORPUS_COUNT_RE = re.compile(
    r"\|\s*Corpus \(frozen citekeys\)\s*\|\s*(\d+)\s*\|"
)


def check_coverage_gate(
    project_notes_dir: Path,
    tree_root: Path,
) -> dict[str, Any]:
    """Re-run the coverage check on the revised corpus (gate-4).

    When to use: called by ``build_approve_payload`` every time it assembles
    the gate payload — including per-round re-fire (same function,
    single-sourced, never duplicated). Deterministic, no LLM, ALWAYS runs.

    Convention (shared with ``manuscript.types.lit_review._compute_corpus_hash_note``):
    a manuscript slug (``tree_root.name``) matches the ``rv review`` scope id
    whose frozen corpus lives at ``reviews/<slug>/_corpus.md``.

    BLOCKs on:
      (a) ``corpus_hash`` stamped in ``_manuscript.md`` no longer matches the
          hash of the frozen ``_corpus.md`` on disk (the corpus mutated since
          the Phase-1 freeze — the stale-corpus guard, .5), or the
          stamped hash points at a ``_corpus.md`` that no longer exists.
      (b) the draft's own rendered PRISMA-ledger corpus-count line states a
          SMALLER corpus than the true frozen corpus (a revise narrowing
          scope to shrink the denominator, gate-4's literal
          example).

    A manuscript with no ``corpus_hash`` stamped yet is a correct, honest
    no-op (nothing frozen to verify against yet — never a BLOCK for absence,
    mirroring the ``doi``/``arxiv_id`` precedent).

    Args:
        project_notes_dir: the project's OKF notes root.
        tree_root: the manuscript folder (``manuscripts/<slug>/``).

    Returns:
        ``{"ok": bool, "errors": [...], "warnings": [...]}``.

    """
    from research_vault.hashing import hash_file

    errors: list[str] = []
    warnings: list[str] = []

    manuscript_note_path = tree_root / "_manuscript.md"
    if not manuscript_note_path.exists():
        # No control note at all — nothing to check against; the hermetic
        # .bib gate already covers "manuscript folder missing" concerns.
        return {"ok": True, "errors": [], "warnings": []}

    fields, _ = _pfm_gates(manuscript_note_path.read_text(encoding="utf-8"))
    stamped_hash = str(fields.get("corpus_hash", "")).strip()
    if not stamped_hash:
        warnings.append(
            "coverage-gate: no corpus_hash stamped in _manuscript.md yet — "
            "skipping (nothing frozen to verify scope against)."
        )
        return {"ok": True, "errors": [], "warnings": warnings}

    slug = tree_root.name
    corpus_path = project_notes_dir / "reviews" / slug / "_corpus.md"
    if not corpus_path.exists():
        errors.append(
            f"coverage-gate BLOCK: corpus_hash is stamped ({stamped_hash[:16]}...) "
            f"but the frozen corpus {corpus_path} no longer exists — cannot "
            f"verify the corpus hasn't narrowed since the Phase-1 freeze."
        )
        return {"ok": False, "errors": errors, "warnings": warnings}

    current_hash = hash_file(corpus_path)
    if current_hash != stamped_hash:
        errors.append(
            f"coverage-gate BLOCK: _corpus.md has changed since the Phase-1 "
            f"freeze (stamped corpus_hash {stamped_hash[:16]}... != current "
            f"{current_hash[:16]}...) — the stale-corpus guard (design "
            f"). Re-freeze the corpus_hash deliberately if this "
            f"corpus growth/narrowing is intentional."
        )
        return {"ok": False, "errors": errors, "warnings": warnings}

    # ── (b) draft's own PRISMA count vs the true frozen corpus count ────────
    true_corpus_citekeys = _parse_corpus_citekeys(corpus_path)
    draft_text = _read_draft_text(tree_root)
    m = _PRISMA_CORPUS_COUNT_RE.search(draft_text)
    if m is not None:
        stated_count = int(m.group(1))
        true_count = len(true_corpus_citekeys)
        if stated_count < true_count:
            errors.append(
                f"coverage-gate BLOCK: the draft's PRISMA ledger states "
                f"{stated_count} corpus citekeys but the frozen corpus has "
                f"{true_count} — a revise appears to have narrowed scope to "
                f"shrink the denominator (gate-4)."
            )
            return {"ok": False, "errors": errors, "warnings": warnings}

    return {"ok": True, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# check_coverage_allocation_gate: the full-corpus coverage CONTRACT,
# enforced at the framework stage BEFORE any section is drafted
# (deterministic, ALWAYS runs, hard BLOCK — no judge dependency).
# ---------------------------------------------------------------------------
#
# The verified 0.3.0 drop mechanism (the core pre-publish blocker): a real
# corpus (47 papers) routes past the single-pass ceiling to the lossy per-branch
# fallback, and NO gate ever blocked an *unallocated* paper — ~20/47 papers
# silently vanished from the manuscript. This gate closes that hole at the
# earliest possible point: the framework stage. `_coverage-map.md` (produced by
# `framework-synthesize`, see lit_review.render_synthesize_brief) must allocate
# EVERY frozen-corpus citekey into exactly one of three buckets —
#   - `used`:      cited in a named branch of the frozen spine,
#   - `clustered`: folded into a named group with a stated reason,
#   - `deferred`:  explicitly out of scope, with a stated reason.
# The contract is SURJECTIVE coverage (allocated AT LEAST once), not a
# bijective partition — a citekey allocated to none of them (or a bucket entry
# missing its required reason/branch/group, or an entry naming a citekey
# absent from the corpus) is a fail-closed BLOCK at `approve-framework`. A
# citekey allocated to MORE than one bucket is legitimate (a cross-cutting
# paper load-bearing in multiple branches, e.g. spanning two pipeline stages,
# or both a method-family member and a tension exemplar) — the gate never
# blocks on multi-allocation, only on zero-allocation. Allocation is machine-checkable in
# the note's frontmatter (the D8 mapping-list format `note._parse_frontmatter`
# already reads — charter §6, no new grammar); fuller narrative rationale lives
# in the note's prose body (not read by this gate).
#
# Design decision: the requirement is "machine-checkable allocation in
# frontmatter, reasons in prose." The reason
# for a clustered/deferred entry MUST be machine-checkable for the gate to
# fail-closed on its absence, so a short reason rides in the frontmatter
# mapping-list record (`reason:`), where this gate reads it; the note's prose
# body carries the fuller human-facing rationale. "Reasons in prose" is honored
# as "expanded rationale in prose"; the machine-checked reason is the structured
# frontmatter field.

# The three allocation buckets + the fields each REQUIRES (beyond `citekey`).
_COVERAGE_BUCKET_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "used": ("branch",),
    "clustered": ("group", "reason"),
    "deferred": ("reason",),
}


def _coverage_records(raw: Any) -> tuple[list[dict[str, str]], list[str]]:
    """Split a parsed frontmatter bucket value into (dict records, malformed).

    ``note._parse_frontmatter`` returns a list of dicts for a D8 mapping-list,
    a list of str for a plain scalar-list, or ``""`` for an absent/empty key.
    A bucket item that is NOT a ``key: value`` mapping (a bare scalar) is
    malformed — surfaced, never silently coerced (charter §2).
    """
    if not raw or isinstance(raw, str):
        return [], []
    records: list[dict[str, str]] = []
    malformed: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            records.append(item)
        else:
            malformed.append(str(item))
    return records, malformed


def check_coverage_allocation_gate(
    corpus_path: Path,
    coverage_map_path: Path,
) -> dict[str, Any]:
    """The full-corpus coverage-allocation contract — deterministic,
    fail-closed, no judge dependency.

    When to use: folded into ``approve-framework``'s autonomous evaluation
    (``dag/verbs.py::_evaluate_autonomous_gate``, most-severe-wins with the
    framework-critic verdict) so a lit-review can NEVER reach the drafting
    phase with an unallocated corpus. Also usable standalone.

    BLOCKs on any of:
      - a frozen-corpus citekey that is UNALLOCATED (in none of used /
        clustered / deferred) — the silent-drop hole this gate closes;
      - a ``clustered``/``deferred`` entry with no non-empty ``reason`` (and a
        ``clustered`` entry with no non-empty ``group``); a ``used`` entry with
        no non-empty ``branch`` (an allocation with no anchor is unverifiable);
      - a ledger entry naming a citekey ABSENT from the frozen corpus (a
        non-corpus / phantom citekey);
      - a malformed bucket entry (a bare scalar, not a ``citekey: ...`` record).

    The contract is surjective coverage (every corpus paper allocated to AT
    LEAST one bucket), not a bijective partition — a citekey MAY appear in
    multiple buckets (cross-cutting: e.g. load-bearing in two pipeline
    stages, or both a method-family member and a tension exemplar). The gate
    never blocks on multi-allocation, only on zero-allocation.

    Honest no-op (never a BLOCK for absence, mirroring the ``doi``/``corpus_hash``
    precedent): if no frozen ``_corpus.md`` exists yet, or it has zero citekeys,
    there is nothing to allocate — returns ok with no errors. But once a real
    corpus exists, a MISSING ``_coverage-map.md`` is a hard BLOCK (a real corpus
    with no allocation map is exactly the drop condition).

    A ``CorpusSchemaError`` (a malformed corpus row) is caught and surfaced as a
    fail-closed BLOCK — never an uncaught crash of the gate.

    Args:
        corpus_path: the frozen ``reviews/<slug>/_corpus.md``.
        coverage_map_path: the manuscript's ``_coverage-map.md``.

    Returns:
        ``{"ok": bool, "errors": list[str]}`` — ok is False iff errors is
        non-empty.

    """
    from research_vault.review import CorpusSchemaError

    errors: list[str] = []

    # ── The frozen-corpus key set (the source of truth). ────────────────────
    try:
        corpus_citekeys = _parse_corpus_citekeys(corpus_path) if corpus_path.exists() else []
    except CorpusSchemaError as e:
        return {
            "ok": False,
            "errors": [
                f"coverage-allocation BLOCK: the frozen corpus {corpus_path} has "
                f"a malformed row and cannot be read — {e} (fail-closed; fix the "
                f"corpus row schema before the coverage map can be verified)."
            ],
        }

    if not corpus_citekeys:
        # Nothing frozen to allocate yet — honest no-op.
        return {"ok": True, "errors": []}

    corpus_set = set(corpus_citekeys)

    if not coverage_map_path.exists():
        return {
            "ok": False,
            "errors": [
                f"coverage-allocation BLOCK: the frozen corpus has "
                f"{len(corpus_set)} citekeys but no _coverage-map.md exists at "
                f"{coverage_map_path} — every corpus paper must be allocated to a "
                f"branch (used), a named group (clustered), or explicitly deferred "
                f"BEFORE any section is drafted. framework-synthesize must produce "
                f"this ledger."
            ],
        }

    fields, _ = _pfm_gates(coverage_map_path.read_text(encoding="utf-8"))

    # ── Walk each bucket, validating fields + collecting the allocation. ────
    # `allocated` is a SET (not a dict keyed by "first bucket seen") — a
    # citekey appearing in multiple buckets is legitimate cross-cutting
    # allocation, not a contradiction; the gate only tracks whether each
    # corpus citekey was allocated AT LEAST once, never how many times.
    allocated: set[str] = set()
    for bucket, required in _COVERAGE_BUCKET_REQUIRED_FIELDS.items():
        records, malformed = _coverage_records(fields.get(bucket))
        for bad in malformed:
            errors.append(
                f"coverage-allocation BLOCK: malformed {bucket!r} entry {bad!r} — "
                f"each bucket item must be a 'citekey: <key>' mapping record with "
                f"its required fields, not a bare value."
            )
        for rec in records:
            citekey = str(rec.get("citekey", "")).strip()
            if not citekey:
                errors.append(
                    f"coverage-allocation BLOCK: a {bucket!r} entry has no non-empty "
                    f"'citekey' field ({rec!r}) — every allocation must name its paper."
                )
                continue
            for field_name in required:
                if not str(rec.get(field_name, "")).strip():
                    errors.append(
                        f"coverage-allocation BLOCK: {bucket!r} citekey {citekey!r} "
                        f"has no non-empty {field_name!r} — a {bucket} allocation "
                        f"without its {field_name} is unverifiable."
                    )
            if citekey not in corpus_set:
                errors.append(
                    f"coverage-allocation BLOCK: {bucket!r} entry names citekey "
                    f"{citekey!r}, which is NOT in the frozen corpus — a non-corpus "
                    f"(phantom) citekey in the coverage ledger."
                )
                # A phantom key is never treated as covering a real corpus
                # paper, but it's still fine for the SAME real citekey to also
                # appear (correctly) in another bucket — no dup tracking here.
            allocated.add(citekey)

    # ── The load-bearing check: every corpus citekey allocated SOMEWHERE. ───
    unallocated = sorted(corpus_set - set(allocated))
    if unallocated:
        errors.append(
            f"coverage-allocation BLOCK: {len(unallocated)} frozen-corpus citekey(s) "
            f"are UNALLOCATED — they appear in no used/clustered/deferred bucket and "
            f"would be silently dropped from the manuscript: {unallocated}. Allocate "
            f"each to a branch (used), a named group (clustered + reason), or defer it "
            f"(deferred + reason)."
        )

    return {"ok": not errors, "errors": errors}


# ---------------------------------------------------------------------------
# compute_coverage_diff: the WIDTH lens's mechanical ground truth.
# ---------------------------------------------------------------------------
#
# Mirror of ``check_heading_order``'s role for the INSTRUCT lens: a
# deterministic diff the WIDTH board judge is handed as ground truth (never
# re-derived inside the judge prompt). The board's WIDTH (Coverage) lens
# owns "full-corpus use, no ignored clusters"; this function FINDS the
# dropped paper — the set of ``used`` citekeys the coverage map committed to
# that DO NOT appear as a ``[[citekey]]`` wikilink in the assembled reader
# body — and the judge explains why the drop matters (a whole missing
# cluster vs. a single missing paper). Reuses the same coverage-map parser
# (``_coverage_records`` / ``_pfm_gates``) and the citation SSOT
# (``WIKILINK_CITE_RE``) — charter §6, no new grammar.

def compute_coverage_diff(coverage_map_path: Path, reader_body: str) -> dict[str, Any]:
    """The mechanical WIDTH ground truth: which ``used`` papers were dropped.

    Reads the ``used`` bucket of ``_coverage-map.md`` (the papers the
    framework stage committed to citing in a named branch) and diffs it
    against the citekeys actually present (as ``[[citekey]]`` wikilinks) in
    the assembled ``reader_body``.

    Returns::

        {"used":    [sorted unique 'used' citekeys from the coverage map],
         "present": [sorted 'used' citekeys that DO appear in the body],
         "missing": [sorted 'used' citekeys that DO NOT appear in the body]}

    Honest no-op: an absent/empty coverage map yields empty lists (there is
    nothing committed to drop). ``missing`` is the load-bearing field handed
    to the WIDTH judge as ``coverage_diff``.

    ★ SOURCE-ROUTING (fit-check): ``reader_body`` MUST be the
    ``[[citekey]]`` SOURCE body — the assembled ``_report.md`` + ``sections/*.md``
    from ``draft_files.resolve_draft_files`` (or ``_read_draft_text``). It must
    NEVER be ``[N]``-numbered render (``report.md``, rename of
    the former ``report.rendered.md``): that render has already converted
    every ``[[citekey]]`` to ``[N]``, so ``WIKILINK_CITE_RE`` finds ZERO
    citekeys in it — the diff would then flag EVERY used paper as "missing"
    and false-critical the entire corpus. The board-emit driver
    (``manuscript.cmd_board_emit``) assembles ``reader_body`` from the source
    draft via ``_read_draft_text``; a regression test
    (``test_coverage_diff_source_routing`` at the unit level,
    ``test_pr_d2_source_routing_driver.py`` at the driver level) pins this
    contract.

    """
    from research_vault.manuscript.citation_pattern import WIKILINK_CITE_RE

    used: list[str] = []
    if coverage_map_path.exists():
        fields, _ = _pfm_gates(coverage_map_path.read_text(encoding="utf-8"))
        records, _ = _coverage_records(fields.get("used"))
        used = [
            str(rec.get("citekey", "")).strip()
            for rec in records
            if str(rec.get("citekey", "")).strip()
        ]

    used_set = sorted(set(used))
    cited_in_body = set(WIKILINK_CITE_RE.findall(reader_body or ""))
    present = [k for k in used_set if k in cited_in_body]
    missing = [k for k in used_set if k not in cited_in_body]
    return {"used": used_set, "present": present, "missing": missing}


# ---------------------------------------------------------------------------
# _cold_fanout_dirs_present — NG-4 detector
# ---------------------------------------------------------------------------

def _cold_fanout_dirs_present(tree_root: Path) -> bool:
    """True iff a cold-agent-judge fan-out task set was ever emitted for
    this manuscript (``rv manuscript <project> judge-emit <slug>`` or
    equivalent) — i.e. ``judge/support-matcher/_judge-tasks.json`` exists
    under ``tree_root``. Support-matcher-ONLY (the cold-read gate was
    removed; see DEVLOG).

    Deliberately checks presence of the TASKS file, not the verdicts file
    — the whole point of this detector is to distinguish "a fan-out was
    attempted (verdicts may or may not have landed yet)" from "nothing was
    ever configured on the judge path" (the not_run bucket below).
    """
    judge_dir = tree_root / "judge"
    return (judge_dir / "support-matcher" / "_judge-tasks.json").exists()


# ---------------------------------------------------------------------------
# build_approve_payload — the single gate-assembly entry point
# ---------------------------------------------------------------------------

def build_approve_payload(
    tree_root: Path,
    project_notes_dir: Path,
    ms_type: Any,
    *,
    judge_fn: Callable[[str], str] | None = None,
    literature_root: Path | None = None,
) -> dict[str, Any]:
    """Assemble the manuscript-loop gates for ``approve-manuscript``.

    When to use: called by ``rv dag approve`` at the ``approve-manuscript``
    human-go node (wired in ``dag/verbs.py::cmd_approve``, mirroring
    ``check_framework_gate``'s wiring at ``approve-framework``). ★ Also the
    single-sourced import point for per-round re-fire — do NOT
    duplicate this gate-assembly logic in the review-revise board; call this.

    Args:
        tree_root: the manuscript folder (``manuscripts/<slug>/`` — the
            manifest's parent dir at tick time).
        project_notes_dir: the project's OKF notes root (``literature/``,
            ``concepts/``, etc. live directly under this).
        ms_type: the manuscript's registered ``ManuscriptType`` (for
            ``equation_sources`` — a type with none is a correct no-op for
            the equation gate).
        judge_fn: optional injectable LLM call for ``check_support_tally``
            (the ``(prompt: str) -> str`` shape it already accepts). Passing
            one counts as "judge configured" even absent env vars (test seam).
        literature_root: ``cfg.literature_root`` — the central
            two-layer store, threaded into the hermetic bib gates
            (citekey/authors/year/doi/arxiv_id are CORE-only content).
            ``None`` degrades to reading the project overlay dir directly
            (a monolithic fixture — not a violation, just a degrade path).

    Returns:
        ``{"ok": bool, "blocking": [...], "signals": [...], "not_run": [...]}``
        — ``ok`` is False iff ``blocking`` is non-empty. Every string in every
        list is prefixed with its originating gate in brackets, so a human
        (or meta-review) can triage by source at a glance.

    """
    blocking: list[str] = []
    signals: list[str] = []
    not_run: list[str] = []
    # NG-4b item 3: a support-matcher canary-abort (blind-judge probe fails)
    # must be visible to review.autonomy's gate-policy engine as a TOP-LEVEL
    # flag, not buried inside a `blocking` string. classify_disposition's
    # priority order checks `canary_aborted` BEFORE `blocking` (untrustworthy
    # signal > deterministic block) — without this flag, a canary-abort was
    # indistinguishable from an ordinary fixable BLOCK, so the gate-policy
    # engine would REVISE it (dispatch a bounded auto-revise against the SAME
    # broken judge) instead of HALT-DECLARE-ing (fail-closed, never retry an
    # untrustworthy judge — charter §10). See evaluation_from_structural_payload.
    canary_aborted = False

    # ── 1. Hermetic references.md — deterministic, ALWAYS runs, hard BLOCK
    #      ──
    bib_result = check_citation_resolve(
        project_notes_dir, tree_root, literature_root=literature_root,
    )
    if not bib_result["ok"]:
        blocking.extend(f"[hermetic-bib] {e}" for e in bib_result["errors"])

    # ── 1b. Numbered render — deterministic, ALWAYS runs,
    #      hard BLOCK. Converts `_report.md` (the `[[citekey]]` SOURCE this
    #      node's caller — the assemble node — just wrote) into the
    #      reader-facing `report.md` (`[N]` inline + `## Sources`) +
    #      `references.bib`. Fail-closed: a non-empty ``errors`` (residual
    #      `[[citekey]]`, blank/sentinel token) BLOCKs — a half-converted
    #      `report.md` is never shipped (D-4d/D-4e). Independent of
    #      ``check_citation_resolve`` above (that gate validates
    #      `references.md`; this one drives the numbered render) but both
    #      read the SAME `_report.md` source, never the render itself. ──
    from research_vault.manuscript import bib as _bib

    render_result = _bib.render_numbered_manuscript(
        project_notes_dir, tree_root, literature_root=literature_root,
    )
    if not render_result["ok"]:
        blocking.extend(f"[numbered-render] {e}" for e in render_result["errors"])

    # ── 2. Equation-fidelity — deterministic, ALWAYS runs, SIGNAL only,
    #      D-MS-2. A type with no equation_sources is a correct
    #      no-op (nothing declared to mine, never an error). ────────────────
    equation_sources = getattr(ms_type, "equation_sources", ()) or ()
    if equation_sources:
        ledger = _equations.extract_equation_ledger(
            project_notes_dir, equation_sources, literature_root=literature_root,
        )
        draft_text = _read_draft_text(tree_root)
        eq_findings = _equations.check_equation_fidelity(ledger, draft_text)
        signals.extend(f"[equation-fidelity:{f['severity']}] {f['message']}" for f in eq_findings)

    # ── 3. The LLM gate — BEHIND the judge guard. ────────────────────
    #      Support-matcher is the ONE judge-gated gate now (the former
    #      cold-read self-containment critic was removed; see DEVLOG).
    if _judge_configured(judge_fn):
        support_result = _fidelity_gates.check_support_tally(
            tree_root, notes_root=project_notes_dir, judge_fn=judge_fn,
            literature_root=literature_root,
        )
        # ``errors`` already carries the canary-abort message when
        # canary_aborted is True (fidelity_gates.py's own abort path) — a
        # blind-judge canary failure means the tally could NOT be trusted,
        # so it BLOCKs regardless (fail-closed: cannot confirm citation
        # fidelity -> cannot proceed, never silently treated as a pass).
        blocking.extend(f"[support-matcher] {e}" for e in support_result["errors"])
        if support_result.get("canary_aborted"):
            canary_aborted = True
        else:
            signals.extend(f"[support-matcher:PARTIAL] {w}" for w in support_result["warnings"])
    elif _cold_fanout_dirs_present(tree_root):
        # NG-4 (PRIMARY path): no live judge_fn/env, but an
        # orchestrator-dispatched cold-agent-judge fan-out was emitted for
        # this manuscript (``judge/support-matcher/_judge-tasks.json`` present)
        # — ingest whatever verdicts landed instead of falling into the
        # generic "not configured" not_run bucket below. A CanaryAbortError
        # here (the fan-out judge failed its planted probe) or a halt (the
        # fan-out never completed) is escalated to a hard BLOCK, not a
        # soft not_run — unlike "nothing was ever attempted," a task set
        # was emitted and something SHOULD have come back; treat that
        # gap the same way the live path treats a canary abort: cannot
        # self-certify -> cannot proceed (HALT-DECLARE
        # policy for both "untrustworthy signal" and "floor gate NOT RUN").
        from research_vault.gates.judge_seam import CanaryAbortError

        try:
            support_result = _fidelity_gates.ingest_support_verdicts_from_dir(
                tree_root / "judge" / "support-matcher", tree_root=tree_root,
            )
        except CanaryAbortError as e:
            support_result = {
                "errors": [f"CANARY ABORT (HALT-DECLARE): {e}"],
                "warnings": [], "canary_aborted": True, "halt": True,
            }
        if support_result.get("canary_aborted"):
            canary_aborted = True
            blocking.extend(f"[support-matcher] {e}" for e in support_result["errors"])
        elif support_result.get("halt"):
            # NG-4b: an incomplete/missing judge-fanout is the "floor
            # gate NOT RUN" failure class, NOT a fixable BLOCK — it belongs
            # in `not_run` (-> HALT-DECLARE, priority 2) so the gate-policy
            # engine never dispatches a bounded auto-revise against a floor
            # that never actually ran (explore-rl #3: a floor gate that
            # didn't run must never look like an ordinary fixable finding).
            not_run.extend(f"[support-matcher] {e}" for e in support_result["errors"])
            not_run.append(
                "[support-matcher] HALT-DECLARE: judge-fanout did not "
                "complete — see the error above; this manuscript cannot "
                "self-certify its citation-fidelity floor."
            )
        else:
            blocking.extend(f"[support-matcher] {e}" for e in support_result["errors"])
            signals.extend(f"[support-matcher:PARTIAL] {w}" for w in support_result.get("warnings", []))
    else:
        not_run.append(
            "support-matcher gate NOT RUN — no cold-agent-judge fan-out was "
            "emitted (no `judge/support-matcher/_judge-tasks.json` under this "
            "manuscript), and no test judge_fn was supplied. This is NOT a "
            "pass: the citation-fidelity FLOOR (support-matcher) has NOT been "
            "checked on this manuscript. Emit a judge-fanout task set "
            "(`rv manuscript judge-emit`), fan out "
            "the cold judges, and re-run `rv dag approve` before trusting "
            "this manuscript's citation fidelity. (the in-process API "
            "judge path was deleted — the fan-out is the only production path.)"
        )

    # ── 5. The coverage gate (gate-4) — deterministic, ALWAYS
    #      runs, hard BLOCK. No judge dependency at all — the
    #      integration PR deferred this into not_run; wired for real here. ──
    coverage_result = check_coverage_gate(project_notes_dir, tree_root)
    blocking.extend(f"[coverage-gate] {e}" for e in coverage_result["errors"])
    if coverage_result["warnings"]:
        not_run.extend(f"[coverage-gate] {w}" for w in coverage_result["warnings"])

    # ── 6. Reader-hygiene leak-gate (RD-5) — deterministic, ALWAYS runs,
    #      hard BLOCK. No judge dependency; independent of every other gate.
    hygiene_draft_text = _read_draft_text(tree_root)
    hygiene_result = check_reader_hygiene(hygiene_draft_text)
    blocking.extend(f"[reader-hygiene] {e}" for e in hygiene_result["errors"])

    # ── 7. Heading-order diff (HR-craft rec 5, NG-7) — deterministic, ALWAYS
    #      runs (when the type declares a frozen reading order), SIGNAL only.
    #      Only lit-review declares READING_ORDER today; a type with none is
    #      a correct no-op (never fabricated for a type that hasn't defined one).
    if getattr(ms_type, "key", "") == "lit-review":
        from research_vault.manuscript.types.lit_review import READING_ORDER

        heading_result = check_heading_order(hygiene_draft_text, READING_ORDER)
        signals.extend(f"[heading-order] {w}" for w in heading_result["warnings"])

    return {
        "ok": not blocking,
        "blocking": blocking,
        "signals": signals,
        "not_run": not_run,
        # NG-4b: top-level canary-abort flag — see comment at the top of
        # this function. Consumed by review.autonomy.evaluation_from_structural_payload.
        "canary_aborted": canary_aborted,
    }
