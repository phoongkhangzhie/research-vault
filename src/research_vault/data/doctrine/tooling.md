# Tooling doctrine

The one doctrine for **how we build, manage, and test tools and skills** across all projects.
Sibling of [memory management](./memory-management.md).
Owned by the Principal Architect; every tool and skill task points here as its standard.

The spine is the reuse-over-create principle from the charter: **extend before minting, and prefer
an `rv` subcommand over a new script.** Where an external idea (skill-creator, git-workflow)
conflicts with our global-first / CI-terminal model, our model wins — noted inline.

---

## The three pillars

### 1. Create (recurrence-gated, global-first)

A tool earns its place by **recurrence** — if you did something three times by hand, that is the
signal to promote it. One-offs stay scripts; recurring patterns become `rv` subcommands.

**Global-first** means the default home is `rv <verb>` on PATH, registry-driven via
`projects.json`. A project-specific tool is the exception, not the rule.

**Reuse over create.** Before minting a new subcommand or skill, extend an existing one.
Proliferation is maintenance debt.

**For skills specifically**, apply the four-question intent gate before authoring:

1. What does this skill enable that the model can't do unaided?
2. When (and when NOT) should it trigger?
3. What is its output format?
4. Does its deterministic sub-steps warrant being an `rv` subcommand instead?

If the answers to 1–3 are thin and 4 is "yes," you wanted a tool, not a skill.

**Progressive disclosure** in skill authoring: metadata → ≤500-line body →
on-demand scripts/references/assets. A skill that's 2000 lines of static prose is a tool that
escaped its container.

### 2. Manage (one front door, discoverable, Architect-owned)

One front door: **`rv <verb>`**. Discoverable: **`rv help`** prints every subcommand;
**`docs/cli-reference.md`** is the generated authoritative reference (see the anti-drift section
below). The Architect owns the CLI surface; a new subcommand is an architecture decision.

**For skills**: the description field IS the trigger. It must be ≤1024 characters, start with the
use case ("Use when…" or "Triggers on…"), and contain no angle-bracket template literals. The
name must be kebab-case and ≤64 characters.

### 3. Test and assert (the richest pillar)

#### Tools

- **Hermetic unit suite** — no network, no secrets, no GPU in the default test run.
- **Golden / byte-identical** for artifact producers — any tool that generates a file must have a
  golden fixture that catches output drift.
- **Mock + live smoke** for external-touching tools — hermetic mocks for CI, a smoke test that
  runs against the live service on demand.
- **Self-healing tools gate themselves.** A tool that has a `--check` mode (e.g. `rv help --check`)
  runs that check in CI. The check is the gate.
- **CI-wired.** Every tool test lands in the GH Actions hermetic suite. A tool without a CI gate
  is untested by definition.

#### Skills (three-tier validation)

Adapted from the skill-creator lens to the CI-terminal model:

1. **Structural lint** — validate frontmatter (name kebab/≤64, description present/≤1024/no
   angle-brackets, allowed keys only). Wire as a pre-push gate via `rv check`. This is the
   highest-ROI tier: cheapest to run, catches the most common authoring errors.
2. **Behavioral eval vs baseline** — does the skill beat NOT having it? Measure pass-rate delta
   on a held-out prompt set. Defer the heavy eval harness until enough skills exist to make it
   worthwhile; the rule is the discipline, not the scaffolding.
3. **Trigger eval** — should-trigger / should-NOT-trigger near-miss set (held-out split).
   The concept is adopted; the heavy `claude -p` loop is skipped (against the CI-terminal model).
   A manual trigger review on authoring is sufficient until many skills exist.

