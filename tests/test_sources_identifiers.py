# SPDX-License-Identifier: AGPL-3.0-or-later
"""test_sources_identifiers.py — identifier-persistence read/write helpers
(sources/identifiers.py).

Covers:
  1. write_external_ids_to_note — stamps present PaperHit-style ids into
     note frontmatter via FRONTMATTER_FIELD_MAP; no-op when note absent or
     no mappable ids.
  2. read_external_ids_from_note — round-trip reconstruction of a
     PaperHit-shaped external_ids dict from a note's frontmatter; blank
     placeholder fields (Fix #32's empty doi:/arxiv_id: scaffold) never
     round-trip as present ids.
  3. Round-trip: write(x) -> read() == x for the full id set.
"""
from __future__ import annotations

from pathlib import Path

from research_vault.sources.identifiers import (
    FRONTMATTER_FIELD_MAP,
    read_external_ids_from_note,
    write_external_ids_to_note,
)


def _note(tmp_path: Path, citekey: str, extra_fm: str = "") -> Path:
    lit_dir = tmp_path / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)
    note = lit_dir / f"{citekey}.md"
    note.write_text(
        f"---\ntype: literature\ncitekey: {citekey}\n{extra_fm}---\n\nBody.\n",
        encoding="utf-8",
    )
    return note


class TestWriteExternalIdsToNote:
    def test_noop_when_note_absent(self, tmp_path: Path) -> None:
        note = tmp_path / "literature" / "absent2026.md"
        ok = write_external_ids_to_note(
            note, {"doi": "10.1/x", "arxiv": "1706.03762"},
        )
        assert ok is False
        assert not note.exists()

    def test_noop_when_no_mappable_ids(self, tmp_path: Path) -> None:
        note = _note(tmp_path, "empty2026")
        ok = write_external_ids_to_note(note, {})
        assert ok is False
        ok2 = write_external_ids_to_note(note, {"unmapped-key": "x"})
        assert ok2 is False

    def test_writes_every_present_id(self, tmp_path: Path) -> None:
        note = _note(tmp_path, "smith2020")
        external_ids = {
            "doi": "10.1234/example",
            "arxiv": "1706.03762",
            "pmcid": "PMC1234567",
            "openalex": "W2741809807",
            "pmid": "31000000",
            "s2": "215416146",
        }
        ok = write_external_ids_to_note(note, external_ids)
        assert ok is True
        text = note.read_text(encoding="utf-8")
        assert "doi: 10.1234/example" in text
        # `arxiv` maps to the existing `arxiv_id` frontmatter convention, not
        # a parallel `arxiv:` field.
        assert "arxiv_id: 1706.03762" in text
        assert "\narxiv:" not in text  # no parallel bare `arxiv:` field
        assert "pmcid: PMC1234567" in text
        assert "openalex: W2741809807" in text
        assert "pmid: 31000000" in text
        assert "s2: 215416146" in text

    def test_only_present_keys_are_written_absent_stays_absent(self, tmp_path: Path) -> None:
        note = _note(tmp_path, "partial2026")
        ok = write_external_ids_to_note(note, {"doi": "10.1/x"})
        assert ok is True
        text = note.read_text(encoding="utf-8")
        assert "doi: 10.1/x" in text
        assert "pmcid:" not in text
        assert "openalex:" not in text
        assert "pmid:" not in text
        assert "s2:" not in text
        assert "arxiv_id:" not in text

    def test_empty_string_value_is_not_written(self, tmp_path: Path) -> None:
        note = _note(tmp_path, "blankval2026")
        ok = write_external_ids_to_note(note, {"doi": "10.1/x", "pmid": ""})
        assert ok is True
        text = note.read_text(encoding="utf-8")
        assert "doi: 10.1/x" in text
        assert "pmid:" not in text

    def test_stamping_over_blank_placeholder_does_not_corrupt_next_line(
        self, tmp_path: Path,
    ) -> None:
        """Regression: note.cmd_new's literature scaffold ships EMPTY
        `doi: `/`arxiv_id: `/etc. placeholders (Fix #32). Stamping a value
        over a blank existing field must not swallow the following line —
        a `\\s*` (vs `[ \\t]*`) after the colon crosses the newline when
        the value is blank and corrupts the NEXT field entirely."""
        note = _note(
            tmp_path, "blanklines2026",
            extra_fm="doi: \narxiv_id: \npmcid: \nopenalex: \npmid: \ns2: \n",
        )
        ok = write_external_ids_to_note(
            note,
            {"doi": "10.5555/x", "arxiv": "1706.03762", "s2": "13756489", "pmid": "31000111"},
        )
        assert ok is True
        text = note.read_text(encoding="utf-8")
        # Every field survives on its OWN line, in order — none swallowed.
        assert "doi: 10.5555/x\n" in text
        assert "arxiv_id: 1706.03762\n" in text
        assert "pmcid: \n" in text
        assert "openalex: \n" in text
        assert "pmid: 31000111\n" in text
        assert "s2: 13756489\n" in text
        ids = read_external_ids_from_note(note)
        assert ids == {
            "doi": "10.5555/x", "arxiv": "1706.03762",
            "s2": "13756489", "pmid": "31000111",
        }


