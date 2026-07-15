# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/style.py — the review_tips config seam.

SEAM CONTRACT
  ``get_review_tips(config=None)`` is the call-point for the review DAG nodes'
  spec/prompt.  The shipped default is the researcher's retrieval-grounded prose:
  the citation-neighbor relevance walk (0.3.1), counter-position/L-2 gate, and
  disconfirming obligation, each anchored to the systematic-review methodology it
  operationalizes (protocol pre-registration, both-direction citation-neighbor
  walking, concept-centric synthesis).  Adopters override per lab/venue via the
  ``[review_style]`` section in ``research_vault.toml``.  Method anchors are
  attributed inline to their sources; a consolidated design-references
  bibliography is compiled at publish.

  Shape:
    review_tips = {
        "review_scope_tips":      "<str>",
        "review_screen_tips":     "<str>",
        "review_curate_tips":     "<str>",
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

  ``get_relevance_hops(config=None)`` returns the DEPTH BOUND (in relevance
  hops) for the review-snowball citation-neighbor walk — corpus = the vetted
  core (``review-screen`` output) plus its immediate citation neighborhood.
  Adopted via ``[review_style] relevance_hops = <int>`` in
  ``research_vault.toml``; default 1.  Recall is owned by the SEARCH (broad
  facet queries); precision is owned by this 1-hop bound + ``review-screen``.
  Deeper (2+) is a deliberate recall/precision tradeoff an adopter can opt
  into, never the default.

Two halves independently mergeable:
  - The module plumbing (this file).
  - The default payload — the retrieval-grounded prose strings.
  Keep ``get_review_tips`` / ``get_review_style_preamble`` signatures stable.

Stdlib only.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Required key set (fixed — changing this is a breaking change)
# ---------------------------------------------------------------------------

REVIEW_TIPS_KEYS: frozenset[str] = frozenset({
    "review_scope_tips",
    "review_screen_tips",
    "review_curate_tips",
    "review_relevance_verify_tips",
    "per_paper_relate_tips",
    "review_synthesize_tips",
    "review_critic_tips",
})

# ---------------------------------------------------------------------------
# Default style preamble
# ---------------------------------------------------------------------------

_DEFAULT_PREAMBLE: str = (
    "You are conducting a structured, pre-registered literature review using a "
    "citation-neighbor relevance walk (0.3.1), following the Research Vault "
    "structured literature review protocol.\n"
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
# Default payload — researcher's review-prompt content
# ---------------------------------------------------------------------------
# The keys/shape are fixed by REVIEW_TIPS_KEYS; the prose is the payload.
# Each string is the prompt guidance for that node of the review DAG.

_DEFAULT_REVIEW_TIPS: dict[str, str] = {
    "review_scope_tips": (
        "Freeze the review question, seed queries, inclusion/exclusion criteria, "
        "coverage claim, AND the counter-position BEFORE any search.\n\n"
        "WHY (methodology): this is protocol pre-registration for a systematic review — "
        "the PRISMA-P discipline (Shamseer, Moher et al., 2015): the eligibility "
        "criteria and search strategy are registered and frozen BEFORE records are "
        "screened, so the corpus can't be reverse-engineered to fit a conclusion. "
        "A protocol deviation is LOGGED (new run + new gate), never silent. The "
        "`counter-position` field is this protocol's falsification clause: it names, "
        "in advance, the literature that would refute the coverage claim.\n\n"
        "Required `_protocol.md` fields (all REQUIRED — absence blocks search):\n"
        "  - `question`: the exact research question in one sentence.\n"
        "  - `seed_queries`: a FACET-MATRIX — the established "
        "systematic-review search-strategy discipline (Cochrane Handbook "
        "ch.4 building-block method; PICO/SPIDER question-framing; PRISMA-S "
        "recording), NOT a fixed handful of angle queries. Run this "
        "five-step pipeline, deterministic given the frozen scope, BEFORE "
        "freezing anything:\n"
        "      A. Frame the RQ into 4-6 FACETS via a question-type frame "
        "(PICO/PECO for causal/quantitative, SPIDER/PEO for qualitative, "
        "SPICE for policy). Default to the BROADEST frame (PICO-family) for "
        "recall (Methley et al. 2014: PICO gave the highest sensitivity). "
        "★ MULTI-FRAME UNION: if the RQ genuinely spans two frames (e.g. a "
        "PICO interventional facet AND a SPIDER temporal facet), UNION both "
        "frames' facet skeletons — never pick one frame and drop the "
        "other's crux facet under 'default to PICO'. A picked-over-union "
        "frame choice is exactly how a temporal/stability facet goes "
        "missing.\n"
        "      B. Expand EACH facet into a set of GENUINELY DISTINCT terms "
        "— the recall lever. A facet's canonical phrase is where you START, "
        "not where you stop: relevant papers describe the same concept in "
        "different words (vocabulary mismatch), and an LLM's default is to "
        "UNDER-expand into near-synonyms that collapse to one query on a "
        "semantic backend (LLM-generated review queries reach only "
        "~78-85% of an expert searcher's recall; Wang et al. 2025, "
        "arXiv:2505.07155). Two rules make expansion comprehensive AND "
        "organic:\n"
        "        B1 — Harvest from evidence, not imagination (do this "
        "FIRST). Before inventing synonyms, mine the terms REAL in-scope "
        "papers use: scan the titles, abstracts, and author keywords of "
        "any papers you already surfaced while framing the RQ, and pull "
        "the recurring domain terms, named methods, named "
        "models/datasets/benchmarks, and acronyms verbatim. This surfaces "
        "the terms a community actually uses, which you would not "
        "reliably guess. A facet whose terms are all invented and none "
        "harvested is a red flag for under-coverage.\n"
        "        B2 — Sweep each facet against the variant checklist, but "
        "KEEP a term only when it earns a DISTINCT query on a SEMANTIC "
        "backend (this search runs against dense/semantic retrieval — "
        "S2/OpenAlex/arXiv — NOT a Boolean database). For each facet, add "
        "a term only when the answer names a genuinely different aspect: "
        "a true synonym / near-equivalent phrasing? an acronym <-> full "
        "expansion where the acronym is its own token (e.g. `RLHF` <-> "
        "`reinforcement learning from human feedback`)? a technical <-> "
        "lay term, or an older <-> newer term, that indexes a DIFFERENT "
        "sub-literature? an adjacent / broader / narrower concept a "
        "relevant paper might sit under? a named method / model / "
        "dataset / benchmark / proper noun that is the concept's "
        "canonical instance? DO NOT spend a term on spelling "
        "(behaviour/behavior) or word-form (plural/tense) variants — the "
        "semantic backend already collapses those to the same "
        "neighborhood, so they only inflate the raw count and vanish at "
        "dedup. Write each term as a natural-language query, never a "
        "Boolean/truncation/wildcard string (the backend ignores that "
        "syntax).\n"
        "        B3 — Rejects-only self-check before you freeze the "
        "facet (do NOT pad to a count). For every term you kept: 'Would "
        "this plausibly return a DIFFERENT hit set than this facet's "
        "canonical phrase?' If not, it is a near-synonym — drop it. Then "
        "the coverage question (saturation is coverage, not count): 'Is "
        "there a sub-community, venue, or canonical named method working "
        "on this facet that NONE of my current terms would surface?' If "
        "yes, add a harvested term for it; if you cannot name one, the "
        "facet is saturated. ~3-6 distinct terms is typical — 3 "
        "genuinely distinct beats 6 rewordings.\n"
        "        Worked example (facet = 'value/persona stability of "
        "LLMs over interaction'): canonical `LLM persona stability over "
        "multi-turn dialogue` · harvested-acronym `persona drift` · "
        "adjacent-concept `value consistency in language models` · "
        "lay<->technical `does an AI assistant change its stated "
        "opinions` · named-construct `self-consistency of LLM survey "
        "responses`. Five terms, five DIFFERENT hit-sets — NOT `persona "
        "stability` / `persona consistency` / `stable persona` / "
        "`consistent persona` (four rewordings that collapse to one).\n"
        "      C. For EVERY contested claim / directional hypothesis, "
        "enumerate a dedicated COUNTER facet (disconfirming pole) as a "
        "first-class citizen alongside the thesis facet — never left to "
        "angle luck. ★ PINNED DECODING: generate this Step-C counter-facet "
        "extraction at temp 0, fixed model, frozen prompt — a straw-man "
        "counter-pole ('does X never happen?' instead of the real refuting "
        "sub-literature) is the exact failure this schema exists to catch, "
        "and it is also caught downstream by a cold rejects-only judge "
        "guard (D-6) — pinning decoding here keeps that guard's verdict "
        "reproducible run-to-run.\n"
        "      D. Combine into the query matrix: single-facet high-recall "
        "(~1/facet) + pairwise facet AND-combinations + core multi-facet "
        "precise queries + counter-position queries (each counter facet x "
        "relevant population/outcome facets) + designated citation-chase "
        "seeds. Lands ~40-70 focused / ~100 broad — HR-scale, DERIVED from "
        "the combinatorics, never guessed. ★ Assert the 40-100 band on the "
        "POST-DEDUP DISTINCT-QUERY count, not the raw combinatorial cell "
        "count — near-literal restatements collapse to one query on a "
        "semantic (asta/S2) backend and the raw count overstates distinct "
        "coverage. The Step-B3 rejects-only self-check is what feeds this "
        "distinct-query count: a term that failed B3 was already dropped "
        "before it ever reached the matrix.\n"
        "      E. Freeze + record: write every query EXACTLY as it will run "
        "(PRISMA-S discipline) into the nested schema below; it is hashed "
        "into the frozen scope at `approve-protocol` (anti-fishing pin).\n\n"
        "    Schema — nested `angle -> {thesis, counter}` stance-tagged "
        "lists (each entry is a list of queries, not a single scalar):\n"
        "        seed_queries:\n"
        "          by-temporal:\n"
        "            thesis:\n"
        "              - \"<drift/homogenization query>\"\n"
        "              - \"<a second thesis-side query, same facet>\"\n"
        "            counter:\n"
        "              - \"<persona/value stability query — the REAL refuting "
        "sub-literature, not a bare negation of the thesis query>\"\n"
        "          by-population: \"<legacy scalar form still accepted for a "
        "facet with no contested counter-pole>\"\n"
        "    A facet with a `thesis` list and NO `counter` list is a "
        "protocol defect — `approve-protocol` structurally BLOCKS it (D-7), "
        "same convention as the empty `counter-position` field below. A "
        "near-synonym seed set (8 rewordings of one facet) is the exact "
        "failure mode the FACET step fixes — each facet must probe a "
        "GENUINELY different aspect of the question, not a paraphrase of "
        "another facet. The Step-B3 rejects-only self-check is the "
        "mechanism that prevents this: any term that would return the "
        "same hit set as the facet's canonical phrase gets dropped before "
        "it ever becomes a seed query.\n"
        "  - `sources`: which source-adapters the width-sweep queries, e.g. "
        "`sources: [semantic-scholar, arxiv, openalex]` — the D4 default-on set. "
        "`pubmed` (biomedical) and a web/grey-literature pass are OPT-IN: add "
        "them only when the RQ specifically warrants that domain.\n"
        "  - `inclusion`: criteria a paper must satisfy (population, method, outcome).\n"
        "  - `exclusion`: criteria that disqualify a paper.\n"
        "  - `coverage_claim`: what a COMPLETE corpus would contain "
        "(e.g. 'all English papers 2015–2025 on X in venues Y').\n"
        "  - `counter-position` (REQUIRED — L-2 structural gate): "
        "the literature that would REFUTE the coverage claim — name the specific "
        "sub-literature or opposing view that must be actively sought. "
        "A review with an empty or missing `counter-position` cannot pass the "
        "coverage gate. This is the review's disconfirming obligation made structural.\n"
        "  - `deliverable` (PROPOSE a value — the human confirms or flips it at "
        "`approve-protocol`; default `review` if you don't add the field): "
        "does this review stand alone as the knowledge artifact (`deliverable: "
        "review` — the vetted corpus + synthesis, full stop), or does it flow "
        "all the way through to a submittable manuscript (`deliverable: "
        "manuscript`)? Propose `manuscript` ONLY when the question itself asks "
        "for a paper/survey-shaped output, not merely because a good review "
        "COULD become one. State your recommendation as a one-line "
        "justification in the body prose below the frontmatter (not required "
        "structurally — the frontmatter field alone is what the gate reads), "
        "e.g. '`deliverable: review` — this scopes a grounding literature "
        "survey for an internal decision, no manuscript intended.'\n\n"
        "Anti-fishing (BOTH the angle matrix AND the sources list are frozen here): "
        "the protocol is a CONTRACT. Do not adjust inclusion/exclusion, the angle "
        "matrix, or the sources list after seeing results — breadth is a SCOPE-TIME "
        "commitment made BEFORE evidence, exactly like the criteria. A protocol "
        "revision (including widening seeds/sources) requires a new `review-scope` "
        "run and a new `approve-protocol` gate, never a mid-run edit."
    ),
    "review_screen_tips": (
        "★ The parallel width-sweep itself is now a deterministic TOOL node "
        "(`review-search`, op `sweep`) — it ran automatically before this node "
        "and wrote `_search_hits.md`. This node is a thin judgment layer on top: read "
        "`_search_hits.md`, apply the frozen protocol's inclusion/exclusion "
        "criteria, and accept a seed frontier for the snowball walk. Do not "
        "re-run any search yourself — the sweep already happened.\n\n"
        "WHY (methodology): reconstructible search reporting — the PRISMA 2020 "
        "flow-diagram discipline (Page et al., 2021): record every exclusion "
        "WITH the criterion that excluded it, so a third party can reconstruct "
        "exactly how the corpus was assembled. The audit trail IS the "
        "deliverable, not a by-product.\n\n"
        "Screening discipline:\n"
        "  - Read `_search_hits.md`: per-(angle,source) cell counts, any "
        "degraded/errored cells (an adapter down degraded that cell "
        "gracefully — this is expected, not a search failure), and the ranked "
        "deduped kept set with `[NEW]`/`[IN-CORPUS:<citekey>]` annotation "
        "(already computed mechanically against the real corpus index — do "
        "not re-derive it) and `[DERIVATIVE-OF:*]`/`[BELOW-FLOOR:*]` flags.\n"
        "  - Each kept row now also carries an Abstract/TL;DR snippet + "
        "Venue + Year (when the source adapter had them) — judge the "
        "seed-axis call (is this a SEEDED-model paper or a default one? is "
        "it even in-domain?) on THAT evidence, never on the title alone. A "
        "blank Venue/Abstract cell means the adapter genuinely didn't "
        "return one (e.g. arXiv preprints usually have no formal venue) — "
        "it is not a signal of anything, just fall back to the title +\n"
        "any other cells that are populated.\n"
        "  - Apply inclusion/exclusion from `_protocol.md` to every `[NEW]` "
        "hit. Record each excluded paper WITH the criterion that excluded it "
        "(audit trail) in `_screen.md`.\n"
        "  - A `[BELOW-FLOOR: needs more sources]` item is a signal for the "
        "snowball walk to keep chasing it, not a paper to drop outright. If "
        "the file instead carries a `> Note: [BELOW-FLOOR] suppressed...` "
        "line, the flag was non-discriminating this run (fired on every "
        "kept row) — treat the WHOLE kept set as boundary-sourced rather "
        "than looking for the flag row-by-row.\n"
        "  - Accept the surviving `[NEW]` papers as the seed frontier for the "
        "snowball tool node.\n\n"
        "Output: `_screen.md` is a real note — write the prose exclusion "
        "audit trail FREELY (every excluded paper WITH the criterion that "
        "excluded it; this is the deliverable per the WHY above). THEN put "
        "the accepted seed frontier in a fenced ```seeds``` block at the end "
        "of the file, one identifier per line (DOI/arXiv id/S2 id), no other "
        "prose or table syntax inside the fence:\n\n"
        "    ```seeds\n"
        "    2407.16891\n"
        "    10.48550/arxiv.2408.06929\n"
        "    ```\n\n"
        "The `review-snowball` tool op parses ONLY this fenced block for "
        "seed ids — it does NOT scan the whole file. This is the fix for a "
        "prior bug where the tool op naively scanned every non-empty, "
        "non-`#`, non-`|` line of the file and collected the note's own "
        "YAML frontmatter delimiter (`---`) and prose audit-trail sentences "
        "as if they were seed ids, crashing the snowball walk. Do not put "
        "seed ids anywhere outside the fenced block; do not put anything "
        "else inside it."
    ),
    "review_curate_tips": (
        "★ The citation-neighbor relevance walk itself is now a "
        "deterministic TOOL node (`review-snowball`, op `snowball`) — it ran "
        "automatically before this node and wrote `_corpus_raw.md` + "
        "`_walk.md`. This node is a thin judgment layer on top: "
        "concept-tag the raw corpus, "
        "apply inclusion/exclusion, and emit the FINAL `_corpus.md` (+ "
        "`_coverage-gaps.md` when the walk was budget-terminated).\n\n"
        "★ RELEVANCE-GATE PREVENTION (mandatory, cheap — do this FIRST, "
        "before concept-tagging): `_corpus_raw.md` has ALREADY been through "
        "the mechanical snowball-screen relevance gate (off-domain "
        "candidates like a galaxy survey or a materials-physics paper are "
        "already excluded, moved to a declared '## Rejected as off-domain' "
        "section for audit — never silently vanished). Before you judge "
        "the surviving candidates, COPY the frozen `question`/`inclusion`/"
        "`exclusion`/`coverage_claim`/`counter-position` fields from "
        "`_protocol.md` into your own working notes HERE, inline, rather "
        "than judging against a remembered file pointer — this is the "
        "review's disconfirming obligation AND its inclusion axis made "
        "present at the point of judgment, not one indirection away. A "
        "row flagged `[RELEVANCE:UNCERTAIN]` (unfetchable/too-short "
        "abstract) needs your explicit judgment call, not a rubber stamp — "
        "apply the SAME bias-to-keep discipline: reject only if you are "
        "confident it is off-field, keep+flag otherwise (a dropped "
        "relevant paper is the worse, invisible error).\n\n"
        "WHY (methodology): two named disciplines back the walk you're now "
        "curating.\n"
        "  - Both-direction snowballing (Wohlin, 2014): the tool op already "
        "followed BOTH backward references and forward citations each hop — "
        "a database keyword search alone misses the citation neighbourhood.\n"
        "  - Citation-neighbor relevance walk (0.3.1): the corpus is the "
        "vetted core (this project's `review-screen` output) plus its "
        "immediate citation neighborhood, DEPTH-BOUNDED by `relevance_hops` "
        "(default 1 — deeper is a deliberate recall/precision tradeoff, "
        "never the default). Recall is owned by the SEARCH (broad facet "
        "queries), NOT by chasing citations deep — deep snowballing drifts "
        "into adjacent fields, which is noise, not coverage. The tool op's "
        "mechanical stop reads `_walk.md`'s `stop_reason:` — exactly "
        "`walk-complete:N-hops` (ran every hop cleanly to the bound), "
        "`neighborhood-exhausted` (2 consecutive hops found 0 new "
        "independent papers before the bound), or `budget:N-calls` (the "
        "total-fetch ceiling fired first, bounded corpus).\n\n"
        "  - Each `_corpus_raw.md` candidate now also carries an "
        "Abstract/TL;DR snippet + Venue + Year (mirrors `_search_hits.md`'s "
        "evidence columns) — apply inclusion/exclusion on THAT evidence, "
        "never on the title alone. This matters especially for axes that "
        "are not title-visible (e.g. a 'measured human baseline' "
        "requirement — a title rarely says whether one was collected). A "
        "blank Venue/Abstract cell means the adapter genuinely didn't "
        "return one (e.g. arXiv preprints usually have no formal venue) — "
        "not a signal of anything; fall back to the title + any other "
        "populated cells for that candidate only.\n\n"
        "Curation discipline (each `_corpus_raw.md` candidate):\n"
        "  1. Apply inclusion/exclusion from `_protocol.md`; exclude non-"
        "matching papers, recording the criterion (audit trail).\n"
        "  2. Lightweight concept-tag each accepted paper: which `concepts/` "
        "or `mocs/` regions does its abstract touch? (cheap signal; verified "
        "edges come later in the `relate-<key>` fan-out).\n"
        "  3. `[DERIVATIVE-OF:*]`-flagged candidates stay in the corpus "
        "(discount, never delete — provenance preserved).\n\n"
        "Direction-starvation check: if `_walk.md` shows a hop flagged "
        "`DIRECTION-STARVED` (one direction consistently 0 while the other is "
        "active), flag this as a premature-plateau risk in `_coverage-gaps.md` "
        "regardless of `stop_reason`.\n\n"
        "On BUDGET-TERMINATION (`stop_reason: budget:N-calls`), emit "
        "`_coverage-gaps.md` — the honest residue note. This is the anti-"
        "fabrication move: a budget-bounded corpus must DECLARE its "
        "incompleteness, not hide it behind a green gate. Required contents:\n"
        "  1. A plain statement: 'terminated by the total-fetch budget; "
        "corpus is bounded, not depth-complete.'\n"
        "  2. Which `counter-position` sub-literature (from `_protocol.md`) "
        "remains open/under-explored, if any.\n"
        "  3. Which `concepts/`/`mocs/` regions were still growing (new "
        "concept-tags still appearing) at termination.\n"
        "  4. The un-screened candidate count: how many citation-graph hits "
        "were discovered in the final hop but not yet processed.\n"
        "Do NOT emit `_coverage-gaps.md` on `walk-complete:N-hops` or "
        "`neighborhood-exhausted` termination — both are clean, expected "
        "terminals at the walk's design-time bound; no residue is owed "
        "there.\n\n"
        "Output: `_corpus.md` — the FINAL, concept-tagged citekey list "
        "(table: annotation | citekey | title | abstract), replacing the "
        "raw candidate list. The annotation column MUST use exactly one of "
        "two bracket tags: `[NEW]` for a fresh accept (optionally leg-"
        "prefixed, e.g. `[LEG-1][NEW]`, when you're tracking a legs/facets "
        "structure), or `[IN-CORPUS:<citekey>]` for a paper already "
        "materialized in a prior review cycle. Example accept row: "
        "`| [NEW] | smith2024 | A Study of X | An abstract snippet... |`. "
        "Any other annotation spelling (e.g. `[ACCEPT]`) is not recognized "
        "downstream and will silently drop the row from the Phase-2 fan-out. "
        "CARRY the Abstract/TL;DR text VERBATIM from "
        "`_corpus_raw.md` into this new 4th column for every row — do not "
        "drop it and do not re-summarize it. This is what lets the "
        "downstream cold relevance verifier (`review-relevance-verify`) "
        "re-check the final corpus on real substance rather than title "
        "alone; a blank abstract here degrades that check to UNCERTAIN "
        "(keep+flag) for that paper, so only leave it blank when "
        "`_corpus_raw.md` itself had no abstract for that candidate. "
        "`_corpus.md` and `_walk.md` (and `_coverage-gaps.md` when "
        "present) are the phase-boundary artifacts: the `coverage-gate` "
        "human-go reads them before authorizing Phase-2. `rv dag approve "
        "<run> coverage-gate` structurally reads `stop_reason:` from "
        "`_walk.md` — adequacy is judged by relevance-verify + source-"
        "coverage downstream, not by this walk terminal alone."
    ),
    "review_relevance_verify_tips": (
        "You are the COLD final-corpus relevance verifier (design "
        "2026-07-10-trustworthy-curation-relevance-gate-design.md) — a "
        "REJECTS-ONLY, fresh judge with no stake in `review-curate`'s "
        "decisions. A `_corpus_verify_input.md` note has been prepared for "
        "you: one row per `[NEW]` paper in the final `_corpus.md` "
        "(citekey | title | abstract), PLUS a small number of additional "
        "rows mixed in. Judge EVERY row identically, on its own substance "
        "alone — do not try to guess which rows are 'real' papers vs "
        "anything else; there is no such distinction from your side.\n\n"
        "WHY (methodology): `review-curate` is a bulk, self-certifying "
        "judgment pass — nothing independently re-checks its output before "
        "the expensive Phase-2 relate fan-out. You are that independent "
        "check, mirroring `review-coverage-critic`'s cold, rejects-only "
        "role but for TOPICAL RELEVANCE rather than coverage completeness.\n\n"
        "Calibration (apply the SAME bias-to-keep discipline as "
        "`review-curate`'s inline pass, but COLD — you were not part of "
        "curating this corpus):\n"
        "  - `OFF_DOMAIN`: reject ONLY when you are CONFIDENT the paper has "
        "no language-model/LLM component AND no cultural/value/behavioral "
        "construct related to the frozen protocol's question — an "
        "unambiguous wrong-field paper (e.g. an astronomy survey, a "
        "materials-physics study). This is the SAME high-precision bar as "
        "the mechanical snowball-screen gate that already ran earlier in "
        "this pipeline.\n"
        "  - `IN`: anything topically plausible, INCLUDING boundary and "
        "disconfirming papers. A row whose text overlaps the corpus "
        "review's `counter-position` sub-literature (the falsification "
        "clause named in `_protocol.md`) is DEFINED in-scope — you must "
        "NEVER reject it for being off-thesis; a review that strips its "
        "own disconfirming canon is fishing.\n"
        "  - `UNCERTAIN`: the abstract is empty, unfetchable, or too thin "
        "to judge confidently. Keep+flag, never a confident reject.\n\n"
        "Output — STRUCTURED TABLE, not prose (never scanned as free "
        "text): write your verdict to `_relevance-verdict.md` as a "
        "markdown table, one row per input row, citekey preserved exactly:\n\n"
        "    | Citekey | Verdict |\n"
        "    |---|---|\n"
        "    | smith2024 | IN |\n"
        "    | jones2023 | OFF_DOMAIN |\n\n"
        "The `Verdict` value MUST be EXACTLY one of `IN` / `OFF_DOMAIN` / "
        "`UNCERTAIN` (nothing else) for EVERY row in the input — a missing "
        "or malformed row is treated as untrustworthy by the downstream "
        "gate, so do not skip any row. Your reasoning (why a specific "
        "paper is off-domain) belongs in free prose BELOW the table — the "
        "audit trail a human reads later — never in place of the table."
    ),
    "per_paper_relate_tips": (
        "Distill this paper into an OKF `literature/<citekey>.md` note using a "
        "PRINCIPLED 5-MOVE READING PROTOCOL — this is the reading DISCIPLINE, "
        "not a rigid schema (the note stays free-form structured thought; only "
        "these mandatory questions must be answered).\n\n"
        "WHY (methodology, Wave 0 — Reading): a review is concept-centric, "
        "NOT author-centric (Webster & Watson, 2002) — the concept-edges below are "
        "the paper's row in the review's concept matrix. But a strong survey is "
        "built from RELATIONSHIPS BETWEEN STUDIES, and systematic-review data "
        "extraction (Cochrane Handbook ch.5 / PICO) mines every study with the "
        "SAME pre-specified item list so extraction is consistent, not improvised. "
        "Meta-ethnography (Noblit & Hare) names 'reading the studies' and "
        "'determining how the studies are related' as distinct phases, and types "
        "every inter-study relation as reciprocal / refutational / "
        "line-of-argument. A per-paper prose summary that draws no edges — to "
        "concepts OR to other papers — is COLLECTION, not review: relate, don't "
        "collect.\n\n"
        "THE 5 MOVES (in order — contribution before result before relations; "
        "you cannot judge how two papers relate until you've pinned each one's "
        "exact arrow):\n\n"
        "  MOVE 1 — Orient/classify (30 seconds). Read title/abstract/figures. "
        "Set `contribution_kind:` to exactly one of: mechanism, theory-bound, "
        "benchmark, survey, application. This decides which of the remaining "
        "moves matter most (a theory paper has bounds, not effect-sizes; a "
        "benchmark study has scores, not mechanisms). NOTE: `contribution_kind` "
        "is NOT `role` (in the required-fields list below) — do not put "
        "methodological/empirical/theoretical/counter-position here; those are "
        "`role` values, a different, adjacent field.\n\n"
        "  MOVE 2 — Extract the contribution precisely. Fill `claim:` with the "
        "EXACT ARROW the paper establishes, in the paper's own terms — narrow "
        "enough that a weaker paraphrase is visibly false (rv craft: test the "
        "exact arrow, refuse a near-neighbour stand-in). Not 'improves "
        "exploration' but 'specific stochasticity-robust drives help on "
        "genuinely hard-exploration tasks.'\n\n"
        "  MOVE 3 — Mine the result WITH magnitude (mandatory when the paper "
        "reports a quantitative result — the fix for the had-no-number gap). "
        "Set `result_reported:` to exactly `yes` or `no` (whitelist — no other "
        "spelling is recognized). When `yes`, add a `## Result` body section "
        "recording the actual finding: the effect size / benchmark score / "
        "theorem's bound / measured rate, WITH population/setting/conditions "
        "(what agent, what environment, what regime), AND the paper's own "
        "stated limitations/scope. When `no`, no `## Result` section is needed — "
        "'no' is a legitimate answer, not a shortfall.\n\n"
        "  MOVE 4 — ★ Relate to the corpus (the move the old reading omitted). "
        "For EACH paper already in the corpus this one bears on, name the "
        "relation and TYPE it as one of: reciprocal (agrees/replicates), "
        "refutational (contradicts/limits), line-of-argument "
        "(extends/special-case-of/supplies-mechanism-for) — Noblit & Hare "
        "step 4, made first-class. Set `paper_relations_sought:` to exactly "
        "`yes` or `no` (whitelist), after having actually checked. When `yes`, "
        "add a `## Related papers` body section (this EXACT heading — "
        "required, checked mechanically) with ONE line per relation, in "
        "OKF-CONFORMANT markdown-link form (NOT a bare citekey, NOT a "
        "`[[wikilink]]` — Google Cloud's OKF spec cross-links notes with "
        "standard markdown links, explicitly not wikilinks, and states the "
        "relationship type belongs IN PROSE, not encoded onto the link):\n"
        "      `- [<display text>](/literature/<citekey>.md) — "
        "SUPPORTS|CONTRADICTS|PARTIAL|EXTENDS: <why, in your own words>`\n"
        "    e.g. `- [Baltaji 2024](/literature/"
        "baltajipersonainconstancymulti2024.md) — SUPPORTS: replicates the "
        "same persona-drift mechanism in a related population.`\n"
        "    (The link path is absolute and bundle-relative — resolved "
        "against this project's notes directory, the OKF bundle root — "
        "which is exactly the markdown link you'd naturally write when "
        "cross-referencing another note. SUPPORTS≈reciprocal, "
        "CONTRADICTS≈refutational, PARTIAL/EXTENDS≈line-of-argument — "
        "the leading TYPE token derives the relation kind mechanically.) The "
        "trailing `(reciprocal|refutational|line-of-argument)` mirror is "
        "OPTIONAL — you may add it for clarity, but the TYPE token is "
        "authoritative: if you add it and it disagrees with the derived "
        "kind, the type wins and the disagreement is surfaced, never "
        "silently resolved. Do NOT omit the type+link to save a word — an "
        "edge-shaped line that does not fully parse (a typo'd type, a bare "
        "citekey with no markdown link, a missing target) is a hard FAIL, "
        "surfaced loudly, never silently dropped — this is checked via a "
        "FULL-BODY scan, so even a line placed under the wrong heading is "
        "still caught, but the canonical `## Related papers` heading is "
        "REQUIRED regardless (downstream traversal depends on it). The "
        "typed TYPE + TARGET are required and "
        "checked; the RELATION'S SUBSTANCE (why/where it holds) stays free "
        "prose — a relation reduced to a bare tag with no reasoning is as "
        "thin as no relation at all (do not over-rigidify this). These typed "
        "edges are carried structure, not decoration: `review-synthesize` "
        "reads them directly from each `literature/<key>.md` note's "
        "`## Related papers` section (its `reads:` already include the "
        "`literature/` directory), and at manuscript-compile time "
        "`review.relations_report()` renders the same edges mechanically "
        "into the comparative-relations section — a real edge here is never "
        "re-derived from prose downstream. Catch near-tautologies: if a "
        "'relation' is just a paraphrase of the other paper's claim, it isn't "
        "a relation. When `no` (this paper doesn't bear on the corpus yet), no "
        "`## Related papers` section is needed — a plain `- ` bullet with no "
        "markdown link elsewhere in the note is ordinary prose and is never "
        "treated as an edge attempt.\n\n"
        "  ★ RETRIEVAL-TIER CAPS EDGE STRENGTH: a note you read at "
        "abstract-only/title-only (or any tier short of full-text) CANNOT "
        "carry a `SUPPORTS`/`CONTRADICTS` edge — of EITHER kind, "
        "paper→paper or paper→concept. You have not actually read the paper "
        "at the fidelity needed to assert or refute a claim; the strongest "
        "type you may use at that retrieval tier is `PARTIAL`. This is "
        "checked mechanically against `read_basis` — an unstamped "
        "`read_basis` is treated the same as non-full-text (fail-closed).\n\n"
        "  MOVE 5 — Relate to concepts + flag provenance (mandatory gating "
        "unchanged this wave; the edge FORMAT below is now OKF markdown "
        "links, same as Move 4). Attach stance-bearing concept edges (see "
        "below), and record the retrieval caveat honestly (verified vs "
        "inferred).\n\n"
        "Required note fields (flat frontmatter):\n"
        "  - `type`: literature\n"
        "  - `citekey`: the paper's citekey (matches corpus)\n"
        "  - `title`: exact title\n"
        "  - `year`: publication year\n"
        "  - `authors`: first author et al.\n"
        "  - `venue`: journal/conference\n"
        "  - `claim`: ONE-SENTENCE summary of the paper's exact-arrow contribution (Move 2)\n"
        "  - `method`: the method used to support the claim\n"
        "  - `evidence`: what evidence/result they present\n"
        "  - `contribution_kind`: Move 1 — one of mechanism/theory-bound/benchmark/survey/application\n"
        "  - `role`: a lightweight categorical tag — one of methodological / "
        "empirical / theoretical / counter-position (replaces the old "
        "overloaded `stance` field, which did contradictory double duty as "
        "both a one-word tag and a full synthesis paragraph). NOTE: `role` is "
        "NOT `contribution_kind` (Move 1, above) — do not put mechanism/"
        "theory-bound/benchmark/survey/application here; those are "
        "`contribution_kind` values, a different, adjacent field.\n"
        "  - `position`: a free-form narrative — how this paper relates to the "
        "review question, in your own words (the narrative half `stance` "
        "used to carry; this is where the real synthesis material lives — "
        "write as much as the paper warrants)\n"
        "  - `result_reported`: Move 3/ exactly `yes` or `no`\n"
        "  - `paper_relations_sought`: Move 4/ exactly `yes` or `no`\n"
        "  - `concepts`: comma-separated concepts/ or mocs/ regions this touches\n\n"
        "Verified concept-edges (Move 5, body of the note, under a `## Concept "
        "edges` heading — this canonical heading name, same OKF markdown-link "
        "form as the paper→paper edges above):\n"
        "  - Draw edges ONLY from the note fields above — never invented.\n"
        "  - Format: `[<display text>](/concepts/<slug>.md) — SUPPORTS: "
        "<one sentence why>`\n"
        "  - Format: `[<display text>](/concepts/<slug>.md) — CONTRADICTS: "
        "<one sentence why>`\n"
        "  - Format: `[<display text>](/concepts/<slug>.md) — PARTIAL: "
        "<one sentence why>`\n"
        "  - e.g. `[WEIRD default](/concepts/western-consensus-"
        "default.md) — SUPPORTS: directly supports the WEIRD-default concept.`\n"
        "  - A `CONTRADICTS` edge is equally valuable to a `SUPPORTS` edge — "
        "the disconfirming obligation applies here too.\n"
        "  - Retrieval-tier cap applies here too: abstract-only/title-only "
        "reads cannot carry `SUPPORTS`/`CONTRADICTS` concept edges — cap "
        "at `PARTIAL`.\n\n"
        "★ THE READING INPUT (OA-fulltext-enrichment, tier 1): before you start "
        "the 5 moves, call `rv research fulltext <project> <citekey> [--doi/"
        "--arxiv/--pmid/--pmcid/--openalex/--oa-url/--oa-status/--source ...]` "
        "with whatever identifiers you already resolved during search/"
        "discovery. This fetches the paper's OPEN-ACCESS FULL TEXT when "
        "available (stdlib-first: PMC XML, S2's openAccessPdf, Unpaywall, "
        "OpenAlex, arXiv PDF — PDF parsing is the last resort, not the hot "
        "path) and caches it under `literature/.fulltext/`. Read the FULL "
        "TEXT when it fetched successfully — Move 3's magnitude/conditions/"
        "limitations and Move 4's typed edges live in the results/discussion "
        "sections, not the abstract. When no OA source is found (all "
        "providers decline), the tool says so plainly — read the ABSTRACT "
        "instead; this is a legitimate, honest degrade, not a shortfall to "
        "hide. Either way, record the basis honestly: after filing the note "
        "with `rv note new <project> literature <citekey>`, call `rv research "
        "fulltext` again (it re-reads the cache, no re-fetch) so it stamps "
        "`read_basis`/`full_text_provider`/`oa_status`/`full_text_url` into "
        "the note's frontmatter — never leave `read_basis` unstamped.\n\n"
        "A paper is more than prose (LEAN — record what you see, never "
        "fetch or download it — applies to ARTIFACTS: repo/checkpoint/"
        "dataset. The paper's own body is the EXCEPTION as of tier 1: its "
        "full text IS fetched, per the reading-input paragraph above). While "
        "you read the paper, extract three more things in the SAME pass:\n\n"
        "  1. `key_equations:` — the paper's PIVOTAL equations, if any. Split by "
        "the flat-frontmatter doctrine (no multi-line LaTeX in frontmatter):\n"
        "       - BODY: add a `## Key equations` section, one labeled block per "
        "equation:\n"
        "           `### [eq:elbo] Evidence lower bound  *(critical)*`\n"
        "           `$$ \\log p(x) \\ge \\mathbb{E}_{q}[\\log p(x,z) - \\log q(z)] $$`\n"
        "       - FRONTMATTER: `key_equations:` as a mapping-list criticality "
        "ledger keyed by the SAME label used in the body. Each label must be "
        "unique within the note and match the body heading character-for-"
        "character — the manuscript equation-fidelity gate joins the "
        "frontmatter `critical:` flag to the body block on this label, so a "
        "duplicate or mismatch silently breaks the join (the gate would read "
        "no criticality for a real critical equation):\n"
        "           key_equations:\n"
        "             - label: eq:elbo\n"
        "               critical: true\n"
        "             - label: eq:kl\n"
        "               critical: false\n"
        "       Mark `critical: true` ONLY when the paper's central claim turns "
        "on that equation (the argument doesn't hold without it) — default "
        "`critical: false` for a supporting or incidental equation. Apply the "
        "survey-reader test: mark `critical: true` only for an equation a "
        "survey of this area would reproduce to state the paper's contribution "
        "— typically 0–2 per paper, often zero. If you've marked more than two "
        "critical, you are over-marking. Re-check against 'does the argument "
        "collapse without it?' A paper with no pivotal equations gets NO "
        "`## Key equations` section and NO `key_equations:` field at all — "
        "never a placeholder or an empty guess. This ledger is what the "
        "downstream manuscript loop's equation-fidelity gate reads; a dropped "
        "`critical: true` equation is a BLOCK there, so mark conservatively "
        "and only from what the paper actually states. The frontmatter ledger "
        "is authoritative for criticality; the `*(critical)*` body tag is "
        "only a human-readable mirror — if they disagree, the ledger wins.\n"
        "  2. `repo:` — takes a code repository URL (GitHub/GitLab/etc.) only. "
        "You may find the repo link inside a project page — record the repo "
        "URL, not the page. A non-code project landing page belongs in "
        "`artifacts:` as `project-page:`, never in `repo:`. Leave `repo:` "
        "empty when the paper ships no code — do not guess or search beyond "
        "what you already read.\n"
        "  3. `artifacts:` — a scalar list of other first-class artifacts the "
        "paper points to, as `label: url` pointers (dataset, project page, "
        "model checkpoint, leaderboard):\n"
        "           artifacts:\n"
        "             - dataset: https://example.org/dataset\n"
        "             - project-page: https://example.org/project\n"
        "     RECORD-WHAT-YOU-SEE ONLY (D-MS-6, LEAN scope): this is a list of "
        "pointers, not an acquisition system — do not clone the repo, download "
        "the checkpoint, or fetch the dataset. Note that it exists and where; "
        "nothing is retrieved.\n\n"
        "reads: — you have access to:\n"
        "  - the paper itself: FULL TEXT when `rv research fulltext` found an "
        "OA source (cached under `literature/.fulltext/`), the ABSTRACT "
        "otherwise — never invent a middle ground; the tool tells you which.\n"
        "  - `concepts/` directory (existing concept nodes)\n"
        "  - `mocs/` directory (existing maps of content)\n"
        "Do not invent concept nodes that don't exist in `concepts/`; "
        "flag missing concepts as TODOs in the note body."
    ),
    "review_synthesize_tips": (
        "Synthesize the full corpus (all `literature/<key>.md` notes from Phase-2) "
        "into the review's conceptual map.\n\n"
        "WHY (methodology): organize by CONCEPT across papers, not paper-by-paper "
        "(Webster & Watson, 2002). The `concepts/` and `mocs/` notes are the concept "
        "matrix made durable — concepts are the rows, papers the cells. If your "
        "synthesis reads as a sequence of paper summaries, you have transcribed the "
        "corpus, not synthesized it.\n\n"
        "★ TRAVERSE, DON'T RE-DERIVE (Wave 0 — Reading): the relate-<key> "
        "fan-out already discovered and TYPED the paper→paper comparative "
        "relations (reciprocal/refutational/line-of-argument) — read them "
        "directly, VERBATIM, from each `literature/<key>.md` note's "
        "`## Related papers` section (already in your `reads:`) BEFORE "
        "writing the comparative parts of your synthesis. This turns the "
        "survey's comparative spine from re-derived prose into carried "
        "structure. Do NOT try to reconstruct which papers refute/extend "
        "which from the `position`/`claim` narrative prose — the typed "
        "`[<display>](/literature/<citekey>.md) — SUPPORTS|CONTRADICTS|"
        "PARTIAL|EXTENDS: <reason>` lines already say so mechanically; scan "
        "for those lines across the corpus "
        "rather than re-deriving the relation from scratch. Malformed edge "
        "lines cannot reach you here (the per-paper relate-<key> presence "
        "check hard-FAILs on those before this node runs) — but a DANGLING "
        "edge (a target citekey this note names that is not itself a "
        "`literature/<key>.md` note in the corpus) can still occur; flag any "
        "you find in your coverage cross-check below rather than quietly "
        "ignoring it.\n\n"
        "Outputs:\n"
        "  1. `concepts/<c>.md` updates — for each concept touched by 2+ papers, "
        "ensure a concept note exists and its incoming-edge list is current. "
        "Add concept notes if missing (they are OKF type `concepts`).\n"
        "  2. `mocs/<region>.md` updates — map-of-content notes summarizing which "
        "papers populate each sub-region. An MOC entry: "
        "`- [citekey] <claim> (<role>)`.\n"
        "  3. Where the corpus has real paper→paper edges (from step 0 above), "
        "reflect the comparative spine explicitly — 'X refutes Y (refutational)', "
        "'X extends Y (line-of-argument)' — rather than re-discovering the "
        "same comparison from scratch.\n\n"
        "Orphan-avoidance:\n"
        "  - Every `literature/<key>.md` note must appear in at least one MOC region.\n"
        "  - Flag orphan notes (no MOC entry) as soft warnings — do not block, but list them.\n\n"
        "Coverage claim cross-check:\n"
        "  - Compare the corpus against the `coverage_claim` from `_protocol.md`.\n"
        "  - Note any regions of the claim that are thin (few papers) vs dense.\n\n"
        "The synthesis is the input to `review-coverage-critic`."
    ),
    "review_critic_tips": (
        "You are the coverage critic (reviewer role). You are a REJECTS-ONLY reviewer: "
        "a `[PASS]` does NOT certify coverage, it only fails to find a blocking hole.\n\n"
        "WHY (methodology): your two hardest axes have named backing. Axis 1 tests "
        "whether the citation-neighbor walk's coverage is genuine or premature — a walk "
        "that hit `neighborhood-exhausted` while direction-starved (one snowball "
        "direction dry; cf. Wohlin, 2014) or tag-under-counted is PREMATURE, not a clean "
        "terminal. Axis 4 enforces the disconfirming obligation: a review that only "
        "confirms is fishing, so the pre-registered counter-position must be sought, not "
        "merely declared.\n\n"
        "Judge FOUR axes (each can independently issue `[BLOCK]`):\n\n"
        "1. WALK COVERAGE — is the citation neighborhood genuinely covered, or premature?\n"
        "   Read the `_walk.md` hop table + `stop_reason:`. Check:\n"
        "   - Did the walk reach `walk-complete:N-hops` cleanly (every hop ran to the "
        "declared depth bound)? (genuinely complete — nothing to flag here)\n"
        "   - OR did it stop at `neighborhood-exhausted` while one direction (forward OR "
        "backward) stayed dry across the hops? "
        "(direction-starved — flag as `DIRECTION-STARVED` and issue `[BLOCK]`)\n"
        "   - OR did verified concept-edges (from `relate-<key>` notes) consistently "
        "outrun the cheap concept-tags? (tag-under-counting — issue `[BLOCK]`)\n"
        "   A `neighborhood-exhausted` terminal reached with direction-starvation or "
        "tag-under-counting is PREMATURE.\n\n"
        "2. ORPHAN CONCEPTS/MOCS — soft flag (do not block, but list)\n"
        "   Do NOT hand-stem-match note filenames to corpus citekeys — run "
        "`rv review <project> coverage <scope>` for the deterministic orphan list "
        "(keyed by `citekey:` frontmatter field, not filename stem).\n"
        "   Report orphan count and keys from that output. Issue a soft warning, not a `[BLOCK]`.\n\n"
        "3. PROTOCOL ADHERENCE — did the corpus honor the frozen criteria?\n"
        "   Compare the accepted corpus against `_protocol.md` inclusion/exclusion.\n"
        "   Any paper included that violates inclusion criteria = fishing = `[BLOCK]`.\n"
        "   Any paper excluded that meets inclusion criteria = coverage gap = `[BLOCK]`.\n\n"
        "4. COUNTER-POSITION (L-2 gate — REQUIRED)\n"
        "   The `_protocol.md` MUST have a non-empty `counter-position` field.\n"
        "   - Missing or empty `counter-position` → `[BLOCK]` (hard structural gate).\n"
        "   - Non-empty `counter-position` but corpus contains ZERO papers from the "
        "declared opposing sub-literature → `[BLOCK]` (sought-not-just-present).\n"
        "   - A SPECIFIC facet's counter-pole is thin/empty (e.g. `by-temporal`'s "
        "`counter` queries returned nothing while its `thesis` side is well-populated) "
        "→ `[BLOCK]`, itemized as `COUNTER-POSITION THIN-POLE <facet>` (the "
        "EXACT prefix — never `COUNTER-POSITION ABSENT`/`NOT SOUGHT`, which "
        "are the two DIFFERENT, protocol-level hard blocks above), and this "
        "is the ONE case where you must ALSO stamp a STRUCTURED "
        "`remediation_target` (see below) naming exactly which facet is thin "
        "— a bounded autonomous backtrack re-runs THAT facet's frozen "
        "counter queries harder, it cannot guess which pole from prose alone.\n"
        "   A `counter-position` that was declared but not actively sought is fishing "
        "in reverse — a confirming-only review dressed as balanced.\n\n"
        "STRUCTURED `remediation_target` (`COUNTER-POSITION THIN-POLE` "
        "BLOCK ONLY): when — and ONLY when — every `[BLOCK]` reason you are "
        "about to itemize starts with the EXACT prefix `COUNTER-POSITION "
        "THIN-POLE` — i.e. NOT `COUNTER-POSITION ABSENT`/`NOT SOUGHT` "
        "(protocol-level, no single facet to name) and NOT mixed with a "
        "`DIRECTION-STARVED`/`TAG-UNDER-COUNTING`/`PROTOCOL-DRIFT` finding — "
        "ALSO write three additional frontmatter fields naming the exact "
        "backtrack target:\n"
        "  ---\n"
        "  verdict: BLOCK\n"
        "  remediation_target_node: review-snowball\n"
        "  remediation_target_pole: by-temporal\n"
        "  remediation_target_directive: re-run the by-temporal facet's frozen "
        "counter queries harder (all sources, relaxed per-cell limit) and "
        "re-seed a snowball citation-chase from whatever thin counter-hits "
        "turn up\n"
        "  ---\n"
        "`remediation_target_pole` MUST be the EXACT angle key from the "
        "protocol's `seed_queries:` matrix (e.g. `by-temporal`, "
        "`by-population`) — never a paraphrase, never a guess at a facet "
        "that isn't in the frozen matrix. If ANY OTHER finding is also "
        "present — `COUNTER-POSITION ABSENT`/`NOT SOUGHT`, or an axis-1/3 "
        "finding (`DIRECTION-STARVED`/`TAG-UNDER-COUNTING`/`PROTOCOL-DRIFT`) "
        "— do NOT write these three fields at all — a mixed BLOCK is not "
        "eligible for the autonomous pole-directed backtrack and routes to "
        "a human/agent revise round exactly as before.\n\n"
        "Output format — STRUCTURED FRONTMATTER FIELD, not a prose bracket "
        "token (single-human-gate design, 2026-07-09): `approve-review` "
        "(Gate 3) resolves AUTONOMOUSLY from `_coverage-critic.md` — there is "
        "no human reading your prose reply, so the gate parses ONLY a "
        "frontmatter field, never prose. Your reasoning still matters (it is "
        "the audit trail and the REVISE reasons a human will read later) — "
        "it goes in the BODY below the frontmatter, not in place of the field.\n\n"
        "WRITE YOUR VERDICT TO `_coverage-critic.md` as YAML frontmatter:\n"
        "  ---\n"
        "  verdict: PASS\n"
        "  ---\n"
        "or:\n"
        "  ---\n"
        "  verdict: BLOCK\n"
        "  ---\n\n"
        "The `verdict:` value MUST be EXACTLY `PASS` or `BLOCK` (nothing "
        "else — no other spelling, no bracket, no extra words on that "
        "line). Any other value, an empty value, or a missing field means "
        "the gate cannot trust the note and HALTS closed — the review "
        "loop does not proceed, it does not default to PASS.\n\n"
        "`PASS` — no blocking holes found (not a certification).\n"
        "`BLOCK` — one or more blocking holes found; in the BODY below the "
        "frontmatter, list each as a `- <reason>` bullet (axis name + a "
        "one-line reason):\n"
        "    - DIRECTION-STARVED plateau (axis 1)\n"
        "    - TAG-UNDER-COUNTING plateau (axis 1)\n"
        "    - PROTOCOL-DRIFT (axis 3)\n"
        "    - COUNTER-POSITION ABSENT (axis 4 — hard block)\n"
        "    - COUNTER-POSITION NOT SOUGHT (axis 4 — hard block)\n"
        "    - COUNTER-POSITION THIN-POLE <facet-key> (axis 4 — a SPECIFIC "
        "facet's counter side is thin/empty; pair with the structured "
        "`remediation_target_*` fields above)\n\n"
        "Body template (below the frontmatter, for the audit trail — the "
        "gate does not parse this, a human reading the note later does):\n"
        "  'N papers, R rounds, plateau at round K; j orphan concepts "
        "(soft); counter-position: sought/absent; k BLOCK(s).'\n"
        "  - <reason bullet per BLOCK finding, if verdict: BLOCK>\n\n"
        "Never write 'coverage verified' — you are a rejects-only screen. "
        "A `verdict: BLOCK` with no itemized reason bullets in the body is "
        "treated as a generic, unspecific block — always itemize."
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
        - The default is the researcher's review-prompt content; adopters own the prose.
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
    """
    if config is not None:
        raw = getattr(config, "_raw", {})
        override = raw.get("review_style", {})
        if isinstance(override, dict):
            preamble = override.get("preamble")
            if isinstance(preamble, str) and preamble.strip():
                return preamble
    return _DEFAULT_PREAMBLE


# ---------------------------------------------------------------------------
# Relevance-hops config seam (0.3.1 — citation-neighbor relevance walk)
# ---------------------------------------------------------------------------

DEFAULT_RELEVANCE_HOPS: int = 1

# Deprecated alias — one-release back-compat only (0.3.1).
DEFAULT_SATURATION_BACKSTOP_WAVES: int = DEFAULT_RELEVANCE_HOPS

# Breadth x depth bounds: this default is kept numerically in sync with
# sources.snowball's own DEFAULT_RELEVANCE_HOPS (the SSOT for a caller
# invoking run_citation_neighbor_walk directly, e.g. tests) rather than only
# overriding one call site — combined with the seed_cap/frontier_cap/
# fetch_budget knobs (sources/snowball.py), this is the closed set of
# breadth x depth bounds on the walk.


def get_relevance_hops(config: Any = None) -> int:
    """Return the review-snowball citation-neighbor relevance walk's DEPTH
    BOUND (in relevance hops).

    Corpus = the vetted core (``review-screen`` output) plus its immediate
    citation neighborhood. Recall is owned by the SEARCH (broad facet
    queries); precision is owned by this depth bound + ``review-screen`` —
    deep citation snowballing was a poor recall tool (drifts into adjacent
    fields, which is noise, not coverage), so the default stays tight (1
    hop). Deeper (2+) is a deliberate recall/precision tradeoff an adopter
    can opt into per venue/RQ, never the shipped default.

    Args:
        config: a loaded Config instance (or None for the shipped default).
                If the config has ``[review_style] relevance_hops = N``
                (a positive int), that value overrides the default. The
                legacy key ``saturation_backstop_waves`` is accepted as a
                DEPRECATED alias for one release if ``relevance_hops`` is
                absent.

    Returns:
        The hop-count cap (int, >= 1).  Default 1.  A non-int, non-positive,
        or missing override falls back to the default (never a crash, never a
        silently-accepted nonsensical cap like 0 or a negative number).
    """
    if config is not None:
        raw = getattr(config, "_raw", {})
        override = raw.get("review_style", {})
        if isinstance(override, dict):
            value = override.get("relevance_hops")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                return value
            legacy = override.get("saturation_backstop_waves")
            if isinstance(legacy, int) and not isinstance(legacy, bool) and legacy >= 1:
                import warnings

                warnings.warn(
                    "[review_style] saturation_backstop_waves is deprecated "
                    "— use relevance_hops instead. Will be removed in a "
                    "future release.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                return legacy
    return DEFAULT_RELEVANCE_HOPS


#: Deprecated alias for ``get_relevance_hops``. Will be removed in a future
#: release.
get_saturation_backstop_waves = get_relevance_hops


# ---------------------------------------------------------------------------
# Remediation round-cap config seam
# ---------------------------------------------------------------------------

DEFAULT_REMEDIATION_MAX_ROUNDS: int = 2


def get_remediation_max_rounds(config: Any = None) -> int:
    """Return the autonomous coverage-gap remediation round cap.

    One of the independent termination bounds on the bounded remediation
    loop: even a pathological "one new paper per wave" corpus cannot exceed
    this many autonomous remediation rounds before the loop declares residue
    and surfaces for human review.

    Args:
        config: a loaded Config instance (or None for the shipped default).
                If the config has ``[review_style] remediation_max_rounds = N``
                (a positive int), that value overrides the default.

    Returns:
        The round cap (int, >= 1). Default 2 (conservative). A non-int,
        non-positive, or missing override falls back to the default —
        fail-closed (a missing/malformed counter reads as "the conservative
        default", never as "unbounded").
    """
    if config is not None:
        raw = getattr(config, "_raw", {})
        override = raw.get("review_style", {})
        if isinstance(override, dict):
            value = override.get("remediation_max_rounds")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                return value
    return DEFAULT_REMEDIATION_MAX_ROUNDS


# ---------------------------------------------------------------------------
# Critic-backtrack round-cap config seam (D-5a) — SEPARATE from
# `remediation_max_rounds` above: a critic backtrack round re-pays the
# (full-distill + incremental-relate) delta for the papers it finds, which
# is a materially different cost than a saturation-remediation round's
# re-sweep — so the two bounds are independently configurable, never shared.
# ---------------------------------------------------------------------------

DEFAULT_CRITIC_BACKTRACK_MAX_ROUNDS: int = 2


def get_critic_backtrack_max_rounds(config: Any = None) -> int:
    """Return the autonomous, pole-directed critic-backtrack round cap.

    One of the independent termination bounds on the pole-directed
    backtrack loop (``review.remediation.resolve_coverage_critic`` +
    ``dag/verbs.py``'s approve-review round-stepping, which drives
    ``review.remediation.run_directed_remediation_round`` one round at a
    time across the harness's async cold-judge fan-out): even a pathological
    "one new counter-paper per wave" pole cannot exceed this many autonomous
    backtrack rounds before the loop HALT-DECLAREs (a counter-position/
    thin-pole BLOCK is a hard structural gate, axis 4 — it cannot declare
    residue like the coverage-gate's saturation remediation can; closing it
    for good needs a criteria change, a human decision).

    Args:
        config: a loaded Config instance (or None for the shipped default).
                If the config has ``[review_style] critic_backtrack_max_rounds = N``
                (a positive int), that value overrides the default.

    Returns:
        The round cap (int, >= 1). Default 2 (conservative). A non-int,
        non-positive, or missing override falls back to the default —
        fail-closed, never unbounded.
    """
    if config is not None:
        raw = getattr(config, "_raw", {})
        override = raw.get("review_style", {})
        if isinstance(override, dict):
            value = override.get("critic_backtrack_max_rounds")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                return value
    return DEFAULT_CRITIC_BACKTRACK_MAX_ROUNDS


# ---------------------------------------------------------------------------
# Facet-breadth / facet-coverage config seam (0.3.1 — search-breadth +
# facet-coverage redesign, "recall from queries"). All four knobs are
# per-review-type overridable via an OPTIONAL nested
# ``[review_style.by_type.<review_type>]`` sub-table checked BEFORE the
# flat ``[review_style]`` table, which is checked before the shipped
# default — three-tier lookup, same fail-closed-to-default posture as
# every other knob in this module (a non-int/non-positive/missing override
# at ANY tier falls through to the next, never a crash, never an
# unbounded/zero value). ``review_type`` is an adopter-supplied free string
# (e.g. "systematic", "scoping") — rv itself has no fixed review-type
# vocabulary; a project that never sets one simply never has a
# ``by_type`` table to match and always resolves via the flat tier.
# ---------------------------------------------------------------------------

def _review_style_int_override(
    config: Any, key: str, *, review_type: str | None = None,
) -> int | None:
    """Shared 2-tier lookup (``by_type.<review_type>`` then flat) for a
    positive-int ``[review_style]`` knob. Returns ``None`` (never raises,
    never returns a non-positive/non-int value) when neither tier carries a
    usable override — the caller falls back to its own shipped default."""
    if config is None:
        return None
    raw = getattr(config, "_raw", {})
    override = raw.get("review_style", {})
    if not isinstance(override, dict):
        return None

    if review_type:
        by_type = override.get("by_type", {})
        if isinstance(by_type, dict):
            type_override = by_type.get(review_type, {})
            if isinstance(type_override, dict):
                value = type_override.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                    return value

    value = override.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return None


DEFAULT_MIN_QUERIES_PER_FACET: int = 3
DEFAULT_MIN_QUERIES_PER_POLE: int = 2
DEFAULT_MIN_HITS_PER_POLE: int = 3
# Search-primary redesign (thin-pole-as-finding): ONE bounded remediation
# attempt, then an autonomous under-searched-vs-sparse judgment — never a
# second/third fishing round. A still-thin pole after the one attempt is
# resolved by review.facet_remediation.resolve_facet_coverage's anti-gaming
# teeth (a recorded within-facet-query-append round proves genuine seeking),
# not by more budget.
DEFAULT_MAX_FACET_REMEDIATION_ROUNDS: int = 1


def get_min_queries_per_facet(config: Any = None, *, review_type: str | None = None) -> int:
    """Layer 1 generation-time floor (N) — every THESIS-ONLY facet (no
    declared counter pole; see ``sources.sweep.check_facet_breadth_floor``)
    must carry at least this many post-dedup distinct queries. Non-additive
    with ``get_min_queries_per_pole`` — a facet that DOES declare both poles
    is checked by the per-pole floor instead (2 x M=2 already >= N=3 by the
    shipped defaults), never by summing the two. Default 3.
    """
    override = _review_style_int_override(config, "min_queries_per_facet", review_type=review_type)
    return override if override is not None else DEFAULT_MIN_QUERIES_PER_FACET


def get_min_queries_per_pole(config: Any = None, *, review_type: str | None = None) -> int:
    """Layer 1 generation-time floor (M) — every DECLARED pole (thesis or
    counter list under a nested facet) must carry at least this many
    post-dedup distinct queries. Default 2.
    """
    override = _review_style_int_override(config, "min_queries_per_pole", review_type=review_type)
    return override if override is not None else DEFAULT_MIN_QUERIES_PER_POLE


def get_min_hits_per_pole(config: Any = None, *, review_type: str | None = None) -> int:
    """Layer 2 result-time floor (K) — every declared pole must surface at
    least this many DISTINCT (deduped) papers in the result pool, or it is
    "thin" and eligible for Layer-3 facet-remediation. Default 3 (aligned
    to ``sources.sweep.compose_sweep_result``'s independence ``floor=3``).
    """
    override = _review_style_int_override(config, "min_hits_per_pole", review_type=review_type)
    return override if override is not None else DEFAULT_MIN_HITS_PER_POLE


def get_max_facet_remediation_rounds(config: Any = None, *, review_type: str | None = None) -> int:
    """Layer 3 bound (R) — the facet re-search remediation loop's global
    round cap. One remediation attempt (default R=1), then an autonomous
    under-searched-vs-sparse judgment (``review.facet_remediation.
    resolve_facet_coverage``): a pole still thin after the attempt is
    either an under-searched retry target (never reached — R=1 means no
    second round) or a genuinely-sparse pole, recorded as a gap and passed
    (GO/GO-WITH-RESIDUE) — never a human HALT, UNLESS the anti-gaming
    teeth (a recorded ``within-facet-query-append`` round for that exact
    pole) find no evidence a round for it ever actually ran, in which case
    it still HALT-DECLAREs ("never genuinely searched").
    """
    override = _review_style_int_override(
        config, "max_facet_remediation_rounds", review_type=review_type,
    )
    return override if override is not None else DEFAULT_MAX_FACET_REMEDIATION_ROUNDS
