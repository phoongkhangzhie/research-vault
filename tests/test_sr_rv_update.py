"""test_sr_rv_update.py — SR-RV-UPDATE acceptance tests.

Covers `rv update` (framework refresh for an existing vault) + the demo removal
+ the version stamp / hash-manifest + the 3-bucket file partition + git safety.

All tests are hermetic: `rv init` scaffolds a real vault (with git) in tmp_path;
the update runs against that vault. Never touches ~/vault.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from research_vault import scaffold
from research_vault.init import cmd_init_in_dir
from research_vault.update import (
    run_update,
    compute_plan,
    FileAction,
    UNCHANGED,
    CHANGED,
    NEW,
    USER_MODIFIED,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def vault(tmp_path):
    """A freshly-initialised vault (with git repo + initial commit)."""
    target = tmp_path / "myvault"
    rc = cmd_init_in_dir(str(target))
    assert rc == 0, "rv init must succeed"
    return target


def _git(target: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(target), *args], capture_output=True, text=True
    )
    return r.stdout


def _commit_count(target: Path) -> int:
    out = _git(target, "rev-list", "--count", "HEAD").strip()
    return int(out) if out else 0


def _read_manifest(target: Path) -> dict:
    return json.loads((target / scaffold.MANIFEST_NAME).read_text(encoding="utf-8"))


def _set_vault_version(target: Path, version: str) -> None:
    """Lower the recorded framework_version in BOTH the [meta] block + manifest."""
    cfg = target / "research_vault.toml"
    t = cfg.read_text(encoding="utf-8")
    t = scaffold.upsert_meta_block(
        t, framework_version=version, scaffolded_at="2020-01-01T00:00:00+00:00",
        updated_at="2020-01-01T00:00:00+00:00",
    )
    cfg.write_text(t, encoding="utf-8")
    m = _read_manifest(target)
    m["framework_version"] = version
    (target / scaffold.MANIFEST_NAME).write_text(json.dumps(m, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Fresh init → immediate update = clean no-op
# ---------------------------------------------------------------------------

def test_fresh_init_then_update_is_noop(vault, capsys):
    before = _commit_count(vault)
    rc = run_update(vault)
    assert rc == 0
    out = capsys.readouterr().out
    assert "already up to date" in out
    # No commit was made.
    assert _commit_count(vault) == before


def test_noop_leaves_every_file_unchanged(vault):
    """Snapshot every file hash, run update, confirm nothing changed."""
    def snapshot() -> dict[str, str]:
        snap = {}
        for p in vault.rglob("*"):
            if p.is_file() and ".git" not in p.parts:
                snap[str(p.relative_to(vault))] = scaffold.hash_path(p)
        return snap

    before = snapshot()
    run_update(vault)
    after = snapshot()
    assert before == after, "a no-op update must not change any file"


# ---------------------------------------------------------------------------
# 2. Stale vault → refresh doctrine + recompose hats + update CLAUDE.md
# ---------------------------------------------------------------------------

def test_stale_vault_refreshes_doctrine_silently(vault):
    """A pristine-at-old-version doctrine file is silently overwritten to new."""
    doctrine_file = vault / "doctrine" / "standards.md"
    shipped = doctrine_file.read_bytes()

    # Simulate the file as it shipped at an OLDER version: different content,
    # and the manifest records THAT old hash (so it's pristine, not user-modified).
    old_content = b"# OLD standards doctrine (v0.0.9)\n"
    doctrine_file.write_bytes(old_content)
    m = _read_manifest(vault)
    m["managed"]["doctrine/standards.md"] = scaffold._hash_bytes(old_content)
    (vault / scaffold.MANIFEST_NAME).write_text(json.dumps(m, indent=2), encoding="utf-8")
    _set_vault_version(vault, "0.0.9")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "simulate stale")

    rc = run_update(vault)
    assert rc == 0
    # Refreshed back to the shipped content — no backup (it was pristine).
    assert doctrine_file.read_bytes() == shipped
    assert not (vault / "doctrine" / "standards.md.rv-bak").exists()


def test_stale_vault_recomposes_hats(vault):
    """Deleting a hat + a stale doctrine edit → update recomposes the hats."""
    hat = vault / ".claude" / "agents" / "engineer.md"
    assert hat.is_file()
    hat.unlink()  # missing hat
    _set_vault_version(vault, "0.0.9")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "drop a hat + stale version")

    rc = run_update(vault)
    assert rc == 0
    assert hat.is_file(), "update must recompose the missing hat"
    body = hat.read_text(encoding="utf-8")
    assert body.startswith("---\n"), "recomposed hat has CC frontmatter"
    assert "engineer" in body


def test_stale_vault_updates_meta_and_manifest_version(vault):
    _set_vault_version(vault, "0.0.9")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "stale")

    run_update(vault)
    meta = scaffold.read_meta((vault / "research_vault.toml").read_text(encoding="utf-8"))
    assert meta["framework_version"] == scaffold.package_version()
    assert _read_manifest(vault)["framework_version"] == scaffold.package_version()
    # scaffolded_at preserved (was the simulated old date); updated_at bumped.
    assert meta["scaffolded_at"] == "2020-01-01T00:00:00+00:00"
    assert meta["updated_at"] != "2020-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# 3. USER-OWNED provably untouched (esp. architecture.md)
# ---------------------------------------------------------------------------

def test_user_owned_files_byte_identical_after_update(vault):
    """notes/, research_vault.toml projects, DEVLOG.md, control/, architecture.md."""
    # Plant user content.
    (vault / "notes" / "findings" / "my-finding.md").write_text("USER FINDING\n", encoding="utf-8")
    (vault / "architecture.md").write_text("MY CUSTOM ARCHITECTURE MAP\n", encoding="utf-8")
    (vault / "DEVLOG.md").write_text("MY DEVLOG\n", encoding="utf-8")
    (vault / "control" / "vault.md").write_text("MY CONTROL STATE\n", encoding="utf-8")
    # Register a real project by editing the toml (user-owned bit).
    cfg = vault / "research_vault.toml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + '\n[projects.myproj]\nsource_dir = "/tmp/myproj"\n',
        encoding="utf-8",
    )
    _set_vault_version(vault, "0.0.9")

    snapshots = {
        p: (vault / p).read_bytes()
        for p in (
            "notes/findings/my-finding.md",
            "architecture.md",
            "DEVLOG.md",
            "control/vault.md",
        )
    }
    project_line_before = "[projects.myproj]" in cfg.read_text(encoding="utf-8")

    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "plant user content")
    run_update(vault)

    for p, content in snapshots.items():
        assert (vault / p).read_bytes() == content, f"USER-OWNED file changed: {p}"
    # The user's project registration survived the [meta] rewrite.
    assert "[projects.myproj]" in cfg.read_text(encoding="utf-8")
    assert project_line_before


def test_architecture_md_never_in_managed_set(vault):
    """architecture.md must be in the USER_OWNED_NEVER_TOUCH set and never planned."""
    assert "architecture.md" in scaffold.USER_OWNED_NEVER_TOUCH
    plan = compute_plan(vault, _read_manifest(vault).get("managed", {}))
    assert all(fa.relpath != "architecture.md" for fa in plan)


# ---------------------------------------------------------------------------
# 4. User-modified doctrine → backup, not clobber, surfaced
# ---------------------------------------------------------------------------

def test_user_modified_doctrine_backed_up_and_surfaced(vault, capsys):
    doctrine_file = vault / "doctrine" / "tooling.md"
    original = doctrine_file.read_text(encoding="utf-8")
    doctrine_file.write_text(original + "\n\nMY LOCAL EDIT — keep this\n", encoding="utf-8")
    _set_vault_version(vault, "0.0.9")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "user edit + stale")

    rc = run_update(vault)
    assert rc == 0
    out = capsys.readouterr().out

    bak = vault / "doctrine" / "tooling.md.rv-bak"
    assert bak.is_file(), "user-modified file must be backed up to .rv-bak"
    assert "MY LOCAL EDIT" in bak.read_text(encoding="utf-8"), "backup keeps the user's edit"
    # New framework version installed (the edit is gone from the live file).
    assert "MY LOCAL EDIT" not in doctrine_file.read_text(encoding="utf-8")
    # Surfaced loudly.
    assert "USER-MODIFIED" in out
    assert "tooling.md" in out


def test_skip_modified_keeps_user_version(vault, capsys):
    doctrine_file = vault / "doctrine" / "tooling.md"
    doctrine_file.write_text("MY VERSION — DO NOT TOUCH\n", encoding="utf-8")
    _set_vault_version(vault, "0.0.9")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "user edit + stale")

    rc = run_update(vault, skip_modified=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert doctrine_file.read_text(encoding="utf-8") == "MY VERSION — DO NOT TOUCH\n"
    assert not (vault / "doctrine" / "tooling.md.rv-bak").exists()
    assert "skip-modified" in out.lower() or "KEEP" in out


# ---------------------------------------------------------------------------
# 5. .gitignore append-merge
# ---------------------------------------------------------------------------

def test_gitignore_append_merge_preserves_user_lines(vault):
    gi = vault / ".gitignore"
    # User-customised .gitignore missing some framework patterns.
    gi.write_text("# my ignores\nmy-secret-dir/\n*.tmp\n", encoding="utf-8")
    _set_vault_version(vault, "0.0.9")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "custom gitignore + stale")

    run_update(vault)
    text = gi.read_text(encoding="utf-8")
    # User lines preserved.
    assert "my-secret-dir/" in text
    assert "*.tmp" in text
    # Framework patterns added.
    assert "state/*" in text
    assert "control/" in text
    # No duplicates.
    non_comment = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert len(non_comment) == len(set(non_comment)), "no duplicate .gitignore patterns"


def test_gitignore_merge_is_pure_and_idempotent():
    existing = "# mine\nfoo/\n" + scaffold.FRAMEWORK_GITIGNORE  # already has all patterns
    merged, added = scaffold.append_merge_gitignore(existing)
    assert added == [], "nothing to add when all framework patterns present"
    assert merged == existing


# ---------------------------------------------------------------------------
# 6. Git safety + dry-run + idempotency
# ---------------------------------------------------------------------------

def test_dirty_tree_refused_without_force(vault, capsys):
    (vault / "notes" / "dirty.md").write_text("uncommitted\n", encoding="utf-8")
    _set_vault_version(vault, "0.0.9")  # writes to toml, but the dirty file above is the trigger
    # Do NOT commit — tree is dirty.
    rc = run_update(vault)
    assert rc == 1
    err = capsys.readouterr().err
    assert "uncommitted" in err.lower() or "dirty" in err.lower() or "stash" in err.lower()


def test_dirty_tree_allowed_with_force(vault):
    (vault / "notes" / "dirty.md").write_text("uncommitted\n", encoding="utf-8")
    _set_vault_version(vault, "0.0.9")
    rc = run_update(vault, force=True)
    assert rc == 0


def test_dry_run_writes_nothing(vault):
    def snapshot() -> dict[str, str]:
        return {
            str(p.relative_to(vault)): scaffold.hash_path(p)
            for p in vault.rglob("*")
            if p.is_file() and ".git" not in p.parts
        }

    # Make it stale + modify a doctrine file so there IS a plan.
    (vault / "doctrine" / "standards.md").write_text("changed\n", encoding="utf-8")
    _set_vault_version(vault, "0.0.9")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "stale")

    before = snapshot()
    rc = run_update(vault, dry_run=True)
    assert rc == 0
    after = snapshot()
    assert before == after, "--dry-run must write nothing"


def test_check_alias_writes_nothing(vault, capsys):
    _set_vault_version(vault, "0.0.9")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "stale")
    rc = run_update(vault, check=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "--check" in out


def test_update_is_idempotent(vault):
    _set_vault_version(vault, "0.0.9")
    (vault / "doctrine" / "standards.md").write_text("changed\n", encoding="utf-8")
    m = _read_manifest(vault)
    m["managed"]["doctrine/standards.md"] = scaffold._hash_bytes(b"changed\n")
    (vault / scaffold.MANIFEST_NAME).write_text(json.dumps(m, indent=2), encoding="utf-8")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "stale")

    rc1 = run_update(vault)
    assert rc1 == 0
    count_after_first = _commit_count(vault)

    # Second run: nothing left to do → no-op, no new commit.
    rc2 = run_update(vault)
    assert rc2 == 0
    assert _commit_count(vault) == count_after_first, "idempotent re-run makes no commit"


def test_no_commit_leaves_diff_uncommitted(vault):
    _set_vault_version(vault, "0.0.9")
    (vault / "doctrine" / "standards.md").write_text("changed\n", encoding="utf-8")
    m = _read_manifest(vault)
    m["managed"]["doctrine/standards.md"] = scaffold._hash_bytes(b"changed\n")
    (vault / scaffold.MANIFEST_NAME).write_text(json.dumps(m, indent=2), encoding="utf-8")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "stale")

    before = _commit_count(vault)
    rc = run_update(vault, no_commit=True)
    assert rc == 0
    assert _commit_count(vault) == before, "--no-commit makes no commit"
    # There is an uncommitted diff.
    assert _git(vault, "status", "--porcelain").strip() != ""


# ---------------------------------------------------------------------------
# 7. Commit message + non-vault guard
# ---------------------------------------------------------------------------

def test_update_commit_message(vault):
    _set_vault_version(vault, "0.0.9")
    (vault / "doctrine" / "standards.md").write_text("changed\n", encoding="utf-8")
    m = _read_manifest(vault)
    m["managed"]["doctrine/standards.md"] = scaffold._hash_bytes(b"changed\n")
    (vault / scaffold.MANIFEST_NAME).write_text(json.dumps(m, indent=2), encoding="utf-8")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "stale")

    run_update(vault)
    msg = _git(vault, "log", "-1", "--pretty=%s").strip()
    assert msg == f"rv update: framework v0.0.9 → v{scaffold.package_version()}"


def test_update_outside_vault_errors(tmp_path, capsys):
    rc = run_update(tmp_path / "not-a-vault")
    assert rc == 1
    err = capsys.readouterr().err
    assert "research_vault.toml" in err


# ---------------------------------------------------------------------------
# 8. Version helpers
# ---------------------------------------------------------------------------

def test_version_tuple_and_lt():
    assert scaffold.version_tuple("0.1.0") == (0, 1, 0)
    assert scaffold.version_tuple("1.2.0rc1") == (1, 2, 0)
    assert scaffold.version_lt("0.0.9", "0.1.0")
    assert not scaffold.version_lt("0.1.0", "0.1.0")
    assert not scaffold.version_lt("0.2.0", "0.1.0")


# ---------------------------------------------------------------------------
# 9. Staleness nudge in rv check (Slice 5)
# ---------------------------------------------------------------------------

def test_check_nudges_when_vault_stale(vault):
    from research_vault.check import _framework_staleness_nudge
    from research_vault.config import (
        Config, _load_toml, _expand_paths, _default_config, _merge,
    )
    _set_vault_version(vault, "0.0.9")

    cfg_path = vault / "research_vault.toml"
    merged = _merge(_default_config(), _load_toml(cfg_path))
    merged = _expand_paths(merged, vault)
    cfg = Config(merged, config_file=cfg_path)

    msg = _framework_staleness_nudge(cfg)
    assert msg, "a stale vault must produce a nudge"
    assert "rv update" in msg
    assert "0.0.9" in msg


def test_check_no_nudge_when_current(vault):
    from research_vault.check import _framework_staleness_nudge
    from research_vault.config import (
        Config, _load_toml, _expand_paths, _default_config, _merge,
    )
    cfg_path = vault / "research_vault.toml"
    merged = _merge(_default_config(), _load_toml(cfg_path))
    merged = _expand_paths(merged, vault)
    cfg = Config(merged, config_file=cfg_path)

    assert _framework_staleness_nudge(cfg) == "", "current vault must produce no nudge"


# ---------------------------------------------------------------------------
# 10. init/update parity — the anti-drift guarantee
# ---------------------------------------------------------------------------

def test_init_and_update_share_managed_enumeration(vault):
    """Every framework static init writes is in the update plan (no init-only file)."""
    manifest = _read_manifest(vault)["managed"]
    static_relpaths = {rel for rel, _ in scaffold.iter_managed_statics()}
    # Every managed static is recorded in the manifest by init.
    for rel in static_relpaths:
        assert rel in manifest, f"init did not record managed static in manifest: {rel}"
    # And every one is classified by the update plan.
    plan = compute_plan(vault, manifest)
    planned = {fa.relpath for fa in plan if fa.kind == "static"}
    assert static_relpaths == planned


# ---------------------------------------------------------------------------
# 11. USER_OWNED_NEVER_TOUCH guard is active (belt-and-suspenders)
# ---------------------------------------------------------------------------

def test_user_owned_guard_refuses_poisoned_write_set(vault, capsys):
    """A managed-set entry whose top-level name collides with USER_OWNED_NEVER_TOUCH
    must be caught by the belt-and-suspenders guard and cause run_update to abort
    with rc=1 — proving the guard is active, not decorative.

    Poison: inject a synthetic FileAction for 'architecture.md' (CHANGED static)
    into the plan returned by compute_plan.  Without the guard this would proceed
    to write; with the guard it must REFUSE.

    The non-vacuousness proof:
      - 'architecture.md' IS in USER_OWNED_NEVER_TOUCH (so the guard can fire).
      - We inject it into the plan as a CHANGED static (so the guard sees it).
      - run_update returns 1 and prints the INTERNAL ERROR message.
    """
    # Precondition: the poison target is indeed in the set — guard can fire.
    assert "architecture.md" in scaffold.USER_OWNED_NEVER_TOUCH

    # Poison: the real plan is fine; we inject one extra CHANGED static whose
    # top-level name is "architecture.md" to simulate a future naming collision.
    poisoned_entry = FileAction("architecture.md", CHANGED, "static",
                                new_hash="sha256:deadbeef")

    _set_vault_version(vault, "0.0.9")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "stale version for guard test")

    real_compute_plan = compute_plan

    def patched_compute_plan(target, manifest_hashes):
        plan = real_compute_plan(target, manifest_hashes)
        plan.append(poisoned_entry)
        return plan

    with patch("research_vault.update.compute_plan", side_effect=patched_compute_plan):
        rc = run_update(vault, force=True)

    assert rc == 1, "guard must abort (rc=1) when write-set overlaps USER_OWNED_NEVER_TOUCH"
    err = capsys.readouterr().err
    assert "INTERNAL ERROR" in err
    assert "architecture.md" in err
