"""test_approve_gate.py — SR-APPROVE-GATE: independent fail-closed proof.

This test file is INDEPENDENT of the conftest _approver_token_env autouse fixture
for the core fail-closed tests — it explicitly controls RV_APPROVER_TOKEN to prove
the gate is not bypassable by environment coercion.

Test plan:
  FC-1  Non-TTY + no token + no fingerprint → rc=1, state UNCHANGED
  FC-2  Non-TTY + token set + NO fingerprint in config → rc=1, state UNCHANGED
        (fingerprint must be provisioned for the token path to activate)
  FC-3  Non-TTY + token set + WRONG fingerprint in config → rc=1, state UNCHANGED
  FC-4  Non-TTY + token set + MATCHING fingerprint → rc=0, state SUCCEEDED
  FC-5  Non-TTY + token set + matching fingerprint + --reject → rc=0, state BLOCKED
        (gate covers --reject too)
  FC-6  Non-TTY + --yes flag + no token → rc=1, state UNCHANGED
        (--yes is ignored when no TTY present)
  TTY-1 TTY-simulated 'y' answer → rc=0, state SUCCEEDED (method=tty)
  TTY-2 TTY-simulated abort answer → rc=1, state UNCHANGED
  PROV  Provenance fields recorded on approve (approved_by, approval_method, approved_at)
  ENF-1 enforce=false + valid sig → gate off, non-TTY approve proceeds
  ENF-2 enforce=false + NO sig (raw toml edit) + token provisioned → gate STILL ENFORCED
  ENF-3 enforce=false + invalid sig + token provisioned → gate STILL ENFORCED
  TOML  _update_toml_approval creates/replaces [approval] section correctly
  CLI   rv approval build_parser: setup/disable/enable/status subverbs exist
  STAT  approval_status_lines: returns correct label and anti-leak warning
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure src is importable.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.dag.approval import (
    check_human_presence,
    compute_fingerprint,
    compute_enforce_sig,
    verify_enforce_sig,
    verify_fingerprint,
    get_config_id,
    approval_status_lines,
    _FAIL_CLOSED_MSG,
    _SECRET_NAME,
)
from research_vault.dag.verbs import cmd_approve, cmd_run, cmd_tick, cmd_complete
from research_vault.dag.store import RunStore
from research_vault.config import Config, reset_config_cache
from research_vault.adapters.base import EnvSecretStore

# ─── Constants ───────────────────────────────────────────────────────────────

_TEST_TOKEN = "test-gate-token-independent"
_TEST_FP = compute_fingerprint(_TEST_TOKEN)

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _argns(**kwargs: Any) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def _minimal_cfg(tmp_path: Path, *, token_fingerprint: str = "", enforce: bool = True,
                 enforce_sig: str = "") -> Config:
    """Build a minimal Config with the given approval block (no real TOML file)."""
    raw: dict[str, Any] = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {},
        "approval": {
            "enforce": enforce,
            "token_fingerprint": token_fingerprint,
            "enforce_sig": enforce_sig,
        },
    }
    return Config(raw)


def _cfg_with_fp(tmp_path: Path, fingerprint: str = _TEST_FP) -> Config:
    """Config with a provisioned fingerprint."""
    return _minimal_cfg(tmp_path, token_fingerprint=fingerprint)


def _cfg_no_fp(tmp_path: Path) -> Config:
    """Config with no fingerprint (typical missing-setup state)."""
    return _minimal_cfg(tmp_path, token_fingerprint="")


def _setup_awaiting_go_run(tmp_path: Path) -> str:
    """Create a run with a human-go gate in awaiting-go state.
    Returns the run_id. Caller MUST have RESEARCH_VAULT_CONFIG set.
    """
    run_id = "gate-test-run"
    manifest = {
        "run_id": run_id,
        "name": "gate test",
        "global_cap": 4,
        "nodes": [
            {"id": "work", "type": "agent", "spec": "test://x", "label": "work"},
            {
                "id": "gate",
                "type": "human-go",
                "label": "gate",
                "needs": [{"from": "work", "edge": "afterok"}],
            },
        ],
    }
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(manifest), encoding="utf-8")
    cmd_run(_argns(manifest=str(mf)))
    cmd_complete(_argns(run_id=run_id, node_id="work", status="succeeded"))
    cmd_tick(_argns(run_id=run_id))
    return run_id


def _build_toml_config(tmp_path: Path, *, token_fingerprint: str = _TEST_FP,
                       enforce: bool = True, enforce_sig: str = "") -> Path:
    """Write a research_vault.toml and set RESEARCH_VAULT_CONFIG. Returns config path."""
    cfg_file = tmp_path / "research_vault.toml"
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "notes").mkdir(exist_ok=True)
    cfg_file.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{tmp_path / "notes"}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
        f'[approval]\nenforce = {"true" if enforce else "false"}\n'
        f'token_fingerprint = "{token_fingerprint}"\n'
        f'enforce_sig = "{enforce_sig}"\n',
        encoding="utf-8",
    )
    os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)
    reset_config_cache()
    return cfg_file


# ─── FC-1: no token, no fingerprint → fail closed ────────────────────────────

class TestFC1NoTokenNoFingerprint:
    """Non-TTY + no token + no fingerprint → fail closed, state unchanged."""

    def test_check_human_presence_no_token_no_fp(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = _cfg_no_fp(tmp_path)
        secrets = EnvSecretStore()
        args = _argns(run_id="r1", node_id="n1", yes=False, reject=False)
        ok, method, approver, reason = check_human_presence(args, cfg, secrets)
        assert not ok, "Must fail closed when no token and no fingerprint"
        assert method == ""
        assert "[crew-cannot-self-approve]" in reason
        assert "rv onboard" in reason, (
            "Fail-closed message must point first-time users at `rv onboard` "
            "(provisions the inline-approval token) — not just 'rv approval setup'."
        )

    def test_cmd_approve_returns_1_state_unchanged(self, tmp_path, monkeypatch, capsys):
        """FC-1 end-to-end: cmd_approve returns 1, node stays awaiting-go."""
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        _build_toml_config(tmp_path, token_fingerprint="")
        run_id = _setup_awaiting_go_run(tmp_path)
        capsys.readouterr()

        rc = cmd_approve(_argns(run_id=run_id, node_id="gate"))
        assert rc == 1, "Must return 1 when no token and no fingerprint"

        # State UNCHANGED — still awaiting-go, not succeeded or blocked.
        store = RunStore(tmp_path / "state")
        rs = store.load(run_id)
        assert rs.node_status("gate") == "awaiting-go", (
            "Gate state must remain awaiting-go after fail-closed; "
            f"got {rs.node_status('gate')!r}"
        )
        err = capsys.readouterr().err
        assert "[crew-cannot-self-approve]" in err
        assert "rv onboard" in err, (
            "cmd_approve's fail-closed stderr must surface `rv onboard` as the "
            "one-time fix for a non-TTY first-time user."
        )

    def test_fail_closed_msg_leads_with_rv_onboard(self):
        """_FAIL_CLOSED_MSG must mention `rv onboard` as the one-time inline-approval
        setup — the lead recommendation for a first-time non-TTY user — while still
        keeping `rv approval setup` / terminal guidance as alternatives.
        """
        msg = _FAIL_CLOSED_MSG.format(run_id="r1", node_id="n1")
        assert "rv onboard" in msg
        assert "rv approval setup" in msg
        assert "At your terminal" in msg or "terminal" in msg


# ─── FC-2: token set but no fingerprint in config → fail closed ──────────────

class TestFC2TokenNoFingerprint:
    """Non-TTY + token set + no fingerprint in config → fail closed."""

    def test_cmd_approve_returns_1_no_fingerprint(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("RV_APPROVER_TOKEN", _TEST_TOKEN)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        _build_toml_config(tmp_path, token_fingerprint="")
        run_id = _setup_awaiting_go_run(tmp_path)
        capsys.readouterr()

        rc = cmd_approve(_argns(run_id=run_id, node_id="gate"))
        assert rc == 1

        store = RunStore(tmp_path / "state")
        rs = store.load(run_id)
        assert rs.node_status("gate") == "awaiting-go", (
            "State must remain awaiting-go when token set but no fingerprint"
        )


# ─── FC-3: wrong fingerprint → fail closed ───────────────────────────────────

class TestFC3WrongFingerprint:
    """Non-TTY + token set + WRONG fingerprint → fail closed, state unchanged."""

    def test_cmd_approve_returns_1_wrong_fp(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("RV_APPROVER_TOKEN", _TEST_TOKEN)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        _build_toml_config(tmp_path, token_fingerprint="deadbeef" * 8)  # wrong fp
        run_id = _setup_awaiting_go_run(tmp_path)
        capsys.readouterr()

        rc = cmd_approve(_argns(run_id=run_id, node_id="gate"))
        assert rc == 1

        store = RunStore(tmp_path / "state")
        rs = store.load(run_id)
        assert rs.node_status("gate") == "awaiting-go"
        err = capsys.readouterr().err
        assert "mismatch" in err or "authorized" in err


# ─── FC-4: correct token + matching fingerprint → succeeds ───────────────────

class TestFC4CorrectToken:
    """Non-TTY + token set + matching fingerprint → approval succeeds."""

    def test_cmd_approve_succeeds_with_token(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("RV_APPROVER_TOKEN", _TEST_TOKEN)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        _build_toml_config(tmp_path, token_fingerprint=_TEST_FP)
        run_id = _setup_awaiting_go_run(tmp_path)
        capsys.readouterr()

        rc = cmd_approve(_argns(run_id=run_id, node_id="gate"))
        assert rc == 0, "Approval must succeed with matching token"

        store = RunStore(tmp_path / "state")
        rs = store.load(run_id)
        assert rs.node_status("gate") == "succeeded"
        ns = rs.node_states.get("gate", {})
        assert ns.get("approval_method") == "token"
        assert ns.get("approved_by", "").startswith("token:")
        assert ns.get("approved_at"), "approved_at must be recorded"


# ─── FC-5: correct token + --reject → blocked, gate covered ─────────────────

class TestFC5RejectGate:
    """Non-TTY + token + --reject → state becomes blocked (gate covers --reject)."""

    def test_cmd_approve_reject_covered_by_gate(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("RV_APPROVER_TOKEN", _TEST_TOKEN)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        _build_toml_config(tmp_path, token_fingerprint=_TEST_FP)
        run_id = _setup_awaiting_go_run(tmp_path)
        capsys.readouterr()

        rc = cmd_approve(_argns(run_id=run_id, node_id="gate", reject=True))
        assert rc == 0

        store = RunStore(tmp_path / "state")
        rs = store.load(run_id)
        assert rs.node_status("gate") == "blocked"

    def test_no_token_reject_also_blocked(self, tmp_path, monkeypatch, capsys):
        """--reject with no valid credential also fails closed (state unchanged)."""
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        _build_toml_config(tmp_path, token_fingerprint="")
        run_id = _setup_awaiting_go_run(tmp_path)
        capsys.readouterr()

        rc = cmd_approve(_argns(run_id=run_id, node_id="gate", reject=True))
        assert rc == 1

        store = RunStore(tmp_path / "state")
        rs = store.load(run_id)
        assert rs.node_status("gate") == "awaiting-go", (
            "Reject without credentials must also fail closed"
        )


# ─── FC-6: --yes with no TTY → still fails closed ───────────────────────────

class TestFC6YesFlagNoTTY:
    """--yes is ignored when no TTY present — still fails closed."""

    def test_yes_flag_ignored_without_tty(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        _build_toml_config(tmp_path, token_fingerprint="")
        run_id = _setup_awaiting_go_run(tmp_path)
        capsys.readouterr()

        # --yes with no TTY and no token → must still fail closed.
        rc = cmd_approve(_argns(run_id=run_id, node_id="gate", yes=True))
        assert rc == 1, "--yes must not bypass the gate when no TTY is present"

        store = RunStore(tmp_path / "state")
        rs = store.load(run_id)
        assert rs.node_status("gate") == "awaiting-go"

    def test_check_human_presence_yes_no_tty(self, tmp_path, monkeypatch):
        """check_human_presence: --yes with no TTY → not ok."""
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = _cfg_no_fp(tmp_path)
        args = _argns(run_id="r", node_id="n", yes=True, reject=False)
        ok, _, _, _ = check_human_presence(args, cfg, EnvSecretStore())
        assert not ok


# ─── TTY-1: TTY with 'y' → succeeds ─────────────────────────────────────────

class TestTTY1Simulated:
    """TTY simulated via monkeypatching stdin.isatty + input."""

    def test_tty_y_approves(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _prompt: "y")

        cfg = _cfg_no_fp(tmp_path)
        args = _argns(run_id="r", node_id="n", yes=False, reject=False)
        ok, method, approver, reason = check_human_presence(args, cfg, EnvSecretStore())
        assert ok
        assert method == "tty"
        assert approver == "operator"

    def test_tty_enter_approves(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _prompt: "")

        cfg = _cfg_no_fp(tmp_path)
        args = _argns(run_id="r", node_id="n", yes=False, reject=False)
        ok, method, _, _ = check_human_presence(args, cfg, EnvSecretStore())
        assert ok
        assert method == "tty"

    def test_tty_yes_flag_skips_prompt(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        # input should NOT be called (--yes skips the prompt at TTY)
        monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(RuntimeError("input() was called")))

        cfg = _cfg_no_fp(tmp_path)
        args = _argns(run_id="r", node_id="n", yes=True, reject=False)
        ok, method, _, _ = check_human_presence(args, cfg, EnvSecretStore())
        assert ok
        assert method == "tty"


# ─── TTY-2: TTY abort → fail closed ─────────────────────────────────────────

class TestTTY2Abort:
    """TTY with abort answer → fail closed (state unchanged)."""

    def test_tty_n_aborts(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")

        cfg = _cfg_no_fp(tmp_path)
        args = _argns(run_id="r", node_id="n", yes=False, reject=False)
        ok, method, _, reason = check_human_presence(args, cfg, EnvSecretStore())
        assert not ok
        assert method == ""
        assert "aborted" in reason or "state unchanged" in reason


# ─── PROV: provenance fields ─────────────────────────────────────────────────

class TestProvenance:
    """Approval writes approved_by / approval_method / approved_at into node_states."""

    def test_tty_provenance(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("RV_APPROVER_TOKEN", _TEST_TOKEN)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        _build_toml_config(tmp_path, token_fingerprint=_TEST_FP)
        run_id = _setup_awaiting_go_run(tmp_path)
        capsys.readouterr()

        rc = cmd_approve(_argns(run_id=run_id, node_id="gate"))
        assert rc == 0

        store = RunStore(tmp_path / "state")
        rs = store.load(run_id)
        ns = rs.node_states.get("gate", {})
        assert ns.get("approval_method") == "token", f"method wrong: {ns!r}"
        assert ns.get("approved_by", "").startswith("token:"), f"approver wrong: {ns!r}"
        assert ns.get("approved_at"), f"approved_at missing: {ns!r}"

    def test_reject_provenance(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("RV_APPROVER_TOKEN", _TEST_TOKEN)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        _build_toml_config(tmp_path, token_fingerprint=_TEST_FP)
        run_id = _setup_awaiting_go_run(tmp_path)
        capsys.readouterr()

        rc = cmd_approve(_argns(run_id=run_id, node_id="gate", reject=True))
        assert rc == 0

        store = RunStore(tmp_path / "state")
        rs = store.load(run_id)
        ns = rs.node_states.get("gate", {})
        assert ns.get("approval_method") == "token"
        assert ns.get("approved_at")


# ─── ENF-1: signed enforce=false → gate off ──────────────────────────────────

class TestEnfDisableSigned:
    """Slice 3: enforce=false with valid sig → gate disabled."""

    def test_signed_disable_allows_non_tty_approve(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("RV_APPROVER_TOKEN", _TEST_TOKEN)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")

        # Build the signed enforce_sig.
        config_id = str((tmp_path / "research_vault.toml").resolve())
        sig = compute_enforce_sig(_TEST_TOKEN, config_id)

        _build_toml_config(
            tmp_path,
            token_fingerprint=_TEST_FP,
            enforce=False,
            enforce_sig=sig,
        )
        run_id = _setup_awaiting_go_run(tmp_path)
        capsys.readouterr()

        rc = cmd_approve(_argns(run_id=run_id, node_id="gate"))
        assert rc == 0, "Signed disable must allow non-TTY approve"

        store = RunStore(tmp_path / "state")
        rs = store.load(run_id)
        assert rs.node_status("gate") == "succeeded"


# ─── ENF-2: raw toml enforce=false without sig → gate STILL ENFORCED ────────

class TestEnfRawTomlEditInert:
    """Slice 3: raw toml enforce=false with no sig and no token → inert.

    The scenario: an agent edits the toml to enforce=false but does NOT have
    RV_APPROVER_TOKEN.  Without a valid sig AND without the token, the approval
    must be refused (the raw toml edit gives the agent nothing extra).

    The signed-disable mechanism protects against this: when a token IS provisioned
    in config, the gate checks the enforce_sig keyed on that token.  No sig +
    no token in env → sig verification fails → gate stays enforced.
    """

    def test_raw_toml_edit_inert_no_env_token(self, tmp_path, monkeypatch, capsys):
        """Agent edits enforce=false in toml, has NO RV_APPROVER_TOKEN → refused."""
        # Agent does NOT have the token in their env.
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")

        # Config: fingerprint provisioned (token exists somewhere), enforce=false, no sig.
        _build_toml_config(
            tmp_path,
            token_fingerprint=_TEST_FP,
            enforce=False,
            enforce_sig="",  # no sig — the raw toml edit case
        )
        run_id = _setup_awaiting_go_run(tmp_path)
        capsys.readouterr()

        rc = cmd_approve(_argns(run_id=run_id, node_id="gate"))
        assert rc == 1, (
            "enforce=false with no sig and no token in env MUST be refused. "
            "A raw toml edit is inert — this is the load-bearing security test."
        )

        store = RunStore(tmp_path / "state")
        rs = store.load(run_id)
        assert rs.node_status("gate") == "awaiting-go", (
            "State must remain awaiting-go after the inert toml-edit attempt"
        )

    def test_raw_toml_edit_with_valid_token_uses_token_path(self, tmp_path, monkeypatch, capsys):
        """If the agent ALSO has the token, approve succeeds via the token path.

        The inert-toml-edit protection is specifically against agents that edit
        the toml but don't have the token.  An agent WITH the token could always
        approve via the standard token path — the signed-disable is not designed
        to protect against a same-credential adversary.
        """
        monkeypatch.setenv("RV_APPROVER_TOKEN", _TEST_TOKEN)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")

        # Config: enforce=false + no sig, but the agent also has the token.
        _build_toml_config(
            tmp_path,
            token_fingerprint=_TEST_FP,
            enforce=False,
            enforce_sig="",
        )
        run_id = _setup_awaiting_go_run(tmp_path)
        capsys.readouterr()

        # The enforce=false fails sig-check → gate resets to enforce=True.
        # The token then satisfies the token-path → approve succeeds.
        rc = cmd_approve(_argns(run_id=run_id, node_id="gate"))
        assert rc == 0, (
            "An agent WITH the token can still approve via the token path "
            "even when enforce=false has no sig — the gate falls back to token-path."
        )
        store = RunStore(tmp_path / "state")
        rs = store.load(run_id)
        assert rs.node_status("gate") == "succeeded"


# ─── ENF-3: invalid sig → gate still enforced ────────────────────────────────

class TestEnfInvalidSig:
    """enforce=false + invalid sig + no token in env → gate still enforced.

    This proves that writing a wrong enforce_sig (or any arbitrary value) to
    the toml doesn't create a signed-disable bypass, even if the config block
    says enforce=false.  The sig is verified against the provisioned token; an
    agent without the token cannot forge a valid sig.
    """

    def test_invalid_sig_no_env_token_enforced(self, tmp_path, monkeypatch, capsys):
        """enforce=false + invalid sig + no env token → gate still enforced."""
        # Agent doesn't have the token.
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")

        _build_toml_config(
            tmp_path,
            token_fingerprint=_TEST_FP,
            enforce=False,
            enforce_sig="badsig" * 10,  # wrong sig — can't forge without the token
        )
        run_id = _setup_awaiting_go_run(tmp_path)
        capsys.readouterr()

        rc = cmd_approve(_argns(run_id=run_id, node_id="gate"))
        assert rc == 1, "Invalid sig without env token must be refused"

        store = RunStore(tmp_path / "state")
        rs = store.load(run_id)
        assert rs.node_status("gate") == "awaiting-go"


# ─── TOML: _update_toml_approval ─────────────────────────────────────────────

class TestTomlUpdate:
    """_update_toml_approval writes and replaces [approval] section correctly."""

    def test_creates_section_when_absent(self, tmp_path):
        from research_vault.approval import _update_toml_approval
        cfg_file = tmp_path / "research_vault.toml"
        cfg_file.write_text('[adapters]\nnotifier = "file"\n', encoding="utf-8")
        _update_toml_approval(cfg_file, {"enforce": True, "token_fingerprint": "abc", "enforce_sig": ""})
        text = cfg_file.read_text(encoding="utf-8")
        assert "[approval]" in text
        assert 'token_fingerprint = "abc"' in text
        assert "enforce = true" in text
        # Original section must still be present.
        assert "[adapters]" in text

    def test_replaces_existing_section(self, tmp_path):
        from research_vault.approval import _update_toml_approval
        cfg_file = tmp_path / "research_vault.toml"
        cfg_file.write_text(
            '[adapters]\nnotifier = "file"\n'
            '[approval]\nenforce = true\ntoken_fingerprint = "old"\nenforce_sig = ""\n',
            encoding="utf-8",
        )
        _update_toml_approval(cfg_file, {"enforce": False, "token_fingerprint": "new", "enforce_sig": "sig"})
        text = cfg_file.read_text(encoding="utf-8")
        assert 'token_fingerprint = "new"' in text
        assert 'token_fingerprint = "old"' not in text
        assert "enforce = false" in text
        assert "[adapters]" in text  # untouched

    def test_section_count_one(self, tmp_path):
        """Replacement must leave exactly one [approval] header."""
        from research_vault.approval import _update_toml_approval
        cfg_file = tmp_path / "research_vault.toml"
        cfg_file.write_text(
            '[approval]\nenforce = true\ntoken_fingerprint = "x"\nenforce_sig = ""\n',
            encoding="utf-8",
        )
        _update_toml_approval(cfg_file, {"enforce": True, "token_fingerprint": "y", "enforce_sig": ""})
        text = cfg_file.read_text(encoding="utf-8")
        assert text.count("[approval]") == 1


# ─── CLI: parser subverbs ────────────────────────────────────────────────────

class TestCLIParser:
    """rv approval build_parser: all four subverbs exist."""

    def test_setup_subverb(self):
        from research_vault.approval import build_parser
        p = build_parser()
        args = p.parse_args(["setup"])
        assert args.approval_cmd == "setup"
        assert args.keyring is False

    def test_disable_subverb(self):
        from research_vault.approval import build_parser
        p = build_parser()
        args = p.parse_args(["disable"])
        assert args.approval_cmd == "disable"

    def test_enable_subverb(self):
        from research_vault.approval import build_parser
        p = build_parser()
        args = p.parse_args(["enable"])
        assert args.approval_cmd == "enable"

    def test_status_subverb(self):
        from research_vault.approval import build_parser
        p = build_parser()
        args = p.parse_args(["status"])
        assert args.approval_cmd == "status"

    def test_yes_flag_in_registry(self):
        """--yes must be a recognized flag for rv dag approve."""
        from research_vault.dag.verbs import build_parser as dag_build_parser
        p = dag_build_parser()
        sub = p._subparsers._actions[-1]
        app_p = sub.choices["approve"]
        args = app_p.parse_args(["run-1", "node-1", "--yes"])
        assert args.yes is True


# ─── STAT: approval_status_lines ─────────────────────────────────────────────

class TestStatusLines:
    """approval_status_lines returns correct labels and anti-leak warning."""

    def test_enforce_on_no_token(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        cfg = _minimal_cfg(tmp_path)
        lines = approval_status_lines(cfg, EnvSecretStore())
        assert any("enforce=on" in l for l in lines)
        assert any("token=absent" in l for l in lines)

    def test_enforce_on_token_provisioned(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RV_APPROVER_TOKEN", _TEST_TOKEN)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = _cfg_with_fp(tmp_path)
        lines = approval_status_lines(cfg, EnvSecretStore())
        assert any("enforce=on" in l for l in lines)
        assert any("token=provisioned" in l for l in lines)

    def test_enforce_off_signed_label(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RV_APPROVER_TOKEN", _TEST_TOKEN)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = _minimal_cfg(tmp_path, token_fingerprint=_TEST_FP, enforce=False, enforce_sig="fakesig")
        lines = approval_status_lines(cfg, EnvSecretStore())
        assert any("enforce=off (signed)" in l for l in lines)

    def test_enforce_off_unsigned_label(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = _minimal_cfg(tmp_path, enforce=False, enforce_sig="")
        lines = approval_status_lines(cfg, EnvSecretStore())
        assert any("trust-me mode" in l for l in lines)

    def test_anti_leak_warning_fires(self, tmp_path, monkeypatch):
        """Warning fires when RV_APPROVER_TOKEN is set as a plain env var."""
        monkeypatch.setenv("RV_APPROVER_TOKEN", "leaked-token")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = _minimal_cfg(tmp_path)
        lines = approval_status_lines(cfg, EnvSecretStore())
        assert any("WARNING" in l and "RV_APPROVER_TOKEN" in l for l in lines)

    def test_no_anti_leak_warning_when_not_set(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = _minimal_cfg(tmp_path)
        lines = approval_status_lines(cfg, EnvSecretStore())
        assert not any("WARNING" in l for l in lines)


# ─── Fingerprint helpers ──────────────────────────────────────────────────────

class TestFingerprintHelpers:
    """compute_fingerprint / verify_fingerprint / compute_enforce_sig."""

    def test_fingerprint_stable(self):
        fp = compute_fingerprint("mytoken")
        assert fp == compute_fingerprint("mytoken")

    def test_fingerprint_verify_match(self):
        fp = compute_fingerprint("abc")
        assert verify_fingerprint("abc", fp)

    def test_fingerprint_verify_mismatch(self):
        fp = compute_fingerprint("abc")
        assert not verify_fingerprint("xyz", fp)

    def test_enforce_sig_roundtrip(self):
        sig = compute_enforce_sig("tok", "cfg-id-1")
        assert verify_enforce_sig("tok", "cfg-id-1", sig)

    def test_enforce_sig_wrong_token(self):
        sig = compute_enforce_sig("tok", "cfg-id-1")
        assert not verify_enforce_sig("wrong", "cfg-id-1", sig)

    def test_enforce_sig_wrong_config_id(self):
        sig = compute_enforce_sig("tok", "cfg-id-1")
        assert not verify_enforce_sig("tok", "cfg-id-2", sig)

    def test_enforce_sig_empty_is_false(self):
        assert not verify_enforce_sig("tok", "cid", "")
