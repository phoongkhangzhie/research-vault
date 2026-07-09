"""test_sources_enrich.py — OA-first full-text enrichment (tier 1).

Hermetic: every provider's network call is monkeypatched at the module-level
`_http_get_bytes` / `_fetch_pmc_xml` / `_fetch_unpaywall` seam (mirrors the
existing sources/ adapter test pattern — `_fetch_atom`/`_fetch_json`). The
PDF path uses a REAL tiny PDF built with pymupdf itself (no PDF-bytes
fixture guessing) since pymupdf is a core dep as of 0.3.0.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources import enrich
from research_vault.sources.base import PaperHit


def _hit(**kw) -> PaperHit:
    defaults = dict(
        title="A Paper", year=2020, authors=["A. Author"],
        external_ids={}, abstract="abs", citation_count=0, source="semantic-scholar",
    )
    defaults.update(kw)
    return PaperHit(**defaults)


REAL_RESULT_TEXT = (
    "We evaluate our method on the benchmark and find a 12.4 point "
    "improvement over the baseline across five random seeds, under the "
    "standard held-out test split. Limitations: results may not transfer "
    "to out-of-distribution settings; we only test English text. " * 3
)

LOGIN_WALL_TEXT = (
    "Sign in to continue. Create account or Log in to view this content. "
    "You must authenticate to access this article. Please sign in with your "
    "institutional credentials or create a free account to continue reading. "
    "This publisher requires registration before granting article access. "
    "Log in via your library or subscription provider to proceed further. "
)

BOT_CHECK_TEXT = (
    "Just a moment... Checking your browser before accessing this page. "
    "This process is automatic. Your browser will redirect to your requested "
    "content shortly. Please allow up to 5 seconds. Ray ID: 8f3a9c2d1e0b7f4a "
    "Cloudflare DDoS protection is active for this site. Enable javascript "
    "and cookies to continue, then complete the security check below. "
)


# ---------------------------------------------------------------------------
# 1. The junk / login-wall screen
# ---------------------------------------------------------------------------

class TestScreenFetch:
    def test_real_content_passes(self) -> None:
        assert enrich.screen_fetch(REAL_RESULT_TEXT) is None

    def test_login_wall_rejected(self) -> None:
        reason = enrich.screen_fetch(LOGIN_WALL_TEXT)
        assert reason is not None and "login" in reason.lower()

    def test_bot_check_rejected(self) -> None:
        reason = enrich.screen_fetch(BOT_CHECK_TEXT)
        assert reason is not None and "bot" in reason.lower()

    def test_empty_content_rejected(self) -> None:
        assert enrich.screen_fetch("") is not None

    def test_auth_redirect_path_rejected(self) -> None:
        reason = enrich.screen_fetch(REAL_RESULT_TEXT, url="https://example.org/login?next=/paper")
        assert reason is not None and "auth" in reason.lower()

    def test_pdf_binary_garbage_rejected(self) -> None:
        garbage = "%PDF-1.4\nendstream\nendobj\n" + ("x" * 400)
        reason = enrich.screen_fetch(garbage)
        assert reason is not None

    def test_short_legitimate_text_mentioning_author_not_false_flagged(self) -> None:
        """kz-argus follow-up (PR #184): the bare `"auth"` login-signal
        substring-matched legitimate content mentioning "author"/"authors"
        (e.g. a short acknowledgements/attribution snippet under the
        <1000-char guard) — a false login-wall rejection. Tighten the
        signal so real prose about authors doesn't trip it."""
        text = (
            "This work builds on prior author contributions and cites "
            "related work by several authors in the field of natural "
            "language processing and machine learning systems research. "
            "We thank the original authors for making their code and data "
            "publicly available, which enabled this follow-up study to "
            "reproduce and extend their reported findings across settings."
        )
        assert 300 <= len(text) < 1000
        reason = enrich.screen_fetch(text)
        assert reason is None


# ---------------------------------------------------------------------------
# 2. PDF -> text (real pymupdf, no mocking of the library itself)
# ---------------------------------------------------------------------------

class TestPdfExtraction:
    def _make_pdf_bytes(self, text: str) -> bytes:
        import pymupdf
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), text)
        raw = doc.tobytes()
        doc.close()
        return raw

    def test_pdf_bytes_to_text_extracts_real_text(self) -> None:
        pdf_bytes = self._make_pdf_bytes("hello full text world")
        extracted = enrich._pdf_bytes_to_text(pdf_bytes)
        assert "hello" in extracted and "world" in extracted


