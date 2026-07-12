"""test_note.py — tests for the note verb (OKF notes).

All hermetic (tmp_instance). No ~/vault reads or writes.
"""

import pytest
from research_vault.config import load_config
from research_vault import note as note_mod


@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


def test_new_creates_note_in_correct_subdir(cfg):
    """cmd_new creates a note in the OKF type subdirectory."""
    path = note_mod.cmd_new("demo-research", "findings", "A key finding", config=cfg)
    assert path.exists()
    assert path.parent.name == "findings"
    content = path.read_text()
    assert "type: findings" in content
    assert "title: A key finding" in content


def test_new_all_types_accepted(cfg):
    """All OKF types are accepted without error."""
    for t in note_mod.OKF_TYPES:
        path = note_mod.cmd_new("demo-research", t, f"Note of type {t}", config=cfg)
        assert path.exists()
        assert path.parent.name == t


def test_new_invalid_type_raises(cfg):
    """cmd_new raises ValueError for an invalid OKF type."""
    with pytest.raises(ValueError, match="Unknown note type"):
        note_mod.cmd_new("demo-research", "invalid-type", "bad note", config=cfg)


def test_new_with_custom_id(cfg):
    """cmd_new respects the note_id override for the filename slug."""
    path = note_mod.cmd_new(
        "demo-research", "literature", "A paper",
        config=cfg, note_id="smith-2024"
    )
    assert path.stem == "smith-2024"


def test_new_with_tags(cfg):
    """cmd_new stores tags in the frontmatter."""
    path = note_mod.cmd_new(
        "demo-research", "concepts", "A concept",
        config=cfg, tags=["grounding", "fabrication"]
    )
    content = path.read_text()
    assert "grounding" in content
    assert "fabrication" in content


def test_list_empty(cfg):
    """cmd_list returns empty list when no notes exist."""
    notes = note_mod.cmd_list("demo-research", config=cfg)
    assert notes == []


def test_list_returns_notes_by_type(cfg):
    """cmd_list returns all notes, cmd_list with type filters correctly."""
    note_mod.cmd_new("demo-research", "literature", "Paper A", config=cfg)
    note_mod.cmd_new("demo-research", "literature", "Paper B", config=cfg)
    note_mod.cmd_new("demo-research", "findings", "Finding X", config=cfg)

    all_notes = note_mod.cmd_list("demo-research", config=cfg)
    assert len(all_notes) == 3

    lit_notes = note_mod.cmd_list("demo-research", "literature", config=cfg)
    assert len(lit_notes) == 2


def test_check_valid_notes_passes(cfg):
    """cmd_check returns empty list when notes are valid."""
    note_mod.cmd_new("demo-research", "experiments", "Experiment plan", config=cfg)
    violations = note_mod.cmd_check("demo-research", config=cfg)
    assert violations == []


def test_check_detects_wrong_type_directory(cfg):
    """cmd_check catches a note whose type field doesn't match its directory."""
    path = note_mod.cmd_new("demo-research", "findings", "Misfiled note", config=cfg)
    # Rewrite the type field to be wrong
    content = path.read_text()
    content = content.replace("type: findings", "type: literature")
    path.write_text(content)

    violations = note_mod.cmd_check("demo-research", config=cfg)
    assert any("findings" in v for v in violations)


def test_unknown_project_raises(cfg):
    """cmd_new raises KeyError for an unknown project."""
    with pytest.raises(KeyError, match="Unknown project"):
        note_mod.cmd_new("ghost-project", "findings", "note", config=cfg)


def test_cli_note_new(tmp_instance, capsys):
    """rv note <project> new prints the created path (project-first form)."""
    from research_vault.cli import main
    result = main(["note", "demo-research", "new", "findings", "CLI finding"])
    assert result == 0
    out = capsys.readouterr().out
    assert "Created:" in out


def test_cli_note_check_clean(tmp_instance, capsys):
    """rv note <project> check exits 0 when notes are valid (project-first form)."""
    from research_vault.cli import main
    from research_vault.config import load_config
    cfg = load_config(reload=True)
    note_mod.cmd_new("demo-research", "experiments", "Experiment", config=cfg)
    result = main(["note", "demo-research", "check"])
    assert result == 0


# ---------------------------------------------------------------------------
# PR-4/K: citekey scaffold field + conformance lint (K-2/K-4)
# ---------------------------------------------------------------------------

def test_literature_template_carries_citekey_placeholder(cfg):
    """A freshly-scaffolded literature note (0.3.2 (the overlay unwind): shared-canonical,
    a single note — no overlay to look past) carries a blank `citekey:`
    field."""
    note_path = note_mod.cmd_new("demo-research", "literature", "A paper", config=cfg)
    content = note_path.read_text()
    assert "citekey:" in content


