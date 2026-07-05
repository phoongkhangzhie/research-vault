"""check.py — `rv check` — preflight prerequisite check.

When to use: ``rv check`` before starting any research loop. Verifies that
all prerequisites are available and reports missing items with clear install
instructions. Fail-fast: reports ALL failures, not just the first.

Checks:
  1. Claude CLI — ``claude --version`` must succeed (the agent runtime)
  2. ANTHROPIC_API_KEY — must be set in env or resolvable via keyring
  3. Toolkit Tier-1 — 27-package research-toolkit core (installed by default)
  4. Toolkit Tier-2 — GPU-fragile local-inference stack ([local] extra)
  5. asta / Zotero / W&B — integration checks (optional)

Per-provider SDKs (openai/google-genai/mistralai/cohere) and figure libs
(matplotlib/seaborn) are NOT shipped — the adopter installs them directly.
litellm (Tier-1 core) covers most providers without a per-provider SDK.

Exit codes:
  0 — all required prerequisites present (optional checks may warn)
  1 — one or more REQUIRED prerequisites missing

Stdlib only for the module itself — all toolkit checks use guarded imports.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from typing import Any


# ---------------------------------------------------------------------------
# Tier-1 package registry — 27-package research-toolkit core
# ---------------------------------------------------------------------------
# Each entry: (pip_name, import_name, group_label, purpose)
_TIER1_PACKAGES: list[tuple[str, str, str, str]] = [
    # core-4: primary model seam + Anthropic SDK + tokenizer + ML utilities
    ("anthropic",       "anthropic",     "core",         "Anthropic API client"),
    ("litellm",         "litellm",       "core",         "unified provider seam (primary)"),
    ("tiktoken",        "tiktoken",      "core",         "token counting"),
    ("scikit-learn",    "sklearn",       "core",         "ML utilities"),
    # Analysis
    ("datasets",        "datasets",      "analysis",     "HuggingFace Datasets"),
    ("pandas",          "pandas",        "analysis",     "DataFrame"),
    ("numpy",           "numpy",         "analysis",     "arrays"),
    ("pyarrow",         "pyarrow",       "analysis",     "columnar data / Parquet"),
    ("scipy",           "scipy",         "analysis",     "statistical tests"),
    ("statsmodels",     "statsmodels",   "analysis",     "regression / inference"),
    # Eval (torch-free; bert-score + lm-eval require torch → Tier-2 [local])
    ("inspect-ai",      "inspect_ai",    "eval",         "inspect-ai evaluation framework"),
    ("evaluate",        "evaluate",      "eval",         "HuggingFace Evaluate"),
    ("sacrebleu",       "sacrebleu",     "eval",         "BLEU / chrF scores"),
    ("rouge-score",     "rouge_score",   "eval",         "ROUGE scores"),
    # Multilingual
    ("sentencepiece",   "sentencepiece", "multilingual", "SentencePiece tokenizer"),
    ("sacremoses",      "sacremoses",    "multilingual", "Moses tokenizer / detokenizer"),
    ("langdetect",      "langdetect",    "multilingual", "language detection"),
    # Integrations (pip-installable)
    ("wandb",           "wandb",         "integrations", "W&B experiment tracking"),
    ("pyzotero",        "pyzotero",      "integrations", "Zotero citation management"),
    ("keyring",         "keyring",       "integrations", "secret-store adapter (API key resolution)"),
    # Utilities / harness
    ("tenacity",        "tenacity",      "utils",        "retry logic"),
    ("tqdm",            "tqdm",          "utils",        "progress bars"),
    ("orjson",          "orjson",        "utils",        "fast JSON"),
    ("pydantic",        "pydantic",      "utils",        "data validation"),
    ("jinja2",          "jinja2",        "utils",        "templating"),
    ("rich",            "rich",          "utils",        "terminal formatting"),
    ("python-dotenv",   "dotenv",        "utils",        ".env loading"),
]
# Note: asta is reported as an optional integration via _check_asta() — not in _TIER1_PACKAGES
# because it may not be available on PyPI. rv check surfaces it in the Integrations section.

_TIER2_PACKAGES: list[tuple[str, str, str, str]] = [
    ("torch",          "torch",          "local", "PyTorch (GPU)"),
    ("transformers",   "transformers",   "local", "HuggingFace Transformers"),
    ("accelerate",     "accelerate",     "local", "multi-GPU training"),
    ("huggingface_hub","huggingface_hub","local", "HuggingFace Hub client"),
    ("fasttext",       "fasttext",       "local", "FastText embeddings"),
    ("lm-eval",        "lm_eval",        "local", "lm-evaluation-harness (requires torch)"),
    ("bert-score",     "bert_score",     "local", "BERTScore (requires torch)"),
    ("vllm",           "vllm",           "serve", "vLLM serving (GPU)"),
    ("sglang",         "sglang",         "serve", "SGLang serving (GPU)"),
]


def _probe_import(import_name: str) -> bool:
    """Return True if the module is importable. Never raises."""
    import importlib
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False
    except Exception:
        # Some packages raise non-ImportError on import (e.g. CUDA init failures).
        return False


def _check_tier1() -> list[tuple[str, str, str, bool]]:
    """Probe all Tier-1 packages. Returns list of (pip_name, purpose, group, ok)."""
    results = []
    for pip_name, import_name, group, purpose in _TIER1_PACKAGES:
        ok = _probe_import(import_name)
        results.append((pip_name, purpose, group, ok))
    return results


def _check_tier2() -> list[tuple[str, str, str, bool]]:
    """Probe all Tier-2 packages. Returns list of (pip_name, purpose, group, ok)."""
    results = []
    for pip_name, import_name, group, purpose in _TIER2_PACKAGES:
        ok = _probe_import(import_name)
        results.append((pip_name, purpose, group, ok))
    return results


def _fmt_tier_section(
    results: list[tuple[str, str, str, bool]],
    warn_missing: bool = False,
) -> tuple[list[str], list[str]]:
    """Format a tier section for the report. Returns (lines, missing_pip_names)."""
    from collections import defaultdict

    lines: list[str] = []
    missing: list[str] = []

    groups: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
    for pip_name, purpose, group, ok in results:
        groups[group].append((pip_name, purpose, ok))

    for group_name, items in groups.items():
        ok_count = sum(1 for _, _, ok in items if ok)
        total = len(items)
        group_ok = ok_count == total
        if group_ok:
            group_tag = "OK"
        elif warn_missing:
            group_tag = "WARN"
        else:
            group_tag = "MISS"
        lines.append(f"  [{group_tag}] {group_name}: {ok_count}/{total}")
        for pip_name, purpose, ok in items:
            tag = "+" if ok else "-"
            lines.append(f"         {tag} {pip_name}  ({purpose})")
            if not ok:
                missing.append(pip_name)

    return lines, missing


# ---------------------------------------------------------------------------
# Required checks (carried over from SR-5)
# ---------------------------------------------------------------------------

def _check_claude_cli() -> tuple[bool, str]:
    """Return (ok, message) for the Claude CLI check."""
    claude_path = shutil.which("claude")
    if claude_path:
        return True, f"Claude CLI: found at {claude_path}"
    return False, (
        "Claude CLI: NOT FOUND\n"
        "  Install: https://docs.anthropic.com/en/docs/claude-code\n"
        "  The Claude CLI is the agent runtime — Research Vault cannot dispatch\n"
        "  agents without it."
    )


def _check_api_key() -> tuple[bool, str]:
    """Return (ok, message) for the ANTHROPIC_API_KEY check.

    Resolution order (highest priority first):
      1. ANTHROPIC_API_KEY env var
      2. System keyring: keyring set research_vault ANTHROPIC_API_KEY
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        prefix = key[:8] + "…" if len(key) > 8 else "***"
        return True, f"ANTHROPIC_API_KEY: set via env ({prefix})"

    try:
        import keyring  # type: ignore[import]
        val = keyring.get_password("research_vault", "ANTHROPIC_API_KEY")
        if val:
            return True, "ANTHROPIC_API_KEY: found in system keyring"
    except ImportError:
        pass
    except Exception:
        pass

    return False, (
        "ANTHROPIC_API_KEY: NOT SET\n"
        "  Provision options (pick one):\n"
        "    export ANTHROPIC_API_KEY=sk-ant-…          (env var — session only)\n"
        "    keyring set research_vault ANTHROPIC_API_KEY  (keyring — persists across sessions)\n"
        "  Get a key at: https://console.anthropic.com/\n"
        "  Note: env var takes precedence over keyring when both are set."
    )


