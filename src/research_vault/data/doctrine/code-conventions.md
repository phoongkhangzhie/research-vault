# Research code conventions — the within-code craft

Sibling to [`doctrine/project-structure.md`](./project-structure.md): that page owns the
**folder structure** (where notes, code, data, results, figures, and manuscripts sit and
how a note links to an artifact); this page owns the **craft inside `code/`** — how the
code itself is written, tested, and released so that a claim built on top of it is
trustworthy. Read both — a well-laid-out repo full of untested, unreproducible scripts is
still not sound research code.

Each item below is tagged **[enforced → CHECK-n]** (a real gate — `rv note check`, the DAG
complete-gate, or a repo-level check — will fail or warn on a violation) or **[guidance]**
(a judgment call the gate cannot mechanically verify; craft, not a check). Where an item is
guidance but still worth declaring for review, it also appears in the **Layer-B checklist**
(§9) — a human-answered form, not a pretense of automation. **Note on rollout:** the
note-plane checks (CHECK-1/2/3a/4a) ride the existing `rv note check` machinery today; the
repo-plane checks (CHECK-3b/5/6a/7/8a-c) are planned to ship via a dedicated repo-level
verb — not yet implemented as of this page's authoring — tracked in §10.

## 1. The thesis: research code's output is a claim, not a feature

A bug in a web app yields a visibly broken page; a bug in an estimator yields a *plausible
wrong number* that ships in a paper (Kapoor & Narayanan document leakage across 17 fields
and 294 papers). So the bar for research code is not "does it run" but **"is every number
reproducible and traceable to tested code and versioned data."** rv already owns the
provenance substrate — `results_commit`, the `repro_*` fields, `scores[].hash` — this page
owns the code-craft that sits on top of that substrate, plus the narrow slice of it that a
gate can actually check.

## 2. `src/` is a tested library; experiments are thin orchestration

- Scientific logic — estimators, metrics, transforms, splits — lives in **importable
  modules** under `code/src/`, where a test can import it directly. **[guidance]** — that
  logic belongs in `src/` rather than inline in a script is a judgment call a static gate
  cannot prove; it's craft, not a checkable fact about a tree.
- Experiments are **thin, config-driven** scripts: they wire library calls to data and emit
  a `results/scores/` artifact. A run is defined by a config *file* that is itself a hashed
  artifact (`repro_config_location` + `repro_config_hash`). **[enforced → CHECK-2]**
  (config-artifact hash integrity — folded into the CHECK-1 chain, below).
- `code/src/` stays refactorable: notes never reference a path under `code/` — see
  project-structure.md's linkage principle P1. **[guidance]**

## 3. The claim-is-the-output framing

- Every number in `results/scores/` or a manuscript traces through the **full provenance
  chain**: result → producing code's git SHA → config → data hash. **[enforced →
  CHECK-1, the flagship check]** — see §5.
- No manual post-processing between a computed result and the paper. Manuscript numbers are
  **injected** from the hashed `scores:` artifact, never hand-typed (Sandve's R1/R4).
  **[enforced → CHECK-6b]** — rides the manuscript loop's macro/hash-drift gate (ships with
  the manuscript loop, not this bundle).

## 4. Notebooks are for exploration only

- A notebook may **never** be the sole source of a number that ends up in `results/scores/`
  or the manuscript. This is the one **narrow, enforced invariant**: **[enforced →
  CHECK-3a]** — no `scores:` entry's `location` may end in `.ipynb`.
- No `.ipynb` files live under the `code/src/` import path. **[enforced → CHECK-3b]**
- Restart-and-run-all before commit; pair an exploratory notebook with the module it
  informed; keep notebooks off the pipeline entirely. **[guidance]** — hygiene a static
  gate cannot verify (nothing stops a stale notebook from being re-run out of order).

## 5. Reproducibility and determinism

