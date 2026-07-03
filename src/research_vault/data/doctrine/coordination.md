# Coordination

How the hub talks to managers — one front door, an async file bus, hot/cold sessions.

## The pieces

- **The hub** — the operator's single front door. Always on, but **thin**: it holds only
  small project *cards* (see the registry), never a full prompt. It reads the control files
  to brief and route work.
- **Control files — the bus.** One `CONTROL.md` per project: a markdown handshake
  surface. The hub posts requests; the manager posts results. **Async** (nobody has to
  be live at once), **durable** (survives any session dying), **legible** (you can read
  the whole exchange), and near-free.
- **Managers — hot or cold.** The project you're actively in runs a **hot** session
  (full context loaded once, holds working state). Dormant projects have **no open
  session** — their state lives in the control file + devlog, woken on demand. Idle costs
  nothing.
- **The review board** posts review requests and verified findings onto the bus (see
  [review-board.md](./review-board.md)).

## The control file

**One control file per project — centralized in the hub repo.**
One file *per project*, but all of them gathered in the hub repo, not scattered across each
project's own repo. For a single operator that's the right call:

- **No real contention.** There are never two concurrent human editors, so per-project-repo
  arguments for avoiding races are moot — and one location means the hub reads them all without
  reaching into N repos.
- **One place, versioned together.** Every project's control file lives and is versioned
  alongside the doctrine and the task board it coordinates with.
- **Rests on its own.** A dormant project's control file is its state-at-rest until woken.

Fixed sections so the hub and manager always know where to look:

```md
# Control — <project>

## Inbox  (hub → manager: do these)
- [ ] 2026-06-24 review: the compose-stage fabrication guard  ·#R12
- [ ] 2026-06-24 decision needed: ship Sonnet on apply flows?

## Handshakes  (needs a yes/no before proceeding)
- [ ] <project>: OK to add `finding` type to store?  (awaiting operator)

## Outbox  (manager → hub: done / blocked / found)
- 2026-06-24 done: regenerated panels A–D with provenance  ·#R09
- 2026-06-24 blocked: needs the WVS license file to proceed

## Open findings  (from the review board; resolve or dismiss-with-reason)
- [ ] #R12 strong: compose fabricates metrics for thin personas — unverified guard
```

The hub reads every project's `Inbox`/`Outbox`/`Open findings` to build its brief.

## Recursion: the same bus, one level down

The control-file pattern is **self-similar**. The hub coordinates managers over
`control/<project>.md`; **each manager coordinates its own subagents the same way**, over a
project-local team bus at `<repo>/.agents/CONTROL.md` (same sections: Inbox · Handshakes ·
Outbox · Open findings). A subagent posts its `⟦RETURN⟧` and any flags to *its manager's* bus —
making the manager's coordination state durable, just as the manager's outbox makes the hub's
view durable.

This fixes the **communication chain**: everything flows to your coordinator, never laterally.
A reviewer with a stack-coherence concern flags **its manager** (not the Architect directly);
the manager decides to act or escalate **up** to the Architect over the hub bus; a genuine
conflict goes to the operator. So:

```text
reviewer/engineer ──▶ manager ──▶ Architect ──▶ operator
   (team bus)      (hub bus handshake)   (completed staff work)
```

Each arrow is a node talking to the one above it. No agent reaches across to a peer authority —
that would be the back-channel the whole bus exists to prevent.

## Merge authority — split by class

