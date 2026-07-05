"""keys.py — the credential / feature registry (the F4 SSOT).

Single source of truth for:

  1. The keyring **service name** (one constant — no more ``research_vault``
     underscore vs ``research-vault`` hyphen split).  ``EnvSecretStore`` imports
     it, so a key WRITTEN by ``rv onboard`` is READ by ``rv check`` AND the runtime
     model seam (the F4 round-trip).
  2. Per feature **key**: env-var, keyring username, request-form URL, label, and
     the capability it unlocks.
  3. The feature **catalog** (``FEATURES``) — the ordered list ``rv check`` and
     ``rv onboard`` both render from.  Every feature is FEATURE-REQUIRED: a missing
     one is "locked until you add the key", NEVER a FAIL.  Only the agent runtime
     is hard-REQUIRED, and it is not in this registry (checked directly).

The corrected required-model (Khang): the agent runtime (Claude Code) is the ONLY
hard requirement.  There is NO required API key.  A fresh adopter with the runtime
and zero keys → ``rv check`` GREEN (exit 0), every feature shown "locked".

Stdlib only (``os`` + a lazy ``keyring`` import).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# The ONE keyring service name (F4 unification).
# ---------------------------------------------------------------------------
# Before F4 the service name was split: check.py used "research_vault" (underscore)
# while EnvSecretStore + wandb used "research-vault" (hyphen).  A key written under
# one was invisible to the other.  This constant is now the single SSOT; every
# reader/writer resolves through it.
KEYRING_SERVICE = "research-vault"

# ---------------------------------------------------------------------------
# Class labels (F3).  Only the runtime is REQUIRED; every feature key is
# FEATURE-REQUIRED (locked-until-you-add, never a FAIL); OPTIONAL is reserved.
# ---------------------------------------------------------------------------
CLASS_REQUIRED = "REQUIRED"
CLASS_FEATURE_REQUIRED = "FEATURE-REQUIRED"
CLASS_OPTIONAL = "OPTIONAL"


# ---------------------------------------------------------------------------
# Key specs (keyring-storable secrets)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KeySpec:
    """A keyring-storable credential.

    ``env_var`` MUST equal ``EnvSecretStore._env_name(keyring_username)`` — that
    invariant (asserted in the tests) is what makes the round-trip work: onboard
    writes under ``keyring_username``; the runtime reads it back via the same
    env-var name.
    """

    id: str
    env_var: str
    keyring_username: str
    label: str
    unlocks: str
    request_url: str
    note: str = ""


# Provider API keys — provider-PLURAL, not Anthropic-specific.  ANY one present
# unlocks API-model experiments (local-model / lit-review-only adopters need none).
PROVIDER_KEYS: tuple[KeySpec, ...] = (
    KeySpec(
        id="anthropic",
        env_var="ANTHROPIC_API_KEY",
        keyring_username="anthropic-api-key",
        label="Anthropic API key",
        unlocks="API-model experiments (Anthropic models)",
        request_url="https://console.anthropic.com/settings/keys",
    ),
    KeySpec(
        id="openai",
        env_var="OPENAI_API_KEY",
        keyring_username="openai-api-key",
        label="OpenAI API key",
        unlocks="API-model experiments (OpenAI models)",
        request_url="https://platform.openai.com/api-keys",
    ),
)

S2_KEY = KeySpec(
    id="s2",
    env_var="S2_API_KEY",
    keyring_username="s2-api-key",
    label="Semantic Scholar API key",
    unlocks="`rv research find` retrieval",
    request_url="https://www.semanticscholar.org/product/api",
)

WANDB_KEY = KeySpec(
    id="wandb",
    env_var="WANDB_API_KEY",
    keyring_username="wandb-api-key",
    label="Weights & Biases API key",
    unlocks="experiment observability + `rv wandb pull`",
    request_url="https://wandb.ai/settings",
)

ZOTERO_KEY = KeySpec(
    id="zotero",
    env_var="ZOTERO_KEY",
    keyring_username="zotero-key",
    label="Zotero API key",
    unlocks="`rv cite`",
    request_url="https://www.zotero.org/settings/keys",
)

# All keyring-storable keys, in onboarding order.
KEYRING_KEYS: tuple[KeySpec, ...] = PROVIDER_KEYS + (S2_KEY, WANDB_KEY, ZOTERO_KEY)

_BY_ID: dict[str, KeySpec] = {k.id: k for k in KEYRING_KEYS}


def get_key(key_id: str) -> KeySpec:
    """Return the KeySpec with ``id == key_id``. Raises KeyError if unknown."""
    return _BY_ID[key_id]


# ---------------------------------------------------------------------------
# Feature catalog (what rv check + rv onboard render / walk)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Feature:
    """A capability a fresh adopter can unlock.

    ``kind``:
      - ``"key"``      — backed by one-or-more keyring keys (``keys``); present
                          when ANY of them resolves.
      - ``"package"``  — a Python package the adopter installs (``import_name``);
                          present when importable.
      - ``"handoff"``  — a guided sub-flow (``handoff_cmd``, e.g. ``rv compute init``);
                          present when its manifest exists (checked by the caller).
    """

    id: str
    title: str
    unlocks: str
    kind: str = "key"
    keys: tuple[KeySpec, ...] = ()
    request_url: str = ""
    note: str = ""
    import_name: str = ""
    handoff_cmd: str = ""
    cls: str = field(default=CLASS_FEATURE_REQUIRED)


FEATURES: tuple[Feature, ...] = (
    Feature(
        id="provider",
        title="Provider API key(s)",
        unlocks="API-model experiments",
        kind="key",
        keys=PROVIDER_KEYS,
        note=(
            "any ONE provider unlocks API-model experiments; skippable if you run "
            "local models or lit-review only"
        ),
    ),
    Feature(
        id="s2",
        title="Semantic Scholar (s2)",
        unlocks="`rv research find` retrieval",
        kind="key",
        keys=(S2_KEY,),
        request_url=S2_KEY.request_url,
    ),
    Feature(
        id="asta",
        title="asta",
        unlocks="`rv research find --deep`",
        kind="package",
        import_name="asta",
        request_url="https://share.hsforms.com/1L4hUh20oT3mu8iXJQMV77w3ioxm",
        note=(
            "the access request needs an institutional email (not a personal "
            "gmail); see allenai.org/asta/resources/mcp"
        ),
    ),
    Feature(
        id="wandb",
        title="Weights & Biases (wandb)",
        unlocks="experiment observability + `rv wandb pull`",
        kind="key",
        keys=(WANDB_KEY,),
        request_url=WANDB_KEY.request_url,
    ),
    Feature(
        id="zotero",
        title="Zotero",
        unlocks="`rv cite`",
        kind="key",
        keys=(ZOTERO_KEY,),
        request_url=ZOTERO_KEY.request_url,
    ),
    Feature(
        id="compute",
        title="Remote compute",
        unlocks="remote-cluster experiments",
        kind="handoff",
        handoff_cmd="rv compute init",
        note="hands off to the guided compute-manifest flow (`rv compute init`)",
    ),
)

_FEATURE_BY_ID: dict[str, Feature] = {f.id: f for f in FEATURES}


def get_feature(feature_id: str) -> Feature:
    """Return the Feature with ``id == feature_id``. Raises KeyError if unknown."""
    return _FEATURE_BY_ID[feature_id]


# ---------------------------------------------------------------------------
# Resolution + storage — the unified read/write path
# ---------------------------------------------------------------------------

def mask(value: str) -> str:
    """Return a masked prefix of a secret — NEVER the full value.

    <=4 chars → fully hidden.  Otherwise the first 6 chars + an ellipsis, enough
    to eyeball-verify the right key without ever echoing it.
    """
    v = value.strip()
    if len(v) <= 4:
        return "***"
    return v[:6] + "…"


def resolve_key(
    spec: KeySpec, *, skip_keyring: bool | None = None
) -> tuple[bool, str, str]:
    """Resolve a single key.

    Returns ``(present, source, masked)`` where ``source`` is ``"env"`` |
    ``"keyring"`` | ``""``.  Never raises; never returns the plaintext value.

    Resolution order: env var (highest) → system keyring.  ``skip_keyring``
    defaults to the ``VAULT_SKIP_KEYRING`` env flag (so tests/CI can disable it).
    """
    val = os.environ.get(spec.env_var, "").strip()
    if val:
        return True, "env", mask(val)

    if skip_keyring is None:
        skip_keyring = bool(os.environ.get("VAULT_SKIP_KEYRING"))
    if not skip_keyring:
        try:
            import keyring  # type: ignore[import]
            stored = keyring.get_password(KEYRING_SERVICE, spec.keyring_username)
            if stored and stored.strip():
                return True, "keyring", mask(stored.strip())
        except ImportError:
            pass
        except Exception:
            pass

    return False, "", ""


def resolve_any(
    specs: tuple[KeySpec, ...] | list[KeySpec], *, skip_keyring: bool | None = None
) -> tuple[bool, list[tuple[KeySpec, str, str]]]:
    """Resolve a group of keys (e.g. provider-plural).

    Returns ``(present, hits)`` where ``present`` is True if ANY key resolved and
    ``hits`` is ``[(spec, source, masked), ...]`` for the resolved ones.
    """
    hits: list[tuple[KeySpec, str, str]] = []
    for spec in specs:
        present, source, masked = resolve_key(spec, skip_keyring=skip_keyring)
        if present:
            hits.append((spec, source, masked))
    return (len(hits) > 0), hits


def store_key(spec: KeySpec, value: str) -> None:
    """Write a secret to the system keyring under the unified (service, username).

    Raises ImportError if ``keyring`` is not installed (the caller surfaces it).
    Never logs the value.
    """
    import keyring  # type: ignore[import]
    keyring.set_password(KEYRING_SERVICE, spec.keyring_username, value.strip())
