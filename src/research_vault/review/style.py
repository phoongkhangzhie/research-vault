# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/style.py — the review_tips config seam (SR-LR-1, section 5L.6).

SEAM CONTRACT
  ``get_review_tips(config=None)`` is the call-point for the review DAG nodes'
  spec/prompt.  The shipped default is the researcher's retrieval-grounded section 5L.6 prose:
  the saturation loop, counter-position/L-2 gate, and disconfirming obligation, each
  anchored to the systematic-review methodology it operationalizes (protocol
  pre-registration, both-direction snowballing, saturation-as-plateau, concept-centric
  synthesis).  Adopters override per lab/venue via the ``[review_style]`` section in
  ``research_vault.toml``.  Method anchors are attributed inline to their sources; a
  consolidated design-references bibliography is compiled at publish.

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

  ``get_saturation_backstop_waves(config=None)`` returns the wave-count cap for
  the review-snowball loop's TERMINATION BACKSTOP (SR-LR-1-BACKSTOP) — a
  guaranteed-termination escape hatch for when the primary saturation rule
  (2-consecutive-zero rounds) doesn't converge (an exploding-intersection RQ
  where every wave keeps finding more).  Adopted via
  ``[review_style] saturation_backstop_waves = <int>`` in
  ``research_vault.toml``; default 3.  This is additive, NOT a weakening of the
  primary rule — the primary rule still fires first whenever it converges; the
  backstop only fires when it doesn't, and the corpus is then recorded as
  ``backstop-terminated`` (bounded, NOT saturated), never conflated with a
  genuine saturation plateau.

Two halves independently mergeable:
  - Engineer ships this module (SR-LR-1 plumbing).
  - The researcher owns the default payload — the retrieval-grounded section 5L.6 strings.
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
    "review_screen_tips",
    "review_curate_tips",
    "per_paper_relate_tips",
    "review_synthesize_tips",
    "review_critic_tips",
})

# ---------------------------------------------------------------------------
# Default style preamble
# ---------------------------------------------------------------------------

