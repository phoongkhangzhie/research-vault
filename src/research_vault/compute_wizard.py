# SPDX-License-Identifier: AGPL-3.0-or-later
"""compute_wizard.py — the guided `run_compute_wizard()` interactive flow.

Net-new module (kept OUT of compute.py so it does not collide with concurrent
edits to ``cmd_init``).  Drives the full compute-declaration interactively:

    archetype → role/profile → host → submit → (loop) → W&B → CONFIRM → write

Design invariants (safety-critical):
  - **The compute manifest is the ONLY thing this module ever writes.**  It reads
    ``~/.ssh/config`` strictly read-only (via :mod:`sshconfig`) and persists via
    ``compute._save_manifest(cfg, manifest)`` to ``compute._manifest_path(cfg)``.
    It NEVER re-invokes ``cmd_init`` in-process (that was the F7 wrong-cfg crash);
    the caller's ``cfg`` is threaded straight through to ``_save_manifest``.
  - **No write before the explicit confirm.**  The assembled manifest is rendered
    and the user must answer ``[y/N]`` before anything lands on disk.  Confirm=No
    or EOF/Ctrl-C ⇒ nothing written.
  - **Non-TTY ⇒ detect + display only.**  Never mutates without an interactive
    confirm (preserves the onboard hand-off as the floor).
  - **`host` = the ssh alias** (``ssh <host>`` runs it directly) — never a
    duplicated HostName.  One ``host`` per profile; multiple endpoints = multiple
    profiles.  ``host_group`` is display-only (not modelled by the wizard v1).

Stdlib only.
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Callable

from .compute import (
    _FILL_PREFIX,
    _load_manifest,
    _manifest_path,
    _save_manifest,
)
from .sshconfig import SshAlias, detect_ssh_aliases

# Default submit patterns per scheduler archetype (mirrors the FILL-scaffold).
_SUBMIT_DEFAULTS = {
    "ssh+slurm": "sbatch --partition=FILL --account=FILL --gres=gpu:{gpus} --time=FILL",
    "ssh+pbs": "qsub -q FILL -A FILL",
}

# FILL sentinel value for an unset host (re-runnability: startswith(_FILL_PREFIX)).
_FILL_HOST = "FILL — ssh host alias (set via the wizard; must resolve in ~/.ssh/config)"

# Role presets → seeded when_to_use templates (the user confirms/edits).
_ROLE_TEMPLATES = {
    "compute-node": (
        "Submit training/eval JOBS here (scheduler). The compute/login node."
    ),
    "transfer-node": (
        "Big downloads + dataset/checkpoint STAGING here (plain ssh; high-bandwidth "
        "DTN). Same filesystem as the compute node. "
        "Anti-pattern: do NOT submit jobs here — there is no scheduler."
    ),
    "login": "Interactive/login shell + light pre/post-processing.",
}


class _WizardAbort(Exception):
    """Raised on EOF/Ctrl-C — caught at the top, guarantees no write."""


# ---------------------------------------------------------------------------
# Small IO helpers (input_fn injected in tests)
# ---------------------------------------------------------------------------

def _read(input_fn: Callable[[str], str], prompt: str) -> str:
    try:
        return input_fn(prompt)
    except (EOFError, KeyboardInterrupt):
        raise _WizardAbort() from None


def _ask_yes(input_fn: Callable[[str], str], question: str, *, default_no: bool = True) -> bool:
    suffix = " [y/N] " if default_no else " [Y/n] "
    ans = _read(input_fn, question + suffix).strip().lower()
    if not ans:
        return not default_no
    return ans in ("y", "yes")


def _menu(
    input_fn: Callable[[str], str],
    title: str,
    options: list[tuple[str, str]],
    default_idx: int,
) -> str:
    """Print a numbered menu; return the chosen VALUE. Blank ⇒ default."""
    print(title)
    for i, (val, label) in enumerate(options, 1):
        marker = "  (default)" if (i - 1) == default_idx else ""
        print(f"    {i}) {label}{marker}")
    raw = _read(input_fn, "    choice [number]: ").strip()
    if raw == "":
        return options[default_idx][0]
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(options):
            return options[idx][0]
    for val, _label in options:
        if raw.lower() == val.lower():
            return val
    print(f"    (unrecognised — using default: {options[default_idx][0]})")
    return options[default_idx][0]


# ---------------------------------------------------------------------------
# Per-step prompts
# ---------------------------------------------------------------------------

def _ask_archetype(input_fn: Callable[[str], str], which_fn: Callable[[str], str | None]) -> str:
    default_idx = 0  # local
    if which_fn("sbatch"):
        default_idx = 1
    elif which_fn("qsub"):
        default_idx = 2
    options = [
        ("local", "local — this machine"),
        ("ssh+slurm", "ssh+slurm — sbatch/sacct cluster"),
        ("ssh+pbs", "ssh+pbs — qsub/qstat cluster"),
        ("ssh", "ssh — plain remote host"),
    ]
    return _menu(input_fn, "  Archetype:", options, default_idx)


def _ask_role(input_fn: Callable[[str], str], archetype: str) -> tuple[str, str]:
    """Return (profile_name, when_to_use_template)."""
    options = [
        ("compute-node", "compute-node — job submission (scheduler)"),
        ("transfer-node", "transfer-node — data staging (DTN)"),
        ("login", "login — interactive shell"),
        ("custom", "custom — name it yourself"),
    ]
    if archetype in ("ssh+slurm", "ssh+pbs"):
        default_idx = 0
    elif archetype == "ssh":
        default_idx = 1
    else:
        default_idx = 3
    choice = _menu(input_fn, "  Role:", options, default_idx)
    if choice == "custom":
        name = _read(input_fn, "    profile name: ").strip() or "endpoint"
        return name, ""
    return choice, _ROLE_TEMPLATES.get(choice, "")


def _ask_when_to_use(input_fn: Callable[[str], str], template: str) -> str:
    if template:
        raw = _read(
            input_fn, f"  role note [{template}] (Enter=keep): "
        ).strip()
        return raw if raw else template
    raw = _read(input_fn, "  role note (blank to skip): ").strip()
    return raw


def _ask_host(
    input_fn: Callable[[str], str],
    ssh_aliases: list[SshAlias],
    wired_hosts: set[str],
) -> str:
    """Return the chosen host alias, or "" to skip (leave FILL)."""
    if ssh_aliases:
        print("  ssh aliases (~/.ssh/config):")
        for i, a in enumerate(ssh_aliases, 1):
            hint_parts: list[str] = []
            if a.hostname:
                hint_parts.append(a.hostname)
            if a.user:
                hint_parts.append(f"user={a.user}")
            hint = f"  ({', '.join(hint_parts)})" if hint_parts else ""
            wired = "  [wired]" if a.alias in wired_hosts else ""
            print(f"    {i}) {a.alias}{hint}{wired}")
        literal_idx = len(ssh_aliases) + 1
        skip_idx = len(ssh_aliases) + 2
        print(f"    {literal_idx}) type alias")
        print(f"    {skip_idx}) skip (leave FILL)")
        raw = _read(input_fn, "  host [number or alias]: ").strip()
        if raw == "":
            return ""
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(ssh_aliases):
                return ssh_aliases[n - 1].alias
            if n == literal_idx:
                return _read(input_fn, "    alias: ").strip()
            return ""  # skip or out-of-range
        return raw  # typed an alias directly
    print("  No ssh aliases detected in ~/.ssh/config.")
    return _read(input_fn, "  host alias (blank to skip): ").strip()


def _ask_submit(input_fn: Callable[[str], str], archetype: str) -> str:
    default = _SUBMIT_DEFAULTS.get(archetype, "")
    raw = _read(input_fn, f"  submit [{default}] (Enter=default): ").strip()
    return raw if raw else default


def _ask_wandb(manifest: dict[str, Any], input_fn: Callable[[str], str], env: dict[str, str]) -> None:
    """Ask for the W&B entity only — project defaults to the run's own slug (automatic).

    project is deliberately NOT prompted here: resolve_run_logging_target's
    per-project default (project_slug) covers the common case. An adopter who
    truly wants one static project shared across every project in this instance
    can still hand-edit results.wandb.project into the manifest afterwards.
    """
    print("\n  W&B (config only — API key stays in keyring):")
    print("  (project = your project slug, automatic per-run — entity only, below)")
    entity_prefill = env.get("WANDB_ENTITY", "")
    ep = f" [{entity_prefill}]" if entity_prefill else ""
    entity = _read(input_fn, f"  entity (username/team){ep}: ").strip() or entity_prefill
    results = manifest.setdefault("results", {})
    wandb = results.setdefault("wandb", {})
    if entity:
        wandb["entity"] = entity
    elif "entity" not in wandb:
        wandb["entity"] = ""


# ---------------------------------------------------------------------------
# Profile assembly + re-runnability
# ---------------------------------------------------------------------------

def _is_host_configured(prof: dict[str, Any]) -> bool:
    """A remote profile is configured iff its host is set and not a FILL sentinel."""
    host = prof.get("host", "")
    return bool(host) and not str(host).startswith(_FILL_PREFIX)


def _is_profile_configured(prof: dict[str, Any]) -> bool:
    if prof.get("archetype", "local") == "local":
        return True
    return _is_host_configured(prof)


def _build_one_profile(
    input_fn: Callable[[str], str],
    which_fn: Callable[[str], str | None],
    ssh_aliases: list[SshAlias],
    wired_hosts: set[str],
) -> tuple[str, dict[str, Any]]:
    archetype = _ask_archetype(input_fn, which_fn)

    if archetype == "local":
        wtu = _ask_when_to_use(input_fn, "Local subprocess runs (zero-infra default).")
        prof: dict[str, Any] = {"archetype": "local"}
        if wtu:
            prof["when_to_use"] = wtu
        return "local", prof

    name, wtu_template = _ask_role(input_fn, archetype)
    prof = {"archetype": archetype}
    wtu = _ask_when_to_use(input_fn, wtu_template)
    if wtu:
        prof["when_to_use"] = wtu

    host = _ask_host(input_fn, ssh_aliases, wired_hosts)
    prof["host"] = host if host else _FILL_HOST

    if archetype in _SUBMIT_DEFAULTS:
        prof["submit_pattern"] = _ask_submit(input_fn, archetype)

    # NAMES-only secret forwarding seed (values never stored — resolved at submit).
    prof["secrets_forward"] = ["WANDB_API_KEY"]
    return name, prof


def _compute_active(profiles: dict[str, dict[str, Any]]) -> list[str]:
    """Active = local (if present) + every configured remote profile, order-stable."""
    active: list[str] = []
    if "local" in profiles:
        active.append("local")
    for name, prof in profiles.items():
        if name == "local":
            continue
        if prof.get("archetype", "local") != "local" and _is_host_configured(prof):
            active.append(name)
    return active or ["local"]


def _remove_endpoint(
    input_fn: Callable[[str], str],
    profiles: dict[str, dict[str, Any]],
) -> None:
    """Unwire a configured remote endpoint: reset its host to FILL (manifest-only)."""
    removable = [
        n for n, p in profiles.items()
        if n != "local" and p.get("archetype", "local") != "local" and _is_host_configured(p)
    ]
    if not removable:
        print("  (no configured remote endpoints to remove)")
        return
    options = [(n, n) for n in removable]
    choice = _menu(input_fn, "  Which endpoint to unwire?", options, 0)
    prof = profiles.get(choice)
    if prof is not None:
        prof["host"] = _FILL_HOST
        print(f"  [OK] unwired {choice} (host reset to FILL; re-run the wizard to re-wire).")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_summary(manifest: dict[str, Any], path: Any) -> None:
    print("\n" + ("-" * 60))
    print("  Manifest (not yet written):")
    backends = manifest.get("backends", {})
    print(f"    active: {', '.join(backends.get('active', [])) or '(none)'}")
    for name, prof in backends.get("profiles", {}).items():
        arche = prof.get("archetype", "?")
        bits = [f"archetype={arche}"]
        if "host" in prof:
            bits.append(f"host={prof['host']}")
        if prof.get("submit_pattern"):
            sp = prof["submit_pattern"]
            bits.append(f"submit='{sp[:40]}…'" if len(sp) > 40 else f"submit='{sp}'")
        print(f"    - {name}: {', '.join(bits)}")
        if prof.get("when_to_use"):
            print(f"        role: {prof['when_to_use']}")
    wandb = manifest.get("results", {}).get("wandb", {})
    if wandb:
        ent = wandb.get("entity", "") or "(blank)"
        proj = wandb.get("project", "") or "<per-run slug, auto>"
        print(f"    W&B: entity={ent}  project={proj}")
    print(f"  Target: {path}")
    print("-" * 60)


def _display_only(
    manifest: dict[str, Any],
    ssh_aliases: list[SshAlias],
    skipped: list[str],
) -> None:
    """Non-interactive path: detect + display, NEVER mutate."""
    print("\n  Non-interactive: detect + display only (no changes).")
    if ssh_aliases:
        print("  ssh aliases (~/.ssh/config):")
        for a in ssh_aliases:
            hint = f"  → {a.hostname}" if a.hostname else ""
            print(f"    - {a.alias}{hint}")
    else:
        print("  No ssh aliases detected in ~/.ssh/config.")
    if skipped:
        print(f"  (skipped unreadable includes: {', '.join(skipped)})")
    print("  → `rv compute init --guided` (or `rv onboard`) at a TTY to configure.")
    print("  → `rv compute init` for the plain FILL-scaffold, then `rv doctor`.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_compute_wizard(
    cfg: Any,
    *,
    interactive: bool,
    input_fn: Callable[[str], str] | None = None,
    ssh_config_path: str | os.PathLike[str] | None = None,
    which_fn: Callable[[str], str | None] | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Run the guided compute-declaration wizard. Always returns 0.

    ``cfg`` is threaded straight through to ``_save_manifest`` — the manifest
    lands at ``_manifest_path(cfg)`` and nothing else is ever written.  At a
    non-interactive stdin (``interactive=False``) this only detects + displays;
    it never mutates without the explicit confirm.
    """
    input_fn = input_fn or input
    which_fn = which_fn or shutil.which
    env = env if env is not None else dict(os.environ)

    # Read-only ssh-config scan (never raises).
    skipped: list[str] = []
    ssh_aliases = detect_ssh_aliases(ssh_config_path, skipped_out=skipped)

    manifest = _load_manifest(cfg)
    manifest.setdefault("backends", {}).setdefault("profiles", {})
    profiles: dict[str, dict[str, Any]] = manifest["backends"]["profiles"]

    if not interactive:
        _display_only(manifest, ssh_aliases, skipped)
        return 0

    try:
        _run_interactive(cfg, manifest, profiles, input_fn, which_fn, env, ssh_aliases, skipped)
    except _WizardAbort:
        print("\n  (aborted — nothing written to the manifest.)")
        return 0
    return 0


