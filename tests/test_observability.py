"""test_observability.py — SR-MODEL-SEAM S1: observability backends + emission counter.

Hermetic: no network. The litellm callback pipeline is exercised by invoking the
CustomLogger methods DIRECTLY with synthetic kwargs (the "fake litellm" pattern) —
we never make a real completion call. litellm IS installed in the dev env, so
subclassing the real CustomLogger is fine; only the network is faked.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pytest

from research_vault.adapters.observability import (
    EmissionStats,
    LangfuseBackend,
    LocalBackend,
    NoneBackend,
    WeaveBackend,
    make_emission_counter,
    resolve_observability_backend,
)
from research_vault.config import Config


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path, observability: dict | None = None) -> Config:
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "projects": {},
    }
    if observability is not None:
        raw["observability"] = observability
    return Config(raw)


class _FakeUsage:
    def __init__(self, p: int, c: int) -> None:
        self.prompt_tokens = p
        self.completion_tokens = c


class _FakeResponse:
    def __init__(self, p: int, c: int) -> None:
        self.usage = _FakeUsage(p, c)


def _times():
    start = datetime.datetime(2026, 7, 5, 12, 0, 0)
    end = datetime.datetime(2026, 7, 5, 12, 0, 2)  # 2.0s
    return start, end


# ---------------------------------------------------------------------------
# EmissionStats accrual
# ---------------------------------------------------------------------------

def test_emission_stats_accrues_usage_cost_latency():
    stats = EmissionStats()
    start, end = _times()
    stats.record_event(
        {"response_cost": 0.0123, "model": "claude-x"},
        _FakeResponse(100, 40),
        start,
        end,
        success=True,
    )
    assert stats.events == 1
    assert stats.prompt_tokens == 100
    assert stats.completion_tokens == 40
    assert stats.total_tokens == 140
    assert stats.total_cost_usd == pytest.approx(0.0123)
    assert stats.latencies_s == [2.0]


def test_emission_stats_failure_counts_event_not_tokens():
    stats = EmissionStats()
    start, end = _times()
    stats.record_event({}, None, start, end, success=False)
    assert stats.events == 1  # the event fired (seam observed)
    assert stats.total_tokens == 0
    assert stats.total_cost_usd == 0.0


def test_emission_stats_as_summary_shape_matches_run_summary():
    stats = EmissionStats()
    start, end = _times()
    stats.record_event({"response_cost": 0.01}, _FakeResponse(10, 5), start, end, success=True)
    summary = stats.as_summary()
    for key in ("calls", "prompt_tokens", "completion_tokens", "total_tokens",
                "total_cost_usd", "latency_p50_s", "latency_p95_s"):
        assert key in summary
    assert summary["calls"] == 1
    assert summary["total_tokens"] == 15


def test_emission_stats_extract_usage_from_dict_response():
    stats = EmissionStats()
    start, end = _times()
    stats.record_event(
        {"response_cost": 0.0},
        {"usage": {"prompt_tokens": 7, "completion_tokens": 3}},
        start,
        end,
        success=True,
    )
    assert stats.prompt_tokens == 7
    assert stats.completion_tokens == 3


# ---------------------------------------------------------------------------
# _EmissionCounter (CustomLogger) — feeds stats; ALWAYS registered
# ---------------------------------------------------------------------------

def test_emission_counter_feeds_stats_via_success_event():
    stats = EmissionStats()
    counter = make_emission_counter(stats)
    start, end = _times()
    counter.log_success_event(
        {"response_cost": 0.02}, _FakeResponse(50, 10), start, end
    )
    assert stats.events == 1
    assert stats.total_tokens == 60


def test_emission_counter_registers_on_litellm_callbacks(monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "callbacks", [], raising=False)
    from research_vault.adapters.observability import _register_callback
    stats = EmissionStats()
    counter = make_emission_counter(stats)
    _register_callback(counter)
    assert counter in litellm.callbacks


# ---------------------------------------------------------------------------
# LocalBackend — zero-infra JSONL default
# ---------------------------------------------------------------------------

def test_local_backend_probe_ok(tmp_path):
    b = LocalBackend(tmp_path / "state")
    ok, msg = b.probe()
    assert ok is True
    assert "llm_calls.jsonl" in msg


def test_local_backend_writes_jsonl_line(tmp_path, monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "callbacks", [], raising=False)
    state = tmp_path / "state"
    b = LocalBackend(state)
    b.start()
    # The registered logger is the last callback; invoke it directly (fake litellm).
    logger = litellm.callbacks[-1]
    start, end = _times()
    logger.log_success_event(
        {"response_cost": 0.05, "model": "claude-y"}, _FakeResponse(20, 8), start, end
    )
    jsonl = state / "llm_calls.jsonl"
    assert jsonl.exists()
    rec = json.loads(jsonl.read_text().strip())
    assert rec["status"] == "success"
    assert rec["model"] == "claude-y"
    assert rec["prompt_tokens"] == 20
    assert rec["completion_tokens"] == 8


# ---------------------------------------------------------------------------
# NoneBackend
# ---------------------------------------------------------------------------

def test_none_backend_probe_and_start_noop():
    b = NoneBackend()
    ok, msg = b.probe()
    assert ok is True and "disabled" in msg
    assert b.start() is None


# ---------------------------------------------------------------------------
# WeaveBackend — guarded; probe fails loud when weave absent / key absent
# ---------------------------------------------------------------------------

def test_weave_backend_probe_fails_when_weave_absent(monkeypatch):
    # Simulate weave not installed by blocking the import.
    import builtins
    real_import = builtins.__import__

    def _blocked(name, *a, **k):
        if name == "weave" or name.startswith("weave."):
            raise ImportError("blocked by test")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    # Evict any cached weave so the blocked import path is hit.
    monkeypatch.delitem(sys.modules, "weave", raising=False)
    b = WeaveBackend("entity/proj", key_present=True)
    ok, msg = b.probe()
    assert ok is False
    assert "weave" in msg.lower() and "zero" in msg.lower()


def test_weave_backend_probe_fails_when_key_absent(monkeypatch):
    pytest.importorskip("weave")
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    b = WeaveBackend("entity/proj", key_present=False)
    ok, msg = b.probe()
    assert ok is False
    assert "WANDB_API_KEY" in msg


def test_weave_backend_probe_ok_when_installed_and_key_present(monkeypatch):
    pytest.importorskip("weave")
    b = WeaveBackend("entity/proj", key_present=True)
    ok, msg = b.probe()
    assert ok is True
    assert "entity/proj" in msg


def test_weave_backend_probe_fails_when_no_project(monkeypatch):
    pytest.importorskip("weave")
    b = WeaveBackend("", key_present=True)
    ok, msg = b.probe()
    assert ok is False
    assert "project" in msg.lower()


# ---------------------------------------------------------------------------
# LangfuseBackend
# ---------------------------------------------------------------------------

def test_langfuse_backend_probe_fails_when_absent(monkeypatch):
    # langfuse not installed in the dev env → probe should fail cleanly.
    ok, msg = LangfuseBackend().probe()
    assert ok is False
    assert "langfuse" in msg.lower()


def test_langfuse_backend_start_appends_string_callback(monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "success_callback", [], raising=False)
    monkeypatch.setattr(litellm, "failure_callback", [], raising=False)
    LangfuseBackend().start()
    assert "langfuse" in litellm.success_callback
    assert "langfuse" in litellm.failure_callback


# ---------------------------------------------------------------------------
# resolve_observability_backend — config-driven selection
# ---------------------------------------------------------------------------

def test_resolve_default_is_local(tmp_path):
    b = resolve_observability_backend(_cfg(tmp_path))
    assert b.name == "local"


def test_resolve_none(tmp_path):
    b = resolve_observability_backend(_cfg(tmp_path, {"backend": "none"}))
    assert b.name == "none"


def test_resolve_weave(tmp_path):
    b = resolve_observability_backend(
        _cfg(tmp_path, {"backend": "weave", "wandb_project": "e/p"}), key_present=True
    )
    assert b.name == "weave"
    assert b.project == "e/p"


def test_resolve_langfuse(tmp_path):
    b = resolve_observability_backend(_cfg(tmp_path, {"backend": "langfuse"}))
    assert b.name == "langfuse"


def test_resolve_unknown_raises(tmp_path):
    with pytest.raises(ValueError, match="Unknown observability backend"):
        resolve_observability_backend(_cfg(tmp_path, {"backend": "bogus"}))


# ---------------------------------------------------------------------------
# S3 — rv check --require-observability
# ---------------------------------------------------------------------------

def test_check_observability_local_ok(tmp_path):
    from research_vault.check import _check_observability
    ok, msg, required = _check_observability(_cfg(tmp_path, {"backend": "local"}))
    assert ok is True
    assert required is False
    assert "local" in msg.lower()


def test_check_observability_none_is_ok(tmp_path):
    from research_vault.check import _check_observability
    ok, msg, _ = _check_observability(_cfg(tmp_path, {"backend": "none"}))
    assert ok is True
    assert "disabled" in msg


def test_check_observability_unknown_backend_fails(tmp_path):
    from research_vault.check import _check_observability
    ok, msg, _ = _check_observability(_cfg(tmp_path, {"backend": "bogus"}))
    assert ok is False
    assert "Unknown observability backend" in msg


def test_check_observability_weave_without_key_fails(tmp_path, monkeypatch):
    pytest.importorskip("weave")
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    from research_vault.check import _check_observability
    ok, msg, _ = _check_observability(
        _cfg(tmp_path, {"backend": "weave", "wandb_project": "e/p"})
    )
    assert ok is False


def test_run_preflight_includes_observability_key(tmp_path):
    from research_vault.check import run_preflight
    result = run_preflight(_cfg(tmp_path, {"backend": "local"}))
    assert "observability" in result
    assert result["observability"] is True


def test_require_observability_promotes_to_required_fail(tmp_path, monkeypatch):
    """--require-observability + broken weave wiring → all_required_ok False."""
    pytest.importorskip("weave")
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    from research_vault.check import run_preflight
    # weave backend, no key → obs_ok False → required gate fails.
    result = run_preflight(
        _cfg(tmp_path, {"backend": "weave", "wandb_project": "e/p"}),
        require_observability=True,
    )
    assert result["observability"] is False
    assert result["all_required_ok"] is False
    assert "required: --require-observability" in result["report"]


def test_require_observability_false_does_not_gate(tmp_path, monkeypatch):
    """Without the flag, a broken observability wiring does NOT fail all_required
    on the observability axis (claude/api may still fail — check obs axis only)."""
    from research_vault.check import run_preflight
    result = run_preflight(_cfg(tmp_path, {"backend": "local"}), require_observability=False)
    # local backend is ok; the report must not contain the required-observability tag.
    assert "required: --require-observability" not in result["report"]


def test_check_parser_accepts_require_observability_flag():
    from research_vault.check import build_parser
    p = build_parser()
    args = p.parse_args(["--require-observability"])
    assert args.require_observability is True
    args2 = p.parse_args([])
    assert args2.require_observability is False


# ---------------------------------------------------------------------------
# rv observability verb (coordinator add) — discovery + wiring test surface
# ---------------------------------------------------------------------------

def test_observability_verb_parser_subcommands():
    from research_vault.observability_cli import build_parser
    p = build_parser()
    for subcmd in ("status", "probe"):
        args = p.parse_args([subcmd])
        assert args.obs_cmd == subcmd


def test_observability_verb_probe_local_ok(tmp_path, monkeypatch, capsys):
    from research_vault import observability_cli
    cfg = _cfg(tmp_path, {"backend": "local"})
    monkeypatch.setattr(observability_cli, "_load_cfg", lambda: cfg)
    import argparse as _ap
    rc = observability_cli.run(_ap.Namespace(obs_cmd="probe"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Safe to run" in out
    assert "Plane B" in out


def test_observability_verb_probe_weave_no_key_fails(tmp_path, monkeypatch, capsys):
    pytest.importorskip("weave")
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    from research_vault import observability_cli
    cfg = _cfg(tmp_path, {"backend": "weave", "wandb_project": "e/p"})
    monkeypatch.setattr(observability_cli, "_load_cfg", lambda: cfg)
    import argparse as _ap
    rc = observability_cli.run(_ap.Namespace(obs_cmd="probe"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "ZERO records" in out


def test_observability_verb_status_shows_reach_recipe(tmp_path, monkeypatch, capsys):
    from research_vault import observability_cli
    cfg = _cfg(tmp_path, {"backend": "local"})
    monkeypatch.setattr(observability_cli, "_load_cfg", lambda: cfg)
    import argparse as _ap
    rc = observability_cli.run(_ap.Namespace(obs_cmd="status"))
    out = capsys.readouterr().out
    assert rc == 0
    # The copy-paste reach path must be surfaced (reachability, coordinator add #2).
    assert "load_adapters(cfg).model" in out or "adapters.model.complete" in out


# ---------------------------------------------------------------------------
# Plane-B run-logging resolution
# ---------------------------------------------------------------------------

def test_resolve_run_logging_target_disabled_by_default(tmp_path):
    from research_vault.adapters.observability import resolve_run_logging_target
    enabled, entity, project = resolve_run_logging_target(_cfg(tmp_path, {"backend": "local"}))
    assert enabled is False


def test_resolve_run_logging_target_parses_entity_project(tmp_path):
    from research_vault.adapters.observability import resolve_run_logging_target
    enabled, entity, project = resolve_run_logging_target(
        _cfg(tmp_path, {"backend": "local", "run_logging": True, "wandb_project": "acme/proj"})
    )
    assert enabled is True
    assert entity == "acme"
    assert project == "proj"


def test_probe_run_logging_disabled_is_ok(tmp_path):
    from research_vault.adapters.observability import probe_run_logging
    ok, msg = probe_run_logging(_cfg(tmp_path, {"backend": "local"}))
    assert ok is True
    assert "disabled" in msg


def test_probe_run_logging_enabled_no_key_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    from research_vault.adapters.observability import probe_run_logging
    ok, msg = probe_run_logging(
        _cfg(tmp_path, {"backend": "local", "run_logging": True, "wandb_project": "e/p"})
    )
    assert ok is False
    assert "WANDB_API_KEY" in msg
