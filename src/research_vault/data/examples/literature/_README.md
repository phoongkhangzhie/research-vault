# literature/ — the CENTRAL two-layer literature store (example)

This directory is the shared, cross-project CENTRAL STORE — `cfg.literature_root`
(PR-A: the central two-layer literature store, §0.5 PR-A/PR-B). It sits at the
**instance/hub level**, a sibling of every registered project's own notes
directory (`demo-research/`, `demo-litreview/`), NOT inside either one.

## What lives here

One file per paper, keyed by canonical citekey: `literature_root/<citekey>.md`.
Each file is the **central core** — intrinsic paper facts, distilled ONCE and
shared by every project that adopts the paper:

- identity ids (`doi`, `arxiv_id`, `pmcid`, `openalex`, `pmid`, `s2`)
- `contribution_kind`, `result_reported` + body `## Result`
- `key_equations` (a criticality ledger) + body `## Key equations`
- body `## Related papers` — the typed paper->paper edges (Move 4)

**Never here:** `role`, `position`, or concept-edges — those are RQ-relative
and belong in each adopting project's thin **overlay** (see
`demo-litreview/notes/literature/`).

## The worked example

`smith2024.md` and `jones2023.md` are the two papers `demo-litreview`'s
`lit-review-loop.json` relates (`relate-smith2024`/`relate-jones2023`). Each
has a matching thin overlay at
`demo-litreview/notes/literature/<citekey>.md` carrying `central: <citekey>`
+ this project's `role`/`position`/concept-edges — together they demonstrate
the full two-layer split a real `rv note <project> new literature` write produces.

Read through the resolver, never by hand-globbing this directory + parsing
frontmatter directly: `note.load_literature_note(cfg, project, citekey)` /
`note.iter_literature_notes(cfg, project)`.
