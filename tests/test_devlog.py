"""test_devlog.py — tests for the devlog verb (3-zone journal convention:
Now / Decisions / Log).

All hermetic (tmp_instance). No ~/vault reads or writes.
"""

import datetime
import pytest
from research_vault.config import load_config, reset_config_cache
from research_vault import devlog as devlog_mod


@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


@pytest.fixture
def cs_project_cfg(tmp_instance):
    """Register a CS-project-convention project (source_dir = <repo>/notes)
    alongside the default demo-research/demo-litreview registry, then
    reload config. DEVLOG.md for this project must resolve/read at the
    repo root (source_dir.parent), not under source_dir itself."""
    from research_vault.project import cmd_add

    repo = tmp_instance / "repos" / "cs-devlog-demo"
    notes = repo / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    cmd_add(
        name="cs-devlog-demo", code="cdd", source_dir=str(notes), roster=[],
        config_path=tmp_instance / "research_vault.toml",
    )
    reset_config_cache()
    return load_config(reload=True), repo


# ---------------------------------------------------------------------------
# init — 3-zone seed
# ---------------------------------------------------------------------------

def test_init_creates_devlog_with_3_zones(cfg):
    """cmd_init creates a DEVLOG.md seeded with the Now / Decisions / Log zones."""
    path = devlog_mod.cmd_init("demo-research", config=cfg)
    assert path.exists()
    content = path.read_text()
    assert "# DEVLOG — demo-research" in content
    assert "## Now" in content
    assert "## Decisions" in content
    assert "## Log" in content
    today = datetime.date.today().isoformat()
    assert f"### {today}" in content
    assert "#### Done" in content


def test_init_with_note(cfg):
    """cmd_init embeds the note in the header."""
    path = devlog_mod.cmd_init("demo-research", "Bootstrap entry.", config=cfg)
    assert "Bootstrap entry." in path.read_text()


def test_init_raises_if_exists(cfg):
    """cmd_init raises FileExistsError on second call without --overwrite."""
    devlog_mod.cmd_init("demo-research", config=cfg)
    with pytest.raises(FileExistsError, match="already exists"):
        devlog_mod.cmd_init("demo-research", config=cfg)


def test_init_overwrite(cfg):
    """cmd_init with overwrite=True replaces the existing DEVLOG."""
    devlog_mod.cmd_init("demo-research", "first", config=cfg)
    path = devlog_mod.cmd_init("demo-research", "second", config=cfg, overwrite=True)
    assert "second" in path.read_text()


# ---------------------------------------------------------------------------
# check — MISSING hard-blocks, structure lints hard-block (FAIL), staleness WARNs
# ---------------------------------------------------------------------------

def test_check_ok_fresh_devlog(cfg):
    """cmd_check returns OK for a freshly created DEVLOG."""
    devlog_mod.cmd_init("demo-research", config=cfg)
    result = devlog_mod.cmd_check("demo-research", config=cfg)
    assert result["status"] == "OK"
    assert result["errors"] == []


def test_check_missing_devlog(cfg):
    """cmd_check returns MISSING when no DEVLOG.md exists — the only status
    that always hard-blocks alongside FAIL."""
    result = devlog_mod.cmd_check("demo-litreview", config=cfg)
    assert result["status"] == "MISSING"


def test_check_stale_devlog_is_warn_not_fail(cfg):
    """A stale latest Log entry is a WARN (non-blocking), never FAIL."""
    path = devlog_mod.cmd_init("demo-research", config=cfg)
    old_date = (datetime.date.today() - datetime.timedelta(days=20)).isoformat()
    content = path.read_text()
    today = datetime.date.today().isoformat()
    content = content.replace(f"### {today}", f"### {old_date}")
    path.write_text(content)

    result = devlog_mod.cmd_check("demo-research", config=cfg)
    assert result["status"] == "WARN", result
    assert result["errors"] == []
    assert any("20 days" in w for w in result["warnings"])


def test_check_fails_missing_now_zone(cfg):
    """Lint (a): a devlog with no '## Now' zone hard-fails."""
    path = devlog_mod.cmd_init("demo-research", config=cfg)
    content = path.read_text().replace("## Now", "## Was")
    path.write_text(content)

    result = devlog_mod.cmd_check("demo-research", config=cfg)
    assert result["status"] == "FAIL"
    assert any("Now" in e for e in result["errors"])


