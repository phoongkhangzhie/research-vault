# literature/ — the shared-canonical literature store (example)

This directory is the shared, cross-project store — `cfg.literature_root`.
It sits at the **instance/hub level**, a sibling of every registered
project's own notes directory (`demo-research/`, `demo-litreview/`), NOT
inside either one.

## What lives here

One file per paper, keyed by canonical citekey: `literature_root/<citekey>.md`.
Each file is the ONE note for that paper — project-independent content,
shared by every project that cites it (the overlay unwind (0.3.2),
dissolved the earlier two-layer core-plus-per-project-overlay split):

- identity ids (`doi`, `arxiv_id`, `pmcid`, `openalex`, `pmid`, `s2`)
- `contribution_kind`, `result_reported` + body `## Result`
- `key_equations` (a criticality ledger) + body `## Key equations`
- body `## Related papers` — the typed paper->paper edges (Move 4)
- body `## Concept edges` — the typed paper->concept edges (Move 5)

**Never here:** `role`/`position` — those are RQ-relative and belong in
each adopting project's curated MOC (see `demo-litreview/notes/mocs/`).
Corpus membership ("this paper is in project X's corpus") is likewise
never a field on this note — it's recorded mechanically in the project's
corpus ledger (`review/ledger.py`).

## The worked example

`smith2024.md` and `jones2023.md` are the two papers `demo-litreview`'s
`lit-review-loop.json` relates (`relate-smith2024`/`relate-jones2023`).
`demo-litreview/notes/mocs/literature-roles.md` narrates each paper's
RQ-relative role (methodological / counter-position) for that project.

Read through `note.cmd_list`/`note.cmd_check` (or a direct
`cfg.literature_root` glob, since there's no overlay indirection left to
resolve) — never hand-assemble a "core + overlay" pair, that model is gone.
