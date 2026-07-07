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
├── manuscripts/                  # write-ups, paper outlines, submission artifacts
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
structural move that makes the linkage convention below hold.

## Results / runs / scores convention

### One home, two frozen subdirs

All computed outputs live under `results/`. The `runs/` vs `scores/` split is
convention-frozen:

| Subdir | Contents | Format | Git policy | Role |
|---|---|---|---|---|
| `results/runs/` | raw model outputs, logs, checkpoints | `*.jsonl`, `*.log`, `*.ckpt.json` | **gitignored** (large); integrity via hash + optional W&B/artifact push | the evidence trail |
| `results/scores/` | computed metrics / tables | `*.csv`, `*.json` | **TRACKED** (small; the citeable SSOT) | what findings cite |

### Naming rule

`results/{runs,scores}/<experiment-slug>[__<variant>].<ext>` — `<experiment-slug>` MUST
match the `experiments/<slug>.md` note stem, so note↔artifact correspondence is nominal,
not guessed (`experiments/hfs-landscape.md` ↔ `results/scores/hfs-landscape.csv`). Use
`__<variant>` (double underscore) for model/condition suffixes
(`hfs-landscape__haiku`), keeping the slug prefix greppable.

### Figures are a separate class

`figures/` is **not** under `results/`: a figure is a *designed* deliverable (designer /
house style, charter §3), not a raw computation. Each figure carries provenance in a
sidecar or caption — script · data/run-id · git SHA · date (note-conventions #5).
Tracked by default (few, small, the manuscript payload).

### Tracked vs ignored

- **Tracked:** `results/scores/**`, `figures/**`, all `notes/**`, `manuscripts/**`.
- **Gitignored:** `results/runs/**`, large `data/**` inputs — their integrity lives in
  **hashes** (experiment-note `results_hash`, dataset-note `hash`) + optional W&B/artifact
  push, not in git.

## The notes↔artifacts linkage convention

Four principles that keep the note graph stable:

**P1 — Reference only convention-frozen roots; never `code/`.** A note may reference an
artifact only under `results/`, `data/` (via a datasets note, see P3), `figures/`, or
`manuscripts/`. It MUST NOT reference a path under `code/`. `code/` is refactorable; the
frozen roots are not. Paths are repo-root-relative (`results/scores/hfs-landscape.csv`),
resolved from `source_dir`'s parent.

**P2 — The machine-checkable link is a hashed frontmatter field, not a prose path.** An
experiment note's primary link to its result is `results_location` (path to the computed
**score** artifact) + `results_hash` (sha256). `check_result_provenance` verifies existence
+ hash match, and `rv note <p> check` / the DAG complete-gate enforce it. Prose mentions in
the note body are human-facing secondary; the frontmatter pair is the source of truth.

**P3 — Data is linked through a `datasets/` provenance note, never a hand-copied path.** A
raw input in `data/` (or a remote DOI/URL) gets a `datasets/<slug>.md` note carrying
`location` + `hash`. Experiment notes link it via `repro_dataset_id: datasets/<slug>`.
`datasets` is a **shared** OKF type → the note lives in the rv instance's `datasets_root`,
shared across projects; `data/` in the repo is just the (optionally gitignored) bytes it
points at. Lineage is structural, so a `data/` reorg touches one provenance note, not N
findings.

**P4 — The computed score is the anchor; raw runs and W&B are supplementary.**
`results_location` points at the `results/scores/<exp>.csv` SSOT table (the thing findings
cite), **not** at the raw `results/runs/*.jsonl`. This is what lets **many runs collapse
into one experiment note**: the score CSV is the single hashed anchor; the individual runs
are linked as a supplementary list (`results_runs:`, a YAML indented list of
`<entity/project/run_id>` — the aggregate-experiment case, alongside the scalar
`results_wandb_run` for the single-run case).

### Formal path-stability rules

1. Note→artifact references resolve repo-root-relative and target only `results/ data/
   figures/ manuscripts/`.
2. `results/scores/` (computed) vs `results/runs/` (raw) is frozen; `<slug>` matches the
   note stem.
3. The frontmatter hash pair is the integrity contract; the path is only the locator. If
   an artifact must move, the hash still identifies it — the convention's job is to make
   moves rare by freezing the roots.
4. Inter-note links stay OKF bundle-relative (`[text](/findings/slug.md)`, not wikilinks);
   structural edits go through the link-safe note tool (note-conventions #7).
