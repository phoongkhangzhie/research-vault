"""conftest.py — shared fixtures for Research Vault tests.

All tests are hermetic: they run inside tmp_path, never touch ~/vault or any
private instance. The Config is constructed from in-test TOML files or defaults.
"""

import os
import sys
import pytest
from pathlib import Path

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