def _check_asta() -> tuple[bool, str, bool]:
    """Return (ok, message, required) for the asta check."""
    try:
        import asta  # type: ignore[import]
        return True, "asta: installed", False
    except ImportError:
        return False, (
            "asta: not installed"
            " (optional — enables `rv research find --deep`;"
            " plain `rv research find` works without it)"
        ), False


def _check_wandb() -> tuple[bool, str, bool]:
    """Return (ok, message, required) for the W&B SDK + key check.

    W&B is a documented prerequisite for `rv wandb pull` (SR-WB) — not an optional
    enhancement. Check two things: SDK importable AND WANDB_API_KEY is set.
    If either fails, the W&B feature set is unavailable.
    Not blocking `all_required_ok` (W&B features degrade gracefully for non-W&B workflows).
    """
    try:
        import wandb  # type: ignore[import]
        wandb_ver = getattr(wandb, "__version__", "?")
    except ImportError:
        return False, (
            "wandb SDK: NOT INSTALLED (required for `rv wandb`)\n"
            "  Install: pip install wandb  or  uv add wandb\n"
            "  Get a free account at: https://wandb.ai"
        ), False

    key = os.environ.get("WANDB_API_KEY", "").strip()
    if key:
        prefix = key[:8] + "…" if len(key) > 8 else "***"
        return True, f"wandb: SDK ok (v{wandb_ver}), WANDB_API_KEY set ({prefix})", False

    if not os.environ.get("VAULT_SKIP_KEYRING"):
        try:
            import keyring  # type: ignore[import]
            val = keyring.get_password("research-vault", "wandb-api-key")
            if val:
                return True, f"wandb: SDK ok (v{wandb_ver}), WANDB_API_KEY found in keyring", False
        except ImportError:
            pass
        except Exception:
            pass

    return False, (
        f"wandb: SDK ok (v{wandb_ver}) but WANDB_API_KEY not set\n"
        "  Set via: export WANDB_API_KEY=<your-wandb-api-key>\n"
        "  Get a key at: https://wandb.ai/settings"
    ), False


