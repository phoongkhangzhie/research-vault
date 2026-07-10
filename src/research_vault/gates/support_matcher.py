# SPDX-License-Identifier: AGPL-3.0-or-later
"""support_matcher.py — claim→source support-matcher.

SHAREABLE LOCATION (D-SV-0, PR-M3): lives in research_vault.gates — a
top-level shared module, NOT under manuscript/. Re-instantiated from the
preserved craft in data/doctrine/honesty-gates.md (the module was removed
during a manuscript-loop refactor; the craft survived the removal). The manuscript loop is
the first consumer (see manuscript/fidelity_gates.py for the thin adapter),
not the only one — any loop needing a claim→source support check may call
match_support() directly.

SCOPE AND HONEST BOUNDARY
==========================
This module gives a SEMANTIC guarantee, not a prose guarantee:
  - STRUCTURAL (deterministic, sound): every [[citekey]] wikilink resolves; every
    references.md entry carries a real external id. These guarantees are in check_gates.py.
  - SEMANTIC (LLM-judged, assisted): whether a cited source actually backs the claim
    in the prose. This module is the semantic layer.

We do NOT guarantee "no hallucinated references in prose" — prose citation vs
non-citation is genuinely ambiguous and no regex is sound for that. For prose we
ASSIST the clear cases (naked_cite.py) and spotlight the rest. Document this honest
boundary and never claim a guarantee we cannot make.

VERDICTS
========
Four typed verdicts, bracket-keyed (mirrors the local CI-gate's [PASS]/[BLOCK]
convention but is a NEW 4-verdict extractor — the existing one is [PASS]/[BLOCK]-only,
not overloaded here):

  [SUPPORTS]     — the cited note directly backs the claim with a quotable span
  [PARTIAL]      → WARN — the note is related but does not fully support the claim;
                   or the cited note has stance: exploratory but the claim is
                   confirmatory-strength ("we show / establishes / confirms")
  [ABSENT]       → BLOCK — no span in the note backs the claim; source cannot be quoted
  [CONTRADICTS]  → BLOCK — the note's content opposes the claim

Anti-positivity moves (baked into the rubric and enforced in build_prompt):
  (1) DISCONFIRMING-READ-FIRST — explicitly ask the judge to look for how the claim
      could fail before accepting it.
  (2) DO NOT FEED THE PAPER'S OWN ABSTRACT/THESIS — the judge reads the note's
      STRUCTURED fields (TL;DR, metrics, findings, limitations), not the paper's
      own argument for itself.
  (3) TWO-SIDED RUBRIC — the judge must assess both the supporting and the
      contradicting evidence before settling on a verdict.

RUBRIC SEAM
===========
The researcher-authored adversarial rubric ships as DEFAULT_SUPPORT_RUBRIC — the seam
default, exactly like per_section_tips in style.py (§5J.13-D).

  - get_support_rubric(override=None, config=None) — returns the active rubric.
  - The config key is [manuscript_support] in research_vault.toml.
  - Adopter-override: pass rubric_override="..." to match_support(), or set
    [manuscript_support].rubric in the project TOML.
  - Runtime slots {CLAIM} / {NOTE_CONTENT} are filled by _build_judge_prompt.
  - {CANDIDATE_NOTES} is a disambiguation-mode slot; filled by the caller.

LLM JUDGE CALL
==============
The judge is injectable (judge_fn parameter) so tests can mock it hermetically.
PR-F: there is NO in-process API judge default. The PRODUCTION path is the
cold-agent-judge emit/ingest fan-out (``emit_support_tasks`` /
``ingest_support_verdicts`` in ``manuscript/fidelity_gates.py``) — rv NEVER
reaches the Anthropic Messages endpoint itself for a judge.
``match_support(judge_fn=None)`` raises loudly (fail-closed): a caller with no
judge_fn is a wiring error, not a soft no-op. The injectable ``judge_fn`` seam
remains for TESTS only.

D-MS-4 RESOLVED: Opus-tier judge at runtime (not the engineer's run model);
resolved on the cold-fanout side, never from an env var read here.

LOGGING
=======
judge_model + prompt_hash are returned in SupportVerdict and can be stored in
RunState.meta["support_matcher"] by the caller (the DAG gate or rv manuscript check).

Stdlib only.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# PR-F: the direct-API judge default (a judge-model env read + an in-process
# ``_default_judge_fn`` that hit the Anthropic Messages endpoint) was DELETED.
# The production judge path is the cold-agent-judge emit/ingest fan-out; rv
# reads no judge-model / API-key env var to run a judge. ``judge_model`` is a
# pass-through label only (stamped into ``SupportVerdict`` for audit),
# defaulting to "" — never a live model resolution.

# Confirmatory-strength verbs that escalate exploratory findings to BLOCK
# (D-MS-5: hedged/low-confidence finding stated as unhedged claim → BLOCK;
#  general drift → WARN). Used in J-2 stance-mismatch check.
_CONFIRMATORY_VERBS: frozenset[str] = frozenset({
    "we show",
    "we establish",
    "we demonstrate",
    "we confirm",
    "we prove",
    "establishes",
    "demonstrates",
    "confirms",
    "proves",
    "definitively",
    "unambiguously",
})

# ---------------------------------------------------------------------------
# Bracket extractor — 4-verdict support-matcher (NEW — does NOT overload
# control.py's [PASS]/[BLOCK] extractor, by design per §5J.13-A (3))
# ---------------------------------------------------------------------------

_SUPPORT_TOKEN_RE = re.compile(
    r"^\[(SUPPORTS|PARTIAL|ABSENT|CONTRADICTS)\]$",
    re.IGNORECASE,
)


def _extract_support_verdict(verdict_val: str) -> str | None:
    """Return the support-verdict token if *verdict_val* is exactly a bracketed token.

    Recognized forms: ``[SUPPORTS]``, ``[PARTIAL]``, ``[ABSENT]``, ``[CONTRADICTS]``
    (case-insensitive, full value).

    A bare word — 'SUPPORTS', 'absent', 'contradicts' — does NOT match.
    This prevents prose narrative from false-triggering the gate.

    Mirrors control.py:_extract_gate_verdict for [PASS]/[BLOCK] but is a NEW
    4-verdict extractor scoped exclusively to the support-matcher (§5J.13-A (3)).
    """
    m = _SUPPORT_TOKEN_RE.match(verdict_val.strip())
    return m.group(1).upper() if m else None


# ---------------------------------------------------------------------------
# Rubric seam
# ---------------------------------------------------------------------------

# Researcher-authored default rubric — the seam default (researcher, §5J.13-D).
# Mirrors the get_style_preamble() pattern in style.py.
#
# Runtime slots filled by _build_judge_prompt before the judge call:
#   {CLAIM}   — the manuscript sentence carrying the \cite{} under test
#   {NOTE_CONTENT} — the note's structured fields block
#
# Adopter-overridable via:
#   (a) rubric_override="..." passed to match_support(), OR
#   (b) [manuscript_support] rubric = "..." in research_vault.toml
#
# Design notes: disconfirm-first CAPS the verdict (anti-sycophancy);
# "can't quote → [ABSENT]" makes null the safe default (no rubber-stamp path).
# ALCE recall=verify verdict; ALCE precision=disambiguation selector.
DEFAULT_SUPPORT_RUBRIC: str = """\
SUPPORT-MATCHER JUDGE RUBRIC

