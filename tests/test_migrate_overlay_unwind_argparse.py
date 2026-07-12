"""Arg-parsing tests for scripts/migrate_overlay_unwind.py's main().

Bounded to the arg-parse seam only: an overlay_dir that does not exist makes
migrate_pair() a cheap no-op (prints skip + returns zero-stats), so main()'s
return code is a faithful signal of whether the 3 positionals resolved —
without exercising the full filesystem migration walk.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "migrate_overlay_unwind.py"
_spec = importlib.util.spec_from_file_location("migrate_overlay_unwind", _SCRIPT_PATH)
migrate_overlay_unwind = importlib.util.module_from_spec(_spec)
sys.modules["migrate_overlay_unwind"] = migrate_overlay_unwind
_spec.loader.exec_module(migrate_overlay_unwind)


def test_moc_slug_space_form_parses(tmp_path, capsys):
    """`--moc-slug NAME` (space form) is picked up AND the 3 positionals
    still resolve — it must not fall through to the docstring/return-1 path."""
    overlay_dir = tmp_path / "does-not-exist"
    core_dir = tmp_path / "core"
    moc_dir = tmp_path / "moc"

    rc = migrate_overlay_unwind.main(
        [str(overlay_dir), str(core_dir), str(moc_dir), "--moc-slug", "custom-roles"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    # the docstring/return-1 fallthrough path never fires — its usage text
    # would be printed on failure; a real summary line is printed on success.
    assert "overlays scanned" in out


def test_moc_slug_equals_form_parses(tmp_path, capsys):
    """`--moc-slug=NAME` (equals form) still parses correctly."""
    overlay_dir = tmp_path / "does-not-exist"
    core_dir = tmp_path / "core"
    moc_dir = tmp_path / "moc"

    rc = migrate_overlay_unwind.main(
        [str(overlay_dir), str(core_dir), str(moc_dir), "--moc-slug=custom-roles"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "overlays scanned" in out


def test_missing_positional_falls_through_to_docstring(tmp_path, capsys):
    """A genuinely missing positional still correctly falls through to the
    docstring/return-1 path — proves the fix didn't over-widen matching."""
    overlay_dir = tmp_path / "does-not-exist"
    core_dir = tmp_path / "core"

    rc = migrate_overlay_unwind.main([str(overlay_dir), str(core_dir), "--moc-slug", "x"])
    assert rc == 1