def _check_observability(cfg: Any = None) -> tuple[bool, str, bool]:
    """Return (ok, message, required) for the SR-MODEL-SEAM observability wiring.

    Reuses the backend's own ``probe()`` (the SSOT) — backend selection + key
    resolution + import wiring, WITHOUT any network call. Reports which backend is
    configured and whether a run now would produce records. Never raises.

    ``required`` is False here; `rv check --require-observability` promotes the
    observability gate into ``all_required_ok`` (the experiment-preflight path).
    """
    try:
        from .config import load_config as _load_config
        _cfg = cfg if cfg is not None else _load_config()
    except Exception as exc:
        return False, f"observability: config error — {exc}", False

    backend_name = str((getattr(_cfg, "observability", {}) or {}).get("backend", "local"))

    try:
        from .adapters.observability import resolve_observability_backend
        backend = resolve_observability_backend(_cfg)
        ok, msg = backend.probe()
    except ValueError as exc:
        # Unknown backend name in config — surface loudly.
        return False, f"observability: {exc}", False
    except Exception as exc:
        return False, f"observability({backend_name}): probe error — {exc}", False

    # backend=none is a deliberate opt-out → OK (not a warning).
    return ok, msg, False


def _check_zotero() -> tuple[bool, str, bool]:
    """Return (ok, message, required) for the Zotero key check."""
    key = os.environ.get("ZOTERO_KEY", "").strip()
    if key:
        return True, "ZOTERO_KEY: set", False

    try:
        import keyring  # type: ignore[import]
        val = keyring.get_password("research_vault", "ZOTERO_KEY")
        if val:
            return True, "ZOTERO_KEY: found in keyring", False
    except ImportError:
        pass
    except Exception:
        pass

    return False, (
        "ZOTERO_KEY: NOT SET (optional)\n"
        "  Set via: export ZOTERO_KEY=<your-zotero-api-key>\n"
        "  Get a key at: https://www.zotero.org/settings/keys\n"
        "  Required for `rv cite` and Zotero-backed literature management."
    ), False


