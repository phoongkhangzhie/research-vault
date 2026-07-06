# Note conventions

Two layers: a **baseline** every project keeps, and a **profile** the hub adds on
top, optimized for the kind of work. The baseline is the keeper's to enforce; the
profile is the hub's to shape.

> **Every project's notes vault is an OKF bundle.**
> The note-vault format is **Open Knowledge Format** (markdown + YAML frontmatter + bundle-relative
> links) — vendor-neutral, agent-readable, git-diffable — universal across all projects. New
> projects are *born* OKF (the standup scaffold); an adopted vault is *migrated* to it.

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
