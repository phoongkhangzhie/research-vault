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
"""

import os
import sys
import pytest
from pathlib import Path

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