# ---------------------------------------------------------------------------
# Main preflight runner
# ---------------------------------------------------------------------------

def run_preflight(cfg: Any = None, *, require_observability: bool = False) -> dict[str, Any]:
    """Run all preflight checks and return a result dict.

    cfg: optional Config object (accepted for backward compat; no longer used
         for project-integrity checks — the CONTRACT check is removed, SR-LENS-RM).
    require_observability: SR-MODEL-SEAM — when True, the observability wiring check
         is promoted into ``all_required_ok`` (the experiment-preflight gate: refuse
         to green if a run would produce ZERO records).

    Returns:
      {
        "claude_cli":       bool,
        "api_key":          bool,
        "tier1_missing":    list[str],  pip names of missing Tier-1 packages
        "tier2_missing":    list[str],  pip names of missing Tier-2 packages
        "asta":             bool,
        "zotero":           bool,
        "wandb_key":        bool,
        "observability":    bool,       observability wiring ok (probe passed)
        "compute_manifest": bool,
        "all_required_ok":  bool,
        "report":           str,        human-readable multi-line report
      }

    all_required_ok is governed by claude_cli and api_key (+ observability when
    require_observability=True).
    Per-provider SDKs and figure libs are not checked — they are the adopter's own install.
    This is the programmatic entrypoint (used by tests and `rv check`).
    """
    lines: list[str] = ["=== rv check — Research Vault preflight ===", ""]

    # Required checks
    claude_ok, claude_msg = _check_claude_cli()
    apikey_ok, apikey_msg = _check_api_key()

    # Toolkit tier probes
    tier1_results = _check_tier1()
    tier2_results = _check_tier2()

    # Optional integration checks
    asta_ok, asta_msg, _ = _check_asta()
    zotero_ok, zotero_msg, _ = _check_zotero()
    wandb_ok, wandb_msg, _ = _check_wandb()
    obs_ok, obs_msg, _ = _check_observability(cfg)

    all_required = claude_ok and apikey_ok
    if require_observability:
        all_required = all_required and obs_ok

    # Required section
    lines.append("Required:")
    status = "OK" if claude_ok else "FAIL"
    lines.append(f"  [{status}] {claude_msg}")
    status = "OK" if apikey_ok else "FAIL"
    lines.append(f"  [{status}] {apikey_msg}")
    if require_observability:
        status = "OK" if obs_ok else "FAIL"
        lines.append(f"  [{status}] {obs_msg}  (required: --require-observability)")

    # Tier-1 section
    lines.append("")
    lines.append(
        "Toolkit Tier-1 (27-package core — pip install research-vault):"
    )
    tier1_lines, tier1_missing = _fmt_tier_section(tier1_results, warn_missing=False)
    lines.extend(tier1_lines)

    # Tier-2 section
    lines.append("")
    lines.append(
        "Toolkit Tier-2 (GPU-fragile local inference — pip install research-vault[local]):"
    )
    tier2_lines, tier2_missing = _fmt_tier_section(tier2_results, warn_missing=True)
    lines.extend(tier2_lines)
    if tier2_missing:
        lines.append(
            "  [INFO] Tier-2 missing packages need a GPU box — "
            "install on your compute node, not your laptop."
        )

    # Optional integrations section
    lines.append("")
    lines.append("Integrations (keys / API access):")
    status = "OK" if asta_ok else "INFO"
    lines.append(f"  [{status}] {asta_msg}")
    status = "OK" if zotero_ok else "WARN"
    lines.append(f"  [{status}] {zotero_msg}")
    status = "OK" if wandb_ok else "WARN"
    lines.append(f"  [{status}] {wandb_msg}")
    # SR-MODEL-SEAM: observability wiring line (INFO unless --require-observability).
    status = "OK" if obs_ok else ("FAIL" if require_observability else "WARN")
    lines.append(f"  [{status}] {obs_msg}")

    # Compute manifest nudge
    compute_manifest_present = False
    try:
        from .compute import _manifest_path
        from .config import load_config as _load_config
        _cfg = cfg if cfg is not None else _load_config()
        compute_manifest_present = _manifest_path(_cfg).exists()
    except Exception:
        pass

    # Summary
    lines.append("")
    if all_required:
        lines.append("Result: OK — all required prerequisites present.")
        if tier1_missing or not asta_ok or not zotero_ok or not wandb_ok:
            lines.append("  (some optional tools not found — some features limited)")
    else:
        lines.append("Result: FAIL — required prerequisites missing (see FAIL items above).")

    # Note: per-provider SDKs (openai/google-genai/mistralai/cohere) and figure libs
    # (matplotlib/seaborn) are NOT checked — the adopter installs them directly.

    # Nudge: missing Tier-1 → bootstrap
    if tier1_missing:
        lines.append("")
        lines.append(
            f"Tier-1 missing ({len(tier1_missing)} packages). "
            "Run `rv bootstrap` to auto-install:"
        )
        lines.append("  rv bootstrap")
        lines.append(
            "  (best-effort venv install; Tier-1 hard-required, Tier-2 attempted + tolerated)"
        )

    # Nudge: compute manifest
    if not compute_manifest_present:
        lines.append("")
        lines.append(
            "Compute: compute_manifest.json not found — declare your compute environment:\n"
            "  rv compute init   (DECLARE: scaffold manifest with local + remote FILL blocks)\n"
            "  rv doctor         (DISCOVER: probe each declared backend)\n"
            "  rv compute show   (VERIFY: merged declared+discovered recipe)"
        )

    report = "\n".join(lines)

    return {
        "claude_cli": claude_ok,
        "api_key": apikey_ok,
        "tier1_missing": tier1_missing,
        "tier2_missing": tier2_missing,
        "asta": asta_ok,
        "zotero": zotero_ok,
        "wandb_key": wandb_ok,
        "observability": obs_ok,
        "compute_manifest": compute_manifest_present,
        "all_required_ok": all_required,
        "report": report,
    }


