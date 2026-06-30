# Research Vault

An adoptable, zero-infra AI research-assistant framework.

**Status:** private alpha (SR-1 scaffold). Not yet public — leakage gate + doctrine copy ship at SR-4
before the repo opens.

## What it is

Research Vault is a standalone CLI framework for running a full AI-assisted research loop: literature
review, experiment planning, analysis, and findings — grounded by the disciplines that make an AI
trustworthy for research (no fabrication, every outcome is a finding, verify the artifact, not the signal).

The **doctrine is the headline, not the code.** The code is the proof those disciplines are runnable.

## Prerequisites (stated, not abstracted)

- Python 3.12+, uv
- Claude CLI (`claude`) — the agent runtime. Research Vault is a Claude-narrated system; it names this
  dependency rather than abstracting it.
- asta (for the `research` verb, SR-2+)
- Zotero + API key (for the `cite` verb, SR-2+)

## Install (when published)

```bash
uv tool install research-vault
rv --help
```

## Quick start (SR-5+, after `rv init` ships)

```bash
rv init my-research-instance
cd my-research-instance
rv check            # preflight: verify prerequisites
rv task demo-research add "My first task"
```

## Build status

| SR | What | Status |
|---|---|---|
| SR-1 | Package scaffold + config plane + `task`/`note`/`control`/`devlog` | THIS PR |
| SR-2 | Remaining verbs + adapter Protocols + local-defaults | — |
| SR-3 | DAG orchestrator + OKF typed-artifact coupling | — |
| SR-4 | Portable doctrine + full named crew + leakage gate teeth | — (human-go) |
| SR-5 | Example loops + `rv init` + multi-project demo | — |

## The standalone boundary

The live `~/vault` is **NOT a dependency, NOT imported, NOT edited** — ever. Research Vault is built
fresh, like any other project. See `architecture.md` for the full map.
