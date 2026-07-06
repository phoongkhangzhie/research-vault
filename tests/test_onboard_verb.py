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


# ---------------------------------------------------------------------------
# _step_compute — pure handoff, no in-process cmd_init invocation (F7 fix)
# ---------------------------------------------------------------------------

class _FakeComputeFeature:
    """Minimal stand-in for the compute Feature dataclass."""
    id = "compute"
    title = "Remote compute"
    unlocks = "remote-cluster experiments"
    kind = "handoff"
    handoff_cmd = "rv compute init"


def _locked_status() -> dict:
    return {"id": "compute", "status": "locked", "detail": "", "source": "", "handoff_cmd": "rv compute init"}


def _unlocked_status() -> dict:
    return {"id": "compute", "status": "unlocked", "detail": "compute_manifest.json present", "source": "manifest", "handoff_cmd": "rv compute init"}


def test_step_compute_absent_manifest_prints_handoff(capsys):
    """When compute_manifest is absent, _step_compute prints the handoff command and does NOT call cmd_init."""
    from research_vault.onboard import _step_compute
    import research_vault.compute as _compute_mod

    cmd_init_calls = []

    def _boom(*_a, **_kw):
        cmd_init_calls.append(1)
        raise AssertionError("cmd_init must never be called from _step_compute")

    _orig = getattr(_compute_mod, "cmd_init", None)
    _compute_mod.cmd_init = _boom  # type: ignore[attr-defined]
    try:
        _step_compute(_locked_status(), _FakeComputeFeature(), step_no=7)
    finally:
        if _orig is None:
            del _compute_mod.cmd_init
        else:
            _compute_mod.cmd_init = _orig  # type: ignore[attr-defined]

    out = capsys.readouterr().out
    assert "rv compute init" in out, "handoff command must be printed"
    assert "rv doctor" in out, "follow-up rv doctor must be mentioned"
    # The structural proof: cmd_init was never invoked.
    assert cmd_init_calls == [], "cmd_init must not be called in-process"


def test_step_compute_absent_manifest_no_exception_interactive(capsys, monkeypatch):
    """_step_compute raises no exception — not even in an interactive context."""
    from research_vault.onboard import _step_compute

    # Monkeypatch to make cmd_init raise if accidentally imported+called.
    monkeypatch.setattr(
        "research_vault.compute.cmd_init",
        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("[Errno 2] No such file")),
        raising=False,
    )

    # No exception must escape.
    _step_compute(_locked_status(), _FakeComputeFeature(), step_no=7)
    out = capsys.readouterr().out
    assert "rv compute init" in out
    # Must never print the old error message.
    assert "could not run" not in out


def test_step_compute_manifest_present_skips(capsys):
    """When compute_manifest.json is present, _step_compute prints 'already declared' and returns."""
    from research_vault.onboard import _step_compute

    _step_compute(_unlocked_status(), _FakeComputeFeature(), step_no=7)
    out = capsys.readouterr().out
    assert "already declared" in out
    assert "rv compute init" not in out, "should not print handoff when already declared"


def test_step_compute_via_cmd_onboard_no_cmd_init_called(capsys, clean_env, fake_keyring, monkeypatch):
    """End-to-end: cmd_onboard in interactive mode must not invoke cmd_init for the compute step."""
    from research_vault.onboard import cmd_onboard

    cmd_init_calls = []

    def _never_called(*_a, **_kw):
        cmd_init_calls.append(1)
        raise FileNotFoundError("[Errno 2] No such file or directory: '/fake/manifest'")

    monkeypatch.setattr("research_vault.compute.cmd_init", _never_called, raising=False)

    # Patch _compute_manifest_present to return False (manifest absent).
    monkeypatch.setattr(
        "research_vault.check._compute_manifest_present",
        lambda cfg=None: False,
    )

    # Even interactive (assume_tty=True) + saying yes to everything must NOT call cmd_init.
    with patch("shutil.which", return_value="/usr/bin/claude"):
        rc = cmd_onboard(
            assume_tty=True,
            input_fn=lambda q: "y",  # say yes to everything
            getpass_fn=lambda q: "sk-ant-dummy",
        )

    out = capsys.readouterr().out
    assert rc == 0
    assert cmd_init_calls == [], f"cmd_init was called {len(cmd_init_calls)} time(s) — must be 0"
    assert "could not run" not in out
    assert "rv compute init" in out, "handoff message must still appear"


# ---------------------------------------------------------------------------
# (original test preserved below)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _step_approval_setup — inline gate approval onboarding step
# ---------------------------------------------------------------------------

class _FakeCfg:
    """Minimal stand-in for a Config with a config_file."""
    def __init__(self, config_file=None):
        self.config_file = config_file
        self._raw: dict = {}