# ---------------------------------------------------------------------------
# 3. HTML -> text (stdlib)
# ---------------------------------------------------------------------------

def test_html_to_text_strips_tags_and_script() -> None:
    html = "<html><head><script>evil()</script></head><body><p>Real content here.</p></body></html>"
    text = enrich._html_to_text(html)
    assert "Real content here." in text
    assert "evil()" not in text


# ---------------------------------------------------------------------------
# 4. PMCProvider
# ---------------------------------------------------------------------------

JATS_XML = (
    '<?xml version="1.0"?>\n'
    "<article><body><p>" + REAL_RESULT_TEXT + "</p></body></article>"
)

JATS_XML_LOGIN_WALL = (
    '<?xml version="1.0"?>\n'
    "<article><body><p>" + LOGIN_WALL_TEXT + "</p></body></article>"
)


class TestPMCProvider:
    def test_can_handle_requires_pmcid(self) -> None:
        provider = enrich.PMCProvider()
        assert provider.can_handle(_hit(external_ids={"pmcid": "PMC123"})) is True
        assert provider.can_handle(_hit(external_ids={})) is False

    def test_fetch_extracts_jats_body_text(self, monkeypatch) -> None:
        monkeypatch.setattr(enrich, "_fetch_pmc_xml", lambda pmcid: JATS_XML)
        provider = enrich.PMCProvider()
        result = provider.fetch(_hit(external_ids={"pmcid": "PMC123"}))
        assert result is not None
        assert result.junk_reason is None
        assert "12.4 point" in result.text
        assert result.provider == "pmc"
        assert result.content_kind == "xml"

    def test_fetch_declines_on_login_wall(self, monkeypatch) -> None:
        monkeypatch.setattr(enrich, "_fetch_pmc_xml", lambda pmcid: JATS_XML_LOGIN_WALL)
        provider = enrich.PMCProvider()
        result = provider.fetch(_hit(external_ids={"pmcid": "PMC123"}))
        assert result is not None
        assert result.junk_reason is not None

    def test_fetch_network_failure_returns_none(self, monkeypatch) -> None:
        def boom(pmcid):
            raise OSError("network down")
        monkeypatch.setattr(enrich, "_fetch_pmc_xml", boom)
        provider = enrich.PMCProvider()
        assert provider.fetch(_hit(external_ids={"pmcid": "PMC123"})) is None

    def test_fetch_malformed_xml_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(enrich, "_fetch_pmc_xml", lambda pmcid: "<not><valid")
        provider = enrich.PMCProvider()
        assert provider.fetch(_hit(external_ids={"pmcid": "PMC123"})) is None


# ---------------------------------------------------------------------------
# 5. S2OAProvider / OpenAlexOAProvider / ArxivPDFProvider (shared _fetch_generic)
# ---------------------------------------------------------------------------

class TestS2OAProvider:
    def test_can_handle_requires_oa_url_from_s2(self) -> None:
        provider = enrich.S2OAProvider()
        assert provider.can_handle(_hit(oa_url="https://x/y.pdf", oa_source="semantic-scholar")) is True
        assert provider.can_handle(_hit(oa_url="https://x/y.pdf", oa_source="openalex")) is False
        assert provider.can_handle(_hit()) is False

    def test_fetch_html_landing_page(self, monkeypatch) -> None:
        html = f"<html><body><p>{REAL_RESULT_TEXT}</p></body></html>"
        monkeypatch.setattr(
            enrich, "_http_get_bytes",
            lambda url, **kw: (html.encode("utf-8"), "text/html"),
        )
        provider = enrich.S2OAProvider()
        hit = _hit(oa_url="https://example.org/landing", oa_status="green", oa_source="semantic-scholar")
        result = provider.fetch(hit)
        assert result is not None and result.junk_reason is None
        assert "12.4 point" in result.text
        assert result.content_kind == "html"


class TestOpenAlexOAProvider:
    def test_can_handle_requires_openalex_source(self) -> None:
        provider = enrich.OpenAlexOAProvider()
        assert provider.can_handle(_hit(source="openalex", oa_url="https://x")) is True
        assert provider.can_handle(_hit(source="semantic-scholar", oa_url="https://x")) is False


