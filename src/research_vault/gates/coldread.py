"""coldread.py — LLM cold-read self-containment judge (SR-MS-COLDREAD).

SHAREABLE LOCATION (D-SV-0, PR-M3): lives in research_vault.gates — a
top-level shared module, NOT under manuscript/. Re-instantiated from the
preserved craft in data/doctrine/honesty-gates.md (the module was removed
in SR-RM-FIGMS; the craft survived the removal). The manuscript loop is
the first consumer (see manuscript/fidelity_gates.py for the thin adapter),
not the only one — any loop that produces a reader-facing artifact may call
run_cold_read() directly.

SCOPE AND HONEST BOUNDARY
==========================
This module gives a SELF-CONTAINMENT judgment, not a truth judgment:
  - STRUCTURAL (deterministic, sound, no LLM): Flag-A scan over pdftotext output —
    the same leak patterns as the .tex body scan, applied to the rendered PDF text.
    Belt-and-suspenders: catches any leak the render introduces that .tex scan missed.
  - SEMANTIC (LLM-judged, assisted): whether a fresh reader with ONLY the PDF text
    can follow every reference, term, and cross-reference without external help.

This is ORTHOGONAL to SR-MS-2 (support-matcher):
  - support-matcher: are claims TRUE (claim ↔ the cited note's structured fields).
  - cold-read: do references RESOLVE for a stranger (reads ONLY the pdftotext output).

VERDICT TOKENS (3 — separate from SR-MS-2's 4-verdict set)
===========================================================
  [STANDS-ALONE]  Every reference resolves from the paper alone. Earned only after
                  a full disconfirming sweep.
  [DANGLING]      A reference leads outside the paper (internal id, artifact path,
                  raw hash, provenance pointer, broken cross-ref, undefined term).
                  → BLOCK.
  [NEEDS-CONTEXT] A reference resolves but is too thin for a fresh reader.
                  → WARN.

RUBRIC SEAM
===========
The researcher-authored adversarial cold-read rubric ships as DEFAULT_COLDREAD_RUBRIC — the seam
default, exactly as DEFAULT_SUPPORT_RUBRIC sits in support_matcher.py.

  - get_coldread_rubric(override=None, config=None) — returns the active rubric.
  - The config key is [manuscript_coldread] in research_vault.toml.
  - Runtime slot {PDF_TEXT} filled by _build_coldread_prompt.

BIDIRECTIONAL CANARY
====================
Both canary probes run through the full pdftotext→judge pipeline before any real
verdict is trusted:
  (a) Known self-contained probe → MUST emit [STANDS-ALONE], BLOCK_COUNT=0.
      If the judge flags it → judge is TRIGGER-HAPPY → ABORT.
  (b) Known leaky probe → MUST emit [DANGLING], BLOCK_COUNT≥2.
      If the judge waves it through → judge is BLIND → ABORT.

FLAG-A (architect — belt-and-suspenders)
====================================
The AUDIENCE Layer-1 body leak-scan runs on .tex source. COLDREAD has pdftotext,
so ALSO run the same deterministic patterns (sha256/covers_hash/results_hash,
results/* paths) over the pdftotext OUTPUT — catching any leak the render
introduces that the .tex scan could not see. Deterministic BLOCK, independent of
the LLM judge.

LLM JUDGE CALL
==============
Reuses the _default_judge_fn from support_matcher.py (same Anthropic Messages
API urllib call, same ANTHROPIC_API_KEY requirement, same stdlib-only contract).
Injectable via judge_fn parameter so tests can mock it hermetically.

Stdlib only.
sr: SR-MS-COLDREAD
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Resolved at import time from RV_JUDGE_MODEL env var.
# Tests always pass judge_fn= (mock) so this is never evaluated in test runs.
DEFAULT_JUDGE_MODEL: str = os.environ.get("RV_JUDGE_MODEL", "")

# ---------------------------------------------------------------------------
# Bracketed verdict extractor — 3-verdict cold-read (NEW — distinct from
# support_matcher.py's 4-verdict extractor by design per SR-MS-COLDREAD spec)
# ---------------------------------------------------------------------------

_COLDREAD_TOKEN_RE = re.compile(
    r"^\[(STANDS-ALONE|DANGLING|NEEDS-CONTEXT)\]$",
    re.IGNORECASE,
)


def _extract_coldread_verdict(verdict_val: str) -> str | None:
    """Return the cold-read verdict token if *verdict_val* is exactly a bracketed token.

    Recognized forms: ``[STANDS-ALONE]``, ``[DANGLING]``, ``[NEEDS-CONTEXT]``
    (case-insensitive, full value, brackets required).

    A bare word without brackets does NOT match — prevents prose narrative from
    false-triggering the gate.

    This is a NEW 3-verdict extractor scoped exclusively to the cold-read judge.
    It does NOT overload support_matcher.py's [SUPPORTS]/[PARTIAL]/[ABSENT]/
    [CONTRADICTS] extractor (SR-MS-COLDREAD spec: separate extractors per domain).
    """
    m = _COLDREAD_TOKEN_RE.match(verdict_val.strip())
    if not m:
        return None
    return m.group(1).upper()


# ---------------------------------------------------------------------------
# Rubric seam
# ---------------------------------------------------------------------------

# Researcher-authored default rubric — the seam default (researcher, SR-MS-COLDREAD).
# Mirrors get_support_rubric() / DEFAULT_SUPPORT_RUBRIC in support_matcher.py.
#
# Runtime slot filled by _build_coldread_prompt before the judge call:
#   {PDF_TEXT} — the pdftotext output of the compiled paper
#
# Adopter-overridable via:
#   (a) rubric_override="..." passed to run_cold_read(), OR
#   (b) [manuscript_coldread] rubric = "..." in research_vault.toml
DEFAULT_COLDREAD_RUBRIC: str = """\
COLD-READ SELF-CONTAINMENT JUDGE RUBRIC

