# The manuscript loop — reach for this when…

The **third loop family**, alongside `rv experiment` and `rv review`. Where those two
**build** `notes/` (the crew-reasoning pillar), the manuscript loop **transforms** `notes/`
into `manuscripts/<slug>/` (the user-facing deliverable pillar) — see
[project-structure.md](./project-structure.md)'s "two content pillars" section for the
structural framing this loop realizes.

```
  KNOWLEDGE LOOPS                          THE MANUSCRIPT LOOP
  (experiment, lit-review)                 (rebuilt, by TYPE)
  build ──►  notes/         ──transform──►  manuscripts/<slug>/     ──►  user-facing deliverable
            (crew reasoning)  by type       (report.md, sections/,
                                             references.md, figures/)
```

**Trigger:** reach for `rv manuscript <project> new <slug> --type <type>` when you have
enough OKF notes (a completed or substantially-saturated `rv review` pass, or a body of
`experiments/`/`findings/` notes) and need a **submittable document** — a survey/review
paper (`type: lit-review`, the only type shipped) or, in the future, a results paper
(`type: experiment-paper`, designed for but not built). This is the synthesis step, distinct
from the knowledge loops that produce the notes it consumes: don't hand-write markdown sections
and hand-collect citations/numbers/equations from OKF piles — the manuscript loop's per-manuscript
folder is what the hermetic references build, the hard fidelity gates, the equation machinery, and
the review-revise board all plug into.

Design of record: the survey type-system design (the full type-system design, PR breakdown,
and the resolved operator decisions this loop was built to).

---

## The end-to-end walkthrough — the `lit-review` (survey) path

This is the same shape `rv orient`/the other loops document: **a reader who has never seen
the capability can run it end-to-end from this section alone.**

### 1. Scaffold — `rv manuscript <project> new <slug> --type lit-review`

Creates the per-manuscript folder and, because `lit-review` HAS a Phase-1 (the
framework-selection sub-loop, design §5), a Phase-1 DAG manifest:

```
manuscripts/<slug>/
├── _manuscript.md        # control + frontmatter: type, spine, corpus_hash, run_state
├── report.md
├── sections/*.md
├── references.md          # hermetic — built from notes/literature/ frontmatter
└── figures/
```

Convention (zero-config for the common case): if this manuscript summarizes a completed
`rv review` pass, use the **same slug** as that review's scope id — the loop reads its frozen
corpus from `reviews/<slug>/_corpus.md` (see "Known limitations" below for the override gap).

### 2. Phase-1 — the framework-selection sub-loop, human-owned

```
scope ─► framework-propose ─► [HG: approve-framework]
```

- **`scope`** renders the PRISMA inclusion ledger from `coverage_report()` and stamps the
  corpus hash (the stale-corpus guard).
- **`framework-propose`** reads `mocs/`/`concepts/`/`gaps/` and proposes **four candidate
  organizing shapes** — pipeline/lifecycle, maturity/evolution arc, N-axis orthogonal
  taxonomy, coupled problem/solution taxonomies — each defended from the MOCs, with a
  `_framework-candidates.md` menu. It **proposes, never commits.**
- **`approve-framework`** (human-go) — you pick / shape / nest / go custom, writing the spine
  (`spine_shape` + `branches`) into `_manuscript.md`. `check_framework_gate` BLOCKs a
  non-empty freeze attempt with an empty spine. **The organizing framework is a human
  commitment (design D5) — it cannot be reliably discovered by the machine.**

Run Phase-1 with `rv dag run manuscripts/<slug>/phase1-dag.json`, then
`rv dag approve <run_id> approve-framework` once the spine is frozen.

### 3. Expand — `rv manuscript <project> expand <slug>`