class TestArxivPDFProvider:
    def test_can_handle_requires_arxiv_id(self) -> None:
        provider = enrich.ArxivPDFProvider()
        assert provider.can_handle(_hit(external_ids={"arxiv": "1706.03762"})) is True
        assert provider.can_handle(_hit(external_ids={})) is False

    def test_fetch_derives_pdf_url_from_arxiv_id(self, monkeypatch) -> None:
        captured = {}

        def fake_get(url, **kw):
            captured["url"] = url
            html = f"<html><body>{REAL_RESULT_TEXT}</body></html>"
            return html.encode("utf-8"), "text/html"

        monkeypatch.setattr(enrich, "_http_get_bytes", fake_get)
        provider = enrich.ArxivPDFProvider()
        provider.fetch(_hit(external_ids={"arxiv": "1706.03762"}))
        assert captured["url"] == "https://arxiv.org/pdf/1706.03762.pdf"


# ---------------------------------------------------------------------------
# 6. UnpaywallProvider — requires email (config, not a credential)
# ---------------------------------------------------------------------------

class TestUnpaywallProvider:
    def test_can_handle_false_without_email(self) -> None:
        provider = enrich.UnpaywallProvider(email="")
        assert provider.can_handle(_hit(external_ids={"doi": "10.1/x"})) is False

    def test_can_handle_true_with_email_and_doi(self) -> None:
        provider = enrich.UnpaywallProvider(email="ops@example.org")
        assert provider.can_handle(_hit(external_ids={"doi": "10.1/x"})) is True

    def test_fetch_reads_best_oa_location(self, monkeypatch) -> None:
        def fake_unpaywall(doi, email):
            assert doi == "10.1/x"
            assert email == "ops@example.org"
            return {
                "oa_status": "gold",
                "best_oa_location": {"url_for_pdf": None, "url": "https://example.org/oa.html"},
            }

        html = f"<html><body>{REAL_RESULT_TEXT}</body></html>"
        monkeypatch.setattr(enrich, "_fetch_unpaywall", fake_unpaywall)
        monkeypatch.setattr(enrich, "_http_get_bytes", lambda url, **kw: (html.encode("utf-8"), "text/html"))
        provider = enrich.UnpaywallProvider(email="ops@example.org")
        result = provider.fetch(_hit(external_ids={"doi": "10.1/x"}))
        assert result is not None
        assert result.oa_status == "gold"

    def test_fetch_no_oa_location_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(enrich, "_fetch_unpaywall", lambda doi, email: {"best_oa_location": None})
        provider = enrich.UnpaywallProvider(email="ops@example.org")
        assert provider.fetch(_hit(external_ids={"doi": "10.1/x"})) is None


# ---------------------------------------------------------------------------
# 7. providers_from_config — [fulltext] unpaywall_email wiring
# ---------------------------------------------------------------------------

class TestProvidersFromConfig:
    def test_no_email_configured_unpaywall_self_skips(self) -> None:
        class FakeCfg:
            fulltext = {"unpaywall_email": ""}

        providers = enrich.providers_from_config(FakeCfg())
        unpaywall = next(p for p in providers if p.name == "unpaywall")
        assert unpaywall.can_handle(_hit(external_ids={"doi": "10.1/x"})) is False

    def test_email_configured_unpaywall_active(self) -> None:
        class FakeCfg:
            fulltext = {"unpaywall_email": "ops@example.org"}

        providers = enrich.providers_from_config(FakeCfg())
        unpaywall = next(p for p in providers if p.name == "unpaywall")
        assert unpaywall.can_handle(_hit(external_ids={"doi": "10.1/x"})) is True

    def test_none_config_defaults_safely(self) -> None:
        providers = enrich.providers_from_config(None)
        assert len(providers) == 5


# ---------------------------------------------------------------------------
# 8. enrich_hit — ordered fallback + all-decline degrade + cache
# ---------------------------------------------------------------------------

class _StubProvider:
    def __init__(self, name: str, *, handles: bool, result: enrich.FetchResult | None):
        self.name = name
        self._handles = handles
        self._result = result
        self.fetch_calls = 0

    def can_handle(self, hit: PaperHit) -> bool:
        return self._handles

    def fetch(self, hit: PaperHit) -> enrich.FetchResult | None:
        self.fetch_calls += 1
        return self._result


