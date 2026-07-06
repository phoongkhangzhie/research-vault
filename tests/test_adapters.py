"""test_adapters.py — Tests for the adapter Protocols + local-default implementations.

All tests are hermetic: they run in tmp_path, never touch external services,
never call macOS-only binaries (no ``security`` calls).
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

from research_vault.adapters.base import (
    AdapterSet,
    EnvSecretStore,
    FileNotifier,
    LocalSubprocess,
    load_adapters,
    Notifier,
    ComputeBackend,
    SecretStore,
)
from research_vault.config import Config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    return d


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """Minimal Config object for adapter tests."""
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {
            "notifier": "file",
            "backend": "local",
            "secrets": "env",
        },
        "projects": {},
    }
    return Config(raw)


# ---------------------------------------------------------------------------
# Protocol conformance: local defaults satisfy the Protocols at runtime
# ---------------------------------------------------------------------------

def test_file_notifier_satisfies_protocol(state_dir: Path) -> None:
    n = FileNotifier(state_dir)
    assert isinstance(n, Notifier), "FileNotifier must satisfy the Notifier Protocol"


def test_local_subprocess_satisfies_protocol() -> None:
    b = LocalSubprocess()
    assert isinstance(b, ComputeBackend), "LocalSubprocess must satisfy ComputeBackend Protocol"


def test_env_secret_store_satisfies_protocol() -> None:
    s = EnvSecretStore()
    assert isinstance(s, SecretStore), "EnvSecretStore must satisfy SecretStore Protocol"


# ---------------------------------------------------------------------------
# FileNotifier
# ---------------------------------------------------------------------------

def test_file_notifier_stdout(state_dir: Path, capsys) -> None:
    n = FileNotifier(state_dir)
    n.notify("hello world", level="info", subject="test")
    out = capsys.readouterr().out
    assert "hello world" in out
    assert "INFO" in out


def test_file_notifier_warn_to_stderr(state_dir: Path, capsys) -> None:
    n = FileNotifier(state_dir)
    n.notify("something wrong", level="warn")
    captured = capsys.readouterr()
    assert "something wrong" in captured.err
    assert captured.out == ""


def test_file_notifier_writes_inbox_jsonl(state_dir: Path, capsys) -> None:
    n = FileNotifier(state_dir)
    n.notify("event A", level="info", subject="ev", tags=["t1"], payload={"k": 1})
    inbox = state_dir / "inbox.jsonl"
    assert inbox.exists(), "inbox.jsonl must be created"
    lines = inbox.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["message"] == "event A"
    assert record["level"] == "info"
    assert record["subject"] == "ev"
    assert record["tags"] == ["t1"]
    assert record["payload"] == {"k": 1}
    assert "ts" in record


def test_file_notifier_appends_desk_md(state_dir: Path, capsys) -> None:
    n = FileNotifier(state_dir)
    n.notify("first", level="info")
    n.notify("second", level="warn", subject="sub")
    desk = state_dir / "desk.md"
    assert desk.exists(), "desk.md must be created"
    content = desk.read_text(encoding="utf-8")
    assert "first" in content
    assert "second" in content
    assert "sub" in content


def test_file_notifier_creates_state_dir_if_missing(tmp_path: Path, capsys) -> None:
    new_state = tmp_path / "nonexistent" / "state"
    assert not new_state.exists()
    n = FileNotifier(new_state)
    n.notify("creating dir", level="info")
    assert new_state.exists()


def test_file_notifier_multiple_events_append(state_dir: Path, capsys) -> None:
    n = FileNotifier(state_dir)
    for i in range(5):
        n.notify(f"msg-{i}", level="info")
    inbox = state_dir / "inbox.jsonl"
    lines = inbox.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 5


# ---------------------------------------------------------------------------
# LocalSubprocess
# ---------------------------------------------------------------------------

def test_local_subprocess_submit_returns_pid(tmp_path: Path) -> None:
    b = LocalSubprocess()
    handle = b.submit([sys.executable, "-c", "import time; time.sleep(0.2)"])
    assert handle.isdigit(), f"handle should be a PID string, got {handle!r}"
    # Give it a moment then check status — should be RUNNING or DONE
    status = b.status(handle)
    assert status in ("RUNNING", "DONE")


def test_local_subprocess_done_after_completion(tmp_path: Path) -> None:
    b = LocalSubprocess()
    handle = b.submit([sys.executable, "-c", "pass"])
    # Wait for it to finish
    proc = b._procs[handle]
    proc.wait()
    status = b.status(handle)
    assert status == "DONE"


def test_local_subprocess_failed_on_nonzero_exit(tmp_path: Path) -> None:
    b = LocalSubprocess()
    handle = b.submit([sys.executable, "-c", "import sys; sys.exit(1)"])
    proc = b._procs[handle]
    proc.wait()
    status = b.status(handle)
    assert status == "FAILED"


def test_local_subprocess_unknown_handle() -> None:
    b = LocalSubprocess()
    # A handle not in our registry and not a valid PID → UNKNOWN
    status = b.status("not-a-number")
    assert status == "UNKNOWN"


# ---------------------------------------------------------------------------
# EnvSecretStore
# ---------------------------------------------------------------------------

def test_env_secret_store_reads_env_var(monkeypatch) -> None:
    monkeypatch.setenv("ZOTERO_KEY", "my-test-key")
    s = EnvSecretStore()
    assert s.get("zotero-key") == "my-test-key"


def test_env_secret_store_name_conversion(monkeypatch) -> None:
    monkeypatch.setenv("S2_API_KEY", "s2val")
    s = EnvSecretStore()
    assert s.get("s2-api-key") == "s2val"


def test_env_secret_store_dot_to_underscore(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_KEY", "ak")
    s = EnvSecretStore()
    assert s.get("anthropic.key") == "ak"


def test_env_secret_store_raises_key_error_if_missing(monkeypatch) -> None:
    monkeypatch.delenv("MISSING_SECRET_XYZ", raising=False)
    # Disable keyring lookup in test environment
    monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
    s = EnvSecretStore()
    with pytest.raises(KeyError, match="missing-secret-xyz"):
        s.get("missing-secret-xyz")


def test_env_secret_store_env_name() -> None:
    assert EnvSecretStore._env_name("zotero-key") == "ZOTERO_KEY"
    assert EnvSecretStore._env_name("s2-api-key") == "S2_API_KEY"
    assert EnvSecretStore._env_name("anthropic.key") == "ANTHROPIC_KEY"


def test_env_secret_store_no_security_binary(monkeypatch) -> None:
    """Verify EnvSecretStore never calls the macOS 'security' binary."""
    import subprocess as _sp
    original_run = _sp.run

    def guard_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd and "security" in cmd[0]:
            raise AssertionError(
                f"EnvSecretStore must NOT call the macOS 'security' binary: {cmd}"
            )
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(_sp, "run", guard_run)
    monkeypatch.setattr(_sp, "check_output", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("security binary called via check_output")))

    monkeypatch.delenv("ZOTERO_KEY", raising=False)
    s = EnvSecretStore()
    with pytest.raises((KeyError, Exception)):
        s.get("zotero-key")


# ---------------------------------------------------------------------------
# load_adapters + AdapterSet
# ---------------------------------------------------------------------------

def test_load_adapters_returns_adapter_set(cfg: Config, tmp_path: Path) -> None:
    (tmp_path / "state").mkdir(exist_ok=True)
    adapter_set = load_adapters(cfg)
    assert isinstance(adapter_set, AdapterSet)
    assert isinstance(adapter_set.notifier, Notifier)
    assert isinstance(adapter_set.backend, ComputeBackend)
    assert isinstance(adapter_set.secrets, SecretStore)


def test_load_adapters_unknown_notifier_raises(cfg: Config) -> None:
    cfg._raw["adapters"]["notifier"] = "telegram"
    cfg.adapters = cfg._raw["adapters"]
    with pytest.raises(ValueError, match="telegram"):
        load_adapters(cfg)


def test_load_adapters_unknown_backend_raises(cfg: Config) -> None:
    # SR-7 added slurm/pbs/ssh/generic as known remote backends.
    # Use a genuinely unknown name to test the error path.
    cfg._raw["adapters"]["backend"] = "kubernetes"
    cfg.adapters = cfg._raw["adapters"]
    with pytest.raises(ValueError, match="kubernetes"):
        load_adapters(cfg)


def test_load_adapters_unknown_secrets_raises(cfg: Config) -> None:
    cfg._raw["adapters"]["secrets"] = "vault"
    cfg.adapters = cfg._raw["adapters"]
    with pytest.raises(ValueError, match="vault"):
        load_adapters(cfg)
