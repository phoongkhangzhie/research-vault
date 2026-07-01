# Drift-watch — source provenance for doctrine/

This file records where each doctrine document was copied (or synthesized) from, so
periodic re-sync is tractable. When the upstream source changes significantly, diffing
against this table tells you what to update here.

**Access:** `~/vault` is the private upstream source. It is read-only to this repo — only
the operator may push to it. This file is maintained by the engineer who ports a document;
update the "last synced" date when you re-port after an upstream change.

## Source map

| doctrine/ file | upstream source | last synced |
|---|---|---|
| `agent-charter.md` | `~/vault/src/content/docs/method/agent-charter.md` | 2026-06-30 |
| `note-conventions.md` | `~/vault/src/content/docs/method/conventions.md` | 2026-06-30 |
| `standards.md` | `~/vault/src/content/docs/method/standards.md` | 2026-06-30 |
| `review-board.md` | `~/vault/src/content/docs/method/review.md` | 2026-06-30 |
| `coordination.md` | `~/vault/src/content/docs/method/coordination.mdx` | 2026-06-30 |
| `memory-management.md` | `~/vault/src/content/docs/method/memory-management.md` | 2026-06-30 |
| `roles/alfred.md` | synthesized (charter orchestration + coordination + how-it-works.md) | 2026-06-30 |
| `roles/wren.md` | `~/vault/src/content/docs/method/roles/architect.md` | 2026-06-30 |
| `roles/atlas.md` | `~/vault/src/content/docs/method/roles/manager.md` | 2026-06-30 |
| `roles/mason.md` | `~/vault/src/content/docs/method/roles/engineer.md` | 2026-06-30 |
| `roles/argus.md` | `~/vault/src/content/docs/method/roles/reviewer.md` | 2026-06-30 |
| `roles/iris.md` | `~/vault/src/content/docs/method/roles/designer.md` | 2026-06-30 |
| `roles/ada.md` | `~/vault/src/content/docs/method/roles/researcher.md` | 2026-06-30 |

## Scrub summary (applied to all ported docs)

The following classes of content were stripped on port; any re-sync must re-apply these scrubs:

1. **Private identity strings** — operator name, cluster username, GitHub handle → generic
   "the operator" or placeholder
2. **Private project codenames** — specific research project names → removed or replaced with
   generic descriptions
3. **Private site/URL** — operator's personal domain → removed
4. **Private cluster paths** — specific cluster filesystem paths → generic path placeholders
5. **CLI name** — `vault` command → `rv` (the research-vault CLI)
6. **Private route examples** — `<codename>-reviewer` hat names → `<project>-reviewer`
7. **Private design themes** — specific project/site palette names → removed
8. **Versioned model IDs** — specific pinned model version strings → kept as abstract tier names
   (Sonnet/Opus/Haiku) only
9. **Private memory slugs** — personal journal/Q&A memory files → not ported
10. **Internal /method/ links** → relative doctrine/ links

## Re-sync procedure

When a source doc changes substantially:

```bash
# 1. Read the upstream source
cat ~/vault/src/content/docs/method/<source>.md

# 2. Diff against the current doctrine/ version
diff ~/research-vault/doctrine/<target>.md <(cat ~/vault/src/content/docs/method/<source>.md)

# 3. Port the changes (applying the scrub rules above)
# Edit doctrine/<target>.md

# 4. Run the leakage scanner
bash ~/research-vault/scripts/leakage_scan.sh doctrine

# 5. Update this file's "last synced" date
# 6. Commit
```
