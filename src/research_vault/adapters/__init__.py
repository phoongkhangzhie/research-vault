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

# RemoteBackend is exported lazily via PEP 562 module __getattr__ so that
# importing this package when backend=local does NOT pull in adapters.remote
# (the ssh/subprocess import).  Callers that explicitly need RemoteBackend get
# it through normal attribute access or 'from research_vault.adapters.remote
# import RemoteBackend' directly.


def __getattr__(name: str):  # noqa: ANN001
    """PEP 562 lazy module attribute — resolves heavy/optional members on first access.

    ModelClient / ObservabilityError are resolved lazily so importing this package
    never imports ``model_client``. The normal reach is ``AdapterSet.model``; these
    exports are for direct typing/use.
    """
    if name == "RemoteBackend":
        from .remote import RemoteBackend  # noqa: PLC0415
        return RemoteBackend
    if name == "ModelClient":
        from .model_client import ModelClient  # noqa: PLC0415
        return ModelClient
    if name == "ObservabilityError":
        from .model_client import ObservabilityError  # noqa: PLC0415
        return ObservabilityError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Notifier",
    "ComputeBackend",
    "SecretStore",
    "AdapterSet",
    "FileNotifier",
    "LocalSubprocess",
    "EnvSecretStore",
    "load_adapters",
    "RemoteBackend",       # Available lazily via __getattr__
    "ModelClient",         # Available lazily via __getattr__ (SR-MODEL-SEAM)
    "ObservabilityError",  # Available lazily via __getattr__ (SR-MODEL-SEAM)
]
