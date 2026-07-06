"""test_sshconfig.py — read-only ~/.ssh/config alias detector (compute wizard host step).

The wizard's host step auto-detects ssh aliases so the adopter picks from a menu
instead of typing a literal host. The detector is STRICTLY read-only: it never
writes to (or even opens for writing) anything under the ssh-config directory.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Basic Host-stanza parsing
# ---------------------------------------------------------------------------

def test_single_host_alias(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    cfg = tmp_path / "config"
    cfg.write_text(
        "Host sc\n"
        "    HostName login.cluster.edu\n"
        "    User alice\n",
        encoding="utf-8",
    )
    aliases = detect_ssh_aliases(cfg, follow_includes=False)
    assert len(aliases) == 1
    a = aliases[0]
    assert a.alias == "sc"
    assert a.hostname == "login.cluster.edu"
    assert a.user == "alice"
    assert Path(a.source_file) == cfg


def test_multiple_patterns_on_one_host_line(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    cfg = tmp_path / "config"
    cfg.write_text(
        "Host web1 web2 db\n"
        "    HostName shared.example.com\n"
        "    User bob\n",
        encoding="utf-8",
    )
    aliases = detect_ssh_aliases(cfg, follow_includes=False)
    names = {a.alias for a in aliases}
    assert names == {"web1", "web2", "db"}
    # HostName/User apply to every pattern in the block.
    for a in aliases:
        assert a.hostname == "shared.example.com"
        assert a.user == "bob"


def test_wildcard_and_negation_patterns_excluded(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    cfg = tmp_path / "config"
    cfg.write_text(
        "Host *\n"
        "    ForwardAgent yes\n"
        "Host prod-* !prod-bad literal\n"
        "    HostName p.example.com\n"
        "Host db?\n"
        "    HostName d.example.com\n",
        encoding="utf-8",
    )
    aliases = detect_ssh_aliases(cfg, follow_includes=False)
    names = {a.alias for a in aliases}
    # Only the concrete, non-wildcard, non-negation pattern survives.
    assert names == {"literal"}


def test_case_insensitive_keywords(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    cfg = tmp_path / "config"
    cfg.write_text(
        "hOsT sc\n"
        "    hostname LOGIN.edu\n"
        "    USER carol\n",
        encoding="utf-8",
    )
    aliases = detect_ssh_aliases(cfg, follow_includes=False)
    assert len(aliases) == 1
    assert aliases[0].alias == "sc"
    assert aliases[0].hostname == "LOGIN.edu"
    assert aliases[0].user == "carol"


def test_equals_syntax(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    cfg = tmp_path / "config"
    cfg.write_text(
        "Host=sc\n"
        "    HostName=login.edu\n",
        encoding="utf-8",
    )
    aliases = detect_ssh_aliases(cfg, follow_includes=False)
    assert len(aliases) == 1
    assert aliases[0].alias == "sc"
    assert aliases[0].hostname == "login.edu"


def test_comments_and_blank_lines_ignored(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    cfg = tmp_path / "config"
    cfg.write_text(
        "# a comment\n"
        "\n"
        "Host sc   # trailing not a host\n"
        "    HostName login.edu\n"
        "# Host commented-out\n",
        encoding="utf-8",
    )
    aliases = detect_ssh_aliases(cfg, follow_includes=False)
    names = {a.alias for a in aliases}
    assert "commented-out" not in names
    assert "sc" in names


def test_match_block_resets_host_context(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    cfg = tmp_path / "config"
    cfg.write_text(
        "Host sc\n"
        "    HostName login.edu\n"
        "Match host somehost\n"
        "    User override\n",
        encoding="utf-8",
    )
    aliases = detect_ssh_aliases(cfg, follow_includes=False)
    # The Match block must not attach `User override` to sc.
    sc = [a for a in aliases if a.alias == "sc"]
    assert len(sc) == 1
    assert sc[0].user is None


# ---------------------------------------------------------------------------
# Include following
# ---------------------------------------------------------------------------

def test_include_followed_relative_to_ssh_dir(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    ssh_dir = tmp_path / ".ssh"
    (ssh_dir / "conf.d").mkdir(parents=True)
    (ssh_dir / "conf.d" / "extra").write_text(
        "Host included-host\n    HostName inc.edu\n", encoding="utf-8"
    )
    cfg = ssh_dir / "config"
    cfg.write_text("Include conf.d/extra\nHost main\n    HostName m.edu\n", encoding="utf-8")

    aliases = detect_ssh_aliases(cfg, follow_includes=True)
    names = {a.alias for a in aliases}
    assert "included-host" in names
    assert "main" in names


def test_include_glob_expansion(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    ssh_dir = tmp_path / ".ssh"
    (ssh_dir / "conf.d").mkdir(parents=True)
    (ssh_dir / "conf.d" / "a.conf").write_text("Host aa\n", encoding="utf-8")
    (ssh_dir / "conf.d" / "b.conf").write_text("Host bb\n", encoding="utf-8")
    cfg = ssh_dir / "config"
    cfg.write_text("Include conf.d/*.conf\n", encoding="utf-8")

    aliases = detect_ssh_aliases(cfg, follow_includes=True)
    names = {a.alias for a in aliases}
    assert {"aa", "bb"} <= names


def test_include_not_followed_when_disabled(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    ssh_dir = tmp_path / ".ssh"
    (ssh_dir / "conf.d").mkdir(parents=True)
    (ssh_dir / "conf.d" / "extra").write_text("Host included-host\n", encoding="utf-8")
    cfg = ssh_dir / "config"
    cfg.write_text("Include conf.d/extra\nHost main\n", encoding="utf-8")

    aliases = detect_ssh_aliases(cfg, follow_includes=False)
    names = {a.alias for a in aliases}
    assert "included-host" not in names
    assert "main" in names


def test_include_cycle_guarded(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir(parents=True)
    a = ssh_dir / "config"
    b = ssh_dir / "b"
    a.write_text("Host aa\nInclude b\n", encoding="utf-8")
    b.write_text("Host bb\nInclude config\n", encoding="utf-8")  # cycle back

    # Must terminate (cycle guard), not hang or RecursionError.
    aliases = detect_ssh_aliases(a, follow_includes=True)
    names = {al.alias for al in aliases}
    assert {"aa", "bb"} <= names


def test_unreadable_include_surfaced_in_skipped(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir(parents=True)
    cfg = ssh_dir / "config"
    cfg.write_text("Include does-not-exist-*.conf\nInclude missing-literal\nHost main\n", encoding="utf-8")

    skipped: list[str] = []
    aliases = detect_ssh_aliases(cfg, follow_includes=True, skipped_out=skipped)
    names = {a.alias for a in aliases}
    assert "main" in names
    # The missing literal include is surfaced (never crashes).
    assert any("missing-literal" in s for s in skipped)


# ---------------------------------------------------------------------------
# Robustness — never raise
# ---------------------------------------------------------------------------

def test_missing_config_returns_empty(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    aliases = detect_ssh_aliases(tmp_path / "nope", follow_includes=True)
    assert aliases == []


def test_empty_config_returns_empty(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    cfg = tmp_path / "config"
    cfg.write_text("", encoding="utf-8")
    assert detect_ssh_aliases(cfg) == []


def test_malformed_config_no_crash(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    cfg = tmp_path / "config"
    cfg.write_bytes(b"\xff\xfe garbage \x00 Host x\n not valid ==== \n")
    # Must not raise.
    result = detect_ssh_aliases(cfg)
    assert isinstance(result, list)


def test_default_config_path_is_user_ssh_config(tmp_path, monkeypatch):
    """With no config_path, it reads ~/.ssh/config — and never raises if absent."""
    from research_vault.sshconfig import detect_ssh_aliases

    monkeypatch.setenv("HOME", str(tmp_path))
    # No ~/.ssh/config present → empty, no crash.
    assert detect_ssh_aliases() == []


# ---------------------------------------------------------------------------
# SAFETY: strictly read-only (safety assert #1, #3)
# ---------------------------------------------------------------------------

def test_config_never_written_bytes_and_mtime_stable(tmp_path):
    from research_vault.sshconfig import detect_ssh_aliases

    ssh_dir = tmp_path / ".ssh"
    (ssh_dir / "conf.d").mkdir(parents=True)
    (ssh_dir / "conf.d" / "x.conf").write_text("Host xx\n    HostName x.edu\n", encoding="utf-8")
    cfg = ssh_dir / "config"
    cfg.write_text("Include conf.d/*.conf\nHost sc\n    HostName s.edu\n", encoding="utf-8")

    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in ssh_dir.rglob("*") if p.is_file()}
    detect_ssh_aliases(cfg, follow_includes=True)
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in ssh_dir.rglob("*") if p.is_file()}
    assert before == after, "ssh-config dir must be byte- and mtime-identical after a scan"


def test_no_write_open_targets_ssh_dir(tmp_path, monkeypatch):
    """Spy on builtins.open — no write-mode open may target anything under the ssh dir."""
    import builtins
    from research_vault import sshconfig

    ssh_dir = tmp_path / ".ssh"
    (ssh_dir / "conf.d").mkdir(parents=True)
    (ssh_dir / "conf.d" / "x.conf").write_text("Host xx\n", encoding="utf-8")
    cfg = ssh_dir / "config"
    cfg.write_text("Include conf.d/*.conf\nHost sc\n", encoding="utf-8")

    real_open = builtins.open
    ssh_dir_resolved = ssh_dir.resolve()

    def spy_open(file, mode="r", *args, **kwargs):
        target = Path(str(file)).resolve()
        is_under = ssh_dir_resolved == target or ssh_dir_resolved in target.parents
        if is_under and any(m in mode for m in ("w", "a", "x", "+")):
            raise AssertionError(f"write-mode open on ssh dir: {file!r} mode={mode!r}")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    # Any Path.write_* would also route through here in CPython? No — use read only.
    sshconfig.detect_ssh_aliases(cfg, follow_includes=True)
