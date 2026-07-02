"""style.py — the `manuscript-style` config seam for Research Vault.

This is the per-section craft layer: default writing guidance for each section
node in the drafting-DAG (§5J.6). Adopters customize per venue (NeurIPS vs a
journal) by overriding entries — the plumbing calls it; the adopter owns what
it says. Directly parallel to figures' apply_style(preset, skin) seam.

The default `per_section_tips` dict bakes Ada's grounding craft (§5J.3c) so
section agents synthesize HONESTLY, not just fluently. Each tip is the `spec:`
payload string that the corresponding DAG node pulls in at dispatch time.

K-1 completeness gate (§5M / §5J.3c): the gather-scope tip instructs the agent
to emit a complete inclusion ledger covering the plan-master's `covers:` set
when a preregistration master is in scope. This is the EMISSION half — SR-MS-2
enforces it at `rv manuscript check`.

Stdlib only.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Section keys — the canonical ordered list of agent section nodes in the DAG.
# human-go gates (approve-thesis, approve-framing, approve-manuscript) are EXEMPT
# from per_section_tips (they are decision gates, not dispatch targets).
# ---------------------------------------------------------------------------

SECTION_KEYS: tuple[str, ...] = (
    "gather-scope",
    "related-work",
    "method",
    "experimental-setup",
    "results-discussion",
    "limitations",
    "conclusion",
    "introduction",
    "abstract",
    "appendix-repro",
    "assemble",
    "compile",
    "critic",
)

# ---------------------------------------------------------------------------
# Default per_section_tips (Ada-specced grounding craft, §5J.3c)
# ---------------------------------------------------------------------------

per_section_tips: dict[str, str] = {
    # ── gather-scope ────────────────────────────────────────────────────────
    # K-1 completeness gate EMISSION half: enumerate every findings/ note in/out
    # + reason. When a plan_kind: preregistration master is in --scope, ALSO
    # enumerate every plan_role: main child in that master's covers: set —
    # each included or explicitly excluded-with-reason. Graceful when absent.
    "gather-scope": (
        "Emit an INCLUSION LEDGER: for EVERY findings/ note available to this project, "
        "state whether it is INCLUDED (in scope for synthesis) or EXCLUDED, and give a "
        "one-line reason. Silent omission of any finding is the top integrity risk — every "
        "note must appear in one column or the other. Also carry each included finding's "
        "Confidence and Caveats fields forward into scope (these feed the limitations section). "
        "\n\n"
        "K-1 MANDATORY (when a preregistration master is in scope): if the --scope set "
        "includes an experiments note with plan_kind: preregistration, enumerate EVERY "
        "plan_role: main child listed in that master's covers: field — each child must be "
        "either INCLUDED in the synthesis scope or EXCLUDED with an explicit reason. "
        "A plan_role: main child that is silently absent from the ledger is a K-1 violation "
        "that rv manuscript check will BLOCK on in SR-MS-2. "
        "Graceful when no preregistration master is in scope: the standard inclusion ledger "
        "over findings/ is sufficient. "
        "\n\n"
        "Output format: a markdown table with columns | Note | Status | Reason |, followed "
        "by a one-sentence draft thesis and a proposed section outline."
    ),

    # ── related-work ────────────────────────────────────────────────────────
    "related-work": (
        "Synthesize the related work section. Anti-fabrication rules: "
        "(1) cite ONLY papers that are filed as literature/ notes in this project "
        "(the closed .bib; an unresolvable \\cite{key} will be a hard error at rv manuscript check); "
        "(2) for EVERY cited paper, state its relationship to our work — one of: "
        "EXTENDS | CONTRADICTS | ORTHOGONAL | BASELINE. Do not include a citation "
        "without a stated delta. "
        "(3) Draw relationships ONLY from the literature/ note's structured fields "
        "(TL;DR, metrics, findings, limitations) — never from memory or re-summarized text. "
        "Output: a related-work section in LaTeX prose that references \\cite{} keys "
        "from the project's refs.bib."
    ),

    # ── method ──────────────────────────────────────────────────────────────
    "method": (
        "Write the method section. Anti-fabrication rule: reconcile against "
        "results_commit — describe the code that ACTUALLY RAN, not the intended design. "
        "If the implementation diverges from the plan note, describe the implementation. "
        "Do not describe methodology that has no results_commit anchor. "
        "Output: a methods section in LaTeX prose, referencing the commit SHA where relevant."
    ),

    # ── experimental-setup ──────────────────────────────────────────────────
    "experimental-setup": (
        "Write the experimental setup section. Anti-fabrication rule (CRITICAL): "
        "write ONLY facts that are captured in the experiments/ and datasets/ notes "
        "(results_commit, datasets.location, datasets.hash, repro_* fields). "
        "If a seed, hyperparameter, or configuration detail is NOT recorded in the note's "
        "provenance fields, write exactly: 'not recorded in provenance' — NEVER fabricate "
        "a plausible value. A stated hole is honest; a guessed value is fabrication. "
        "The LLM must never fill in missing experimental details from general knowledge. "
        "Output: an experimental-setup section in LaTeX, citing dataset notes and "
        "experiment notes by their OKF ids."
    ),

    # ── results-discussion ──────────────────────────────────────────────────
    "results-discussion": (
        "Write the results and discussion section. Anti-fabrication rules: "
        "(1) EVERY quantitative result must be a LaTeX macro reference (e.g. \\resultAccHFS) "
        "injected from the hash-verified results.tex — the LLM MUST NOT type a digit. "
        "If no macro exists for a result, do not state that result. "
        "(2) No CI-interval or ablation claim without the corresponding logged artifact "
        "in an experiments/ note (results_location must be non-empty). "
        "(3) Apply the polarity/overreach check: a correlational result must not be framed "
        "as causal; a finding with confidence: low must not be framed as a robust result. "
        "Output: results and discussion in LaTeX prose using \\result* macros throughout."
    ),

    # ── limitations ─────────────────────────────────────────────────────────
    "limitations": (
        "Write the limitations section. Anti-fabrication rules: "
        "(1) SEED from the Caveats and Confidence fields of the in-scope findings/ notes "
        "(as enumerated in the gather-scope inclusion ledger) — harvest from the record, "
        "do not invent limitations that are not in the notes. "
        "(2) MANDATORY: every in-scope finding with confidence: low MUST appear in this "
        "section named by its finding id, even if it feels minor — its low confidence is "
        "a recorded fact. A silently-dropped caveat is the primary integrity risk. "
        "(3) Do not hedge findings that have confidence: high without a stated reason. "
        "Output: a limitations section in LaTeX, referencing finding OKF ids."
    ),

    # ── conclusion ──────────────────────────────────────────────────────────
    "conclusion": (
        "Write the conclusion section. Anti-fabrication rules: "
        "(1) Claims must be a STRICT SUBSET of the results in results-discussion.tex — "
        "do not introduce new claims or numbers in the conclusion. "
        "(2) Future work statements must not imply results that were not produced. "
        "Output: a conclusion in LaTeX prose. This section is written BEFORE introduction "
        "and abstract so that the framing can be checked against the full paper body."
    ),

    # ── introduction ────────────────────────────────────────────────────────
    "introduction": (
        "Write the introduction section (written LATE — after all body sections). "
        "Anti-fabrication rules: "
        "(1) Claims in the introduction must be a SUBSET of claims already present "
        "in the body sections (method, results-discussion, conclusion) that have been "
        "committed to sections/. Do not introduce a new claim here that lacks a body anchor. "
        "(2) The problem statement must be grounded in the literature/ notes (closed .bib). "
        "(3) The 'we show' / 'we establish' framing must match the confidence level of the "
        "underlying finding — a confidence: low finding requires qualified framing. "
        "Output: an introduction in LaTeX, written as the LAST body section before abstract."
    ),

    # ── abstract ────────────────────────────────────────────────────────────
    "abstract": (
        "Write the abstract (the LAST section written). "
        "Anti-fabrication rules: "
        "(1) Every claim in the abstract must trace to a sentence already in "
        "introduction.tex, results-discussion.tex, or conclusion.tex — the abstract "
        "is a SUBSET, not a summary from memory. "
        "(2) The support-matcher will run the abstract against the body after compile — "
        "an abstract claim with no body anchor will produce [ABSENT] in the critic pass. "
        "(3) State the thesis, key result (macro reference or qualified claim), and "
        "the primary limitation. Do not inflate. "
        "Output: a single-paragraph abstract in LaTeX."
    ),

    # ── appendix-repro ──────────────────────────────────────────────────────
    "appendix-repro": (
        "Write the reproducibility appendix. This section branches off Gate 1 "
        "(approve-thesis) and is populated ONLY from structured provenance fields — "
        "the LLM MUST NOT fill any field from memory or general knowledge. "
        "Inject ONLY from: results_wandb_run, results_commit, results_hash + sidecar, "
        "datasets.location + datasets.hash, the DAG manifest run_id, and SR-6 hardware "
        "fields (repro_hw). "
        "If a field is absent from the experiment note provenance, write the literal sentinel: "
        "'not-recorded-in-provenance' — never a guessed or plausible value. "
        "Seeds and hyperparameters come from repro_* fields (SR-EXP-REPRO) if available; "
        "if SR-EXP-REPRO has not landed, write 'not-recorded-in-provenance' for those rows. "
        "Output: a LaTeX appendix table of reproducibility fields. "
        "The appendix DOES NOT depend on approve-framing (Gate 2) — it runs in parallel."
    ),

    # ── assemble ────────────────────────────────────────────────────────────
    "assemble": (
        "Assemble the full manuscript. Join abstract.tex + appendix-repro.tex with the "
        "remaining body sections already written to sections/. Update main.tex to \\input "
        "all section files in the correct order. Verify that refs.bib is present and "
        "\\bibliography{refs} is in main.tex. "
        "Do NOT rewrite any section prose — this is a structural assembly pass only. "
        "Output: updated main.tex with \\input statements for every section file."
    ),

    # ── compile ─────────────────────────────────────────────────────────────
    "compile": (
        "Run `rv manuscript compile <id>` to execute the exec-guarded chktex + pdflatex "
        "fix-loop (bounded iterations). The compile verb is the only path to produce the "
        "PDF — do not call pdflatex directly. "
        "If pdflatex/chktex are absent, the verb exits with a friendly install message "
        "(never a crash). Surface the compile log if any errors remain after N iterations. "
        "On success, the manuscript note's manuscript_pdf and manuscript_hash fields "
        "are updated by rv manuscript compile."
    ),

    # ── critic ──────────────────────────────────────────────────────────────
    "critic": (
        "Perform a critical review of the compiled manuscript. Anti-positivity-bias rules: "
        "(1) Read the DISCONFIRMING interpretation first — actively seek where the paper "
        "overclaims, elides a caveat, or cites a source that does not support the claim. "
        "(2) Do NOT use the paper's own abstract/thesis as a prior — judge each claim "
        "against the cited literature/ note fields and the recorded results. "
        "(3) You MUST report your worst-three findings even if the draft looks good — "
        "a 'looks good' verdict is not a permitted output; manufacture a critique if needed. "
        "Gate semantics (SR-MS-2 will enforce programmatically): "
        "[SUPPORTS] | [PARTIAL] | [ABSENT] | [CONTRADICTS] per (sentence, citekey) pair; "
        "BLOCK on [ABSENT] / [CONTRADICTS], WARN on [PARTIAL]. "
        "Output: a critic report with (a) three or more findings, (b) bracketed verdicts "
        "on sampled (sentence, cite) pairs, (c) BLOCK/WARN counts."
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_section_tips(
    override: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return the per_section_tips dict, optionally merged with caller overrides.

    When to use: the DAG scaffolder calls this to embed per-section spec strings
    into each agent node. Adopters customize per venue by passing override={}.

    The default dict bakes Ada's grounding craft (§5J.3c); the override merges
    on top of defaults — only the keys you specify are replaced. This is the
    adopter-customizable seam directly parallel to figures' apply_style(preset, skin).

    Args:
        override: optional dict of section-key → tip string to merge on top of defaults.
                  Keys not present in override retain their defaults.

    Returns:
        A new dict (never mutates module-level per_section_tips).
    """
    tips = dict(per_section_tips)
    if override:
        tips.update(override)
    return tips