The **manager classifies** each PR — `auto-merge` / `review-then-merge` / `human-go`, with a
grounded `basis` (reversibility · harness-coverage · severity · precedent). The **engineer
executes** the merge **on the authorizing gate** (CI-green, ± a reviewer verdict, ± the
operator's go). **Coordinators never run a merge** (no shell — the boundary is structural).

`human-go` (the protected classes: headline results, cross-project conventions, outward-facing /
deploy, operator's explicit gates) escalates as an **evidence packet** — harness + independent
stress-test + "what could break & why it doesn't" + honest caveats. No rubber stamps.

**The control plane is the enforcer.** These flows are doctrine that agents must *follow* —
and agents drift. The coordination control plane (the validator + conformance audit) replays
the record and asserts the rules *held*: that a merge matched its class, that flags routed
**up** not laterally, that no task closed without a `⟦RETURN⟧`, and that every irreversible
carried structured provenance. Doctrine you can *test* is the difference between an operating
system and a constitution.

## Routing

**Routing is computed, not recalled.** The manager fills its spawn-request `hat` from the
build-agents roster (the subagent name in `.claude/agents/<role>.md` IS the canonical hat),
and the hub *executes* the named hat without re-deriving. The roster is the single source of
truth, so the computed hat can't drift — no hand-recall, no stale registry.

## Verify, don't relay

A specific does not become fact by being passed along. Two rules, one principle (grounding):

- **Verify tool/CI claims against the source of truth.** "CI green" / "tests pass" from the builder is a
  *claim*, not a state — check it against the authority (the CI provider's runs API, the actual file)
  before it enters a durable record. A relayed "CI green" that was actually red, once written to the bus,
  misleads every later reader until someone re-verifies.
- **Trace every relayed specific to source.** A count, a field list, a constant passed through a chain is
  unverified until traced to the code or record it came from. Numbers drift in relay; the artifact is the
  authority.

## The cheap trigger

A manager **reads its control file at the top of each turn** — a one-line discipline, so
handshakes get picked up without a watcher. Want true auto-wake (a dormant project pings
when something lands)? Add a file-change hook — optional machinery to add only if manual
nudging annoys you.

## Why it's cheap

Dormant projects cost nothing (no session). The active project is one warm session,
loaded once. Coordination is tiny file I/O. The hub stays thin. That beats both
*reloading a full prompt on every switch* and *keeping every manager hot*.

## Dispatch: fresh + pointed by default; resume is the justified exception

**Default: spawn a FRESH agent pointed at a durable spec.** Every DAG `agent` node carries
a `spec` field — a non-empty pointer to the brief the agent is dispatched against
(a task-file section, a control-file slug, or a path). Absence is a `ManifestError` by
construction (the schema enforces it). The `rv dag` frontier line prints `FRESH — spec:<ptr>`
so the adopter's runtime knows to spawn new.

**Why fresh by default:** a resumed background agent reloads its entire accumulated transcript
on every invocation — per-call cost grows monotonically. A fresh agent pays only for the
pointed spec and re-derives from current ground truth (can't drift on stale in-context
assumptions).

**Resume is the justified exception.** A `continues` field overrides to resume mode:
```json
"continues": { "node": "<prior-agent-node-id>", "reason": "<non-empty justification>" }
```
The schema validates: `continues.node` must exist, be `type: agent`, be a
transitive-upstream ancestor, and not be self. `continues.reason` is **required** — the
tool forces articulation of the justification on the record. The frontier prints
`CONTINUES <node> — <reason> — spec:<ptr>`.

Valid use: tight iterative continuation with no intervening durable artifact — a one-step
refinement where transcript context is genuinely useful.

**Named anti-patterns (tooled as structural WARNs, not hard errors):**

- *No pointed spec:* an agent node without `spec` is a `ManifestError` — you cannot
  dispatch ungrounded.
- *Resume across a durable-artifact boundary:* a `continues` whose DAG path from the
  continued ancestor crosses a `produces:` or `human-go` node. The validator warns
  (`⚠ … resumes across a durable-artifact/decision boundary — prefer a fresh dispatch
  pointed at the artifact`). Non-fatal: the schema accepts it, but the structural smell
  is surfaced at `dag run`/`tick`/`status`.

**What stays doctrine (not tooled):** whether a resume is *tight enough* to justify
`continues` vs a fresh dispatch is irreducible judgment. The tool enforces grounding
(`spec`) and reference integrity (`continues.node` must resolve), and forces the
justification (`reason`); it does not adjudicate "tight enough." That residue lives here.

## Bound the reading-scope: the `reads:` grounding manifest

**The problem `spec:` alone does not solve.** A fresh dispatch with a pointed `spec:`
says *what to do* — but a fresh agent still re-grounds by broad exploration: it reads
whatever looks relevant. That re-grounding is fresh dispatch's one real cost. An unbounded
reading-scope re-inflates the very token cost fresh dispatch was meant to kill.

**`reads:` = WHAT TO LOOK AT.** Each DAG `agent` node may carry a `reads:` field — a
bounded list of grounding pointers the agent is expected to read:
```json
"reads": [
  "src/research_vault/dag/schema.py",
  "tasks/design.md#5B-SCOPE",
  {"ref": "control/research-vault.md#sr-scope", "why": "prior verdict"}
]
```
`spec:` crystallizes WHAT-to-do; `reads:` crystallizes WHAT-to-look-at. They are distinct
fields, distinct concerns, on the same node.

**`reads:` is OPTIONAL — but absent emits a WARN.** `spec:` (required) already guarantees
≥1 grounding pointer. `reads:` is the ADDITIONAL bounded evidence set for nodes that need
supporting artifacts beyond the spec. Forcing it on every trivial node breeds filler.
Absent `reads:` emits a non-fatal `⚠ … dispatched with an unbounded reading-scope` warn
at `dag run`/`tick`/`status` — the structural-smell WARN idiom from SR-DISP applied here.

**The relationship to spawn-request `inputs:`.** The spawn-request control-bus field
`inputs:` (one of the 11 `SPAWN_REQUIRED` fields) is the *prose* reading-scope, authored
by a coordinator before artifacts exist. When that spawn becomes a DAG `agent` node, its
`inputs:` becomes the node's machine-checked `reads:` — the same concept at two layers,
teeth applied once at the machine layer. The prose `inputs:` field is unchanged; it is the
semantic ancestor of the structured `reads:` field.

**Teeth (what is tooled vs what is doctrine):**

| Concern | Layer |
|---|---|
| `reads:` well-formed (list · non-empty-if-present · str-or-`{ref,why}` items) | **TOOLED** (ManifestError, pure validate) |
| Every pointer RESOLVES (file/anchor/bus exists) | **TOOLED** (hard, at `dag run`/`tick`) |
| `reads:` surfaced on the `DISPATCH` line for the runtime | **TOOLED** (frontier print suffix) |
| Is the scope **SUFFICIENT** (agent won't need more)? | **DOCTRINE** (irreducible spec-author judgment) |
| Is the scope **MINIMAL** (no over-listing)? | **DOCTRINE** (same irreducible judgment) |
| Did the agent actually read outside scope? | **RUNTIME** (not RV — no observation seam) |

**The scope-sufficiency loop.** RV has no observation seam into what the agent read —
it cannot diff actual-reads vs declared. The loop closes through the artifact RV already
owns: a returning agent that had to read far beyond its `reads:` surfaces it in its
`⟦RETURN⟧` (`confidence`/`retro` — "reads-scope was insufficient; had to consult X").
The spec-author reads that to fix the scope next round. No new hard `⟦RETURN⟧` field —
this is a doctrine convention, not a schema change.

**The `DISPATCH` line** carries the bounded scope so the adopter's runtime hands the agent
its targeted reading list:
```
→ DISPATCH  [lit-search] Literature search
    FRESH — spec:task://research#lit-search — reads: src/schema.py, tasks/design.md#5B-SCOPE
```
When `reads:` is absent the suffix is omitted. A runtime that logs tool-calls could diff
actual-reads vs declared and emit an "out-of-scope read" signal — that is a runtime feature,
not an RV one. RV only *enables* it by surfacing `reads:` on the frontier.

## CONTRACT — the project lens (milestone-manual re-bake)

Each project carries a `CONTRACT.md` in `.agents/<slug>/` — the project lens composed into
every agent hat (`charter + role + CONTRACT`). It holds **slow-moving strategic content only**:
Identity, golden rules, pointers, roadmap, roster.  Operational state stays off it (read fresh
from the board).

`rv project new` scaffolds a placeholdered skeleton.  The architect fills it on first real work.

**On a milestone** (a phase boundary, a major deliverable, a significant scope change): the
architect updates the affected project's CONTRACT (roadmap block especially), then runs
`rv build-agents --project <slug>` to re-bake the hats.  No automated staleness check — mtime
is an unreliable oracle (the mtime-freshness trap); content drift is a human judgment call on
milestone boundaries.

A missing or unfilled-stub CONTRACT is surfaced as a WARN by `rv build-agents` (loud, to
stderr, with a banner embedded in every hat) and by `rv check` (Project integrity section).
These are nudges — `rv check` exit code is unchanged (exit 1 is reserved for missing Claude CLI
/ API key).  The teeth are visibility, not a gate.
