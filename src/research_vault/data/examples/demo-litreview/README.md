# demo-litreview — Literature Review Loop Example

A runnable demonstration of the Research Vault **lit-review loop** DAG.

## What this demonstrates

The `lit-review-loop.json` manifest encodes the OKF-coupled literature review:

1. **Scope** (researcher) — define what papers are in scope for this review
2. **Survey** (researcher) — identify and collect in-scope papers
3. **Distill paper 1** (researcher) — read and file an OKF literature note
4. **Distill paper 2** (researcher) — read and file another OKF literature note
5. **OKF coverage gate** — you verify every in-scope paper has a literature note
6. **Synthesize** (researcher) — extract claims to concepts/, build index in mocs/
7. **Synthesis critic** (reviewer) — Argus flags orphan concepts and missing MOC links
8. **Human-go gate** — final review of synthesis quality

## The OKF coverage gate

The `okf-coverage-gate` human-go node becomes approvable only when ALL distill nodes
have succeeded. A distill node CANNOT succeed (via `rv dag complete`) unless its
`produces: {note: "literature/<key>.md"}` note exists with the correct `type: literature`
frontmatter.

This means: **every in-scope paper gets a literature note before synthesis begins.**

## Running the loop

```bash
# Start the loop
rv dag run examples/demo-litreview/lit-review-loop.json

# After scoping and surveying...
rv dag complete lit-review-loop-topic scope
rv dag complete lit-review-loop-topic survey

# Distill each paper (must create literature/<key>.md first)
rv note demo-litreview new literature "Smith et al. 2024" --id smith2024
rv dag complete lit-review-loop-topic distill-paper-1

rv note demo-litreview new literature "Jones 2023" --id jones2023
rv dag complete lit-review-loop-topic distill-paper-2

# Tick and approve the coverage gate
rv dag tick lit-review-loop-topic
rv dag approve lit-review-loop-topic okf-coverage-gate
```

## OKF note types used

| Node | Produces | Directory |
|------|----------|-----------|
| distill-paper-1 | literature note | `notes/literature/` |
| distill-paper-2 | literature note | `notes/literature/` |
| synthesize | concepts (soft) | `notes/concepts/` |
| synthesize | MOC links (soft) | `notes/mocs/` |

## Cross-project corroboration (SR-XPB) — extending the synthesis stage

After synthesis, the hub can wire in the `corroborate → judge → assert` fragment to
corroborate findings against declared peer projects.  See
`corroborate-judge-fragment.json` for the full DAG node fragment.

### Prerequisites

1. **Hub declares edges first** (one-time setup):
   ```bash
   rv project relate <your-project> <peer-project> --kind <why>
   rv project edges   # verify the edge is declared
   ```

2. **Corroborate** (after synthesis node completes):
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
