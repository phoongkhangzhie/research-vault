# demo-litreview — Literature Review Loop Example

A runnable demonstration of the Research Vault **lit-review loop** DAG.

## What this demonstrates

The `lit-review-loop.json` manifest mirrors the shipped two-phase loop built by
`review/__init__.py` (`_build_phase1_manifest` + `_build_phase2_manifest`) as a
single static illustration:

**Phase-1 (7 nodes) — discovery, pre-registered, citation-neighbor relevance walk:**

1. **review-scope** (researcher) — freeze the question, seed queries,
   inclusion/exclusion, and a REQUIRED counter-position (L-2 anti-fishing gate)
2. **approve-protocol** — Gate 1: human approves the protocol before any search fires
3. **review-search** (tool, deterministic) — width-sweep the frozen protocol's
   angle matrix; protocol-gated by an artifact-watch on `_protocol.md`
4. **review-screen** (researcher) — apply inclusion/exclusion to the search hits,
   accept a seed frontier
5. **review-snowball** (tool, deterministic) — both-direction citation-neighbor
   relevance walk, depth-bounded by `relevance_hops` (default 1)
6. **review-curate** (researcher) — concept-tag + curate the raw corpus into
   the final `_corpus.md`
7. **coverage-gate** — Gate 2: resolved AUTONOMOUSLY (single-human-gate
   design: `approve-protocol`, Gate 1, is the only genuine human gate in this
   loop). Every `[NEW]` citekey must have a relate slot or be recorded
   MENTION-ONLY before this resolves. Resolution authorizes the Phase-2
   fan-out.

**Phase-2 (5 nodes) — per-paper distillation + synthesis:**

8. **relate-smith2024** / **relate-jones2023** (researcher, parallel) — read and
   file an OKF literature note per in-scope paper, applying the 5-move
   principled paper-reading protocol
9. **review-synthesize** (researcher) — extract claims to `concepts/`, build the
   index in `mocs/`
10. **review-coverage-critic** (reviewer, rejects-only) — flags premature
    walk termination, orphan concepts, protocol non-adherence, and a missing/ignored
    counter-position ([PASS]/[BLOCK])
