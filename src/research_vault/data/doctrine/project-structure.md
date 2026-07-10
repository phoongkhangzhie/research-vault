# Project folder structure — the CS-project convention

rv already owns the **OKF note types** (`literature/ concepts/ methods/ experiments/
findings/ mocs/ gaps/` + shared `datasets/`) and their frontmatter/lint contract. This page
owns the layer above: how those notes sit next to **code, data, results, figures, and
manuscripts** in a project repo, and how a note *links* to a result or a dataset as a
**stable graph** rather than a brittle path string.

**The crux:** *a note must reference an artifact by a path that survives a code refactor.*
Burying results under `code/src/...` breaks every citing note the moment the package is
reorganised. The fix is structural: hoist `results/ data/ figures/ manuscripts/` to the
repo root as convention-frozen roots, and make the machine-checkable link a hashed
frontmatter field, not a prose path.

**Sibling page:** this page owns the folder layout (*where* things sit); its sibling
[`doctrine/code-conventions.md`](./code-conventions.md) owns the craft *inside* `code/`
(tested libraries, reproducibility, testing discipline, releasability) and the CHECK-tagged
gates that enforce the checkable slice of it.

## The two content pillars

A project repo carries exactly two **content pillars** — the crew's reasoning and the
user's deliverable — plus the **mechanical roots** that supply their raw material:

- **`notes/` — the reasoning pillar.** The crew-facing OKF knowledge base: atoms built by
  the knowledge loops (`experiment`, `lit-review`, …). This is *how the crew thinks* —
  literature notes, concepts, methods, experiments, findings, MOCs, gaps.
- **`manuscripts/` — the deliverable pillar.** The user-facing layer: papers, surveys, and
  reports the human actually reads. Built by the **manuscript loop**, which transforms
  `notes/` into a submittable document **by type** (`type: lit-review` today; a future
  `type: experiment-paper`). Peer of `notes/`, not a member of the mechanical-roots pile.
- **The mechanical roots (`code/ data/ results/ figures/`)** are supporting machinery, not
  content pillars in their own right — they hold the code that runs, the inputs it reads,
  the outputs it computes, and the designed figures drawn from those outputs. Notes and
  manuscripts *reference* these roots (the linkage convention below); they don't live in
  them.

```
  KNOWLEDGE LOOPS                          THE MANUSCRIPT LOOP
  (experiment, lit-review, …)              (by type)
  build ──►  notes/         ──transform──►  manuscripts/<slug>/     ──►  user-facing deliverable
            (crew reasoning)  by type       (report.md, sections/,
                                             references.md, figures/)
```

**Reach for the manuscript loop** (`rv manuscript <project> new <slug> --type <type>`) when
you have a saturated `notes/` corpus and need a submittable document out of it — the
notes-to-manuscript synthesis step, distinct from the knowledge loops (`rv experiment`,
`rv review`) that build `notes/` in the first place. See
[manuscript-loop.md](./manuscript-loop.md) for the full end-to-end walkthrough (scaffold →
framework approval → expand → the 2×3 review board → the fidelity gates → the
`manuscripts/<slug>/` output) and the known limitations accumulated across the build.

## The canonical top-level tree

**Repo root IS the vault.** `source_dir = <repo>/notes`. No `vault/` wrapper — the project
repo is a sibling of the rv instance, its own git repo; the rv instance registers it via
`source_dir`. `research_vault.toml` (the config SSOT) lives in the **rv instance**, not the
project repo — the project repo carries only its own content.

```
<project>/                        # git repo root = the OKF vault (source_dir = ./notes)
├── notes/                        # OKF knowledge base — the ONLY note store
│   ├── literature/                 (rv OKF_TYPES; project-scoped)
│   ├── concepts/
│   ├── methods/
│   ├── experiments/               pre-registration + results-provenance notes
│   ├── findings/
│   ├── mocs/
│   ├── gaps/
│   ├── log/                       dated reasoning log (project-log baseline)
│   ├── index.md                   overview + live questions
│   └── _templates/                 note templates
│                                 # NOTE: datasets/ is SHARED — it lives in the rv
│                                 # instance's datasets_root, NOT here. See §"Linkage" P3.
├── code/                         # ALL source + tests + project tooling
│   ├── src/…                       package(s) — freely refactorable (nothing links INTO here)
│   ├── tests/
│   └── tools/
├── data/                         # raw / external INPUTS — read-only, never written by code
├── results/                      # ★ the SINGLE home for computed outputs (see below)
│   ├── runs/                       raw run outputs: *.jsonl, logs, checkpoints (large → ignored)
│   └── scores/                     computed metrics: *.csv/*.json (small → TRACKED, the SSOT)
├── figures/                      # designed, provenance-stamped figures — TRACKED
├── manuscripts/                  # ★ the deliverable pillar — one self-contained folder
│   └── <slug>/                     per manuscript (NOT an OKF-typed taxonomy — see below)
│       ├── _manuscript.md           control + frontmatter: `type:` (e.g. lit-review), spine
│       ├── report.md
│       ├── sections/*.md
│       ├── references.md           hermetic — built from notes/literature/ frontmatter
│       └── figures/
├── architecture.md                the Architect's living Mermaid map (USER-OWNED)
├── DEVLOG.md                      engineering decisions (Done / Decisions / Open-next)
├── pointers.md                    read-fresh crew pointers
├── library.json                   corpus index (rv cite)
├── .agents/                       per-project agent memories
├── .claude/                       crew hats + skills
├── .gitignore                     rv framework .gitignore + project rules
└── README.md
```

