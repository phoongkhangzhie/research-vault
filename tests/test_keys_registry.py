"""test_keys_registry.py — the credential/feature registry SSOT (F4 unification).

The key-registry is the single source of truth for the keyring service name and,
per feature key, its env-var + keyring username. `rv check`, `rv onboard`, and the
runtime EnvSecretStore all resolve through it — so a key WRITTEN by onboard is READ
by check and the runtime (no service-name split).
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fake keyring backend — dict-backed, injected via monkeypatch
# ---------------------------------------------------------------------------

class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str):
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self.store[(service, username)] = value


@pytest.fixture
def fake_keyring(monkeypatch):
    fk = _FakeKeyring()
    import keyring as _kr
    monkeypatch.setattr(_kr, "get_password", fk.get_password)
    monkeypatch.setattr(_kr, "set_password", fk.set_password)
    return fk


# ---------------------------------------------------------------------------
# Service-name SSOT
# ---------------------------------------------------------------------------

def test_single_keyring_service_constant():
    """The registry defines ONE keyring service — the hyphen form used by the runtime."""
    from research_vault.keys import KEYRING_SERVICE
    assert KEYRING_SERVICE == "research-vault"


def test_env_secret_store_uses_registry_service():
    """EnvSecretStore's service name is the registry SSOT, not a private literal."""
    from research_vault.keys import KEYRING_SERVICE
    from research_vault.adapters.base import EnvSecretStore
    assert EnvSecretStore._SERVICE == KEYRING_SERVICE


# ---------------------------------------------------------------------------
# Key specs
# ---------------------------------------------------------------------------

def test_provider_keys_are_plural():
    """Provider keys are provider-plural, not Anthropic-specific."""
    from research_vault.keys import PROVIDER_KEYS
    ids = {k.id for k in PROVIDER_KEYS}
    assert "anthropic" in ids
    assert "openai" in ids
    assert len(PROVIDER_KEYS) >= 2


def test_key_env_var_matches_env_secret_store_derivation():
    """Each key's env_var equals EnvSecretStore's derivation of its keyring username.

    This is the invariant that makes the round-trip work: a value stored under
    keyring_username is read back by EnvSecretStore.get(keyring_username) via the
    same env-var name.
    """
    from research_vault.keys import KEYRING_KEYS
    from research_vault.adapters.base import EnvSecretStore
    for spec in KEYRING_KEYS:
        assert EnvSecretStore._env_name(spec.keyring_username) == spec.env_var, (
            f"{spec.id}: env_var {spec.env_var} != derived "
            f"{EnvSecretStore._env_name(spec.keyring_username)}"
        )


def test_each_key_has_request_url():
    from research_vault.keys import KEYRING_KEYS
    for spec in KEYRING_KEYS:
        assert spec.request_url.startswith("https://"), f"{spec.id} has no request URL"
        assert spec.unlocks, f"{spec.id} has no unlocks string"


# ---------------------------------------------------------------------------
# resolve_key
# ---------------------------------------------------------------------------

def test_resolve_key_reads_env_first(monkeypatch):
    from research_vault.keys import resolve_key, get_key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-envvalue")
    present, source, masked = resolve_key(get_key("anthropic"))
    assert present is True
    assert source == "env"
    assert "sk-ant-envvalue" not in masked  # masked prefix only, never the full value
    assert masked.endswith("…")


def test_resolve_key_reads_keyring_when_env_absent(monkeypatch, fake_keyring):
    from research_vault.keys import resolve_key, get_key, KEYRING_SERVICE
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VAULT_SKIP_KEYRING", raising=False)
    fake_keyring.set_password(KEYRING_SERVICE, "anthropic-api-key", "sk-ant-kr")
    present, source, masked = resolve_key(get_key("anthropic"))
    assert present is True
    assert source == "keyring"


def test_resolve_key_absent_when_neither(monkeypatch):
    from research_vault.keys import resolve_key, get_key
    monkeypatch.delenv("ZOTERO_KEY", raising=False)
    monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
    present, source, masked = resolve_key(get_key("zotero"))
    assert present is False
    assert source == ""
    assert masked == ""


def test_skip_keyring_flag_honored(monkeypatch, fake_keyring):
    from research_vault.keys import resolve_key, get_key, KEYRING_SERVICE
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    fake_keyring.set_password(KEYRING_SERVICE, "wandb-api-key", "wbkey")
    # skip_keyring=True → must NOT see the keyring value.
    present, _, _ = resolve_key(get_key("wandb"), skip_keyring=True)
    assert present is False


# ---------------------------------------------------------------------------
# store_key + round-trip (the F4 acceptance)
# ---------------------------------------------------------------------------