- **Environment pinned, not merely captured.** A lockfile-grade artifact exists at repo root
  (`uv.lock`, `requirements.lock`, or a version-pinned `environment.yml`) and
  `repro_env_python` names a concrete version, not a range. **[enforced → CHECK-5, soft/WARN]**
  — pinning maturity legitimately varies by project weight (a containerized pipeline pins
  differently than a bare script), so this degrades to a warning rather than a hard block.
- **Seed recorded** whenever a result is claimed. `repro_seed` must be non-sentinel.
  **[enforced → CHECK-4a]** — promoted into the hard CHECK-1 chain: a seedless claimed
  result is not reproducible. (The other 21 `repro_*` completeness fields stay at the
  existing WARN-level sentinel lint — seed is chain-critical, the rest are nudges.)
- **Seed actually threaded** through every stochastic call, and framework determinism flags
  set. **[guidance]** — the gate can confirm the field is populated; it cannot confirm the
  seed is wired through every call site. Declare it in the Layer-B checklist.
- **Determinism / tolerance policy declared per experiment** via `repro_determinism: exact |
  tol:<eps> | stochastic`. **[enforced → CHECK-4b consumes it]** — the field is scaffolded
  to the strict default `exact`; a stochastic or GPU-dependent pipeline must explicitly
  relax it, so a golden-rerun test applies the right comparison instead of failing forever
  on legitimate nondeterminism. Choosing the *correct* value for a given pipeline is
  **[guidance]**.

## 6. Research-quality testing — the science-critical path, not coverage-chasing

Coverage-percentage is the wrong target for research code: the oracle problem (there's
often no independent ground truth to assert against) means chasing a coverage number
manufactures test theater rather than catching the bugs that actually matter. The right
target is the **science-critical path** — the code where a bug becomes a wrong finding, not
a wrong pixel.

- **Mark the science-critical path explicitly** with a `# science-critical` comment on the
  function or module (estimators, metrics, split/eval logic). Every marked symbol must have
  **at least one** corresponding test. **[enforced → CHECK-7, soft/WARN]** — targeted at the
  named load-bearing set, never a global coverage threshold; degrades to a `[science-path]`
  warning, never a hard block (misfiring hard here would manufacture the exact coverage
  theater this dimension exists to reject).
- **Metamorphic / property tests** for the oracle problem — e.g. permuting inputs leaves a
  mean unchanged; shuffling labels collapses a classifier to chance. **[guidance +
  Layer-B]** — the *right* relations for a given estimator are a domain judgment a gate
  cannot invent; declare which ones exist in the Layer-B checklist.
- **A golden / regression test** on a headline result: rerun a fixture, compare the
  recomputed score against the recorded `scores[].hash` under the declared
  `repro_determinism` policy. **[guidance + Layer-B]** — registering the rerun is a project
  act; `repro_determinism` (§5) is the machinery hook that keeps it from failing-forever
  on a nondeterministic pipeline.
- **A cheap end-to-end smoke test on a tiny data slice** — a rejects-only kill before an
  expensive real run (the cheapest-sufficient screen, before spending the real compute
  budget). **[guidance + Layer-B]** — declare the smoke entrypoint.
- **Tests are maintained, not decorative** — update, delete, or translate stale tests as
  the code changes, and CI actually executes them (verify CI claims against the Actions run,
  never take a relayed "green" on faith). **[guidance + CI]** — no silently-skipped
  science-critical tests.

## 7. Releasable from day one

- **No secrets, no hardcoded absolute/personal paths** (`/Users/…`, `/home/…`, known
  cluster mounts) anywhere under `code/`. **[enforced → CHECK-8a]** — extends the existing
  leakage scan's private-marker half with two new regex classes over `code/**`.
- **`CITATION.cff` present and minimally valid** — parses as YAML with the required keys
  (`cff-version`, `message`, `title`, `authors`) — makes the software citable in its own
  right. **[enforced → CHECK-8b, WARN early → HARD at the release gate]**. Keeping the
  author list and version current is **[guidance]**.
- **An OSI `LICENSE` file, matching a recognized SPDX identifier.** **[enforced →
  CHECK-8c, WARN early → HARD at the release gate]**. *Which* license to choose is
  **[guidance]** — the gate checks the file exists and is recognizable, never picks or
  second-guesses the choice.
