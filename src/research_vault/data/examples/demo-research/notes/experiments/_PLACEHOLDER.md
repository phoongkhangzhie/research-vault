---
type: experiments
---

# experiments/ — Pre-registration notes

Each file in this directory is a pre-registration note for one experiment.
The research loop enforces that this note is filed BEFORE the run node fires.

**Naming convention:** `exp-<id>.md`

**Required frontmatter:**
```yaml
---
type: experiments
citekey: exp-<id>
title: <Experiment title>
description: <one-sentence summary of the note (optional; WARN if empty)>
---
```

The `type: experiments` field must match this directory name (OKF type-dir contract).
The DAG's `produces: {note: "experiments/exp-<id>.md"}` + `afterok` watch enforces filing.
