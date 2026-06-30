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
