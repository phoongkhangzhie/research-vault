"""style.py — the `manuscript-style` config seam for Research Vault.

This is the per-section craft layer: default writing guidance for each section
node in the drafting-DAG (§5J.6). Adopters customize per venue (NeurIPS vs a
journal) by overriding entries — the plumbing calls it; the adopter owns what
it says. Directly parallel to figures' apply_style(preset, skin) seam.

The default `per_section_tips` dict bakes Ada's grounding craft (§5J.3c) so
section agents synthesize HONESTLY, not just fluently. Each tip is the `spec:`
payload string that the corresponding DAG node pulls in at dispatch time.

`manuscript_style_preamble` is a module-level sibling: 7 voice/stance rules
(Ada-authored, §5J.6 fold-in part C) prepended to EVERY agent section node's
spec by the scaffolder. Grounded in Gopen & Swan (1990), Whitesides (2004),
Hyland (1998).

K-1 completeness gate (§5M / §5J.3c): the gather-scope tip instructs the agent
to emit a complete inclusion ledger covering the plan-master's `covers:` set
when a preregistration master is in scope. This is the EMISSION half — SR-MS-2
enforces it at `rv manuscript check`.

Stdlib only.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Section config — canonical section keys with status markers.
# Status: "required" | "optional" | "venue-optional"
# The scaffolder uses get_active_sections() to determine which to include.
# human-go gates (approve-thesis, approve-framing, approve-manuscript) are NOT
# in SECTION_KEYS — they are decision gates, not dispatch targets.
# ---------------------------------------------------------------------------

SECTION_STATUS: dict[str, str] = {
    "gather-scope": "required",
    "related-work": "required",
    "background": "optional",           # OPTIONAL: formalism/notation for Method
    "method": "required",
    "experimental-setup": "required",
    "results-discussion": "required",
    "limitations": "required",
    "ethics-impacts": "venue-optional",  # VENUE-OPTIONAL: after limitations
    "conclusion": "required",
    "introduction": "required",
    "abstract": "required",
    "appendix-repro": "required",
    "data-code-availability": "venue-optional",  # VENUE-OPTIONAL: near appendix
    "assemble": "required",
    "compile": "required",
    "critic": "required",
}

# Full ordered list of all possible section keys (including optional/venue-optional).
# The scaffolder selects a subset based on toggles (see get_active_sections()).
# per_section_tips must contain an entry for EVERY key listed here.
SECTION_KEYS: tuple[str, ...] = (
    "gather-scope",
    "related-work",
    "background",           # OPTIONAL — after related-work
    "method",
    "experimental-setup",
    "results-discussion",
    "limitations",
    "ethics-impacts",       # VENUE-OPTIONAL — after limitations
    "conclusion",
    "introduction",
    "abstract",
    "appendix-repro",
    "data-code-availability",  # VENUE-OPTIONAL — near appendix
    "assemble",
    "compile",
    "critic",
)


def get_active_sections(
    include_optional: bool = False,
    include_venue_optional: bool = False,
) -> tuple[str, ...]:
    """Return the section keys to include in the drafting-DAG manifest.

    When to use: called by the scaffolder (manuscript.cmd_new) to determine
    which agent section nodes to generate. The default (no flags) produces the
    13 required sections that, combined with the 3 human-go gates, yield the
    16-node canonical manifest (§5J.2).

    Args:
        include_optional: include sections marked "optional" (e.g. background).
        include_venue_optional: include sections marked "venue-optional"
            (e.g. ethics-impacts, data-code-availability).

    Returns:
        Ordered tuple of section keys to include.
    """
    active: list[str] = []
    for key in SECTION_KEYS:
        status = SECTION_STATUS.get(key, "required")
        if status == "required":
            active.append(key)
        elif status == "optional" and include_optional:
            active.append(key)
        elif status == "venue-optional" and include_venue_optional:
            active.append(key)
    return tuple(active)


# ---------------------------------------------------------------------------
# manuscript_style_preamble — 7 voice/stance rules (Ada-authored, §5J.6 part C)
#
# Prepended by the scaffolder to every agent section node's spec (NOT the
# human-go gates). Adopter-overridable via get_style_preamble(override=...).
#
# Grounding: Gopen & Swan (1990) "The Science of Scientific Writing";
# Whitesides (2004) "Whitesides' Group: Writing a Paper";
# Hyland (1998) metadiscourse + hedging taxonomy.
#
# Confidence enum note: the live OKF schema uses confidence as a free-text
# field typically populated as "low" or "high". The hedge-strength lexicon
# includes a "medium" tier that degrades gracefully when the field is binary
# (low/high) — map any non-high, non-low value to the medium tier.
# ---------------------------------------------------------------------------

manuscript_style_preamble: str = """\
MANUSCRIPT VOICE AND STANCE RULES (prepend to every section spec)

These 7 rules govern how you write every sentence in every section.
They are not stylistic suggestions — they are anti-fabrication constraints.

