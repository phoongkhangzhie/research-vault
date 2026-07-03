"""git_discipline.py — identity-free git-discipline layer for Research Vault.

When to use: ``rv git-discipline`` to install/manage git hooks that enforce
healthy git habits without requiring a named identity system.  Anti-patterns
this prevents: committed-to-main directly · never made a worktree · hand-merged
red CI.

Design: GD.1 (protect-main off structure not identity), GD.2 (core.hooksPath
per-repo), GD.3 (profile-aware check), GD.4 (consent/opt-in install).

Subcommands
-----------
  check --staged [--repo <path>]
      Runs cheapest-reject-first:
        1. protect-main (branch + path keyed, identity-free)
        2. staged leakage scan (profile-aware: secrets everywhere; private
           markers framework-repo-only)
        3. rv lint (when src/ files are staged)
      Called by the .githooks/pre-commit shim.

  commit-msg <file>
      Checks that the commit message subject matches the conventional-commit
      format: ``^(feat|fix|docs|refactor|test|chore|ci|build|perf)(\\(.+\\))?: .+``
      Called by the .githooks/commit-msg shim.

  install [--project <slug> | --all]
      Sets ``core.hooksPath .githooks`` per repo. Creates the .githooks/ dir
      with pre-commit and commit-msg POSIX sh shims if not present. Idempotent.
      Also prints recommended GitHub branch-protection ruleset per repo.

  uninstall [--project <slug> | --all]
      Unsets ``core.hooksPath`` per repo.

  status [--project <slug> | --all]
      Reports install state per repo.

Repo profiles (GD.1 / GD-D7)
------------------------------
  Framework repo (instance_root — the public OSS package):
      Leakage: secrets + private-markers (all 9 classes; it's PUBLIC).
  Project repo (projects[slug].source_dir — the researcher's own repo):
      Leakage: secrets ONLY (class 5).  A project repo is the researcher's own
      possibly-private content; gating it on codenames is wrong.

Leakage rule — crew identity domain (LOAD-BEARING)
----------------------------------------------------
  The real crew identity domain lives in PRIVATE instance config
  (``crew.identity_domain`` in research_vault.toml).  The public repo's
  default is the placeholder ``example.invalid``.  No scanned file may contain
  the real domain string — the scanner's site-URL class catches it.

Stdlib only.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from .config import Config, load_config

# Conventional-commit subject format
_CC_RE = re.compile(
    r"^(feat|fix|docs|refactor|test|chore|ci|build|perf)(\(.+\))?: .+",
    re.IGNORECASE,
)

# Hook shim templates (generic POSIX sh — resolve profile from cwd via config)
_PRE_COMMIT_SHIM = """\
#!/usr/bin/env sh
# pre-commit — Research Vault git-discipline hook
# Calls `rv git-discipline check --staged` which runs:
#   1. protect-main (branch/path-keyed, identity-free)
#   2. staged leakage scan (profile-aware)
#   3. rv lint (when src/ files are staged)
# To bypass (consciously): git commit --no-verify  OR  RV_ALLOW_MAIN_COMMIT=1
exec rv git-discipline check --staged
"""

_COMMIT_MSG_SHIM = """\
#!/usr/bin/env sh
# commit-msg — Research Vault git-discipline hook
# Enforces conventional-commit format on the commit subject.
exec rv git-discipline commit-msg "$1"
"""


# ---------------------------------------------------------------------------
# Core checks
# ---------------------------------------------------------------------------

def _current_branch(repo: Path) -> str:
    """Return the current branch name (or '' on error)."""
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def _staged_paths(repo: Path) -> list[str]:
    """Return the list of staged file paths (relative)."""
    r = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return []
    return [p.strip() for p in r.stdout.splitlines() if p.strip()]


def _allowlist_for(cfg: Config) -> list[str]:
    """Return the protect-main allowlist from config.

    Config key: [git_discipline] protect_main_allowlist (list of path prefixes).
    Default: EMPTY (refuse ALL direct commits to main).
    """
    gd = cfg._raw.get("git_discipline", {})
    return list(gd.get("protect_main_allowlist", []))


def _is_framework_repo(cfg: Config, repo: Path) -> bool:
    """Return True if *repo* is the framework repo (instance_root)."""
    try:
        return repo.resolve() == cfg.instance_root.resolve()
    except Exception:
        return True  # default to framework-repo (stricter)


def check_protect_main(cfg: Config, repo: Path, staged: list[str]) -> str | None:
    """Return an error string if the commit violates protect-main, else None.

    Identity-free: keyed off branch name and staged paths only.
    """
    branch = _current_branch(repo)
    if branch not in ("main", "master"):
        return None  # feature branch — always OK

    # On main: check allowlist
    env_bypass = os.environ.get("RV_ALLOW_MAIN_COMMIT", "")
    if env_bypass.strip() in ("1", "true", "yes"):
        return None

    allowlist = _allowlist_for(cfg)
    if not allowlist:
        # Empty allowlist: refuse ALL staged commits on main
        if staged:
            return (
                f"protect-main: refused commit to '{branch}'. "
                f"Staged: {staged}. "
                f"Work on a branch ('rv wt add <task>') or set "
                f"[git_discipline] protect_main_allowlist in config. "
                f"Bypass consciously: git commit --no-verify  or  "
                f"RV_ALLOW_MAIN_COMMIT=1"
            )
        return None

    # Non-empty allowlist: reject if any staged path is NOT covered
    uncovered = []
    for path in staged:
        covered = any(
            path == prefix or path.startswith(prefix.rstrip("/") + "/")
            for prefix in allowlist
        )
        if not covered:
            uncovered.append(path)

    if uncovered:
        return (
            f"protect-main: refused commit to '{branch}'. "
            f"Staged paths not in allowlist: {uncovered}. "
            f"Allowlist: {allowlist}."
        )
    return None


def check_commit_msg(subject: str) -> str | None:
    """Return an error string if the subject is not conventional-commit, else None."""
    subject = subject.strip().splitlines()[0].strip() if subject.strip() else ""
    if not subject:
        return "commit-msg: subject is empty. Use format: type(scope): description"
    if not _CC_RE.match(subject):
        return (
            f"commit-msg: subject does not match conventional-commit format.\n"
            f"  Got:      {subject!r}\n"
            f"  Expected: <type>(<scope>): <description>\n"
            f"  Types:    feat|fix|docs|refactor|test|chore|ci|build|perf"
        )
    return None


def _run_leakage_scan(
    repo: Path, *, staged: bool = True, framework: bool = True
) -> tuple[int, str]:
    """Run leakage_scan.sh with appropriate flags. Returns (exit_code, output)."""
    # DEV-REPO TOOLING: leakage_scan.sh is a bash script that ships in the repo's
    # scripts/ directory — it is NOT packaged in the wheel (no data/ slot for it).
    # This function is called from the pre-commit hook integration (cmd_check) and
    # is dev/CI tooling, not a user-facing verb. Task #22 part 2 audit — confirmed
    # dev-only. The graceful fallback (fails-open when script not found) already
    # handles the wheel/non-repo context cleanly.
    # Find the script relative to the package installation or repo root.
    candidates = [
        # Running from within the package source tree (development)
        Path(__file__).parent.parent.parent / "scripts" / "leakage_scan.sh",
        # Running from repo root (installed)
        Path.cwd() / "scripts" / "leakage_scan.sh",
    ]
    script = None
    for c in candidates:
        if c.exists():
            script = c
            break

    if script is None:
        # Cannot run scan — warn but don't block (fail-open for scan-unavailable)
        return 0, "(leakage_scan.sh not found — scan skipped)"

    args = ["bash", str(script)]
    if staged:
        args.append("--staged")
    if not framework:
        args.append("--secrets-only")

    r = subprocess.run(
        args,
        capture_output=True,
        text=True,
        cwd=str(repo),
    )
    return r.returncode, r.stdout + r.stderr


def cmd_check(cfg: Config, repo: Path, *, staged: bool = True) -> int:
    """Run all pre-commit checks. Returns exit code."""
    errors: list[str] = []

    # 1. protect-main (cheapest — first)
    staged_paths = _staged_paths(repo) if staged else []
    err = check_protect_main(cfg, repo, staged_paths)
    if err:
        errors.append(err)

    # 2. Staged leakage scan (profile-aware)
    if not errors:  # only if protect-main passed (cheapest-reject-first)
        is_fw = _is_framework_repo(cfg, repo)
        code, out = _run_leakage_scan(repo, staged=staged, framework=is_fw)
        if code != 0:
            errors.append(f"leakage scan FAILED:\n{out}")
        elif "(scan skipped)" not in out:
            pass  # clean

    # 3. rv lint (when src/ files are staged)
    if not errors and staged:
        src_staged = [p for p in staged_paths if p.startswith("src/")]
        if src_staged:
            r = subprocess.run(
                [sys.executable, "-m", "research_vault.cli", "lint"],
                capture_output=True, text=True,
                cwd=str(repo),
            )
            if r.returncode != 0:
                errors.append(f"rv lint FAILED:\n{r.stdout}{r.stderr}")

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    print("rv git-discipline check: OK")
    return 0


# ---------------------------------------------------------------------------
# Install / uninstall / status
# ---------------------------------------------------------------------------

def _hooks_dir(repo: Path) -> Path:
    return repo / ".githooks"


def _write_shims(repo: Path) -> None:
    """Create .githooks/ with pre-commit and commit-msg POSIX sh shims."""
    hooks_dir = _hooks_dir(repo)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    pre_commit = hooks_dir / "pre-commit"
    if not pre_commit.exists():
        pre_commit.write_text(_PRE_COMMIT_SHIM)
    pre_commit.chmod(0o755)

    commit_msg = hooks_dir / "commit-msg"
    if not commit_msg.exists():
        commit_msg.write_text(_COMMIT_MSG_SHIM)
    commit_msg.chmod(0o755)


def _install_repo(repo: Path, *, alias: str = "", verbose: bool = True) -> bool:
    """Install hooks in one repo. Returns True on success."""
    if not repo.exists() or not (repo / ".git").exists():
        if verbose:
            print(f"  {alias or repo}: not a git repo — skipping")
        return False

    # Create shims
    _write_shims(repo)

    # Set core.hooksPath
    r = subprocess.run(
        ["git", "-C", str(repo), "config", "core.hooksPath", ".githooks"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        if verbose:
            print(f"  {alias or repo}: failed to set core.hooksPath: {r.stderr.strip()}")
        return False

    if verbose:
        print(f"  {alias or repo}: installed .githooks/ + core.hooksPath = .githooks")
        print(f"    Hooks: pre-commit (protect-main + leakage + lint), commit-msg (conventional format)")
        _print_branch_protection_guidance(repo, alias=alias or str(repo))

    return True


def _uninstall_repo(repo: Path, *, alias: str = "", verbose: bool = True) -> bool:
    """Uninstall hooks in one repo. Returns True on success."""
    if not repo.exists() or not (repo / ".git").exists():
        if verbose:
            print(f"  {alias or repo}: not a git repo — skipping")
        return False

    r = subprocess.run(
        ["git", "-C", str(repo), "config", "--unset", "core.hooksPath"],
        capture_output=True, text=True,
    )
    if r.returncode not in (0, 5):  # 5 = key not found (already unset)
        if verbose:
            print(f"  {alias or repo}: failed to unset core.hooksPath: {r.stderr.strip()}")
        return False

    if verbose:
        print(f"  {alias or repo}: uninstalled (core.hooksPath removed)")
    return True


def _status_repo(repo: Path, *, alias: str = "") -> dict:
    """Return status dict for one repo."""
    label = alias or str(repo)
    if not repo.exists() or not (repo / ".git").exists():
        return {"label": label, "installed": False, "reason": "not a git repo"}

    r = subprocess.run(
        ["git", "-C", str(repo), "config", "core.hooksPath"],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and ".githooks" in r.stdout:
        hooks_dir = _hooks_dir(repo)
        has_pre_commit = (hooks_dir / "pre-commit").exists()
        has_commit_msg = (hooks_dir / "commit-msg").exists()
        return {
            "label": label,
            "installed": True,
            "hooks_path": r.stdout.strip(),
            "pre_commit": has_pre_commit,
            "commit_msg": has_commit_msg,
        }
    return {"label": label, "installed": False, "reason": "core.hooksPath not set"}


def _print_branch_protection_guidance(repo: Path, *, alias: str) -> None:
    """Print recommended GitHub branch-protection ruleset for the repo."""
    print(f"")
    print(f"  Recommended GitHub branch-protection for {alias}:")
    print(f"    gh api repos/<owner>/<repo>/branches/main/protection --method PUT \\")
    print(f"      --field required_pull_request_reviews[required_approving_review_count]=1 \\")
    print(f"      --field enforce_admins=true")
    print(f"    Rules: require PR · require CI status checks · block force-push/delete")
    print(f"    NOTE: 'require different reviewer' is NOT enforced here — that needs a")
    print(f"    second GitHub account. The hooks + doctrine are the gate without one.")
    print(f"    A purely local repo has no server-side protection — hooks + doctrine only.")


def _get_repos(cfg: Config, *, project: str | None = None,
               all_repos: bool = False) -> dict[str, Path]:
    """Return the target repo map based on flags."""
    if project:
        try:
            proj = cfg.project(project)
            src = proj.get("source_dir")
            if not src:
                raise ValueError(f"Project {project!r} has no source_dir")
            return {project: Path(src).expanduser()}
        except KeyError as e:
            raise ValueError(str(e)) from e

    if all_repos:
        repos: dict[str, Path] = {}
        # Framework repo first
        repos["_framework"] = cfg.instance_root
        for slug in cfg.all_project_slugs():
            proj = cfg.projects[slug]
            src = proj.get("source_dir")
            if src:
                repos[slug] = Path(src).expanduser()
        return repos

    # Default: framework repo only
    return {"_framework": cfg.instance_root}


def cmd_install(
    cfg: Config,
    *,
    project: str | None = None,
    all_repos: bool = False,
) -> int:
    """Install git-discipline hooks. Returns exit code."""
    print("rv git-discipline install:")
    try:
        repos = _get_repos(cfg, project=project, all_repos=all_repos)
    except ValueError as e:
        print(f"  error: {e}", file=sys.stderr)
        return 1

    any_fail = False
    for alias, repo in repos.items():
        ok = _install_repo(repo, alias=alias)
        if not ok:
            any_fail = True

    if not any_fail and not all_repos and not project:
        # Also offer the --all one-liner
        print("")
        print("  To install in all registered project repos too:")
        print("    rv git-discipline install --all")

    return 1 if any_fail else 0


def cmd_uninstall(
    cfg: Config,
    *,
    project: str | None = None,
    all_repos: bool = False,
) -> int:
    """Uninstall git-discipline hooks. Returns exit code."""
    print("rv git-discipline uninstall:")
    try:
        repos = _get_repos(cfg, project=project, all_repos=all_repos)
    except ValueError as e:
        print(f"  error: {e}", file=sys.stderr)
        return 1

    any_fail = False
    for alias, repo in repos.items():
        ok = _uninstall_repo(repo, alias=alias)
        if not ok:
            any_fail = True
    return 1 if any_fail else 0


def cmd_status(
    cfg: Config,
    *,
    project: str | None = None,
    all_repos: bool = False,
) -> int:
    """Print install status per repo. Returns exit code."""
    try:
        repos = _get_repos(cfg, project=project, all_repos=all_repos)
    except ValueError as e:
        print(f"rv git-discipline status: error: {e}", file=sys.stderr)
        return 1

    any_not_installed = False
    for alias, repo in repos.items():
        s = _status_repo(repo, alias=alias)
        if s["installed"]:
            pre = "yes" if s.get("pre_commit") else "MISSING"
            cmsg = "yes" if s.get("commit_msg") else "MISSING"
            print(
                f"  {s['label']}: INSTALLED  "
                f"(core.hooksPath={s['hooks_path']}, "
                f"pre-commit={pre}, commit-msg={cmsg})"
            )
        else:
            print(
                f"  {s['label']}: NOT INSTALLED  "
                f"({s.get('reason', 'unknown reason')})"
            )
            any_not_installed = True

    if any_not_installed:
        print("")
        print("  Install with: rv git-discipline install  (or --all for all repos)")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``git-discipline`` verb.

    When to use: ``rv git-discipline`` to install and manage git hooks that
    enforce healthy git habits (protect-main, leakage scan, conventional commits)
    without requiring a named identity system.
    Anti-patterns addressed: committed-to-main · never made a worktree ·
    hand-merged red CI.
    """
    desc = (
        "Identity-free git-discipline layer: protect-main (branch/path-keyed), "
        "profile-aware leakage scan, conventional-commit format enforcement.\n"
        "Hooks are installed per-repo via core.hooksPath; worktrees inherit.\n"
        "Anti-patterns caught: committed-to-main / never-made-a-worktree / "
        "hand-merged-red-CI."
    )
    if parent is not None:
        p = parent.add_parser(
            "git-discipline",
            help="Install and manage identity-free git-discipline hooks.",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv git-discipline", description=desc)

    sub = p.add_subparsers(dest="gd_cmd", required=True)

    # check --staged [--repo <path>]
    check_p = sub.add_parser(
        "check",
        help="Run all pre-commit checks (called by the hook shim).",
    )
    check_p.add_argument(
        "--staged", action="store_true", default=True,
        help="Scan only staged files (default; for pre-commit hook).",
    )
    check_p.add_argument(
        "--repo", default=None,
        help="Target repo path (default: cwd resolved via config).",
    )

    # commit-msg <file>
    cmsg_p = sub.add_parser(
        "commit-msg",
        help="Check the commit message file for conventional-commit format.",
    )
    cmsg_p.add_argument("msg_file", help="Path to the commit message file (COMMIT_EDITMSG).")

    # install [--project <slug> | --all]
    inst_p = sub.add_parser(
        "install",
        help="Install hooks per repo (sets core.hooksPath → .githooks/).",
    )
    inst_grp = inst_p.add_mutually_exclusive_group()
    inst_grp.add_argument("--project", default=None, metavar="SLUG",
                          help="Install in the named project repo only.")
    inst_grp.add_argument("--all", action="store_true", dest="all_repos",
                          help="Install in the framework repo + all registered project repos.")

    # uninstall [--project <slug> | --all]
    un_p = sub.add_parser(
        "uninstall",
        help="Remove hooks per repo (unsets core.hooksPath).",
    )
    un_grp = un_p.add_mutually_exclusive_group()
    un_grp.add_argument("--project", default=None, metavar="SLUG",
                        help="Uninstall from the named project repo only.")
    un_grp.add_argument("--all", action="store_true", dest="all_repos",
                        help="Uninstall from all repos.")

    # status [--project <slug> | --all]
    stat_p = sub.add_parser(
        "status",
        help="Report install status per repo.",
    )
    stat_grp = stat_p.add_mutually_exclusive_group()
    stat_grp.add_argument("--project", default=None, metavar="SLUG",
                          help="Show status for the named project repo.")
    stat_grp.add_argument("--all", action="store_true", dest="all_repos",
                          help="Show status for all repos.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch git-discipline subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv git-discipline: config error: {e}", file=sys.stderr)
        return 1

    cmd = args.gd_cmd

    if cmd == "check":
        # Resolve the repo path
        repo_path = Path(args.repo).resolve() if getattr(args, "repo", None) else Path.cwd()
        return cmd_check(cfg, repo_path, staged=getattr(args, "staged", True))

    elif cmd == "commit-msg":
        try:
            msg_path = Path(args.msg_file)
            subject = msg_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"rv git-discipline commit-msg: cannot read {args.msg_file}: {e}",
                  file=sys.stderr)
            return 1
        err = check_commit_msg(subject)
        if err:
            print(err, file=sys.stderr)
            return 1
        print("rv git-discipline commit-msg: OK")
        return 0

    elif cmd == "install":
        return cmd_install(
            cfg,
            project=getattr(args, "project", None),
            all_repos=getattr(args, "all_repos", False),
        )

    elif cmd == "uninstall":
        return cmd_uninstall(
            cfg,
            project=getattr(args, "project", None),
            all_repos=getattr(args, "all_repos", False),
        )

    elif cmd == "status":
        return cmd_status(
            cfg,
            project=getattr(args, "project", None),
            all_repos=getattr(args, "all_repos", False),
        )

    else:
        print(f"rv git-discipline: unknown subcommand {cmd!r}", file=sys.stderr)
        return 1
