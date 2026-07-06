# Memory management

The one doctrine for **how the system remembers** — agent memory, project devlogs, and the
`CLAUDE.md` / `.claude/rules` instruction layer. It consolidates the core principles with
lessons from evaluated external approaches, states what to **adopt vs reject**, and ties the
implementing task cards back here as their standard.

The spine: **memory is curated and grounded, never passively captured.** Where an external
idea conflicts with that, the core principle wins.

## The spine (these win conflicts)

1. **Curated + grounded, not passive.** We *decide* what's worth remembering, and every entry traces
   to a real source (a session, a devlog, recorded memory). Memory is the **system-of-record**, not a
   lossy auto-summary. This is the charter's grounding mandate applied to memory itself.
2. **OKF links everywhere; `[[ ]]` is abolished.** Cross-references are OKF markdown links —
   sibling-relative `[text](slug.md)` inside a flat memory dir, bundle-relative `[text](/section/slug.md)`
   inside a project vault. Never Obsidian `[[name]]`. A planned (not-yet-written)
   target is **plain text** or `_(to write: …)_`, never a link to a nonexistent file.
3. **Self-healing, not manual cleanup.** Memory and devlog ride the same **test → heal → gate** engine
   as code and the project vault: a validator flags drift, an idempotent `heal` repairs it, CI / pre-commit
   gates it. A one-time cleanup pass is a smell that the self-healing layer is missing.
4. **The load budget is real.** Native auto-memory loads only the first **~200 lines / 25 KB** of the
   index (`MEMORY.md`) at session start (verified, hard limit); topic files load on demand. The whole
   design serves this budget — the index stays lean, detail lives in fetched-on-demand files.
5. **Archive, don't delete — and keep it findable.** Files persist on disk forever; the budget governs
   only what *auto-loads*. Past budget, aged entries must be archived (dropped from the live index) **and**
   covered by search, so old memory is *saved AND findable*, never buried.

## Structure & hierarchy

- A **flat index + on-demand files** is the working shape. `MEMORY.md` is the index — one
  `description:` line per entry; each `slug.md` is the on-demand fetch. Memory files carry YAML
  frontmatter with a `type` (`user` / `feedback` / `project` / `reference`) and a one-line `description`
  written as a **search-result row**.
- **Group by theme**, with a **"last updated" signal** per entry, so the index is scannable and
  staleness is visible.
- **Hierarchical grouping** (`tools/`, `domain/<project>/`) is the *growth* shape — adopt it when a flat
  index strains the budget, not before. Each entry reads as **"date — what — why."**

## The budget & load-on-demand

- Keep the live index **under ~200 lines / 25 KB**. That is the auto-load ceiling — everything past it
  is on disk but not in the session unless fetched.
- **Detail lives in on-demand files**, never inlined into the index. The index is a routing table, not
  the content.
- When the index approaches the ceiling, **archive aged/superseded entries** rather than letting the
  tail silently fall out of the loaded window.

## The lifecycle: staging → promotion → pointer

1. **Staging** — a learning accrues as a normal curated memory entry.
2. **Promotion** — when it matures into something reusable (a skill, a tool, a doctrine, a convention),
   it is *promoted* out of memory into its proper home. A one-time converter that proves useful becomes a
   standing capability; a recurring lesson becomes doctrine.
3. **Pointer** — the memory entry then becomes a **pointer** to where the content now lives, instead of
   duplicating it. This keeps the index lean and prevents drift between memory and the system-of-record.

**Archival** is the lifecycle's tail: never delete a memory file; move aged/superseded entries to an
archive tier (dropped from the live index but on disk), and ensure **search covers the archive** so
archiving is not burying.

## Retrieval: the 3-layer search

The discipline is **search → index → fetch**, and the rule is **never load full content before filtering**:

1. **Search** over `description:` lines (already written as result rows) + bodies, with metadata filters
   (`--type`, `--since`) and relevance/date ordering.
2. **Index** — return a compact table (name · type · description · last-updated), ~1 line per hit.
3. **Fetch** the chosen file(s) on demand. Never dump full bodies first.

