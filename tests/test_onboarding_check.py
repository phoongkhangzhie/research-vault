"""test_onboarding_check.py — S1: the `rv check` reframe (F1/F2/F3).

The corrected required-model: the agent runtime is the ONLY hard
requirement.  A fresh adopter with the runtime and zero keys → rv check GREEN
(exit 0), every feature shown "locked", never FAIL.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


def _env_without_any_keys() -> dict[str, str]:
    """A clean env with every credential env-var stripped."""
    drop = {
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
        "MISTRAL_API_KEY", "COHERE_API_KEY", "GROQ_API_KEY", "TOGETHER_API_KEY",
        "S2_API_KEY", "ASTA_MCP_KEY", "WANDB_API_KEY", "ZOTERO_KEY",
    }
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["VAULT_SKIP_KEYRING"] = "1"  # ignore the dev machine's real keyring
    return env


# ---------------------------------------------------------------------------
# F3 — the load-bearing fix: GREEN with runtime + zero keys
# ---------------------------------------------------------------------------

def test_f3_green_with_runtime_and_no_keys():
    """A fresh adopter (runtime present, NO keys) → all_required_ok True, exit-0 path."""
    from research_vault.check import run_preflight
    with patch.dict(os.environ, _env_without_any_keys(), clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()
    assert result["all_required_ok"] is True, (
        "runtime present + zero keys MUST be OK (F3). Report:\n" + result["report"]
    )
    assert result["required_failed"] == []
    assert result["api_key"] is False  # no provider key — but that does NOT fail the gate
    assert "Result: OK" in result["report"]


def test_f3_run_returns_exit_zero_with_no_keys():
    """The `rv check` verb returns exit 0 for runtime + zero keys."""
    import argparse
    from research_vault.check import run
    with patch.dict(os.environ, _env_without_any_keys(), clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            rc = run(argparse.Namespace(require_observability=False, rich=False))
    assert rc == 0


def test_f3_missing_provider_key_is_locked_never_fail():
    """A missing provider key is a LOCKED feature, never a FAIL item."""
    from research_vault.check import run_preflight
    with patch.dict(os.environ, _env_without_any_keys(), clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()
    provider = next(f for f in result["features"] if f["id"] == "provider")
    assert provider["status"] == "locked"
    assert provider["class"] == "FEATURE-REQUIRED"
    # The provider key must NOT appear in the required-failed culprit list.
    assert "provider" not in " ".join(result["required_failed"]).lower()


def test_f3_runtime_missing_is_the_only_fail():
    """When the runtime is absent, it is THE required failure (exit 1 path)."""
    from research_vault.check import run_preflight
    with patch.dict(os.environ, _env_without_any_keys(), clear=True):
        with patch("shutil.which", return_value=None):
            result = run_preflight()
    assert result["all_required_ok"] is False
    assert any("runtime" in c.lower() for c in result["required_failed"])
    assert "Result: FAIL" in result["report"]


# ---------------------------------------------------------------------------
# F1 — the result names its own culprits inline
# ---------------------------------------------------------------------------

def test_f1_required_failed_travels_with_result():
    from research_vault.check import run_preflight
    with patch.dict(os.environ, _env_without_any_keys(), clear=True):
        with patch("shutil.which", return_value=None):
            result = run_preflight()
    assert isinstance(result["required_failed"], list)
    assert result["required_failed"], "culprit list must be non-empty on FAIL"
    # The culprit name is echoed into the Result line (travels with the result).
    culprit = result["required_failed"][0]
    assert culprit in result["report"]


# ---------------------------------------------------------------------------
# F2 — three-class framing; Zotero contradiction killed
# ---------------------------------------------------------------------------

def test_f2_no_zotero_optional_required_contradiction():
    """The old 'ZOTERO_KEY: NOT SET (optional) ... Required for' contradiction is gone."""
    from research_vault.check import run_preflight
    with patch.dict(os.environ, _env_without_any_keys(), clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()
    report = result["report"]
    # No line simultaneously says "(optional)" AND "Required for".
    for line in report.splitlines():
        assert not ("(optional)" in line and "Required for" in line), (
            f"contradictory optional/required framing survived: {line}"
        )
    # One framing: cite is what zotero unlocks.
    zot = next(f for f in result["features"] if f["id"] == "zotero")
    assert "rv cite" in zot["unlocks"]


def test_f2_every_feature_shows_request_url_when_locked():
    from research_vault.check import run_preflight
    with patch.dict(os.environ, _env_without_any_keys(), clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()
    for feat in result["features"]:
        if feat["status"] == "locked" and feat["kind"] in ("key", "package"):
            assert feat["urls"], f"{feat['id']} locked but no request URL shown"
            for u in feat["urls"]:
                assert u["url"].startswith("https://")


def test_f2_asta_note_surfaces_institutional_email():
    from research_vault.check import run_preflight
    with patch.dict(os.environ, _env_without_any_keys(), clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()
    asta = next(f for f in result["features"] if f["id"] == "asta")
    # asta may be installed on the dev box; the NOTE must still be attached.
    assert "institutional" in asta["note"].lower()


def test_f2_all_features_present_in_report():
    """Every feature title appears in the plain report (no silent drop)."""
    from research_vault.check import run_preflight
    with patch.dict(os.environ, _env_without_any_keys(), clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()
    for feat in result["features"]:
        assert feat["title"] in result["report"], f"{feat['id']} missing from report"


# ---------------------------------------------------------------------------
# asta detection by key, not import (bug-guard for the pip-import regression)
# ---------------------------------------------------------------------------

def test_check_asta_uses_no_import():
    """_check_asta must NOT import asta — asta is NOT a pip package."""
    import ast, textwrap, inspect
    from research_vault.check import _check_asta
    src = textwrap.dedent(inspect.getsource(_check_asta))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "asta", (
                    "_check_asta must not contain 'import asta' — asta is not a pip package"
                )
        if isinstance(node, ast.ImportFrom):
            assert node.module != "asta", (
                "_check_asta must not contain 'from asta import ...' — asta is not a pip package"
            )


def test_check_asta_available_when_key_present(monkeypatch):
    """_check_asta reports available when ASTA_MCP_KEY is set."""
    monkeypatch.setenv("ASTA_MCP_KEY", "testastakey123")
    monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
    from research_vault.check import _check_asta
    ok, msg, required = _check_asta()
    assert ok is True
    assert "available" in msg.lower()
    assert required is False


def test_check_asta_locked_when_key_absent(monkeypatch):
    """_check_asta reports no access (with request URL) when key is absent."""
    monkeypatch.delenv("ASTA_MCP_KEY", raising=False)
    monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
    from research_vault.check import _check_asta
    ok, msg, required = _check_asta()
    assert ok is False
    assert "share.hsforms.com" in msg  # the request form URL must be in the message
    assert "institutional" in msg.lower()
    assert required is False


def test_check_asta_round_trip_via_keyring(monkeypatch):
    """Key stored by onboard (store_key(ASTA_KEY)) resolves in _check_asta (round-trip)."""
    monkeypatch.delenv("ASTA_MCP_KEY", raising=False)
    monkeypatch.delenv("VAULT_SKIP_KEYRING", raising=False)

    # Inject a fake keyring backend.
    import keyring as _kr
    class _FakeKR:
        _store: dict = {}
        def get_password(self, svc, usr): return self._store.get((svc, usr))
        def set_password(self, svc, usr, val): self._store[(svc, usr)] = val
    fk = _FakeKR()
    monkeypatch.setattr(_kr, "get_password", fk.get_password)
    monkeypatch.setattr(_kr, "set_password", fk.set_password)

    from research_vault.keys import ASTA_KEY, store_key
    # Write the key exactly as onboard does.
    store_key(ASTA_KEY, "my-asta-key-value")

    # _check_asta must find it.
    from research_vault.check import _check_asta
    ok, msg, _ = _check_asta()
    assert ok is True, f"round-trip failed — _check_asta returned: {msg}"
    assert "keyring" in msg.lower()


def test_asta_feature_status_locked_without_key(monkeypatch):
    """The asta feature status is 'locked' (not 'unlocked') when ASTA_MCP_KEY is absent."""
    from research_vault.check import run_preflight
    with patch.dict(os.environ, _env_without_any_keys(), clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()
    asta = next(f for f in result["features"] if f["id"] == "asta")
    assert asta["status"] == "locked"
    assert asta["kind"] == "key", "asta feature kind must be 'key' after the fix"


def test_asta_feature_status_unlocked_with_key(monkeypatch):
    """The asta feature status is 'unlocked' when ASTA_MCP_KEY is present."""
    from research_vault.check import run_preflight
    env = _env_without_any_keys()
    env["ASTA_MCP_KEY"] = "testastakey123"
    with patch.dict(os.environ, env, clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()
    asta = next(f for f in result["features"] if f["id"] == "asta")
    assert asta["status"] == "unlocked"


# ---------------------------------------------------------------------------
# Back-compat: existing dict contract intact
# ---------------------------------------------------------------------------

def test_backcompat_dict_fields_present():
    from research_vault.check import run_preflight
    with patch.dict(os.environ, _env_without_any_keys(), clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()
    for field in (
        "claude_cli", "api_key", "tier1_missing", "tier2_missing",
        "asta", "zotero", "wandb_key", "observability", "compute_manifest",
        "all_required_ok", "report",
    ):
        assert field in result, f"back-compat field {field} dropped"