(1) OBJECTIVE/FRANK VOICE — compile a verified argument, do not advocate.
    Your job is to report what the evidence shows, not to persuade. If the
    evidence is weak, say so. A sentence that sounds confident but is not
    grounded is fabrication.

(2) TENSE DISCIPLINE:
    PAST tense for what you/the experiment did and found:
      "We trained the model on ..."  "The model achieved ..."
    PRESENT tense for standing facts and what a figure/table shows:
      "Figure 2 shows ..."  "The accuracy metric is ..."
    Do not mix: a finding from this study is past; a law of physics is present.

(3) ACTIVE FOR OUR CLAIMS, PASSIVE ONLY WHEN ACTOR IS IRRELEVANT:
    "We found that ..."  (not "It was found that ...")
    "The baseline failed to ..."  (not "It was observed to fail ...")
    Reserve passive for methods steps where the actor is genuinely irrelevant:
    "Data were collected at three sites."

(4) CALIBRATED HEDGING — claim verb bound to the source finding's confidence:
    The stance-strength ceiling is the confidence level of the finding note.
    Hedge-strength lexicon (bind each claim verb to the appropriate tier):
      LOW confidence  → "suggests" / "is consistent with" / "may indicate"
                        / "we observe a trend that" / "it appears that"
      MEDIUM confidence → "indicates" / "supports" / "provides evidence for"
                          / "we find" / "results show"
      HIGH confidence → "demonstrates" / "establishes" / "we show" /
                        "confirms" / "proves"
    Causal-claim guard: never use a causation verb (causes/drives/leads to)
    for a correlational finding, regardless of confidence. Use "is associated
    with" or "co-occurs with" for correlations.
    Confidence degradation note: if the confidence field is binary (low/high),
    map absent-but-plausible values to the medium tier.

(5) READER-EXPECTATION STRUCTURE (Gopen & Swan):
    (a) Place the key claim in the STRESS POSITION (end of sentence/clause).
    (b) OLD information before NEW information in each sentence.
    (c) Keep subject and verb as close together as possible.
    Failing these rules buries the finding in syntactic noise.

(6) CONCISION + ONE TERM PER CONCEPT:
    Cut every word that does not carry information. Never use two terms for
    the same concept ("model" vs "system" vs "approach") — pick one and use
    it throughout. Nominalizations ("the performance of" → "performs") are
    almost always weaker.