Implemented as a keyword search over description lines and bodies (and the same for devlog:
searchable by date / topic / tag). Keyword-over-descriptions now; vector/semantic deferred until
the corpus demands it.

## Links: OKF

- Real target exists → OKF link (`[text](slug.md)` in memory; `[text](/section/slug.md)` in a project vault).
- Planned target → plain-text marker `_(to write: …)_`. Never `[[ ]]`; never a link to a nonexistent file.
- After any **bulk** link conversion, **verify every link resolves** (a blanket substitution can mangle
  literal-example or forward-ref brackets into broken links — caught only by re-verification). This is why
  the convention must be *enforced by tooling*, not by careful hands.
  → Use `rv note` (to create/update note files), never hand-edit links.

## Self-healing: test / heal / gate

Memory and devlog are corpora of the self-healing vault, not exceptions to it:

- **Test** — `check` flags any `[[ ]]`, counts markdown links for backlink/orphan detection, and detects
  broken / duplicate-basename links across memory + devlog + project vaults.
- **Heal** — an idempotent `heal` (not a one-time migration) resolves `[[Title]]` → the right OKF link,
  converts empty `[[ ]]` → `_(to write: …)_`, fixes unambiguous broken links, regenerates `index.md`;
  ambiguous cases are **flagged for a human, never guessed**.
- **Gate** — `check` runs in CI / pre-commit so drifted memory can't land.

The `MEMORY.md` index itself should be **auto-generated** (theme-grouped + "last updated") by this engine,
so it can't go stale through manual neglect.

## CLAUDE.md & `.claude/rules` hygiene

`CLAUDE.md` is the always-loaded instruction layer; it shares the budget discipline.

- **Keep it tight.** Official guidance targets roughly **<200 lines** for reliable adherence (a guideline
  for adherence, not a hard cap). Bullets over prose. Declare **critical paths** and **verification
  commands** explicitly. State **prohibitions** — they're followed more reliably than positive guidance.
- **Separate global from project.** Global guidance in the user `CLAUDE.md`; project specifics in the
  repo's `CLAUDE.md`, which extends and may override global.
- **`.claude/rules/*.md` with `paths:` globs** — a verified Claude Code feature for **path-scoped
  conditional loading**: a rule file loads only when files matching its glob are in play. Use it to move
  context-specific instructions out of the always-on `CLAUDE.md` and into rules that load only when
  relevant — the same load-on-demand discipline, applied to instructions.

## Adopt vs reject

| Idea | Verdict |
|---|---|
| CLAUDE.md hygiene (tight, bullets, critical paths, verification cmds, prohibitions > positives) | **Adopt** |
| `.claude/rules/*.md` with `paths:` globs — path-scoped conditional loading | **Adopt** (verified real feature) |
| Separate global vs project instructions | **Adopt** |
| Hierarchical grouping (`tools/`, `domain/<project>/`); entry = "date, what, why" | **Adopt at growth** (flat index first) |
| Staging → promotion-to-skill → pointer lifecycle | **Adopt** |
| Load-on-demand (index loads, topic files on demand); "last updated" index column | **Adopt** |
| 3-layer retrieval (search → index of id·type·title → fetch on-demand) | **Adopt** |
| Metadata filters + full-text search | **Adopt** |
| Vector / semantic search | **Defer** (YAGNI until corpus demands it) |
| **Passive auto-capture** (auto-summarize every Read/Edit/Bash → auto-inject) | **REJECT** |
| Always-on background worker for memory | **REJECT** |

**Why reject passive capture:** an auto-captured, LLM-compressed "observation" with no traceable
source is an **ungrounded-claim injection vector** — precisely the fabrication risk the charter
forbids. Curated grounded memory stays the system-of-record.

## Right-sizing

- **Now (cheap, build today):** lean themed index + "last updated"; OKF links enforced by `rv check`
  + CI gate; keyword search over descriptions (grep); the staging→promotion→pointer lifecycle;
  archive-don't-delete; CLAUDE.md hygiene + `.claude/rules` for path-scoped context.
- **When it grows (defer until the corpus demands it):** hierarchical `tools/` · `domain/<project>/`
  directories; vector/semantic search; any PreToolUse memory-injection hook.