# ---------------------------------------------------------------------------
# CLI verb
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``check`` verb.

    When to use: ``rv check`` before running any research loop. Verifies that
    the Claude CLI, API key, and toolkit tiers (Tier-1 27-package core + Tier-1
    extras + Tier-2 GPU) are available. Reports missing packages with install
    instructions. Run `rv bootstrap` if Tier-1 packages are missing.
    Exit 0 if all required prerequisites are present; exit 1 if any are missing.
    """
    desc = (
        "Preflight check — verify Research Vault prerequisites. "
        "Checks: Claude CLI (required), ANTHROPIC_API_KEY (required), "
        "Toolkit Tier-1 (27-package core defaults), "
        "Tier-2 (GPU/local inference — [local] extra), "
        "asta (optional), Zotero/ZOTERO_KEY (optional), W&B (optional). "
        "Per-provider SDKs and figure libs are not checked (adopter installs directly). "
        "Exit 0 if all required prerequisites are present; exit 1 if any are missing. "
        "API keys are resolved from env vars (highest priority) or the system keyring "
        "(e.g. `keyring set research_vault ANTHROPIC_API_KEY`). "
        "Run `rv check` before starting any research loop."
    )
    if parent is not None:
        p = parent.add_parser(
            "check",
            help="Preflight check — verify prerequisites before running research loops.",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv check", description=desc)

    p.add_argument(
        "--require-observability",
        dest="require_observability",
        action="store_true",
        default=False,
        help=(
            "SR-MODEL-SEAM: promote the observability wiring check into the required "
            "gate — exit 1 if the configured backend would produce ZERO records "
            "(missing dep/key). Use in an experiment preflight so a run cannot start "
            "silently un-observed. Use `rv observability probe` for a standalone check."
        ),
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch: rv check."""
    result = run_preflight(
        require_observability=getattr(args, "require_observability", False)
    )
    print(result["report"])
    return 0 if result["all_required_ok"] else 1