You are a FRESH READER. You have been handed ONE compiled paper (its extracted
text, below) and NOTHING else — no repository, no author's notes, no build
transcript, no chat history, no prior acquaintance with this project. Your one
job: decide whether every reference, term, pointer, and cross-reference in the
paper RESOLVES from the paper's own text alone. You are an ADVERSARIAL checker,
not a proofreader. Your default posture is doubt: a reference does NOT resolve
until the paper's own words resolve it. A judge that waves a paper through is
worse than no judge — it certifies a stranger can read a paper that a stranger
cannot.

This is ORTHOGONAL to whether the paper's claims are TRUE (a different gate
checks that). You judge only whether a reader can FOLLOW the paper unaided.

────────────────────────────────────────────────────────────────────────
INPUT — the compiled paper text (this is ALL you may use)
────────────────────────────────────────────────────────────────────────
{PDF_TEXT}

────────────────────────────────────────────────────────────────────────
HARD CONSTRAINTS — read before you judge
────────────────────────────────────────────────────────────────────────
C1. ★ NO EXTERNAL RESOLUTION (anti-anchoring — the load-bearing rule).
    You are FORBIDDEN to resolve any reference using knowledge from outside
    the PDF text above. If a term, acronym, symbol, pointer, dataset name,
    method name, or cross-reference cannot be resolved from the paper's OWN
    words, then it DOES NOT RESOLVE — even if you personally know what it
    means, even if it is "obvious" from the field, even if the surrounding
    project would make it clear. You do not have the project. You have the
    page. Judge only from what is printed. If you catch yourself thinking
    "well, everyone in this area knows that X is..." — STOP: that is exactly
    the anchoring this gate exists to catch. The reader you stand in for does
    NOT know.

C2. VERBATIM SPAN OR IT DIDN'T HAPPEN. Every [DANGLING] and every
    [NEEDS-CONTEXT] flag MUST quote the EXACT offending text, character-for-
    character, copied from the paper above — no paraphrase, no cleanup, no
    reconstruction. If you cannot quote the literal string that dangles, you
    do NOT have a flag; drop it. (Being unable to quote is the tell that the
    flag is a vibe, not a fact.) The verbatim span is what makes the verdict
    auditable and fail-loud.

C3. DISCONFIRM FIRST. Before you may certify anything as self-contained, you
    must first sweep the WHOLE paper for what does NOT stand alone (Step 1).
    You do not get to affirm until you have hunted for leaks. Skipping the
    sweep invalidates the judgment.

C4. DISTINGUISH APPARATUS FROM LEAKS (do not over-flag). Normal scholarly
    apparatus RESOLVES and is NOT a flag: a bibliographic citation like
    "[12]" or "(Smith, 2021)" backed by a References/Bibliography list in the
    paper; a "Figure 2"/"Table 1"/"Section 4" that IS present in the text; an
    acronym expanded on first use ("holistic fidelity score (HFS)"); standard
    field terms and common mathematical notation. These are how papers
    legitimately point inside themselves. A flag is for a pointer that leads
    NOWHERE the reader can go: to the repo, to a run, to a file, to a hash,
    to an undefined coinage, to a float that is not there.

