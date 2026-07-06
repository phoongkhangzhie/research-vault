"""update.py — `rv update` — refresh an existing vault's framework files.

The gap this closes: ``pip install --upgrade research-vault`` updates the PACKAGE,
but a vault's already-scaffolded framework files (CLAUDE.md, doctrine/, the crew
hats) stay frozen at the version they were scaffolded with.  ``rv update``
propagates the upgraded package INTO the vault, preserving user content.

The crux: crew hats are DERIVED, not files.  ``.claude/agents/<role>.md`` is
composed by ``build_agents._compose_hat()`` from ``doctrine/`` + build_agents
constants.  So ``rv update`` never diffs/copies a hat — it refreshes ``doctrine/``
+ the statics, then re-runs ``build-agents --target claude-code`` to RECOMPOSE
them.

The file partition (3 buckets + derived):
  USER-OWNED (never touched):
    notes/, projects/ source dirs, control/, state/, tasks/,
    research_vault.toml (except the [meta] stamp), DEVLOG.md, architecture.md
  FRAMEWORK STATICS (overwrite-with-backup, hash-based user-modified policy):
    doctrine/**, CLAUDE.md, QUICKSTART.md
  APPEND-MERGE:
    .gitignore (add missing framework patterns; never remove a user line)
  DERIVED (regenerate; back up + point at doctrine if drifted):
    .claude/agents/<role>.md crew hats

User-modified policy (hash-based, per framework static):
  pristine   (hash(current) == manifest)          → overwrite silently
  modified   (hash(current) != manifest and != new) → back up to <path>.rv-bak,
                                                       overwrite, surface LOUDLY
                                                       (or keep with --skip-modified)

Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import shutil
import subprocess
import sys
from pathlib import Path

from . import scaffold

# Plan action tags.
NEW = "NEW"
UNCHANGED = "unchanged"
CHANGED = "CHANGED"
USER_MODIFIED = "USER-MODIFIED"


# ---------------------------------------------------------------------------
# Plan computation (pure — writes nothing; the SAME code path dry-run + real run)
# ---------------------------------------------------------------------------

class FileAction:
    """One planned action on a framework-managed file."""

    __slots__ = ("relpath", "action", "kind", "new_hash", "cur_hash")

    def __init__(self, relpath: str, action: str, kind: str,
                 new_hash: str | None = None, cur_hash: str | None = None):
        self.relpath = relpath
        self.action = action        # NEW | UNCHANGED | CHANGED | USER_MODIFIED
        self.kind = kind            # "static" | "hat" | "gitignore"
        self.new_hash = new_hash    # as-shipped/as-composed hash (for the manifest)
        self.cur_hash = cur_hash

    def __repr__(self) -> str:  # pragma: no cover
        return f"FileAction({self.relpath!r}, {self.action}, {self.kind})"


def _classify(relpath: str, kind: str, new_hash: str, dst: Path,
              manifest_hashes: dict[str, str]) -> FileAction:
    """Classify one file into NEW / UNCHANGED / CHANGED / USER_MODIFIED."""
    if not dst.exists():
        return FileAction(relpath, NEW, kind, new_hash=new_hash)
    cur_hash = scaffold.hash_path(dst)
    if cur_hash == new_hash:
        return FileAction(relpath, UNCHANGED, kind, new_hash=new_hash, cur_hash=cur_hash)
    manifest_hash = manifest_hashes.get(relpath)
    if manifest_hash is not None and cur_hash == manifest_hash:
        # Pristine at the last shipped version → safe to overwrite silently.
        return FileAction(relpath, CHANGED, kind, new_hash=new_hash, cur_hash=cur_hash)
    # Differs from BOTH the new shipped bytes and the last-recorded hash → the
    # user edited it (or it predates the manifest) → conservative: backup.
    return FileAction(relpath, USER_MODIFIED, kind, new_hash=new_hash, cur_hash=cur_hash)


def compute_plan(target: Path, manifest_hashes: dict[str, str]) -> list[FileAction]:
    """Compute the full framework-refresh plan without writing anything.

    Covers framework statics (CLAUDE.md, QUICKSTART.md, doctrine/**), the derived
    crew hats (composed from the NEW package doctrine so the prediction is exact),
    and the .gitignore append-merge.
    """
    import importlib.resources

    plan: list[FileAction] = []

    # 1. Framework statics.
    for vault_rel, content in scaffold.iter_managed_statics():
        new_hash = scaffold._hash_bytes(content)
        plan.append(_classify(vault_rel, "static", new_hash, target / vault_rel, manifest_hashes))

    # 2. Derived hats — compose from the NEW (package) doctrine to predict exactly.
    from .build_agents import _VAULT_ROLES, compose_cc_file
    with importlib.resources.as_file(scaffold.pkg_data() / "doctrine") as pkg_doctrine:
        pkg_doctrine = Path(pkg_doctrine)
        for role in _VAULT_ROLES:
            relpath, contents = compose_cc_file(role, pkg_doctrine)
            new_hash = scaffold._hash_bytes(contents.encode("utf-8"))
            plan.append(_classify(relpath, "hat", new_hash, target / relpath, manifest_hashes))

    # 3. .gitignore append-merge.
    gi = target / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.is_file() else ""
    merged, added = scaffold.append_merge_gitignore(existing)
    if not gi.is_file():
        plan.append(FileAction(".gitignore", NEW, "gitignore"))
    elif added:
        plan.append(FileAction(".gitignore", CHANGED, "gitignore"))
    else:
        plan.append(FileAction(".gitignore", UNCHANGED, "gitignore"))

    return plan


def _plan_is_noop(plan: list[FileAction]) -> bool:
    """True when every planned action is UNCHANGED (nothing to write)."""
    return all(fa.action == UNCHANGED for fa in plan)


# ---------------------------------------------------------------------------
# Rendering the plan
# ---------------------------------------------------------------------------

def _render_plan(plan: list[FileAction], *, skip_modified: bool) -> list[str]:
    """Human-readable per-file plan lines (for --dry-run / --check)."""
    lines: list[str] = []
    for fa in plan:
        if fa.action == UNCHANGED:
            continue
        if fa.action == USER_MODIFIED and fa.kind == "static":
            if skip_modified:
                lines.append(f"  USER-MODIFIED (kept, --skip-modified): {fa.relpath}")
            else:
                lines.append(
                    f"  USER-MODIFIED → backup {fa.relpath} to "
                    f"{fa.relpath}{scaffold.BACKUP_SUFFIX} + install new version"
                )
        elif fa.action == USER_MODIFIED and fa.kind == "hat":
            lines.append(
                f"  DRIFTED (derived) → backup {fa.relpath} to "
                f"{fa.relpath}{scaffold.BACKUP_SUFFIX} + recompose from doctrine"
            )
        elif fa.action == NEW:
            lines.append(f"  NEW: {fa.relpath}")
        elif fa.action == CHANGED:
            verb = "recompose" if fa.kind == "hat" else "update"
            lines.append(f"  CHANGED ({verb}): {fa.relpath}")
    if not lines:
        lines.append("  (all framework files already up to date)")
    return lines


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git_porcelain(target: Path) -> tuple[bool, str]:
    """Return (is_git_repo, porcelain_status). is_git_repo=False if not a repo."""
    if shutil.which("git") is None:
        return False, ""
    r = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return False, ""
    s = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    return True, s.stdout


def _git_commit(target: Path, message: str) -> bool:
    """Stage all + commit with a local identity. Returns True on success."""
    r = subprocess.run(["git", "-C", str(target), "add", "-A"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"rv update: WARNING — git add failed ({r.stderr.strip()}).", file=sys.stderr)
        return False
    r = subprocess.run(
        [
            "git", "-C", str(target),
            "-c", "user.name=rv update",
            "-c", "user.email=rv-update@example.invalid",
            "-c", "commit.gpgsign=false",
            "commit", "-m", message,
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"rv update: WARNING — git commit failed ({r.stderr.strip()}).", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def run_update(
    target: Path,
    *,
    check: bool = False,
    dry_run: bool = False,
    no_commit: bool = False,
    skip_modified: bool = False,
    force: bool = False,
) -> int:
    """Refresh the framework-managed files in the vault at ``target``.

    ``check`` / ``dry_run`` both write nothing and print the per-file plan.
    Returns 0 on success (including a clean no-op), 1 on error/abort.
    """
    target = Path(target).expanduser().resolve()
    config_path = target / "research_vault.toml"
    if not config_path.is_file():
        print(
            f"rv update: no research_vault.toml at {target}. "
            "Run this inside a vault (or `rv init` to create one).",
            file=sys.stderr,
        )
        return 1

    plan_only = check or dry_run

    # ── Versions ─────────────────────────────────────────────────────────────
    toml_text = config_path.read_text(encoding="utf-8")
    meta = scaffold.read_meta(toml_text)
    manifest = scaffold.read_manifest(target)
    vault_version = meta.get("framework_version") or manifest.get("framework_version") or "0.0.0"
    pkg_version = scaffold.package_version()
    version_changed = scaffold.version_tuple(vault_version) != scaffold.version_tuple(pkg_version)

    print(f"rv update: vault framework v{vault_version} · installed package v{pkg_version}")

    # ── Compute the plan (same code path for dry-run + real run) ─────────────
    manifest_hashes: dict[str, str] = dict(manifest.get("managed", {}))
    plan = compute_plan(target, manifest_hashes)
    noop = _plan_is_noop(plan) and not version_changed

    # ── No-op short-circuit (before the dirty guard: nothing to write, so a
    #    dirty tree is irrelevant when the vault is already current) ──────────
    if noop:
        print("rv update: already up to date — nothing to change.")
        return 0

    # ── Dirty-tree guard (real run only — dry-run writes nothing) ────────────
    if not plan_only:
        is_repo, porcelain = _git_porcelain(target)
        if not is_repo:
            if not force:
                print(
                    "rv update: this vault is not a git repository — the update diff "
                    "cannot be isolated. Re-run with --force to update anyway (no commit "
                    "will be made).",
                    file=sys.stderr,
                )
                return 1
            print(
                "rv update: WARNING — not a git repository; proceeding under --force "
                "(no commit will be made).",
                file=sys.stderr,
            )
        elif porcelain.strip() and not force:
            print(
                "rv update: the working tree has uncommitted changes. Commit or stash "
                "them first so the update diff is clean (or re-run with --force).",
                file=sys.stderr,
            )
            return 1

    # ── Dry-run / check: print the plan, write nothing ───────────────────────
    if plan_only:
        print(f"rv update: plan ({'--check' if check else '--dry-run'} — writing nothing):")
        for line in _render_plan(plan, skip_modified=skip_modified):
            print(line)
        if version_changed:
            print(f"  META: framework_version {vault_version} → {pkg_version}")
        print(f"  META: rewrite .rv-manifest.json + [meta] in research_vault.toml")
        return 0

    # ── Apply ────────────────────────────────────────────────────────────────
    new_manifest_hashes: dict[str, str] = dict(manifest_hashes)
    changed_paths: list[str] = []
    backed_up: list[str] = []

    # Belt-and-suspenders: verify the write-set never overlaps USER_OWNED names.
    # A future static whose filename collides with a user-owned name must be
    # caught here — not silently overwrite user content (charter §2 + §5).
    _write_set_tops = {
        fa.relpath.split("/")[0]
        for fa in plan
        if fa.kind in ("static", "hat") and fa.action != UNCHANGED
    }
    _collision = _write_set_tops & scaffold.USER_OWNED_NEVER_TOUCH
    if _collision:
        print(
            f"rv update: INTERNAL ERROR — the planned write-set intersects "
            f"USER_OWNED_NEVER_TOUCH: {sorted(_collision)}. Aborting to protect "
            "user content. Report this as a bug.",
            file=sys.stderr,
        )
        return 1

    # 1. Framework statics: enumerate the shipped bytes once, apply per plan.
    static_actions = {fa.relpath: fa for fa in plan if fa.kind == "static"}
    for vault_rel, content in scaffold.iter_managed_statics():
        fa = static_actions[vault_rel]
        dst = target / vault_rel
        if fa.action == UNCHANGED:
            continue
        if fa.action == USER_MODIFIED and skip_modified:
            print(f"  KEEP (user-modified, --skip-modified): {vault_rel} — left untouched")
            # Do NOT advance the manifest hash — keep it flagged as user-modified.
            continue
        if fa.action == USER_MODIFIED:
            bak = dst.with_name(dst.name + scaffold.BACKUP_SUFFIX)
            shutil.copy2(dst, bak)
            backed_up.append(vault_rel)
            print(
                f"  USER-MODIFIED: {vault_rel} was locally modified — saved to "
                f"{vault_rel}{scaffold.BACKUP_SUFFIX}; new framework version installed."
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(content)
        new_manifest_hashes[vault_rel] = fa.new_hash  # type: ignore[assignment]
        changed_paths.append(vault_rel)
        if fa.action in (NEW, CHANGED):
            print(f"  {fa.action}: {vault_rel}")

    # 2. Derived hats: back up any DRIFTED hat, then RECOMPOSE via build-agents.
    for fa in plan:
        if fa.kind != "hat":
            continue
        dst = target / fa.relpath
        if fa.action == USER_MODIFIED and dst.is_file():
            bak = dst.with_name(dst.name + scaffold.BACKUP_SUFFIX)
            shutil.copy2(dst, bak)
            backed_up.append(fa.relpath)
            print(
                f"  DRIFTED (derived): {fa.relpath} was hand-edited — saved to "
                f"{fa.relpath}{scaffold.BACKUP_SUFFIX}; recomposing from doctrine "
                "(edit doctrine/, not the hat)."
            )

    _recompose_hats(config_path, target)
    # Record the freshly-composed hat hashes from disk (build-agents rewrote them).
    for rel, h in scaffold.hash_hats(target).items():
        new_manifest_hashes[rel] = h
    hat_changed = [fa.relpath for fa in plan if fa.kind == "hat" and fa.action != UNCHANGED]
    changed_paths.extend(hat_changed)

    # 3. .gitignore append-merge.
    gi = target / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.is_file() else ""
    merged, added = scaffold.append_merge_gitignore(existing)
    if added:
        gi.write_text(merged, encoding="utf-8")
        changed_paths.append(".gitignore")
        print(f"  .gitignore: append-merged {len(added)} framework pattern(s) (user lines preserved)")

    # 4. Version stamp: [meta] + .rv-manifest.json.
    now_iso = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    scaffolded_at = meta.get("scaffolded_at") or now_iso
    new_toml = scaffold.upsert_meta_block(
        toml_text,
        framework_version=pkg_version,
        scaffolded_at=scaffolded_at,
        updated_at=now_iso,
    )
    config_path.write_text(new_toml, encoding="utf-8")
    scaffold.write_manifest(target, framework_version=pkg_version, managed=new_manifest_hashes)
    print(f"  META: framework_version {vault_version} → {pkg_version}; manifest rewritten")

    # ── Commit ───────────────────────────────────────────────────────────────
    if no_commit:
        print(
            "rv update: --no-commit — the refreshed framework diff is left staged/unstaged. "
            "Review with `git diff`, then commit."
        )
    else:
        is_repo, _ = _git_porcelain(target)
        if is_repo:
            msg = f"rv update: framework v{vault_version} → v{pkg_version}"
            if _git_commit(target, msg):
                print(f"rv update: committed — {msg}")
        else:
            print(
                "rv update: not a git repository — framework refreshed on disk, no commit made.",
                file=sys.stderr,
            )

    if backed_up:
        print(
            f"\nrv update: {len(backed_up)} locally-modified file(s) backed up to "
            f"*{scaffold.BACKUP_SUFFIX} — review and delete the backups once you've "
            "reconciled any local edits."
        )
    print("\nrv update: done.")
    return 0


def _recompose_hats(config_path: Path, target: Path) -> None:
    """Re-run build-agents (claude-code) so the crew hats recompose from doctrine.

    Constructs Config directly from the vault's TOML (bypassing the module cache,
    same rationale as `rv init`) so build-agents writes to the right instance_root.
    """
    from .config import (
        Config, _load_toml, _expand_paths, _default_config, _merge, reset_config_cache,
    )
    from .build_agents import cmd_build as _cmd_build_agents
    try:
        _defaults = _default_config()
        _raw = _load_toml(config_path)
        _merged = _merge(_defaults, _raw)
        _instance_root = Path(_merged.get("instance_root", str(target)))
        _merged = _expand_paths(_merged, _instance_root)
        _cfg = Config(_merged, config_file=config_path)
        reset_config_cache()
        _cmd_build_agents(cfg=_cfg, target="claude-code")
    except Exception as exc:
        print(
            f"rv update: WARNING — could not recompose hats: {exc}. "
            "Run `rv build-agents --target claude-code` manually.",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# CLI verb
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``update`` verb.

    When to use: ``rv update`` after ``pip install --upgrade research-vault`` to
    propagate the upgraded framework (doctrine, CLAUDE.md, QUICKSTART.md, and the
    recomposed crew hats) into THIS vault, preserving all user content. Use
    ``rv update --dry-run`` (or ``--check``) to preview the per-file plan without
    writing anything.
    """
    desc = (
        "Refresh this vault's framework-managed files from the installed package "
        "(doctrine/, CLAUDE.md, QUICKSTART.md) and recompose the crew hats — after "
        "a `pip install --upgrade research-vault`. USER-OWNED content (notes/, "
        "projects, control/, research_vault.toml, DEVLOG.md, architecture.md) is "
        "never touched. A locally-modified framework file is backed up to "
        "<path>.rv-bak before the new version installs."
    )
    if parent is not None:
        p = parent.add_parser(
            "update",
            help="Refresh the vault's framework files from the upgraded package.",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv update", description=desc)

    p.add_argument(
        "dir", nargs="?", default=None,
        help="Vault directory to update (default: resolve from config / CWD).",
    )
    p.add_argument(
        "--check", action="store_true",
        help="Preview the per-file plan (alias of --dry-run); write nothing.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Preview the per-file plan (NEW / CHANGED / USER-MODIFIED / unchanged); write nothing.",
    )
    p.add_argument(
        "--no-commit", action="store_true",
        help="Refresh the files but leave the diff staged/unstaged (no commit).",
    )
    p.add_argument(
        "--skip-modified", action="store_true",
        help="Keep locally-modified framework statics as-is (warn) instead of backing up + overwriting.",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Proceed even if the working tree is dirty or the vault is not a git repo.",
    )
    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch: rv update."""
    if getattr(args, "dir", None):
        target = Path(args.dir)
    else:
        from .config import load_config
        try:
            cfg = load_config()
            target = cfg.instance_root
        except Exception as exc:
            print(f"rv update: config error: {exc}", file=sys.stderr)
            return 1
    return run_update(
        target,
        check=getattr(args, "check", False),
        dry_run=getattr(args, "dry_run", False),
        no_commit=getattr(args, "no_commit", False),
        skip_modified=getattr(args, "skip_modified", False),
        force=getattr(args, "force", False),
    )
