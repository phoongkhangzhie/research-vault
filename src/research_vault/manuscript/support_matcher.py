"""support_matcher.py — claim→source support-matcher for Research Vault (SR-MS-2).

SCOPE AND HONEST BOUNDARY
==========================
This module gives a SEMANTIC guarantee, not a prose guarantee:
  - STRUCTURAL (deterministic, sound): every \\cite{key} resolves; every .bib entry
    carries a real external id. These guarantees are in check_gates.py.
  - SEMANTIC (LLM-judged, assisted): whether a cited source actually backs the claim
    in the prose. This module is the semantic layer.

We do NOT guarantee "no hallucinated references in prose" — prose citation vs
non-citation is genuinely ambiguous and no regex is sound for that. For prose we
ASSIST the clear cases (naked_cite.py) and spotlight the rest. Document this honest
boundary and never claim a guarantee we cannot make.

VERDICTS
========
Four typed verdicts, bracket-keyed (mirrors SR-CI's [PASS]/[BLOCK] convention but
is a NEW 4-verdict extractor — the existing one is [PASS]/[BLOCK]-only, not
overloaded here):

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
Ada is authoring the judge rubric prompt content in parallel. Build this module
as a seam with a placeholder default that her rubric drops into — exactly like
per_section_tips in style.py.

  - get_support_rubric(override=None, config=None) — returns the active rubric.
  - The config key is [manuscript_support] in research_vault.toml.
  - Ada's authored rubric replaces DEFAULT_SUPPORT_RUBRIC by passing override=
    or setting [manuscript_support].rubric in the project TOML.

LLM JUDGE CALL
==============
The judge is injectable (judge_fn parameter) so tests can mock it hermetically.
Default judge_fn uses urllib.request to call the Anthropic Messages API (stdlib only).
Requires ANTHROPIC_API_KEY env var. If absent → raises RuntimeError (callers can
treat this as a soft failure and degrade to [ABSENT] if appropriate).

D-MS-4 RESOLVED: Opus-tier judge at runtime (not the engineer's run model).

LOGGING
=======
judge_model + prompt_hash are returned in SupportVerdict and can be stored in
RunState.meta["support_matcher"] by the caller (the DAG gate or rv manuscript check).

Stdlib only.
sr: SR-MS-2
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

# D-MS-4 resolved: Opus-tier judge is the runtime model for the matcher.
DEFAULT_JUDGE_MODEL = "claude-opus-4-5"

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

# Placeholder default rubric — Ada's authored rubric drops in via override or config.
# Structure: the rubric is the judge prompt body that instructs the LLM how to assess
# whether a claim is backed by the structured fields of a literature note.
#
# Ada's rubric replaces this by:
#   (a) passing rubric_override="..." to match_support(), OR
#   (b) setting [manuscript_support].rubric in research_vault.toml
#
# This is the CONTENT of the judge prompt after the claim+note are injected.
DEFAULT_SUPPORT_RUBRIC: str = """\
You are a rigorous academic research integrity judge.

Your task: assess whether a CITED SOURCE backs a CLAIM from a manuscript.

ANTI-POSITIVITY MOVES (mandatory — follow in order):
(1) DISCONFIRMING READ FIRST: before deciding the source supports the claim,
    actively search for how the claim could be WRONG given the source. List at
    least one way the claim could be falsified by the source content.
(2) DO NOT USE THE PAPER'S OWN ABSTRACT/THESIS as evidence. Judge only against
    the structured fields: TL;DR, metrics, findings, limitations.
(3) TWO-SIDED RUBRIC: assess both supporting AND contradicting evidence before
    settling on a verdict.

VERDICT GUIDE:
  [SUPPORTS]     — the source directly backs the claim with a quotable span from
                   the note's structured fields. Quote the span verbatim.
  [PARTIAL]      — the source is related but does not fully support the claim;
                   or the claim overstates the finding's confidence/scope.
  [ABSENT]       — no span in the note backs the claim; you cannot quote support.
  [CONTRADICTS]  — the source's content opposes, refutes, or directly contradicts
                   the claim.

POLARITY:
  Report the source's overall stance toward the claim's direction:
    positive / negative / neutral / mixed

OUTPUT FORMAT (strict — machine-parsed):
  VERDICT: [SUPPORTS|PARTIAL|ABSENT|CONTRADICTS]
  VERBATIM_SPAN: <exact quote from the note's structured fields, or "none">
  POLARITY: <positive|negative|neutral|mixed>
  REASONING: <1–3 sentence explanation>
"""


def get_support_rubric(
    override: str | None = None,
    config: Any | None = None,
) -> str:
    """Return the active support-judge rubric.

    Priority: override arg > [manuscript_support].rubric in config > DEFAULT.

    Ada's authored rubric drops in via:
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
      citekey:        the BibTeX citekey being checked
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
        }


# ---------------------------------------------------------------------------
# Note field extraction (reads STRUCTURED fields only — not the abstract)
# ---------------------------------------------------------------------------

