---
type: literature
---

# literature/ — Literature distillation notes

Each file is a distilled note for one paper in the review corpus.
The lit-review loop's distill nodes produce these notes.
The OKF coverage gate blocks until ALL in-scope papers have a note here
(or are recorded as MENTION-ONLY).

**Naming convention:** `<citekey>.md`  (e.g., `smith2024.md`, `jones2023.md`)

**Required frontmatter:**
```yaml
---
type: literature
citekey: <citekey>
title: <Paper title>
---
```
