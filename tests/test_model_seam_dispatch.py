"""test_model_seam_dispatch.py — SR-MODEL-SEAM: FAITHFUL callback-dispatch coverage.

Why this file exists: the original seam tests (test_model_client.py,
test_experiment_run.py) drove the emission counter by HAND-INVOKING a single
synchronous ``log_success_event``. That never exercised litellm's REAL dispatch,
so three defects shipped green:

  1. weave-backend ``assert_observed`` false-raised (counter read as 0),
  2. Plane-B ``run.summary["calls"] == 0`` for a healthy run,
  3. one completion double-counted (``events == 2``, two JSONL lines).

All three are timing/dispatch bugs invisible to a synchronous hand-invoke. This
module reproduces litellm 1.91's actual behaviour:

  * the success callback fires OFF the calling thread (litellm submits the sync
    ``success_handler`` to a background ``ThreadPoolExecutor`` and, for providers
    whose sync SDK call rides an async HTTP path, awaits ``async_success_handler``
    on an event loop) — so the counter LAGS the return of ``complete()``;
  * for ONE completion the callback fires TWICE — once via ``log_success_event``
    and once via ``async_log_success_event`` — with the SAME ``litellm_call_id``.

``FaithfulCompletion`` patches ``litellm.completion`` to reproduce exactly that:
it returns synchronously and, after a delay, fires the sync + async success
callbacks from two SEPARATE threads (the real race the dedupe lock guards). No
network, no real keys — hermetic. The genuinely no-mock path is covered by the
``live``-gated tests in test_model_seam_live.py (run by the operator with keys).
"""
from __future__ import annotations

import asyncio
import datetime
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from research_vault.adapters.model_client import ModelClient, ObservabilityError
from research_vault.config import Config


# ---------------------------------------------------------------------------
# Faithful litellm dispatch reproduction
# ---------------------------------------------------------------------------