def test_step_approval_setup_accept_path_binds_token(tmp_path, fake_keyring):
    """Accept path calls provision_approver_token_to_keyring and stores token in keyring."""
    from research_vault.onboard import _step_approval_setup
    from research_vault.approval import provision_approver_token_to_keyring

    config_file = tmp_path / "research_vault.toml"
    config_file.write_text("[approval]\n", encoding="utf-8")
    cfg = _FakeCfg(config_file=config_file)

    setup_calls = []

    def fake_provision(cfg_arg):
        setup_calls.append(cfg_arg)
        # Simulate keyring write.
        import keyring
        keyring.set_password("research-vault", "rv-approver-token", "fake-token")
        return True, "token stored in keyring (service=research-vault, username=rv-approver-token)"

    with patch("research_vault.approval.provision_approver_token_to_keyring", fake_provision):
        with patch("research_vault.approval._is_approver_token_bound", return_value=False):
            _step_approval_setup(
                cfg,
                interactive=True,
                input_fn=lambda q: "y",  # say yes
                step_no=8,
            )

    # The real setup function was called.
    assert len(setup_calls) == 1
    assert setup_calls[0] is cfg


def test_step_approval_setup_skip_path_leaves_no_token(capsys, tmp_path, fake_keyring):
    """Declining the prompt leaves nothing in the keyring."""
    from research_vault.onboard import _step_approval_setup

    config_file = tmp_path / "research_vault.toml"
    config_file.write_text("[approval]\n", encoding="utf-8")
    cfg = _FakeCfg(config_file=config_file)

    with patch("research_vault.approval._is_approver_token_bound", return_value=False):
        with patch("research_vault.approval.provision_approver_token_to_keyring") as mock_prov:
            _step_approval_setup(
                cfg,
                interactive=True,
                input_fn=lambda q: "n",  # say no
                step_no=8,
            )
            mock_prov.assert_not_called()

    out = capsys.readouterr().out
    assert "skipped" in out.lower()
    # No token in keyring.
    assert fake_keyring.store.get(("research-vault", "rv-approver-token")) is None


def test_step_approval_setup_already_bound_short_circuits(capsys, tmp_path, fake_keyring):
    """If the token is already in the keyring, skip with 'already set up'."""
    from research_vault.onboard import _step_approval_setup

    config_file = tmp_path / "research_vault.toml"
    config_file.write_text("[approval]\ntoken_fingerprint = \"abc\"\n", encoding="utf-8")
    cfg = _FakeCfg(config_file=config_file)

    with patch("research_vault.approval._is_approver_token_bound", return_value=True):
        with patch("research_vault.approval.provision_approver_token_to_keyring") as mock_prov:
            _step_approval_setup(
                cfg,
                interactive=True,
                input_fn=lambda q: (_ for _ in ()).throw(AssertionError("should not prompt")),
                step_no=8,
            )
            mock_prov.assert_not_called()

    out = capsys.readouterr().out
    assert "already set up" in out


def test_step_approval_setup_non_tty_skips_cleanly(capsys, tmp_path, fake_keyring):
    """Non-interactive: step is skipped silently (no output, no provision call)."""
    from research_vault.onboard import _step_approval_setup

    config_file = tmp_path / "research_vault.toml"
    config_file.write_text("[approval]\n", encoding="utf-8")
    cfg = _FakeCfg(config_file=config_file)

    with patch("research_vault.approval.provision_approver_token_to_keyring") as mock_prov:
        _step_approval_setup(
            cfg,
            interactive=False,
            input_fn=lambda q: (_ for _ in ()).throw(AssertionError("should not prompt")),
            step_no=8,
        )
        mock_prov.assert_not_called()

    out = capsys.readouterr().out
    # Silent skip — no output at all.
    assert out == ""


def test_step_approval_setup_no_cfg_skips_with_note(capsys):
    """No cfg (or cfg.config_file is None): skip with a brief note."""
    from research_vault.onboard import _step_approval_setup

    with patch("research_vault.approval.provision_approver_token_to_keyring") as mock_prov:
        _step_approval_setup(
            None,
            interactive=True,
            input_fn=lambda q: "y",
            step_no=8,
        )
        mock_prov.assert_not_called()

    out = capsys.readouterr().out
    assert "skipped" in out.lower()


