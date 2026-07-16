# SPDX-License-Identifier: AGPL-3.0-or-later
"""fidelity_gates.py — manuscript-loop THIN ADAPTER over the shared gates.

The hard fidelity gate re-instantiated — the claim->source
support-matcher — lives in the SHAREABLE ``research_vault.gates`` package
(D-SV-0), not here. This module is the manuscript-loop's own thin,
additive wiring on top of it:

  check_support_tally(tree_root, ...) — walks every ``*.md`` file under a
      manuscript tree, finds every sentence carrying a ``[[citekey]]``
      wikilink, and calls ``gates.support_matcher.match_support()`` once per
      (sentence, citekey) pair. Runs the blind-judge canary FIRST (honesty-gates.md §4)
      — a known-supported synthetic probe through the SAME extractor+judge
      path; if it comes back [ABSENT], the judge/extractor is blind and the
      whole tally aborts loudly rather than emit false-BLOCKs.

The function returns a plain dict (not a dataclass) — the same shape the
design's hard-fidelity-gate section expects the ``approve-manuscript``
payload assembler to consume: ``errors`` (BLOCK-level strings), ``warnings``
(WARN-level strings), ``honest_report`` (never says "verified"), and
``canary_aborted``. Consumed by ``manuscript/check_gates.py::build_approve_payload``
(the manuscript-integration PR) — see that module for the honesty-class
assembly (support-matcher is the citation-fidelity BLOCK floor).

(The former cold-read self-containment critic — the manuscript-tree
adapter over ``gates.coldread.run_cold_read()`` — was removed: it was
SIGNAL-only, non-actionable under hands-off autonomy, and redundant with
the 2x3 review board's coherence axis + RD-6's hard term-definition gate.
The operator's call; see DEVLOG. The cold-agent-judge fan-out seam below
is now support-matcher-ONLY.)

Doctrine: data/doctrine/honesty-gates.md, data/doctrine/review-board.md.

SCOPE — additive, minimal shared-seam edit at the time this file was written:
  This file did NOT touch ``manuscript/check_gates.py`` — that module has
  SINCE LANDED (the manuscript-integration PR) and imports
  ``check_support_tally`` from here as its judge-guarded LLM gate.

Stdlib only. Hermetic in tests (judge_fn is always injectable — no live LLM
call required to exercise this module).
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from research_vault.gates.support_matcher import match_support
from research_vault.manuscript.citation_pattern import WIKILINK_CITE_RE as _WIKILINK_CITE_RE

# the judge-model env read was DELETED — no rv code reads a judge-model
# env var to run a judge. ``judge_model`` is a pass-through audit label only,
# defaulting to "". Production support-matching runs via the emit/ingest cold
# fan-out (``emit_support_tasks`` / ``ingest_support_verdicts`` below); the
# inline ``check_support_tally`` path is exercised only with a test-injected
# ``judge_fn``.
_DEFAULT_JUDGE_MODEL: str = ""

# Batch sizing: default per-batch task count for emit_support_tasks.
# Raised from the original 8 -> 20 (the operator's call, live 0.3.0
# validation run: 82 tasks / 11 batches for a 25-paper survey was too
# many cold-judge spawns for the hub to fan out; 20 packs the same task
# count into ~4-5 batches).
# A single task carries a claim + a ~5KB source-note excerpt (per-field
# cap _PER_FIELD_CAP=1200 x several fields), so 20 tasks/batch keeps a
# single cold judge's context manageable while cutting fan-out ~2-3x.
DEFAULT_SUPPORT_BATCH_SIZE: int = 20


# ---------------------------------------------------------------------------
# _collect_support_items — shared (sentence, citekey, section) extraction
# ---------------------------------------------------------------------------

def _collect_support_items(draft_files: "list[Path]") -> list[tuple[str, str, str]]:
    """Extract every (sentence, citekey, section) triple carrying a citation.

    Shared by BOTH judge paths (charter §6: single source, not two
    independently-drifting copies): the inline judge loop
    (``check_support_tally``: test-injected ``judge_fn`` only, no live
    API default) and the cold-fanout emit path
    (``emit_support_tasks``) call this identically so the two paths
    see the EXACT same set of (claim, citekey) pairs for a given draft.
    """
    all_items: list[tuple[str, str, str]] = []
    for md in draft_files:
        if not md.exists():
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        section_name = md.stem
        sentences = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            for cm in _WIKILINK_CITE_RE.finditer(sent):
                k = cm.group(1).strip()
                if k:
                    all_items.append((sent, k, section_name))
    return all_items


def _resolve_cited_note_path(
    citekey: str, notes_root: Path, literature_root: Path | None = None,
) -> Path:
    """Resolve the note path a citekey's structured fields (``## Result``
    etc, all intrinsic/CORE-only content) should be read from.

    Resolution order:
      1. ``literature_root/<citekey>.md`` (the CENTRAL CORE) — when given
         and the file exists.
      2. ``notes_root/literature/<citekey>.md`` (legacy/degrade — a
         monolithic fixture, or a caller with no literature_root).
      3. ``notes_root/<citekey>.md`` (pre-existing bare fallback).

    Both ``check_support_tally`` and ``emit_support_tasks`` share this so
    the live-judge and cold-fanout-emit paths never resolve a citekey to
    two different notes.
    """
    if literature_root is not None:
        core_path = Path(literature_root) / f"{citekey}.md"
        if core_path.exists():
            return core_path
    note_path = notes_root / "literature" / f"{citekey}.md"
    if not note_path.exists():
        note_path = notes_root / f"{citekey}.md"
    return note_path


# ---------------------------------------------------------------------------
# check_support_tally — batch support-match over a manuscript tree
# ---------------------------------------------------------------------------

def check_support_tally(
    tree_root: Path,
    *,
    notes_root: Path | None = None,
    judge_fn: Callable[[str], str] | None = None,
    judge_model: str = _DEFAULT_JUDGE_MODEL,
    rubric_override: str | None = None,
    config: Any | None = None,
    literature_root: Path | None = None,
) -> dict[str, Any]:
    r"""Run the claim->source support-matcher on all (sentence, [[citekey]]) pairs.

    For each sentence containing a [[citekey]] wikilink, calls gates.support_matcher's
    match_support() with the cited literature/ note's structured fields.

    Returns a dict with:
      "verdicts":      list of SupportVerdict (one per (sentence, citekey) pair)
      "n_sentences":   int
      "m_citations":   int
      "k_block":       int (ABSENT or CONTRADICTS)
      "j_warn":        int (PARTIAL)
      "honest_report": str — "N sentences, M citations, k BLOCK, j WARN"
      "errors":        list of BLOCK-level strings
      "warnings":      list of WARN-level strings
      "canary_aborted": bool

    BLOCK on [ABSENT] / [CONTRADICTS]; WARN on [PARTIAL].
    Honest output: 'N sentences, M citations, k BLOCK, j WARN' — never 'verified'.

    When notes_root is None: inferred from tree_root (manuscripts/<id>/ ->
    the project notes root two levels up).
    """
    from research_vault.manuscript.draft_files import resolve_draft_files

    draft_files = resolve_draft_files(tree_root)
    if not draft_files:
        return {
            "verdicts": [], "n_sentences": 0, "m_citations": 0,
            "k_block": 0, "j_warn": 0,
            "honest_report": "0 sentences, 0 citations, 0 BLOCK, 0 WARN",
            "errors": [], "warnings": [],
            "canary_aborted": False,
        }

    _notes_root = notes_root
    if _notes_root is None:
        _notes_root = tree_root.parent.parent  # manuscripts/<id>/ -> project root

    # ── Blind-judge canary (honesty-gates.md §4) ────────────────────────────
    # Before running the real tally, run one synthetic KNOWN-SUPPORTED probe
    # through the SAME extractor+judge pipeline. If it returns [ABSENT], the
    # judge is blind (extraction empty or judge mis-wired) — indistinguishable
    # from a real refutation. ABORT the gate LOUDLY rather than surface the
    # BLOCKs below as if they were real.
    with tempfile.TemporaryDirectory() as _canary_dir:
        _canary_note = Path(_canary_dir) / "canary_probe.md"
        _canary_note.write_text(
            "---\ntype: literature\n---\n"
            "## Result\n"
            "The accuracy on the benchmark is 85.3%, a statistically significant "
            "improvement over the 80.1% baseline (p < 0.01).\n",
            encoding="utf-8",
        )
        _canary_claim = (
            "The model achieves 85.3% accuracy, significantly above the 80.1% baseline."
        )
        try:
            _canary_verdict = match_support(
                claim=_canary_claim,
                citekey="canary_probe_known_positive",
                note_path=_canary_note,
                rubric_override=rubric_override,
                config=config,
                judge_fn=judge_fn,
                judge_model=judge_model,
            )
        except Exception:  # noqa: BLE001
            _canary_verdict = None

        _canary_absent = _canary_verdict is None or _canary_verdict.verdict == "ABSENT"
        if _canary_absent:
            _abort_msg = (
                "support-judge appears blind on a known-supported probe — "
                "extraction or judge mis-wired; the BLOCKs below are NOT real "
                "refutations. Fix wiring before trusting this gate."
            )
            return {
                "verdicts": [],
                "n_sentences": 0,
                "m_citations": 0,
                "k_block": 0,
                "j_warn": 0,
                "honest_report": "0 sentences, 0 citations, 0 BLOCK, 0 WARN (CANARY ABORTED)",
                "errors": [_abort_msg],
                "warnings": [],
                "canary_aborted": True,
            }

    # ── Collect every (sentence, citekey, section) triple ───────────────────
    all_items = _collect_support_items(draft_files)

    verdicts: list[Any] = []
    errors: list[str] = []
    warnings: list[str] = []
    n_sentences = len({item[0] for item in all_items})
    m_citations = len(all_items)

    for sentence, citekey, section in all_items:
        note_path = _resolve_cited_note_path(citekey, _notes_root, literature_root or getattr(config, "literature_root", None))

        stance: str | None = None
        plan_role: str | None = None
        if note_path.exists():
            try:
                ntext = note_path.read_text(encoding="utf-8")
            except OSError:
                ntext = ""
            from research_vault.note import _parse_frontmatter as _pfm
            nf, _ = _pfm(ntext)
            stance = nf.get("stance") or None
            plan_role = nf.get("plan_role") or None

        v = match_support(
            claim=sentence,
            citekey=citekey,
            note_path=note_path,
            stance=stance,
            plan_role=plan_role,
            rubric_override=rubric_override,
            config=config,
            judge_fn=judge_fn,
            judge_model=judge_model,
            section=section,
        )
        verdicts.append(v)

        if v.blocks:
            errors.append(
                f"support-matcher [{v.verdict}] BLOCK: [[{citekey}]] — "
                f"claim: '{sentence[:120]}' — "
                f"quoted span: {v.verbatim_span or 'none'} — "
                f"reasoning: {v.reasoning[:200]}"
            )
        elif v.warns:
            warnings.append(
                f"support-matcher [PARTIAL] WARN: [[{citekey}]] — "
                f"claim: '{sentence[:120]}' — "
                f"reasoning: {v.reasoning[:200]}"
            )

    k_block = sum(1 for v in verdicts if v.blocks)
    j_warn = sum(1 for v in verdicts if v.warns)

    return {
        "verdicts": verdicts,
        "n_sentences": n_sentences,
        "m_citations": m_citations,
        "k_block": k_block,
        "j_warn": j_warn,
        "honest_report": (
            f"{n_sentences} sentences, {m_citations} citations, {k_block} BLOCK, {j_warn} WARN"
        ),
        "errors": errors,
        "warnings": warnings,
        "canary_aborted": False,
    }


# ---------------------------------------------------------------------------
# Support-matcher cold-agent-judge fan-out (PRIMARY path)
#
# emit_support_tasks / ingest_support_verdicts replace the inline
# ``judge_fn(prompt)`` call above with the emit-tasks -> hub-fanout ->
# ingest-verdicts contract: rv never calls an LLM itself on this path — it
# writes ``_judge-tasks.json`` (the claim/citekey/source pairs + interleaved
# unmarked canaries), the hub fans out fresh cold subagent-judges over it,
# and rv ingests ``_judge-verdicts.json`` by id. Same fixed 4-verdict vocab,
# same rejects-only semantics, same fail-closed defaulting as the inline
# path above — see ``gates/judge_seam.py`` for the shared primitives.
# ---------------------------------------------------------------------------

def _compute_citation_set_hash(items: "list[tuple[str, str, str]]") -> str:
    """Deterministic hash of the draft's citation universe (the exact
    (sentence, citekey, section) triples ``_collect_support_items``
    extracts) — stamped into ``tasks_doc`` at emit time and recomputed at
    ingest time (PR Finding C: draft<->tasks binding).

    A citation added to (or removed from) the draft AFTER emit changes
    this hash — ``ingest_support_verdicts`` HALTs on a mismatch rather
    than silently trusting a stale task set as the citation-fidelity
    floor. Order-independent (sorted before hashing) so a no-op reflow of
    the same citations does not spuriously trip the check.
    """
    normalized = sorted(json.dumps(list(item), sort_keys=True) for item in items)
    blob = "\n".join(normalized).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


_SUPPORT_VERDICT_VOCAB: frozenset[str] = frozenset(
    {"SUPPORTS", "PARTIAL", "ABSENT", "CONTRADICTS"}
)
# Fail-closed default for support-matcher: "can't quote -> can't confirm"
# (mirrors match_support's own default; see support_matcher.py's ABSENT
# doc). Never a certifying value.
_SUPPORT_FAIL_CLOSED_DEFAULT = "ABSENT"

# NOTE on the design spec's JSON example vs. this vocab: the fan-out
# contract JSON literal shows ``"verdict": "SUPPORTED"`` — but the brief
# explicitly says the vocab must MATCH THE EXISTING EXTRACTOR, and
# ``gates.support_matcher._extract_support_verdict`` (the live code, the
# doctrine-of-record) uses ``SUPPORTS``, not ``SUPPORTED``. Followed the
# code (do NOT widen/rename the fixed vocab; SUPPORTED would silently fail
# to match ``_SUPPORT_VERDICT_VOCAB`` and get fail-closed-defaulted to
# ABSENT on every real task) — same "operator override / doc typo, code is
# the SSOT" precedent as D-MS-2 (equations.py). Flagged for the architect
# review — see the PR description.


def _format_note_source_excerpt(fields: dict[str, str]) -> str:
    """Render a literature note's structured fields as the ``source``
    excerpt a cold judge receives — same field set + ordering as
    ``support_matcher._build_judge_prompt``'s ``fields_block`` (reuse the
    same per-field cap so a huge note doesn't blow the task file's size),
    but standalone here since the fanout path never calls
    ``_build_judge_prompt`` (there is no live judge to prompt on rv's side).
    """
    _PER_FIELD_CAP = 1200
    lines: list[str] = []
    for k, v in sorted(fields.items()):
        if not v:
            continue
        val = v[:_PER_FIELD_CAP]
        if len(v) > _PER_FIELD_CAP:
            val += f" […truncated {len(v) - _PER_FIELD_CAP} chars…]"
        lines.append(f"{k}: {val}")
    return "\n".join(lines) if lines else "(no structured fields available)"


def _support_canary_bank() -> list[tuple[dict[str, str], str]]:
    """The interleaved support-matcher canary probes — bidirectional
    (a rubber-stamping AND a blind judge are both catchable), the same
    bidirectional-canary discipline the rest of this loop's judge gates use.

    Returns (task_fields_without_id, expected_verdict) pairs. ``kind`` and
    the claim/citekey/source shape are IDENTICAL to a real task — no field
    marks these as canaries ("canaries carry NO marker").

    PR BLOCK fix: the citekeys here are deliberately ordinary
    bibtex-style slugs (``smith2019`` etc.), indistinguishable from a real
    task's citekey. The ORIGINAL bank used self-labeling citekeys
    (``canary-known-supported``/``-absent``/``-contradicts``) — a cold
    judge reading the public ``_judge-tasks.json`` could read the expected
    verdict directly off the citekey string, acing all 3 canaries without
    ever judging claim-vs-source, which defeats the whole "is the judge
    actually working" check. The expected verdict now lives ONLY in the
    caller-assembled ``canary_key`` (never serialized into the public
    tasks doc) — see ``test_no_tell_value_level_serialized_doc``.
    """
    return [
        (
            {
                "kind": "support",
                "claim": (
                    "The model achieves 85.3% accuracy, significantly above "
                    "the 80.1% baseline."
                ),
                "citekey": "smith2019",
                "source": (
                    "result: The accuracy on the benchmark is 85.3%, a "
                    "statistically significant improvement over the 80.1% "
                    "baseline (p < 0.01)."
                ),
            },
            "SUPPORTS",
        ),
        (
            {
                "kind": "support",
                "claim": (
                    "The model was trained using reinforcement learning "
                    "from human feedback."
                ),
                "citekey": "chen2021",
                "source": (
                    "result: The accuracy on the benchmark is 85.3%, a "
                    "statistically significant improvement over the 80.1% "
                    "baseline (p < 0.01)."
                ),
            },
            "ABSENT",
        ),
        (
            {
                "kind": "support",
                "claim": "The model's accuracy is below the 80.1% baseline.",
                "citekey": "patel2020",
                "source": (
                    "result: The accuracy on the benchmark is 85.3%, a "
                    "statistically significant improvement over the 80.1% "
                    "baseline (p < 0.01)."
                ),
            },
            "CONTRADICTS",
        ),
    ]


def emit_support_tasks(
    tree_root: Path,
    *,
    notes_root: Path | None = None,
    manuscript_slug: str = "",
    batch_size: int = DEFAULT_SUPPORT_BATCH_SIZE,
    rubric_override: str | None = None,
    config: Any | None = None,
    literature_root: Path | None = None,
) -> dict[str, Any]:
    r"""Emit ``_judge-tasks.json`` + ``_judge-canary-key.json`` for the
    support-matcher cold-agent-judge fan-out (Phase A).

    Walks every draft file exactly as ``check_support_tally`` does (shares
    ``_collect_support_items`` — same items, same order, never drifts from
    the live-judge path), resolves each cited note's structured fields into
    a ``source`` excerpt, and interleaves 3 bidirectional canary probes at
    deterministic (not marker-revealing) positions among the real tasks.
    Batches task ids into groups of ``batch_size`` so the hub fans out a
    HANDFUL of cold judges, not one per claim.

    rv does NOT call an LLM on this path — no judge_fn, no env var. This
    is the whole point of the cold-agent-judge design: the fan-out is
    harness-side.

    Args:
        tree_root:        the manuscript folder (``manuscripts/<slug>/``).
        notes_root:        the project's OKF notes root; inferred from
                           ``tree_root`` when None (mirrors
                           ``check_support_tally``).
        manuscript_slug:  stamped into the tasks doc's ``manuscript`` field.
        batch_size:       max task ids per batch (default
                           ``DEFAULT_SUPPORT_BATCH_SIZE`` — "a handful of
                           batches", per the dispatch brief, not one cold
                           judge spawn per claim).
        rubric_override:  optional rubric override, stamped into the tasks
                           doc's ``rubric`` field (additive beyond the
                           design's literal JSON example — the hub needs
                           SOME way to see an adopter-overridden rubric
                           without hardcoding ``DEFAULT_SUPPORT_RUBRIC`` on
                           its own side).
        config:           optional Config for the rubric config-seam.

    Returns:
        ``{"tasks_doc": {...}, "canary_key_doc": {...}, "skipped_non_corpus":
        [...]}`` — write the first two with ``gates.judge_seam.write_json``
        (the canary_key_doc goes to a location the hub/judges never read,
        ). ``skipped_non_corpus`` is the sorted, deduped
        list of citekeys the draft cited that are NOT in the frozen review
        corpus (concept-slug wikilinks etc.) — surfaced so the caller can
        report them, never a silent drop (empty when no frozen
        ``_corpus.md`` exists to filter against — see the implementation
        comment on corpus scoping).

    A draft with zero [[citekey]] pairs is a correct, honest no-op: both docs
    carry an empty ``tasks``/``canaries`` collection, never fabricated.

    """
    from research_vault.manuscript.draft_files import resolve_draft_files
    from research_vault.gates import judge_seam
    from research_vault.gates.support_matcher import (
        _read_note_structured_fields,
        get_support_rubric,
    )
    from research_vault.review import _parse_corpus_citekeys

    draft_files = resolve_draft_files(tree_root)
    _notes_root = notes_root if notes_root is not None else tree_root.parent.parent

    all_items = _collect_support_items(draft_files) if draft_files else []
    citation_set_hash = _compute_citation_set_hash(all_items)

    # Live 0.3.0 validation finding: the draft can drop CONCEPT-slug
    # wikilinks inline as if they were paper citations (e.g.
    # ``[[survey-to-behaviour-is-the-untested-arrow]]``). A concept note
    # has no ``literature/<key>.md`` and no structured source fields — it
    # is not a paper, so "is this claim supported by that source?" is not
    # a well-formed question for it. Scope emission to the review's frozen
    # corpus (``reviews/<slug>/_corpus.md``, the same source-of-truth
    # ``check_gates.py``'s coverage-gate uses) when it exists; a citekey
    # outside the corpus is SKIPPED (not emitted as a task) but surfaced
    # via ``skipped_non_corpus`` on the return -- never a silent drop.
    #
    # When no frozen corpus exists (a manuscript not backed by an rv
    # review loop), there is no ground truth to filter against -- fall
    # back to the pre-fix behaviour of emitting every citekey the draft
    # names, so non-review-backed manuscripts are unaffected.
    corpus_citekeys: set[str] | None = None
    if manuscript_slug:
        corpus_path = _notes_root / "reviews" / manuscript_slug / "_corpus.md"
        if corpus_path.exists():
            corpus_citekeys = set(_parse_corpus_citekeys(corpus_path))

    real_tasks: list[dict[str, Any]] = []
    skipped_non_corpus: list[str] = []
    for sentence, citekey, _section in all_items:
        if corpus_citekeys is not None and citekey not in corpus_citekeys:
            skipped_non_corpus.append(citekey)
            continue
        note_path = _resolve_cited_note_path(citekey, _notes_root, literature_root or getattr(config, "literature_root", None))
        fields = _read_note_structured_fields(note_path)
        source = _format_note_source_excerpt(fields)
        real_tasks.append({
            "kind": "support",
            "claim": sentence,
            "citekey": citekey,
            "source": source,
        })

    if not real_tasks:
        tasks_doc = {
            "schema": judge_seam.TASKS_SCHEMA,
            "gate": "support-matcher",
            "manuscript": manuscript_slug,
            "judge_kind": "cold",
            "created": judge_seam.now_iso(),
            "rubric": get_support_rubric(override=rubric_override, config=config),
            "batches": [],
            "tasks": [],
            "citation_set_hash": citation_set_hash,
        }
        canary_key_doc = {"schema": judge_seam.CANARY_KEY_SCHEMA, "canaries": {}}
        return {
            "tasks_doc": tasks_doc,
            "canary_key_doc": canary_key_doc,
            "skipped_non_corpus": sorted(set(skipped_non_corpus)),
        }

    combined, canary_key = judge_seam.interleave_with_canaries(
        real_tasks, _support_canary_bank(),
    )

    task_ids = [t["id"] for t in combined]
    batches = [
        {"batch_id": f"b{i // batch_size + 1:02d}", "task_ids": task_ids[i:i + batch_size]}
        for i in range(0, len(task_ids), batch_size)
    ]

    tasks_doc = {
        "schema": judge_seam.TASKS_SCHEMA,
        "gate": "support-matcher",
        "manuscript": manuscript_slug,
        "judge_kind": "cold",
        "created": judge_seam.now_iso(),
        "rubric": get_support_rubric(override=rubric_override, config=config),
        "batches": batches,
        "tasks": combined,
        "citation_set_hash": citation_set_hash,
    }
    canary_key_doc = {"schema": judge_seam.CANARY_KEY_SCHEMA, "canaries": canary_key}

    return {
        "tasks_doc": tasks_doc,
        "canary_key_doc": canary_key_doc,
        "skipped_non_corpus": sorted(set(skipped_non_corpus)),
    }


def ingest_support_verdicts(
    tasks_doc: dict[str, Any],
    canary_key_doc: dict[str, Any] | None,
    verdicts_doc: dict[str, Any] | None,
    *,
    current_citation_set_hash: str | None = None,
) -> dict[str, Any]:
    r"""Ingest ``_judge-verdicts.json`` for the support-matcher fan-out
    (Phase C) — the id-join, canary check, and fail-closed
    assembly. Returns the SAME shape ``check_support_tally`` returns (so
    ``check_gates.build_approve_payload`` consumes both paths identically),
    plus ``halt``/``halt_reason``/``missing_ids``/``unrecognized_ids``.

    Guards (undiminished vs. the live judge path):
      - id<->id join (never prompt-text matching).
      - Canary-verified FIRST: ``gates.judge_seam.check_canaries`` raises
        ``CanaryAbortError`` on any missing/mismatched canary — callers
        MUST let this propagate (or catch it and HALT-DECLARE; do not
        swallow it and proceed).
      - Fail-closed: a verdicts file entirely missing, or present but
        carrying ZERO verdicts while real tasks exist, is the
        "floor gate NOT RUN" case -> ``halt=True`` (never ``ok:True``).
        A PARTIAL file (some ids present, some missing) is NOT a halt —
        each missing real-task id defaults to ABSENT (BLOCK, the
        fail-closed value) and is surfaced in ``missing_ids`` so the
        caller can re-fan just those ids (resumable).
      - Fixed vocab: an unrecognized verdict string also fail-closed
        defaults to ABSENT, surfaced in ``unrecognized_ids`` — never
        silently coerced or ignored.
      - Draft<->tasks binding (PR Finding C): when
        ``current_citation_set_hash`` is supplied and ``tasks_doc`` carries
        a ``citation_set_hash`` stamp that does NOT match it, the tasks
        file is STALE — the draft changed (a citation was added, removed,
        or reworded) since this task set was emitted, so the citation
        universe the fan-out judged is no longer the citation universe on
        the page -> ``halt=True`` (fail-closed; never a silent pass on a
        stale floor gate). ``current_citation_set_hash=None`` (the default,
        used by direct/pure callers and existing tests) skips this check —
        only ``ingest_support_verdicts_from_dir`` (which has live draft
        access) supplies it.

    A zero-task ``tasks_doc`` (the draft had no [[citekey]] pairs) is an honest
    no-op — no halt, zero everything.

    """
    from research_vault.gates import judge_seam

    real_task_ids = [
        t["id"] for t in tasks_doc.get("tasks", [])
    ]
    canaries = (canary_key_doc or {}).get("canaries", {})
    real_task_ids = [tid for tid in real_task_ids if tid not in canaries]
    task_by_id = {t["id"]: t for t in tasks_doc.get("tasks", [])}

    if not task_by_id:
        return {
            "verdicts": [], "n_sentences": 0, "m_citations": 0,
            "k_block": 0, "j_warn": 0,
            "honest_report": "0 sentences, 0 citations, 0 BLOCK, 0 WARN",
            "errors": [], "warnings": [],
            "canary_aborted": False,
            "halt": False, "halt_reason": "",
            "missing_ids": [], "unrecognized_ids": [],
        }

    stamped_hash = tasks_doc.get("citation_set_hash")
    if (
        current_citation_set_hash is not None
        and stamped_hash is not None
        and stamped_hash != current_citation_set_hash
    ):
        return {
            "verdicts": [], "n_sentences": 0, "m_citations": 0,
            "k_block": 0, "j_warn": 0,
            "honest_report": "0 sentences, 0 citations, 0 BLOCK, 0 WARN (STALE TASKS)",
            "errors": [
                "support-matcher judge-fanout HALT: _judge-tasks.json is "
                "STALE — the manuscript draft's citation set has changed "
                "since this task set was emitted (citation_set_hash "
                "mismatch). A citation added after emit was never judged; "
                "this is NOT a pass. Re-run `rv manuscript judge-emit` and "
                "re-fan the fresh task set before approving."
            ],
            "warnings": [],
            "canary_aborted": False,
            "halt": True,
            "halt_reason": (
                "citation_set_hash mismatch — the draft changed since "
                "emit; the tasks file no longer reflects the current "
                "citation universe (stale)."
            ),
            "missing_ids": [], "unrecognized_ids": [],
        }

    if judge_seam.fanout_incomplete(tasks_doc, verdicts_doc):
        return {
            "verdicts": [], "n_sentences": 0, "m_citations": 0,
            "k_block": 0, "j_warn": 0,
            "honest_report": "0 sentences, 0 citations, 0 BLOCK, 0 WARN (FAN-OUT NOT RUN)",
            "errors": [
                "support-matcher judge-fanout HALT: _judge-verdicts.json is "
                "missing or empty while real tasks were emitted — the "
                "citation-fidelity FLOOR was never checked (floor-gate "
                "NOT RUN). This is NOT a pass."
            ],
            "warnings": [],
            "canary_aborted": False,
            "halt": True,
            "halt_reason": (
                "verdicts file absent/empty for a non-empty support-matcher "
                "task set — fan-out did not complete."
            ),
            "missing_ids": [t["id"] for t in tasks_doc["tasks"]],
            "unrecognized_ids": [],
        }

    verdict_by_id: dict[str, str] = {}
    for v in (verdicts_doc or {}).get("verdicts", []):
        vid = v.get("id")
        if vid:
            verdict_by_id[vid] = str(v.get("verdict", ""))

    # Canary check FIRST — an untrustworthy judge invalidates everything
    # else; let CanaryAbortError propagate to the caller.
    judge_seam.check_canaries(canaries, verdict_by_id)

    filled, missing_ids, unrecognized_ids = judge_seam.fail_closed_fill(
        real_task_ids, verdict_by_id, _SUPPORT_VERDICT_VOCAB, _SUPPORT_FAIL_CLOSED_DEFAULT,
    )

    errors: list[str] = []
    warnings: list[str] = []
    verdict_records: list[dict[str, Any]] = []
    k_block = 0
    j_warn = 0
    n_sentences = len({task_by_id[tid]["claim"] for tid in real_task_ids})

    for tid in real_task_ids:
        task = task_by_id[tid]
        verdict = filled[tid]
        verdict_records.append({"id": tid, "verdict": verdict, "citekey": task["citekey"]})
        if verdict in ("ABSENT", "CONTRADICTS"):
            k_block += 1
            reason = (
                "no verdict returned by the fan-out (defaulted fail-closed)"
                if tid in missing_ids
                else (
                    f"unrecognized verdict string (defaulted fail-closed)"
                    if tid in unrecognized_ids
                    else "cold-judge verdict"
                )
            )
            errors.append(
                f"support-matcher [{verdict}] BLOCK: [[{task['citekey']}]] — "
                f"claim: '{task['claim'][:120]}' — id: {tid} — {reason}"
            )
        elif verdict == "PARTIAL":
            j_warn += 1
            warnings.append(
                f"support-matcher [PARTIAL] WARN: [[{task['citekey']}]] — "
                f"claim: '{task['claim'][:120]}' — id: {tid}"
            )

    return {
        "verdicts": verdict_records,
        "n_sentences": n_sentences,
        "m_citations": len(real_task_ids),
        "k_block": k_block,
        "j_warn": j_warn,
        "honest_report": (
            f"{n_sentences} sentences, {len(real_task_ids)} citations, "
            f"{k_block} BLOCK, {j_warn} WARN"
        ),
        "errors": errors,
        "warnings": warnings,
        "canary_aborted": False,
        "halt": False,
        "halt_reason": "",
        "missing_ids": missing_ids,
        "unrecognized_ids": unrecognized_ids,
    }


def emit_support_tasks_to_dir(judge_dir: Path, tree_root: Path, **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper: emit + write both artifacts under ``judge_dir``.

    ``judge_dir`` is typically ``tree_root / "judge" / "support-matcher"``
    (one directory per gate, per "one file per gate").
    """
    from research_vault.gates import judge_seam

    result = emit_support_tasks(tree_root, **kwargs)
    judge_seam.write_json(judge_dir / "_judge-tasks.json", result["tasks_doc"])
    judge_seam.write_json(judge_dir / "_judge-canary-key.json", result["canary_key_doc"])
    return result


