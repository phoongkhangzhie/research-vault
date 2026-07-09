# SPDX-License-Identifier: AGPL-3.0-or-later
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

IMPORT-LIGHT (charter / SR-PKG): ``litellm`` and ``weave`` are core deps that MUST
stay lazy. This module keeps EVERY ``litellm`` / ``weave`` import inside functions.
The ``CustomLogger`` subclasses are built by factory closures so that importing this
module — which ``load_adapters`` does on the ``rv help`` path — never pulls in
litellm at module top-level. ``weave`` (core since SR-MODEL-SEAM) is imported ONLY
when backend == "weave".

Stdlib only at module top.
sr: SR-MODEL-SEAM
"""
from __future__ import annotations

import datetime
import json
import os
import threading
import warnings
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


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
# Per-completion de-dup — litellm fires a callback TWICE for one completion
# ---------------------------------------------------------------------------

def make_call_deduper() -> Callable[[Any], bool]:
    """Return ``already_seen(kwargs) -> bool``, thread-safe, keyed on the completion id.

    Why: for a SINGLE ``litellm.completion`` litellm dispatches the success callback
    TWICE — once via the sync ``success_handler`` (submitted to a background
    ``ThreadPoolExecutor``) and once via the async ``async_success_handler``
    (providers whose sync SDK call rides an async HTTP path fire both;
    ``should_run_logging`` gates them under SEPARATE ``sync_success`` /
    ``async_success`` flags, so both run). A naive CustomLogger therefore counts /
    logs one completion twice (the double-count defect: ``events == 2`` and two
    JSONL lines for one call).

    The dedupe keys on litellm's ``litellm_call_id`` (present in the callback
    ``kwargs`` == ``model_call_details``, and IDENTICAL across the sync + async
    fire of the same completion). The two fires happen on DIFFERENT threads (the
    executor thread and the async-loop thread), so the check-and-add is guarded by
    a lock to avoid a check/check/add/add race double-counting.

    A missing ``litellm_call_id`` (unusual — some fakes / very old litellm) is
    treated as "not seen" so the event is still counted (fail-open: never silently
    drop a real event).
    """
    seen: set[str] = set()
    lock = threading.Lock()

    def already_seen(kwargs: Any) -> bool:
        cid = None
        try:
            cid = kwargs.get("litellm_call_id") if hasattr(kwargs, "get") else None
        except Exception:
            cid = None
        if cid is None:
            return False  # can't dedupe — count it (fail-open)
        with lock:
            if cid in seen:
                return True
            seen.add(cid)
            return False

    return already_seen


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

    already_seen = make_call_deduper()  # one completion == one counted event

    class _EmissionCounter(CustomLogger):  # type: ignore[misc, valid-type]
        """ALWAYS-registered counter — increments per call + accrues usage/cost/latency.

        De-duped per completion (``litellm_call_id``): litellm fires the success
        callback on both the sync executor thread AND the async loop, so a naive
        counter would double-count. The FIRST fire (sync or async) counts; the
        second is skipped.
        """

        def log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
            if already_seen(kwargs):
                return
            stats.record_event(kwargs, response_obj, start_time, end_time, success=True)

        def log_failure_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
            if already_seen(kwargs):
                return
            stats.record_event(kwargs, response_obj, start_time, end_time, success=False)

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
            if already_seen(kwargs):
                return
            stats.record_event(kwargs, response_obj, start_time, end_time, success=True)

        async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
            if already_seen(kwargs):
                return
            stats.record_event(kwargs, response_obj, start_time, end_time, success=False)

    return _EmissionCounter()


def _make_jsonl_logger(jsonl_path: Path) -> Any:
    """Build a litellm ``CustomLogger`` that appends one JSONL line per call.

    Lazy litellm import (import-light). Used by ``LocalBackend`` — the zero-infra
    Plane-A default. Never raises inside the callback (best-effort local trace).
    """
    from litellm.integrations.custom_logger import CustomLogger  # lazy — toolkit dep

    already_seen = make_call_deduper()  # one completion == one JSONL line

    class _LocalJSONLLogger(CustomLogger):  # type: ignore[misc, valid-type]
        def _write(self, kwargs, response_obj, start_time, end_time, status):  # noqa: ANN001
            if already_seen(kwargs):
                return  # litellm's sync + async fire → one line, not two
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


_WANDB_ILLEGAL_CHARS = frozenset(" /\\#?%:")


def _warn_if_wandb_unsafe(project: str) -> None:
    """Loud warn (charter §2) on a W&B-illegal char — never silently sanitize (D4)."""
    if project and any(ch in _WANDB_ILLEGAL_CHARS for ch in project):
        warnings.warn(
            f"resolve_run_logging_target: resolved W&B project {project!r} contains "
            "a character W&B is likely to reject (space, '/', '\\\\', '#', '?', '%', "
            "':'). Set [observability].wandb_project explicitly to override the "
            "auto-slug default with a W&B-safe name.",
            UserWarning,
            stacklevel=3,
        )


def resolve_run_logging_target(
    cfg: Any, project_slug: str | None = None
) -> tuple[bool, str, str]:
    """Resolve the Plane-B (classic W&B run) logging target.

    Returns (enabled, entity, project):
      enabled — ``[observability].run_logging`` is True.

      entity — resolved independently of project, precedence:
        [observability].wandb_project entity-part (``entity/project``) → ``WANDB_ENTITY``
        env → compute manifest ``results.wandb.entity`` (account-level, verbatim).

      project — decoupled from entity, precedence:
        [observability].wandb_project project-part → ``WANDB_PROJECT`` env →
        ``project_slug`` (the calling project's slug — the new per-project default) →
        compute manifest ``results.wandb.project`` (legacy last-resort fallback, kept
        for existing manifests that still declare a static instance-wide project).

      Either may be "" when unresolved. A resolved project containing a W&B-illegal
      character (space, /, \\, #, ?, %, :) triggers a loud UserWarning — never a
      silent sanitize (charter §2 / D4).

    Never imports wandb — pure config resolution.
    """
    obs = getattr(cfg, "observability", None) or {}
    enabled = bool(obs.get("run_logging", False))

    explicit = str(obs.get("wandb_project", "")).strip()
    explicit_entity, explicit_project = "", ""
    if explicit:
        if "/" in explicit:
            explicit_entity, explicit_project = explicit.split("/", 1)
        else:
            explicit_project = explicit

    env_entity = os.environ.get("WANDB_ENTITY", "").strip()
    env_project = os.environ.get("WANDB_PROJECT", "").strip()

    try:
        from ..wandb_pull import _resolve_wandb_from_manifest
        manifest_entity, manifest_project = _resolve_wandb_from_manifest(cfg)
    except Exception:
        manifest_entity, manifest_project = "", ""

    entity = explicit_entity or env_entity or manifest_entity
    project = (
        explicit_project
        or env_project
        or (project_slug or "").strip()
        or manifest_project
    )

    _warn_if_wandb_unsafe(project)
    return enabled, entity, project


def probe_run_logging(cfg: Any, project_slug: str | None = None) -> tuple[bool, str]:
    """Rejects-only probe for Plane-B run logging. Returns (ok, message). No network.

    ok=True means: run_logging disabled (nothing to check) OR wandb importable +
    WANDB_API_KEY present. A run's project defaults to its own slug at call-time
    (``resolve_run_logging_target``'s ``project_slug`` fallback) — so an unresolved
    STATIC project is normal, not a failure; only a genuinely missing dep/key fails
    the probe. Never raises.
    """
    enabled, entity, project = resolve_run_logging_target(cfg, project_slug=project_slug)
    if not enabled:
        return True, "run-logging (Plane B): disabled ([observability].run_logging=false)"
    try:
        import wandb  # noqa: F401 — core dep, but guard anyway (import-light stance)
    except Exception:
        return False, (
            "run-logging (Plane B): `wandb` not importable — install core deps. "
            "A run now would produce NO rv wandb pull-able run."
        )
    if not os.environ.get("WANDB_API_KEY", "").strip():
        return False, (
            "run-logging (Plane B): WANDB_API_KEY not set — a run now would fail to "
            "log a classic run (rv wandb pull would have nothing to read)."
        )
    if project.strip():
        target = f"{entity}/{project}" if entity else project
        return True, f"run-logging (Plane B): classic W&B run → {target}"
    ent_msg = entity or "(default account)"
    return True, (
        "run-logging (Plane B): classic W&B run → entity="
        f"{ent_msg}, project=<per-run slug> (auto — defaults to the run's own project "
        "slug at call-time; set [observability].wandb_project to override)."
    )


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