_DEFAULT_PREAMBLE: str = (
    "You are conducting a structured, pre-registered, saturation-gated literature review "
    "following the Research Vault SR-LR-1 protocol (section 5L).\n"
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
# Default payload — researcher's review-prompt content (section 5L.6)
# ---------------------------------------------------------------------------
# The architect owns the keys/shape; the researcher owns the prose.
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
        "  - `seed_queries`: an ANGLE MATRIX (NG-3, breadth-then-depth), not near-"
        "synonyms — one query per DISTINCT analytical angle:\n"
        "        seed_queries:\n"
        "          by-method:     \"<query targeting the methodological angle>\"\n"
        "          by-outcome:    \"<query targeting the measured outcome>\"\n"
        "          by-paradigm:   \"<query targeting the theoretical paradigm>\"\n"
        "          by-population: \"<query targeting the studied population/domain>\"\n"
        "    A near-synonym seed set (8 rewordings of one angle) is the exact "
        "failure mode this fixes — it can miss a paper the depth snowball only "
        "recovers by luck. Each angle must probe a GENUINELY different facet of "
        "the question, not a paraphrase of another angle.\n"
        "  - `sources`: which source-adapters the width-sweep queries, e.g. "
        "`sources: [semantic-scholar, arxiv, openalex]` — the D4 default-on set. "
        "`pubmed` (biomedical) and a web/grey-literature pass are OPT-IN: add "
        "them only when the RQ specifically warrants that domain.\n"
        "  - `inclusion`: criteria a paper must satisfy (population, method, outcome).\n"
        "  - `exclusion`: criteria that disqualify a paper.\n"
        "  - `coverage_claim`: what a COMPLETE corpus would contain "
        "(e.g. 'all English papers 2015–2025 on X in venues Y').\n"
        "  - `counter-position` (REQUIRED — L-2 structural gate, section 5L.3/section 5M): "
        "the literature that would REFUTE the coverage claim — name the specific "
        "sub-literature or opposing view that must be actively sought. "
        "A review with an empty or missing `counter-position` cannot pass the "
        "coverage gate. This is the review's disconfirming obligation made structural.\n\n"
        "Anti-fishing (BOTH the angle matrix AND the sources list are frozen here): "
        "the protocol is a CONTRACT. Do not adjust inclusion/exclusion, the angle "
        "matrix, or the sources list after seeing results — breadth is a SCOPE-TIME "
        "commitment made BEFORE evidence, exactly like the criteria. A protocol "
        "revision (including widening seeds/sources) requires a new `review-scope` "
        "run and a new `approve-protocol` gate, never a mid-run edit."
    ),
    "review_screen_tips": (
        "★ Option C hybrid (review-loop-nodekind-drift-fix): the PARALLEL "
        "WIDTH-SWEEP itself is now a deterministic TOOL node (`review-search`, "
        "op `sweep`) — it ran automatically before this node and wrote "
        "`_search_hits.md`. This node is the THIN JUDGMENT LAYER on top: read "
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
        "★ Option C hybrid (review-loop-nodekind-drift-fix): the both-"
        "direction, multi-round saturation WALK itself is now a deterministic "
        "TOOL node (`review-snowball`, op `snowball`) — it ran automatically "
        "before this node and wrote `_corpus_raw.md` + `_saturation.md`. This "
        "node is the THIN JUDGMENT LAYER on top: concept-tag the raw corpus, "
        "apply inclusion/exclusion, and emit the FINAL `_corpus.md` (+ "
        "`_coverage-gaps.md` on backstop-termination).\n\n"
        "WHY (methodology): two named disciplines back the walk you're now "
        "curating.\n"
        "  - Both-direction snowballing (Wohlin, 2014): the tool op already "
        "followed BOTH backward references and forward citations each round — "
        "a database keyword search alone misses the citation neighbourhood.\n"
        "  - Saturation-as-plateau: theoretical saturation (Glaser & Strauss, "
        "1967) operationalized as a MEASURABLE stopping rule (Saunders et al., "
        "2018). The tool op's mechanical stop reads `_saturation.md`'s "
        "`stop_reason:` — exactly `saturated` (2 consecutive rounds, 0 new "
        "independent papers) or `backstop:N-waves` (the guaranteed-"
        "termination cap fired first).\n\n"
        "★ DECLARED CAVEAT (do not silently drop this — it is load-bearing): "
        "the FULL saturation stop rule this loop was designed against is '0 "
        "new independent papers AND 0 new concept-tags' for 2 consecutive "
        "rounds. Concept-tags are an LLM signal with no mechanical detector, "
        "so the tool op's mechanical stop covers the PAPER half only. YOU are "
        "responsible for the concept-tag half here: if verified concept-edges "
        "(from the upcoming `relate-<key>` fan-out) would plausibly have kept "
        "growing past the mechanical stop point — i.e. the corpus stopped "
        "gathering NEW papers but the CONCEPTS those papers would touch were "
        "still expanding — flag this as tag-under-counting / premature-"
        "plateau residue in `_coverage-gaps.md`, even when `stop_reason == "
        "saturated`. Do not wait for backstop-termination to raise this "
        "concern if you see it.\n\n"
        "Curation discipline (each `_corpus_raw.md` candidate):\n"
        "  1. Apply inclusion/exclusion from `_protocol.md`; exclude non-"
        "matching papers, recording the criterion (audit trail).\n"
        "  2. Lightweight concept-tag each accepted paper: which `concepts/` "
        "or `mocs/` regions does its abstract touch? (cheap signal; verified "
        "edges come later in the `relate-<key>` fan-out).\n"
        "  3. `[DERIVATIVE-OF:*]`-flagged candidates stay in the corpus "
        "(discount, never delete — provenance preserved) but are NOT double-"
        "counted against the concept/tag caveat above.\n\n"
        "Direction-starvation check: if `_saturation.md` shows a round flagged "
        "`DIRECTION-STARVED` (one direction consistently 0 while the other is "
        "active), flag this as a premature-plateau risk in `_coverage-gaps.md` "
        "regardless of `stop_reason`.\n\n"
        "On BACKSTOP-TERMINATION (`stop_reason: backstop:N-waves`), emit "
        "`_coverage-gaps.md` — the honest residue note. This is the anti-"
        "fabrication move: a bounded-not-saturated corpus must DECLARE its "
        "incompleteness, not hide it behind a green gate. Required contents:\n"
        "  1. A plain statement: 'terminated by backstop after N waves; "
        "corpus is bounded-not-saturated.'\n"
        "  2. Which `counter-position` sub-literature (from `_protocol.md`) "
        "remains open/under-explored, if any.\n"
        "  3. Which `concepts/`/`mocs/` regions were still growing (new "
        "concept-tags still appearing) at termination.\n"
        "  4. The un-screened candidate count: how many citation-graph hits "
        "were discovered in the final round but not yet round-processed.\n"
        "Do NOT emit `_coverage-gaps.md` on genuine `saturated` termination "
        "UNLESS the concept-tag caveat above applies — its presence signals "
        "an open frontier, so only write it when one genuinely remains.\n\n"
        "Output: `_corpus.md` — the FINAL, concept-tagged citekey list (table: "
        "annotation | citekey | title), replacing the raw candidate list. "
        "`_corpus.md` and `_saturation.md` (and `_coverage-gaps.md` when "
        "present) are the phase-boundary artifacts: the `coverage-gate` "
        "human-go reads them before authorizing Phase-2. `rv dag approve "
        "<run> coverage-gate` structurally reads `stop_reason:` from "
        "`_saturation.md` and, on backstop-termination, LOUDLY flags it to "
        "the approving human (⚠ backstop-terminated, NOT saturated) — a "
        "bounded corpus must never look identical to a saturated one at this "
        "gate."
    ),
    "per_paper_relate_tips": (
        "Distill this paper into an OKF `literature/<citekey>.md` note using a "
        "PRINCIPLED 5-MOVE READING PROTOCOL — this is the reading DISCIPLINE, "
        "not a rigid schema (the note stays free-form structured thought; only "
        "these mandatory questions must be answered).\n\n"
        "WHY (methodology, Wave 0 — Reading, PR-1..5): a review is concept-centric, "
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
        "benchmark study has scores, not mechanisms).\n\n"
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
        "add a `## Related papers` body section with ONE line per relation:\n"
        "      `- [SUPPORTS|CONTRADICTS|PARTIAL|EXTENDS] <citekey> — <why, "
        "in your own words>`\n"
        "    ([SUPPORTS]≈reciprocal, [CONTRADICTS]≈refutational, "
        "[PARTIAL]/[EXTENDS]≈line-of-argument — the bracket TAG derives the "
        "relation kind mechanically.) The trailing `(reciprocal|refutational|"
        "line-of-argument)` mirror is OPTIONAL — you may add it for clarity, "
        "but the TAG is authoritative: if you add it and it disagrees with the "
        "tag, the tag wins and the disagreement is surfaced, never silently "
        "resolved. Do NOT omit the tag+target to save a word — a line under "
        "this heading that opens `- [` and does not fully parse (a typo'd tag, "
        "a missing target) is a hard FAIL, surfaced loudly, never silently "
        "dropped. The typed TAG + TARGET are required and checked; the "
        "RELATION'S SUBSTANCE (why/where it holds) stays free prose — a "
        "relation reduced to a bare tag with no reasoning is as thin as no "
        "relation at all (do not over-rigidify this). These are traversed "
        "downstream by `rv review <project> relations <scope>` — "
        "`review-synthesize` reads that command's output instead of "
        "re-deriving the comparative spine from scratch, so a real edge here "
        "is carried structure, not decoration. Catch near-tautologies: if a "
        "'relation' is just a paraphrase of the other paper's claim, it isn't "
        "a relation. When `no` (this paper doesn't bear on the corpus yet), no "
        "`## Related papers` section is needed — a plain `- ` bullet with no "
        "bracket elsewhere in the note is ordinary prose and is never treated "
        "as an edge attempt.\n\n"
        "  MOVE 5 — Relate to concepts + flag provenance (unchanged this "
        "wave). Attach stance-bearing concept edges (see below), and record "
        "the retrieval caveat honestly (verified vs inferred).\n\n"
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
        "empirical / theoretical / counter-position (PR-4: replaces the old "
        "overloaded `stance` field, which did contradictory double duty as "
        "both a one-word tag and a full synthesis paragraph)\n"
        "  - `position`: a free-form narrative — how this paper relates to the "
        "review question, in your own words (PR-4: the narrative half `stance` "
        "used to carry; this is where the real synthesis material lives — "
        "write as much as the paper warrants)\n"
        "  - `result_reported`: Move 3/PR-5 — exactly `yes` or `no`\n"
        "  - `paper_relations_sought`: Move 4/PR-2 — exactly `yes` or `no`\n"
        "  - `concepts`: comma-separated concepts/ or mocs/ regions this touches\n\n"
        "Verified concept-edges (Move 5, body of the note):\n"
        "  - Draw edges ONLY from the note fields above — never invented.\n"
        "  - Format: `[SUPPORTS] concepts/<c>.md — <one sentence why>`\n"
        "  - Format: `[CONTRADICTS] concepts/<c>.md — <one sentence why>`\n"
        "  - Format: `[PARTIAL] concepts/<c>.md — <one sentence why>`\n"
        "  - A `[CONTRADICTS]` edge is equally valuable to a `[SUPPORTS]` edge — "
        "the disconfirming obligation applies here too.\n\n"
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
        "A paper is more than prose (§7.5 LEAN — record what you see, never "
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
        "★ TRAVERSE, DON'T RE-DERIVE (Wave 0 — Reading, PR-2): the relate-<key> "
        "fan-out already discovered and TYPED the paper→paper comparative "
        "relations (reciprocal/refutational/line-of-argument) — run "
        "`rv review <project> relations <scope>` FIRST and read its output "
        "before writing the comparative parts of your synthesis. This turns "
        "the survey's comparative spine from re-derived prose into carried "
        "structure. Do NOT hand-re-read every literature/ note trying to "
        "reconstruct which papers refute/extend which — the deterministic "
        "command already surfaces every typed edge in the corpus. The same "
        "command ALSO surfaces malformed edge lines (a typo'd tag, a missing "
        "target) and dangling edges (a target citekey not yet in the corpus) "
        "under its own headed sections — never silently absorbed into a "
        "clean-looking total; if either is non-empty, flag it in your "
        "coverage cross-check below rather than quietly ignoring it.\n\n"
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
        "whether the plateau meets theoretical-saturation criteria (Glaser & Strauss, "
        "1967) as operationalized into a measurable stopping rule (Saunders et al., "
        "2018) — a plateau that is direction-starved (one snowball direction dry; cf. "
        "Wohlin, 2014) or tag-under-counted is PREMATURE, not saturated. Axis 4 "
        "enforces the disconfirming obligation: a review that only confirms is "
        "fishing, so the pre-registered counter-position must be sought, not merely "
        "declared.\n\n"
        "Judge FOUR axes (each can independently issue `[BLOCK]`):\n\n"
        "1. SATURATION PLATEAU — is it real or premature? (section 5L.2)\n"
        "   Read the `_saturation.md` curve. Check:\n"
        "   - Did the curve plateau at round K with 0 new citekeys AND 0 new concept-tags "
        "for 2 consecutive rounds? (genuine saturation)\n"
        "   - OR did it plateau while one direction (forward OR backward) stayed dry? "
        "(direction-starved — flag as `DIRECTION-STARVED` and issue `[BLOCK]`)\n"
        "   - OR did verified concept-edges (from `relate-<key>` notes) consistently "
        "outrun the cheap concept-tags? (tag-under-counting — issue `[BLOCK]`)\n"
        "   A plateau reached with direction-starvation or tag-under-counting is PREMATURE.\n\n"
        "2. ORPHAN CONCEPTS/MOCS — soft flag (do not block, but list)\n"
        "   Do NOT hand-stem-match note filenames to corpus citekeys — run "
        "`rv review <project> coverage <scope>` for the deterministic orphan list "
        "(keyed by `citekey:` frontmatter field, not filename stem).\n"
        "   Report orphan count and keys from that output. Issue a soft warning, not a `[BLOCK]`.\n\n"
        "3. PROTOCOL ADHERENCE — did the corpus honor the frozen criteria?\n"
        "   Compare the accepted corpus against `_protocol.md` inclusion/exclusion.\n"
        "   Any paper included that violates inclusion criteria = fishing = `[BLOCK]`.\n"
        "   Any paper excluded that meets inclusion criteria = coverage gap = `[BLOCK]`.\n\n"
        "4. COUNTER-POSITION (L-2 gate — REQUIRED, section 5L.3/section 5M)\n"
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
        - The default is the researcher's review-prompt content (section 5L.6); adopters own the prose.

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


# ---------------------------------------------------------------------------
# Saturation backstop config seam (SR-LR-1-BACKSTOP)
# ---------------------------------------------------------------------------

DEFAULT_SATURATION_BACKSTOP_WAVES: int = 3


def get_saturation_backstop_waves(config: Any = None) -> int:
    """Return the review-snowball TERMINATION BACKSTOP wave-count cap.

    The primary saturation stop-rule (2-consecutive-zero rounds, §5L.2) is
    principled but not guaranteed to converge — an exploding-intersection
    review question (every wave finds more) can run it unboundedly.  This
    backstop is HyperResearch's termination guarantee, additively grafted
    onto rv's principled primary rule (rv keeps the primary rule as the
    preferred stop; HR has no saturation notion at all and just caps at N
    waves, "proceeding anyway, marking gaps thin" — rv's backstop mirrors
    that cap but ALSO requires the honest residue declaration in
    ``_coverage-gaps.md``, section 5L.2-backstop).

    Args:
        config: a loaded Config instance (or None for the shipped default).
                If the config has ``[review_style] saturation_backstop_waves = N``
                (a positive int), that value overrides the default.

    Returns:
        The wave-count cap (int, >= 1).  Default 3.  A non-int, non-positive,
        or missing override falls back to the default (never a crash, never a
        silently-accepted nonsensical cap like 0 or a negative number).

    sr: SR-LR-1-BACKSTOP
    """
    if config is not None:
        raw = getattr(config, "_raw", {})
        override = raw.get("review_style", {})
        if isinstance(override, dict):
            value = override.get("saturation_backstop_waves")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                return value
    return DEFAULT_SATURATION_BACKSTOP_WAVES


# ---------------------------------------------------------------------------
# Remediation round-cap config seam (NG-6a §4.3 bound 2)
# ---------------------------------------------------------------------------

DEFAULT_REMEDIATION_MAX_ROUNDS: int = 2


def get_remediation_max_rounds(config: Any = None) -> int:
    """Return the NG-6a autonomous coverage-gap remediation round cap.

    One of the three independent termination bounds on the bounded
    remediation loop (§4.3): even a pathological "one new paper per wave"
    corpus cannot exceed this many autonomous remediation rounds before the
    loop declares residue and surfaces for human review.

    Args:
        config: a loaded Config instance (or None for the shipped default).
                If the config has ``[review_style] remediation_max_rounds = N``
                (a positive int), that value overrides the default.

    Returns:
        The round cap (int, >= 1). Default 2 (conservative, per the design
        doc). A non-int, non-positive, or missing override falls back to the
        default — fail-closed (a missing/malformed counter reads as "the
        conservative default", never as "unbounded").

    sr: NG-6a
    """
    if config is not None:
        raw = getattr(config, "_raw", {})
        override = raw.get("review_style", {})
        if isinstance(override, dict):
            value = override.get("remediation_max_rounds")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                return value
    return DEFAULT_REMEDIATION_MAX_ROUNDS