def _read_note_structured_fields(note_path: Path) -> dict[str, str]:
    """Extract structured fields from a literature/ note for judge input.

    Reads: TL;DR, metrics, findings, limitations, stance, plan_role.
    Does NOT read the paper's own abstract or thesis (anti-positivity move 2).

    Returns a flat dict of non-empty fields.
    """
    if not note_path.exists():
        return {}
    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    # Parse frontmatter
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_block = text[3:end].strip()
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = re.match(r"^(\w[\w_-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith(("'", '"')) and val.endswith(val[0]):
                val = val[1:-1]
            fields[key] = val

    # Extract only the structured fields (NOT abstract/title for anti-positivity)
    structured_keys = (
        "tldr", "tl_dr", "tl-dr",
        "metrics", "findings", "limitations",
        "caveats", "confidence",
        "stance", "plan_role",
        "key_findings", "result", "results",
        "notes", "summary",
    )
    result: dict[str, str] = {}
    for k in structured_keys:
        if k in fields and fields[k]:
            result[k] = fields[k]

    # Also extract body sections (e.g. ## Findings, ## Limitations)
    body = text[end + 4:]
    for section_name in ("TL;DR", "Findings", "Limitations", "Metrics", "Key Findings"):
        pattern = re.compile(
            rf"^#+ {re.escape(section_name)}\s*\n(.*?)(?=^#+|\Z)",
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        m = pattern.search(body)
        if m:
            content = m.group(1).strip()
            if content:
                key = section_name.lower().replace(";", "").replace(" ", "_")
                result.setdefault(key, content[:500])

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
    """
    fields_block = "\n".join(
        f"  {k}: {v[:400]}" for k, v in sorted(note_fields.items()) if v
    ) or "  (no structured fields available)"

    stance_block = ""
    if stance and stance not in ("MISSING", "", "none"):
        stance_block = (
            f"\nSOURCE STANCE CONTEXT (for calibrated assessment):\n"
            f"  The cited note has stance: {stance!r}"
        )
        if plan_role and plan_role not in ("MISSING", "", "none"):
            stance_block += f" and plan_role: {plan_role!r}"
        stance_block += (
            "\n  If the claim uses confirmatory-strength language (e.g. 'we show',"
            " 'establishes', 'proves') but the source is exploratory/tentative,"
            " that is evidence for [PARTIAL] or [CONTRADICTS].\n"
        )

    return (
        f"{rubric}\n\n"
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

    # Extract VERBATIM_SPAN: ...
    m = re.search(r"VERBATIM_SPAN:\s*(.+?)(?=\nPOLARITY:|$)", raw, re.IGNORECASE | re.DOTALL)
    if m:
        span = m.group(1).strip()
        if span.lower() not in ("none", "n/a", "no quote", ""):
            verbatim_span = span[:500]

    # Extract POLARITY: ...
    m = re.search(r"POLARITY:\s*(\w+)", raw, re.IGNORECASE)
    if m:
        pol = m.group(1).lower()
        if pol in ("positive", "negative", "neutral", "mixed"):
            polarity = pol

    # Extract REASONING: ...
    m = re.search(r"REASONING:\s*(.+)", raw, re.IGNORECASE | re.DOTALL)
    if m:
        reasoning = m.group(1).strip()[:500]

    # If ABSENT but a span was returned, ignore the span (can't quote + ABSENT is contradictory)
    if verdict == "ABSENT":
        verbatim_span = None

    return verdict, verbatim_span, polarity, reasoning


# ---------------------------------------------------------------------------
# Default judge_fn (urllib-based, Anthropic Messages API)
# ---------------------------------------------------------------------------

def _default_judge_fn(prompt: str, model: str = DEFAULT_JUDGE_MODEL) -> str:
    """Call the Anthropic Messages API via stdlib urllib.

    Requires ANTHROPIC_API_KEY in the environment.
    Zero external deps (stdlib only).

    Raises RuntimeError if the API key is absent or the request fails.
    """
    import json
    import os
    import urllib.error
    import urllib.request

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — the support-matcher judge requires it. "
            "Set the env var or pass judge_fn= to mock the call."
        )

    payload = {
        "model": model,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        raise RuntimeError(
            f"Anthropic API error {e.code}: {body_bytes[:400]}"
        ) from e

    # Extract text from the response
    content = result.get("content", [])
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            return block["text"]
    return ""


# ---------------------------------------------------------------------------
# Core public callable — match_support()
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
    judge_model: str = DEFAULT_JUDGE_MODEL,
) -> SupportVerdict:
    """Assess whether a cited source backs a claim in the manuscript.

    This is the reusable callable — both the semantic gate in check_gates.py
    and the (A) naked-cite resolver in naked_cite.py call this function.

    Args:
        claim:           the manuscript sentence containing the \\cite{citekey}.
        citekey:         the BibTeX citekey being checked.
        note_path:       path to the literature/ OKF note for this source.
        stance:          optional stance: field from the note (for J-2 gate; None → skip).
        plan_role:       optional plan_role: field (for J-2 gate context; None → skip).
        rubric_override: optional complete rubric replacement (Ada's rubric drops in here).
        config:          optional Config for rubric lookup via [manuscript_support].
        judge_fn:        injectable LLM call (prompt: str) -> str. Defaults to
                         the urllib Anthropic API call. Pass a mock in tests.
        judge_model:     the model-id to log (D-MS-4 resolved: Opus-tier).

    Returns:
        SupportVerdict with verdict, verbatim_span, polarity, judge_model, prompt_hash.
        ABSENT is the safe default when the note cannot be read or the judge fails.

    BLOCK on [ABSENT] / [CONTRADICTS]; WARN on [PARTIAL].
    Log judge_model + prompt_hash to RunState.meta["support_matcher"] at the call site.

    sr: SR-MS-2
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
        )

    # Call the judge
    _judge = judge_fn if judge_fn is not None else _default_judge_fn
    try:
        raw_response = _judge(prompt)
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
