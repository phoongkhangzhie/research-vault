"""adapters/model_client.py — SR-MODEL-SEAM: the provided model client.

When to use: a harness NEVER hand-rolls ``anthropic.Anthropic()`` or calls
``litellm.completion`` directly. It reaches the seam:

    from research_vault.adapters import load_adapters
    adapters = load_adapters(cfg)
    resp = adapters.model.complete(
        model="claude-...", messages=[{"role": "user", "content": "..."}]
    )

Why: a hand-rolled client produces ZERO observability records (the P1 failure —
the Haiku experiments logged nothing). ``ModelClient`` makes logging automatic and
UNFORGETTABLE: it resolves provider keys via the SecretStore into env, arms the
configured observability backend ONCE, and registers the always-on
``_EmissionCounter`` — so every ``complete()`` is traced (Plane A) and aggregated
(Plane B) with zero per-call code in the harness.

Reliability contract (charter §2 — surface, never silently drop):
  ``assert_observed()`` (also fired on ``__exit__`` and ``atexit``) catches the
  silently-broken seam: if the backend is not "none" AND calls were made AND the
  emission counter saw ZERO events, the callback pipeline never fired → a LOUD
  warn via the Notifier, and an ``ObservabilityError`` under ``require=True``.

IMPORT-LIGHT: ``litellm`` is imported lazily (inside ``complete`` / the counter
factory). Importing this module does NOT import litellm. ``AdapterSet.model`` is a
LAZY property so ``load_adapters`` never constructs a ModelClient (which would
import litellm and call ``weave.init``) as a side effect.

sr: SR-MODEL-SEAM
"""
from __future__ import annotations

import atexit
import os
import sys
import time
from typing import Any

from .observability import (
    EmissionStats,
    ObservabilityBackend,
    make_emission_counter,
    _register_callback,
)


class ObservabilityError(RuntimeError):
    """Raised when a required observability guarantee is violated (require=True)."""


# How long ``flush`` waits (bounded) for litellm's threaded/async success callbacks
# to fire before reading the emission counter. litellm dispatches the success
# callback on a background ``ThreadPoolExecutor`` (and, for providers whose sync SDK
# call rides an async HTTP path, via the async success handler) — so the counter LAGS
# the synchronous return of ``complete()`` by up to a few seconds. Observed lag under
# a weave-wrapped call: ~3s. 10s is a safe upper bound with headroom; ``flush``
# returns the instant the counter catches up, so the common (healthy) path is fast —
# the bound only bites for a genuinely un-observed call, which then reports loud.
_FLUSH_TIMEOUT_S = 10.0
_FLUSH_POLL_S = 0.02


# Provider (and weave/Plane-B auth) secret-name → env-var. The SecretStore resolves
# each into env so litellm / weave pick them up. Best-effort: an unresolved key is
# skipped (the provider you are not using needs no key). litellm reads only env.
_PROVIDER_KEY_SECRETS: list[tuple[str, str]] = [
    ("anthropic-api-key", "ANTHROPIC_API_KEY"),
    ("openai-api-key",    "OPENAI_API_KEY"),
    ("gemini-api-key",    "GEMINI_API_KEY"),
    ("google-api-key",    "GOOGLE_API_KEY"),
    ("mistral-api-key",   "MISTRAL_API_KEY"),
    ("cohere-api-key",    "COHERE_API_KEY"),
    ("groq-api-key",      "GROQ_API_KEY"),
    ("together-api-key",  "TOGETHER_API_KEY"),
    # weave (Plane A) + classic run (Plane B) auth:
    ("wandb-api-key",     "WANDB_API_KEY"),
]