**Universal principle (from skill-creator's grader self-critique):** *An assertion that passes
for a wrong output is a defect.* Hunt non-discriminating tests. Every check must be able to catch
a violation — if it can't, it is not a check.

#### Capstone

A meta-runner command that runs all tool/skill suites in one pass — the toolchain asserting
itself. Not yet built — the target state.

---

## Identity and separation of duties

In the OSS package, use `gh` and `git` directly with your own credentials. The separation-of-
duties principle still applies: **the reviewer must not be the PR's author.** Enforce this
manually: before posting an approval, run `gh pr view <pr> --json author` and confirm the
author is not you.

The principle behind the design (for future tier-3 adapter tooling): the enforcement hole is
that a single shell session can silently use the owner keyring even when a different role token
is intended. A role-specific identity system (planned for a later increment) closes this by:

1. Persisting the active role across tool-call boundaries (not just the current shell invocation).
2. Wrapping `gh` to inject the role-specific `GH_TOKEN` at every call, fail-closed.
3. Adding a structural self-vs-author guard on PR-write subcommands.

Until that adapter ships, use plain `gh` with a role-specific token and enforce the grounding
gate manually — see [engineer.md](./roles/engineer.md) and [reviewer.md](./roles/reviewer.md) for the
per-role session sequence.

---

## The skill-vs-tool boundary (decided)

| Characteristic | Use a tool (`rv` subcommand) | Use a skill (SKILL.md) |
|---|---|---|
| Output | Deterministic, verifiable | Requires judgment or sequencing |
| Test approach | Hermetic + golden | Behavioral eval |
| Sub-steps | All deterministic | Calls tools for deterministic sub-steps |

A mature skill is **a thin judgment layer over tools**. A "skill" that is just fixed steps is an
`rv` subcommand that escaped its container (the reuse-over-create signal). Conversely, an
`rv` subcommand that requires the model to make non-deterministic choices is a tool trying to
be a skill.

---

## The anti-drift pattern: generate-don't-list

The canonical example is the **CLI reference** (`docs/cli-reference.md`):

- **What it solves.** A hand-maintained list of `rv` commands drifts as soon as someone adds a
  case arm without updating the list. The reference and the script become inconsistent silently.
- **The pattern.** A generator script parses the case arms in the CLI entry point and emits
  `docs/cli-reference.md`. The reference is *derived*, not maintained. `rv help --check` runs
  the generator in diff mode and fails if the output would differ from the committed file.
- **The teeth.** The generator enforces three rules at commit/CI time:
  1. Every case arm must have a preceding `#` doc-comment. An undocumented arm is a build
     failure. You cannot add an `rv` subcommand without documenting it.
  2. The committed `docs/cli-reference.md` must match what the generator would produce. Staleness
     is a build failure.
  3. The hand-written help echo (`rv help`) must mention every case arm verb. Echo drift is a
     build failure in `--check` mode.
- **Apply this pattern broadly.** Any time you are tempted to hand-maintain a list that is
  derivable from code (command lists, skill indexes, token manifests), generate it instead and
  gate the generated output in CI.

### Reach for the verb, never hand-roll

**The principle.** Every `rv` subcommand was promoted because a manual sequence recurred (the
recurrence gate above). Once promoted, the verb IS the standard — not an option. When the model
reaches for a bare shell sequence (`gh pr view …`, `git worktree add …`, hand-editing a link) and
an `rv` verb covers it, **stop and use the verb**. The manual path becomes an anti-pattern.

This principle is self-referential: a new tool whose model-habit doesn't actually switch from
hand-rolling to reaching for the verb has not achieved its goal, no matter how correct its
implementation. **The habit switch IS the deliverable.** This is why every CLI tool ships with
a discovery/habit layer (see below and the [Adopt section](#adopt--skip-from-skill-creator)).

**The rv-cli discovery skill (unbuilt — the target state).** The rv-cli discovery skill is the
single place that fires whenever a model is about to hand-roll something an `rv` verb can do. Its
entries are structured to trigger on **task intent**, not just discovery intent — the critical
distinction: a trigger that fires only on "which command does X" is useless; it must fire when
the model is already reaching for `gh pr view …` directly.

Each entry in the rv-cli skill carries:
- **When to reach for it** — the task-intent trigger (fires on the manual pattern, not the meta-question).
- **`replaces:`** — the exact manual command or sequence it supersedes. Naming the old way is the
  habit-switch mechanism; without it, the anti-pattern persists alongside the verb.
- **Common misses** — cases where models slip back to the manual pattern, so those slips surface
  immediately.

Building the rv-cli skill is a registry-first decision: add the `replaces:` entry before writing
the implementation, as the test that the verb is actually needed. An undocumented replacement is a
build warning (the same doc-comment gate as `rv help --check`).

**Apply the skill-creator lens to this principle itself.** What makes this principle get *used*? The
habit switch happens when: (a) every new `rv` case arm ships its `replaces:` entry at the same
time as the arm; (b) the rv-cli skill's trigger fires on the old manual command pattern, not just
when someone thinks to ask about rv; and (c) a reviewer gates any skill/tooling PR on the
discovery surface being present. The principle becomes habit through tooling enforcement, not repeated
instruction.

---

## Adopt / skip from skill-creator

We took the skill-creator skill and **adapted it** — keeping the design lens, dropping the execution
ceremony — exactly as the memory-tooling decision did with claude-mem (kept the retrieval discipline,
dropped the passive worker). The distilled lens is light **so it actually gets applied every time**;
the heavy comparative-eval harness would not survive contact with a coordinator-class, headless
workflow. We drop the comparative-eval *ceremony*, **not testing** — the engineer still writes
concrete tests; the structural-lint pass remains required.

**KEEP (the design lens — apply every time):**

For a **skill** (judgment + sequencing, a model-driven `SKILL.md`):
- A real `SKILL.md` — kebab name, imperative body.
- **Pushy triggering description** — skills under-trigger, so the description fires on the task
  intent including phrasings that never name the skill ("use this whenever the user mentions X,
  even if they don't ask for it").
- **Progressive disclosure** — the 3-level loading system: metadata → <500-line body → on-demand
  scripts / references / assets.
- **Bundled scripts** for deterministic / repetitive sub-steps. A mature skill is a thin judgment
  layer over tools — don't make every invocation re-derive them.
- **Explain-the-WHY writing** — tell the model *why* a step matters; avoid heavy-handed all-caps
  MUST/NEVER except for genuine grounding rules. A wall of MUSTs is a yellow flag.
- `quick_validate`-style **structural linter** (highest ROI; cheapest gate) — frontmatter valid,
  name kebab/≤64, description present/≤1024/no angle-brackets.
- **Non-discriminating-assertion discipline** — every check must catch a violation; a test that
  passes for wrong output is a defect.
- **Triggering-is-testable** — trigger evals on a should-trigger / should-NOT-trigger near-miss
  split (concept adopted; the heavy `claude -p` loop is skipped — see DROP below).

For a **CLI tool** (`rv` subcommand — deterministic + verifiable):
- **Discovery / habit layer (the load-bearing piece):** a *when-to-use* entry in the `rv-cli`
  discovery skill whose trigger fires on the **task intent**, not just discovery intent. Plus a
  **`replaces:`** / common-misses entry that names the manual way it supersedes.
- **Doc-comment / generate-don't-list anti-drift contract** — every case arm carries a `#`
  doc-comment so it auto-rides the generated reference; `rv help --check` is the gate; an
  undocumented arm is a build failure.
- **Reuse-over-create** — fold into the existing discovery surface (one verb, one row).

**SKIP** (distribution-oriented, against the global-first / CI-terminal model):
- `.skill` packaging format
- HTML eval-viewer / GUI review loop
- Heavy benchmark JSON schemas / quantitative grading
- The blind comparator
- `claude -p` description-optimization loop
- Passive skill auto-discovery from transcript analysis
- With-skill-vs-baseline subagent test pairs (use engineer-direct script-over-eyeball tests)

---

## Steps (remaining)

- [ ] Build the structural linter for skills (`quick_validate`-style) + wire as an `rv check`
  subcommand and pre-push gate.
- [ ] Wire `rv help --check` into CI (the echo-drift + staleness gate).
- [ ] Build the meta-runner (selfcheck) — runs all tool/skill suites in one pass.
- [ ] Codify the non-discriminating-assertion rule as a linting step in the test suite template.
- [ ] Build the `rv-cli` discovery skill with `replaces:` entries for all existing subcommands.

---

## Candidate rules — promote on second instance

The following observations appeared once and have not recurred. Recorded here as candidates;
if the same pattern bites a second time, promote to a numbered rule in the appropriate section above.

- **Map-in-the-same-change** (SR-PKG): a data-relocation or structural SR that changes where files
  live must touch `architecture.md` in the same PR, so the map cannot merge stale. Caught downstream
  when SR-PKG merged without updating the diagram. Acceptance criterion template: "architecture.md
  updated to reflect new paths."

- **Third-framing before escalating a tradeoff** (SR-PLAN-FREEZE hash): when two stated requirements
  appear mutually exclusive (e.g. "tamper-detect any change" vs "all-default hash unchanged"), spend
  one design cycle checking for a third framing that satisfies both before escalating the tradeoff to
  the operator. In the freeze-hash case, hashing present-entries-only rather than fixed slots resolved
  the apparent conflict without any operator decision.

- **Find-the-load-bearing-invariant** (SR-GAP-CLOSE): before designing a lifecycle or feature over an
  existing store, identify the ONE merged invariant the whole design depends on. Finding it first
  collapses multiple downstream design questions simultaneously. The idempotent-preserve guard in
  SR-GAP-CLOSE resolved three separate design questions at once once it was named.

- **Reconcile diagram-vs-prose before implementation** (SR-GAP-CLOSE): when a design carries BOTH a
  state diagram and prose transition rules, reconcile them explicitly so an implementation cannot be
  faithful to one while diverging from the other. The Signal-2 scope ambiguity arose from
  diagram/prose inconsistency that was only caught during implementation, not at design time.