def test_check_fails_dangling_superseded_by(cfg):
    """Lint (b): a 'superseded-by D-NNN' that doesn't resolve to a real
    decision id hard-fails."""
    path = devlog_mod.cmd_init("demo-research", config=cfg, overwrite=True)
    devlog_mod.cmd_append(
        "demo-research", "Decisions", "Chose approach A.", config=cfg,
    )
    content = path.read_text()
    # D-001 exists; flip its status to point at a non-existent D-999.
    content = content.replace("· in-force", "· superseded-by D-999", 1)
    path.write_text(content)

    result = devlog_mod.cmd_check("demo-research", config=cfg)
    assert result["status"] == "FAIL"
    assert any("D-999" in e for e in result["errors"])


def test_check_fails_empty_rejected_field(cfg):
    """Lint (c): a Decisions record with an empty 'Rejected' field hard-fails."""
    path = devlog_mod.cmd_init("demo-research", config=cfg, overwrite=True)
    content = path.read_text()
    content = content.replace(
        "_(no decisions recorded yet)_\n",
        "### D-001 · " + datetime.date.today().isoformat() + " · in-force\n\n"
        "**Context:** _(n/a)_\n"
        "**Decision:** did the thing\n"
        "**Rejected:** \n"
        "**Consequences:** _(n/a)_\n"
        "**Touches:** _(none)_\n\n",
    )
    path.write_text(content)

    result = devlog_mod.cmd_check("demo-research", config=cfg)
    assert result["status"] == "FAIL"
    assert any("Rejected" in e for e in result["errors"])


def test_check_passes_decision_with_honest_rejected(cfg):
    """A decision appended via cmd_append gets a non-empty (honest) Rejected
    default — the append path must never itself trip lint (c)."""
    devlog_mod.cmd_init("demo-research", config=cfg, overwrite=True)
    devlog_mod.cmd_append(
        "demo-research", "Decisions", "Chose approach A.", config=cfg,
    )
    result = devlog_mod.cmd_check("demo-research", config=cfg)
    assert result["status"] == "OK", result


# ---------------------------------------------------------------------------
# append — Done (Log), Decisions (ADR-lite ledger), Now (mutable overwrite)
# ---------------------------------------------------------------------------

def test_append_done_adds_bullet_to_log(cfg):
    """cmd_append('Done', ...) adds a bullet to today's Log entry."""
    devlog_mod.cmd_init("demo-research", config=cfg)
    path = devlog_mod.cmd_append(
        "demo-research", "Done", "Scaffolded SR-1.", config=cfg
    )
    content = path.read_text()
    assert "Scaffolded SR-1." in content


def test_append_creates_devlog_if_missing(cfg):
    """cmd_append creates the DEVLOG if it doesn't exist yet."""
    path = devlog_mod.cmd_append(
        "demo-litreview", "Decisions", "Chose OKF format.", config=cfg
    )
    assert path.exists()
    assert "Chose OKF format." in path.read_text()


def test_append_decisions_auto_assigns_d_nnn(cfg):
    """Each 'Decisions' append auto-assigns the next D-NNN, newest on top."""
    devlog_mod.cmd_init("demo-research", config=cfg, overwrite=True)
    path = devlog_mod.cmd_append("demo-research", "Decisions", "First call.", config=cfg)
    path = devlog_mod.cmd_append("demo-research", "Decisions", "Second call.", config=cfg)
    content = path.read_text()
    assert "### D-001" in content
    assert "### D-002" in content
    # newest-on-top: D-002 appears before D-001
    assert content.index("D-002") < content.index("D-001")


def test_append_decisions_touches_stamps_cross_link(cfg):
    """--touches stamps the OKF cross-link into the Decisions record."""
    devlog_mod.cmd_init("demo-research", config=cfg, overwrite=True)
    path = devlog_mod.cmd_append(
        "demo-research", "Decisions", "Chose approach A.", config=cfg,
        touches="[Approach A](/methodology/approach-a.md)",
    )
    content = path.read_text()
    assert "**Touches:** [Approach A](/methodology/approach-a.md)" in content