def test_check_warns_absent_citekey_never_blocks(cfg):
    """A literature note with no citekey WARNs but does not flip the exit code."""
    note_mod.cmd_new("demo-research", "literature", "A paper", config=cfg)
    violations = note_mod.cmd_check("demo-research", config=cfg)
    assert any(v.startswith("[citekey-lint] WARN:") and "missing" in v for v in violations)

    from research_vault.cli import main
    result = main(["note", "demo-research", "check"])
    assert result == 0  # WARN-class, never blocks (DECIDED K-D2)


def test_check_warns_non_conformant_citekey(cfg):
    """A citekey that doesn't match familyShorttitleYear WARNs. 0.3.2 (the overlay unwind):
    the citekey lives directly on the single shared note `cmd_new` returns
    — no overlay indirection."""
    note_path = note_mod.cmd_new("demo-research", "literature", "A paper", config=cfg)
    content = note_path.read_text().replace("citekey: ", "citekey: 2005.14165", 1)
    note_path.write_text(content)

    violations = note_mod.cmd_check("demo-research", config=cfg)
    assert any(
        v.startswith("[citekey-lint] WARN:") and "does not conform" in v
        for v in violations
    )


def test_check_conformant_citekey_no_warning(cfg):
    """A properly-conformant citekey produces zero citekey-lint violations.
    0.3.2 (the overlay unwind): the citekey lives directly on the single shared note."""
    note_path = note_mod.cmd_new("demo-research", "literature", "A paper", config=cfg)
    content = note_path.read_text().replace("citekey: ", "citekey: smithStudyFooBar2023", 1)
    note_path.write_text(content)

    violations = note_mod.cmd_check("demo-research", config=cfg)
    assert not any(v.startswith("[citekey-lint]") for v in violations)


def test_check_sentinel_citekey_still_warns(cfg):
    """The CITEKEY_SENTINEL never accidentally passes conformance."""
    from research_vault.cite import CITEKEY_SENTINEL

    path = note_mod.cmd_new("demo-research", "literature", "A paper", config=cfg)
    content = path.read_text().replace("citekey: ", f"citekey: {CITEKEY_SENTINEL}", 1)
    path.write_text(content)

    violations = note_mod.cmd_check("demo-research", config=cfg)
    assert any(v.startswith("[citekey-lint] WARN:") for v in violations)


# ---------------------------------------------------------------------------
# PR-2: OKF-reserved filenames (index.md / log.md) are skipped everywhere
# ---------------------------------------------------------------------------

def test_reserved_filenames_skipped_in_cmd_list(cfg):
    findings_dir = cfg.project_notes_dir("demo-research") / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)
    (findings_dir / "index.md").write_text("not an OKF note\n", encoding="utf-8")
    (findings_dir / "log.md").write_text("not an OKF note either\n", encoding="utf-8")
    note_mod.cmd_new("demo-research", "findings", "A real finding", config=cfg)

    notes = note_mod.cmd_list("demo-research", "findings", config=cfg)
    paths = {n["path"].name for n in notes}
    assert "index.md" not in paths
    assert "log.md" not in paths
    assert len(notes) == 1


def test_reserved_filenames_skipped_in_cmd_check(cfg):
    """A bare index.md/log.md (no 'type:' field) would otherwise surface as
    a spurious 'missing type' violation — reserved filenames are never OKF
    concept notes and must not be mis-parsed as one."""
    findings_dir = cfg.project_notes_dir("demo-research") / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)
    (findings_dir / "index.md").write_text("not an OKF note\n", encoding="utf-8")

    violations = note_mod.cmd_check("demo-research", config=cfg)
    assert not any("index.md" in v for v in violations)


def test_reserved_filenames_skipped_in_literature_cmd_list(cfg):
    """0.3.2 (the overlay unwind): literature is shared-canonical — the reserved-filename
    skip is exercised through cmd_list against cfg.literature_root, the
    same seam every other shared type uses (iter_literature_notes no
    longer exists — there is no per-project overlay to iterate)."""
    (cfg.literature_root).mkdir(parents=True, exist_ok=True)
    (cfg.literature_root / "index.md").write_text("not an OKF note\n", encoding="utf-8")
    note_mod.cmd_new("demo-research", "literature", "A real paper", config=cfg, note_id="real2024")

    notes = note_mod.cmd_list("demo-research", "literature", config=cfg)
    assert {n["path"].stem for n in notes} == {"real2024"}