────────────────────────────────────────────────────────────────────────
PROCEDURE (do the steps in order; show them in the output)
────────────────────────────────────────────────────────────────────────
STEP 1 — DISCONFIRMING SWEEP (mandatory, first). Read the paper as a stranger
  and list every reference that fails to resolve from the page. Hunt these
  leak-shapes in priority order:
    (a) INTERNAL PLUMBING — a run id, ms_id, dag_run, covers_hash/results_hash
        token, a bare 64-hex or "sha256:" hash, a build/job id. A reader
        cannot open a run. → [DANGLING]
    (b) ARTIFACT PATH — a filesystem path to data or code
        ("results/foo.csv", "data/*.json", an absolute "/Users/…" or
        "/home/…" path). A reader does not have your filesystem. → [DANGLING]
    (c) PROVENANCE POINTER — "as recorded in provenance", "see the run",
        "see Note 3", "in the vault", "the attached artifact", any "see X"
        where X is not a section/figure/table/citation the paper contains.
        → [DANGLING]
    (d) BROKEN CROSS-REFERENCE — a "Figure 4"/"Table 7"/"Section 9"/"Appendix
        C"/"Eq. (12)" whose target is NOT present in the text; a raw
        unrendered "\\ref"/"??"/"Figure ??"; a claimed figure or table that
        never appears. → [DANGLING]
    (e) UNDEFINED TERM USED AS IF DEFINED — an acronym never expanded, a
        coined name / metric / method / dataset used as though the reader
        already knows it, a symbol never introduced. If its meaning is not
        established anywhere in the paper, it dangles. → [DANGLING]
    (f) RESOLVABLE-BUT-THIN — the reference DOES resolve (the reader can find
        the target) but the explanation is so terse a fresh reader would
        struggle to follow it: a term defined only by a one-word gloss, a
        method named and pointed-to but never described, a figure present but
        with no legend a stranger can read. → [NEEDS-CONTEXT]
  For each leak, copy the VERBATIM span (C2). If you cannot quote it, it is
  not a leak — do not list it. If the sweep finds nothing, write
  "no unresolved references found."

STEP 2 — ADJUDICATE EACH FLAG. For every item from Step 1, assign:
    • [DANGLING]      → the reference cannot resolve from the paper at all
                        (shapes a–e). Objective. BLOCK.
    • [NEEDS-CONTEXT] → the reference resolves but is underexplained (shape f).
                        A judgment call. WARN.
  Apply C4: if the "leak" is in fact normal apparatus that resolves inside
  the paper, drop it — do not flag it.

STEP 3 — OVERALL VERDICT. If Step 2 produced at least one flag, the paper's
  overall verdict is the WORST flag ([DANGLING] if any dangles, else
  [NEEDS-CONTEXT]). If Step 1 found nothing after a genuine full sweep, the
  overall verdict is [STANDS-ALONE] — and only then.

────────────────────────────────────────────────────────────────────────
VERDICT MEANINGS
────────────────────────────────────────────────────────────────────────
[STANDS-ALONE]  Every reference, term, and cross-reference resolves from the
                paper's own text. A stranger with only this PDF can read it end
                to end. (Earned only after a full disconfirming sweep — never
                the default.)
[DANGLING]      A reference that leads outside the paper (internal id, artifact
                path, raw hash, provenance pointer, broken cross-ref, undefined
                term). The reader hits a dead end. → BLOCK. Verbatim span
                REQUIRED.
[NEEDS-CONTEXT] A reference that resolves but is too thin for a fresh reader to
                follow comfortably. → WARN (surfaced for the human, who owns the
                judgment call). Verbatim span REQUIRED.

────────────────────────────────────────────────────────────────────────
OUTPUT (machine-parseable). Emit one FLAG block per issue, then the SUMMARY.
────────────────────────────────────────────────────────────────────────
For each flag (repeat the block; emit ZERO blocks if the paper stands alone):

FLAG:
VERDICT: [DANGLING|NEEDS-CONTEXT]
SPAN: "<exact verbatim string copied from the paper — no paraphrase>"
KIND: <internal-plumbing|artifact-path|provenance-pointer|broken-xref|undefined-term|thin-explanation>
WHERE: <section/heading or nearby verbatim text locating the span in the paper>
MISSING: <what a fresh reader needs to resolve this, that the paper never supplies>

Then always, exactly once:

SUMMARY:
OVERALL: [STANDS-ALONE|DANGLING|NEEDS-CONTEXT]
BLOCK_COUNT: <number of [DANGLING] flags>
WARN_COUNT: <number of [NEEDS-CONTEXT] flags>
SWEPT: <one line confirming you read the whole paper as a fresh reader>
"""


def get_coldread_rubric(
    override: str | None = None,
    config: Any | None = None,
) -> str:
    """Return the active cold-read judge rubric.

    Priority: override arg > [manuscript_coldread].rubric in config > DEFAULT.

    The researcher-authored rubric ships as DEFAULT_COLDREAD_RUBRIC. To override:
      (a) pass rubric_override="..." to run_cold_read(), OR
      (b) set [manuscript_coldread] rubric = "..." in research_vault.toml.
    """
    if override is not None:
        return override
    if config is not None:
        raw = getattr(config, "_raw", {})
        ms_cr = raw.get("manuscript_coldread", {})
        if isinstance(ms_cr, dict):
            rubric_cfg = ms_cr.get("rubric")
            if isinstance(rubric_cfg, str) and rubric_cfg.strip():
                return rubric_cfg
    return DEFAULT_COLDREAD_RUBRIC


# ---------------------------------------------------------------------------
# Flag-A deterministic scan (belt-and-suspenders over pdftotext output)
# ---------------------------------------------------------------------------

# Mirror the leak-detection patterns from check_gates.py but applied to
# pdftotext output (not .tex source). Same patterns — belt-and-suspenders:
# catches any leak the LaTeX render introduces that the .tex scan missed.

_FA_SHA256_PREFIX_RE = re.compile(r"\bsha256:[0-9a-fA-F]{8,}", re.IGNORECASE)
_FA_BARE_HEX64_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
_FA_INTERNAL_TOKEN_RE = re.compile(
    r"\b(covers_hash|results_hash|run_id|dag_run)\b"
)
_FA_REPRO_SENTINEL_RE = re.compile(r"not-recorded-in-provenance", re.IGNORECASE)
_FA_ARTIFACT_PATH_RE = re.compile(
    r"\bresults/[^\s,\"'<>]+\.(?:csv|json)\b",
    re.IGNORECASE,
)
_FA_ABS_PATH_RE = re.compile(
    r"(?<![\\])(?:/Users/|/home/|~/)[^\s,\"'<>]{3,}",
)


def flag_a_scan(pdf_text: str) -> list[str]:
    """Deterministic Flag-A scan over pdftotext output (§5J.16.3 architect addendum).

    Runs the same leak-detection patterns as check_body_leakage() (check_gates.py)
    but applied to the pdftotext-extracted PDF text, not the .tex source.

    Belt-and-suspenders: catches any leak the LaTeX render introduces that the
    .tex body scan could not see (e.g. a macro that expands to a hash at compile
    time, or a results-path injected via \\input{} at render time).

    Detected patterns (all → deterministic BLOCK):
      - sha256:<hex> prefix — internal hash stamp
      - Bare 64-char hex run — sha256 hash value
      - covers_hash / results_hash / run_id / dag_run tokens
      - not-recorded-in-provenance sentinel
      - results/*.csv or results/*.json — artifact-path shapes
      - Absolute /Users/ or /home/ or ~/ paths

    Args:
        pdf_text: the raw text extracted from the compiled PDF by pdftotext.

    Returns:
        List of BLOCK-level hit strings (empty = no Flag-A leaks detected).

    HERMETIC: no LLM, no network, stdlib only.
    sr: SR-MS-COLDREAD
    """
    hits: list[str] = []

    for m in _FA_SHA256_PREFIX_RE.finditer(pdf_text):
        hits.append(
            f"Flag-A [sha256-prefix]: '{m.group()[:40]}' — internal hash stamp "
            f"in rendered PDF must not appear in a public paper."
        )

    for m in _FA_BARE_HEX64_RE.finditer(pdf_text):
        hits.append(
            f"Flag-A [bare-hex64]: '{m.group()[:20]}...' — bare 64-char hex "
            f"in rendered PDF looks like a hash value; not resolvable by a reader."
        )

    for m in _FA_INTERNAL_TOKEN_RE.finditer(pdf_text):
        hits.append(
            f"Flag-A [internal-token]: '{m.group()}' — DAG-internal id token "
            f"in rendered PDF is not resolvable by a reader."
        )

    for m in _FA_REPRO_SENTINEL_RE.finditer(pdf_text):
        hits.append(
            f"Flag-A [sentinel]: '{m.group()}' — repro sentinel string "
            f"in rendered PDF; not a valid public statement."
        )

    for m in _FA_ARTIFACT_PATH_RE.finditer(pdf_text):
        hits.append(
            f"Flag-A [artifact-path]: '{m.group()}' — artifact filesystem path "
            f"in rendered PDF; reader does not have this filesystem."
        )

    for m in _FA_ABS_PATH_RE.finditer(pdf_text):
        hits.append(
            f"Flag-A [abs-path]: '{m.group()[:60]}' — absolute local machine path "
            f"in rendered PDF; not resolvable by a reader."
        )

    return hits


# ---------------------------------------------------------------------------
# Canary probes (the researcher's bidirectional probes)
# ---------------------------------------------------------------------------

# Canary (a): known self-contained — guards against a TRIGGER-HAPPY judge.
# Expect OVERALL=[STANDS-ALONE], BLOCK_COUNT=0.
_CANARY_A_TEXT: str = (
    "We evaluate holistic fidelity score (HFS), a 0–100 measure of how closely a "
    "model's outputs track human reference judgments, across three models. As shown "
    "in Figure 1, the strongest model reaches an HFS of 71.4, a clear margin over the "
    "weakest at 52.9. This pattern is consistent with prior work on reference-based "
    "scoring [4]. Section 3 details the scoring procedure; Table 1 reports the full "
    "per-model breakdown.\n\n"
    "Figure 1: HFS by model. [plot present]\n"
    "Table 1: Per-model HFS and 95% confidence intervals. [table present]\n\n"
    "References\n"
    "[4] A. Rivera and B. Osei (2023). Reference-based fidelity scoring for "
    "generative models. Journal of Evaluation Methods, 11(2), 88–104."
)

# Canary (b): known leaky — guards against a BLIND judge.
# Expect OVERALL=[DANGLING], BLOCK_COUNT≥2.
_CANARY_B_TEXT: str = (
    "The full effect is reported in run covers_hash "
    "a3f9c1e28b7d46f0a3f9c1e28b7d46f0a3f9c1e28b7d46f0a3f9c1e28b7d4f00d, with the "
    "scored outputs available at results/hfs_by_model.csv. As recorded in "
    "provenance, the improvement holds across all seeds; see the run for the "
    "per-seed breakdown. Figure 4 shows the trend."
)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_coldread_prompt(pdf_text: str, rubric: str) -> str:
    """Build the full judge prompt for a cold-read assessment.

    Fills the {PDF_TEXT} slot in the rubric with the pdftotext output.
    The rubric is the researcher-authored adversarial cold-read rubric by default.
    """
    if "{PDF_TEXT}" in rubric:
        return rubric.replace("{PDF_TEXT}", pdf_text)
    # Legacy rubric without slot — append text after the rubric
    return f"{rubric}\n\n=== PAPER TEXT ===\n{pdf_text}\n\nNow give your verdict."


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

@dataclass
class ColdReadFlag:
    """A single flag from the cold-read judge response.

    Fields mirror the FLAG block in the rubric's OUTPUT format.
    """
    verdict: str      # DANGLING or NEEDS-CONTEXT
    span: str         # verbatim quoted span from the paper
    kind: str         # internal-plumbing | artifact-path | ... | thin-explanation
    where: str        # section/heading location hint
    missing: str      # what the reader would need to resolve this


@dataclass
class ColdReadResult:
    """Result of a cold_read judge call.

    Attributes:
      flags:           list of ColdReadFlag (one per flagged issue)
      flag_a_hits:     list of deterministic Flag-A hit strings (never empty
                       if pdftotext output contains a leak pattern)
      overall:         STANDS-ALONE | DANGLING | NEEDS-CONTEXT
      block_count:     number of [DANGLING] flags from the LLM judge
      warn_count:      number of [NEEDS-CONTEXT] flags from the LLM judge
      honest_report:   "P passages scanned, b BLOCK, w WARN"
      judge_model:     the model-id used for this call
      prompt_hash:     sha256 hex of the prompt sent
      raw_response:    the raw judge response (not serialized to meta)
      canary_aborted:  True iff one of the bidirectional canaries failed
      abort_reason:    non-empty string describing which canary failed and why

    Meta note: "passages scanned" in honest_report is a count of the non-empty
    sections of the pdftotext input, not a sentence count (the cold-read judge
    reads the whole paper in one call, unlike support_matcher which iterates
    per-citation).
    """
    flags: list[ColdReadFlag]
    flag_a_hits: list[str]
    overall: str
    block_count: int
    warn_count: int
    honest_report: str
    judge_model: str
    prompt_hash: str
    raw_response: str = field(default="", repr=False)
    canary_aborted: bool = False
    abort_reason: str = ""

    @property
    def blocks(self) -> bool:
        """True iff this result causes a BLOCK.

        Blocks on: DANGLING LLM flag, any Flag-A hit, OR unparseable judge output.
        UNPARSEABLE is a fail-closed sentinel: a judge that passes the canaries but
        returns malformed output on the real paper cannot certify it — it blocks.
        """
        return (
            self.block_count > 0
            or len(self.flag_a_hits) > 0
            or self.overall == "UNPARSEABLE"
        )

    @property
    def warns(self) -> bool:
        """True iff this result produces WARN-only (NEEDS-CONTEXT, no BLOCK)."""
        return self.warn_count > 0 and not self.blocks

    def to_meta_dict(self) -> dict[str, Any]:
        """Serialize for RunState.meta['cold_read'] storage."""
        return {
            "overall": self.overall,
            "block_count": self.block_count,
            "warn_count": self.warn_count,
            "flag_a_count": len(self.flag_a_hits),
            "honest_report": self.honest_report,
            "judge_model": self.judge_model,
            "prompt_hash": self.prompt_hash,
            "canary_aborted": self.canary_aborted,
            "abort_reason": self.abort_reason,
        }


def _parse_coldread_response(raw: str) -> tuple[list[ColdReadFlag], str, int, int]:
    """Parse the cold-read judge response into (flags, overall, block_count, warn_count).

    FAIL-CLOSED: if the SUMMARY block is absent or the OVERALL token is unparseable,
    returns overall="UNPARSEABLE" (never "STANDS-ALONE"). Callers must treat
    UNPARSEABLE as a BLOCK — not a silent pass.

    Rationale: a judge that passes both canary probes (well-formed responses on the
    known probes) but returns malformed output on the REAL paper cannot be trusted on
    that paper. Flag-A is deterministic but covers only hash/path shapes; it cannot
    catch semantic danglings (undefined term, broken cross-ref, provenance-pointer prose)
    that the LLM would have caught. Defaulting to STANDS-ALONE on a malformed response
    would let those semantic danglings ship silently. Fail closed instead.

    Parses the FLAG block format from the rubric:
      FLAG:
      VERDICT: [DANGLING|NEEDS-CONTEXT]
      SPAN: "..."
      KIND: ...
      WHERE: ...
      MISSING: ...
    And the SUMMARY block:
      SUMMARY:
      OVERALL: [STANDS-ALONE|DANGLING|NEEDS-CONTEXT]
      BLOCK_COUNT: N
      WARN_COUNT: N
      SWEPT: ...
    """
    flags: list[ColdReadFlag] = []

    # Parse FLAG blocks
    flag_block_re = re.compile(
        r"FLAG:\s*\n"
        r"VERDICT:\s*(\[[\w-]+\])\s*\n"
        r"SPAN:\s*(.+?)\s*\n"
        r"KIND:\s*(.+?)\s*\n"
        r"WHERE:\s*(.+?)\s*\n"
        r"MISSING:\s*(.+?)(?=\n\n|\nFLAG:|\nSUMMARY:|$)",
        re.DOTALL | re.IGNORECASE,
    )
    for m in flag_block_re.finditer(raw):
        raw_verdict = m.group(1).strip()
        extracted = _extract_coldread_verdict(raw_verdict)
        if extracted in ("DANGLING", "NEEDS-CONTEXT"):
            span = m.group(2).strip().strip('"\'')
            flags.append(ColdReadFlag(
                verdict=extracted,
                span=span[:500],
                kind=m.group(3).strip()[:100],
                where=m.group(4).strip()[:200],
                missing=m.group(5).strip()[:300],
            ))

    # Parse SUMMARY block — FAIL-CLOSED: absent/malformed SUMMARY → "UNPARSEABLE"
    # "UNPARSEABLE" is a sentinel that callers MUST treat as a BLOCK, never a pass.
    overall = "UNPARSEABLE"
    block_count = 0
    warn_count = 0

    has_summary = bool(re.search(r"\bSUMMARY:", raw, re.IGNORECASE))
    if has_summary:
        summary_overall_re = re.compile(
            r"OVERALL:\s*(\[[\w-]+\])",
            re.IGNORECASE,
        )
        m_overall = summary_overall_re.search(raw)
        if m_overall:
            extracted_overall = _extract_coldread_verdict(m_overall.group(1).strip())
            if extracted_overall:
                overall = extracted_overall
            # else: OVERALL token unrecognized → stays "UNPARSEABLE"
        # else: SUMMARY block present but no OVERALL line → stays "UNPARSEABLE"

    m_block = re.search(r"BLOCK_COUNT:\s*(\d+)", raw, re.IGNORECASE)
    if m_block:
        block_count = int(m_block.group(1))

    m_warn = re.search(r"WARN_COUNT:\s*(\d+)", raw, re.IGNORECASE)
    if m_warn:
        warn_count = int(m_warn.group(1))

    # Cross-check: if block_count > 0 but overall was parsed as STANDS-ALONE,
    # prefer DANGLING (the block count is more specific evidence of a failing flag)
    if block_count > 0 and overall == "STANDS-ALONE":
        overall = "DANGLING"
    if warn_count > 0 and overall == "STANDS-ALONE":
        overall = "NEEDS-CONTEXT"

    return flags, overall, block_count, warn_count


# ---------------------------------------------------------------------------
# Default judge_fn (reuse support_matcher's urllib-based Anthropic call)
# ---------------------------------------------------------------------------

def _default_judge_fn(prompt: str, model: str = DEFAULT_JUDGE_MODEL) -> str:
    """Call the Anthropic Messages API via the shared gates._llm helper.

    Same underlying call as support_matcher._default_judge_fn — the shared
    gates._llm.call_anthropic_messages helper — with a larger max_tokens
    budget (cold-read may emit more FLAG blocks than support-matcher).
    Requires ANTHROPIC_API_KEY in the environment. Zero external deps.

    Raises RuntimeError if the API key is absent or the request fails.
    """
    from research_vault.gates._llm import call_anthropic_messages

    return call_anthropic_messages(
        prompt, model, max_tokens=2048, timeout=90, caller_label="cold-read",
    )


# ---------------------------------------------------------------------------
# Environment guard for --cold-read Layer-2
# ---------------------------------------------------------------------------

def cold_read_layer2_env_guard() -> tuple[str, str]:
    """Check that RV_JUDGE_MODEL and ANTHROPIC_API_KEY are set.

    Returns (judge_model, api_key) if both are present.
    Raises RuntimeError loudly if either is absent — never silently degrades.

    Called by verbs.py for the --cold-read Layer-2 path (parallel to --semantic).
    """
    judge_model = os.environ.get("RV_JUDGE_MODEL", "").strip()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    missing = []
    if not judge_model:
        missing.append("RV_JUDGE_MODEL")
    if not api_key:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        raise RuntimeError(
            f"rv manuscript check --cold-read: FAIL — "
            f"env var(s) required but absent: {', '.join(missing)}. "
            f"Set them to the Opus-tier model ID and API key before running "
            f"the cold-read gate. (Plain 'rv manuscript check' stays hermetic.)"
        )
    return judge_model, api_key


# ---------------------------------------------------------------------------
# Core public callable — run_cold_read()
# ---------------------------------------------------------------------------

def run_cold_read(
    pdf_text: str,
    *,
    rubric_override: str | None = None,
    config: Any | None = None,
    judge_fn: Callable[[str], str] | None = None,
    judge_model: str = DEFAULT_JUDGE_MODEL,
) -> ColdReadResult:
    """Assess whether a compiled paper STANDS ALONE for a fresh reader.

    Runs the full cold-read pipeline:
      1. Flag-A deterministic scan (always, before the LLM).
      2. Bidirectional canary probes (both directions, before trusting any verdict).
      3. LLM judge on the real pdf_text (only if both canaries pass).

    Args:
        pdf_text:        pdftotext-extracted text of the compiled paper.
        rubric_override: optional complete rubric replacement (drops in the researcher's rubric).
        config:          optional Config for rubric lookup via [manuscript_coldread].
        judge_fn:        injectable LLM call (prompt: str) -> str.
                         Defaults to urllib Anthropic API call.
                         Pass a mock in tests (no key required).
        judge_model:     the model-id to log (D-AUD-5 resolved: Opus-tier).

    Returns:
        ColdReadResult with flags, Flag-A hits, overall verdict, tally,
        meta fields (judge_model, prompt_hash), and canary_aborted flag.

    sr: SR-MS-COLDREAD
    """
    _judge = judge_fn or (lambda p: _default_judge_fn(p, model=judge_model))
    rubric = get_coldread_rubric(override=rubric_override, config=config)

    # ── 1. Flag-A: deterministic scan (always runs, independent of LLM) ─────
    flag_a_hits = flag_a_scan(pdf_text)

    # ── 2. Bidirectional canary ───────────────────────────────────────────────
    # Canary (a): known self-contained → judge MUST emit [STANDS-ALONE], BLOCK_COUNT=0
    canary_a_prompt = _build_coldread_prompt(_CANARY_A_TEXT, rubric)
    try:
        canary_a_raw = _judge(canary_a_prompt)
    except Exception as e:
        canary_a_raw = ""

    _canary_a_flags, _canary_a_overall, _canary_a_blocks, _ = _parse_coldread_response(canary_a_raw)
    trigger_happy = (_canary_a_overall != "STANDS-ALONE") or (_canary_a_blocks > 0)

    if trigger_happy:
        abort_msg = (
            "cold-read judge is TRIGGER-HAPPY on a known self-contained probe — "
            "it flagged the clean canary (a) as non-standalone. "
            "Fix judge wiring before trusting this gate."
        )
        prompt_hash = hashlib.sha256(canary_a_prompt.encode()).hexdigest()[:16]
        return ColdReadResult(
            flags=[],
            flag_a_hits=flag_a_hits,
            overall="STANDS-ALONE",  # cannot trust the judge
            block_count=0,
            warn_count=0,
            honest_report="0 passages, 0 BLOCK, 0 WARN (CANARY ABORTED — trigger-happy judge)",
            judge_model=judge_model,
            prompt_hash=prompt_hash,
            raw_response=canary_a_raw,
            canary_aborted=True,
            abort_reason=abort_msg,
        )

    # Canary (b): known leaky → judge MUST emit [DANGLING], BLOCK_COUNT≥2
    canary_b_prompt = _build_coldread_prompt(_CANARY_B_TEXT, rubric)
    try:
        canary_b_raw = _judge(canary_b_prompt)
    except Exception as e:
        canary_b_raw = ""

    _canary_b_flags, _canary_b_overall, _canary_b_blocks, _ = _parse_coldread_response(canary_b_raw)
    blind = (_canary_b_overall != "DANGLING") or (_canary_b_blocks < 2)

    if blind:
        abort_msg = (
            "cold-read judge is BLIND on a known-leaky probe — "
            "it failed to flag [DANGLING] (or BLOCK_COUNT < 2) on the leaky canary (b). "
            "A self-containment gate's dominant failure is rubber-stamping. "
            "Fix judge wiring before trusting this gate."
        )
        prompt_hash = hashlib.sha256(canary_b_prompt.encode()).hexdigest()[:16]
        return ColdReadResult(
            flags=[],
            flag_a_hits=flag_a_hits,
            overall="STANDS-ALONE",  # cannot trust the judge
            block_count=0,
            warn_count=0,
            honest_report="0 passages, 0 BLOCK, 0 WARN (CANARY ABORTED — blind judge)",
            judge_model=judge_model,
            prompt_hash=prompt_hash,
            raw_response=canary_b_raw,
            canary_aborted=True,
            abort_reason=abort_msg,
        )

    # ── 3. Real judgment on actual pdf_text ───────────────────────────────────
    real_prompt = _build_coldread_prompt(pdf_text, rubric)
    prompt_hash = hashlib.sha256(real_prompt.encode()).hexdigest()[:16]

    try:
        raw_response = _judge(real_prompt)
    except Exception as e:
        raw_response = ""

    flags, overall, block_count, warn_count = _parse_coldread_response(raw_response)

    # ── 4. Merge Flag-A into the overall verdict (Flag-A always blocks) ───────
    # If Flag-A has hits and the LLM said STANDS-ALONE, escalate to DANGLING.
    # If the LLM output was UNPARSEABLE (fail-closed sentinel), leave it as-is —
    # UNPARSEABLE already blocks, and escalating to DANGLING would mask the root cause.
    if flag_a_hits and overall == "STANDS-ALONE":
        overall = "DANGLING"

    # Compose honest report (transparent about both LLM and Flag-A sources)
    n_flag_a = len(flag_a_hits)
    _unparseable_note = " (UNPARSEABLE output — fail-closed BLOCK)" if overall == "UNPARSEABLE" else ""
    honest_report = (
        f"1 paper, {block_count} LLM BLOCK, {warn_count} LLM WARN, "
        f"{n_flag_a} Flag-A BLOCK{_unparseable_note}"
    )

    return ColdReadResult(
        flags=flags,
        flag_a_hits=flag_a_hits,
        overall=overall,
        block_count=block_count,
        warn_count=warn_count,
        honest_report=honest_report,
        judge_model=judge_model,
        prompt_hash=prompt_hash,
        raw_response=raw_response,
        canary_aborted=False,
        abort_reason="",
    )
