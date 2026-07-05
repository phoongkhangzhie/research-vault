"""adapters/secret_forward.py — command-line-clean secret forwarding to remote jobs.

The gap: a remote compute job that logs to W&B (or any authed service) needs the
credential in its process env ON THE COMPUTE NODE, but no forwarding exists. The
existing ``env=`` submit path routes values ONTO THE COMMAND LINE (``--export=KEY=val``
or ``KEY=val cmd``) — visible via ``ps aux`` / ``scontrol show job`` / scheduler
accounting. Secrets must therefore NEVER reuse the ``env`` path.

Security spine (the load-bearing invariant, verified by tests):
  * command-line-clean — a secret VALUE appears on NO argv: not the local ``ssh``
    argv (delivered via ``input=`` STDIN), not ``--export``, not the remote process
    argv (sourced from a file).
  * never on local disk — the plaintext lives only in process memory + the kernel
    pipe (ssh STDIN) + a mode-600 remote file that is sourced then immediately deleted.
  * names-not-values — only env-var NAMES appear in the manifest / logs / reports.

Trust model: the secret NAMES are validated against ``^[A-Za-z_][A-Za-z0-9_]*$`` so
they cannot inject shell into the sourced file. Secret VALUES are ``shlex.quote``-d
into ``export NAME='...'`` lines, safe under ``.`` (source). The remote scratch dir
comes from the adopter-authored manifest (trusted, like ``host`` / ``submit_pattern``)
and is left unquoted so a leading ``$HOME`` expands on the remote shell.

Stdlib only — no third-party deps.
"""
from __future__ import annotations

import re
import secrets as _sysrandom
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Protocol

# Env-var-name form. Rejecting anything else keeps the sourced file free of
# shell metacharacters (a name is written verbatim as ``export <NAME>=...``).
_SECRET_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DEFAULT_SCRATCH = "$HOME/.rv-secrets"
_DEFAULT_TTL_MINUTES = 720
_STAGE_TIMEOUT = 30  # seconds; staging is a single small write, must be fast


class _SecretStoreLike(Protocol):
    def get(self, name: str) -> str: ...


def validate_secret_name(name: Any) -> None:
    """Raise ValueError unless ``name`` is a valid env-var name.

    The gate that prevents injection into the remote sourced file: only
    ``^[A-Za-z_][A-Za-z0-9_]*$`` is allowed. Anything else (dashes, spaces,
    ``;``, ``$(...)``, empty) is rejected loudly.
    """
    if not isinstance(name, str) or not _SECRET_NAME_RE.match(name):
        raise ValueError(
            f"Invalid secret name {name!r} in secrets_forward: must match "
            f"^[A-Za-z_][A-Za-z0-9_]*$ (env-var-name form). Rejected to prevent "
            f"injection into the remotely-sourced secret file. Use the plain "
            f"env-var NAME (e.g. WANDB_API_KEY), never a value."
        )


def resolve_secrets(names: list[str], store: _SecretStoreLike) -> dict[str, str]:
    """Validate every name, then resolve every value. Fail-closed, NO network I/O.

    Called BEFORE any ssh call so a missing secret aborts the submit before a
    job is ever sent. Raises:
      * ValueError — a name is malformed (validate_secret_name).
      * RuntimeError — a name does not resolve (env unset + not in keyring),
        naming the offending secret.
    Returns an insertion-ordered ``{name: value}`` mapping on success.
    """
    for n in names:
        validate_secret_name(n)
    resolved: dict[str, str] = {}
    for n in names:
        try:
            resolved[n] = store.get(n)
        except KeyError as e:
            raise RuntimeError(
                f"secrets_forward: required secret {n!r} could not be resolved "
                f"(env var ${n} is unset and it is not in the keyring). Set it "
                f"before submitting. Resolved BEFORE any ssh call — no job was sent."
            ) from e
    return resolved


def build_secret_blob(resolved: dict[str, str]) -> str:
    """Build the sourceable env-file content: one ``export NAME='value'`` per line.

    Values are ``shlex.quote``-d so arbitrary content is safe under ``.`` (source).
    This string lives only in memory + the ssh STDIN pipe — never on any argv.
    """
    return "".join(
        f"export {name}={shlex.quote(value)}\n" for name, value in resolved.items()
    )


