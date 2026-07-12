# Note conventions

Two layers: a **baseline** every project keeps, and a **profile** the hub adds on
top, optimized for the kind of work. The baseline is the keeper's to enforce; the
profile is the hub's to shape.

> **Every project's notes vault is an OKF bundle.**
> The note-vault format is **Open Knowledge Format** (markdown + YAML frontmatter + bundle-relative
> links) — vendor-neutral, agent-readable, git-diffable — universal across all projects. New
> projects are *born* OKF (the standup scaffold); an adopted vault is *migrated* to it.

> **Where notes sit relative to code/data/results/figures/manuscripts:** see
> [`doctrine/project-structure.md`](./project-structure.md) — the canonical CS-project
> folder-structure convention and the notes↔artifacts linkage rules (hashed frontmatter, not
> prose paths).

## Baseline — every project

1. **Overview** (`notes/index` or the repo README) — what this is, current status, and
   the **live questions** it's trying to answer. Kept short and current.
2. **Project log** — dated, append-only **thinking**: what I tried, what I saw, what it
   means, what's next. Dead ends included. This is the *reasoning* record, distinct from
   the `DEVLOG.md` (which is engineering decisions). One entry per working session.
3. **`DEVLOG.md`** — engineering decisions (the existing convention): `Done` /
   `Decisions` / `Open / next`.
4. **Findings** — atomic and citable, one fixed shape so you never re-decide the format:
   > **Claim.** One sentence.
   > **Evidence.** The run / data / figure it rests on (link it).
   > **Confidence.** strong / tentative / shaky — and why.
   > **Caveats.** What would make this wrong; what's not yet controlled for.
   > **Open.** The next question it raises.
5. **Figures with provenance — and designed, never default.** Every plot or diagram is
   given a deliberate visual identity — never raw matplotlib/Mermaid defaults. Do one
   design pass per *family* of plots → a reusable house style, then apply it across the
   set. Every figure records, in a sidecar or caption, the **script · data/run-id ·
   git SHA · date**. A figure you can't regenerate is a rumour; one that looks templated
   cheapens the work.
6. **Grounding** — every specific (number, name, quote) traces to a real source: a run,
   a file, a log entry. Never invent specifics. If it isn't grounded, mark it a
   *hypothesis*, not a finding.
7. **Links — OKF bundle-relative.** Connect notes so thoughts compound instead of pile up — a
   claim links to its evidence, a finding to the questions it opens. Links are written
   `[text](/section/slug.md)` (bundle-root-relative, **not** `[[wikilinks]]`); filenames are
   slugs. Broken links are *tolerated* (not-yet-written knowledge). **Structural edits go through
   the link-safe note tool, never by hand.**

## OKF conformance

> **Target spec:** Open Knowledge Format v0.1 —
> [`GoogleCloudPlatform/knowledge-catalog`](https://github.com/GoogleCloudPlatform/knowledge-catalog),
> `okf/SPEC.md`, pinned at commit
> [`d44368c15e38e7c92481c5992e4f9b5b421a801d`](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/d44368c15e38e7c92481c5992e4f9b5b421a801d/okf/SPEC.md).
> Each project's own notes bundle, the shared literature bundle, and the shared datasets
> bundle are each *individually* OKF-conformant — rv does not claim OKF conformance for
> the cross-bundle links between them (the spec is silent on cross-bundle references; see
> below).

Normative points rv honors:

- **One file per concept** — one markdown document per note, named by a stable slug.
- **Bundle-relative links** — `[text](/section/slug.md)`, resolved against the bundle
  root, never `[[wikilinks]]`.
- **Relationship type belongs in prose, not on the link.** "A link from concept A to
  concept B asserts a *relationship*. The specific kind of relationship … is conveyed by
  the surrounding prose, not by the link itself." An edge line therefore reads
  `- [display](/literature/<citekey>.md) — SUPPORTS: reason`, never a bracket prefix
  ahead of the link.
- **Consumers MUST tolerate broken links** — "a link whose target does not exist in the
  bundle is not malformed; it may simply represent not-yet-written knowledge." A reader
  never raises on a dangling link; a *producer*-side curation gate may still enforce
  resolvability before publish.
- **Unknown-field tolerance** — an unrecognized frontmatter `type` or extra key is never
  a hard failure; producers may add any additional keys.
- **Reserved filenames** — `index.md` (directory listing) and `log.md` (chronological
  update history) are reserved at any level of the bundle.

rv's declared extensions (deliberate, documented divergences beyond the spec's own
extension point):

- **Typed relationship prose token.** rv further constrains the free prose OKF permits
  into a mechanically-parseable leading token — `SUPPORTS:` / `CONTRADICTS:` /
  `PARTIAL:` / `EXTENDS:` — so a Noblit & Hare-style traversal can fold the corpus's
  comparative spine without re-deriving it from unstructured prose. A plain OKF reader
  still sees an ordinary markdown link followed by an ordinary sentence.
- **A cross-bundle backbone** (planned, not yet built) — a small `okf:<bundle>/<concept>.md`
  URI scheme letting a project's thin literature *overlay* point back at the shared
  central literature bundle, and similarly for the shared datasets bundle. The OKF spec
  is explicitly silent on cross-bundle references, nested bundles, and bundle-boundary
  resolution — this is a first-class rv extension for cross-project reasoning and
  synthesis, not a claim of spec-level cross-bundle conformance.

## Profiles — the hub's optimization

### Research

A **role-based Zettelkasten** — notes organised by *role, not topic*; topic emerges from
links, and an MOC is made only once a cluster has formed. Use the **link-safe OKF note tool** for
all structural edits — raw edits only for prose.

- **`literature/`** — one note per source. Frontmatter (type, authors, year, venue, url,
  zotero, status tags) + sections: cite callout · TL;DR · problem · method · metrics
  (deep for load-bearing papers) · key findings · **what I take from it** (promote
  reusable ideas to concept notes) · limitations · links.
- **`concepts/`** — one **atomic, falsifiable claim** per note, in your own words,
  linking to supporting/contradicting literature. "That's the layer where the argument lives."
- **`mocs/`** — Maps of Content threading concepts + literature into an argument.
- **`findings/`** — *the run-backed layer, and usually the missing piece.* Your **own
  results** as atomic notes in the baseline shape (claim → evidence → confidence → caveats →
  open), each **citing the run / score file and the figure** that backs it. This is what
  the keeper surfaces to the hub. Distinct from `concepts/` (which argue the domain).
- **specs + runs** — pre-registered analysis plans (anti-circularity) and raw run
  outputs; findings cite these by id.
- **Figures** — regenerable from a **tracked script** that reads the tracked result
  files, stamped with provenance. A manuscript panel you can't regenerate is a rumour.

### Product / build

- **`decisions/`** — short ADR-style notes: context → decision → consequences.
- **`roadmap`** — what's next and why; **`metrics`** — the numbers that matter, dated.
- The project log captures user/usage insight and direction shifts.

### Benchmark / eval

- **Results tables** per model/condition (one source of truth), with the run behind each.
- **Methodology** note (frozen) + a **comparisons** / leaderboard view.
- Findings cite the exact scored run; contaminated numbers are excluded, not plotted.

## The review (what the keeper checks)

Missing or stale log · a finding with no run behind it · a figure with no provenance ·
an ungrounded specific · contradictions between notes · a profile drifting from the
baseline. Flagged and sent back — not rewritten.
