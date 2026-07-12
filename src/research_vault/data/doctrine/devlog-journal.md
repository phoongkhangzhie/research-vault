# DEVLOG journal convention

The **`DEVLOG.md`** at a project's root is the per-project grounded record of decisions
and progress — one file, three zones, each with a different lifecycle. Managed via `rv devlog` (see `rv devlog --help` for the full subcommand list); never
grep/cat it by hand — use `rv devlog <project> index` / `search` for the structured
read face.

## Why three zones, not one flat log

A flat, single-shape DEVLOG conflates three record types that behave differently:

- **State you resume from** changes every session and should be overwritten, not
  accumulated (an ever-growing "what's next" list is stale within a week).
- **A decision** is a point-in-time commitment with real alternatives that were
  rejected — worth revisiting individually, and worth knowing *why* something was
  NOT done, not just what was.
- **A terse daybook** of what happened is genuinely append-only and cheap to skim
  chronologically.

Mixing the three means the decision record gets buried under daily noise, and the
resume-state gets stale because nobody wants to touch a growing append-only file.
Splitting them gives each the write discipline it actually needs.

## The three zones

### 1. `## Now` — mutable, overwritten each session

The resume-point: current state in 1-3 lines, open threads/next steps, and pointers to
the decision ids currently in force. This is the ONLY zone that gets overwritten rather
than appended to — `rv devlog <project> append Now "<text>"` replaces its body wholesale.
If it's stale, the next session's write fixes it; there is nothing to preserve.

### 2. `## Decisions` — append-only, immutable, ADR-lite

Each decision is an individually addressable record, newest on top:

```
### D-003 · 2026-07-12 · in-force
**Context:** why this came up
**Decision:** what was chosen
**Rejected:** what alternative(s) were considered and why they lost
**Consequences:** what this commits us to
**Touches:** [note title](/path/to/note.md)
```

Decision records are **never edited** once written. A decision that gets reversed earns
a **new** `D-NNN`; the old record's status flips to `superseded-by D-NNN` (never deleted,
never rewritten in place) — the history of *why* stays intact. `rv devlog <project>
append Decisions "<text>"` auto-assigns the next `D-NNN` and prepends the record.

**`Rejected` is never empty.** A decision with no stated alternative is either not a real
decision (nothing to record) or the alternatives were never considered (say so honestly —
`_(no alternative considered)_` is a valid, honest value; a blank field is not).

**`Touches`** points at the OKF note(s) this decision bears on, using the same
bundle-relative markdown link convention as note-to-note cross-links (see
[`note-conventions.md`](./note-conventions.md)) — never a raw filesystem path.

### 3. `## Log` — append-only terse daybook

One dated entry per working day, newest on top, a bare `### Done` bullet list:

```
### 2026-07-12

#### Done
- shipped the 3-zone DEVLOG structure
```

Kept terse on purpose — this is a chronological trace, not a place to re-litigate.
`rv devlog <project> append Done "<text>"` creates today's entry if missing and appends
a bullet.

## Cadence and signal discipline

Event-triggered, not scheduled: a Log entry per working session; a Decision record at
the moment the call is actually made — not batched at end of day, and not backfilled
from memory. Capture only what is needed to **resume**, or what would be
**re-litigated or re-discovered if lost** — dead ends and negative results included,
since those are exactly what gets silently re-tried without a record. Refuse to record
anything git already records (a commit hash is not a Log bullet).

`rv devlog <project> check` treats a DEVLOG entirely **missing** as a hard failure.
Everything else — staleness, a thin Log — is a **signal**, not a gate: cadence
compliance is not something to game.

## The journal ↔ OKF-note boundary

The DEVLOG is the **fleeting / process** layer. A project's OKF notes (findings,
methodology, gaps, concepts — see [`note-conventions.md`](./note-conventions.md)) are
the **evergreen / knowledge** layer. Content **promotes** from journal to note when it
hardens into something durable:

- A `Decisions` record that settles a real methodological question → a methodology note.
- A `Log` entry reporting a result → a findings note.
- A recurring unknown that keeps resurfacing in `Now`/`Log` → a gap note.

**Cross-links are asymmetric, by design.** A journal entry may link OUT to an OKF note
(the `Touches:` field, or an inline link in a Log bullet) — that's how a decision or a
day's work grounds itself in the evergreen layer. An OKF note must **never** link back
to a dated journal entry: the note is timeless, meant to be read years later without a
"you had to be there" reference to a specific day. If a note needs to justify itself,
it links to the *promoted* content (another note), not to the DEVLOG entry that
originated it.

## Structural lints (`rv devlog <project> check`)

Cheap, rejects-only structure checks, run alongside the freshness signal:

- `## Now` is present.
- Every `superseded-by D-NNN` resolves to a real, recorded decision id.
- No `Decisions` record has an empty `Rejected` field.

A structural violation or an entirely missing DEVLOG both hard-fail the check.
Staleness (no recent Log entry) is reported as a warning only — visible, never blocking.
