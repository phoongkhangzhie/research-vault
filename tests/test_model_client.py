"""test_model_client.py — SR-MODEL-SEAM S2: ModelClient + reliability contract.

Hermetic: no network, no real completions. We inject a fake litellm.completion and
drive the emission counter directly. Covers: key-via-SecretStore-into-env,
start-once, loud-warn (backend=weave + weave absent / key absent), assert_observed
raises under require when counter==0.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from research_vault.adapters.model_client import ModelClient, ObservabilityError
from research_vault.adapters.observability import NoneBackend
from research_vault.config import Config


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeSecrets:
    """SecretStore that resolves a fixed name→value map; KeyError otherwise."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._m = mapping

    def get(self, name: str) -> str:
        if name in self._m:
            return self._m[name]
        raise KeyError(name)


class FakeNotifier:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def notify(self, message, *, level="info", subject="", tags=None, payload=None):  # noqa: ANN001
        self.events.append((level, message))


class SpyBackend:
    """ObservabilityBackend spy — records probe/start calls; configurable probe result."""

    def __init__(self, name="local", probe_ok=True, probe_msg="ok"):
        self.name = name
        self._probe_ok = probe_ok
        self._probe_msg = probe_msg
        self.probe_calls = 0
        self.start_calls = 0

    def probe(self):
        self.probe_calls += 1
        return self._probe_ok, self._probe_msg

    def start(self):
        self.start_calls += 1


def _cfg(tmp_path: Path) -> Config:
    return Config({
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "projects": {},
    })


@pytest.fixture(autouse=True)
def _clean_litellm_callbacks(monkeypatch):
    """Isolate litellm.callbacks per test so counters don't leak across tests."""
    import litellm
    monkeypatch.setattr(litellm, "callbacks", [], raising=False)
    yield


# ---------------------------------------------------------------------------
# Key resolution into env
# ---------------------------------------------------------------------------

