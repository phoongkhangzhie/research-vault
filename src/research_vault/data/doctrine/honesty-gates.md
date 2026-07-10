# Honesty Gates — doctrine for LLM-judged research integrity checks

Harvested from `manuscript/support_matcher.py`, `manuscript/coldread.py`, and
`manuscript/review_board.py` before those modules were removed.
The craft here is reusable: any LLM-judged gate in research-vault should apply
these principles.

## 1. Anti-anchoring — the load-bearing constraint

**Rule:** A judge may only use what is explicitly present in the supplied input.
External knowledge — "everyone in this area knows…", "the authors surely
checked…", "that paper is famous for…" — is **forbidden as resolution**. If a
reference, term, or claim cannot be resolved from the supplied text, it does not
resolve. Period.

**Why it matters:** Anchoring is the dominant silent failure of automated
reviewers. A judge that fills gaps from background knowledge produces
*false confidence*, not a genuine integrity check. The gate exists precisely
to catch what background knowledge would wave through.

**Implementation pattern:**
- Open the rubric with an explicit anti-anchoring constraint (C1) naming the
  forbidden sources.
- Add a "STOP" trigger: "If you catch yourself thinking '…everyone in this area
  knows that X is…' — STOP: that is the anchoring this gate exists to catch."
- Scope all input slots (`{CLAIM}`, `{NOTE_CONTENT}`, `{PDF_TEXT}`) to exactly
  the evidence the judge may use; never leak project context into the prompt.

---

## 2. Disconfirm-first — the mandatory adversarial sweep

**Rule:** Before extending any positive verdict, the judge MUST first write the
strongest disconfirming observation it can construct. The disconfirm step is
**mandatory and first** — skipping it invalidates the judgment.

**Why it matters:** Positivity bias is the default failure mode of LLM judges.
A judge that looks for support before looking for problems will systematically
over-confirm. Disconfirm-first structurally prevents this by requiring the judge
to be an adversary before it is an advocate.

**Implementation pattern:**
- Label the disconfirming step STEP 1 in the procedure, with the word "mandatory,
  first" in the instruction.
- Provide a priority-ordered checklist of disconfirming shapes to hunt (e.g.,
  opposite direction → narrowing → modality mismatch → scope mismatch → silence).
- Require the STEP 1 output to appear in the machine-parseable block
  (`DISCONFIRM: <observation>`) so the gate can verify it was done.
- When a disconfirming finding AND a supporting span both exist, the disconfirming
  one **caps** the verdict: support that survives a caveat is at best PARTIAL;
  support contradicted outright is CONTRADICTS.

---

## 3. Verbatim-span-or-BLOCK — the evidence anchor

**Rule:** Any verdict other than the null-verdict (ABSENT / DANGLING) MUST quote
an EXACT, character-for-character span copied from the supplied input. No
paraphrase, no stitching, no ellipsis-bridging distant clauses.

**Why it matters:** "Can't quote → can't confirm" makes the null verdict the safe
default. A judge that wants to paraphrase to make a fit work is telling you the
support is a vibe, not a fact. The verbatim span is what makes a verdict auditable
and fail-loud rather than a rubber stamp.

**Corollary — ABSENT is the safe default:** When the judge cannot find a quotable
span, ABSENT (or the equivalent null-verdict) is mandatory. Being unable to quote
is itself the evidence that support is absent.

**Implementation pattern:**
- State the verbatim-span requirement as a hard constraint (C2) with the phrase
  "or it didn't happen."
- Make the output format require a `SPAN: "..."` field (or equivalent) for every
  non-null verdict. The parser should treat an absent or paraphrased span as
  evidence of a null-verdict, not as a warning.
- For a [BLOCK]-producing verdict (ABSENT, DANGLING), having no span is fine —
  it is the expected form. For positive verdicts, no span = downgrade to null.

---

## 4. Blind-judge canary — calibration before trusting any verdict

**Rule:** Before trusting any round of judge verdicts, run at least one synthetic
known-outcome probe through the **same** judge_fn + rubric pipeline. A probe that
returns the wrong verdict means the judge is broken for that input distribution —
**ABORT**, do not surface the round's verdicts as real.

**Bidirectional calibration (stronger):**
- **(a) Known-positive probe** — a synthetic input designed to earn the highest
  positive verdict (e.g., SUPPORTS, STANDS-ALONE). If the judge rejects it →
  the judge is TRIGGER-HAPPY → ABORT.
