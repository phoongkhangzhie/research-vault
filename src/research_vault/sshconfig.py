# SPDX-License-Identifier: AGPL-3.0-or-later
"""sshconfig.py — read-only ~/.ssh/config alias detector for the compute wizard.

The guided compute-onboarding wizard's *host* step auto-detects the ssh aliases
already declared in the adopter's ``~/.ssh/config`` so they pick from a menu
instead of typing a literal host.  ``remote.py`` runs ``ssh <host>`` directly, so
the *alias* is exactly what a compute profile's ``host`` field must store — never
a duplicated ``HostName``.  ``HostName``/``User`` are captured for DISPLAY ONLY
(to help the human recognise which alias is which).

Hard guarantees (safety-critical — the wizard mutates only the compute manifest):
  - **Strictly read-only.**  This module never opens any path for writing.  It
    only reads text.  The ``~/.ssh/config`` file (and its includes) are left
    byte- and mtime-identical.
  - **Never raises.**  A missing, empty, malformed, or permission-denied config
    yields ``[]`` (or a partial result) — never an exception.
  - **Include following is bounded.**  ``Include`` directives are expanded
    (globs relative to ``~/.ssh/``), guarded by a depth cap AND a visited-set
    cycle guard.  Unreadable includes are surfaced in an optional ``skipped_out``
    list, never crashing the scan.

Stdlib only.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path

# Bound on Include recursion — real configs nest 1-2 deep; 16 is a generous cap
# that still terminates promptly on a pathological chain the visited-set misses.
_MAX_INCLUDE_DEPTH = 16


@dataclass
class SshAlias:
    """A concrete (non-wildcard) ssh Host alias.

    ``alias`` is the value stored in a compute profile's ``host`` field.
    ``hostname`` / ``user`` are DISPLAY-ONLY hints (never persisted to the
    manifest — the alias alone drives ``ssh <host>``).
    """

    alias: str
    hostname: str | None = None
    user: str | None = None
    source_file: str = ""


def _default_config_path() -> Path:
    return Path(os.path.expanduser("~/.ssh/config"))


def _is_concrete_pattern(pattern: str) -> bool:
    """A pattern is a usable alias iff it has no wildcard/negation metacharacters."""
    if not pattern:
        return False
    if pattern.startswith("!"):
        return False  # negation
    if "*" in pattern or "?" in pattern:
        return False  # wildcard
    return True


def _split_keyword(line: str) -> tuple[str, list[str]] | None:
    """Split an ssh-config line into (keyword_lower, [args]).

    Handles both ``Keyword arg`` and ``Keyword=arg`` forms.  Returns None for
    comment/blank lines.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    # `Keyword=value` — normalise the first '=' to a space, then tokenise.
    if "=" in stripped:
        head, _, tail = stripped.partition("=")
        if " " not in head.strip():
            stripped = f"{head.strip()} {tail.strip()}"
    parts = stripped.split()
    if not parts:
        return None
    return parts[0].lower(), parts[1:]