You verify whether a cited note actually supports a specific manuscript claim.
You are an ADVERSARIAL checker, not a proofreader. Your default posture is
doubt: a citation is guilty until a verbatim span proves it innocent. A judge
that rubber-stamps is worse than no judge — it manufactures false confidence.

────────────────────────────────────────────────────────────────────────
INPUTS
────────────────────────────────────────────────────────────────────────
THE CLAIM (one manuscript sentence, carrying the [[citekey]] under test):
{CLAIM}

THE CITED NOTE (the literature/ OKF note's RECORDED content — its structured
fields: TL;DR, findings, metrics, method, limitations. This is ALL the
evidence you may use):
{NOTE_CONTENT}

────────────────────────────────────────────────────────────────────────
HARD CONSTRAINTS — read before you judge
────────────────────────────────────────────────────────────────────────
C1. NO SELF-ANCHORING. Judge the CLAIM against the NOTE's recorded content
    ONLY. You are FORBIDDEN to use: the manuscript's own thesis/abstract, the
    cited paper's title or abstract framing, or your background knowledge of
    what "that paper is famous for." If a fact is not recorded in the NOTE
    text above, it does not exist for this judgment — even if you are certain
    the real paper says it. Attribution is defined relative to the identified
    source, never relative to what the writer or you believe.

