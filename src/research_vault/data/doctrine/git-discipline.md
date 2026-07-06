# Git discipline

Healthy git habits for the research-vault crew, without requiring a named
identity system.  Every constraint here has a tooled form — the doctrine
states the judgment; the tools enforce the mechanics.

## The core rule: never work on main

All work follows the path: **worktree → branch → PR → CI green → reviewer
dispatch → merge**.  No commit lands on main except via a PR that cleared the
gate.  The `rv wt` verb creates and removes the worktree; the protect-main
hook refuses a direct commit to main in case the path is skipped.

Anti-patterns this guards against:

- **Committed to main directly** — the protect-main hook refuses it; `rv git-health`
  flags the stale state.
- **Never made a worktree** — skip `rv wt` and you skip isolation; protect-main
  catches the resulting direct-to-main attempt.
- **Hand-merged on a red CI** — only green CI + reviewer pass authorizes a merge;
  the gate does not accept "it looked fine."

## Worktree convention

```
rv wt add <task>                    # framework repo (instance_root)
rv wt add <task> --project <slug>   # project repo (source_dir)
rv wt add <task> --as <role>        # set crew git identity by construction
```

Worktrees live in `<repo>-wt/` (sibling, never nested inside the repo).
`core.hooksPath` installed by `rv git-discipline install` means every worktree
of that repo inherits the hooks automatically — no per-worktree setup.

## Git hooks

Installed per-repo via `core.hooksPath`:

```
rv git-discipline install              # framework repo
rv git-discipline install --project <slug>   # a project repo
rv git-discipline install --all        # framework + every registered project repo
```

Consent: `init` and `project add` print the one-liner above — never a silent
install.  A stranger cloning the repo is not surprised by commit-blocking hooks
they did not invite.

**pre-commit** (cheapest-reject-first):

1. **protect-main** — branch ∈ {main, master} + any staged path outside the
   configured `[git_discipline] protect_main_allowlist` → REFUSE.  Default
   allowlist is empty (all direct commits to main refused).  Bypass consciously:
   `git commit --no-verify` or `RV_ALLOW_MAIN_COMMIT=1`.

2. **Staged leakage scan** (profile-aware) — scans only the staged files:
   - *Framework repo* (the public OSS package): secrets + private-markers (all
     9 classes: codenames, identity strings, site URLs, cluster paths, secrets,
     versioned model IDs, memory-template slugs, citekeys, project-registry ids).
   - *Project repo* (the researcher's own content): **secrets only** (class 5).
     A project repo may legitimately contain codenames, bibliography, cluster
     paths — gating it on private-marker classes is wrong.

   **Operator-naming convention (class 2 — private identity strings):** crew
   members must refer to the human as **"the operator"** in all committed
   artifacts — DEVLOG entries, commit messages, docs, and code comments.  Never
   use a personal name.  The leakage gate (class 2) treats the operator's name as
   a private-marker and will BLOCK the staged commit.  A DEVLOG entry like
   "per the operator's decision" is correct; using the operator's actual name
   in its place is a gate-caught leak.  This applies to framework *and* project
   repos — the personal name is a private marker in both profiles.

3. **`rv lint`** — when `src/` files are staged.

**commit-msg** — enforces conventional-commit subject format:

```
<type>(<scope>): <description>
types: feat | fix | docs | refactor | test | chore | ci | build | perf
```

## Crew git identity

When a crew member works in a worktree, the commit authorship should reflect
the role for readability.  Use `--as <role>` at worktree creation:

```
rv wt add <task> --as <role>
```

This sets `git config user.email = mason@<crew-domain>` and
`git config user.name = Mason` in the new worktree — by construction,
not by a separate "activate" step that can be forgotten.

The crew domain is configured in `research_vault.toml` under `[crew]
identity_domain`.  The public repo default is a placeholder (`example.invalid`);
the real domain lives in private instance config.

**Important:** authorship in git history carrying `<role>@<crew-domain>` is the
accepted carve-out for attribution in a public repository.  The leakage scanner
scans *file content*, not commit metadata.  A reviewer must not flag this
attribution as a leak.

## Separation of duties (single-account model)

Research-vault runs under **one GitHub account** — the adopter's.  Author ≠
reviewer is not a GitHub-account check; it is the **TTY approve-gate**: a
dispatched agent has no interactive terminal, so it cannot satisfy the approval
prompt.  The human operator is structurally the approving party.

| Layer | What it enforces | Mechanism |
|---|---|---|
| **GitHub branch-protection** | require PR · require CI · no direct push · no force-push · require conversation resolution | Server-side, real |
| **Role-hat dispatch** | author ≠ reviewer as *process*: the reviewer hat is a **distinct dispatch** from the engineer hat (fresh subagent, reviewer lens, no shared author context) | Structural in the dispatch |
| **TTY approve-gate** | the approval step requires an interactive terminal — a dispatched crew agent has none; only the human operator can satisfy it | `approval.py` (`stdin.isatty()`) |

The honest bottom line: for a solo human operator, the hooks plus green CI plus
the TTY approve-gate are the whole enforcement story.  For the AI-runs-the-crew
mode, author ≠ reviewer is the role-hat + require-review + TTY gate — not a
cryptographic identity guarantee, but a structural one with real teeth.

## Branch protection guidance

`rv git-discipline install` prints the recommended ruleset for each repo:

- Require a PR before merging.
- Require status checks (CI) to pass before merging.
- Block force-push and deletion of the base branch.
- Require conversation resolution before merging.
- "Require a different reviewer" (GitHub setting) is not needed — the TTY
  approve-gate enforces the crew-cannot-self-approve boundary structurally.

A project repo kept purely local (un-hosted) has no server-side protection.
The hooks plus this doctrine are the whole gate there; that must be stated
explicitly when registering the project.

## Tooled enforcement map

| Discipline | Tool | Location |
|---|---|---|
| Never commit to main | protect-main check | `.githooks/pre-commit` → `rv git-discipline check` |
| No secrets in commits | staged leakage scan (class 5) | `.githooks/pre-commit` |
| No private markers in framework commits | staged leakage scan (classes 1-4, 6-9) | `.githooks/pre-commit` (framework profile) |
| Conventional commit format | commit-msg check | `.githooks/commit-msg` → `rv git-discipline commit-msg` |
| Crew identity by construction | `--as <role>` at wt creation | `rv wt add --as` |
| Stale branch cleanup | Signal D (squash-merge detection) | `rv git-health --prune` |
| Branch protection guidance | per-repo ruleset print | `rv git-discipline install` |
