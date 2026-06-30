## 2026-06-30 (fix-round)

### Done
- BLOCKER 1: moved `project` positional to top-level verb parser for all 4 verbs (task, note,
  control, devlog) — documented form `rv task <project> <subcommand>` now works. All CLI tests
  updated to project-first form; new test exercises the documented form (was never tested).
- BLOCKER 2: fixed `_default_config()` to use relative sub-paths ("notes", "tasks", etc.)
  instead of cwd-absolute paths — `_expand_paths()` now resolves them against instance_root,
  so a config with only `instance_root` set correctly derives all paths from it.
  New test: minimal config (instance_root only) asserts all derived paths are under instance_root.
- BLOCKER 3: replaced a bare principal name with "owner" in the control.py template
  (docstring, _render_control_file, cmd_inbox fallback) and in control/acme.md. Added
  the corresponding word-boundary marker (case-insensitive) to the CI leakage scanner
  so this class of leak can't slip through again.
- 72 tests passing (was 70), rv help --check OK, leakage grep clean.

### Decisions
- Used "owner" (not "<owner>" or "the principal") in control template — clean, short, generic.
- Relative defaults in _default_config() rather than a sentinel/None approach — simpler, and
  _expand_paths() already had the relative-path resolution logic.

### Open / next
- PR #1 (feat/sr-1-scaffold) ready for re-review by rv-reviewer + rv-architect fit-check.

## 2026-06-30

### Done
- SR-1 scaffold: standalone package skeleton, config plane, CLI dispatcher, first 4 portable verbs
  (task, note, control, devlog), `rv help --check`, CI skeleton, tests.
- Package: `research_vault` / CLI verb `rv`, uv-managed, Python 3.12, pytest hermetic suite.
- Config plane: multi-project registry SSOT in `config.py` — zero hardcoded paths, zero codenames.
- Verbs are project-scoped: `rv task <project> …`, `control/<project>.md`, etc.

### Decisions
- Config SSOT: `research_vault.toml` (TOML, instance-local) with env-override escape hatch
  (`RESEARCH_VAULT_CONFIG`). Multi-project registry mirrors the live projects.json shape.
- Verbs re-implemented fresh from behavioral spec — no byte-for-byte copy of ~/vault code.
- `rv help --check` greens only when every registered verb has a `when_to_use` docstring.
- Leakage-scanner CI job: skeleton in place (grep for private markers); teeth land at SR-4.

### Open / next
- SR-2: remaining verbs (research, cite, role, build-agents, mdstore, wt, git-health, lint,
  wait-for) + adapter Protocols (Notifier, ComputeBackend, SecretStore) + plugin seam.
- ZERO ~/vault edits confirmed: all build + test work happened inside ~/research-vault.