- **(b) Known-negative probe** — a synthetic input designed to earn a BLOCK verdict
  (e.g., ABSENT, DANGLING). If the judge passes it → the judge is BLIND →
  RUBBER-STAMPING → ABORT.

Both canaries must pass before any real verdict is trusted.

**Why it matters:** An LLM judge calibrated on the wrong distribution, or confused
by a rubric formatting issue, or calling a wrong model tier, can silently produce
the wrong verdict on every real input. The canary catches this before any verdict
is banked. A judge that never fails the canary is not being tested; calibrate the
probes so they are genuinely at the edges of the expected behavior.

**Implementation pattern:**
- Run canaries at the start of `check_*_tally()` (or equivalent entry point),
  not as a separate pre-flight step that can be bypassed.
- Canary probes should exercise the full pipeline: extractor → note-reader →
  judge → verdict-parser. A canary that mocks the judge doesn't test calibration.
- Return `{"canary_aborted": True, "errors": [...]}` on canary failure — never
  surface partial verdicts from a broken judge as if they were real.
- Always include `"canary_aborted": False` in the normal return path so callers
  can unconditionally check `result.get("canary_aborted")`.
- Skip canaries when `rubric == ""` (backward-compat with unit tests that don't
  wire a judge) — but document the skip path and test it.

---

## 5. Fail-closed defaults — silence is not certification

**Rule:** An unscoreable dimension, a missing field, or a parse failure is NOT a
pass. Default to the blocking/null end of the scale. Specifically:
- A dimension that cannot be scored defaults to the floor-failing score (not the
  floor itself — below it).
- A verdict that cannot be parsed defaults to the null-verdict (ABSENT, DANGLING).
- A judge call that fails (network, missing key, timeout) defaults to ABSENT — the
  gate BLOCKs, not passes.

**Why it matters:** Fail-open defaults are how integrity gates silently stop doing
their job. A gate that passes on exception is indistinguishable from no gate at
all once the infrastructure has a bad day.

**Implementation pattern:**
- Document the fail-closed default explicitly in the module's `SCOPE AND HONEST
  BOUNDARY` section.
- In the parser, treat unknown bracket tokens as the null-verdict, not as a parse
  error that propagates as None.
- In the judge caller, catch all exceptions and return the null-verdict with an
  error note — never raise through to the caller as an unhandled exception that
  the caller silently ignores.

---

## 6. Scope extraction — prevent rubric contamination of judgment

**Rule:** When a calibration mock (or the judge parser) needs to detect signals
in the prompt, scope the detection to the **input content sections only** (the
`=== CLAIM ===` / `=== CITED SOURCE ===` / `{PDF_TEXT}` blocks), NOT the full
prompt string.

**Why it matters:** Rubric text contains instructional examples ("e.g., 'proves',
'establishes'", "e.g., ABSENT") that false-trigger signal detectors if the full
prompt string is searched. The rubric is the judge's instruction manual, not
evidence — never let it contaminate verdict logic.

**Implementation pattern:**
- Delimit input slots with explicit markers (`=== CLAIM ===`, `=== CITED SOURCE ===`)
  appended by the prompt-builder, not just `{CLAIM}` substitution.
- In the parser and any mock, parse from markers forward; stop at the next marker
  or end-of-block. Never search `full_prompt` for verdict signals.
- Apply signal checks (`_CONFIRM`, `_CORREL`, `_HEDGE`) to extracted content
  strings, not the prompt string.

---

## 7. Stem matching for morphological variants

**Rule:** When detecting vocabulary signals via regex (e.g., correlational language,
hedging), use **stem patterns with `\w*`** suffix rather than whole-word boundary
`\b` at the end of partial stems.

**Example:** Use `r"\b(correlat\w*|associat\w*)"` — NOT `r"\b(correlat|associat)\b"`.
The trailing `\b` prevents matching "correlation" because the word boundary fails
after "correlat" (since "i" follows). Stems without trailing `\b` correctly match
"correlates", "correlation", "associated", "associational".

---

## References

These principles were first implemented and tested in:
- `manuscript/support_matcher.py` — 4-verdict claim→source matcher
- `manuscript/coldread.py` — self-containment judge with Flag-A
- `manuscript/review_board.py` — 7-dim review-board scorer

The modules were removed in the figure + manuscript loop removal.
The craft lives here, available for any future gate that calls an LLM judge in
a research integrity context.
