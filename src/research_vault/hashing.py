# SPDX-License-Identifier: AGPL-3.0-or-later
"""hashing.py — shared file-hash utility (sha256 streaming).

Extracted from wandb_pull.py (SR-FIG-METHOD-AB, C2) so the render-script
domain and the wandb-pull domain share ONE implementation and can never drift.

Usage::

    from research_vault.hashing import hash_file
    digest = hash_file(Path("results.csv"))  # "sha256:<hex>"

The ``"sha256:<hex>"`` format is the canonical hash format used throughout
research-vault (experiment notes, figure notes, results manifests).  Any
domain that hashes a data file must use this module so the digest format
remains consistent.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def hash_file(path: Path) -> str:
    """Compute sha256 hash of a file via streaming chunked read.

    Reads in 1 MiB chunks to handle large artifacts without loading the
    entire file into memory.

    Args:
        path: Path to the file to hash.

    Returns:
        ``"sha256:<hex>"`` — lowercase hex digest prefixed with the algorithm
        name, e.g. ``"sha256:e3b0c44298fc1c149afbf4c8996fb924..."``.

    Raises:
        OSError: if the file cannot be opened (propagated from ``open``).
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):  # 1 MiB chunks
            h.update(chunk)
    return "sha256:" + h.hexdigest()
