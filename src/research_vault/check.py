"""check.py — `rv check` — preflight prerequisite check.

When to use: ``rv check`` before starting any research loop. Verifies that
all prerequisites are available and reports missing items with clear install
instructions. Fail-fast: reports ALL failures, not just the first.

Checks:
  1. Claude CLI — ``claude --version`` must succeed (the agent runtime)
  2. ANTHROPIC_API_KEY — must be set in env or resolvable via keyring
  3. Toolkit Tier-1 — 28-package research-toolkit core (installed by default)
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

from .keys import (
    CLASS_FEATURE_REQUIRED,
    CLASS_REQUIRED,
    FEATURES,
    PROVIDER_KEYS,
    WANDB_KEY,
    ZOTERO_KEY,
    resolve_any,
    resolve_key,
)


# ---------------------------------------------------------------------------
# Tier-1 package registry — 28-package research-toolkit core
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
    ("weave",           "weave",         "integrations", "W&B Weave Plane-A traces (SR-MODEL-SEAM)"),
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
    """Return (ok, message) for the provider-API-key check (provider-PLURAL).

    F3: a provider key is NOT required — it is FEATURE-REQUIRED.  A missing one
    means "API-model experiments locked", never a FAIL.  ANY one provider key
    (Anthropic, OpenAI, …) satisfies the capability.

    F4: resolution routes through the registry SSOT (env var → unified keyring
    service), so a key written by `rv onboard` is seen here.
    """
    present, hits = resolve_any(PROVIDER_KEYS)
    if present:
        spec, source, masked = hits[0]
        others = "" if len(hits) == 1 else f" (+{len(hits) - 1} more)"
        return True, f"provider API key: {spec.label} set via {source} ({masked}){others}"

    urls = "; ".join(f"{k.label} → {k.request_url}" for k in PROVIDER_KEYS)
    return False, (
        "provider API key: none set — API-model experiments locked until you add one\n"
        f"  Add via `rv onboard`, or set an env var (e.g. export ANTHROPIC_API_KEY=sk-ant-…),\n"
        f"  or keyring. Request a key: {urls}\n"
        "  Skippable if you run local models or lit-review only."
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
            "wandb SDK: NOT INSTALLED (needed for `rv wandb pull`)\n"
            "  Install: pip install wandb  or  uv add wandb\n"
            "  Get a free account at: https://wandb.ai"
        ), False

    # F4: resolve WANDB_API_KEY through the registry SSOT (env → unified keyring).
    present, source, masked = resolve_key(WANDB_KEY)
    if present:
        return True, f"wandb: SDK ok (v{wandb_ver}), WANDB_API_KEY set via {source} ({masked})", False

    return False, (
        f"wandb: SDK ok (v{wandb_ver}) but WANDB_API_KEY not set — "
        "experiment observability + `rv wandb pull` locked until you add the key\n"
        "  Add via `rv onboard`, or: export WANDB_API_KEY=<your-wandb-api-key>\n"
        f"  Get a key at: {WANDB_KEY.request_url}"
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
    """Return (ok, message, required) for the Zotero key check.

    F4: resolves through the registry SSOT (env → unified keyring), so a key
    written by `rv onboard` is seen here.  Never required — one framing: needed
    for `rv cite`, locked until you add it.
    """
    present, source, masked = resolve_key(ZOTERO_KEY)
    if present:
        return True, f"zotero: ZOTERO_KEY set via {source} ({masked})", False

    return False, (
        "zotero: ZOTERO_KEY not set — needed for `rv cite`, locked until you add it\n"
        "  Add via `rv onboard`, or: export ZOTERO_KEY=<your-zotero-api-key>\n"
        f"  Get a key at: {ZOTERO_KEY.request_url}"
    ), False


# ---------------------------------------------------------------------------
# Feature status builder (the FEATURE-REQUIRED catalog — F2/F3)
# ---------------------------------------------------------------------------

def _compute_manifest_present(cfg: Any = None) -> bool:
    """Return True if a compute_manifest.json exists for this instance."""
    try:
        from .compute import _manifest_path
        from .config import load_config as _load_config
        _cfg = cfg if cfg is not None else _load_config()
        return bool(_manifest_path(_cfg).exists())
    except Exception:
        return False


def _feature_status(feature: Any, *, manifest_present: bool) -> dict[str, Any]:
    """Resolve a single Feature to a structured status dict.

    Returns:
      {
        "id", "title", "class", "unlocks", "kind",
        "status":  "unlocked" | "locked",
        "source":  "env" | "keyring" | "package" | "manifest" | "",
        "detail":  short human string (masked prefix / version / "" ),
        "urls":    [{"label", "url"}, ...]  (request forms for a locked feature),
        "note":    extra caveat (e.g. asta institutional-email),
        "handoff_cmd": str,  (compute only)
      }
    """
    urls = [{"label": k.label, "url": k.request_url} for k in feature.keys]
    if feature.request_url and not urls:
        urls = [{"label": feature.title, "url": feature.request_url}]

    status = "locked"
    source = ""
    detail = ""

    if feature.kind == "key":
        present, hits = resolve_any(feature.keys)
        if present:
            status = "unlocked"
            spec, source, masked = hits[0]
            extra = "" if len(hits) == 1 else f" (+{len(hits) - 1} more)"
            detail = f"{spec.label} ({masked}){extra}"
    elif feature.kind == "package":
        if _probe_import(feature.import_name):
            status, source = "unlocked", "package"
            detail = "installed"
    elif feature.kind == "handoff":
        if manifest_present:
            status, source = "unlocked", "manifest"
            detail = "compute_manifest.json present"

    return {
        "id": feature.id,
        "title": feature.title,
        "class": feature.cls,
        "unlocks": feature.unlocks,
        "kind": feature.kind,
        "status": status,
        "source": source,
        "detail": detail,
        "urls": urls,
        "note": feature.note,
        "handoff_cmd": feature.handoff_cmd,
    }


def build_features(cfg: Any = None) -> list[dict[str, Any]]:
    """Build the structured FEATURE-REQUIRED status list from the registry catalog.

    Shared by ``rv check`` (read), the rich renderer, and ``rv onboard`` (which
    re-derives its idempotent skip-state from these statuses — no state file).
    """
    manifest_present = _compute_manifest_present(cfg)
    return [_feature_status(f, manifest_present=manifest_present) for f in FEATURES]


# ---------------------------------------------------------------------------
# Main preflight runner
# ---------------------------------------------------------------------------

def run_preflight(cfg: Any = None, *, require_observability: bool = False) -> dict[str, Any]:
    """Run all preflight checks and return a result dict.

    The corrected required-model (F3): the agent runtime (Claude CLI) is the ONLY
    hard-REQUIRED item.  There is NO required API key.  A fresh adopter with the
    runtime present and ZERO keys → ``all_required_ok = True`` (exit 0); every
    feature key is FEATURE-REQUIRED and shows "locked", never FAIL.

    cfg: optional Config object (accepted for backward compat).
    require_observability: SR-MODEL-SEAM — when True, the observability wiring
         check is promoted into ``all_required_ok`` (the experiment-preflight gate).

    Returns (contract stable — tests assert on the dict, not rendered output):
      {
        "claude_cli":       bool,       the runtime (the ONLY hard requirement)
        "runtime":          bool,       alias of claude_cli (clearer name)
        "api_key":          bool,       ANY provider key present (feature, not required)
        "tier1_missing":    list[str],
        "tier2_missing":    list[str],
        "asta":             bool,
        "zotero":           bool,
        "wandb_key":        bool,
        "observability":    bool,
        "compute_manifest": bool,
        "features":         list[dict],  the FEATURE-REQUIRED catalog statuses (F2/F3)
        "required_failed":  list[str],   REQUIRED items that failed (F1 — culprits inline)
        "all_required_ok":  bool,        governed ONLY by the runtime (+ obs when required)
        "report":           str,         human-readable plain-text report
      }
    """
    lines: list[str] = ["=== rv check — Research Vault preflight ===", ""]

    # Hard-required check: the runtime ONLY.
    claude_ok, claude_msg = _check_claude_cli()
    # Provider key is a FEATURE, not a requirement — resolved for the api_key field.
    apikey_ok, apikey_msg = _check_api_key()

    # Toolkit tier probes
    tier1_results = _check_tier1()
    tier2_results = _check_tier2()

    # Feature-required catalog (provider / s2 / asta / wandb / zotero / compute).
    features = build_features(cfg)
    compute_manifest_present = _compute_manifest_present(cfg)

    # Individual integration checks (for the back-compat dict fields + report text).
    asta_ok, asta_msg, _ = _check_asta()
    zotero_ok, zotero_msg, _ = _check_zotero()
    wandb_ok, wandb_msg, _ = _check_wandb()
    obs_ok, obs_msg, _ = _check_observability(cfg)

    # F3: all_required_ok gates ONLY on the runtime (+ observability when required).
    required_failed: list[str] = []
    if not claude_ok:
        required_failed.append("agent runtime (Claude CLI)")
    if require_observability and not obs_ok:
        required_failed.append("observability wiring")
    all_required = len(required_failed) == 0

    # ── Required section (runtime only) ──────────────────────────────────────
    lines.append("Required:")
    status = "OK" if claude_ok else "FAIL"
    lines.append(f"  [{status}] {claude_msg}")
    lines.append(
        "         (the agent runtime is the ONLY hard requirement — no API key is required)"
    )
    if require_observability:
        status = "OK" if obs_ok else "FAIL"
        lines.append(f"  [{status}] {obs_msg}  (required: --require-observability)")

    # ── Tier-1 section ───────────────────────────────────────────────────────
    lines.append("")
    lines.append(
        "Toolkit Tier-1 (28-package core — pip install research-vault):"
    )
    tier1_lines, tier1_missing = _fmt_tier_section(tier1_results, warn_missing=False)
    lines.extend(tier1_lines)

    # ── Tier-2 section ───────────────────────────────────────────────────────
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

    # ── Feature-required integrations (one framing: locked-until-you-add) ─────
    lines.append("")
    lines.append("Integrations (keys / API access) — each FEATURE-REQUIRED (locked until you add it):")
    for feat in features:
        tag = "OK" if feat["status"] == "unlocked" else "LOCKED"
        detail = f" — {feat['detail']}" if feat["detail"] else ""
        lines.append(f"  [{tag}] {feat['title']}: unlocks {feat['unlocks']}{detail}")
        if feat["status"] == "locked":
            for u in feat["urls"]:
                lines.append(f"         request: {u['label']} → {u['url']}")
            if feat["handoff_cmd"]:
                lines.append(f"         run: {feat['handoff_cmd']}")
            if feat["note"]:
                lines.append(f"         note: {feat['note']}")
    # SR-MODEL-SEAM: observability wiring line (INFO unless --require-observability).
    status = "OK" if obs_ok else ("FAIL" if require_observability else "INFO")
    lines.append(f"  [{status}] observability: {obs_msg}")

    # ── Summary (F1: culprits travel inline) ─────────────────────────────────
    lines.append("")
    if all_required:
        lines.append("Result: OK — the agent runtime is present (the only hard requirement).")
        locked = [f["title"] for f in features if f["status"] == "locked"]
        if locked:
            lines.append(
                f"  ({len(locked)} feature(s) locked: {', '.join(locked)} — "
                "add keys via `rv onboard` to unlock)"
            )
    else:
        lines.append(
            "Result: FAIL — required prerequisite missing: "
            + ", ".join(required_failed)
        )

    # Nudge: any locked feature → point at rv onboard.
    if any(f["status"] == "locked" for f in features):
        lines.append("")
        lines.append(
            "Locked capabilities above are optional — run `rv onboard` for a guided, "
            "idempotent setup (adds keys to your keyring; never writes plaintext)."
        )

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
        "runtime": claude_ok,
        "api_key": apikey_ok,
        "tier1_missing": tier1_missing,
        "tier2_missing": tier2_missing,
        "asta": asta_ok,
        "zotero": zotero_ok,
        "wandb_key": wandb_ok,
        "observability": obs_ok,
        "compute_manifest": compute_manifest_present,
        "features": features,
        "required_failed": required_failed,
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
    the Claude CLI, API key, and toolkit tiers (Tier-1 28-package core + Tier-1
    extras + Tier-2 GPU) are available. Reports missing packages with install
    instructions. Run `rv bootstrap` if Tier-1 packages are missing.
    Exit 0 if all required prerequisites are present; exit 1 if any are missing.
    """
    desc = (
        "Preflight check — verify Research Vault prerequisites. "
        "Checks: Claude CLI (required), ANTHROPIC_API_KEY (required), "
        "Toolkit Tier-1 (28-package core defaults), "
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
