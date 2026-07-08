"""conftest.py — shared fixtures for Research Vault tests.

All tests are hermetic: they run inside tmp_path, never touch ~/vault or any
private instance. The Config is constructed from in-test TOML files or defaults.

SR-APPROVE-GATE: the approval gate is ON by default.  Tests that call cmd_approve
exercise the REAL gate via the token path.  The autouse ``_approver_token_env``
fixture provisions ``RV_APPROVER_TOKEN`` + a matching fingerprint baked into the
``tmp_instance`` TOML so all approval calls resolve through the token branch.

Token constant (tests only): ``TEST_APPROVER_TOKEN``
Fingerprint constant (tests only): ``TEST_APPROVER_FINGERPRINT``

To test fail-closed behaviour, unset ``RV_APPROVER_TOKEN`` inside your test:
    monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
and assert the cmd returns 1 with the doctrine slug in stderr.

SR-TEST-ISOLATION: the operator's real config-discovery chain (see
``research_vault.config`` module docstring) walks up from CWD and falls back
to ``$XDG_CONFIG_HOME/research_vault/config.toml`` / ``~/.config/research_vault/
config.toml``. Without isolation, a test that exercises this path for real
(e.g. one that doesn't set ``RESEARCH_VAULT_CONFIG``) can read — or worse,
WRITE — the operator's live registry. This bit for real: an unisolated
``test_cmd_add_no_config_raises`` run silently appended a bogus
``[projects.x]`` entry into the operator's real
``~/.vault-state/rv/research_vault.toml``.

The autouse, session-scoped ``_isolate_home`` fixture below points ``HOME``
and ``XDG_CONFIG_HOME`` at a fresh session tmp dir and unsets
``RESEARCH_VAULT_CONFIG`` before any test runs, so:
  - CWD walk-up never escapes into a real ancestor directory's
    ``research_vault.toml`` (pytest's rootdir/cwd during the run is the repo
    checkout, which has no ``research_vault.toml`` at any level — this
    fixture's HOME/XDG isolation covers the *fallback* level; individual
    tests are still responsible for not chdir-ing into a real ancestor tree).
  - XDG fallback resolves under the sandboxed HOME, never the operator's own.
  - No stray ``RESEARCH_VAULT_CONFIG`` from the outer shell leaks into a test
    that forgets to set its own.
"""

import os
import sys
import pytest
from pathlib import Path


@pytest.fixture(autouse=True, scope="session")
def _isolate_home(tmp_path_factory):
    """SR-TEST-ISOLATION: sandbox HOME/XDG_CONFIG_HOME for the whole session.

    Prevents any test — including ones that don't explicitly set
    ``RESEARCH_VAULT_CONFIG`` — from resolving, reading, or writing the
    operator's real ``~/.config/research_vault/config.toml`` or
    ``~/.vault-state/rv/research_vault.toml`` via the XDG fallback level of
    config discovery (see ``research_vault.config`` module docstring).

    Session-scoped + applied once, before any test module import runs, so it
    is in effect even for tests that construct a Config or call cmd_add
    before ever touching ``RESEARCH_VAULT_CONFIG`` themselves.
    """
    sandbox_home = tmp_path_factory.mktemp("sandbox_home")
    sandbox_xdg = sandbox_home / ".config"
    sandbox_xdg.mkdir(parents=True, exist_ok=True)

    old_home = os.environ.get("HOME")
    old_xdg = os.environ.get("XDG_CONFIG_HOME")
    old_rv_config = os.environ.get("RESEARCH_VAULT_CONFIG")

    os.environ["HOME"] = str(sandbox_home)
    os.environ["XDG_CONFIG_HOME"] = str(sandbox_xdg)
    os.environ.pop("RESEARCH_VAULT_CONFIG", None)

    yield

    if old_home is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = old_home
    if old_xdg is None:
        os.environ.pop("XDG_CONFIG_HOME", None)
    else:
        os.environ["XDG_CONFIG_HOME"] = old_xdg
    if old_rv_config is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old_rv_config

# ---------------------------------------------------------------------------
# SR-APPROVE-GATE: test-token constants
# ---------------------------------------------------------------------------
# Fixed token used by all tests.  The fingerprint was computed as:
#   hashlib.sha256(b"rv-approver-token-v1:" + token.encode()).hexdigest()
TEST_APPROVER_TOKEN = "test-approver-token-fixture"
TEST_APPROVER_FINGERPRINT = (
    "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"
)

# Ensure the src package is importable from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
# Ensure tests/ is importable (for tests.gitutil shared fixtures)
sys.path.insert(0, str(Path(__file__).parent.parent))

# Re-export shared fixtures from gitutil so they are globally available
# to all test modules without requiring explicit imports.
from tests.gitutil import tmp_git_repo  # noqa: F401  (pytest fixture registration)


@pytest.fixture(autouse=True)
def reset_config_cache():
    """Reset the config module cache before each test so path changes take effect."""
    from research_vault.config import reset_config_cache
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture(autouse=True)
def _approver_token_env(monkeypatch):
    """SR-APPROVE-GATE: provision RV_APPROVER_TOKEN for the token approval path.

    All tests run with a fixed token so cmd_approve exercises the REAL gate via
    the token branch (not a bypass).  Tests that need to probe fail-closed
    behaviour should unset the env var:
        monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
    """
    monkeypatch.setenv("RV_APPROVER_TOKEN", TEST_APPROVER_TOKEN)
    # Honor VAULT_SKIP_KEYRING so keyring is never consulted during tests.
    monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")


@pytest.fixture
def tmp_instance(tmp_path):
    """A temporary Research Vault instance with a minimal config.

    Returns the instance root path. Config is wired via RESEARCH_VAULT_CONFIG env.
    Includes one demo project 'demo-research' in the registry.
    """
    config_file = tmp_path / "research_vault.toml"
    config_file.write_text(
        f"""
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"

[projects.demo-research]
source_dir = "{tmp_path / 'projects' / 'demo-research'}"
tasks_dir = "{tmp_path / 'tasks' / 'demo-research'}"

[projects.demo-litreview]
source_dir = "{tmp_path / 'projects' / 'demo-litreview'}"
tasks_dir = "{tmp_path / 'tasks' / 'demo-litreview'}"

# SR-APPROVE-GATE: token fingerprint for test-time token approval.
# Matches TEST_APPROVER_TOKEN defined in conftest.py.
[approval]
enforce = true
token_fingerprint = "{TEST_APPROVER_FINGERPRINT}"
enforce_sig = ""
""",
        encoding="utf-8",
    )

    # Set env to point at our test config
    old = os.environ.get("RESEARCH_VAULT_CONFIG")
    os.environ["RESEARCH_VAULT_CONFIG"] = str(config_file)
    yield tmp_path
    # Restore env
    if old is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old
