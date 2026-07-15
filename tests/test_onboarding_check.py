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
        if feat["status"] == "locked" and feat["kind"] in ("key", "key_liveness", "package"):
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
    """_check_asta reports available when ASTA_MCP_KEY is set AND the session pings live."""
    monkeypatch.setenv("ASTA_MCP_KEY", "testastakey123")
    monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
    import research_vault.check as check_mod
    monkeypatch.setattr(check_mod, "asta_liveness_probe", lambda: ("live", "gateway confirmed"))
    ok, msg, required = check_mod._check_asta()
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

    # _check_asta must find it AND ping it live (presence alone is not enough).
    import research_vault.check as check_mod
    monkeypatch.setattr(check_mod, "asta_liveness_probe", lambda: ("live", "gateway confirmed"))
    ok, msg, _ = check_mod._check_asta()
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
    # Section G: asta is an OAuth-session credential, verified by a rejects-only
    # liveness ping (not presence-only) — its feature kind is 'key_liveness'.
    assert asta["kind"] == "key_liveness"


def test_asta_feature_status_unlocked_with_key(monkeypatch):
    """The asta feature status is 'unlocked' only when the key is present AND the
    liveness ping confirms the session is live — presence alone is not enough."""
    import research_vault.keys as keys_mod
    monkeypatch.setattr(keys_mod, "asta_liveness_probe", lambda: ("live", "gateway confirmed"))
    from research_vault.check import run_preflight
    env = _env_without_any_keys()
    env["ASTA_MCP_KEY"] = "testastakey123"
    with patch.dict(os.environ, env, clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()
    asta = next(f for f in result["features"] if f["id"] == "asta")
    assert asta["status"] == "unlocked"
    assert "session live" in asta["detail"]


# ---------------------------------------------------------------------------
# Section G — asta liveness (rejects-only, not presence). The load-bearing fix:
# a key present but a DEAD session must be reported LOCKED, never [OK].
# ---------------------------------------------------------------------------

def test_asta_feature_status_locked_when_session_dead(monkeypatch):
    """Key present + gateway rejects the session (invalid_grant/expired) → locked, not unlocked."""
    import research_vault.keys as keys_mod
    monkeypatch.setattr(
        keys_mod, "asta_liveness_probe",
        lambda: ("dead", "gateway rejected the session — run `asta auth login` to reauthenticate"),
    )
    from research_vault.check import run_preflight
    env = _env_without_any_keys()
    env["ASTA_MCP_KEY"] = "testastakey123"
    with patch.dict(os.environ, env, clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()
    asta = next(f for f in result["features"] if f["id"] == "asta")
    assert asta["status"] == "locked", (
        "a present-but-DEAD asta session must be LOCKED, never unlocked/[OK]"
    )
    assert "DEAD" in asta["detail"]
    assert result["asta"] is False


def test_check_asta_reports_fail_when_session_dead(monkeypatch):
    """_check_asta's ok field is False when the key is present but the session is dead."""
    monkeypatch.setenv("ASTA_MCP_KEY", "testastakey123")
    monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
    import research_vault.check as check_mod
    monkeypatch.setattr(
        check_mod, "asta_liveness_probe",
        lambda: ("dead", "gateway rejected the session — run `asta auth login` to reauthenticate"),
    )
    ok, msg, required = check_mod._check_asta()
    assert ok is False, "a DEAD session must never report ok=True"
    assert "dead" in msg.lower()
    assert required is False


def test_asta_feature_status_locked_when_offline_unverified(monkeypatch):
    """Key present + liveness ping can't confirm (offline/timeout) → locked with an
    honest 'not liveness-verified' label — never a crash, never a false [OK]."""
    import research_vault.keys as keys_mod
    monkeypatch.setattr(
        keys_mod, "asta_liveness_probe",
        lambda: ("unverified", "gateway unreachable — could not verify session (offline?)"),
    )
    from research_vault.check import run_preflight
    env = _env_without_any_keys()
    env["ASTA_MCP_KEY"] = "testastakey123"
    with patch.dict(os.environ, env, clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()  # must not raise
    asta = next(f for f in result["features"] if f["id"] == "asta")
    assert asta["status"] == "locked"
    assert "not liveness-verified" in asta["detail"]
    assert result["asta"] is False


def test_check_asta_reports_fail_when_offline_unverified(monkeypatch):
    """_check_asta's ok field is False (never True) when liveness could not be confirmed."""
    monkeypatch.setenv("ASTA_MCP_KEY", "testastakey123")
    monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
    import research_vault.check as check_mod
    monkeypatch.setattr(
        check_mod, "asta_liveness_probe",
        lambda: ("unverified", "gateway unreachable — could not verify session (offline?)"),
    )
    ok, msg, required = check_mod._check_asta()
    assert ok is False
    assert "not verified" in msg.lower() or "unverified" in msg.lower()
    assert required is False


def test_asta_liveness_probe_no_cli_reports_unverified(monkeypatch):
    """asta_liveness_probe: `asta` CLI missing from PATH → 'unverified', never raises."""
    import research_vault.keys as keys_mod
    monkeypatch.setattr(keys_mod.shutil, "which", lambda name: None)
    status, detail = keys_mod.asta_liveness_probe()
    assert status == "unverified"
    assert "PATH" in detail


def test_asta_liveness_probe_not_authenticated_is_dead(monkeypatch):
    """asta_liveness_probe: no local session ('Not authenticated') → 'dead'."""
    import research_vault.keys as keys_mod
    monkeypatch.setattr(keys_mod.shutil, "which", lambda name: "/usr/local/bin/asta")
    monkeypatch.setattr(
        keys_mod, "_run_asta_auth_status",
        lambda path: (0, "❌ Not authenticated\n   Run asta auth login to authenticate\n"),
    )
    status, detail = keys_mod.asta_liveness_probe()
    assert status == "dead"


def test_asta_liveness_probe_invalid_grant_is_dead(monkeypatch):
    """asta_liveness_probe: gateway rejects the session (invalid_grant-style) → 'dead'."""
    import research_vault.keys as keys_mod
    monkeypatch.setattr(keys_mod.shutil, "which", lambda name: "/usr/local/bin/asta")
    fake_output = (
        "Local Token Status   | ✅ Valid\n"
        "Server Verification  | ❌ Invalid\n"
        "   HTTP 401: invalid_grant\n"
    )
    monkeypatch.setattr(keys_mod, "_run_asta_auth_status", lambda path: (0, fake_output))
    status, detail = keys_mod.asta_liveness_probe()
    assert status == "dead"


def test_asta_liveness_probe_valid_is_live(monkeypatch):
    """asta_liveness_probe: gateway confirms → 'live' (the healthy real-world shape)."""
    import research_vault.keys as keys_mod
    monkeypatch.setattr(keys_mod.shutil, "which", lambda name: "/usr/local/bin/asta")
    fake_output = (
        "Local Token Status   | ✅ Valid\n"
        "Server Verification  | ✅ Valid\n"
        "Email                | someone@example.edu\n"
    )
    monkeypatch.setattr(keys_mod, "_run_asta_auth_status", lambda path: (0, fake_output))
    status, detail = keys_mod.asta_liveness_probe()
    assert status == "live"


def test_asta_liveness_probe_connection_error_is_unverified_not_dead(monkeypatch):
    """A network/connection failure during verification must NOT be mis-reported as
    a dead session — it's genuinely unknown (e.g. offline)."""
    import research_vault.keys as keys_mod
    monkeypatch.setattr(keys_mod.shutil, "which", lambda name: "/usr/local/bin/asta")
    fake_output = (
        "Local Token Status   | ✅ Valid\n"
        "Server Verification  | ❌ Invalid\n"
        "   Connection error: [Errno 8] nodename nor servname provided\n"
    )
    monkeypatch.setattr(keys_mod, "_run_asta_auth_status", lambda path: (0, fake_output))
    status, detail = keys_mod.asta_liveness_probe()
    assert status == "unverified", (
        "a connection error must degrade to 'unverified', never a false 'dead'"
    )


def test_asta_liveness_probe_timeout_is_unverified_not_crash(monkeypatch):
    """A liveness-ping timeout degrades gracefully to 'unverified' — never raises."""
    import subprocess
    import research_vault.keys as keys_mod
    monkeypatch.setattr(keys_mod.shutil, "which", lambda name: "/usr/local/bin/asta")

    def _raise_timeout(path):
        raise subprocess.TimeoutExpired(cmd="asta auth status", timeout=15)

    monkeypatch.setattr(keys_mod, "_run_asta_auth_status", _raise_timeout)
    status, detail = keys_mod.asta_liveness_probe()  # must not raise
    assert status == "unverified"


def test_asta_liveness_probe_oserror_is_unverified_not_crash(monkeypatch):
    """An OSError running the CLI degrades gracefully to 'unverified' — never raises."""
    import research_vault.keys as keys_mod
    monkeypatch.setattr(keys_mod.shutil, "which", lambda name: "/usr/local/bin/asta")

    def _raise_oserror(path):
        raise OSError("permission denied")

    monkeypatch.setattr(keys_mod, "_run_asta_auth_status", _raise_oserror)
    status, detail = keys_mod.asta_liveness_probe()  # must not raise
    assert status == "unverified"


def test_asta_liveness_probe_never_imports_asta_module():
    """asta_liveness_probe must NOT `import asta` — asta is a CLI, not a pip package."""
    import ast
    import inspect
    import textwrap
    from research_vault.keys import asta_liveness_probe as fn
    src = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "asta"
        if isinstance(node, ast.ImportFrom):
            assert node.module != "asta"


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
