"""test_onboard_verb.py — S4: `rv onboard` — guided, idempotent, no-echo setup."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class _FakeKeyring:
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, value):
        self.store[(service, username)] = value


@pytest.fixture
def fake_keyring(monkeypatch):
    fk = _FakeKeyring()
    import keyring as _kr
    monkeypatch.setattr(_kr, "get_password", fk.get_password)
    monkeypatch.setattr(_kr, "set_password", fk.set_password)
    return fk


@pytest.fixture
def clean_env(monkeypatch):
    for k in (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "S2_API_KEY", "WANDB_API_KEY", "ZOTERO_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("VAULT_SKIP_KEYRING", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("RV_PLAIN", raising=False)


# ---------------------------------------------------------------------------
# Non-interactive fallback — prints remediation, never prompts, exit 0
# ---------------------------------------------------------------------------

def test_onboard_non_tty_prints_remediation(capsys, clean_env, fake_keyring):
    from research_vault.onboard import cmd_onboard
    with patch("shutil.which", return_value="/usr/bin/claude"):
        rc = cmd_onboard(assume_tty=False)
    out = capsys.readouterr().out
    assert rc == 0
    # Non-interactive path announces itself + prints the env-var remediation.
    assert "non-interactive" in out.lower()
    assert "export ANTHROPIC_API_KEY" in out
    assert "keyring set research-vault anthropic-api-key" in out
    # Explicit lock messaging.
    assert "won't work until you add the key" in out


def test_onboard_print_flag_forces_remediation_on_tty(capsys, clean_env, fake_keyring):
    """--print forces the remediation path even when assume_tty=True."""
    from research_vault.onboard import cmd_onboard
    with patch("shutil.which", return_value="/usr/bin/claude"):
        rc = cmd_onboard(assume_tty=True, print_only=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "export WANDB_API_KEY" in out


# ---------------------------------------------------------------------------
# Interactive — getpass stores to keyring, no echo, re-verified
# ---------------------------------------------------------------------------

def test_onboard_interactive_stores_key_no_echo(capsys, clean_env, fake_keyring):
    from research_vault.onboard import cmd_onboard
    from research_vault.keys import KEYRING_SERVICE

    secret = "sk-ant-SUPERSECRET-abcdef123456"
    prompts: list[str] = []

    def fake_input(q):
        prompts.append(q)
        # Say yes only to the Anthropic provider key; no to everything else.
        if "Add Anthropic API key now?" in q:
            return "y"
        return "n"

    def fake_getpass(q):
        return secret

    with patch("shutil.which", return_value="/usr/bin/claude"):
        rc = cmd_onboard(
            assume_tty=True, input_fn=fake_input, getpass_fn=fake_getpass,
        )
    out = capsys.readouterr().out
    assert rc == 0
    # Stored under the unified SSOT.
    assert fake_keyring.store[(KEYRING_SERVICE, "anthropic-api-key")] == secret
    # The secret was NEVER echoed to stdout — only a masked prefix.
    assert secret not in out
    assert "SUPERSECRET" not in out
    assert "stored + verified" in out


def test_onboard_stored_key_resolves_by_check_and_runtime(clean_env, fake_keyring):
    """A key added via onboard is read back by rv check AND the runtime (round-trip)."""
    from research_vault.onboard import cmd_onboard
    from research_vault.check import _check_api_key
    from research_vault.adapters.base import EnvSecretStore

    def fake_input(q):
        return "y" if "Add Anthropic API key now?" in q else "n"

    with patch("shutil.which", return_value="/usr/bin/claude"):
        cmd_onboard(assume_tty=True, input_fn=fake_input, getpass_fn=lambda q: "sk-ant-round")

    ok, _ = _check_api_key()
    assert ok is True
    assert EnvSecretStore().get("anthropic-api-key") == "sk-ant-round"


# ---------------------------------------------------------------------------
# Idempotency — satisfied step is skipped
# ---------------------------------------------------------------------------

def test_onboard_idempotent_skips_satisfied(capsys, clean_env, fake_keyring, monkeypatch):
    from research_vault.onboard import cmd_onboard
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-already")

    calls = {"getpass": 0}

    def fake_getpass(q):
        calls["getpass"] += 1
        return "should-not-be-asked"

    def fake_input(q):
        return "n"

    with patch("shutil.which", return_value="/usr/bin/claude"):
        rc = cmd_onboard(assume_tty=True, input_fn=fake_input, getpass_fn=fake_getpass)
    out = capsys.readouterr().out
    assert rc == 0
    # Provider already configured → skipped, masked confirm, no getpass for it.
    assert "already configured" in out


# ---------------------------------------------------------------------------
# Order + coverage — all seven steps present
# ---------------------------------------------------------------------------

def test_onboard_walks_all_steps_in_order(capsys, clean_env, fake_keyring):
    from research_vault.onboard import cmd_onboard
    with patch("shutil.which", return_value="/usr/bin/claude"):
        cmd_onboard(assume_tty=False)
    out = capsys.readouterr().out
    # Ordered step markers.
    idx_runtime = out.find("Agent runtime")
    idx_provider = out.find("Provider API key(s)")
    idx_s2 = out.find("Semantic Scholar")
    idx_asta = out.find("asta")
    idx_wandb = out.find("Weights & Biases")
    idx_zotero = out.find("Zotero")
    idx_compute = out.find("Remote compute")
    order = [idx_runtime, idx_provider, idx_s2, idx_asta, idx_wandb, idx_zotero, idx_compute]
    assert all(i >= 0 for i in order), f"a step is missing: {order}"
    assert order == sorted(order), f"steps out of order: {order}"


def test_onboard_asta_institutional_email_note(capsys, clean_env, fake_keyring):
    from research_vault.onboard import cmd_onboard
    with patch("shutil.which", return_value="/usr/bin/claude"):
        cmd_onboard(assume_tty=False)
    out = capsys.readouterr().out
    assert "institutional" in out.lower()


def test_onboard_always_exit_zero_even_without_runtime(capsys, clean_env, fake_keyring):
    """Onboard itself never blocks — it exits 0 even if the runtime is absent."""
    from research_vault.onboard import cmd_onboard
    with patch("shutil.which", return_value=None):
        rc = cmd_onboard(assume_tty=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "NOT FOUND" in out


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

def test_onboard_registered_in_verb_registry():
    from research_vault.cli import _VERB_REGISTRY
    assert "onboard" in _VERB_REGISTRY
    entry = _VERB_REGISTRY["onboard"]
    assert entry["when_to_use"].strip()
    # Named anti-pattern present (the discovery-surface contract).
    assert "Anti-pattern" in entry["when_to_use"]


def test_onboard_no_plaintext_env_written(tmp_path, clean_env, fake_keyring, monkeypatch):
    """Onboard must NOT write a plaintext .env anywhere in the CWD."""
    from research_vault.onboard import cmd_onboard
    monkeypatch.chdir(tmp_path)

    def fake_input(q):
        return "y" if "Add Anthropic API key now?" in q else "n"

    secret = "sk-ant-PLAINTEXT-LEAK-CANARY"
    with patch("shutil.which", return_value="/usr/bin/claude"):
        cmd_onboard(assume_tty=True, input_fn=fake_input, getpass_fn=lambda q: secret)
    # No plaintext .env, and the secret value never lands in ANY file on disk
    # (it lives only in the keyring, which the fake holds in memory).
    assert not (tmp_path / ".env").exists()
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert secret not in f.read_text(encoding="utf-8", errors="ignore"), (
                f"plaintext secret leaked to {f}"
            )
