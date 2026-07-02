"""adapters/github_ci.py — SR-CIF: tier-3 GitHub Actions CI fetch as a SignalSource.

GitHubActionsSource implements the SignalSource Protocol (status.py:47) and plugs
into the existing ``extra_sources`` seam (control.py:453, 293, 334).

TIER-3 / OPT-IN — requires ``gh`` (GitHub CLI) and a GitHub repository.
A zero-infra adopter never constructs this source; reconcile/status/approve stay
fully local on the SR-CI local artifact gate.

HARD BOUNDARY (crew-cannot-self-approve):
  This source FETCHES + SURFACES CI truth. It NEVER auto-approves.
  The SignalSource interface only returns frozenset[str] — no write, no approve,
  no verdict-header write. The crew-cannot-self-approve block lives in the gate
  (control.py:562-604) and is completely untouched by this module.

  By construction: the worst a mis-authored source can do is contribute (or
  withhold) an id from the terminal set. The gate still owns the verdict.

Usage (opt-in, from cmd_reconcile caller):
    from research_vault.adapters.github_ci import GitHubActionsSource
    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    findings = cmd_reconcile("my-project", extra_sources=[src])

Green semantics (D-CIF-4):
  "green" = all REQUIRED checks concluded success.
  Failing optional checks do NOT block green.
  This matches branch-protection semantics.

gh absent → raises (FileNotFoundError / RuntimeError); the combined-set builder
  skips with a loud "false GREEN possible" warning (control.py:300-311) — the gate
  does NOT go green on an unverified fetch. Zero crash, zero false-green.

NO poller (D-CIF-3 DEFER): one-shot fetch at reconcile/gate time only.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Config


class GitHubActionsSource:
    """SignalSource: GitHub Actions CI state fetched via ``gh pr checks``.

    Implements the SignalSource Protocol (status.py:47) for the tier-3 GitHub
    adapter (SR-CIF). Plugs into the ``extra_sources`` seam with zero core changes.

    Green semantics: all REQUIRED checks concluded ``pass``.
    Failing optional checks do NOT block green (branch-protection semantics, D-CIF-4).

    On fetch error (gh absent, API error, auth missing): raises so the combined-set
    builder emits "false GREEN possible" and skips this source — the gate cannot
    record a terminal signal on an unverified fetch (D-CIF-1 hard-refuse).

    Args:
        repo:      GitHub repository slug, e.g. "owner/repo".
        pr_number: Pull-request number as an integer.
    """

    def __init__(self, repo: str, pr_number: int) -> None:
        self._repo = repo
        self._pr = pr_number

    # ------------------------------------------------------------------
    # SignalSource Protocol implementation
    # ------------------------------------------------------------------

    def build_live_set(self, config: "Config", project: str) -> frozenset[str]:
        """Return ``{pr-<n>}`` when the PR is open (not yet merged/closed).

        A PR that is open is "live" (in-flight). On fetch error, raises so the
        combined-set builder can warn and skip rather than silently omit.
        """
        state = self._fetch_pr_state()
        if state == "open":
            return frozenset({f"pr-{self._pr}"})
        return frozenset()

    def get_terminal_set(self, config: "Config", project: str) -> frozenset[str]:
        """Return ``{pr-<n>}`` ONLY when ALL required checks concluded success.

        D-CIF-1 hard-refuse: red / pending / unverified → contributes NOTHING.
        On fetch error, raises so the combined-set builder emits its
        "false GREEN possible" warning (control.py:300-311) and skips.
        """
        checks = self._fetch_checks()
        required_checks = [c for c in checks if c["required"]]

        if not required_checks:
            # No required checks at all — treat as not-verified (conservative).
            # A PR with zero required checks cannot be confirmed green by construction.
            return frozenset()

        all_pass = all(c["state"] == "pass" for c in required_checks)
        if all_pass:
            return frozenset({f"pr-{self._pr}"})
        return frozenset()

    # ------------------------------------------------------------------
    # Private helpers — gh subprocess calls
    # ------------------------------------------------------------------

    def _fetch_checks(self) -> list[dict]:
        """Fetch PR check results via ``gh pr checks``.

        Returns a list of dicts: [{"name": str, "state": str, "required": bool}].

        gh pr checks output (tab-delimited):
            <name> \\t <state> \\t <required> \\t <link>

        ``state`` values from gh: "pass" | "fail" | "pending" | "skipping"
        ``required`` column: "true" | "false"

        Raises RuntimeError (or FileNotFoundError if gh absent) on failure,
        so the combined-set builder's except clause fires and warns.
        """
        result = subprocess.run(
            [
                "gh", "pr", "checks", str(self._pr),
                "--repo", self._repo,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"gh pr checks failed for PR #{self._pr} in {self._repo!r}: "
                f"{result.stderr.strip()!r}"
            )

        checks: list[dict] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            name = parts[0].strip()
            state = parts[1].strip().lower()
            required = parts[2].strip().lower() == "true"
            checks.append({"name": name, "state": state, "required": required})

        return checks

    def _fetch_pr_state(self) -> str:
        """Fetch PR open/closed/merged state via ``gh pr view``.

        Returns one of: "open" | "closed" | "merged" | "unknown".

        Raises RuntimeError (or FileNotFoundError if gh absent) on failure.
        """
        result = subprocess.run(
            [
                "gh", "pr", "view", str(self._pr),
                "--repo", self._repo,
                "--json", "state",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"gh pr view failed for PR #{self._pr} in {self._repo!r}: "
                f"{result.stderr.strip()!r}"
            )

        try:
            data = json.loads(result.stdout)
            return data.get("state", "unknown").lower()
        except (json.JSONDecodeError, AttributeError):
            raise RuntimeError(
                f"gh pr view returned unexpected output for PR #{self._pr}: "
                f"{result.stdout[:200]!r}"
            )