def test_append_done_touches_stamps_cross_link(cfg):
    """--touches stamps the OKF cross-link into a Done bullet."""
    devlog_mod.cmd_init("demo-research", config=cfg, overwrite=True)
    path = devlog_mod.cmd_append(
        "demo-research", "Done", "Wrote the finding up.", config=cfg,
        touches="[Finding](/findings/f1.md)",
    )
    content = path.read_text()
    assert "touches: [Finding](/findings/f1.md)" in content


def test_append_now_overwrites_not_accumulates(cfg):
    """cmd_append('Now', ...) REPLACES the Now zone body, it does not append."""
    devlog_mod.cmd_init("demo-research", config=cfg, overwrite=True)
    devlog_mod.cmd_append("demo-research", "Now", "State: first pass.", config=cfg)
    path = devlog_mod.cmd_append("demo-research", "Now", "State: second pass.", config=cfg)
    content = path.read_text()
    assert "State: second pass." in content
    assert "State: first pass." not in content


# ---------------------------------------------------------------------------
# view / index / search
# ---------------------------------------------------------------------------

def test_view_returns_top_lines(cfg):
    """cmd_view returns the first N lines of the DEVLOG."""
    devlog_mod.cmd_init("demo-research", config=cfg)
    content = devlog_mod.cmd_view("demo-research", config=cfg, lines=5)
    lines = content.splitlines()
    assert len(lines) <= 5
    assert "DEVLOG" in content


def test_view_missing_raises(cfg):
    """cmd_view raises FileNotFoundError when DEVLOG doesn't exist."""
    with pytest.raises(FileNotFoundError):
        devlog_mod.cmd_view("demo-litreview", config=cfg)


def test_unknown_project_raises(cfg):
    """cmd_init raises KeyError for an unknown project."""
    with pytest.raises(KeyError, match="Unknown project"):
        devlog_mod.cmd_init("ghost-project", config=cfg)


def test_cli_devlog_init(tmp_instance, capsys):
    """rv devlog <project> init prints the created path (project-first form)."""
    from research_vault.cli import main
    result = main(["devlog", "demo-research", "init"])
    assert result == 0
    assert "Created:" in capsys.readouterr().out


def test_cli_devlog_check_ok(tmp_instance, capsys):
    """rv devlog <project> check exits 0 for a fresh DEVLOG (project-first form)."""
    from research_vault.cli import main
    main(["devlog", "demo-research", "init"])
    result = main(["devlog", "demo-research", "check"])
    assert result == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_cli_devlog_check_fail_exits_nonzero(tmp_instance, capsys):
    """rv devlog check exits 1 when a structure lint fails."""
    from research_vault.cli import main
    from research_vault.config import load_config as _lc

    main(["devlog", "demo-research", "init"])
    cfg = _lc()
    path = cfg.project_devlog("demo-research")
    path.write_text(path.read_text().replace("## Now", "## Was"))

    result = main(["devlog", "demo-research", "check"])
    assert result == 1
    assert "FAIL" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# CS-project convention (source_dir = <repo>/notes) — DEVLOG.md lives at the
# repo root (source_dir.parent), same convention as pointers.md/architecture.md.
# ---------------------------------------------------------------------------

class TestDevlogCsProjectConvention:
    def test_init_writes_devlog_at_repo_root_not_under_notes(self, cs_project_cfg):
        """cmd_init must create DEVLOG.md at the repo root, not inside notes/."""
        cs_cfg, repo = cs_project_cfg
        path = devlog_mod.cmd_init("cs-devlog-demo", config=cs_cfg)
        assert path == repo / "DEVLOG.md"
        assert path.exists()
        assert "notes" not in path.relative_to(repo).parts

    def test_check_reads_devlog_placed_at_repo_root(self, cs_project_cfg):
        """A DEVLOG.md placed directly at the repo root (as the CS-project
        convention scaffolds it) must be found by cmd_check — never MISSING."""
        cs_cfg, repo = cs_project_cfg
        today = datetime.date.today().isoformat()
        (repo / "DEVLOG.md").write_text(
            f"# DEVLOG — cs-devlog-demo\n\nNewest entries on top.\n\n"
            "## Now\n\n_(state)_\n\n"
            "## Decisions\n\n_(no decisions recorded yet)_\n\n"
            f"## Log\n\n### {today}\n\n#### Done\n- did a thing\n",
            encoding="utf-8",
        )
        result = devlog_mod.cmd_check("cs-devlog-demo", config=cs_cfg)
        assert result["status"] == "OK", result