def ingest_support_verdicts_from_dir(
    judge_dir: Path, tree_root: Path | None = None,
) -> dict[str, Any]:
    """Convenience wrapper: read all three artifacts from ``judge_dir`` and
    ingest. Returns the ``ingest_support_verdicts`` result, OR (if
    ``_judge-tasks.json`` itself is absent — nothing was ever emitted) an
    honest zero-task no-op, mirroring the empty-tasks_doc case.

    Recomputes the CURRENT draft's citation-set hash (PR Finding C)
    and passes it to ``ingest_support_verdicts`` so a draft that changed
    since emit HALTs rather than silently trusting stale tasks — this is
    the one caller with live filesystem access to do so.

    ``tree_root`` defaults to ``judge_dir.parent.parent`` (the
    ``tree_root / "judge" / "support-matcher"`` layout every emit path in
    this codebase uses — see ``_judge_dir`` in ``manuscript/__init__.py``);
    pass it explicitly if a caller ever uses a non-standard layout.
    """
    from research_vault.gates import judge_seam
    from research_vault.manuscript.draft_files import resolve_draft_files

    tasks_doc = judge_seam.read_json_or_none(judge_dir / "_judge-tasks.json")
    if tasks_doc is None:
        tasks_doc = {"tasks": []}
    canary_key_doc = judge_seam.read_json_or_none(judge_dir / "_judge-canary-key.json")
    verdicts_doc = judge_seam.read_json_or_none(judge_dir / "_judge-verdicts.json")

    _tree_root = tree_root if tree_root is not None else judge_dir.parent.parent
    current_hash: str | None = None
    try:
        draft_files = resolve_draft_files(_tree_root)
        current_items = _collect_support_items(draft_files) if draft_files else []
        current_hash = _compute_citation_set_hash(current_items)
    except (OSError, ValueError):
        # Can't resolve the current draft — leave current_hash None, which
        # skips the staleness check (same honest-degrade the rest of this
        # module uses when a filesystem read fails outside its own gate).
        current_hash = None

    return ingest_support_verdicts(
        tasks_doc, canary_key_doc, verdicts_doc,
        current_citation_set_hash=current_hash,
    )

