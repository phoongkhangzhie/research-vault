"""review/style.py — the review_tips config seam (SR-LR-1, §5L.6).

SEAM CONTRACT
  ``get_review_tips(config=None)`` is the call-point for the review DAG nodes'
  spec/prompt.  The shipped default is engineer-drafted, design-faithful prose (the
  saturation loop, counter-position/L-2 gate, disconfirming obligation) PENDING
  Ada's retrieval-grounded authored strings (follow-up);
  adopters override per lab/venue via the ``[review_style]`` section in
  ``research_vault.toml``.

  Shape:
    review_tips = {
        "review_scope_tips":      "<str>",
        "review_search_tips":     "<str>",
        "review_snowball_tips":   "<str>",
        "per_paper_relate_tips":  "<str>",
        "review_synthesize_tips": "<str>",
        "review_critic_tips":     "<str>",
    }

  Every key must be present in the returned dict (adopter overrides may replace
  individual values but the key set is fixed).  ``get_review_tips`` merges the
  adopter's ``[review_style]`` section over the default so adopters only need to
  specify the keys they want to change.

  ``get_review_style_preamble(config=None)`` returns the preamble injected before
  every node spec string — adopted via ``[review_style] preamble = "..."`` in
  ``research_vault.toml``.

Two halves independently mergeable:
  - Engineer ships this module (SR-LR-1 plumbing).
  - Ada replaces the default payload with her retrieval-grounded �5L.6 strings (follow-up).
  Keep ``get_review_tips`` / ``get_review_style_preamble`` signatures stable.

Stdlib only.
sr: SR-LR-1
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Required key set (fixed — changing this is a breaking change)
# ---------------------------------------------------------------------------

REVIEW_TIPS_KEYS: frozenset[str] = frozenset({
    "review_scope_tips",
    "review_search_tips",
    "review_snowball_tips",
    "per_paper_relate_tips",
    "review_synthesize_tips",
    "review_critic_tips",
})

# ---------------------------------------------------------------------------
# Default style preamble
# ---------------------------------------------------------------------------

_DEFAULT_PREAMBLE: str = (
    "You are conducting a structured, pre-registered, saturation-gated literature review "
    "following the Research Vault SR-LR-1 protocol (§5L).\n"
    "Anti-fabrication spine: every claim must trace to a citekey in the corpus; "
    "every citekey must resolve to a `literature/` OKF note; "
    "no invented references, no paraphrased-without-citation claims.\n"
    "Anti-fishing spine: the scope question, inclusion/exclusion criteria, and "
    "counter-position are frozen in `_protocol.md` BEFORE any search executes; "
    "do not widen the scope after seeing results.\n"
    "Disconfirming obligation: the `counter-position` field is REQUIRED and must be "
    "actively sought — a review that only confirms its hypothesis is fishing."
)

# ---------------------------------------------------------------------------
# Default payload — Ada's review-prompt content (§5L.6)
# ---------------------------------------------------------------------------
# The Architect owns the keys/shape; Ada owns the prose.
# Each string is the prompt guidance for that node of the review DAG.

_DEFAULT_REVIEW_TIPS: dict[str, str] = {
    "review_scope_tips": (
        "Freeze the review question, seed queries, inclusion/exclusion criteria, "
        "coverage claim, AND the counter-position BEFORE any search.\n\n"
        "Required `_protocol.md` fields (all REQUIRED — absence blocks search):\n"
        "  - `question`: the exact research question in one sentence.\n"
        "  - `seed_queries`: 3–8 Semantic Scholar query strings covering the question.\n"
        "  - `inclusion`: criteria a paper must satisfy (population, method, outcome).\n"
        "  - `exclusion`: criteria that disqualify a paper.\n"
        "  - `coverage_claim`: what a COMPLETE corpus would contain "
        "(e.g. 'all English papers 2015–2025 on X in venues Y').\n"
        "  - `counter-position` (REQUIRED — L-2 structural gate, §5L.3/§5M): "
        "the literature that would REFUTE the coverage claim — name the specific "
        "sub-literature or opposing view that must be actively sought. "
        "A review with an empty or missing `counter-position` cannot pass the "
        "coverage gate. This is the review's disconfirming obligation made structural.\n\n"
        "Anti-fishing: the protocol is a CONTRACT. Do not adjust inclusion/exclusion "
        "after seeing results. A protocol revision requires a new `review-scope` run "
        "and a new `approve-protocol` gate."
    ),
    "review_search_tips": (
        "Execute search using the frozen protocol from `_protocol.md`. "
        "Do not modify the inclusion/exclusion criteria seen in the protocol.\n\n"
        "Search discipline:\n"
        "  - Run each seed query from the protocol's `seed_queries` list via "
        "`rv research find <query>` (or `--deep` for a richer result set).\n"
        "  - Annotate every result: `[NEW]` (not in corpus) or `[IN-CORPUS:<citekey>]` "
        "(already filed). Use `rv research find --project <slug>` so the annotation "
        "is driven by the real corpus index.\n"
        "  - Apply inclusion/exclusion from the protocol. Record each excluded paper "
        "with the criterion that excluded it (audit trail).\n"
        "  - Breadth before precision: start with broad queries, then narrow.\n"
        "  - Record all hits (not just accepted papers) so saturation is measurable.\n\n"
        "Output: a `_search_hits.md` log of every query + its result count + annotations."
    ),
    "review_snowball_tips": (
        "Run the saturation loop INSIDE this node (§5L.2). "
        "The loop is INTERNAL — do NOT create new DAG nodes per round; "
        "this is a bounded walk over the citation graph, not a DAG cycle.\n\n"
        "Each round:\n"
        "  1. Take the frontier of accepted `[NEW]` citekeys from the previous round "
        "(seed: the accepted papers from `review-search`).\n"
        "  2. For each frontier paper, run BOTH directions:\n"
        "     - Forward: `rv research cited-by <paper-id>` — who cites this paper.\n"
        "     - Backward: `rv research references <paper-id>` — what this paper cites.\n"
        "  3. Annotate each result via `_corpus_annotation` (imported from "
        "`research_vault.research`) — `[NEW]` papers enter the frontier; "
        "`[IN-CORPUS:*]` are already in-corpus and skipped.\n"
        "  4. Lightweight concept-tag each `[NEW]` paper: which `concepts/` or `mocs/` "
        "regions does its abstract touch? (cheap signal; verified edges come later in "
        "the `relate-<key>` fan-out).\n"
        "  5. Apply inclusion/exclusion from the protocol; exclude non-matching papers.\n\n"
        "STOP when 2 CONSECUTIVE rounds yield:\n"
        "  - 0 new `[NEW]` citekeys (forward + backward combined), AND\n"
        "  - 0 new concept-tags.\n\n"
        "Direction-starvation check: if backward citations are consistently 0 while "
        "forward are positive (or vice versa), the frontier may be direction-starved — "
        "flag this in the saturation curve as a premature-plateau risk.\n\n"
        "Emit TWO artifacts:\n"
        "  `_corpus.md`: the frozen `[NEW]` citekey list (table: annotation | citekey | title).\n"
        "  `_saturation.md`: the saturation curve — a table of "
        "(round, new_citekeys_forward, new_citekeys_backward, new_concept_tags, "
        "cumulative_corpus) showing the plateau. If a direction is dry while the other "
        "is active, annotate the row as `DIRECTION-STARVED`.\n\n"
        "The `_corpus.md` and `_saturation.md` are the phase-boundary artifacts: "
        "the `coverage-gate` human-go reads them before authorizing Phase-2."
    ),
    "per_paper_relate_tips": (
        "Distill this paper into an OKF `literature/<citekey>.md` note.\n\n"
        "Required note fields (flat frontmatter):\n"
        "  - `type`: literature\n"
        "  - `citekey`: the paper's citekey (matches corpus)\n"
        "  - `title`: exact title\n"
        "  - `year`: publication year\n"
        "  - `authors`: first author et al.\n"
        "  - `venue`: journal/conference\n"
        "  - `claim`: ONE-SENTENCE summary of the paper's central claim\n"
        "  - `method`: the method used to support the claim\n"
        "  - `evidence`: what evidence/result they present\n"
        "  - `stance`: how it relates to the review question "
        "(supporting / opposing / tangential / methodological)\n"
        "  - `concepts`: comma-separated concepts/ or mocs/ regions this touches\n\n"
        "Verified concept-edges (body of the note):\n"
        "  - Draw edges ONLY from the note fields above — never invented.\n"
        "  - Format: `[SUPPORTS] concepts/<c>.md — <one sentence why>`\n"
        "  - Format: `[CONTRADICTS] concepts/<c>.md — <one sentence why>`\n"
        "  - Format: `[PARTIAL] concepts/<c>.md — <one sentence why>`\n"
        "  - A `[CONTRADICTS]` edge is equally valuable to a `[SUPPORTS]` edge — "
        "the disconfirming obligation applies here too.\n\n"
        "reads: — you have access to:\n"
        "  - the paper itself (abstract + key sections)\n"
        "  - `concepts/` directory (existing concept nodes)\n"
        "  - `mocs/` directory (existing maps of content)\n"
        "Do not invent concept nodes that don't exist in `concepts/`; "
        "flag missing concepts as TODOs in the note body."
    ),
    "review_synthesize_tips": (
        "Synthesize the full corpus (all `literature/<key>.md` notes from Phase-2) "
        "into the review's conceptual map.\n\n"
        "Outputs:\n"
        "  1. `concepts/<c>.md` updates — for each concept touched by 2+ papers, "
        "ensure a concept note exists and its incoming-edge list is current. "
        "Add concept notes if missing (they are OKF type `concepts`).\n"
        "  2. `mocs/<region>.md` updates — map-of-content notes summarizing which "
        "papers populate each sub-region. An MOC entry: "
        "`- [citekey] <claim> (<stance>)`.\n\n"
        "Orphan-avoidance:\n"
        "  - Every `literature/<key>.md` note must appear in at least one MOC region.\n"
        "  - Flag orphan notes (no MOC entry) as soft warnings — do not block, but list them.\n\n"
        "Coverage claim cross-check:\n"
        "  - Compare the corpus against the `coverage_claim` from `_protocol.md`.\n"
        "  - Note any regions of the claim that are thin (few papers) vs dense.\n\n"
        "The synthesis is the input to `review-coverage-critic`."
    ),
    "review_critic_tips": (
        "You are the coverage critic (Argus role). You are a REJECTS-ONLY reviewer: "
        "a `[PASS]` does NOT certify coverage, it only fails to find a blocking hole.\n\n"
        "Judge FOUR axes (each can independently issue `[BLOCK]`):\n\n"
        "1. SATURATION PLATEAU — is it real or premature? (§5L.2)\n"
        "   Read the `_saturation.md` curve. Check:\n"
        "   - Did the curve plateau at round K with 0 new citekeys AND 0 new concept-tags "
        "for 2 consecutive rounds? (genuine saturation)\n"
        "   - OR did it plateau while one direction (forward OR backward) stayed dry? "
        "(direction-starved — flag as `DIRECTION-STARVED` and issue `[BLOCK]`)\n"
        "   - OR did verified concept-edges (from `relate-<key>` notes) consistently "
        "outrun the cheap concept-tags? (tag-under-counting — issue `[BLOCK]`)\n"
        "   A plateau reached with direction-starvation or tag-under-counting is PREMATURE.\n\n"
        "2. ORPHAN CONCEPTS/MOCS — soft flag (do not block, but list)\n"
        "   Any `literature/<key>.md` note not appearing in any MOC region is an orphan.\n"
        "   Report orphan count and keys. Issue a soft warning, not a `[BLOCK]`.\n\n"
        "3. PROTOCOL ADHERENCE — did the corpus honor the frozen criteria?\n"
        "   Compare the accepted corpus against `_protocol.md` inclusion/exclusion.\n"
        "   Any paper included that violates inclusion criteria = fishing = `[BLOCK]`.\n"
        "   Any paper excluded that meets inclusion criteria = coverage gap = `[BLOCK]`.\n\n"
        "4. COUNTER-POSITION (L-2 gate — REQUIRED, §5L.3/§5M)\n"
        "   The `_protocol.md` MUST have a non-empty `counter-position` field.\n"
        "   - Missing or empty `counter-position` → `[BLOCK]` (hard structural gate).\n"
        "   - Non-empty `counter-position` but corpus contains ZERO papers from the "
        "declared opposing sub-literature → `[BLOCK]` (sought-not-just-present).\n"
        "   A `counter-position` that was declared but not actively sought is fishing "
        "in reverse — a confirming-only review dressed as balanced.\n\n"
        "Output format (use EXACTLY this bracket convention):\n"
        "  `[PASS]` — no blocking holes found (not a certification).\n"
        "  `[BLOCK]` — one or more blocking holes found; list each:\n"
        "    - DIRECTION-STARVED plateau (axis 1)\n"
        "    - TAG-UNDER-COUNTING plateau (axis 1)\n"
        "    - PROTOCOL-DRIFT (axis 3)\n"
        "    - COUNTER-POSITION ABSENT (axis 4 — hard block)\n"
        "    - COUNTER-POSITION NOT SOUGHT (axis 4 — hard block)\n\n"
        "Honest output template:\n"
        "  '[PASS/BLOCK]: N papers, R rounds, plateau at round K; "
        "j orphan concepts (soft); counter-position: sought/absent; k BLOCK(s).'\n"
        "Never write 'coverage verified' — you are a rejects-only screen."
    ),
}


# ---------------------------------------------------------------------------
# Public seam
# ---------------------------------------------------------------------------

def get_review_tips(config: Any = None) -> dict[str, str]:
    """Return the review_tips dict, merging any adopter ``[review_style]`` override.

    Args:
        config: a loaded Config instance (or None for the shipped default).
                If the config has a ``_raw`` attribute containing a ``[review_style]``
                section, those key/value pairs are merged over the default.

    Returns:
        dict with exactly the keys in REVIEW_TIPS_KEYS.
        Adopter values replace the corresponding default; unknown keys are dropped.

    Contract:
        - Always returns a dict with all REVIEW_TIPS_KEYS present.
        - Adopter overrides cannot remove a key — they can only replace the value.
        - The default is Ada's review-prompt content (§5L.6); adopters own the prose.

    sr: SR-LR-1
    """
    tips: dict[str, str] = dict(_DEFAULT_REVIEW_TIPS)

    if config is not None:
        raw = getattr(config, "_raw", {})
        override = raw.get("review_style", {})
        if isinstance(override, dict):
            for key, value in override.items():
                if key in REVIEW_TIPS_KEYS and isinstance(value, str):
                    tips[key] = value

    return tips


def get_review_style_preamble(config: Any = None) -> str:
    """Return the review style preamble, merged with any adopter override.

    Args:
        config: a loaded Config instance (or None for the shipped default).
                If the config has ``[review_style] preamble = "..."`` it is used.

    Returns:
        The preamble string injected before every node's spec.

    sr: SR-LR-1
    """
    if config is not None:
        raw = getattr(config, "_raw", {})
        override = raw.get("review_style", {})
        if isinstance(override, dict):
            preamble = override.get("preamble")
            if isinstance(preamble, str) and preamble.strip():
                return preamble
    return _DEFAULT_PREAMBLE