C2. VERBATIM SPAN OR IT DIDN'T HAPPEN. Any verdict other than [ABSENT] MUST
    quote an EXACT, character-for-character span copied from the NOTE. No
    paraphrase, no stitching two fragments, no ellipsis-bridging distant
    clauses. If you find yourself wanting to paraphrase to make the fit work,
    that is the tell that the support is a vibe, not a fact → emit [ABSENT].

C3. DISCONFIRM BEFORE YOU CONFIRM. You may not look for supporting evidence
    until you have first written the strongest DISCONFIRMING observation you
    can construct (Step 1). Skipping this step invalidates the judgment.

────────────────────────────────────────────────────────────────────────
PROCEDURE (do the steps in order; show them in the output)
────────────────────────────────────────────────────────────────────────
STEP 0 — DECOMPOSE THE CLAIM. Restate the claim as its single checkable
  proposition, and mark four attributes:
    • POLARITY   — asserted / negated?
    • STRENGTH   — universal ("always", "all"), typical ("generally"),
                   existential ("can", "in some cases"), or hedged?
    • MODALITY   — CAUSAL ("causes/drives/leads to/because") or
                   ASSOCIATIONAL ("correlates/associated with/co-occurs")?
    • SCOPE      — the population / setting / metric the claim is about.
  If the sentence bundles several propositions, judge the ONE the [[citekey]]
  is attached to; name it explicitly.

STEP 1 — DISCONFIRMING READ (mandatory, first). Scan the NOTE for the
  strongest evidence AGAINST support, in this priority order:
    (a) a span that asserts the OPPOSITE of the claim (→ points to CONTRADICTS);
    (b) a span that NARROWS the claim — a boundary condition, a smaller
        population, a weaker effect, a caveat/limitation field (→ PARTIAL);
    (c) a MODALITY mismatch — the note reports a correlation/association but
        the claim asserts causation (→ PARTIAL or CONTRADICTS);
    (d) a STRENGTH mismatch — the note reports an existential/typical result
        but the claim states it as universal/established (→ PARTIAL);
    (e) SILENCE — the note simply never addresses this proposition (→ ABSENT).
  Write the single strongest disconfirming observation you found, or state
  "no disconfirming evidence found in the note."

STEP 2 — SUPPORTING READ. Only now, find the single strongest span that
  would BACK the claim. Copy it VERBATIM. Ask the entailment question: does
  this span, on its own, ENTAIL the decomposed proposition (Step 0) at the
  claim's polarity, strength, modality, and scope? Entailment — not topical
  relatedness — is the bar. "Same subject area" is not support.

STEP 3 — ADJUDICATE. Weigh Step 1 against Step 2 using the criteria table.
  When a disconfirming finding and a supporting span BOTH exist, the
  disconfirming one caps the verdict (support that survives a caveat is at
  best [PARTIAL]; support contradicted outright is [CONTRADICTS]). Never let
  a strong-sounding Step-2 span overwrite a real Step-1 mismatch.

────────────────────────────────────────────────────────────────────────
VERDICT CRITERIA (symmetric — [SUPPORTS] is NOT the default)
────────────────────────────────────────────────────────────────────────
[SUPPORTS]     The quoted span ENTAILS the claim at its exact polarity,
               strength, modality, and scope. No narrowing, no caveat that
               undercuts it, no modality gap. If you removed this span from
               the note, the claim would lose its backing (necessity).

[PARTIAL]      The span backs a WEAKER or NARROWER version of the claim, OR
               backs it only under a caveat. Includes: claim stronger than
               span ("establishes" vs a span saying "suggests"); claim
               universal, span existential; claim causal, span merely
               associational; claim about population A, span about subset A'.
               REQUIRES a verbatim span AND a one-line statement of the GAP.

[ABSENT]       No span in the note entails or addresses the claim's
               proposition. The note is silent, or only topically adjacent.
               You CANNOT quote a supporting span → this verdict is mandatory.
               (Being unable to quote is itself the evidence.)

[CONTRADICTS]  A verbatim span asserts something incompatible with the claim
               — opposite direction, refuted effect, or a causal claim the
               note explicitly attributes to confound/non-causal. REQUIRES
               the contradicting span quoted.

