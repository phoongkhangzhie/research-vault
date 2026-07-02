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

Green semantics (D-CIF-4 REVISED):
  "green" = ALL checks concluded bucket=="pass".
  Checks with bucket=="skipping" are non-blocking (pass-through).
  Any bucket in {"fail", "pending", "cancel"} → not green → id withheld.
  Zero checks → conservative (not green).

  The required/optional distinction is unobtainable from ``gh pr checks`` output;
  the operator-confirmed semantics are: green = every check passed.

ID vocabulary:
  The source emits sr-* tokens extracted from the PR's headRefName (branch name)
  via the shared _ID_TOKEN_RE pattern (controllib.py:123).  This mirrors how
  LocalGitSource works (status.py:106-108) and ensures the ids join correctly
  against the control file via _check_r4 / extract_id_tokens.  pr-<N> tokens are
  NOT emitted — they never match _ID_TOKEN_RE and would be silently inert.

gh absent → raises (FileNotFoundError / RuntimeError); the combined-set builder
  skips with a loud "false GREEN possible" warning (control.py:300-311) — the gate
  does NOT go green on an unverified fetch. Zero crash, zero false-green.

NO poller (D-CIF-3 DEFER): one-shot fetch at reconcile/gate time only.

CLI activation path (OUT OF SCOPE):
  Nothing in the shipped CLI constructs GitHubActionsSource automatically.
  Activation is manual (extra_sources=[...]).  A ``rv reconcile --gh-pr N`` flag
  is filed as a separate follow-up SR.
"""
from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Config


class GitHubActionsSource:
    """SignalSource: GitHub Actions CI state fetched via ``gh pr checks --json``.

    Implements the SignalSource Protocol (status.py:47) for the tier-3 GitHub
    adapter (SR-CIF). Plugs into the ``extra_sources`` seam with zero core changes.

    ID vocabulary: emits sr-* tokens from headRefName (the PR branch name), exactly
    as LocalGitSource does.  This is the remote-CI analogue of that source; both
    speak the same join vocabulary so ids reach _check_r4 / _check_r1 correctly.

    Green semantics (D-CIF-4 REVISED): all non-skipping checks must have
    bucket=="pass".  The required/optional distinction is unobtainable from
    ``gh pr checks``; bucket-based green is operator-confirmed semantics.

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
        """Return sr-* ids from headRefName when the PR is open (not yet merged/closed).

        Fetches ``gh pr view --json state,headRefName``.  If the PR is open, runs
        _ID_TOKEN_RE over headRefName and contributes the matching sr-* tokens.
        On fetch error, raises so the combined-set builder warns and skips.
        """
        state, branch_ids = self._fetch_pr_info()
        if state == "open":
            return branch_ids
        return frozenset()

    def get_terminal_set(self, config: "Config", project: str) -> frozenset[str]:
        """Return sr-* ids from headRefName ONLY when ALL checks concluded success.

        D-CIF-1 hard-refuse: any fail/pending/cancel check, or zero checks →
        contributes NOTHING.  bucket=="skipping" is non-blocking (pass-through).
        On fetch error, raises so the combined-set builder emits its
        "false GREEN possible" warning (control.py:300-311) and skips.
        """
        _, branch_ids = self._fetch_pr_info()
        if not branch_ids:
            # No sr-* tokens in branch name — nothing to contribute
            return frozenset()

        checks = self._fetch_checks()

        if not checks:
            # Zero checks → conservative: cannot confirm green
            return frozenset()

        # Non-skipping checks must all be "pass"
        non_skipping = [c for c in checks if c.get("bucket") != "skipping"]
        if not non_skipping:
            # All checks are skipping — conservative: cannot confirm green
            return frozenset()

        all_pass = all(c.get("bucket") == "pass" for c in non_skipping)
        if all_pass:
            return branch_ids
        return frozenset()

    # ------------------------------------------------------------------
    # Private helpers — gh subprocess calls
    # ------------------------------------------------------------------

    def _fetch_pr_info(self) -> tuple[str, frozenset[str]]:
        """Fetch PR state and branch-derived sr-* ids in one gh call.

        Calls ``gh pr view <N> --repo <repo> --json state,headRefName``.

        Returns:
            (state, branch_ids) where:
            - state: "open" | "closed" | "merged" | "unknown" (lowercased)
            - branch_ids: frozenset of sr-* tokens extracted from headRefName
              via _ID_TOKEN_RE (empty if branch name has no sr-* tokens)

        Raises RuntimeError (or FileNotFoundError if gh absent) on failure,
        so the combined-set builder's except clause fires and warns.
        """
        result = subprocess.run(
            [
                "gh", "pr", "view", str(self._pr),
                "--repo", self._repo,
                "--json", "state,headRefName",
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
        except (json.JSONDecodeError, AttributeError):
            raise RuntimeError(
                f"gh pr view returned unexpected output for PR #{self._pr}: "
                f"{result.stdout[:200]!r}"
            )

        state = data.get("state", "unknown").lower()
        branch = data.get("headRefName", "")

        from ..controllib import _ID_TOKEN_RE
        ids = frozenset(m.group(1).lower() for m in _ID_TOKEN_RE.finditer(branch))
        return state, ids

    def _fetch_checks(self) -> list[dict]:
        """Fetch PR check results via ``gh pr checks --json name,state,bucket``.

        Returns a list of dicts: [{"name": str, "state": str, "bucket": str}].

        Real ``gh pr checks --json`` output (gh 2.9x):
            [{"bucket": "pass", "name": "...", "state": "SUCCESS"}, ...]

        ``bucket`` values: "pass" | "fail" | "pending" | "skipping" | "cancel"
        ``state`` is the raw GitHub check conclusion (SUCCESS, FAILURE, etc.);
        ``bucket`` is the canonical field to key off.

        Raises RuntimeError (or FileNotFoundError if gh absent) on failure,
        so the combined-set builder's except clause fires and warns.
        """
        result = subprocess.run(
            [
                "gh", "pr", "checks", str(self._pr),
                "--repo", self._repo,
                "--json", "name,state,bucket",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"gh pr checks failed for PR #{self._pr} in {self._repo!r}: "
                f"{result.stderr.strip()!r}"
            )

        try:
            return json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            raise RuntimeError(
                f"gh pr checks --json returned unexpected output for PR #{self._pr}: "
                f"{result.stdout[:200]!r}"
            )