def _parse_file(
    path: Path,
    *,
    follow_includes: bool,
    ssh_dir: Path,
    aliases_out: list[SshAlias],
    visited: set[str],
    skipped_out: list[str] | None,
    depth: int,
) -> None:
    """Parse one config file, appending SshAlias objects to ``aliases_out``.

    Recurses into ``Include`` files when ``follow_includes`` is True, guarded by
    ``visited`` (cycle guard) and ``depth`` (depth cap).  Never raises.
    """
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    if resolved in visited:
        return
    visited.add(resolved)

    try:
        # READ-ONLY.  errors="replace" makes malformed bytes non-fatal.
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        if skipped_out is not None:
            skipped_out.append(str(path))
        return

    # `current_block` holds the SshAlias objects for the most-recent Host line;
    # HostName/User keywords attach to them (first value wins, per ssh semantics).
    current_block: list[SshAlias] = []

    for raw_line in text.splitlines():
        parsed = _split_keyword(raw_line)
        if parsed is None:
            continue
        keyword, args = parsed

        if keyword == "host":
            current_block = []
            for pattern in args:
                if _is_concrete_pattern(pattern):
                    a = SshAlias(alias=pattern, source_file=str(path))
                    aliases_out.append(a)
                    current_block.append(a)
        elif keyword == "match":
            # A Match block ends the current Host stanza — its options must not
            # leak onto the preceding Host's aliases.
            current_block = []
        elif keyword == "hostname" and args:
            for a in current_block:
                if a.hostname is None:
                    a.hostname = args[0]
        elif keyword == "user" and args:
            for a in current_block:
                if a.user is None:
                    a.user = args[0]
        elif keyword == "include" and follow_includes:
            if depth >= _MAX_INCLUDE_DEPTH:
                continue
            for token in args:
                _follow_include(
                    token,
                    ssh_dir=ssh_dir,
                    follow_includes=follow_includes,
                    aliases_out=aliases_out,
                    visited=visited,
                    skipped_out=skipped_out,
                    depth=depth + 1,
                )


def _follow_include(
    token: str,
    *,
    ssh_dir: Path,
    follow_includes: bool,
    aliases_out: list[SshAlias],
    visited: set[str],
    skipped_out: list[str] | None,
    depth: int,
) -> None:
    """Expand one Include token (glob, relative to ~/.ssh/) and parse each match."""
    expanded = os.path.expanduser(token)
    p = Path(expanded)
    # Relative includes resolve against the ssh dir (ssh's documented behaviour).
    pattern = expanded if p.is_absolute() else str(ssh_dir / expanded)

    try:
        matches = sorted(glob.glob(pattern))
    except OSError:
        matches = []

    if not matches:
        # A literal (non-glob) include that matched nothing is a real miss worth
        # surfacing; a glob that matched nothing is benign but we still note it.
        if skipped_out is not None:
            skipped_out.append(pattern)
        return

    for match in matches:
        mp = Path(match)
        if not mp.is_file():
            continue
        _parse_file(
            mp,
            follow_includes=follow_includes,
            ssh_dir=ssh_dir,
            aliases_out=aliases_out,
            visited=visited,
            skipped_out=skipped_out,
            depth=depth,
        )


def detect_ssh_aliases(
    config_path: str | os.PathLike[str] | None = None,
    *,
    follow_includes: bool = True,
    skipped_out: list[str] | None = None,
) -> list[SshAlias]:
    """Return the concrete ssh Host aliases declared in ``config_path``.

    Read-only.  Missing/empty/malformed config → ``[]`` (never raises).  Wildcard
    (``*``/``?``) and negation (``!``) patterns are excluded — only aliases usable
    as a direct ``ssh <host>`` target are returned.

    Args:
      config_path: path to the ssh config (default ``~/.ssh/config``).
      follow_includes: expand ``Include`` directives (globs relative to ``~/.ssh/``),
        bounded by a depth cap and cycle guard.
      skipped_out: if provided, unreadable/missing includes are appended here for
        the caller to surface (never silently dropped).

    Returns:
      A list of :class:`SshAlias`, in declaration order, de-duplicated by alias
      (first declaration wins — matching ssh's own precedence).
    """
    path = Path(config_path) if config_path is not None else _default_config_path()
    ssh_dir = path.parent if config_path is not None else Path(os.path.expanduser("~/.ssh"))

    aliases: list[SshAlias] = []
    visited: set[str] = set()
    try:
        _parse_file(
            path,
            follow_includes=follow_includes,
            ssh_dir=ssh_dir,
            aliases_out=aliases,
            visited=visited,
            skipped_out=skipped_out,
            depth=0,
        )
    except Exception:
        # Defensive: the detector must NEVER raise into the wizard.
        return aliases

    # De-dup by alias, first declaration wins.
    seen: set[str] = set()
    deduped: list[SshAlias] = []
    for a in aliases:
        if a.alias in seen:
            continue
        seen.add(a.alias)
        deduped.append(a)
    return deduped