class ModelClient:
    """The provided model seam. Construct via ``AdapterSet.model`` (lazy).

    __init__ performs the one-time setup: resolve keys into env → probe + start the
    observability backend ONCE → register the always-on emission counter.
    """

    def __init__(
        self,
        cfg: Any,
        secrets: Any,
        observability: ObservabilityBackend,
        notifier: Any = None,
        *,
        require: bool = False,
        flush_timeout_s: float = _FLUSH_TIMEOUT_S,
    ) -> None:
        self._cfg = cfg
        self._secrets = secrets
        self._observability = observability
        self._notifier = notifier
        self._require = require
        # Bound for waiting on litellm's threaded/async callbacks (see flush()).
        self._flush_timeout_s = flush_timeout_s

        self._stats = EmissionStats()
        self._counter: Any = None
        self._completions = 0
        self._started = False
        self._asserted = False

        # 1. Resolve provider keys into env FIRST (so the weave probe sees WANDB_API_KEY).
        self._resolve_keys_into_env()
        # 2. Probe + start the backend ONCE, then register the always-on counter.
        self._start_once()
        # 3. Belt-and-suspenders: assert at interpreter exit even if the caller
        #    forgets the context manager / explicit assert_observed().
        #    Store the handler so __exit__ can unregister it — without unregister,
        #    each ModelClient constructed in a long-lived process accumulates one
        #    atexit entry that holds a strong reference to self until interpreter exit.
        self._atexit_handler = self._atexit_assert
        atexit.register(self._atexit_handler)

    # --- setup ---

    def _resolve_keys_into_env(self) -> None:
        """Resolve provider/auth keys via the SecretStore into env. Never raises."""
        if self._secrets is None:
            return
        for secret_name, env_var in _PROVIDER_KEY_SECRETS:
            if os.environ.get(env_var, "").strip():
                continue  # already in env — leave it (env wins)
            try:
                val = self._secrets.get(secret_name)
            except KeyError:
                continue  # not provisioned — the provider you are not using
            except Exception:
                continue
            if val:
                os.environ[env_var] = val

    def _start_once(self) -> None:
        """Probe + start the backend once; always register the emission counter."""
        if self._started:
            return
        backend_name = getattr(self._observability, "name", "none")

        ok, msg = self._observability.probe()
        if ok:
            try:
                self._observability.start()
            except Exception as exc:  # start failed (e.g. weave.init network/auth)
                fail = (
                    f"observability({backend_name}): start() failed — {exc}. "
                    "A run now would produce ZERO records."
                )
                self._warn(fail)
                if self._require:
                    raise ObservabilityError(fail) from exc
        elif backend_name != "none":
            # Backend wanted but not wired (missing dep/key) — loud, up-front.
            self._warn(msg)
            if self._require:
                raise ObservabilityError(msg)

        # The emission counter is ALWAYS registered (both planes read it, and it is
        # what assert_observed() checks). It works regardless of backend wiring.
        self._counter = make_emission_counter(self._stats)
        _register_callback(self._counter)
        self._started = True

    # --- the seam ---

    def complete(self, model: str, messages: list[dict[str, Any]], **kw: Any) -> Any:
        """Call the model through litellm. Zero per-call logging in the harness.

        Returns the litellm ``ModelResponse``. litellm is imported lazily here so
        importing this module stays litellm-free (import-light).
        """
        self._completions += 1
        import litellm  # lazy — toolkit dep
        return litellm.completion(model=model, messages=messages, **kw)

    # --- reliability ---

    def flush(self, timeout_s: float | None = None) -> None:
        """Wait (bounded) for litellm's threaded/async success callbacks to fire.

        litellm does NOT run success callbacks on the calling thread: the sync
        ``success_handler`` is submitted to a background ``ThreadPoolExecutor`` and,
        for providers whose sync SDK call rides an async HTTP path, the async
        ``async_success_handler`` fires on an event loop. So the emission counter
        LAGS the synchronous return of ``complete()`` — reading ``stats`` (for the
        Plane-B ``run.summary``) or judging it (``assert_observed``) immediately
        after a call sees ZERO events even though the seam is healthy. This was the
        root cause of both the weave-backend false ``ObservabilityError`` and the
        Plane-B ``run.summary["calls"] == 0`` defect.

        Poll until the counter catches up to the number of completions, bounded by
        ``timeout_s`` (default: the client's ``flush_timeout_s``). One completion ==
        one DE-DUPED event, so the target is ``events >= completions``; the loop is a
        no-op when nothing is pending (``completions == 0``) or already caught up. A
        genuinely un-observed call (callback never fires) waits the full bound, then
        ``assert_observed`` reports the failure — the correct loud direction (§2).
        """
        if timeout_s is None:
            timeout_s = self._flush_timeout_s
        deadline = time.monotonic() + max(0.0, timeout_s)
        while self._stats.events < self._completions and time.monotonic() < deadline:
            time.sleep(_FLUSH_POLL_S)

    def assert_observed(self) -> None:
        """Surface a silently-broken seam. Idempotent.

        Detects **total** emission loss only — the P1 failure where a run produced
        *zero* records: backend != "none" AND completions > 0 AND events == 0.
        This triggers when the litellm callback pipeline never fired at all (seam
        bypassed, callbacks reset between init and the first call, etc.).

        Partial loss (0 < events < completions) passes silently — and this is BY
        DESIGN. litellm may legitimately fire a callback count that does not equal
        completions (e.g. retries generate multiple callback events per completion,
        or streaming emits one event per chunk). A strict events == completions check
        would false-positive on normal litellm behaviour. If you need to detect
        partial loss, compare ``stats.events`` against ``completions`` yourself.

        Raises ``ObservabilityError`` instead of warning when ``require=True``.
        """
        if self._asserted:
            return
        # Wait for litellm's threaded/async callbacks to land BEFORE judging — else
        # a healthy seam false-reports 0 events (the weave-backend defect).
        self.flush()
        self._asserted = True
        backend_name = getattr(self._observability, "name", "none")
        if backend_name != "none" and self._completions > 0 and self._stats.events == 0:
            msg = (
                f"OBSERVABILITY FAILURE: {self._completions} model call(s) made via "
                f"the seam but the emission counter recorded 0 events — backend "
                f"{backend_name!r} produced ZERO records. The litellm callback did "
                f"not fire (seam bypassed, or callbacks reset). Fix before trusting "
                f"this run's traces/aggregates."
            )
            self._warn(msg)
            if self._require:
                raise ObservabilityError(msg)

    @property
    def stats(self) -> EmissionStats:
        """The accrued emission aggregates (Plane-B run.summary source)."""
        return self._stats

    @property
    def completions(self) -> int:
        """Count of ``complete()`` calls made through the seam."""
        return self._completions

    def _warn(self, msg: str) -> None:
        if self._notifier is not None:
            try:
                self._notifier.notify(msg, level="warn", subject="observability")
                return
            except Exception:
                pass
        print(f"[WARN] observability: {msg}", file=sys.stderr)

    # --- lifecycle ---

    def __enter__(self) -> "ModelClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        # Unregister first so the atexit fallback does not double-fire after an
        # explicit context-manager use. This also releases the strong reference to
        # self that the atexit module would otherwise hold until interpreter exit —
        # preventing handler/instance accumulation across many short-lived clients.
        atexit.unregister(self._atexit_handler)
        self.assert_observed()
        return False

    def _atexit_assert(self) -> None:
        try:
            self.assert_observed()
        except ObservabilityError:
            # atexit handlers must not raise — the loud warn already fired.
            pass
