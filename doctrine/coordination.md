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

**Routing is computed, not recalled.** The manager fills its spawn-request `hat` from
`rv route <repo> <role>` (the repo *is* the project tag), and the hub *executes* the named hat
without re-deriving. `rv route` reads the build-agents roster as its single source of truth, so
the computed hat can't drift from the actual hat set — no hand-recall, no stale registry.

## The cheap trigger

A manager **reads its control file at the top of each turn** — a one-line discipline, so
handshakes get picked up without a watcher. Want true auto-wake (a dormant project pings
when something lands)? Add a file-change hook — optional machinery to add only if manual
nudging annoys you.

## Why it's cheap

Dormant projects cost nothing (no session). The active project is one warm session,
loaded once. Coordination is tiny file I/O. The hub stays thin. That beats both
*reloading a full prompt on every switch* and *keeping every manager hot*.
