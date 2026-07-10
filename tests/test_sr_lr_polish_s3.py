"""test_sr_lr_polish_s3.py — review-loop polish Slice 3: F15 robust corpus parse + fail-loud.

Acceptance criteria:
  - Well-formed corpus → N relate nodes.
  - Rows-present-but-none-[NEW]-parseable → raises ValueError + writes no phase2 dag.
  - Empty corpus (0 rows) → graceful direct-synthesize, no raise.
  - Format-variant rows (whitespace, case) still parse.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# _parse_new_citekeys_from_text unit tests
# ---------------------------------------------------------------------------

from research_vault.review import _parse_new_citekeys_from_text, _count_corpus_data_rows


def test_parse_standard_format():
    """Standard | [NEW] | citekey | title | rows are parsed."""
    text = """\
# Corpus

| Annotation | Citekey | Title |
|---|---|---|
| [NEW] | smith2020 | Paper A |
| [IN-CORPUS:old2019] | old2019 | Old Paper |
| [NEW] | jones2021 | Paper B |
"""
    assert _parse_new_citekeys_from_text(text) == ["smith2020", "jones2021"]


def test_parse_excludes_in_corpus():
    """[IN-CORPUS:*] rows must NOT appear in the output."""
    text = """\
| [IN-CORPUS:alpha2019] | alpha2019 | Alpha |
| [IN-CORPUS:beta2020] | beta2020 | Beta |
"""
    assert _parse_new_citekeys_from_text(text) == []


def test_parse_tolerates_extra_whitespace():
    """Extra whitespace around the annotation and citekey is tolerated."""
    text = """\
|  [NEW]  |  smith2020  | Paper A |
|  [NEW]  |  jones2021  | Paper B |
"""
    result = _parse_new_citekeys_from_text(text)
    assert "smith2020" in result
    assert "jones2021" in result


def test_parse_case_insensitive_new():
    """[new] and [New] are accepted as valid [NEW] annotations."""
    text = """\
| [new] | smith2020 | Paper A |
| [New] | jones2021 | Paper B |
| [NEW] | brown2022 | Paper C |
"""
    result = _parse_new_citekeys_from_text(text)
    assert set(result) == {"smith2020", "jones2021", "brown2022"}


def test_parse_table_pipe_variants():
    """Table rows with no trailing pipe are tolerated (column-count variant)."""
    text = """\
| [NEW] | smith2020 | Paper A
| [NEW] | jones2021 | Paper B
"""
    result = _parse_new_citekeys_from_text(text)
    assert "smith2020" in result
    assert "jones2021" in result


def test_parse_empty_corpus():
    """An empty corpus file returns an empty list."""
    assert _parse_new_citekeys_from_text("") == []
    assert _parse_new_citekeys_from_text("# Corpus\n\nNo table here.\n") == []


def test_parse_strict_no_widening_to_in_corpus():
    """[IN-CORPUS:*] is strictly excluded — this is a [NEW]-only parser."""
    text = "| [IN-CORPUS:zheng2023] | zheng2023 | Title |\n"
    assert _parse_new_citekeys_from_text(text) == []


# ---------------------------------------------------------------------------
# _count_corpus_data_rows unit tests
# ---------------------------------------------------------------------------

def test_count_data_rows_standard():
    """Standard corpus with 2 [NEW] + 1 [IN-CORPUS] = 3 data rows."""
    text = """\
| Annotation | Citekey | Title |
|---|---|---|
| [NEW] | smith2020 | Paper A |
| [NEW] | jones2021 | Paper B |
| [IN-CORPUS:old2019] | old2019 | Old Paper |
"""
    assert _count_corpus_data_rows(text) == 3


def test_count_data_rows_excludes_separator():
    """Separator rows (|---|...) are not counted."""
    text = "| --- | --- | --- |\n| [NEW] | x | y |\n"
    assert _count_corpus_data_rows(text) == 1


def test_count_data_rows_excludes_header():
    """Header rows (Annotation, Citekey, ...) are not counted."""
    text = "| Annotation | Citekey | Title |\n| [NEW] | x | y |\n"
    assert _count_corpus_data_rows(text) == 1


def test_count_data_rows_empty():
    """Empty file returns 0."""
    assert _count_corpus_data_rows("") == 0
    assert _count_corpus_data_rows("# Corpus\n\nSome prose.\n") == 0


# ---------------------------------------------------------------------------
# cmd_expand integration tests
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    from research_vault.config import load_config
    return load_config(reload=True)


def test_expand_well_formed_corpus_emits_relate_nodes(cfg, tmp_path):
    """A well-formed corpus with [NEW] rows emits one relate-<key> per row."""
    corpus = tmp_path / "_corpus.md"
    corpus.write_text("""\