**Why the four hoisted roots (`data/ results/ figures/ manuscripts/` at root, not under
`code/`):** these are the artifact classes that notes reference. Keeping them at the repo
root, sibling to `code/`, means their paths are **decoupled from the package layout** —
`code/` can be reorganised at will and no note reference breaks. This is the single
structural move that makes the linkage convention below hold. `manuscripts/` gets the same
treatment for the same reason (a manuscript cites `results/` and `figures/` paths that must
survive a `code/` refactor) — but as the deliverable pillar it also carries its own
per-manuscript-folder convention, below.

## The per-manuscript folder (not an OKF taxonomy)

`manuscripts/` holds **one self-contained folder per manuscript** — deliberately *not* a
typed taxonomy the way `notes/` is (`literature/ concepts/ methods/ …`). There won't be
enough manuscripts in a project to warrant one; a flat per-slug folder is the right grain.

```
manuscripts/<slug>/
├── _manuscript.md        # control + frontmatter: type, spine, corpus_hash, run_state
├── report.md
├── sections/*.md
├── references.md          # hermetic — built from notes/literature/ frontmatter
└── figures/
```

Each `_manuscript.md` carries a **`type:` field** naming which manuscript-loop
specialization built it — `lit-review` (a review/survey paper) today; a future
`type: experiment-paper` (a results paper) would slot in the same folder shape. The type
determines the section-set and transformation the manuscript loop applies to `notes/`; the
folder convention itself is type-generic. (See [honesty-gates.md](./honesty-gates.md) and
[review-board.md](./review-board.md) for the fidelity-gate craft the manuscript loop's
review-revise machinery is built to.)

## Results / runs / scores convention

### One home, two frozen subdirs

All computed outputs live under `results/`. The `runs/` vs `scores/` split is
convention-frozen:

| Subdir | Contents | Format | Git policy | Role |
|---|---|---|---|---|
| `results/runs/` | raw model outputs, logs, checkpoints | `*.jsonl`, `*.log`, `*.ckpt.json` | **gitignored** (large); integrity via hash + optional W&B/artifact push | the evidence trail |
| `results/scores/` | computed metrics / tables | `*.csv`, `*.json` | **TRACKED** (small; the citeable SSOT) | what findings cite |

### Naming rule

`results/{runs,scores}/<experiment-slug>[__<variant>].<ext>` — `<experiment-slug>` SHOULD
match the `experiments/<slug>.md` note stem, so note↔artifact correspondence is nominal,
not guessed (`experiments/hfs-landscape.md` ↔ `results/scores/hfs-landscape.csv`). Use
`__<variant>` (double underscore) for model/condition suffixes
(`hfs-landscape__haiku`), keeping the slug prefix greppable. When one note anchors
multiple scores (M>1), correspondence is carried explicitly by the `scores:` list (see
P2/P4 below) — each entry names its own file, so the M CSVs need not share a prefix and
may keep topical names.

### Figures are a separate class

