# SPDX-License-Identifier: AGPL-3.0-or-later
"""plan/style.py — the plan_tips config seam (SR-PLAN-1, §5K.4).

SEAM CONTRACT
  ``get_plan_tips(config=None)`` is the call-point for the `plan` DAG node's
  spec/prompt.  The shipped default is the researcher's plan-prompt content
  (folded from §5K.4); adopters override per lab/venue via the ``[plan_style]``
  section in ``research_vault.toml``.

  Shape:
    plan_tips = {
        "main": "<str>",
        "supporting_ablations": "<str>",
        "conditional_ablations": "<str>",
        "diagnosis_table": "<str>",
        "grounding": "<str>",
        "freeze": "<str>",
        "exploratory": "<str>",
    }

  Every key must be present in the returned dict (adopter overrides may replace
  individual values but the key set is fixed).  ``get_plan_tips`` merges the
  adopter's ``[plan_style]`` section over the default so adopters only need to
  specify the keys they want to change.

Two halves are independently mergeable via this seam:
  - Engineer ships this module (SR-PLAN-1 plumbing PR).
  - The researcher's content is the default payload (already folded; §5K.4).
  Keep the ``get_plan_tips`` signature stable — it is the seam boundary.

Stdlib only.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Required key set (fixed — changing this is a breaking change)
# ---------------------------------------------------------------------------

PLAN_TIPS_KEYS: frozenset[str] = frozenset({
    "main",
    "supporting_ablations",
    "conditional_ablations",
    "diagnosis_table",
    "grounding",
    "freeze",
    "exploratory",
})

# ---------------------------------------------------------------------------
# Default payload — researcher's plan-prompt content (§5K.4)
# ---------------------------------------------------------------------------
# The architect owns the keys/shape; the researcher owns the prose.
# Each string is the prompt guidance for that section of the pre-registration.

_DEFAULT_PLAN_TIPS: dict[str, str] = {
    "main": (
        "State the headline claim as an EXACT ARROW: X → Y under condition Z. "
        "No near-neighbour paraphrase — the arrow names the specific manipulation and the specific outcome. "
        "Specify the pre-registered analysis: estimand, test statistic, comparison baseline, and units. "
        "State decision thresholds AND the measurement's own noise floor (split-half variance / seed variance). "
        "A threshold with no stated noise floor is not pre-registered (charter §10: a result 'too good' is "
        "an artifact until explained — the noise floor is the sanity bound). "
        "State the falsifier: the concrete result that would refute the claim. "
        "If nothing could refute it, it is not yet a claim — redesign the experiment."
    ),
    "supporting_ablations": (
        "Each supporting ablation ISOLATES EXACTLY ONE component. "
        "If two things move, it is two ablations or none — never combine. "
        "State a specific purpose tied to a main claim or confound being ruled out. "
        "No ablation without a named target. "
        "Refuse the near-neighbour: if the ablation and the main manipulation are paraphrases of each other, "
        "it is entailment, not evidence — redesign so the ablation and main are genuinely distinguishable. "
        "Every ablation requires a DIAGNOSIS TABLE: one row per outcome range, each row naming "
        "a conclusion and a committed action (e.g., 'hurts → component load-bearing; "
        "unchanged → mechanism does not run through it; reverses → main claim mis-specified'). "
        "No 'fallback' row and no empty cells — an empty branch means redesign the ablation."
    ),
    "conditional_ablations": (
        "Each conditional ablation freezes upfront BOTH: "
        "(1) the TRIGGER — an exact main-result condition using the main's pre-registered thresholds. "
        "A number, not a vibe. Example: 'main1_acc > 0.80 on the held-out split'. "
        "(2) its own DIAGNOSIS TABLE — every outcome named with a committed action, same completeness "
        "requirement as supporting ablations. "
        "The trigger and table are locked before any run; the only branching at evaluation time is "
        "'trigger fired / did not fire'. "
        "If the trigger did not fire, the conditional is recorded as blocked (pre-committed, "
        "trigger false, deliberately not run) — this is a reportable honest negative (charter §2), "
        "not a silent drop."
    ),
    "diagnosis_table": (
        "Required shape for EVERY diagnosis table in this plan: "
        "| Outcome range | Named conclusion | Committed action | "
        "The table must be EXHAUSTIVE over outcomes: every plausible result range has its own row "
        "with a named conclusion and a specific action. "
        "No 'fallback', 'TBD', or empty cells — an incomplete table is a lint FAIL. "
        "The named conclusion should state what the result implies for the claim "
        "(not just a label like 'good' or 'bad'). "
        "The committed action should specify the next step (write-up, re-run, reject claim, etc.)."
    ),
    "grounding": (
        "Every planned run — main, supporting ablation, conditional ablation — NAMES the citable "
        "artifact it will produce: run id, CSV/JSONL file path, figure id, and the expected SHA. "
        "No ablation claim without an ablation run. "
        "No run without a named artifact. "
        "This is the anti-fabrication ground truth: the manuscript's result macros will read "
        "hash-verified experiment notes — if there is no named artifact here, there is no result there. "
        "Statistical tests: if the plan uses a Shapiro-gated t vs. Wilcoxon/signed-rank decision, "
        "note that scipy>=1.11 (the 'analysis' optional extra) provides these. "
        "For small-n cases a stdlib fallback exists: the Wilcoxon signed-rank statistic and its "
        "exact p-value can be computed without scipy using the sign-test or permutation-test "
        "(import statistics, itertools). "
        "Plans must NOT hard-depend on scipy being installed — state the fallback so the analysis "
        "is runnable in a zero-extra environment."
    ),
    "freeze": (
        "The CONFIRMATORY set — the N mains + their supporting ablations + their conditional "
        "ablations listed in this plan — is frozen at human-go-plan approval, before any run dispatches. "
        "No new CONFIRMATORY mains or ablations may be added post-hoc. "
        "A confirmatory revision requires a NEW pre-registration, not an in-place edit of this plan. "
        "Exploratory experiments (stance: exploratory) are always welcome AFTER the freeze — see 'exploratory'. "
        "The freeze covers only the confirmatory backbone; exploration stays fully open."
    ),
    "exploratory": (
        "Flexibility is a FIRST-CLASS DESIGN GOAL, not a threat to validity. "
        "Alongside the frozen confirmatory backbone, explore freely: design new experiments and "
        "ablations as findings emerge, each as a first-class run-bearing note. "
        "Label every exploratory note with stance: exploratory. "
        "The one rule: NEVER report an exploratory result as confirmatory. "
        "An exploratory note is outside the master covers: freeze-set by construction — "
        "adding it does not edit the frozen confirmatory set and trips no pre-registration teeth. "
        "Integrity comes from the honest label, not from freezing everything."
    ),
}


# ---------------------------------------------------------------------------
# Public seam
# ---------------------------------------------------------------------------

def get_plan_tips(config: Any = None) -> dict[str, str]:
    """Return the plan_tips dict, merging any adopter ``[plan_style]`` override.

    Args:
        config: a loaded Config instance (or None for the shipped default).
                If the config has a ``_raw`` attribute containing a ``[plan_style]``
                section, those key/value pairs are merged over the default.

    Returns:
        dict with exactly the keys in PLAN_TIPS_KEYS.
        Adopter values replace the corresponding default; unknown keys are dropped.

    Contract:
        - Always returns a dict with all PLAN_TIPS_KEYS present.
        - Adopter overrides cannot remove a key — they can only replace the value.
        - The default is the researcher's plan-prompt content (§5K.4); adopters own the prose.
    """
    tips: dict[str, str] = dict(_DEFAULT_PLAN_TIPS)

    if config is not None:
        raw = getattr(config, "_raw", {})
        override = raw.get("plan_style", {})
        if isinstance(override, dict):
            for key, value in override.items():
                if key in PLAN_TIPS_KEYS and isinstance(value, str):
                    tips[key] = value

    return tips