class _Usage:
    def __init__(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _Resp:
    def __init__(self, prompt: int, completion: int, model: str, call_id: str) -> None:
        self.usage = _Usage(prompt, completion)
        self.model = model
        self.id = call_id


class FaithfulCompletion:
    """Drop-in for ``litellm.completion`` reproducing litellm 1.91's success dispatch.

    Returns the response SYNCHRONOUSLY (before any callback fires — exactly as real
    litellm does), then fires the success callback on every registered CustomLogger
    TWICE, from two separate background threads, after ``delay_s``:
      * ``log_success_event``          (litellm's sync executor-thread path),
      * ``async_log_success_event``    (litellm's async event-loop path),
    both with the SAME ``litellm_call_id`` in kwargs. This is the exact realism the
    old hand-invoke tests lacked: async lag + double-fire + a cross-thread race.
    """

    def __init__(
        self,
        *,
        delay_s: float = 0.2,
        prompt_tokens: int = 11,
        completion_tokens: int = 7,
        cost: float = 0.001,
        fire_sync: bool = True,
        fire_async: bool = True,
    ) -> None:
        self.delay_s = delay_s
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost = cost
        self.fire_sync = fire_sync
        self.fire_async = fire_async
        self.threads: list[threading.Thread] = []

    def __call__(self, model, messages, **kw):  # noqa: ANN001
        import litellm

        call_id = "req-" + uuid.uuid4().hex
        start = datetime.datetime(2026, 7, 5, 12, 0, 0)
        resp = _Resp(self.prompt_tokens, self.completion_tokens, model, call_id)
        kwargs = {"litellm_call_id": call_id, "response_cost": self.cost, "model": model}
        callbacks = list(getattr(litellm, "callbacks", []) or [])

        def _end() -> datetime.datetime:
            return start + datetime.timedelta(seconds=1)

        def _fire_sync() -> None:
            time.sleep(self.delay_s)
            for cb in callbacks:
                if hasattr(cb, "log_success_event"):
                    cb.log_success_event(kwargs, resp, start, _end())

        def _fire_async() -> None:
            time.sleep(self.delay_s)
            for cb in callbacks:
                if hasattr(cb, "async_log_success_event"):
                    asyncio.run(cb.async_log_success_event(kwargs, resp, start, _end()))

        if self.fire_sync:
            t = threading.Thread(target=_fire_sync, daemon=True)
            t.start()
            self.threads.append(t)
        if self.fire_async:
            t = threading.Thread(target=_fire_async, daemon=True)
            t.start()
            self.threads.append(t)
        return resp

    def join(self, timeout: float = 5.0) -> None:
        for t in self.threads:
            t.join(timeout)


def weave_wrap(inner: FaithfulCompletion):
    """Reproduce weave's ``litellm.completion`` patch: a wrapper that calls the
    underlying completion (which still fires litellm's callbacks). weave.init does
    NOT clobber ``litellm.callbacks`` (ruled out upstream); it only wraps the call,
    adding indirection/latency — the underlying async dispatch is unchanged."""

    def _wrapped(model, messages, **kw):  # noqa: ANN001
        return inner(model, messages, **kw)

    return _wrapped


# ---------------------------------------------------------------------------
# Fakes + fixtures
# ---------------------------------------------------------------------------

class FakeSecrets:
    def get(self, name: str) -> str:
        raise KeyError(name)


class FakeNotifier:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def notify(self, message, *, level="info", subject="", tags=None, payload=None):  # noqa: ANN001
        self.events.append((level, message))


class SpyBackend:
    """ObservabilityBackend spy — configurable name; probe ok, start is a no-op.

    ``name`` matters: the weave-path tests use name="weave" so ``assert_observed``
    exercises the real ``backend_name != "none"`` branch that false-raised.
    """

    def __init__(self, name="local") -> None:
        self.name = name
        self.start_calls = 0

    def probe(self):
        return True, "ok"

    def start(self):
        self.start_calls += 1


def _cfg(tmp_path: Path, observability: dict | None = None) -> Config:
    return Config({
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "projects": {},
        "observability": observability or {},
    })


@pytest.fixture(autouse=True)
def _clean_litellm_callbacks(monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "callbacks", [], raising=False)
    yield


# ---------------------------------------------------------------------------
# Bug 3 — double-count dedupe: one completion == one event (and one JSONL line)
# ---------------------------------------------------------------------------

def test_one_completion_counts_once_despite_sync_and_async_fire(tmp_path, monkeypatch):
    """RED before dedupe: sync + async both count → events == 2.
    GREEN after: the shared litellm_call_id dedupes → events == 1."""
    import litellm

    fake = FaithfulCompletion(fire_sync=True, fire_async=True)
    monkeypatch.setattr(litellm, "completion", fake)

    mc = ModelClient(_cfg(tmp_path), FakeSecrets(), SpyBackend(name="local"))
    mc.complete("anthropic/claude-x", [{"role": "user", "content": "hi"}])
    mc.flush()
    fake.join()
    mc.flush()  # ensure the (deduped) second fire has also been dispatched

    assert mc.completions == 1
    assert mc.stats.events == 1, (
        f"one completion must count once, got {mc.stats.events} "
        "(sync + async success callbacks both counted → double-count regression)"
    )
    # tokens counted exactly once (not doubled)
    assert mc.stats.prompt_tokens == 11
    assert mc.stats.completion_tokens == 7


def test_local_jsonl_writes_one_line_per_completion(tmp_path, monkeypatch):
    """The LocalBackend JSONL logger must also dedupe: one completion → one line,
    not two (RED before dedupe: sync + async each wrote a line)."""
    import litellm
    from research_vault.adapters.observability import LocalBackend

    fake = FaithfulCompletion(fire_sync=True, fire_async=True)
    monkeypatch.setattr(litellm, "completion", fake)

    cfg = _cfg(tmp_path, {"backend": "local"})
    backend = LocalBackend(cfg.state_dir)
    mc = ModelClient(cfg, FakeSecrets(), backend)
    mc.complete("anthropic/claude-x", [{"role": "user", "content": "hi"}])
    mc.flush()
    fake.join()

    jsonl = cfg.state_dir / "llm_calls.jsonl"
    assert jsonl.exists()
    lines = [ln for ln in jsonl.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSONL line per completion, got {len(lines)} "
        "(double-write regression: sync + async each logged)"
    )


# ---------------------------------------------------------------------------
# Bug 1 — weave-backend: the counter fires; assert_observed must NOT false-raise
# ---------------------------------------------------------------------------

def test_weave_backend_counter_fires_after_flush(tmp_path, monkeypatch):
    """RED before flush: with the callback firing off-thread, assert_observed reads
    events == 0 under backend=weave and raises ObservabilityError. GREEN after:
    assert_observed flushes first, sees the real event, does not raise."""
    import litellm

    inner = FaithfulCompletion(delay_s=0.25)
    monkeypatch.setattr(litellm, "completion", weave_wrap(inner))

    notifier = FakeNotifier()
    # backend=weave + require=True → a false 0-count would RAISE (the shipped defect).
    mc = ModelClient(
        _cfg(tmp_path, {"backend": "weave"}),
        FakeSecrets(),
        SpyBackend(name="weave"),
        notifier,
        require=True,
    )
    mc.complete("anthropic/claude-x", [{"role": "user", "content": "hi"}])

    # Must NOT raise — flush lets the off-thread weave-wrapped callback land first.
    mc.assert_observed()
    inner.join()
    assert mc.stats.events == 1
    assert mc.stats.total_tokens > 0
    assert not any(level == "warn" for level, _ in notifier.events)


def test_flush_is_bounded_when_callback_never_fires(tmp_path, monkeypatch):
    """A genuinely un-observed call (no callback ever fires) must not hang: flush is
    bounded, then assert_observed reports the failure loud (charter §2)."""
    import litellm

    # fire nothing — simulate a truly broken pipeline.
    fake = FaithfulCompletion(fire_sync=False, fire_async=False)
    monkeypatch.setattr(litellm, "completion", fake)

    notifier = FakeNotifier()
    # Short flush bound so both the explicit flush AND assert_observed's internal
    # flush stay fast on the genuine-failure path.
    mc = ModelClient(
        _cfg(tmp_path), FakeSecrets(), SpyBackend(name="local"), notifier,
        flush_timeout_s=0.3,
    )
    mc.complete("anthropic/claude-x", [{"role": "user", "content": "hi"}])

    t0 = time.monotonic()
    mc.flush()  # bounded by flush_timeout_s
    elapsed = time.monotonic() - t0
    assert elapsed < 2.0, "flush must return at the bound, not hang"

    mc.assert_observed()
    assert any("OBSERVABILITY FAILURE" in msg for _, msg in notifier.events)


# ---------------------------------------------------------------------------
# Bug 2 — Plane B: run.summary carries the REAL calls/tokens (not 0)
# ---------------------------------------------------------------------------

class _FakeSummary(dict):
    def update(self, d, *a, **k):  # noqa: ANN001
        super().update(d)


class _FakeRun:
    def __init__(self, entity, project, name, config):  # noqa: ANN001
        self.entity = entity or "ent"
        self.project = project
        self.name = name
        self.id = "run123"
        self.config = dict(config)
        self.summary = _FakeSummary()

    def finish(self):
        pass


class _FakeWandb:
    def __init__(self) -> None:
        self.last_run: _FakeRun | None = None

    def init(self, entity=None, project=None, name=None, config=None):  # noqa: ANN001
        self.last_run = _FakeRun(entity, project, name, config or {})
        return self.last_run


def test_plane_b_summary_carries_real_calls_via_dispatch(tmp_path, monkeypatch):
    """RED before flush: log_experiment_run reads stats immediately after run_fn,
    before the off-thread callback lands → run.summary["calls"] == 0 (the shipped
    Plane-B defect). GREEN after: flush() lets the real event land → calls == 1."""
    import litellm
    from research_vault.adapters.base import (
        AdapterSet, FileNotifier, LocalSubprocess, EnvSecretStore,
    )
    from research_vault.experiment_run import log_experiment_run

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")  # avoid keyring lookup
    fake_wandb = _FakeWandb()
    monkeypatch.setitem(sys.modules, "wandb", fake_wandb)

    fake = FaithfulCompletion(delay_s=0.2)
    monkeypatch.setattr(litellm, "completion", fake)

    cfg = _cfg(tmp_path, {
        "backend": "local",
        "run_logging": True,
        "wandb_project": "acme/proj",
    })
    adapters = AdapterSet(
        notifier=FileNotifier(tmp_path / "state"),
        backend=LocalSubprocess(),
        secrets=EnvSecretStore(),
        cfg=cfg,
    )

    def _do_calls(model_client):
        # A REAL seam call — goes through litellm.completion (the faithful dispatch).
        model_client.complete(
            model="anthropic/claude-x",
            messages=[{"role": "user", "content": "hi"}],
        )

    run_path = log_experiment_run(
        cfg, adapters,
        config_params={"model": "anthropic/claude-x", "seed": 7},
        analysis_metrics={"demo_accuracy": 0.99},
        run_fn=_do_calls,
        run_name="my-run",
    )
    fake.join()

    assert run_path == "acme/proj/run123"
    # Assert on the ACTUAL logged run.summary — the exact surface that shipped as 0.
    summary = fake_wandb.last_run.summary
    assert summary["calls"] == 1, (
        f"Plane-B run.summary must reflect the real call, got calls={summary.get('calls')} "
        "(read the counter before the off-thread callback landed → 0)"
    )
    assert summary["total_tokens"] == 18  # 11 + 7, counted once
    assert summary["demo_accuracy"] == 0.99  # analysis metric still merged