Emits the Phase-2 draft+review manifest generically from the type's `section_set`. For
`lit-review`, the 9-row survey section table (design §3): abstract (drafted last), introduction,
PRISMA scope & method (mechanical), the organizing framework/taxonomy (human-shaped, drafted
from the frozen spine), thematic sections (one node covering all N framework branches — see
"Known limitations"), cross-cutting critical analysis, open problems, conclusion, and
references (mechanical, from the hermetic references build). Numbers, citations, table cells, and pivotal
equations are **injected as data**, never hand-typed by the writer (the `results_inject.py`
discipline, extended to equations by the don't-drop-the-math machinery).

### 4. Draft + the hard fidelity gates (re-fired every round)

Every draft/revise round runs, regardless of judge availability:

- **Hermetic references build + citation-resolve gate** — every `[[citekey]]` wikilink in the
  draft resolves to a real `literature/` note; `references.md` is built deterministically from
  frontmatter, no live Zotero call in the compile path. **Hard BLOCK**, always runs, no judge
  dependency.
- **Coverage gate** — re-derives the frozen corpus hash and PRISMA counts; a revise that
  narrows scope to shrink the denominator is a **hard BLOCK**.
- **Equation-fidelity gate** — for each equation the extractor mined from source notes
  (`literature/`'s `## Key equations` block + `key_equations:` criticality ledger), confirms
  a form of it survives in the draft. **SIGNAL only — never BLOCK, even for a
  marked-critical equation** (a deliberate divergence from the design doc's own BLOCK
  recommendation for marked-critical equations — the resolved operator call was SIGNAL for
  both marked-critical and unmarked; see "Known limitations").
- **Support-matcher** (the LLM-judged gate) — every synthesized claim traces to a
  substantiating `literature/`/`concepts/` note (4-verdict `[SUPPORTS|PARTIAL|CONTRADICTS|
  ABSENT]`, disconfirm-first, verbatim-span-or-BLOCK). **Runs ONLY when a judge is configured**
  (`RV_JUDGE_MODEL` + `ANTHROPIC_API_KEY`) — **absence is surfaced loudly** ("support-matcher
  gate NOT RUN"), never a silent skip. (The former cold-read self-containment critic that once
  shared this seam was removed — SIGNAL-only, non-actionable under hands-off autonomy,
  redundant with the review board's own coherence scoring + RD-6's term-definition rule.
  the operator's call, see DEVLOG. Single-cite paragraphs / orphan prose are now caught by the review
  board's SYNTHESIS-VS-ENUMERATION adversary, below.)

### 5. Review — `rv manuscript <project> review <slug>`

The **2-round × 3-reviewer conference-style board** (design §9): each round, 3 FRESH
independent adversarial reviewers score 8 dimensions with a written justification each
(ARR-style), then a meta-review aggregates by **MIN-across-3 (floor, never average)** on the
FLOOR axes — citation fidelity and coverage/search-reproducibility — the axes bound to
provenance the machine already holds, so a reviewer cannot inflate them past the ledger.
Framework soundness is SURFACE (scored + shown, never autogate — you own the spine).
Synthesis-not-enumeration and gap-validity are SIGNAL (this board's own weak-flags). A
rebuttal/revise step between rounds redrafts failing sections and **re-fires** the fidelity +
equation + coverage gates. Every round also runs three **canary probes** through the same
judge — known-STRONG (must not floor), known-WEAK (must not ceiling), and the **mandatory
literal annotated-bibliography probe** (must NOT clear on the SYNTH dimension — the one
distinction the whole `lit-review` type exists to enforce). Any probe out of bounds **aborts
the round loudly** — the scores are not trusted.

If the framework/taxonomy critic judges the spine incoherent across **two or more
consecutive rounds**, the board writes a **reframe-escalation** (misfits + candidate
encapsulating reframes) to the review payload — it **proposes, never auto-reframes** (see
"Known limitations" — the CLI re-entry point is not yet wired).

### 6. Approve — `rv dag approve <run_id> approve-manuscript`

The human makes the final call. A `cleared: true` review verdict is **necessary, never
sufficient** — no overall/average score gates anything, and clearing the board is not the
same as approving the manuscript.

### Output

`manuscripts/<slug>/{report.md, sections/*.md, references.md, figures/}` — a self-contained,
hermetically-buildable folder; `references.md` reproducible offline from the corpus alone, no
network call reachable from the compile path.

---

## Known limitations (surfaced honestly — accumulated across the build wave)

1. **Single-thematic-node v1.** Design §3's "N thematic sections" (one per frozen framework
   branch) is represented as **one** Phase-2 section node covering all N branches, not a true
   per-branch DAG fan-out. Consequence: a branch's review-revise failure re-drafts **all**
   thematic content, not just the failing branch. True fan-out needs the type-generic core's
   Phase-2 builder to accept a per-manuscript dynamic section-set — flagged as core-level
   follow-on work, not built here.
2. **The `reviews/<slug>/` convention, no `--corpus` override.** A manuscript's frozen corpus
   is resolved by **slug match**: `manuscripts/<slug>/` reads `reviews/<slug>/_corpus.md`. A
   manuscript that draws on a differently-named review (or synthesizes across more than one)
   has no override flag today — a `--corpus <review-slug>` follow-on is the natural fix.
3. **The gate judge-guard.** The hermetic references/citation-resolve gate and the coverage gate
   are deterministic and **always** run (hard BLOCK, no judge dependency). The equation-fidelity
   gate is deterministic-first with an LLM-judge fallback but is **SIGNAL only, never BLOCK** —
   even a marked-critical equation silently dropped surfaces as a flag, not a hard stop (a
   deliberate resolved divergence from the design doc's own REC of BLOCK for marked-critical;
   the operator's call was SIGNAL for both marked-critical and unmarked). The support-matcher
   gate requires a configured judge (`RV_JUDGE_MODEL` + `ANTHROPIC_API_KEY`) —
   when absent, it does not silently no-op; it surfaces a loud "gate NOT RUN" notice, and a
   manuscript can still reach `approve-manuscript` without it having fired. The human is the
   backstop in both cases.
4. **SYNTH = SIGNAL, not a hard gate.** The synthesis-vs-enumeration dimension (an annotated
   bibliography detected in the drafted sections) is a SIGNAL-class review-board weak-flag, fed
   to the worst-findings list — it is **detected and surfaced**, never auto-blocked. The
   mandatory annotated-bib canary (item 3 above) exists precisely because this dimension is
   scored, not gated: it proves the judge is not blind to the failure the whole type exists to
   catch, but catching a REAL draft's enumeration problem is still a human read of the
   surfaced SIGNAL findings.
5. **ARR justifications are surfaced, not hard-gated.** Every reviewer score carries a written
   justification (the conference-style ARR discipline), and a missing justification is recorded
   as `missing_justifications` audit metadata on the reviewer-node result — but an unjustified
   score is **not** zeroed or auto-rejected. It is visible, never silently accepted, but the
   human reads the audit trail rather than the machine enforcing it.
6. **`--reframe` is not yet a wired CLI flag.** Design §5.1's reframe-the-spine escalation
   (when the framework critic judges the spine incoherent across ≥2 consecutive rounds) builds
   and surfaces a real escalation payload — misfits + candidate reframes — in the review
   output. But a `--reframe <prior-slug>` flag on `rv manuscript new` (re-entering Phase-1 with
   those misfits/candidates pre-loaded) is **not implemented**; today a human re-scaffolds
   manually via a fresh `rv manuscript <project> new <new-slug> --type lit-review` and
   hand-carries the escalation's misfits/candidates into the new framework-propose round.

None of these are silent — each surfaces a message or an audit field naming the gap. They are
listed here so the next engineer (or the operator) doesn't have to rediscover them by reading
six PRs' worth of DEVLOG entries.

---

## Reuse map (what this loop shares with `rv experiment`/`rv review`)

No new DAG-engine primitive, no new OKF note *type*. Same scaffolder pattern (two-phase
`new`→Phase-1→human-go→`expand`→Phase-2), same deterministic dispatch-brief mechanism
(`dag/brief.py`), same `RunState.meta` skip-once-cleared convention, same structural
human-go-gate wiring into `rv dag approve`. The one frontmatter-vocabulary addition —
`key_equations:` / `repo:` / `artifacts:` on the `literature` note, populated by the
`rv review` loop's `relate-<key>` node — is documented at its extraction site
(`review/style.py`'s `per_paper_relate_tips`) and consumed here by `manuscript/equations.py`.
See [honesty-gates.md](./honesty-gates.md) and [review-board.md](./review-board.md) for the
adversarial-judge craft the fidelity gates and the review board are built to.