@dataclass(frozen=True)
class SecretForwardPlan:
    """A single submit's forwarding plan: where + which nonce + TTL.

    Immutable. Carries NO secret value — only the remote scratch dir, a random
    nonce, and the orphan-sweep TTL. The blob (with values) is built separately
    and passed through STDIN, never stored on the plan.
    """

    scratch: str        # remote dir; may contain $HOME (expanded remotely)
    nonce: str          # 32 hex chars from secrets.token_hex(16)
    ttl_minutes: int    # orphan-sweep age

    @property
    def secfile(self) -> str:
        """Remote path of the staged secret file.

        Unquoted scratch so a leading ``$HOME`` expands on the remote shell; the
        nonce is hex (shell-safe). This exact string is reused verbatim in the
        stage script, the activation wrapper, and the cleanup rm.
        """
        return f"{self.scratch}/{self.nonce}.env"

    def stage_script(self) -> str:
        """The remote ``sh`` script that receives the blob on STDIN and writes it 0600.

        Contains NO secret value — only scratch / nonce / ttl — so it is safe on
        the ``ssh`` argv. Also sweeps orphaned ``*.env`` files older than the TTL.
        """
        return (
            f'umask 077; d={self.scratch}; mkdir -p "$d"; '
            f'f="$d/{self.nonce}.env"; cat > "$f"; chmod 600 "$f"; '
            f"find \"$d\" -maxdepth 1 -name '*.env' -mmin +{self.ttl_minutes} "
            f"-delete 2>/dev/null; true"
        )

    def activation_wrapper(
        self,
        *,
        cwd: str | None,
        nonsecret_env: dict[str, str] | None,
        cmd: list[str],
    ) -> str:
        """The remote ``sh -c`` body: source the secfile, delete it, then run cmd.

        The secret value is sourced from the file — it never appears in this
        string (nor, therefore, on any argv). A ``trap`` also removes the file on
        EXIT/INT/TERM so an early kill cannot leave the plaintext behind. The
        non-secret ``env`` prefix and ``cwd`` are applied here too (they replace
        the normal env/cwd wiring, which is bypassed when secrets are present).
        """
        parts = [
            f"SECFILE={self.secfile}",
            'trap \'rm -f "$SECFILE"\' EXIT INT TERM',
            '. "$SECFILE"',
            'rm -f "$SECFILE"',
        ]
        tail = ""
        if cwd:
            tail += "cd " + shlex.quote(cwd) + " && "
        env_prefix = " ".join(
            f"{k}={shlex.quote(v)}" for k, v in (nonsecret_env or {}).items()
        )
        if env_prefix:
            tail += env_prefix + " "
        tail += shlex.join(cmd)
        parts.append(tail)
        return "; ".join(parts)


def make_plan(scratch: str | None, ttl_minutes: Any) -> SecretForwardPlan:
    """Build a plan with a fresh nonce and the manifest's scratch/TTL (or defaults)."""
    try:
        ttl = int(ttl_minutes)
    except (TypeError, ValueError):
        ttl = _DEFAULT_TTL_MINUTES
    return SecretForwardPlan(
        scratch=(scratch or _DEFAULT_SCRATCH),
        nonce=_sysrandom.token_hex(16),
        ttl_minutes=ttl if ttl > 0 else _DEFAULT_TTL_MINUTES,
    )


def stage_over_stdin(host: str, plan: SecretForwardPlan, blob: str) -> None:
    """Deliver the blob to the remote secfile via ssh STDIN — never on argv.

    ``subprocess.run`` is called by attribute (not bound at def time) so tests
    can patch ``subprocess.run``. Raises RuntimeError on nonzero exit — the
    caller must then NOT submit.
    """
    try:
        result = subprocess.run(
            ["ssh", host, plan.stage_script()],
            input=blob,
            text=True,
            capture_output=True,
            timeout=_STAGE_TIMEOUT,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "ssh not found — cannot stage forwarded secrets. Install "
            "openssh-client or remove secrets_forward from the profile."
        ) from e
    if result.returncode != 0:
        raise RuntimeError(
            f"secrets_forward: staging the secret file on {host!r} failed "
            f"(exit {result.returncode}): {(result.stderr or '')[:200]}"
        )


def best_effort_cleanup(host: str, plan: SecretForwardPlan) -> None:
    """Fire-and-forget remote rm of the secfile. NEVER raises, never masks errors.

    Called when the submit itself fails after staging succeeded — the trap in the
    wrapper never fires in that case (the job never started), so we clean up here.
    The secfile path is left unquoted so ``$HOME`` expands remotely (nonce is hex).
    """
    try:
        subprocess.run(
            ["ssh", host, f"rm -f {plan.secfile}"],
            capture_output=True,
            text=True,
            timeout=_STAGE_TIMEOUT,
        )
    except Exception:
        pass  # best-effort only — the TTL sweeper is the backstop