`figures/` is **not** under `results/`: a figure is a *designed* deliverable (designer /
house style, charter §3), not a raw computation. Each figure carries provenance in a
sidecar or caption — script · data/run-id · git SHA · date (note-conventions #5).
Tracked by default (few, small, the manuscript payload).

### Tracked vs ignored

- **Tracked:** `results/scores/**`, `figures/**`, all `notes/**`, `manuscripts/**`.
- **Gitignored:** `results/runs/**`, large `data/**` inputs — their integrity lives in
  **hashes** (experiment-note `scores[].hash`, dataset-note `hash`) + optional
  W&B/artifact push, not in git.

## The notes↔artifacts linkage convention

Four principles that keep the note graph stable:

**P1 — Reference only convention-frozen roots; never `code/`.** A note may reference an
artifact only under `results/`, `data/` (via a datasets note, see P3), `figures/`, or
`manuscripts/`. It MUST NOT reference a path under `code/`. `code/` is refactorable; the
frozen roots are not. Paths are repo-root-relative (`results/scores/hfs-landscape.csv`),
resolved from `source_dir`'s parent.

**P2 — The machine-checkable link is a hashed frontmatter field, not a prose path.** An
experiment note's primary link to its result is the **`scores:` list** — each entry a
computed **score** artifact's `location` + `hash` (sha256). `check_result_provenance`
verifies **each** entry's existence + hash match, and `rv note <p> check` / the DAG
complete-gate enforce every entry. Prose mentions in the note body are human-facing
secondary; the frontmatter `scores:` list is the source of truth. (Legacy flat
`results_location`/`results_hash` remain a read-only 1-element shorthand via a
normalization shim.)

**P3 — Data is linked through a `datasets/` provenance note, never a hand-copied path.** A
raw input in `data/` (or a remote DOI/URL) gets a `datasets/<slug>.md` note carrying
`location` + `hash`. Experiment notes link it via `repro_dataset_id: datasets/<slug>`.
`datasets` is a **shared** OKF type → the note lives in the rv instance's `datasets_root`,
shared across projects; `data/` in the repo is just the (optionally gitignored) bytes it
points at. Lineage is structural, so a `data/` reorg touches one provenance note, not N
findings.

**P4 — The computed score(s) are the anchors; raw runs and W&B are supplementary.** Each
`scores:` entry points at a `results/scores/<slug>.csv` SSOT table (the thing findings
cite), **not** at the raw `results/runs/*.jsonl`. This is what lets **any N runs → any M
scores collapse into one experiment note**: each score CSV is its own hashed anchor (the
`scores:` list, each entry `location` + `hash`); the individual runs are linked as a
supplementary `runs:` list. All four cardinalities (1→1, N→1, 1→M, N→M) share one schema.

### Formal path-stability rules

1. Note→artifact references resolve repo-root-relative and target only `results/ data/
   figures/ manuscripts/`.
2. `results/scores/` (computed) vs `results/runs/` (raw) is frozen; `<slug>` matches the
   note stem.
3. Each `scores:` entry's location+hash pair is the integrity contract; the path is only
   the locator. If an artifact must move, the hash still identifies it — the convention's
   job is to make moves rare by freezing the roots.
4. Inter-note links stay OKF bundle-relative (`[text](/findings/slug.md)`, not wikilinks);
   structural edits go through the link-safe note tool (note-conventions #7).

## Cold-switching into a project

`pointers.md` and `architecture.md` at the repo root (above) are the two artifacts a
cold context-switch needs. `rv orient <slug>` bundles them with the operational `rv
status` read in one call — see [coordination.md](./coordination.md)'s "`rv orient` —
the one-shot cold-context-switch primitive" section for the trigger and the blessed
`pointers.md` MUST-contain skeleton.

## Project lifecycle — register ↔ stand down

Everything above describes what a *live* project looks like. The lifecycle that gets a
project into (and out of) that state is a small, KEEP-bucket set of primitives — distinct
from the loop step-verbs that collapse into DAG node-execution (the verb-consolidation
program, D1..D5): these are standalone tools an operator/Alfred calls directly, not steps
a loop's DAG carries.

- **Register** — `rv project add`/`rv project new` scaffolds a project into the registry
  (`research_vault.toml`'s `[projects.<slug>]` table) and lays down the folder convention
  this page describes (`notes/`, `manuscripts/`, the mechanical roots). This is the entry
  point every other section on this page assumes has already run.
- **Operate** — the project lives through the OKF loops (`experiment`, `lit-review`, the
  manuscript loop) described above; `rv project relate`/`rv project edges` manage its
  *declared peer* graph (cross-project corroboration) — a separate axis from
  lifecycle.
- **Stand down** — `rv project remove <slug>` is `add`'s parity counterpart: local
  teardown of the registry entry + scaffolded folders. The safety model, briefly (an
  Alfred reaching for this should know the shape before invoking it):
  - **Local, not remote** — removes the local registration/scaffolding; the project's
    GitHub repo (if any) is **preserved** by default, never deleted as a side effect.
  - **Non-destructive by default** — a plain `rv project remove <slug>` does not purge
    anything irreversible; destructive extras are explicit opt-ins.
  - **The unpushed-work firewall** — refuses (or loudly warns) when there is unpushed
    local work under the project, so a teardown can't silently discard it.
  - **Explicit opt-in flags** for the destructive extras: `--purge-repo` (delete the
    scaffolded repo content), `--purge-agents` (remove the project's `.claude/agents/`
    materialization), `--archive-github` (archive rather than delete the GitHub repo),
    `--dry-run` (preview the teardown plan with no writes — the cheap screen before the
    real operation, charter §9).
  - **Emits `⟦VAULT-TEARDOWN⟧`** — a structured handoff so the **vault side** (the hub's
    own registry/state, outside this repo's scope) can complete its half of the teardown;
    `rv project remove` owns the *project*-side teardown, not the hub's bookkeeping.

Register and stand-down are lifecycle **primitives**, not loop nodes — no DAG manifest
ever calls them, and they are never collapsed by the verb-consolidation program (D1..D5)
the way `sweep`/`coverage`/`expand`/etc. are. They are the KEEP-bucket peers of `research
find`/`research add`/`note check` (verb-consolidation §4.3): atomic ops Alfred or a human
calls directly, outside any loop's frontier.