def test_keys_resolved_via_secretstore_into_env(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    secrets = FakeSecrets({"anthropic-api-key": "sk-ant-TEST"})
    ModelClient(_cfg(tmp_path), secrets, SpyBackend(name="none"))
    assert __import__("os").environ["ANTHROPIC_API_KEY"] == "sk-ant-TEST"


def test_env_key_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-EXISTING")
    secrets = FakeSecrets({"anthropic-api-key": "sk-ant-OTHER"})
    ModelClient(_cfg(tmp_path), secrets, SpyBackend(name="none"))
    assert __import__("os").environ["ANTHROPIC_API_KEY"] == "sk-ant-EXISTING"


# ---------------------------------------------------------------------------
# Start-once + always-register counter
# ---------------------------------------------------------------------------

def test_backend_started_once_and_counter_registered(tmp_path):
    import litellm
    spy = SpyBackend(name="local", probe_ok=True)
    mc = ModelClient(_cfg(tmp_path), FakeSecrets({}), spy)
    assert spy.probe_calls == 1
    assert spy.start_calls == 1
    # The emission counter is registered on litellm.callbacks (always).
    assert mc._counter in litellm.callbacks


def test_counter_registered_even_when_probe_fails(tmp_path):
    import litellm
    spy = SpyBackend(name="weave", probe_ok=False, probe_msg="weave missing — ZERO records")
    mc = ModelClient(_cfg(tmp_path), FakeSecrets({}), spy, FakeNotifier())
    # Backend NOT started (probe failed), but counter still registered.
    assert spy.start_calls == 0
    assert mc._counter in litellm.callbacks


# ---------------------------------------------------------------------------
# Loud-warn on broken wiring (weave absent / key absent)
# ---------------------------------------------------------------------------

def test_warn_when_backend_probe_fails(tmp_path):
    notifier = FakeNotifier()
    spy = SpyBackend(name="weave", probe_ok=False, probe_msg="weave not installed — ZERO records")
    ModelClient(_cfg(tmp_path), FakeSecrets({}), spy, notifier)
    assert any(level == "warn" and "ZERO" in msg for level, msg in notifier.events)


def test_raise_under_require_when_probe_fails(tmp_path):
    spy = SpyBackend(name="weave", probe_ok=False, probe_msg="weave not installed")
    with pytest.raises(ObservabilityError):
        ModelClient(_cfg(tmp_path), FakeSecrets({}), spy, FakeNotifier(), require=True)


def test_no_warn_for_none_backend_probe(tmp_path):
    notifier = FakeNotifier()
    # NoneBackend probe returns ok=True, so no warn regardless.
    ModelClient(_cfg(tmp_path), FakeSecrets({}), NoneBackend(), notifier)
    assert notifier.events == []


# ---------------------------------------------------------------------------
# complete() drives the seam + counter
# ---------------------------------------------------------------------------

def test_complete_calls_litellm_and_counts(tmp_path, monkeypatch):
    import litellm

    captured = {}

    def fake_completion(model, messages, **kw):
        captured["model"] = model
        captured["messages"] = messages
        return {"ok": True}

    monkeypatch.setattr(litellm, "completion", fake_completion)
    mc = ModelClient(_cfg(tmp_path), FakeSecrets({}), SpyBackend(name="none"))
    resp = mc.complete("claude-x", [{"role": "user", "content": "hi"}])
    assert resp == {"ok": True}
    assert captured["model"] == "claude-x"
    assert mc.completions == 1


# ---------------------------------------------------------------------------
# assert_observed — the unforgettable-seam guard
# ---------------------------------------------------------------------------

def test_assert_observed_warns_when_calls_but_no_events(tmp_path, monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "completion", lambda model, messages, **kw: {"ok": 1})
    notifier = FakeNotifier()
    # backend=local (not none). We simulate the broken pipeline: complete() bumps
    # _completions but the counter is NOT fed (no callback fired).
    mc = ModelClient(_cfg(tmp_path), FakeSecrets({}), SpyBackend(name="local"), notifier)
    mc.complete("claude-x", [{"role": "user", "content": "hi"}])
    mc.assert_observed()
    assert any(level == "warn" and "OBSERVABILITY FAILURE" in msg for level, msg in notifier.events)


def test_assert_observed_raises_under_require_when_counter_zero(tmp_path, monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "completion", lambda model, messages, **kw: {"ok": 1})
    mc = ModelClient(_cfg(tmp_path), FakeSecrets({}), SpyBackend(name="local"),
                     FakeNotifier(), require=True)
    mc.complete("claude-x", [{"role": "user", "content": "hi"}])
    with pytest.raises(ObservabilityError, match="OBSERVABILITY FAILURE"):
        mc.assert_observed()


def test_assert_observed_no_warn_when_events_present(tmp_path, monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "completion", lambda model, messages, **kw: {"ok": 1})
    notifier = FakeNotifier()
    mc = ModelClient(_cfg(tmp_path), FakeSecrets({}), SpyBackend(name="local"), notifier)
    mc.complete("claude-x", [{"role": "user", "content": "hi"}])
    # Feed the counter directly (simulate the callback firing).
    import datetime
    now = datetime.datetime(2026, 7, 5, 12, 0, 0)
    mc._counter.log_success_event({"response_cost": 0.0}, None, now, now)
    mc.assert_observed()
    assert not any(level == "warn" for level, _ in notifier.events)


def test_assert_observed_idempotent(tmp_path):
    notifier = FakeNotifier()
    mc = ModelClient(_cfg(tmp_path), FakeSecrets({}), SpyBackend(name="local"), notifier)
    mc._completions = 1  # simulate a call with no events
    mc.assert_observed()
    mc.assert_observed()  # second call is a no-op
    warns = [1 for level, _ in notifier.events if level == "warn"]
    assert len(warns) == 1


# ---------------------------------------------------------------------------
# Context manager fires assert_observed on exit
# ---------------------------------------------------------------------------

def test_context_manager_asserts_on_exit(tmp_path, monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "completion", lambda model, messages, **kw: {"ok": 1})
    notifier = FakeNotifier()
    with ModelClient(_cfg(tmp_path), FakeSecrets({}), SpyBackend(name="local"), notifier) as mc:
        mc.complete("claude-x", [{"role": "user", "content": "hi"}])
    # __exit__ ran assert_observed → warn present (counter never fed).
    assert any("OBSERVABILITY FAILURE" in msg for _, msg in notifier.events)


# ---------------------------------------------------------------------------
# atexit handler lifecycle — unregistered on __exit__ (fix/seam-nits)
# ---------------------------------------------------------------------------

def test_atexit_handler_unregistered_on_context_manager_exit(tmp_path, monkeypatch):
    """__exit__ must call atexit.unregister so the handler (and the instance it holds)
    are not retained after the client's lifetime ends.

    RED before the fix: __exit__ only called assert_observed, never atexit.unregister.
    GREEN after: __exit__ calls atexit.unregister(self._atexit_handler) first.
    """
    import atexit as _atexit

    registered_handlers: list = []
    unregistered_handlers: list = []

    monkeypatch.setattr(_atexit, "register",
                        lambda fn, *a, **kw: registered_handlers.append(fn))
    monkeypatch.setattr(_atexit, "unregister",
                        lambda fn: unregistered_handlers.append(fn))

    mc = ModelClient(_cfg(tmp_path), FakeSecrets({}), SpyBackend(name="none"))

    assert len(registered_handlers) == 1, "ModelClient.__init__ must register exactly one handler"
    handler_ref = registered_handlers[0]

    mc.__exit__(None, None, None)

    assert len(unregistered_handlers) == 1, (
        "__exit__ must call atexit.unregister — otherwise the handler (and the instance "
        "it holds via self) leaks until interpreter exit"
    )
    assert unregistered_handlers[0] is handler_ref, (
        "must unregister the exact handler that was registered in __init__"
    )


def test_atexit_handler_not_retained_across_n_clients(tmp_path, monkeypatch):
    """Constructing + __exit__-ing N clients leaves 0 net registered atexit handlers.

    Without unregister: N handlers accumulate and each holds a reference to its client.
    After the fix: each __exit__ unregisters its own handler — net count = 0.
    """
    import atexit as _atexit

    net: list = []  # positive = register, negative = unregister (by object id)

    def _spy_register(fn, *a, **kw):
        net.append(id(fn))

    def _spy_unregister(fn):
        if id(fn) in net:
            net.remove(id(fn))

    monkeypatch.setattr(_atexit, "register", _spy_register)
    monkeypatch.setattr(_atexit, "unregister", _spy_unregister)

    N = 5
    for _ in range(N):
        mc = ModelClient(_cfg(tmp_path), FakeSecrets({}), SpyBackend(name="none"))
        mc.__exit__(None, None, None)

    assert len(net) == 0, (
        f"expected 0 net registered handlers after {N} construct+__exit__ cycles, "
        f"got {len(net)} — atexit handlers leaked"
    )


# ---------------------------------------------------------------------------
# AdapterSet.model — lazy, cached, first-class
# ---------------------------------------------------------------------------

def test_adapterset_model_is_lazy_and_cached(tmp_path):
    from research_vault.adapters.base import AdapterSet, FileNotifier, LocalSubprocess, EnvSecretStore
    aset = AdapterSet(
        notifier=FileNotifier(tmp_path / "state"),
        backend=LocalSubprocess(),
        secrets=EnvSecretStore(),
        cfg=_cfg(tmp_path),
    )
    # No ModelClient built yet.
    assert aset._model_cache is None
    m1 = aset.model
    m2 = aset.model
    assert m1 is m2  # cached
    assert type(m1).__name__ == "ModelClient"


def test_load_adapters_does_not_construct_model(tmp_path, monkeypatch):
    """load_adapters must NOT eagerly build a ModelClient (import-light + no weave.init)."""
    from research_vault.adapters.base import load_adapters
    aset = load_adapters(_cfg(tmp_path))
    assert aset._model_cache is None
    assert aset.cfg is not None