11. **approve-review** — Gate 3: [BLOCK] count + counter-position verdict —
    resolved AUTONOMOUSLY (single-human-gate design: only `approve-protocol`,
    Gate 1, is a human gate; `rv dag approve --auto` or the self-advancing
    runner resolve this one from `review-coverage-critic`'s verdict)

## The two-phase fan-out

`coverage-gate` (Gate 2) is the phase boundary: in the real scaffolder, Phase-2
is a SEPARATE manifest (`phase2-dag.json`) emitted by `rv review <project>
expand <scope>` only after the gate resolves — this resolves the "a static
manifest cannot fan out over a runtime-discovered set" constraint. This demo
shows both phases spliced into a single static
file (with two hardcoded example papers) purely for a linear, self-contained
walkthrough — a real run always has this boundary as two separate DAG runs.

A `relate-<key>` node CANNOT succeed (via `rv dag complete`) unless its
`produces: {note: "literature/<key>.md"}` note exists with the correct `type:
literature` frontmatter AND answers the mandatory reading-discipline checklist
(`contribution_kind`, `role`, `position`, `result_reported`,
`paper_relations_sought` — Wave 0 Reading). This means:
**every in-scope paper gets a genuinely-read literature note before synthesis
begins.**

## The shared-canonical literature store

`literature/<key>.md` is **shared-canonical** (the overlay unwind (0.3.2), the overlay
unwind — earlier the store was two layers, a cross-project central core
plus a thin per-project overlay glued by a `central:` pointer; that
overlay is now dissolved): ONE note per paper at
`../literature/<citekey>.md` (a sibling of this project,
`cfg.literature_root`), carrying intrinsic paper facts (ids,
`contribution_kind`, `## Result`) AND the typed edge graph (paper->paper
`## Related papers`, paper->concept `## Concept edges`) directly. `rv note
<project> new literature` writes (or, for an already-distilled paper,
adopts) this ONE note; every reader routes through `note.cmd_list`/
`note.cmd_check` against `cfg.literature_root` — never hand-assemble a
"core + overlay" pair, that model is gone.

**Corpus membership** ("this paper is in project X's corpus") lives in the
project's mechanical corpus ledger (`review/ledger.py`), never a field on
the note. **RQ-relative role/position** (methodological, counter-position…)
is CURATED narration in a project MOC — see
`notes/mocs/literature-roles.md`, this example's worked pair.

This example ships `smith2024` and `jones2023` PRE-FILLED — the shared
notes live at `../literature/smith2024.md` / `../literature/jones2023.md`.
Because they already exist, the walkthrough below skips the `rv note
<project> new literature` step for these two papers — a `relate-<key>`
node just needs `rv dag complete` once its note is genuinely read (or, for
a fresh third paper, run `rv note <project> new literature` first, exactly
as before).

`rv literature list demo-litreview` enumerates this project's adopted
papers — the union of every citekey in any `_corpus_ledger.md` this
project has produced (mechanical membership, not a filesystem dir any
more) — enriched with each paper's resolving ids + citekey-conformance
FROM that same ledger (zero recomputation — see `literature.py`).

## Running the loop

```bash
# Start the loop
rv dag run examples/demo-litreview/lit-review-loop.json

# Scope, then approve Gate 1
rv dag complete lit-review-loop-topic review-scope
rv dag approve lit-review-loop-topic approve-protocol

# Search (deterministic tool) -> screen -> snowball (deterministic tool) -> curate
rv dag complete lit-review-loop-topic review-search
rv dag complete lit-review-loop-topic review-screen
rv dag complete lit-review-loop-topic review-snowball
rv dag complete lit-review-loop-topic review-curate

# Approve Gate 2 (authorizes the Phase-2 fan-out)
rv dag approve lit-review-loop-topic coverage-gate

# Relate each in-scope paper. smith2024/jones2023 ship PRE-FILLED — a
# fresh review would instead run `rv note <project> new literature "<title>" --id <key>`
# first (creates or adopts the ONE shared note, shared-canonical since
# the overlay unwind (0.3.2)), then complete the reading-discipline checklist before this:
rv dag complete lit-review-loop-topic relate-smith2024
rv dag complete lit-review-loop-topic relate-jones2023

rv dag complete lit-review-loop-topic review-synthesize
rv dag complete lit-review-loop-topic review-coverage-critic

# Approve Gate 3 — final review
rv dag approve lit-review-loop-topic approve-review
```

## OKF note types used

| Node | Produces | Directory |
|------|----------|-----------|
| relate-smith2024 | literature note (shared-canonical) | `../literature/` |
| relate-jones2023 | literature note (shared-canonical) | `../literature/` |
| review-synthesize | concepts (soft) | `notes/concepts/` |
| review-synthesize | MOC links (soft) | `notes/mocs/` |

## Cross-project corroboration — extending the synthesis stage

After synthesis, the hub can wire in the `corroborate → judge → assert` fragment to
corroborate findings against declared peer projects.  See
`corroborate-judge-fragment.json` for the full DAG node fragment.

### Prerequisites

1. **Hub declares edges first** (one-time setup):
   ```bash
   rv project relate <your-project> <peer-project> --kind <why>
   rv project edges   # verify the edge is declared
   ```

2. **Corroborate** (after the review-synthesize node completes):
   ```bash
   rv research corroborate "<claim-from-synthesis>" \
     --from <your-project> \
     --emit state/corroboration-candidates.json
   ```
   If no declared peers: the tool prints a nudge; declare an edge first.

3. **Dispatch the judge** via `rv dag brief <run-id> judge-corroboration`:
   The brief embeds the candidates JSON in `reads:`.  The judge assesses each
   candidate for GENUINE corroboration (same construct, compatible
   operationalization) — accepts or rejects WITH a recorded reason.

4. **Human reviews** the judgment (human-go gate).

5. **Assert** — the researcher writes a findings note with `corroborated_by:`
   frontmatter for accepted candidates only.

**Anti-pattern:** do NOT assert from rank alone.  Rank narrows; judge confirms;
human reviews; then assert.  The fragment structure enforces this: the assert node
reads the judgment report, not the raw candidates.

### Provenance format

Each accepted candidate carries `@slug:note_rel:anchor` provenance:
```yaml
corroborated_by:
  - "@peer-project:findings/their-finding.md:Key Finding"
```
The anchor resolves to the nearest preceding markdown heading in the source note
(or `line-N` if no heading precedes the match).
