"""test_fulltext_cmd.py — `rv research fulltext <project> <citekey>` (tier 1).

Hermetic: the OA fetch waterfall itself is exercised via enrich.enrich_hit's
provider seam (monkeypatched at the provider-network level, not re-mocked
here) — these tests drive the CLI wrapper's own logic: PaperHit construction
from CLI args, cache-dir resolution, note frontmatter stamping, and the
abstract-only degrade path.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault import fulltext
from research_vault.config import load_config
from research_vault.sources import enrich as enrich_mod
from tests.gitutil import invoke_cli


def _args(**overrides):
    defaults = dict(
        project="demo-research", citekey="smith2020", title="A Paper",
        doi=None, arxiv=None, pmid=None, pmcid=None, openalex=None,
        oa_source="", oa_url=None, oa_status=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestHitFromArgs:
    def test_builds_external_ids_from_flags(self) -> None:
        hit = fulltext._hit_from_args(_args(doi="10.1/x", arxiv="1706.03762"))
        assert hit.external_ids["doi"] == "10.1/x"
        assert hit.external_ids["arxiv"] == "1706.03762"

    def test_title_fallback_to_citekey(self) -> None:
        hit = fulltext._hit_from_args(_args(title=""))
        assert hit.title == "smith2020"


class TestStampNoteFrontmatter:
    def test_noop_when_note_absent(self, tmp_path: Path) -> None:
        assert fulltext.stamp_note_frontmatter(tmp_path / "absent.md", {"read_basis": "full-text"}) is False

    def test_injects_new_field(self, tmp_path: Path) -> None:
        note = tmp_path / "smith2020.md"
        note.write_text("---\ntype: literature\ncitekey: smith2020\n---\n\nBody.\n", encoding="utf-8")
        ok = fulltext.stamp_note_frontmatter(note, {"read_basis": "full-text", "oa_status": "green"})
        assert ok is True
        text = note.read_text(encoding="utf-8")
        assert "read_basis: full-text" in text
        assert "oa_status: green" in text
        assert "citekey: smith2020" in text  # existing fields untouched
        assert "Body." in text  # body untouched

    def test_replaces_existing_field(self, tmp_path: Path) -> None:
        note = tmp_path / "smith2020.md"
        note.write_text(
            "---\ntype: literature\nread_basis: abstract-only\n---\n\nBody.\n", encoding="utf-8",
        )
        fulltext.stamp_note_frontmatter(note, {"read_basis": "full-text"})
        text = note.read_text(encoding="utf-8")
        assert "read_basis: full-text" in text
        assert "read_basis: abstract-only" not in text
        assert text.count("read_basis:") == 1

    def test_idempotent_double_stamp(self, tmp_path: Path) -> None:
        note = tmp_path / "smith2020.md"
        note.write_text("---\ntype: literature\n---\n\nBody.\n", encoding="utf-8")
        fields = {"read_basis": "full-text", "full_text_provider": "arxiv-pdf"}
        fulltext.stamp_note_frontmatter(note, fields)
        fulltext.stamp_note_frontmatter(note, fields)
        text = note.read_text(encoding="utf-8")
        assert text.count("read_basis:") == 1
        assert text.count("full_text_provider:") == 1


class TestCmdFulltextDegradeToAbstract:
    def test_all_providers_decline_degrades_gracefully(self, tmp_instance, monkeypatch, capsys) -> None:
        # No identifiers at all -> every provider's can_handle is False -> None.
        cfg = load_config(reload=True)
        args = _args(project="demo-research", citekey="nobody2020", title="Nobody")
        rc = fulltext.cmd_fulltext(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "abstract-only" in out

    def test_no_note_file_does_not_crash(self, tmp_instance) -> None:
        # literature/<citekey>.md does not exist yet — must not raise.
        args = _args(project="demo-research", citekey="unfiled2020")
        rc = fulltext.cmd_fulltext(args)
        assert rc == 0


class TestCmdFulltextFullTextPath:
    def test_full_text_found_stamps_existing_note(self, tmp_instance, monkeypatch, capsys) -> None:
        cfg = load_config(reload=True)
        # PR-A: read_basis/full_text_* provenance is intrinsic — stamped on
        # the CENTRAL CORE, not the per-project overlay.
        core_dir = cfg.literature_root
        core_dir.mkdir(parents=True, exist_ok=True)
        note = core_dir / "smith2020.md"
        note.write_text("---\ntype: literature\ncitekey: smith2020\n---\n\nBody.\n", encoding="utf-8")

        # Force enrich_hit to succeed regardless of providers by monkeypatching
        # the provider registry the CLI wires up.
        import datetime as _dt

        class _AlwaysHitsProvider:
            name = "arxiv-pdf"

            def can_handle(self, hit):
                return True

            def fetch(self, hit):
                return enrich_mod.FetchResult(
                    text="Real body text with a finding: 12.4 points improvement.",
                    provider="arxiv-pdf", url="https://arxiv.org/pdf/1706.03762.pdf",
                    oa_status="green", content_kind="pdf",
                    fetched_at=_dt.datetime.now(_dt.UTC), chars=60,
                )

        monkeypatch.setattr(
            enrich_mod, "default_fetch_providers",
            lambda **kw: [_AlwaysHitsProvider()],
        )

        args = _args(project="demo-research", citekey="smith2020", arxiv="1706.03762")
        rc = fulltext.cmd_fulltext(args)
        assert rc == 0

        text = note.read_text(encoding="utf-8")
        assert "read_basis: full-text" in text
        assert "full_text_provider: arxiv-pdf" in text
        assert "oa_status: green" in text
        assert "full_text_url: https://arxiv.org/pdf/1706.03762.pdf" in text


class TestReadIdentifiersFromFiledNoteNoReResolution:
    """Identifier-persistence read path: `rv research fulltext` reads a
    filed note's persisted identifiers instead of re-resolving them.

    Drives the REAL provider selection (`sources.enrich.PMCProvider`, whose
    `can_handle`/`fetch` read `hit.external_ids["pmcid"]` exactly as
    production code does) — not a monkeypatched seam standing in for it.
    Only the actual network boundary (`enrich._http_get_bytes`) is
    monkeypatched; `subprocess.run` is blocked entirely to PROVE no adapter
    call (asta/S2/etc.) — i.e. no identifier re-resolution — occurs when the
    id already lives in the note's frontmatter.
    """

    def test_pmcid_read_from_note_drives_real_pmc_provider(
        self, tmp_instance, monkeypatch, capsys,
    ) -> None:
        import subprocess as _subprocess

        cfg = load_config(reload=True)
        # PR-A: pmcid/external ids are intrinsic — filed on the CENTRAL CORE.
        core_dir = cfg.literature_root
        core_dir.mkdir(parents=True, exist_ok=True)
        note = core_dir / "pmcread2026.md"
        # Identifier-persistence write path already ran (rv research add) —
        # the note carries a persisted pmcid, no other id.
        note.write_text(
            "---\ntype: literature\ncitekey: pmcread2026\npmcid: PMC7654321\n---\n\nBody.\n",
            encoding="utf-8",
        )

        # The real PMC provider (sources/enrich.py) — no fake stand-in.
        from research_vault.sources.enrich import PMCProvider

        monkeypatch.setattr(
            enrich_mod, "default_fetch_providers", lambda **kw: [PMCProvider()],
        )

        body_text = (
            "Real full-text body pulled via pmcid, no CLI flag supplied. " * 10
        )
        jats_xml = f"<article><body><p>{body_text}</p></body></article>"

        def _fake_http_get_bytes(url, *, timeout=20):
            assert "PMC7654321" in url  # the id from the NOTE reached the fetch URL
            return jats_xml.encode("utf-8"), "application/xml"

        monkeypatch.setattr(enrich_mod, "_http_get_bytes", _fake_http_get_bytes)

        # No id flags at all on the CLI args — the note is the only source.
        def _forbidden_subprocess(*a, **kw):
            raise AssertionError(
                "identifier re-resolution occurred (subprocess.run called) — "
                "the persisted note pmcid should have made this unnecessary",
            )

        monkeypatch.setattr(_subprocess, "run", _forbidden_subprocess)

        args = _args(project="demo-research", citekey="pmcread2026")
        rc = fulltext.cmd_fulltext(args)
        assert rc == 0

        text = note.read_text(encoding="utf-8")
        assert "read_basis: full-text" in text
        assert "full_text_provider: pmc" in text
        # The persisted pmcid is untouched (still in the note, never mutated
        # by the fulltext read path).
        assert "pmcid: PMC7654321" in text

    def test_no_flags_no_note_ids_degrades_to_abstract_only(
        self, tmp_instance, capsys,
    ) -> None:
        # Sanity: absent note / absent ids -> the existing abstract-only
        # degrade path (unchanged by the read-from-note wiring).
        args = _args(project="demo-research", citekey="noidsatall2026")
        rc = fulltext.cmd_fulltext(args)
        assert rc == 0
        assert "abstract-only" in capsys.readouterr().out


class TestCliWiring:
    def test_research_fulltext_reaches_the_dispatcher(self, tmp_instance) -> None:
        # No identifiers -> all providers decline -> exit 0 (graceful degrade).
        # This proves `rv research fulltext` is actually wired into the CLI
        # dispatcher, not just callable as a bare Python function.
        rc = invoke_cli(["research", "fulltext", "demo-research", "unfiled2020"])
        assert rc == 0