class TestReadExternalIdsFromNote:
    def test_empty_dict_when_note_absent(self, tmp_path: Path) -> None:
        note = tmp_path / "literature" / "absent2026.md"
        assert read_external_ids_from_note(note) == {}

    def test_empty_dict_when_no_id_fields(self, tmp_path: Path) -> None:
        note = _note(tmp_path, "noids2026")
        assert read_external_ids_from_note(note) == {}

    def test_blank_placeholder_fields_do_not_round_trip(self, tmp_path: Path) -> None:
        # Fix #32 scaffold: literature notes ship EMPTY doi:/arxiv_id: (and
        # now pmcid:/openalex:/pmid:/s2:) placeholders. An unfilled
        # placeholder must never read back as a present id.
        note = _note(
            tmp_path, "unfilled2026",
            extra_fm="doi: \narxiv_id: \npmcid: \nopenalex: \npmid: \ns2: \n",
        )
        assert read_external_ids_from_note(note) == {}

    def test_reads_all_present_ids(self, tmp_path: Path) -> None:
        note = _note(
            tmp_path, "full2026",
            extra_fm=(
                "doi: 10.5555/full\n"
                "arxiv_id: 2005.14165\n"
                "pmcid: PMC7654321\n"
                "openalex: W1234567890\n"
                "pmid: 30000000\n"
                "s2: 999888777\n"
            ),
        )
        ids = read_external_ids_from_note(note)
        assert ids == {
            "doi": "10.5555/full",
            "arxiv": "2005.14165",
            "pmcid": "PMC7654321",
            "openalex": "W1234567890",
            "pmid": "30000000",
            "s2": "999888777",
        }


class TestRoundTrip:
    def test_write_then_read_equals_source(self, tmp_path: Path) -> None:
        note = _note(tmp_path, "roundtrip2026")
        source = {
            "doi": "10.9999/rt",
            "arxiv": "1810.04805",
            "pmcid": "PMC1111111",
            "openalex": "W9999999999",
            "pmid": "12345678",
            "s2": "1122334455",
        }
        assert write_external_ids_to_note(note, source) is True
        got = read_external_ids_from_note(note)
        assert got == source

    def test_round_trip_is_no_network(self, tmp_path: Path, monkeypatch) -> None:
        """Both write and read must be pure file-IO — no adapter/subprocess
        call. Blocking subprocess.run proves the round trip never shells out."""
        import subprocess as _subprocess

        def _forbidden(*a, **kw):
            raise AssertionError("round trip must not shell out (no network)")

        monkeypatch.setattr(_subprocess, "run", _forbidden)

        note = _note(tmp_path, "nonet2026")
        source = {"doi": "10.1/nonet", "s2": "42"}
        assert write_external_ids_to_note(note, source) is True
        assert read_external_ids_from_note(note) == source

    def test_field_map_keys_are_a_stable_contract(self) -> None:
        assert FRONTMATTER_FIELD_MAP == {
            "doi": "doi",
            "arxiv": "arxiv_id",
            "pmcid": "pmcid",
            "openalex": "openalex",
            "pmid": "pmid",
            "s2": "s2",
        }
