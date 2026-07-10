"""test_cite_keys.py — PR-4/K-1: the canonical citekey keyer + conformance regex.

Hermetic, no network. Exercises the pure ``make_citekey``/``CITEKEY_RE``/
``CITEKEY_SENTINEL`` surface that ``note.py``/``research.py`` reuse for the
review-loop (Zotero-free) citekey path.
"""
from research_vault.cite import (
    CITEKEY_RE,
    CITEKEY_SENTINEL,
    _make_citekey,
    make_citekey,
)


def test_make_citekey_basic_shape():
    key = make_citekey("Smith", "A Study of Foo Bar", "2023", set())
    assert key == "smithStudyFooBar2023"


def test_make_citekey_conforms_to_citekey_re():
    key = make_citekey("Baltaji", "Persona Constancy in Multi-Agent LLMs", "2024", set())
    assert CITEKEY_RE.match(key)


def test_make_citekey_disambiguates_on_collision():
    existing = {"smithStudyFooBar2023"}
    key = make_citekey("Smith", "A Study of Foo Bar", "2023", existing)
    assert key == "smithStudyFooBar2023a"


def test_make_citekey_disambiguates_second_collision():
    existing = {"smithStudyFooBar2023", "smithStudyFooBar2023a"}
    key = make_citekey("Smith", "A Study of Foo Bar", "2023", existing)
    assert key == "smithStudyFooBar2023b"


def test_make_citekey_no_family_falls_back_to_anon():
    key = make_citekey(None, "A Mysterious Paper", "2021", set())
    assert key.startswith("anon")
    assert CITEKEY_RE.match(key)


def test_make_citekey_is_pure_zotero_free():
    """make_citekey never touches the network or Zotero — same result twice."""
    a = make_citekey("Lee", "Cross Lingual Evaluation", "2022", set())
    b = make_citekey("Lee", "Cross Lingual Evaluation", "2022", set())
    assert a == b


def test_backward_compat_alias_is_the_same_function():
    assert _make_citekey is make_citekey


def test_citekey_re_rejects_non_conformant_keys():
    for bad in ["2005.14165", "S2:12345", "openalex-W123", "some-random-slug", "Smith2023", ""]:
        assert not CITEKEY_RE.match(bad), f"{bad!r} should NOT conform"


def test_citekey_re_accepts_conformant_keys():
    for good in ["smithStudyFooBar2023", "leeCrossLingual2022", "anonMysteriousPaper2021a"]:
        assert CITEKEY_RE.match(good), f"{good!r} should conform"


def test_citekey_sentinel_never_conforms():
    """The unresolvable sentinel must never accidentally pass the conformance regex."""
    assert not CITEKEY_RE.match(CITEKEY_SENTINEL)