| Annotation | Citekey | Title |
|---|---|---|
| [NEW] | smith2020 | Paper A |
| [NEW] | jones2021 | Paper B |
| [IN-CORPUS:old2019] | old2019 | Old Paper |
""", encoding="utf-8")

    from research_vault.review import cmd_expand
    manifest = cmd_expand("demo-research", "scope-s3", corpus_path=corpus, config=cfg)
    node_ids = {n["id"] for n in manifest["nodes"]}
    assert "relate-smith2020" in node_ids
    assert "relate-jones2021" in node_ids
    assert "relate-old2019" not in node_ids  # excluded


def test_expand_rows_present_but_no_new_raises(cfg, tmp_path):
    """If corpus has annotation rows but none are [NEW], cmd_expand raises ValueError.

    No phase2-dag.json must be written (the check must precede file write).
    This is the green-but-vacuous guard (F15).
    """
    corpus = tmp_path / "_corpus.md"
    corpus.write_text("""\
| Annotation | Citekey | Title |
|---|---|---|
| [IN-CORPUS:alpha2019] | alpha2019 | Alpha |
| [IN-CORPUS:beta2020] | beta2020 | Beta |
""", encoding="utf-8")

    from research_vault.review import cmd_expand
    review_dir_path = cfg.project_notes_dir("demo-research") / "reviews" / "scope-s3-block"

    with pytest.raises(ValueError, match=r"\[NEW\]"):
        cmd_expand("demo-research", "scope-s3-block", corpus_path=corpus, config=cfg)

    # Phase-2 manifest must NOT have been written
    dag_path = review_dir_path / "phase2-dag.json"
    assert not dag_path.exists(), (
        f"phase2-dag.json was written despite no [NEW] rows — vacuous guard failed"
    )


def test_expand_truly_empty_corpus_graceful(cfg, tmp_path):
    """A corpus with NO annotation rows degrades gracefully (no raise, 0 relate nodes)."""
    corpus = tmp_path / "_corpus.md"
    corpus.write_text("# Corpus\n\nNo papers this round.\n", encoding="utf-8")

    from research_vault.review import cmd_expand
    manifest = cmd_expand("demo-research", "scope-s3-empty", corpus_path=corpus, config=cfg)
    # No relate nodes; still has synthesize+critic+approve
    node_ids = {n["id"] for n in manifest["nodes"]}
    relate_nodes = [nid for nid in node_ids if nid.startswith("relate-")]
    assert relate_nodes == [], f"Expected no relate nodes; got: {relate_nodes}"
    assert "review-synthesize" in node_ids
    assert "approve-review" in node_ids


def test_expand_variant_row_format_parses(cfg, tmp_path):
    """Variant whitespace and case in corpus rows still parse correctly."""
    corpus = tmp_path / "_corpus.md"
    corpus.write_text("""\
|  [new]  |  zheng2023  | Some Title  |
|  [NEW]  |  wang2024   | Other Title |
""", encoding="utf-8")

    from research_vault.review import cmd_expand
    manifest = cmd_expand("demo-research", "scope-s3-variant", corpus_path=corpus, config=cfg)
    node_ids = {n["id"] for n in manifest["nodes"]}
    assert "relate-zheng2023" in node_ids
    assert "relate-wang2024" in node_ids


def test_expand_error_message_mentions_format(cfg, tmp_path):
    """The ValueError from the vacuous guard must mention the expected format."""
    corpus = tmp_path / "_corpus.md"
    corpus.write_text("""\
| [IN-CORPUS:only-paper] | only-paper | Only Paper |
""", encoding="utf-8")

    from research_vault.review import cmd_expand
    with pytest.raises(ValueError) as exc_info:
        cmd_expand("demo-research", "scope-s3-fmt", corpus_path=corpus, config=cfg)

    msg = str(exc_info.value)
    # Must name the expected format shape
    assert "[NEW]" in msg
    assert "citekey" in msg.lower() or "| [NEW] |" in msg
