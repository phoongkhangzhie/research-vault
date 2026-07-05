"""adapters/observability.py — SR-MODEL-SEAM: observability backends + emission counter.

When to use: the observability layer for the provided ``ModelClient`` (see
``adapters/model_client.py``). Every model call through the seam is logged
automatically — the harness engineer never hand-wires a logger.

Two planes (see data/doctrine/compute-run-recipe.md — "traces ≠ runs"):
  Plane A (traces) — per-call request/response traces. ``WeaveBackend`` uses W&B
    Weave (``weave.init`` auto-patches ``litellm.completion``). ``LangfuseBackend``
    appends the string callback ``"langfuse"`` to litellm's success/failure lists.
    ``LocalBackend`` (zero-infra default) writes one JSONL line per call to
    ``<state_dir>/llm_calls.jsonl`` via a litellm ``CustomLogger``.
  Plane B (runs) — a classic W&B run readable by ``rv wandb pull``. Owned by the
    ModelClient run-logging path (S6), NOT a backend here. It uses core ``wandb``
    (no new dep) and reads the SAME ``_EmissionCounter`` aggregates this module owns.

``_EmissionCounter`` — a litellm ``CustomLogger`` that is ALWAYS registered on
``litellm.callbacks`` regardless of backend. It increments an event count per call
AND accrues usage/cost/latency from the ``log_success_event`` kwargs. ONE counter
feeds BOTH planes (Plane A trace tags / Plane B run.summary aggregates), and it is
what ``ModelClient.assert_observed()`` reads to catch a silently-broken seam.

IMPORT-LIGHT (charter / SR-PKG): ``litellm`` and ``weave`` are toolkit deps, NOT
stdlib. This module keeps EVERY ``litellm`` / ``weave`` import lazy (inside
functions). The ``CustomLogger`` subclasses are built by factory closures so that
importing this module — which ``load_adapters`` does on the ``rv help`` path — never
pulls in litellm. ``weave`` is imported ONLY when backend == "weave".

Stdlib only at module top.
sr: SR-MODEL-SEAM
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Emission stats — a plain, litellm-free state object
# ---------------------------------------------------------------------------

class EmissionStats:
    """Accrued per-call aggregates — the SSOT both planes read.

    litellm-free by construction (no import of litellm), so ``ModelClient`` can
    query it without touching the toolkit. Fed by the ``_EmissionCounter``
    ``CustomLogger`` (built lazily in ``make_emission_counter``).

    Fields:
      events            — count of litellm callback events (success + failure).
                          This is the "counter" in ``assert_observed``: if the
                          ModelClient made calls but ``events == 0``, the callback
                          never fired → the seam is silently un-observed.
      prompt_tokens     — summed prompt tokens across successful calls.
      completion_tokens — summed completion tokens across successful calls.
      total_cost_usd    — summed litellm-computed response cost (USD).
      latencies_s       — per-call wall latency in seconds (for p50 / p95).
    """

    def __init__(self) -> None:
        self.events: int = 0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self.latencies_s: list[float] = []

    # --- accrual (called by the CustomLogger closure) ---

    def record_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
        *,
        success: bool,
    ) -> None:
        """Accrue one litellm callback event. Never raises (best-effort accrual)."""
        self.events += 1
        # Latency: end - start (datetime or float epoch seconds)
        try:
            self.latencies_s.append(_duration_seconds(start_time, end_time))
        except Exception:
            pass
        if not success:
            return
        # Usage: response_obj.usage.{prompt,completion}_tokens (object or dict)
        try:
            usage = _extract_usage(response_obj)
            self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        except Exception:
            pass
        # Cost: litellm sets kwargs["response_cost"] on success
        try:
            cost = kwargs.get("response_cost")
            if cost is not None:
                self.total_cost_usd += float(cost)
        except Exception:
            pass

    # --- derived aggregates (Plane B run.summary shape) ---

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def latency_percentile(self, pct: float) -> float:
        """Return the pct-percentile latency in seconds (0.0 if no samples)."""
        if not self.latencies_s:
            return 0.0
        ordered = sorted(self.latencies_s)
        # nearest-rank percentile
        k = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
        return ordered[k]

    def as_summary(self) -> dict[str, Any]:
        """The aggregate dict logged to a Plane-B ``run.summary`` (S6)."""
        return {
            "calls": self.events,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "latency_p50_s": round(self.latency_percentile(50), 4),
            "latency_p95_s": round(self.latency_percentile(95), 4),
        }


def _duration_seconds(start_time: Any, end_time: Any) -> float:
    """Return (end - start) in seconds, handling datetime or float epoch."""
    if isinstance(start_time, datetime.datetime) and isinstance(end_time, datetime.datetime):
        return (end_time - start_time).total_seconds()
    return float(end_time) - float(start_time)


def _extract_usage(response_obj: Any) -> dict[str, Any]:
    """Pull a {prompt_tokens, completion_tokens} dict from a litellm response.

    Handles both the ModelResponse object (``.usage`` attr) and a plain dict.
    """
    usage = None
    if response_obj is not None:
        usage = getattr(response_obj, "usage", None)
        if usage is None and isinstance(response_obj, dict):
            usage = response_obj.get("usage")
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
    }


# ---------------------------------------------------------------------------
# _EmissionCounter — built lazily so importing this module never imports litellm
# ---------------------------------------------------------------------------

def make_emission_counter(stats: EmissionStats) -> Any:
    """Build a litellm ``CustomLogger`` that feeds ``stats``. Imports litellm lazily.

    Returns a ``_EmissionCounter`` INSTANCE (a ``CustomLogger`` subclass defined in
    a closure). Registered ALWAYS on ``litellm.callbacks`` by the ModelClient — one
    counter feeds both planes.

    The subclass is defined INSIDE this function (not at module top) so that
    ``import research_vault.adapters.observability`` — which happens on the
    ``rv help`` path — does not import litellm (import-light, SR-PKG).
    """
    from litellm.integrations.custom_logger import CustomLogger  # lazy — toolkit dep

    class _EmissionCounter(CustomLogger):  # type: ignore[misc, valid-type]
        """ALWAYS-registered counter — increments per call + accrues usage/cost/latency."""

        def log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
            stats.record_event(kwargs, response_obj, start_time, end_time, success=True)

        def log_failure_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
            stats.record_event(kwargs, response_obj, start_time, end_time, success=False)

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
            stats.record_event(kwargs, response_obj, start_time, end_time, success=True)

        async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
            stats.record_event(kwargs, response_obj, start_time, end_time, success=False)

    return _EmissionCounter()


def _make_jsonl_logger(jsonl_path: Path) -> Any:
    """Build a litellm ``CustomLogger`` that appends one JSONL line per call.

    Lazy litellm import (import-light). Used by ``LocalBackend`` — the zero-infra
    Plane-A default. Never raises inside the callback (best-effort local trace).
    """
    from litellm.integrations.custom_logger import CustomLogger  # lazy — toolkit dep

    class _LocalJSONLLogger(CustomLogger):  # type: ignore[misc, valid-type]
        def _write(self, kwargs, response_obj, start_time, end_time, status):  # noqa: ANN001
            try:
                usage = _extract_usage(response_obj)
                record = {
                    "ts": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
                    "status": status,
                    "model": kwargs.get("model", ""),
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "response_cost": kwargs.get("response_cost"),
                    "latency_s": _safe_duration(start_time, end_time),
                }
                jsonl_path.parent.mkdir(parents=True, exist_ok=True)
                with open(jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            except Exception:
                pass  # best-effort local trace — never break a real call

        def log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
            self._write(kwargs, response_obj, start_time, end_time, "success")

        def log_failure_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
            self._write(kwargs, response_obj, start_time, end_time, "failure")

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
            self._write(kwargs, response_obj, start_time, end_time, "success")

        async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
            self._write(kwargs, response_obj, start_time, end_time, "failure")

    return _LocalJSONLLogger()


def _safe_duration(start_time: Any, end_time: Any) -> float | None:
    try:
        return _duration_seconds(start_time, end_time)
    except Exception:
        return None


def _register_callback(cb: Any) -> None:
    """Append a CustomLogger instance to ``litellm.callbacks`` (idempotent-ish).

    Lazy litellm import. Safe to call more than once with distinct instances.
    """
    import litellm  # lazy — toolkit dep
    if not hasattr(litellm, "callbacks") or litellm.callbacks is None:
        litellm.callbacks = []
    litellm.callbacks.append(cb)


# ---------------------------------------------------------------------------
# ObservabilityBackend protocol + implementations
# ---------------------------------------------------------------------------

@runtime_checkable
class ObservabilityBackend(Protocol):
    """A Plane-A trace destination for the model seam.

    ``probe()`` — a rejects-only wiring check: returns ``(ok, message)`` WITHOUT
      making any network call. ok=False means "if you run now you produce zero
      records" (missing dep, unresolved key). Never raises.
    ``start()`` — arm the backend (register callbacks / weave.init). Called ONCE
      by ``ModelClient``. Idempotence is the ModelClient's responsibility.
    """

    name: str

    def probe(self) -> tuple[bool, str]:
        ...

    def start(self) -> None:
        ...


class NoneBackend:
    """No Plane-A tracing. The ``_EmissionCounter`` is still registered by the seam,
    but ``assert_observed`` does NOT warn for backend == "none"."""

    name = "none"

    def probe(self) -> tuple[bool, str]:
        return True, "observability: disabled (backend=none)"

    def start(self) -> None:
        return None


class LocalBackend:
    """Zero-infra default — one JSONL line per call at ``<state_dir>/llm_calls.jsonl``.

    Registers a litellm ``CustomLogger`` (built lazily) on ``litellm.callbacks``.
    """

    name = "local"

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = Path(state_dir)
        self.jsonl_path = self._state_dir / "llm_calls.jsonl"
        self._logger: Any = None

    def probe(self) -> tuple[bool, str]:
        # Needs litellm (for the CustomLogger) + a writable state dir.
        try:
            import litellm  # noqa: F401 — presence check only
        except Exception:
            return False, (
                "observability(local): litellm not importable — "
                "install the core deps (pip install research-vault)."
            )
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, f"observability(local): state_dir not writable — {exc}"
        return True, f"observability: local JSONL → {self.jsonl_path}"

    def start(self) -> None:
        self._logger = _make_jsonl_logger(self.jsonl_path)
        _register_callback(self._logger)


class WeaveBackend:
    """Plane-A traces via W&B Weave — ``weave.init`` auto-patches ``litellm.completion``.

    ``weave`` is an OPT-IN extra ([observability]); imported ONLY here, ONLY when
    backend == "weave". Auth via ``WANDB_API_KEY`` (resolved by the SecretStore into
    env before start).
    """

    name = "weave"

    def __init__(self, project: str, *, key_present: bool = False) -> None:
        # ``project`` is the weave.init target: "entity/project" or bare "project".
        self.project = project
        self._key_present = key_present

    def probe(self) -> tuple[bool, str]:
        try:
            import weave  # noqa: F401 — lazy, opt-in extra
        except Exception:
            return False, (
                "observability(weave): `weave` not installed — "
                "pip install research-vault[observability]. "
                "A run now would produce ZERO Plane-A traces."
            )
        if not self.project.strip():
            return False, (
                "observability(weave): no project configured — set "
                "[observability].wandb_project or the compute manifest results.wandb block."
            )
        if not self._key_present and not os.environ.get("WANDB_API_KEY", "").strip():
            return False, (
                "observability(weave): WANDB_API_KEY not resolvable — "
                "set it in env/keyring. A run now would produce ZERO Plane-A traces."
            )
        return True, f"observability: weave traces → {self.project}"

    def start(self) -> None:
        import weave  # lazy — opt-in extra; imported ONLY when backend=weave
        weave.init(self.project)


class LangfuseBackend:
    """Plane-A traces via Langfuse — appends the string callback ``"langfuse"`` to
    litellm's success + failure callback lists. ``langfuse`` is the adopter's own
    install (never shipped)."""

    name = "langfuse"

    def probe(self) -> tuple[bool, str]:
        try:
            import langfuse  # noqa: F401 — adopter's own install
        except Exception:
            return False, (
                "observability(langfuse): `langfuse` not installed (adopter's own "
                "install: pip install langfuse). A run now would produce ZERO traces."
            )
        pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
        sec = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
        if not (pub and sec):
            return False, (
                "observability(langfuse): LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY "
                "not set. A run now would produce ZERO Plane-A traces."
            )
        return True, "observability: langfuse traces (litellm success/failure_callback)"

    def start(self) -> None:
        import litellm  # lazy — toolkit dep
        for attr in ("success_callback", "failure_callback"):
            lst = getattr(litellm, attr, None)
            if lst is None:
                lst = []
                setattr(litellm, attr, lst)
            if "langfuse" not in lst:
                lst.append("langfuse")


# ---------------------------------------------------------------------------
# Config resolution + factory
# ---------------------------------------------------------------------------

_KNOWN_BACKENDS = ("local", "weave", "langfuse", "none")


def resolve_observability_backend(cfg: Any, *, key_present: bool = False) -> ObservabilityBackend:
    """Build the configured ObservabilityBackend from ``cfg.observability``.

    Selection is driven by ``[observability].backend`` (default "local"):
      backend = "local"    → LocalBackend(cfg.state_dir)   [zero-infra default]
      backend = "weave"    → WeaveBackend(<project>)       [needs [observability] extra]
      backend = "langfuse" → LangfuseBackend()             [adopter's own install]
      backend = "none"     → NoneBackend()

    ``key_present`` tells WeaveBackend that the SecretStore resolved WANDB_API_KEY
    (so ``probe`` can green before it is exported into env). Unknown backend names
    raise ValueError with the known options.

    Never imports litellm/weave — construction is pure. The heavy import happens
    only in ``backend.start()``.
    """
    obs = getattr(cfg, "observability", None) or {}
    backend_name = str(obs.get("backend", "local")).strip() or "local"

    if backend_name == "none":
        return NoneBackend()
    if backend_name == "local":
        return LocalBackend(cfg.state_dir)
    if backend_name == "langfuse":
        return LangfuseBackend()
    if backend_name == "weave":
        project = _resolve_weave_project(cfg)
        return WeaveBackend(project, key_present=key_present)

    known = ", ".join(_KNOWN_BACKENDS)
    raise ValueError(
        f"Unknown observability backend {backend_name!r}. Known: {known}"
    )


def resolve_run_logging_target(cfg: Any) -> tuple[bool, str, str]:
    """Resolve the Plane-B (classic W&B run) logging target.

    Returns (enabled, entity, project):
      enabled — ``[observability].run_logging`` is True.
      entity / project — from [observability].wandb_project ("entity/project" or bare
        "project"), else the compute manifest results.wandb block (the entity/project
        SSOT, via wandb_pull's resolver). Either may be "" when unresolved.
    Never imports wandb — pure config resolution.
    """
    obs = getattr(cfg, "observability", None) or {}
    enabled = bool(obs.get("run_logging", False))
    explicit = str(obs.get("wandb_project", "")).strip()
    entity, project = "", ""
    if explicit:
        if "/" in explicit:
            entity, project = explicit.split("/", 1)
        else:
            project = explicit
    else:
        try:
            from ..wandb_pull import _resolve_wandb_from_manifest
            entity, project = _resolve_wandb_from_manifest(cfg)
        except Exception:
            entity, project = "", ""
    return enabled, entity, project


def probe_run_logging(cfg: Any) -> tuple[bool, str]:
    """Rejects-only probe for Plane-B run logging. Returns (ok, message). No network.

    ok=True means: run_logging disabled (nothing to check) OR wandb importable + a
    project resolvable + WANDB_API_KEY present. ok=False surfaces the specific gap
    (would produce no ``rv wandb pull``-able run). Never raises.
    """
    enabled, entity, project = resolve_run_logging_target(cfg)
    if not enabled:
        return True, "run-logging (Plane B): disabled ([observability].run_logging=false)"
    try:
        import wandb  # noqa: F401 — core dep, but guard anyway (import-light stance)
    except Exception:
        return False, (
            "run-logging (Plane B): `wandb` not importable — install core deps. "
            "A run now would produce NO rv wandb pull-able run."
        )
    if not project.strip():
        return False, (
            "run-logging (Plane B): no W&B project resolvable — set "
            "[observability].wandb_project or the compute manifest results.wandb block."
        )
    if not os.environ.get("WANDB_API_KEY", "").strip():
        return False, (
            "run-logging (Plane B): WANDB_API_KEY not set — a run now would fail to "
            "log a classic run (rv wandb pull would have nothing to read)."
        )
    target = f"{entity}/{project}" if entity else project
    return True, f"run-logging (Plane B): classic W&B run → {target}"


def _resolve_weave_project(cfg: Any) -> str:
    """Resolve the weave.init project target for the weave backend.

    Precedence: [observability].wandb_project (explicit) → the compute manifest
    results.wandb block (entity/project SSOT, reused via wandb_pull's resolver).
    Returns "entity/project" when both are known, else the bare project, else "".
    """
    obs = getattr(cfg, "observability", None) or {}
    explicit = str(obs.get("wandb_project", "")).strip()
    if explicit:
        return explicit
    # Reuse the SSOT resolver — never re-derive the manifest shape here.
    try:
        from ..wandb_pull import _resolve_wandb_from_manifest
        entity, project = _resolve_wandb_from_manifest(cfg)
        if entity and project:
            return f"{entity}/{project}"
        return project or ""
    except Exception:
        return ""
