"""test_model_seam_live.py — SR-MODEL-SEAM S5/S6: LIVE, no-mock acceptance tests.

These make REAL model calls (and, for weave/Plane-B, hit W&B). They are marked
``live`` and SKIP cleanly without the required API keys — CI (disabled) and the
default suite never run them. The operator runs them with real keys at final
verification:

    pytest -m live -q                       # all live acceptance tests
    pytest tests/test_model_seam_live.py -m live

Required env:
  - a provider key (ANTHROPIC_API_KEY) for every test here.
  - WANDB_API_KEY additionally for the weave (S5) + Plane-B round-trip (S6) tests.
  - WANDB_ENTITY / WANDB_PROJECT (or [observability].wandb_project) for the
    round-trip so the run has a home + `rv wandb pull` can read it back.

No mocks: a real ``adapters.model.complete`` call flows through litellm to the
provider, the observability backend, and (S6) a classic W&B run.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


_MODEL = os.environ.get("RV_LIVE_MODEL", "claude-3-5-haiku-latest")


def _has_provider_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _has_wandb_key() -> bool:
    return bool(os.environ.get("WANDB_API_KEY", "").strip())


def _live_cfg(tmp_path: Path, observability: dict) -> "object":
    from research_vault.config import Config
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


@pytest.fixture(autouse=True)
def _clean_litellm_callbacks():
    """Reset litellm callbacks around each live test."""
    import litellm
    saved = list(getattr(litellm, "callbacks", []) or [])
    litellm.callbacks = []
    yield
    litellm.callbacks = saved


# ---------------------------------------------------------------------------
# S5 — Plane A: backend=local → a line in llm_calls.jsonl
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_provider_key(), reason="ANTHROPIC_API_KEY not set")
def test_live_local_backend_writes_jsonl_line(tmp_path):
    from research_vault.adapters import load_adapters

    cfg = _live_cfg(tmp_path, {"backend": "local"})
    adapters = load_adapters(cfg)
    resp = adapters.model.complete(
        model=_MODEL,
        messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        max_tokens=8,
    )
    assert resp is not None
    # Emission counter fired (seam observed):
    adapters.model.assert_observed()  # must NOT warn/raise
    assert adapters.model.stats.events >= 1
    # Plane-A local trace line landed:
    jsonl = cfg.state_dir / "llm_calls.jsonl"
    assert jsonl.exists()
    lines = [ln for ln in jsonl.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 1


# ---------------------------------------------------------------------------
# S5 — Plane A: backend=weave → a trace (weave armed + counter fired)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (_has_provider_key() and _has_wandb_key()),
    reason="ANTHROPIC_API_KEY + WANDB_API_KEY required for the weave trace test",
)
def test_live_weave_backend_produces_trace(tmp_path):
    pytest.importorskip("weave")
    project = os.environ.get("RV_LIVE_WEAVE_PROJECT")
    if not project:
        entity = os.environ.get("WANDB_ENTITY", "").strip()
        proj = os.environ.get("WANDB_PROJECT", "").strip()
        if not (entity and proj):
            pytest.skip("Set RV_LIVE_WEAVE_PROJECT or WANDB_ENTITY+WANDB_PROJECT")
        project = f"{entity}/{proj}"

    from research_vault.adapters import load_adapters

    cfg = _live_cfg(tmp_path, {"backend": "weave", "wandb_project": project})
    adapters = load_adapters(cfg)
    # First .model access arms weave.init(project) — must not raise under require.
    adapters.require_observability = True
    resp = adapters.model.complete(
        model=_MODEL,
        messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        max_tokens=8,
    )
    assert resp is not None
    # The seam + weave callback fired end-to-end (counter accrued the call).
    adapters.model.assert_observed()  # backend=weave, calls>0, counter>0 → no raise
    assert adapters.model.stats.events >= 1
    assert adapters.model.stats.total_tokens > 0


# ---------------------------------------------------------------------------
# S6 — Plane B: classic W&B run round-trip (logged → rv wandb pull reads it back)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (_has_provider_key() and _has_wandb_key()),
    reason="ANTHROPIC_API_KEY + WANDB_API_KEY required for the Plane-B round-trip",
)
def test_live_plane_b_run_roundtrip(tmp_path):
    """Seam opens a wandb run, ≥1 real call, logs aggregates + a known metric +
    model/seed, finishes; then `rv wandb pull` reads it back with repro_* populated."""
    import wandb  # noqa: F401 — core dep

    entity = os.environ.get("WANDB_ENTITY", "").strip()
    proj = os.environ.get("WANDB_PROJECT", "").strip()
    if not (entity and proj):
        pytest.skip("Set WANDB_ENTITY + WANDB_PROJECT for the round-trip test")

    from research_vault.adapters import load_adapters
    from research_vault.experiment_run import log_experiment_run

    cfg = _live_cfg(tmp_path, {
        "backend": "local",
        "run_logging": True,
        "wandb_project": f"{entity}/{proj}",
    })
    adapters = load_adapters(cfg)

    known_metric = {"demo_accuracy": 0.4242}
    pre_reg = {"model": _MODEL, "seed": 12345, "temperature": 0.0}

    # log_experiment_run: init → real call(s) via the seam → summary+config → finish.
    def _do_calls(model_client):
        model_client.complete(
            model=_MODEL,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=8,
        )

    run_path = log_experiment_run(
        cfg,
        adapters,
        config_params=pre_reg,
        analysis_metrics=known_metric,
        run_fn=_do_calls,
        run_name=f"rv-seam-live-{uuid.uuid4().hex[:8]}",
    )
    assert run_path  # entity/project/run_id

    # Read it back with rv wandb pull's fetch (SDK path).
    from research_vault.wandb_pull import fetch_run, parse_run_id
    ent, prj, run_name = parse_run_id(run_path)
    data = fetch_run(ent, prj, run_name, os.environ["WANDB_API_KEY"])

    summary = data["summaryMetrics"]
    assert summary.get("calls", 0) >= 1
    assert summary.get("total_tokens", 0) > 0
    assert abs(float(summary.get("demo_accuracy", 0)) - 0.4242) < 1e-6
    cfgback = data["config"]
    assert cfgback.get("model") == _MODEL
    assert int(cfgback.get("seed")) == 12345