- **A clone → results README walkthrough that actually runs**, plus a code-availability
  statement in the manuscript (ideally an archived DOI, not a mutable HEAD).
  **[guidance + Layer-B]** — whether the walkthrough truly runs end-to-end is an
  integration concern (the same "test the real thing" discipline as everywhere else in
  this codebase), not something a static gate can certify.

## 8. Anti-patterns to kill

Each of these maps to a principle above and, where checkable, to its `CHECK-n`:

- **Results buried under `code/src/`** → project-structure.md's linkage principle P1 (the
  frozen-roots convention); breaks every citing note on a refactor.
- **Duplicate-SSOT drift** — the same artifact tracked under both `results/` and `data/` →
  §7 / CHECK-6a (repo-plane, frozen-roots layout integrity).
- **The hand-edited number** — a manuscript figure typed in rather than injected from a
  hashed `scores:` entry → §3 / CHECK-6b.
- **Seed-recorded-not-threaded** — `repro_seed` populated in frontmatter but never actually
  passed to the stochastic calls it claims to control → §5 (guidance; the gate can only
  confirm the field, not the wiring).
- **Coverage theater** — chasing a global coverage percentage instead of testing the
  science-critical path → §6.
- **Split/eval leakage** — train/test contamination in the evaluation pipeline (Kapoor &
  Narayanan's central finding) → §6 (Layer-B declaration; no static gate can audit this).
- **De-privatize-later** — shipping a project with secrets or personal paths still in the
  history, planning to scrub before release → §7 / CHECK-8a. Scan from day one, not at the
  end.
- **Notebook-as-pipeline** — a notebook silently becomes the actual source of a claimed
  number → §4 / CHECK-3a, the one narrow invariant this page enforces hard.

## 9. Layer-B checklist — declare what a gate can't verify

The items above marked **[guidance]** or **[guidance + Layer-B]** are real requirements,
not decoration — they're just not mechanically checkable from a static tree. Surfaced at
review / manuscript time as a short project-answerable form. Answer honestly; "no" is a
legitimate answer that flags follow-up work, not a failure to hide.

> - **Metamorphic / property tests** — do the critical estimators have them? Which
>   relations do they check (e.g. permutation-invariance, chance-baseline collapse)?
> - **Chance / shuffled-label baseline** — is there one, and does the real pipeline beat it?
> - **Split / eval leakage audit** — has the train/test/eval split path been reviewed for
>   contamination (Kapoor & Narayanan)?
> - **README walkthrough** — does the clone → results quickstart actually run, end to end,
>   on a clean checkout?
> - **Smoke entrypoint** — is there a declared cheap end-to-end smoke test, and does CI
>   actually execute the full suite (not silently skip it)?
> - **Code-availability statement** — is it present in the manuscript, and does it point at
>   an archived artifact (a tagged release or DOI), not a mutable `HEAD`?

## 10. Where the checks live

The `CHECK-n` references above are not aspirational — each rides a real gate:

| Plane | Home | Checks | Status |
|---|---|---|---|
| **Note-plane** | `note.py::cmd_check` → `rv note check`, and the DAG complete-gate (a node can't be marked complete with an incomplete provenance chain) | CHECK-1 (flagship), CHECK-2, CHECK-3a, CHECK-4a | shipping incrementally against existing machinery |
| **Repo-plane** | a dedicated repo-level check verb (planned; not yet shipped) | CHECK-3b, CHECK-5, CHECK-6a, CHECK-7, CHECK-8a/b/c | planned |
| **CI** | the project's existing CI, run repo-wide | runs both of the above; the release subset (CHECK-8b/c hard, CHECK-6b) gates release | planned |

See [`doctrine/project-structure.md`](./project-structure.md) for the folder layout these
checks assume (`results/scores/` vs `results/runs/`, the frozen roots, the `scores:` link
convention) — this page is about the craft *inside* `code/`, that page is about where
things sit *around* it.