def _run_interactive(
    cfg: Any,
    manifest: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
    input_fn: Callable[[str], str],
    which_fn: Callable[[str], str | None],
    env: dict[str, str],
    ssh_aliases: list[SshAlias],
    skipped: list[str],
) -> None:
    path = _manifest_path(cfg)
    print("\n  Declare where jobs run (nothing written until you confirm).")
    if skipped:
        print(f"  (skipped unreadable ssh includes: {', '.join(skipped)})")

    # Re-runnability: report already-configured, offer REMOVE.
    configured = [n for n, p in profiles.items() if _is_profile_configured(p) and n != "local"]
    if configured:
        print(f"  Configured (kept unless unwired): {', '.join(configured)}")
        if _ask_yes(input_fn, "  Unwire an existing endpoint?", default_no=True):
            _remove_endpoint(input_fn, profiles)

    # ADD loop.
    if _ask_yes(input_fn, "  Configure a compute endpoint now?", default_no=False):
        while True:
            wired_hosts = {
                p.get("host", "") for p in profiles.values() if _is_host_configured(p)
            }
            name, prof = _build_one_profile(input_fn, which_fn, ssh_aliases, wired_hosts)
            profiles[name] = prof
            print(f"  [staged] profile '{name}' (archetype={prof['archetype']}).")
            if not _ask_yes(input_fn, "  Configure another endpoint?", default_no=True):
                break

    # W&B.
    _ask_wandb(manifest, input_fn, env)

    # Rebuild active from configured profiles.
    manifest["backends"]["active"] = _compute_active(profiles)

    # CONFIRM → write (no write before the explicit [y/N]).
    _render_summary(manifest, path)
    if _ask_yes(input_fn, f"  Write this manifest to {path}?", default_no=True):
        _save_manifest(cfg, manifest)
        print(f"  [OK] compute manifest written: {path}")
        print("  Next: `rv doctor` to probe capabilities per backend.")
    else:
        print("  (declined — nothing written.)")