def test_store_then_resolve_round_trip(monkeypatch, fake_keyring):
    """A key WRITTEN via store_key is READ by resolve_key (same SSOT)."""
    from research_vault.keys import store_key, resolve_key, get_key
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VAULT_SKIP_KEYRING", raising=False)
    store_key(get_key("anthropic"), "sk-ant-written")
    present, source, _ = resolve_key(get_key("anthropic"))
    assert present is True
    assert source == "keyring"


def test_store_then_runtime_env_secret_store_reads_it(monkeypatch, fake_keyring):
    """A key WRITTEN via store_key is READ by the runtime EnvSecretStore (F4 round-trip).

    This is THE acceptance: onboard writes → the runtime model seam reads.
    """
    from research_vault.keys import store_key, get_key
    from research_vault.adapters.base import EnvSecretStore
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VAULT_SKIP_KEYRING", raising=False)
    store_key(get_key("anthropic"), "sk-ant-written-2")
    # The runtime resolves the provider key by its dash-case name.
    assert EnvSecretStore().get("anthropic-api-key") == "sk-ant-written-2"


def test_store_then_check_reads_it(monkeypatch, fake_keyring):
    """A provider key WRITTEN via store_key is seen by _check_api_key (F4 round-trip)."""
    from research_vault.keys import store_key, get_key
    from research_vault.check import _check_api_key
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VAULT_SKIP_KEYRING", raising=False)
    store_key(get_key("anthropic"), "sk-ant-check")
    ok, _msg = _check_api_key()
    assert ok is True


def test_zotero_round_trip_check_and_runtime(monkeypatch, fake_keyring):
    """Zotero key written via store_key is read by BOTH _check_zotero and the runtime."""
    from research_vault.keys import store_key, get_key
    from research_vault.check import _check_zotero
    from research_vault.adapters.base import EnvSecretStore
    monkeypatch.delenv("ZOTERO_KEY", raising=False)
    monkeypatch.delenv("VAULT_SKIP_KEYRING", raising=False)
    store_key(get_key("zotero"), "zot-secret")
    ok, _msg, _req = _check_zotero()
    assert ok is True
    assert EnvSecretStore().get("zotero-key") == "zot-secret"


# ---------------------------------------------------------------------------
# mask
# ---------------------------------------------------------------------------

def test_mask_never_reveals_full_value():
    from research_vault.keys import mask
    m = mask("sk-ant-supersecret-1234567890")
    assert "supersecret" not in m
    assert "1234567890" not in m


def test_mask_short_value_fully_hidden():
    from research_vault.keys import mask
    assert mask("abc") == "***"


# ---------------------------------------------------------------------------
# Feature catalog
# ---------------------------------------------------------------------------

def test_feature_catalog_order():
    """FEATURES are in onboarding order: provider → s2 → asta → wandb → zotero → compute."""
    from research_vault.keys import FEATURES
    ids = [f.id for f in FEATURES]
    assert ids == ["provider", "s2", "asta", "wandb", "zotero", "compute"]


def test_asta_feature_is_key_not_package():
    """asta is the Allen AI MCP server — detected by key, never by import."""
    from research_vault.keys import get_feature, ASTA_KEY
    asta = get_feature("asta")
    assert asta.kind == "key", (
        "asta must be kind='key' — it is NOT a pip package (no import asta)"
    )
    assert not asta.import_name, "asta.import_name must be empty — no pip probe"
    assert asta.keys == (ASTA_KEY,), "asta feature must reference ASTA_KEY"
    assert "institutional" in asta.note.lower()
    assert asta.request_url.startswith("https://")


def test_asta_key_registered_in_keyring_keys():
    """ASTA_KEY must appear in KEYRING_KEYS (covered by the round-trip invariant test)."""
    from research_vault.keys import KEYRING_KEYS, ASTA_KEY
    assert ASTA_KEY in KEYRING_KEYS


def test_asta_key_env_var_round_trip():
    """ASTA_KEY.env_var == EnvSecretStore derivation of keyring_username (F4 invariant)."""
    from research_vault.keys import ASTA_KEY
    from research_vault.adapters.base import EnvSecretStore
    assert EnvSecretStore._env_name(ASTA_KEY.keyring_username) == ASTA_KEY.env_var, (
        f"env_var {ASTA_KEY.env_var!r} must equal derived "
        f"{EnvSecretStore._env_name(ASTA_KEY.keyring_username)!r}"
    )
    assert ASTA_KEY.env_var == "ASTA_MCP_KEY"
    assert ASTA_KEY.keyring_username == "asta-mcp-key"


def test_compute_feature_is_handoff():
    from research_vault.keys import get_feature
    compute = get_feature("compute")
    assert compute.kind == "handoff"
    assert "rv compute init" in compute.handoff_cmd


def test_every_feature_is_feature_required_class():
    from research_vault.keys import FEATURES, CLASS_FEATURE_REQUIRED
    for f in FEATURES:
        assert f.cls == CLASS_FEATURE_REQUIRED