(7) STRUCTURAL INTENT vs PROMPT-PLEADING:
    Your output is a LaTeX section file in sections/<section-name>.tex.
    Do NOT include meta-commentary ("In this section, we will show ...") or
    forward references unless the section type explicitly calls for them
    (only introduction and abstract may preview the paper's arc).
    Write the argument; let the structure speak.
"""


def get_style_preamble(override: str | None = None) -> str:
    """Return the manuscript_style_preamble string, optionally replaced by caller.

    When to use: the DAG scaffolder calls this to prepend the preamble to each
    agent section node's spec string. Adopters customize by passing override=<str>
    (complete replacement, not merge — the preamble is a coherent whole).

    Args:
        override: optional string to use in place of the default preamble.
                  When None, returns the module-level manuscript_style_preamble.

    Returns:
        The preamble string (default or override).
    """
    if override is not None:
        return override
    return manuscript_style_preamble


# ---------------------------------------------------------------------------
# Default per_section_tips (Ada-specced grounding craft, §5J.3c + fold-in parts A/B)
# Every key in SECTION_KEYS must have an entry here.
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

    # ── background ──────────────────────────────────────────────────────────
    # OPTIONAL section (after related-work). Ada-authored §5J fold-in (B).
    # Purpose: formalism/notation ONLY what Method uses; NO results.
    # Label what is ESTABLISHED (cite literature/ note) vs NOVEL problem-setting.
    "background": (
        "Write the background section (OPTIONAL — include only when Method requires "
        "substantial formalism or notation that the expected reader lacks). "
        "\n\n"
        "SCOPE RULES: "
        "(1) Introduce ONLY the formalism and notation that the Method section will "
        "directly use. Do not include background that is not referenced in Method. "
        "This section is for scaffolding the reader's frame, not for a literature survey "
        "(that belongs in related-work). "
        "(2) NO results here — any performance numbers or findings belong in "
        "results-discussion or limitations. "
        "(3) Label each concept as either: "
        "ESTABLISHED (cite the literature/ note with \\cite{key} — the concept is "
        "from prior work) or NOVEL PROBLEM-SETTING (a contribution of this work — "
        "the concept is introduced here). Never blend the two without the label. "
        "(4) Source: draw concepts from concepts/ and methods/ OKF notes. "
        "Do not introduce formalism not grounded in a concepts/ or methods/ note. "
        "Output: a background section in LaTeX with explicit ESTABLISHED/NOVEL labels "
        "on each introduced concept, with \\cite{} keys for all ESTABLISHED items."
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

    # ── ethics-impacts ──────────────────────────────────────────────────────
    # VENUE-OPTIONAL section (after limitations). Ada-authored §5J fold-in (B).
    # The integrity TWIN of Limitations: harms IF the work SUCCEEDS.
    "ethics-impacts": (
        "Write the ethics and broader impacts section (VENUE-OPTIONAL — required at "
        "some venues, e.g. NeurIPS; omit only when explicitly not required by the venue). "
        "\n\n"
        "This section is the integrity TWIN of limitations: where limitations discuss "
        "where the work may FAIL, ethics-impacts discusses harms IF the work SUCCEEDS. "
        "\n\n"
        "SCOPE RULES: "
        "(1) Ground EACH claimed harm in the actual findings and method of THIS paper — "
        "not in general AI ethics boilerplate. Test: could this sentence paste unchanged "
        "into a paper on a completely different topic? If yes, cut it — it is generic "
        "filler, not a grounded ethical analysis. "
        "(2) Cover the following categories WHERE THEY GENUINELY APPLY to this work: "
        "malicious-use / dual-use (could the capability or artifact be misused?); "
        "fairness / representation (are subpopulations harmed by errors, exclusions, "
        "or training-data skew?); privacy (does the method or data expose individuals?). "
        "If a category does not apply, state that explicitly — 'This work does not "
        "involve personal data.' — rather than omitting the category silently. "
        "(3) HONESTY IS NOT PENALIZED: if the method has genuine dual-use risk, "
        "state it. A section that only describes non-existent risks is less credible "
        "than one that honestly acknowledges real ones. "
        "(4) Do not forecast or speculate about downstream societal effects that are "
        "not grounded in the paper's actual scope and findings. "
        "Output: an ethics and broader impacts section in LaTeX, with each claimed "
        "harm or risk explicitly anchored to a specific finding, model behavior, or "
        "dataset property described in this paper."
    ),

    # ── conclusion ──────────────────────────────────────────────────────────
    # Ada-augmented (§5J fold-in B): Future Work first-class + claim-subset rule.
    "conclusion": (
        "Write the conclusion section. Anti-fabrication rules: "
        "(1) CLAIM SUBSET: every claim in the conclusion must be a strict subset of "
        "results already stated in results-discussion.tex — do not introduce new claims "
        "or numbers. The conclusion is a synthesis, not a discovery. "
        "(2) FUTURE WORK IS FIRST-CLASS (not a throwaway paragraph): "
        "Source future directions from two grounded channels only — "
        "(a) the Caveats / Open fields of in-scope findings/ notes (the honest holes "
        "the researcher recorded during the study), and "
        "(b) gaps proven open in the related-work section (literature/ notes where "
        "the relationship field is ORTHOGONAL or CONTRADICTS and the gap is structural). "
        "Distinguish clearly between INCREMENTAL next steps (direct extensions of this "
        "work, achievable with the same apparatus) and GENUINE OPEN PROBLEMS (questions "
        "that would require a new methodology or dataset to resolve). "
        "(3) NEVER phrase a future direction as an in-progress or established result. "
        "'We are currently ...' and 'Our ongoing work shows ...' are prohibited unless "
        "backed by a separate experiments/ note with results_hash filled in. "
        "(4) Future directions are NOT CONCLUSIONS — do not claim you have demonstrated "
        "something you have only proposed to investigate. "
        "Output: a conclusion section in LaTeX. Write this section BEFORE introduction "
        "and abstract so the framing can be verified against the full paper body."
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

    # ── data-code-availability ──────────────────────────────────────────────
    # VENUE-OPTIONAL section (near appendix). Ada-authored §5J fold-in (B).
    # A ROADMAP into the appendix-repro table; structurally cross-checkable.
    "data-code-availability": (
        "Write the data and code availability section (VENUE-OPTIONAL — include when "
        "required by the venue or when data/code is released publicly). "
        "\n\n"
        "This section is a ROADMAP pointing INTO the appendix-repro table — "
        "do NOT restate hashes, commits, or artifact paths here; point to the table. "
        "\n\n"
        "SCOPE RULES: "
        "(1) Be HONEST about what IS and IS NOT released. Never write 'fully available' "
        "by default. Derive the availability claim from the populated vs "
        "'not-recorded-in-provenance' fields in the appendix-repro table: "
        "if a hash or location field is the sentinel, that artifact is NOT verifiably "
        "released — say so. "
        "(2) For each asset category (code, models, datasets, trained weights, "
        "evaluation scripts), state explicitly: RELEASED (with pointer to appendix "
        "row), RESTRICTED (reason), or NOT APPLICABLE. "
        "(3) Include ACCESS INSTRUCTIONS for each released asset: DOI, repository URL, "
        "license, and any registration requirement. Do not assume the reader can find "
        "the asset from a repository name alone. "
        "(4) STRUCTURAL CROSS-CHECK: rv manuscript check (SR-MS-2) will detect a "
        "'fully available' or 'open access' claim here that is contradicted by a "
        "'not-recorded-in-provenance' sentinel in the appendix-repro table. "
        "Write claims that can survive that check. "
        "Output: a data and code availability section in LaTeX, with explicit "
        "per-asset availability status and access instructions."
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
