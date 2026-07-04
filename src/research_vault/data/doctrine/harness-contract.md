# Experiment harness contract

Standing conventions for any experiment harness that runs LLM evaluation arms,
records responses, and supports resume.  Promoted from live-run bugs F26 and F27
in the dogfood evaluation cycle.

---

## 1. Mock / live isolation (F26)

**The defect this prevents:** a `--live` run that immediately "resumes" thousands of
mock-served responses and makes zero real API calls — producing a green-looking output
while the study is entirely invalidated.

### Rules

- **Separate output directories.**  Mock runs (`--mock` / `dryrun` mode) MUST write
  to a distinct path from live runs.  The convention is:
  - `<results_dir>/mock/` for mock output
  - `<results_dir>/live/` for live output

  Mixing them in a single flat dir is forbidden: a stale mock artifact will silently
  satisfy a live resume check.

- **Resume key must include `run_mode` + `served_model`.**  The key that identifies a
  cached/resumable record (typically a hash or composite key over the request) MUST
  incorporate `run_mode` (values: `"mock"` / `"live"`) and the identity of the model
  actually serving the response (`served_model` — e.g. the model alias used, or
  `"mock"` for dryrun).  A `--live` run MUST NOT reuse a record whose `run_mode`
  is `"mock"`, even if every other field of the key matches.

- **`--live` fails loud on mock-tagged records.**  If a `--live` run's resume scan
  encounters any record tagged `run_mode: "mock"` in the resume set, it MUST exit
  non-zero with a clear error message naming the offending record(s).  Silent reuse
  is the exact defect; loud rejection is the fix.

### Mandatory harness code-review checklist item

A harness PR MUST include a **mock-vs-live resume isolation test** before it can
merge.  The test must:

1. Run the harness in mock mode and confirm it writes to the mock output path.
2. Run the harness in `--live` mode (with a mocked API call counter to avoid real
   spend) and assert:
   - (a) the live run writes to a DISTINCT path from mock output;
   - (b) the live run makes real (mocked) calls rather than reusing any mock record;
   - (c) if a mock-tagged record is planted in the live resume set, the run aborts
     with a non-zero exit and a clear message.

**This checklist item is non-skippable.**  No harness merges without it.

---

## 2. Experiment-scoping (F27)

**The defect this prevents:** `run --exp <exp>` silently running ALL arms in the full
plan rather than only that experiment's arms — causing a multi-exp run to re-execute
the entire plan once per experiment (~2× overspend) while still appearing correct.

### Rules

- **`run --exp <exp>` calls only that experiment's arms.**  When a `--exp` flag is
  given, the harness MUST filter the arm list to only those arms belonging to the
  named experiment.  Passing the full unfiltered arm list to the scheduler is a bug.

- **Planned ≈ already-done → suspiciously-complete sanity check.**  Before dispatching
  any batch of arms, compare `len(planned_arms)` against `len(already_done_arms)`.
  If `already_done / planned >= 0.95` (i.e. near-complete before any new calls), the
  harness MUST print a loud warning and HALT:

  ```
  WARN: suspiciously complete — N/M arms already recorded before this run.
  Possible causes: wrong resume set, wrong --exp filter, or duplicate dispatch.
  Re-check and re-run with --force to override.
  ```

  This guard catches both the "wrong exp filter" bug and the "stale resume from a
  prior run" false-completion class of error.

---

## 3. Why these are standing doctrine (not per-project choices)

Both F26 and F27 produced real study-invalidating defects that were invisible until
post-hoc review:

- F26: the live run reported success, zero errors, correct record counts — but had
  made zero real API calls.  Only a per-record `run_mode` audit caught it.
- F27: the reported completion count was arithmetically correct for a 2× execution —
  it looked like two exps had run when it was one exp run twice.

Neither defect is caught by standard result-quality checks (score distributions, error
rates) — they require structural harness invariant checks.  That is why they are baked
here as non-negotiable, not left to per-project discretion.
