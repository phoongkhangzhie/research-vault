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

CLI activation path (rv control reconcile --gh-pr N):
  ``rv control reconcile --gh-pr N [--repo owner/repo]`` constructs this source
  and passes it as extra_sources.  Repo auto-detected from git remote if omitted.
  ``get_ci_advisory()`` provides the human-facing CI summary line.
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
        # Fetch-result caches: avoid duplicate gh subprocess calls when
        # get_ci_advisory() and get_terminal_set() are called in the same session.
        self._pr_info_cache: "tuple[str, frozenset[str]] | None" = None
        self._checks_cache: "list[dict] | None" = None

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
        """Return sr-* ids from headRefName ONLY when the PR is MERGED and all checks pass.

        Two gates must both be satisfied:
          1. state == "merged" — the PR has actually been merged by a human.
             A green-but-OPEN PR is the normal awaiting-human-go state in this
             crew; it is NOT terminal.  Without this gate, every reconcile while
             a PR waits for the operator merge produces a false [R4] STALE alarm.
          2. All non-skipping checks have bucket=="pass" (CI green).

        D-CIF-1 hard-refuse: any fail/pending/cancel check, or zero checks →
        contributes NOTHING.  bucket=="skipping" is non-blocking (pass-through).
        On fetch error, raises so the combined-set builder emits its
        "false GREEN possible" warning (control.py:300-311) and skips.

        HARD BOUNDARY: this method returns frozenset[str] only.  No write,
        approve, or verdict path exists here — the boundary is unchanged.
        """
        state, branch_ids = self._fetch_pr_info()

        # Gate 1: PR must be actually merged — not merely green.
        # A green-but-open PR is awaiting the human-go operator merge; it is
        # the NORMAL state for this crew and must not be marked terminal.
        if state != "merged":
            return frozenset()

        if not branch_ids:
            # No sr-* tokens in branch name — nothing to contribute
            return frozenset()

        checks = self._fetch_checks()

        if not checks:
            # Zero checks → conservative: cannot confirm green
            return frozenset()

        # Gate 2: non-skipping checks must all be "pass"
        non_skipping = [c for c in checks if c.get("bucket") != "skipping"]
        if not non_skipping:
            # All checks are skipping — conservative: cannot confirm green
            return frozenset()

        all_pass = all(c.get("bucket") == "pass" for c in non_skipping)
        if all_pass:
            return branch_ids
        return frozenset()

    # ------------------------------------------------------------------
    # Human-facing advisory surface (CLI activation path)
    # ------------------------------------------------------------------

    def get_ci_advisory(self) -> str:
        """Return a human-readable CI summary line for display.

        Advisory only — never used for gate logic.  Call before or after
        get_terminal_set(); caching means no extra gh subprocess calls are made.

        Returns one of:
          "CI: GREEN (PR #N)"
          "CI: RED (PR #N — <failing-check-name>, ...)"
          "CI: PENDING (PR #N — N check(s) still running)"
          "CI: UNVERIFIED (PR #N — <reason>)"
        """
        try:
            _, _ = self._fetch_pr_info()
            checks = self._fetch_checks()
        except Exception as exc:
            return f"CI: UNVERIFIED (PR #{self._pr} — {exc})"

        if not checks:
            return f"CI: UNVERIFIED (PR #{self._pr} — no checks found)"

        non_skipping = [c for c in checks if c.get("bucket") != "skipping"]
        if not non_skipping:
            return f"CI: UNVERIFIED (PR #{self._pr} — all checks skipping)"

        pending = [c for c in non_skipping if c.get("bucket") == "pending"]
        if pending:
            return f"CI: PENDING (PR #{self._pr} — {len(pending)} check(s) still running)"

        failed = [c for c in non_skipping if c.get("bucket") != "pass"]
        if failed:
            names = ", ".join(c["name"] for c in failed[:3])
            return f"CI: RED (PR #{self._pr} — {names})"

        return f"CI: GREEN (PR #{self._pr})"

    # ------------------------------------------------------------------
    # Private helpers — gh subprocess calls (with instance-level cache)
    # ------------------------------------------------------------------

    def _fetch_pr_info(self) -> tuple[str, frozenset[str]]:
        """Fetch PR state and branch-derived sr-* ids in one gh call.

        Results are cached on the instance — subsequent calls return the
        same result without issuing another subprocess.

        Calls ``gh pr view <N> --repo <repo> --json state,headRefName``.

        Returns:
            (state, branch_ids) where:
            - state: "open" | "closed" | "merged" | "unknown" (lowercased)
            - branch_ids: frozenset of sr-* tokens extracted from headRefName
              via _ID_TOKEN_RE (empty if branch name has no sr-* tokens)

        Raises RuntimeError (or FileNotFoundError if gh absent) on failure,
        so the combined-set builder's except clause fires and warns.
        """
        if self._pr_info_cache is not None:
            return self._pr_info_cache

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
        self._pr_info_cache = (state, ids)
        return self._pr_info_cache

    def _fetch_checks(self) -> list[dict]:
        """Fetch PR check results via ``gh pr checks --json name,state,bucket``.

        Results are cached on the instance — subsequent calls return the
        same result without issuing another subprocess.

        Returns a list of dicts: [{"name": str, "state": str, "bucket": str}].

        Real ``gh pr checks --json`` output (gh 2.9x):
            [{"bucket": "pass", "name": "...", "state": "SUCCESS"}, ...]

        ``bucket`` values: "pass" | "fail" | "pending" | "skipping" | "cancel"
        ``state`` is the raw GitHub check conclusion (SUCCESS, FAILURE, etc.);
        ``bucket`` is the canonical field to key off.

        Raises RuntimeError (or FileNotFoundError if gh absent) on failure,
        so the combined-set builder's except clause fires and warns.
        """
        if self._checks_cache is not None:
            return self._checks_cache

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
            self._checks_cache = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            raise RuntimeError(
                f"gh pr checks --json returned unexpected output for PR #{self._pr}: "
                f"{result.stdout[:200]!r}"
            )
        return self._checks_cache