────────────────────────────────────────────────────────────────────────
OUTPUT (machine-parseable; one block)
────────────────────────────────────────────────────────────────────────
VERDICT: [SUPPORTS|PARTIAL|ABSENT|CONTRADICTS]
SPAN: "<verbatim span from the note, or NONE for ABSENT>"
CLAIM_CORE: <the decomposed proposition from Step 0, with its 4 attributes>
DISCONFIRM: <the Step-1 observation>
GAP: <for PARTIAL/CONTRADICTS: the specific mismatch; else "—">
"""


def get_support_rubric(
    override: str | None = None,
    config: Any | None = None,
) -> str:
    """Return the active support-judge rubric.

    Priority: override arg > [manuscript_support].rubric in config > DEFAULT.

    The researcher-authored rubric drops in via:
      (a) override="..." (direct pass), OR
      (b) [manuscript_support] rubric = "..." in research_vault.toml.
    """
    if override is not None:
        return override
    if config is not None:
        raw = getattr(config, "_raw", {})
        ms_support = raw.get("manuscript_support", {})
        if isinstance(ms_support, dict):
            rubric_cfg = ms_support.get("rubric")
            if isinstance(rubric_cfg, str) and rubric_cfg.strip():
                return rubric_cfg
    return DEFAULT_SUPPORT_RUBRIC


# ---------------------------------------------------------------------------
# SupportVerdict dataclass
# ---------------------------------------------------------------------------

@dataclass
class SupportVerdict:
    """Result of a single (claim, citekey, note) support-match call.

    Attributes:
      verdict:        [SUPPORTS] | [PARTIAL] | [ABSENT] | [CONTRADICTS]
      verbatim_span:  exact quote from the note's structured fields; None if ABSENT
      polarity:       positive | negative | neutral | mixed
      reasoning:      1–3 sentence explanation from the judge
      claim:          the input claim sentence
      citekey:        the citekey being checked
      note_path:      absolute path string of the literature/ note
      judge_model:    the model-id used for this call (for RunState.meta logging)
      prompt_hash:    sha256 hex of the prompt sent (for audit + drift detection)
      j2_escalation: True when J-2 stance-mismatch caused an escalation to BLOCK
      raw_response:   the raw judge response (for debugging; not serialized to meta)
    """
    verdict: str  # SUPPORTS / PARTIAL / ABSENT / CONTRADICTS
    verbatim_span: str | None
    polarity: str
    reasoning: str
    claim: str
    citekey: str
    note_path: str
    judge_model: str
    prompt_hash: str
    j2_escalation: bool = False
    raw_response: str = field(default="", repr=False)
    # Manuscript section stem (tex.stem) threaded from
    # check_support_tally → match_support → SupportVerdict → to_meta_dict →
    # _detect_absent_rows → GapRecord._meta['section'] → suggest_route().
    # Default "" for back-compat: old verdicts without section → triage fallback.
    section: str = ""

    @property
    def blocks(self) -> bool:
        """True iff this verdict causes a BLOCK (ABSENT or CONTRADICTS, or J-2 inversion)."""
        return self.verdict in ("ABSENT", "CONTRADICTS") or self.j2_escalation

    @property
    def warns(self) -> bool:
        """True iff this verdict causes a WARN only (PARTIAL without J-2 inversion)."""
        return self.verdict == "PARTIAL" and not self.j2_escalation

    def to_meta_dict(self) -> dict[str, Any]:
        """Serialize for RunState.meta['support_matcher'] storage."""
        return {
            "verdict": self.verdict,
            "verbatim_span": self.verbatim_span,
            "polarity": self.polarity,
            "claim_snippet": self.claim[:120],
            "citekey": self.citekey,
            "note_path": self.note_path,
            "judge_model": self.judge_model,
            "prompt_hash": self.prompt_hash,
            "j2_escalation": self.j2_escalation,
            # Section stem for absent_row routing in gap_scan.py.
            # Empty string when not threaded (old verdicts) → back-compat triage fallback.
            "section": self.section,
        }


# ---------------------------------------------------------------------------
# Note field extraction (reads STRUCTURED fields only — not the abstract)
# ---------------------------------------------------------------------------

def _read_note_structured_fields(note_path: Path) -> dict[str, str]:
    """Extract structured fields from a literature/ note for judge input.

    Robust extraction — feeds the judge ALL evidence in real OKF notes.

    Strategy:
      1. Strip HTML comments first (comment-only scaffold → {} → correctly ABSENT).
      2. Frontmatter: include all scalar fields except id/pointer denylist.
      3. Body: extract EVERY ##/### section (heading → content). Skip only a section
         titled EXACTLY 'Abstract' (anti-positivity move 2: the cited paper's own
         abstract is NOT the researcher's recorded distillation). Everything else IS
         evidence the judge must see (## Result, ## Benchmark facts, ## Hypothesis,
         ## Setup, ## Analysis, etc.).
      4. Capture markdown tables (pipe rows) verbatim within each section.
      5. Fall back to the full de-commented body when the note has no ## headings.

    Returns a flat dict of non-empty fields (str values only).

    Does NOT read the paper's own abstract (anti-positivity move 2).

    ★ OA-fulltext-enrichment (tier 1, 0.3.0): this contract is DELIBERATELY
    UNCHANGED by full-text enrichment. The judge still only ever sees these
    structured fields (## Result / findings / metrics, etc.) — never the
    paper's raw full-text body, and never the abstract. What changes is
    UPSTREAM: the note's `## Result` section is now written from full text
    when available, so it carries a real magnitude/conditions/limitations
    span instead of abstract-level vagueness — the judge gets better
    evidence to adjudicate against, with its adversarial, abstract-blind
    contract fully intact. See design 2026-07-08-oa-fulltext-enrichment.md
    §4.2 for the exact chain.
    """
    if not note_path.exists():
        return {}
    try:
        raw_text = note_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    # ── 1. Strip HTML comments ───────────────────────────────────────────────
    html_comment_re = re.compile(r"<!--.*?-->", re.DOTALL)
    text = html_comment_re.sub("", raw_text)

    # ── 2. Parse frontmatter ─────────────────────────────────────────────────
    if not text.strip().startswith("---"):
        return {}
    # Find the closing --- delimiter
    stripped = text.lstrip()
    fm_start = raw_text.find("---")  # use original for FM boundary detection
    end_fm = raw_text.find("\n---", fm_start + 3)
    if end_fm == -1:
        return {}

    fm_block = raw_text[fm_start + 3 : end_fm].strip()
    all_fm: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = re.match(r"^(\w[\w_-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith(("'", '"')) and val.endswith(val[0]):
                val = val[1:-1]
            all_fm[key] = val

    # Denylist: id/pointer fields that are NOT evidence for support-matching
    _FM_DENYLIST: frozenset[str] = frozenset({
        "doi", "arxiv_id", "url", "type", "date", "year", "journal",
        "title", "author", "publisher", "citekey", "zotero_key",
        "results_location", "results_hash", "results_commit",
        "manuscript_pdf", "manuscript_hash", "backed_by", "closes",
        "covers", "dag_run", "synthesized_okf",
        # OA full-text-enrichment provenance fields (tier 1) — pointers/
        # metadata about HOW the note was read, not substantive claim
        # content the judge should weigh.
        "read_basis", "full_text_provider", "oa_status", "full_text_url",
        # identifier-persistence: the fuller external-id set persisted at
        # `rv research add` time (sources/identifiers.py). Provenance/
        # bookkeeping — never substantive claim content the judge weighs.
        "pmcid", "openalex", "pmid", "s2",
    })

    result: dict[str, str] = {}
    for k, v in all_fm.items():
        if k not in _FM_DENYLIST and v:
            result[k] = v

    # ── 3. Extract body sections ─────────────────────────────────────────────
    body = text[end_fm + 4:]  # text AFTER the closing ---

    # Find all ## / ### sections
    section_header_re = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
    headers = list(section_header_re.finditer(body))

    if headers:
        for i, hdr in enumerate(headers):
            section_title = hdr.group(2).strip()
            # Skip ONLY the literally-titled "Abstract" section
            if section_title.lower() == "abstract":
                continue
            # Content is from end of header line to start of next header (or end)
            content_start = hdr.end()
            content_end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
            section_content = body[content_start:content_end].strip()
            if not section_content:
                continue
            # Normalize key: lowercase, strip punctuation, spaces→_
            key = re.sub(r"[^a-z0-9]+", "_", section_title.lower()).strip("_")
            result[key] = section_content
    else:
        # No ## headings → fall back to full de-commented body
        body_stripped = body.strip()
        if body_stripped:
            result["body"] = body_stripped

    return result


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_judge_prompt(
    claim: str,
    citekey: str,
    note_fields: dict[str, str],
    rubric: str,
    stance: str | None = None,
    plan_role: str | None = None,
) -> str:
    """Build the full judge prompt for a (claim, citekey, note) triple.

    Anti-positivity move 2 is baked in: we inject ONLY the note's structured
    fields, not the paper's own abstract or thesis argument.

    J-2 stance context is injected when stance is not None/MISSING.

    If the rubric uses researcher-style {CLAIM}/{NOTE_CONTENT} slots, they are filled
    by substitution before appending the structured blocks. The structured
    === CLAIM === / === CITED SOURCE === markers are ALWAYS appended so the
    parser and test mocks can reliably locate claim + note content.
    """
    # Un-truncate — per-field cap raised to ~2000 chars; overall
    # budget ~6000 chars with a visible marker when exceeded (never silently drop).
    _PER_FIELD_CAP = 2000
    _OVERALL_BUDGET = 6000

    raw_lines: list[str] = []
    total_chars = 0
    for k, v in sorted(note_fields.items()):
        if not v:
            continue
        field_val = v[:_PER_FIELD_CAP]
        if len(v) > _PER_FIELD_CAP:
            field_val += f" […truncated {len(v) - _PER_FIELD_CAP} chars…]"
        line = f"  {k}: {field_val}"
        if total_chars + len(line) > _OVERALL_BUDGET:
            remaining = _OVERALL_BUDGET - total_chars
            if remaining > 40:
                raw_lines.append(line[:remaining])
            raw_lines.append(
                f"  […truncated {sum(len(l) for l in raw_lines) + len(line) - total_chars} "
                f"chars — overall note budget exceeded…]"
            )
            break
        raw_lines.append(line)
        total_chars += len(line)

    fields_block = "\n".join(raw_lines) or "  (no structured fields available)"

    stance_block = ""
    if stance and stance not in ("MISSING", "", "none"):
        stance_block = (
            f"\nSOURCE STANCE CONTEXT (for calibrated assessment):\n"
            f"  The cited note has stance: {stance!r}"
        )
        if plan_role and plan_role not in ("MISSING", "", "none"):
            stance_block += f" and plan_role: {plan_role!r}"
        stance_block += (
            "\n  If the claim uses confirmatory-strength language"
            " but the source is exploratory/tentative,"
            " that is evidence for [PARTIAL] or [CONTRADICTS].\n"
        )

    # Fill researcher-style slots if present (non-destructive — old rubrics without slots pass through)
    filled_rubric = rubric
    if "{CLAIM}" in filled_rubric:
        filled_rubric = filled_rubric.replace("{CLAIM}", claim)
    if "{NOTE_CONTENT}" in filled_rubric:
        filled_rubric = filled_rubric.replace("{NOTE_CONTENT}", fields_block)
    # {CANDIDATE_NOTES} is a disambiguation-mode slot; leave as literal for normal calls
    # (the judge reads it as "N/A — single note mode")

    # Always append === markers so response parsers and test mocks can reliably extract
    # claim and source sections regardless of rubric style.
    return (
        f"{filled_rubric}\n\n"
        f"=== CLAIM (from manuscript) ===\n{claim}\n\n"
        f"=== CITED SOURCE: {citekey} ===\n"
        f"Structured fields (judge ONLY against these — NOT the paper's own abstract):\n"
        f"{fields_block}"
        f"{stance_block}\n\n"
        f"Now give your verdict using the OUTPUT FORMAT above."
    )


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_judge_response(raw: str) -> tuple[str, str | None, str, str]:
    """Parse the judge response into (verdict, verbatim_span, polarity, reasoning).

    Returns ("ABSENT", None, "neutral", raw) on parse failure — ABSENT is the
    safe default (cannot quote → cannot confirm support).
    """
    verdict = "ABSENT"
    verbatim_span: str | None = None
    polarity = "neutral"
    reasoning = raw[:300]

    # Extract VERDICT: [TOKEN]
    m = re.search(r"VERDICT:\s*(\[[\w]+\])", raw, re.IGNORECASE)
    if m:
        token = m.group(1).upper()
        extracted = _extract_support_verdict(token)
        if extracted:
            verdict = extracted

    # Extract VERBATIM_SPAN: or SPAN: (the researcher rubric uses SPAN:)
    m = re.search(
        r"(?:VERBATIM_SPAN|SPAN):\s*(.+?)(?=\n(?:POLARITY|CLAIM_CORE|DISCONFIRM|GAP):|$)",
        raw, re.IGNORECASE | re.DOTALL,
    )
    if m:
        span = m.group(1).strip().strip('"')
        if span.lower() not in ("none", "n/a", "no quote", ""):
            verbatim_span = span[:500]

    # Extract POLARITY: (legacy rubric) — the researcher rubric does not emit POLARITY
    m = re.search(r"POLARITY:\s*(\w+)", raw, re.IGNORECASE)
    if m:
        pol = m.group(1).lower()
        if pol in ("positive", "negative", "neutral", "mixed"):
            polarity = pol

    # Extract REASONING: (legacy) or synthesise from researcher-style DISCONFIRM + GAP fields
    m = re.search(r"REASONING:\s*(.+)", raw, re.IGNORECASE | re.DOTALL)
    if m:
        reasoning = m.group(1).strip()[:500]
    else:
        # Researcher-style: stitch DISCONFIRM + GAP into reasoning field
        parts = []
        md = re.search(r"DISCONFIRM:\s*(.+?)(?=\nGAP:|\Z)", raw, re.IGNORECASE | re.DOTALL)
        if md:
            parts.append(md.group(1).strip())
        mg = re.search(r"GAP:\s*(.+)", raw, re.IGNORECASE | re.DOTALL)
        if mg and mg.group(1).strip() not in ("—", "-", ""):
            parts.append(f"Gap: {mg.group(1).strip()}")
        if parts:
            reasoning = " | ".join(parts)[:500]

    # If ABSENT but a span was returned, ignore the span (can't quote + ABSENT is contradictory)
    if verdict == "ABSENT":
        verbatim_span = None

    return verdict, verbatim_span, polarity, reasoning


# ---------------------------------------------------------------------------
# Core public callable — match_support()
#
# PR-F: the in-process ``_default_judge_fn`` (a direct Anthropic Messages
# call) was DELETED. Production support-matching runs via the cold-agent-judge
# emit/ingest fan-out (``fidelity_gates.emit_support_tasks`` /
# ``ingest_support_verdicts``); ``match_support`` is used inline only with a
# test-injected ``judge_fn``. A ``judge_fn=None`` call raises loudly.
# ---------------------------------------------------------------------------

def match_support(
    claim: str,
    citekey: str,
    note_path: Path,
    *,
    stance: str | None = None,
    plan_role: str | None = None,
    rubric_override: str | None = None,
    config: Any | None = None,
    judge_fn: Callable[[str], str] | None = None,
    judge_model: str = "",
    section: str = "",
) -> SupportVerdict:
    """Assess whether a cited source backs a claim in the manuscript.

    This is the reusable callable — both the semantic gate in check_gates.py
    and the (A) naked-cite resolver in naked_cite.py call this function.

    Args:
        claim:           the manuscript sentence containing the [[citekey]] wikilink.
        citekey:         the citekey being checked.
        note_path:       path to the literature/ OKF note for this source.
        stance:          optional stance: field from the note (for J-2 gate; None → skip).
        plan_role:       optional plan_role: field (for J-2 gate context; None → skip).
        rubric_override: optional complete rubric replacement (the researcher rubric drops in here).
        config:          optional Config for rubric lookup via [manuscript_support].
        judge_fn:        injectable LLM call (prompt: str) -> str. REQUIRED —
                         PR-F deleted the in-process API default; None raises
                         loudly (production runs via the emit/ingest cold
                         fan-out). Pass a mock in tests.
        judge_model:     the model-id to log (D-MS-4 resolved: Opus-tier).
        section:         manuscript section stem (tex.stem) passed
                         through from check_support_tally, stored in SupportVerdict.section
                         and emitted in to_meta_dict() for absent_row routing in gap_scan.py.
                         Default "" (back-compat: old callers without section → triage fallback).

    Returns:
        SupportVerdict with verdict, verbatim_span, polarity, judge_model, prompt_hash.
        ABSENT is the safe default when the note cannot be read or the judge fails.

    BLOCK on [ABSENT] / [CONTRADICTS]; WARN on [PARTIAL].
    Log judge_model + prompt_hash to RunState.meta["support_matcher"] at the call site.

    """
    rubric = get_support_rubric(override=rubric_override, config=config)
    note_fields = _read_note_structured_fields(note_path)
    prompt = _build_judge_prompt(
        claim=claim,
        citekey=citekey,
        note_fields=note_fields,
        rubric=rubric,
        stance=stance,
        plan_role=plan_role,
    )

    # Compute prompt hash for audit + RunState.meta
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]

    # If no structured fields available → ABSENT (can't quote → ABSENT)
    if not note_fields:
        return SupportVerdict(
            verdict="ABSENT",
            verbatim_span=None,
            polarity="neutral",
            reasoning="No structured fields found in the literature note — cannot assess support.",
            claim=claim,
            citekey=citekey,
            note_path=str(note_path),
            judge_model=judge_model,
            prompt_hash=prompt_hash,
            raw_response="",
            section=section,
        )

    # Call the judge. PR-F: there is NO in-process API judge default — a
    # judge_fn=None call is a wiring error (production runs via the
    # emit/ingest cold fan-out), so fail loudly rather than silently reach
    # for a deleted live-API default.
    if judge_fn is None:
        raise RuntimeError(
            "match_support: no judge_fn supplied. The direct-API judge path "
            "was deleted (PR-F) — production support-matching runs via the "
            "cold-agent-judge emit/ingest fan-out "
            "(fidelity_gates.emit_support_tasks / ingest_support_verdicts). "
            "Pass an explicit judge_fn only in tests."
        )
    try:
        raw_response = judge_fn(prompt)
    except Exception as e:  # noqa: BLE001
        # Judge failure degrades to ABSENT (safe — do not pass on failure)
        return SupportVerdict(
            verdict="ABSENT",
            verbatim_span=None,
            polarity="neutral",
            reasoning=f"Judge call failed: {e}",
            claim=claim,
            citekey=citekey,
            note_path=str(note_path),
            judge_model=judge_model,
            prompt_hash=prompt_hash,
            raw_response="",
            section=section,
        )

    verdict, verbatim_span, polarity, reasoning = _parse_judge_response(raw_response)

    # J-2 escalation: exploratory note cited at explicit confirmatory strength → BLOCK
    # (D-MS-5: strength inversion is a BLOCK; general drift is WARN)
    j2_escalation = False
    if stance and stance.lower() in ("exploratory", "pilot", "tentative"):
        claim_lower = claim.lower()
        if any(cv in claim_lower for cv in _CONFIRMATORY_VERBS):
            j2_escalation = True
            # If verdict was PARTIAL, escalate to BLOCK
            if verdict == "PARTIAL":
                pass  # j2_escalation=True already causes .blocks to return True
            # If verdict was SUPPORTS, downgrade to PARTIAL + escalate
            if verdict == "SUPPORTS":
                verdict = "PARTIAL"

    return SupportVerdict(
        verdict=verdict,
        verbatim_span=verbatim_span,
        polarity=polarity,
        reasoning=reasoning,
        claim=claim,
        citekey=citekey,
        note_path=str(note_path),
        judge_model=judge_model,
        prompt_hash=prompt_hash,
        j2_escalation=j2_escalation,
        raw_response=raw_response,
        section=section,
    )


# ---------------------------------------------------------------------------
# Batch support check (used by check_gates.py for the full manuscript)
# ---------------------------------------------------------------------------

@dataclass
class SupportMatchSummary:
    """Summary of a batch support-match run over all (sentence, cite) pairs.

    Honest output: 'N sentences, M citations, k BLOCK, j WARN'
    Never 'citations verified'.
    """
    n_sentences: int
    m_citations: int
    k_block: int
    j_warn: int
    verdicts: list[SupportVerdict]
    judge_model: str
    prompt_hash_set: list[str]

    def honest_report(self) -> str:
        """Return the honest tally line — never 'verified'."""
        return (
            f"{self.n_sentences} sentences, {self.m_citations} citations, "
            f"{self.k_block} BLOCK, {self.j_warn} WARN"
        )

    def meta_dict(self) -> dict[str, Any]:
        """For RunState.meta['support_matcher'] storage."""
        return {
            "n_sentences": self.n_sentences,
            "m_citations": self.m_citations,
            "k_block": self.k_block,
            "j_warn": self.j_warn,
            "judge_model": self.judge_model,
            "prompt_hashes": self.prompt_hash_set,
            "verdicts": [v.to_meta_dict() for v in self.verdicts],
        }
