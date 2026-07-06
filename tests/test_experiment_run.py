"""test_experiment_run.py — SR-MODEL-SEAM S6: Plane-B run logging (hermetic).

No network: a fake ``wandb`` module is injected into sys.modules so ``import wandb``
inside log_experiment_run picks it up. The emission counter is fed directly by the
run_fn (simulating the litellm callback firing) so aggregates flow into run.summary.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

from research_vault.adapters.base import AdapterSet, FileNotifier, LocalSubprocess, EnvSecretStore
from research_vault.config import Config
from research_vault.experiment_run import RunLoggingError, log_experiment_run


# ---------------------------------------------------------------------------
# Fakes
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
        self.finished = False

    def finish(self):
        self.finished = True


class _FakeWandb:
    def __init__(self, raise_on_init=False):
        self.init_calls: list = []
        self.last_run: _FakeRun | None = None
        self._raise = raise_on_init

    def init(self, entity=None, project=None, name=None, config=None):  # noqa: ANN001
        if self._raise:
            raise RuntimeError("wandb.init boom (no key)")
        self.init_calls.append((entity, project, name, config))
        self.last_run = _FakeRun(entity, project, name, config or {})
        return self.last_run


class _FakeNotifier:
    def __init__(self):
        self.events: list = []

    def notify(self, message, *, level="info", subject="", tags=None, payload=None):  # noqa: ANN001
        self.events.append((level, message, payload))


def _cfg(tmp_path: Path, observability: dict) -> Config:
    return Config({
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "projects": {},
        "observability": observability,
    })


def _adapters(tmp_path: Path, cfg: Config, notifier=None, require=False) -> AdapterSet:
    aset = AdapterSet(
        notifier=notifier or _FakeNotifier(),
        backend=LocalSubprocess(),
        secrets=EnvSecretStore(),
        cfg=cfg,
        require_observability=require,
    )
    return aset


@pytest.fixture(autouse=True)
def _clean_litellm_callbacks(monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "callbacks", [], raising=False)
    yield


def _feed_counter(model_client, prompt=10, completion=5, cost=0.01):
    """Simulate the litellm callback firing for one logged call."""
    now = datetime.datetime(2026, 7, 5, 12, 0, 0)
    end = datetime.datetime(2026, 7, 5, 12, 0, 1)

    class _Usage:
        prompt_tokens = prompt
        completion_tokens = completion

    class _Resp:
        usage = _Usage()

    model_client._counter.log_success_event({"response_cost": cost}, _Resp(), now, end)


# ---------------------------------------------------------------------------
# Disabled path — run_fn still executes, returns ""
# ---------------------------------------------------------------------------

def test_disabled_runs_fn_and_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")  # avoid keyring lookup
    cfg = _cfg(tmp_path, {"backend": "local"})  # run_logging defaults False
    adapters = _adapters(tmp_path, cfg)
    called = {"n": 0}

    def run_fn(mc):
        called["n"] += 1

    path = log_experiment_run(
        cfg, adapters, config_params={"model": "m"}, analysis_metrics=None, run_fn=run_fn
    )
    assert path == ""
    assert called["n"] == 1


# ---------------------------------------------------------------------------
# Enabled + no project → loud warn (raise under require)
# ---------------------------------------------------------------------------

def test_enabled_no_project_warns(tmp_path, monkeypatch):
    monkeypatch.delenv("WANDB_PROJECT", raising=False)
    cfg = _cfg(tmp_path, {"backend": "local", "run_logging": True})  # no wandb_project, no slug
    notifier = _FakeNotifier()
    adapters = _adapters(tmp_path, cfg, notifier=notifier)
    path = log_experiment_run(
        cfg, adapters, config_params={}, analysis_metrics=None, run_fn=lambda mc: None
    )
    assert path == ""
    assert any(level == "warn" and "project" in msg.lower() for level, msg, _ in notifier.events)


def test_enabled_no_project_raises_under_require(tmp_path, monkeypatch):
    monkeypatch.delenv("WANDB_PROJECT", raising=False)
    cfg = _cfg(tmp_path, {"backend": "local", "run_logging": True})
    adapters = _adapters(tmp_path, cfg, require=True)
    with pytest.raises(RunLoggingError):
        log_experiment_run(
            cfg, adapters, config_params={}, analysis_metrics=None, run_fn=lambda mc: None
        )


# ---------------------------------------------------------------------------
# project_slug — the new per-project default (D-precedence: slug feeds wandb.init)
# ---------------------------------------------------------------------------

def test_project_slug_feeds_wandb_init_project(tmp_path, monkeypatch):
    monkeypatch.delenv("WANDB_PROJECT", raising=False)
    fake = _FakeWandb()
    monkeypatch.setitem(sys.modules, "wandb", fake)
    # No wandb_project configured — project must come from project_slug.
    cfg = _cfg(tmp_path, {"backend": "local", "run_logging": True})
    adapters = _adapters(tmp_path, cfg)

    path = log_experiment_run(
        cfg, adapters, config_params={"model": "m"}, analysis_metrics=None,
        run_fn=lambda mc: _feed_counter(mc), project_slug="cultural-social-sim",
    )
    assert fake.init_calls[0][1] == "cultural-social-sim"  # (entity, project, name, config)
    assert path.split("/")[1] == "cultural-social-sim"


def test_explicit_wandb_project_overrides_slug(tmp_path, monkeypatch):
    monkeypatch.delenv("WANDB_PROJECT", raising=False)
    fake = _FakeWandb()
    monkeypatch.setitem(sys.modules, "wandb", fake)
    cfg = _cfg(tmp_path, {"backend": "local", "run_logging": True, "wandb_project": "acme/override"})
    adapters = _adapters(tmp_path, cfg)

    log_experiment_run(
        cfg, adapters, config_params={}, analysis_metrics=None,
        run_fn=lambda mc: _feed_counter(mc), project_slug="cultural-social-sim",
    )
    assert fake.init_calls[0][0] == "acme"
    assert fake.init_calls[0][1] == "override"


# ---------------------------------------------------------------------------
# Enabled + wandb.init raises → loud warn (raise under require)
# ---------------------------------------------------------------------------

def test_wandb_init_failure_warns(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "wandb", _FakeWandb(raise_on_init=True))
    cfg = _cfg(tmp_path, {"backend": "local", "run_logging": True, "wandb_project": "e/p"})
    notifier = _FakeNotifier()
    adapters = _adapters(tmp_path, cfg, notifier=notifier)
    path = log_experiment_run(
        cfg, adapters, config_params={}, analysis_metrics=None, run_fn=lambda mc: None
    )
    assert path == ""
    assert any(level == "warn" and "wandb.init" in msg for level, msg, _ in notifier.events)


def test_wandb_init_failure_raises_under_require(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "wandb", _FakeWandb(raise_on_init=True))
    cfg = _cfg(tmp_path, {"backend": "local", "run_logging": True, "wandb_project": "e/p"})
    adapters = _adapters(tmp_path, cfg, require=True)
    with pytest.raises(RunLoggingError):
        log_experiment_run(
            cfg, adapters, config_params={}, analysis_metrics=None, run_fn=lambda mc: None
        )


# ---------------------------------------------------------------------------
# Happy path — config + summary shapes + run path
# ---------------------------------------------------------------------------

def test_happy_path_logs_config_summary_and_returns_run_path(tmp_path, monkeypatch):
    fake = _FakeWandb()
    monkeypatch.setitem(sys.modules, "wandb", fake)
    cfg = _cfg(tmp_path, {"backend": "local", "run_logging": True, "wandb_project": "acme/proj"})
    notifier = _FakeNotifier()
    adapters = _adapters(tmp_path, cfg, notifier=notifier)

    pre_reg = {"model": "claude-x", "seed": 7, "temperature": 0.0}
    metrics = {"demo_accuracy": 0.99}

    def run_fn(mc):
        _feed_counter(mc, prompt=20, completion=8, cost=0.02)

    path = log_experiment_run(
        cfg, adapters, config_params=pre_reg, analysis_metrics=metrics,
        run_fn=run_fn, run_name="my-run",
    )
    assert path == "acme/proj/run123"

    run = fake.last_run
    # config carries the pre-registered params (rv wandb pull alias-table keys).
    assert run.config["model"] == "claude-x"
    assert run.config["seed"] == 7
    # summary carries aggregates + the known metric.
    assert run.summary["calls"] == 1
    assert run.summary["total_tokens"] == 28
    assert run.summary["demo_accuracy"] == 0.99
    assert "total_cost_usd" in run.summary
    assert "latency_p50_s" in run.summary
    assert run.finished is True
    # run path surfaced via notifier payload.
    assert any(
        (payload or {}).get("results_wandb_run") == "acme/proj/run123"
        for _, _, payload in notifier.events
    )


def test_happy_path_writes_run_path_to_note(tmp_path, monkeypatch):
    fake = _FakeWandb()
    monkeypatch.setitem(sys.modules, "wandb", fake)
    cfg = _cfg(tmp_path, {"backend": "local", "run_logging": True, "wandb_project": "acme/proj"})
    adapters = _adapters(tmp_path, cfg)

    note = tmp_path / "exp.md"
    note.write_text("---\ntype: experiments\ntitle: t\n---\nbody\n", encoding="utf-8")

    log_experiment_run(
        cfg, adapters, config_params={"model": "m"}, analysis_metrics=None,
        run_fn=lambda mc: _feed_counter(mc), experiment_note=note,
    )
    text = note.read_text()
    assert "results_wandb_run: acme/proj/run123" in text
