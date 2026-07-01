"""adapters — pluggable adapter layer for Research Vault.

Defines three Protocols that every adapter implementation must satisfy:

  Notifier      — surface events to the operator (inbox, desk, stdout)
  ComputeBackend — submit and query async compute jobs
  SecretStore   — resolve secrets by name without leaking them

Local-default implementations live in this package:
  FileNotifier       (notifier = "file"   in config)
  LocalSubprocess    (backend  = "local"  in config)
  EnvSecretStore     (secrets  = "env"    in config)

Config-driven adapter selection is performed by ``load_adapters(cfg)``,
which returns a bound AdapterSet (notifier + backend + secrets).

Stdlib only — no third-party deps.
"""

from .base import (
    Notifier,
    ComputeBackend,
    SecretStore,
    AdapterSet,
    FileNotifier,
    LocalSubprocess,
    EnvSecretStore,
    load_adapters,
)

__all__ = [
    "Notifier",
    "ComputeBackend",
    "SecretStore",
    "AdapterSet",
    "FileNotifier",
    "LocalSubprocess",
    "EnvSecretStore",
    "load_adapters",
]