def _good_result(provider: str) -> enrich.FetchResult:
    import datetime as _dt
    return enrich.FetchResult(
        text=REAL_RESULT_TEXT, provider=provider, url=f"https://x/{provider}",
        oa_status="green", content_kind="html", fetched_at=_dt.datetime.now(_dt.UTC),
        chars=len(REAL_RESULT_TEXT),
    )


def _junk_result(provider: str) -> enrich.FetchResult:
    import datetime as _dt
    return enrich.FetchResult(
        text="", provider=provider, url=f"https://x/{provider}", oa_status="unknown",
        content_kind="html", fetched_at=_dt.datetime.now(_dt.UTC), chars=0,
        junk_reason="login wall",
    )


class TestEnrichHitOrdering:
    def test_first_provider_wins(self) -> None:
        p1 = _StubProvider("p1", handles=True, result=_good_result("p1"))
        p2 = _StubProvider("p2", handles=True, result=_good_result("p2"))
        result = enrich.enrich_hit(_hit(), providers=[p1, p2])
        assert result.provider == "p1"
        assert p2.fetch_calls == 0  # never tried — first provider already won

    def test_decline_falls_through_to_next(self) -> None:
        p1 = _StubProvider("p1", handles=False, result=None)
        p2 = _StubProvider("p2", handles=True, result=_good_result("p2"))
        result = enrich.enrich_hit(_hit(), providers=[p1, p2])
        assert result.provider == "p2"

    def test_junk_result_falls_through_to_next(self) -> None:
        p1 = _StubProvider("p1", handles=True, result=_junk_result("p1"))
        p2 = _StubProvider("p2", handles=True, result=_good_result("p2"))
        result = enrich.enrich_hit(_hit(), providers=[p1, p2])
        assert result.provider == "p2"

    def test_all_decline_returns_none_degrade_to_abstract(self) -> None:
        p1 = _StubProvider("p1", handles=False, result=None)
        p2 = _StubProvider("p2", handles=True, result=_junk_result("p2"))
        result = enrich.enrich_hit(_hit(), providers=[p1, p2])
        assert result is None


class TestEnrichHitCache:
    def test_cache_write_then_read_skips_refetch(self, tmp_path: Path) -> None:
        p1 = _StubProvider("p1", handles=True, result=_good_result("p1"))
        cache_dir = tmp_path / ".fulltext"
        hit = _hit(external_ids={"doi": "10.1/cache-test"})

        first = enrich.enrich_hit(hit, providers=[p1], cache_dir=cache_dir)
        assert first is not None and first.provider == "p1"
        assert p1.fetch_calls == 1

        second = enrich.enrich_hit(hit, providers=[p1], cache_dir=cache_dir)
        assert second is not None and second.text == REAL_RESULT_TEXT
        assert p1.fetch_calls == 1  # NOT re-fetched — cache hit

    def test_different_identity_key_does_not_share_cache(self, tmp_path: Path) -> None:
        p1 = _StubProvider("p1", handles=True, result=_good_result("p1"))
        cache_dir = tmp_path / ".fulltext"
        hit_a = _hit(external_ids={"doi": "10.1/a"})
        hit_b = _hit(external_ids={"doi": "10.1/b"})

        enrich.enrich_hit(hit_a, providers=[p1], cache_dir=cache_dir)
        enrich.enrich_hit(hit_b, providers=[p1], cache_dir=cache_dir)
        assert p1.fetch_calls == 2  # distinct identity keys -> distinct cache entries

    def test_cache_junk_result_never_written(self, tmp_path: Path) -> None:
        p1 = _StubProvider("p1", handles=True, result=_junk_result("p1"))
        cache_dir = tmp_path / ".fulltext"
        hit = _hit(external_ids={"doi": "10.1/junk"})

        result = enrich.enrich_hit(hit, providers=[p1], cache_dir=cache_dir)
        assert result is None
        assert not cache_dir.exists() or list(cache_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# 9. Real-network probe (behind -m live, mirrors the design doc's item 8)
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestLiveProbe:
    def test_arxiv_pdf_real_fetch(self) -> None:
        provider = enrich.ArxivPDFProvider()
        result = provider.fetch(_hit(external_ids={"arxiv": "1706.03762"}))
        assert result is not None
        assert result.junk_reason is None
        assert len(result.text) > 1000