def test_step_approval_setup_honest_framing_text(capsys, tmp_path):
    """The step text contains the honest framing about discipline-enforced vs can't."""
    from research_vault.onboard import _step_approval_setup

    config_file = tmp_path / "research_vault.toml"
    config_file.write_text("[approval]\n", encoding="utf-8")
    cfg = _FakeCfg(config_file=config_file)

    with patch("research_vault.approval._is_approver_token_bound", return_value=False):
        with patch("research_vault.approval.provision_approver_token_to_keyring", return_value=(True, "ok")):
            _step_approval_setup(
                cfg,
                interactive=True,
                input_fn=lambda q: "n",  # skip after reading framing
                step_no=8,
            )

    out = capsys.readouterr().out
    # The framing must be present before any prompt.
    assert "discipline-enforced" in out
    assert "won't self-approve" in out or "won't" in out
    assert "separate terminal" in out


def test_onboard_approval_step_present_in_interactive_session(capsys, clean_env, fake_keyring, tmp_path):
    """In interactive mode, the approval step appears in the flow (after compute)."""
    from research_vault.onboard import cmd_onboard

    # Build a minimal cfg with config_file so the approval step isn't skipped for that reason.
    config_file = tmp_path / "research_vault.toml"
    config_file.write_text("[approval]\n", encoding="utf-8")

    from research_vault.config import Config
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {},
    }
    cfg = Config(raw, config_file=config_file)

    with patch("shutil.which", return_value="/usr/bin/claude"):
        with patch("research_vault.approval._is_approver_token_bound", return_value=False):
            with patch("research_vault.approval.provision_approver_token_to_keyring",
                       return_value=(True, "token stored")):
                rc = cmd_onboard(
                    cfg,
                    assume_tty=True,
                    input_fn=lambda q: "n",  # skip all prompts including approval
                    getpass_fn=lambda q: "",
                )

    out = capsys.readouterr().out
    assert rc == 0
    # The approval step must appear.
    assert "Inline gate approval" in out
    # Must appear AFTER the compute step.
    idx_compute = out.find("Remote compute")
    idx_approval = out.find("Inline gate approval")
    assert idx_compute >= 0, "compute step missing"
    assert idx_approval >= 0, "approval step missing"
    assert idx_approval > idx_compute, "approval step must come after compute"


def test_onboard_approval_step_absent_in_non_interactive(capsys, clean_env, fake_keyring):
    """In non-interactive mode the approval step is skipped silently (no output for it)."""
    from research_vault.onboard import cmd_onboard
    with patch("shutil.which", return_value="/usr/bin/claude"):
        cmd_onboard(assume_tty=False)
    out = capsys.readouterr().out
    # The step itself should not appear (silent skip).
    assert "Inline gate approval" not in out


# ---------------------------------------------------------------------------
# provision_approver_token_to_keyring — core setup logic in approval.py
# ---------------------------------------------------------------------------

def test_provision_approver_token_to_keyring_binds_token(tmp_path, fake_keyring):
    """provision_approver_token_to_keyring writes fingerprint to config + stores in keyring."""
    from research_vault.approval import provision_approver_token_to_keyring
    from research_vault.dag.approval import compute_fingerprint, verify_fingerprint

    config_file = tmp_path / "research_vault.toml"
    config_file.write_text("[approval]\n", encoding="utf-8")

    from research_vault.config import Config
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {},
    }
    cfg = Config(raw, config_file=config_file)

    ok, msg = provision_approver_token_to_keyring(cfg)

    assert ok is True
    # Message is safe — does NOT contain the raw token.
    stored_token = fake_keyring.store.get(("research-vault", "rv-approver-token"))
    assert stored_token is not None, "token must be in keyring"
    assert stored_token not in msg, "token must NOT appear in the returned message"
    # Fingerprint is in config.
    config_text = config_file.read_text(encoding="utf-8")
    assert "token_fingerprint" in config_text
    # Fingerprint verifies against the stored token.
    import re
    m = re.search(r'token_fingerprint\s*=\s*"([^"]+)"', config_text)
    assert m is not None, "token_fingerprint not found in config"
    assert verify_fingerprint(stored_token, m.group(1))


def test_provision_approver_token_to_keyring_no_config_file(tmp_path):
    """Returns (False, …) when cfg has no config_file."""
    from research_vault.approval import provision_approver_token_to_keyring
    cfg = _FakeCfg(config_file=None)
    ok, msg = provision_approver_token_to_keyring(cfg)
    assert ok is False
    assert "config file" in msg or "rv init" in msg


def test_is_approver_token_bound_true_when_in_keyring(fake_keyring):
    """_is_approver_token_bound returns True when keyring has the token."""
    from research_vault.approval import _is_approver_token_bound
    fake_keyring.store[("research-vault", "rv-approver-token")] = "some-token"
    assert _is_approver_token_bound() is True


def test_is_approver_token_bound_false_when_absent(fake_keyring):
    """_is_approver_token_bound returns False when keyring has no token."""
    from research_vault.approval import _is_approver_token_bound
    # fresh fake_keyring has nothing
    assert _is_approver_token_bound() is False


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
