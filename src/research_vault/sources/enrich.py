# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/enrich.py — OA-first full-text enrichment (tier 1, design
2026-07-08-oa-fulltext-enrichment.md).

Full text is a SECOND-STAGE enrichment on a ``PaperHit`` (abstract -> full
body), kept separate from the sweep so selection stays fast/cheap and cost
stays bounded to the papers actually read (of the design doc — this is
read-time enrichment, called per-paper at the relate boundary, never at
sweep time).

``FetchProvider`` mirrors a generic web-fetch provider pattern, minus
authentication — tier 2 (authenticated paywall crawl) is explicitly OUT of
scope; this module only designs the socket a future ``AuthedCrawlProvider``
would plug into.

Provider ordering is stdlib-first — PMC (JATS XML, stdlib
``xml.etree``) and much of Unpaywall/OpenAlex (HTML landing pages) need no
PDF parser; ``pymupdf`` (core dep, see LICENSE/pyproject — the relicense
prerequisite) is the LAST resort, not the hot path.

Full text is NOT a PaperHit field (too large — 100 KB-1 MB per paper, would
bloat every dedup/rank/serialize path). It lives in the file cache
(``notes/literature/.fulltext/<identity-sha>.{txt,json}``, gitignored) and,
once read, the note's frontmatter provenance (read_basis/
full_text_provider/oa_status/full_text_url).
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .base import PaperHit
from .dedup import identity_key

_USER_AGENT = "research-vault-oa-fetch/1.0"


@dataclass
class FetchResult:
    """A materialized full-text body + its provenance (tier 1 is the
    CLEAN tier: every provider here is re-fetchable by any third party from
    ``url`` alone, no auth, no subscription)."""

    text: str
    provider: str            # "pmc" | "s2-oa" | "unpaywall" | "openalex-oa" | "arxiv-pdf"
    url: str                 # the exact OA URL fetched (re-fetch key)
    oa_status: str           # gold|green|hybrid|bronze|unknown
    content_kind: str        # "xml" | "html" | "pdf"
    fetched_at: datetime
    chars: int
    junk_reason: str | None = None  # set (text discarded) if the junk/login-wall screen failed


@runtime_checkable
class FetchProvider(Protocol):
    name: str

    def can_handle(self, hit: PaperHit) -> bool: ...
    def fetch(self, hit: PaperHit) -> FetchResult | None: ...


# ---------------------------------------------------------------------------
# Shared network primitives — separated from parsing so tests can monkeypatch
# just these (mirrors arxiv.py's _fetch_atom / openalex.py's _fetch_json).
# ---------------------------------------------------------------------------

def _http_get_bytes(url: str, *, timeout: int = 20) -> tuple[bytes, str]:
    """GET *url*, returning (raw bytes, content-type header). Separated for
    test monkeypatching — never called directly by a provider's ``fetch``."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed http(s) scheme)
        content_type = resp.headers.get("content-type", "") or ""
        return resp.read(), content_type


def _http_get_json(url: str, *, timeout: int = 20) -> dict[str, Any]:
    raw, _ = _http_get_bytes(url, timeout=timeout)
    return json.loads(raw.decode("utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# Shared junk / login-wall screen — ported from HR's WebResult.looks_like_junk
# / looks_like_login_wall (web/base.py), MINUS the authenticated-crawl parts.
# Every provider's output passes through this before becoming a FetchResult
# with text — a login-wall or bot-check page means "not actually OA" for
# tier 1: decline that provider, fall through, record oa_status: closed if
# all decline.
# ---------------------------------------------------------------------------

_BOT_SIGNALS = (
    "just a moment", "checking your browser", "ray id", "cloudflare",
    "please wait while we verify", "unusual activity", "captcha",
    "recaptcha", "verify you are human", "verify you are not a robot",
    "please complete the security check", "access denied",
    "enable javascript and cookies", "browser check",
    "ddos protection", "attention required",
)
_ERROR_SIGNALS = (
    "404 not found", "page not found", "403 forbidden",
    "500 internal server error", "502 bad gateway",
    "an error occurred", "this page isn't available",
    "the page you requested", "sorry, we couldn't find",
)
_LOGIN_SIGNALS = (
    "sign in", "sign up", "log in", "login", "create account",
    "authenticate", "register", "sso", "verify your identity",
)
_AUTH_PATHS = ("/login", "/signin", "/signup", "/auth", "/sso", "/register")
_PDF_BINARY_SIGNALS = ("endstream", "endobj", "/flatedecode", "%pdf-")


def screen_fetch(text: str, *, url: str = "") -> str | None:
    """Return a junk_reason string if *text* looks like junk / a login-wall /
    a bot-check page / binary garbage, else ``None``. Tier-1 semantics: any
    of these means "not actually OA" for THIS provider — the caller declines
    and falls through to the next provider (never treats it as real text)."""
    stripped = text.strip()
    if len(stripped) < 300:
        return "Empty or near-empty content"
    sample = text[:2000]
    lower = sample.lower()

    if any(s in lower for s in _BOT_SIGNALS):
        return "Bot detection / CAPTCHA page"
    if any(s in lower for s in _ERROR_SIGNALS):
        return "Error page"
    if len(stripped) < 1000 and any(s in lower for s in _LOGIN_SIGNALS):
        return "Login/signup wall"

    path = urllib.parse.urlparse(url).path.lower()
    if any(p in path for p in _AUTH_PATHS):
        return "URL redirected to an auth path"

    if any(m in lower for m in _PDF_BINARY_SIGNALS):
        return "Binary PDF garbage in content (extraction failed)"

    return None


# ---------------------------------------------------------------------------
# Text extraction — PDF (pymupdf, core dep) / HTML (stdlib) / raw XML (the
# JATS-specific parse lives in the PMC provider below, closer to its schema).
# ---------------------------------------------------------------------------

def _pdf_bytes_to_text(pdf_bytes: bytes) -> str:
    """Extract plain text from PDF bytes via pymupdf (core dep,  the
    relicense prerequisite). Last-resort text path (stdlib-first
    ordering); most tier-1 fetches never reach here."""
    import pymupdf  # local import: keeps the module importable even if the
    # dependency graph shifts later; pymupdf itself is core as of 0.3.0.

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages = [page.get_text("text") for page in doc]
    finally:
        doc.close()
    return "\n".join(p for p in pages if p.strip())


class _TextOnlyHTMLParser(HTMLParser):
    """Minimal stdlib HTML->text: strips tags/script/style, keeps prose."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "nav", "header", "footer"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "nav", "header", "footer") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data.strip())

    def text(self) -> str:
        return "\n".join(self._chunks)


def _html_to_text(html: str) -> str:
    parser = _TextOnlyHTMLParser()
    parser.feed(html)
    return re.sub(r"\n{3,}", "\n\n", parser.text().strip())


def _sniff_content_kind(url: str, content_type: str, raw: bytes) -> str:
    ct = content_type.lower()
    if "pdf" in ct or url.lower().split("?", 1)[0].endswith(".pdf"):
        return "pdf"
    if "xml" in ct or raw.lstrip()[:5] == b"<?xml":
        return "xml"
    return "html"


def _extract_from_bytes(raw: bytes, content_kind: str) -> str:
    if content_kind == "pdf":
        return _pdf_bytes_to_text(raw)
    text = raw.decode("utf-8", errors="replace")
    if content_kind == "xml":
        return text  # caller does its own schema-specific parse (e.g. JATS)
    return _html_to_text(text)


def _fetch_generic(provider_name: str, url: str, *, oa_status: str) -> FetchResult | None:
    """Shared "GET url, sniff kind, extract text, screen for junk" path used
    by every provider except PMC (which has its own dedicated JATS endpoint)."""
    try:
        raw, content_type = _http_get_bytes(url)
    except Exception:
        return None
    kind = _sniff_content_kind(url, content_type, raw)
    try:
        text = _extract_from_bytes(raw, kind)
    except Exception:
        return None
    if not text.strip():
        return None
    reason = screen_fetch(text, url=url)
    if reason:
        return FetchResult(
            text="", provider=provider_name, url=url, oa_status=oa_status,
            content_kind=kind, fetched_at=datetime.now(UTC), chars=0, junk_reason=reason,
        )
    return FetchResult(
        text=text, provider=provider_name, url=url, oa_status=oa_status,
        content_kind=kind, fetched_at=datetime.now(UTC), chars=len(text),
    )


# ---------------------------------------------------------------------------
# Provider 1 — PMC (PMID/PMCID -> EuropePMC OA full-text JATS XML). No PDF
# dep — stdlib xml.etree, exactly like arxiv.py's Atom parse (#1).
# ---------------------------------------------------------------------------

_EUROPEPMC_FULLTEXT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"


def _fetch_pmc_xml(pmcid: str) -> str:
    raw, _ = _http_get_bytes(_EUROPEPMC_FULLTEXT.format(pmcid=pmcid))
    return raw.decode("utf-8", errors="replace")


def _jats_xml_to_text(xml_text: str) -> str:
    """Extract the article body's text content from a JATS XML document.
    Missing/malformed <body> -> empty string (caller treats as decline, not
    a crash — charter §2 surface-don't-drop via the FetchResult chain, not
    an exception escaping to the fan-out)."""
    root = ET.fromstring(xml_text)  # noqa: S314 (trusted EuropePMC response)
    body = root.find(".//body")
    if body is None:
        return ""
    return re.sub(r"\n{3,}", "\n\n", "".join(body.itertext())).strip()


class PMCProvider:
    """PMID/PMCID -> EuropePMC OA full-text JATS XML."""

    name = "pmc"

    def can_handle(self, hit: PaperHit) -> bool:
        return bool(hit.external_ids.get("pmcid"))

    def fetch(self, hit: PaperHit) -> FetchResult | None:
        pmcid = hit.external_ids.get("pmcid")
        if not pmcid:
            return None
        url = _EUROPEPMC_FULLTEXT.format(pmcid=pmcid)
        try:
            xml_text = _fetch_pmc_xml(pmcid)
        except Exception:
            return None
        try:
            text = _jats_xml_to_text(xml_text)
        except ET.ParseError:
            return None
        if not text:
            return None
        reason = screen_fetch(text, url=url)
        if reason:
            return FetchResult(
                text="", provider=self.name, url=url, oa_status="green",
                content_kind="xml", fetched_at=datetime.now(UTC), chars=0, junk_reason=reason,
            )
        return FetchResult(
            text=text, provider=self.name, url=url, oa_status="green",
            content_kind="xml", fetched_at=datetime.now(UTC), chars=len(text),
        )


# ---------------------------------------------------------------------------
# Provider 2 — S2 openAccessPdf (captured at search time — sources/
# semantic_scholar.py). Usually PDF; sometimes an HTML landing page.
# ---------------------------------------------------------------------------

class S2OAProvider:
    name = "s2-oa"

    def can_handle(self, hit: PaperHit) -> bool:
        return bool(hit.oa_url) and hit.oa_source == "semantic-scholar"

    def fetch(self, hit: PaperHit) -> FetchResult | None:
        if not hit.oa_url:
            return None
        return _fetch_generic(self.name, hit.oa_url, oa_status=hit.oa_status or "unknown")


# ---------------------------------------------------------------------------
# Provider 3 — Unpaywall (DOI -> best_oa_location). Requires a contact email
# (their API terms — config, not a credential, #3); absent -> self-skip.
# ---------------------------------------------------------------------------

_UNPAYWALL_API = "https://api.unpaywall.org/v2/{doi}"


def _fetch_unpaywall(doi: str, email: str) -> dict[str, Any]:
    params = urllib.parse.urlencode({"email": email})
    url = f"{_UNPAYWALL_API.format(doi=urllib.parse.quote(doi, safe=''))}?{params}"
    return _http_get_json(url)


class UnpaywallProvider:
    name = "unpaywall"

    def __init__(self, email: str = ""):
        self.email = email

    def can_handle(self, hit: PaperHit) -> bool:
        return bool(self.email) and bool(hit.external_ids.get("doi"))

    def fetch(self, hit: PaperHit) -> FetchResult | None:
        doi = hit.external_ids.get("doi")
        if not doi or not self.email:
            return None
        try:
            data = _fetch_unpaywall(doi, self.email)
        except Exception:
            return None
        best = data.get("best_oa_location") or {}
        url = best.get("url_for_pdf") or best.get("url")
        if not url:
            return None
        oa_status = data.get("oa_status") or "unknown"
        return _fetch_generic(self.name, url, oa_status=oa_status)


# ---------------------------------------------------------------------------
# Provider 4 — OpenAlex OA (already in hit.raw for OpenAlex hits — zero
# extra request; captured into hit.oa_url/oa_status by openalex.py).
# ---------------------------------------------------------------------------

class OpenAlexOAProvider:
    name = "openalex-oa"

    def can_handle(self, hit: PaperHit) -> bool:
        return hit.source == "openalex" and bool(hit.oa_url)

    def fetch(self, hit: PaperHit) -> FetchResult | None:
        if not hit.oa_url:
            return None
        return _fetch_generic(self.name, hit.oa_url, oa_status=hit.oa_status or "unknown")


# ---------------------------------------------------------------------------
# Provider 5 — arXiv PDF (last resort; derived from external_ids["arxiv"]).
# ---------------------------------------------------------------------------

class ArxivPDFProvider:
    name = "arxiv-pdf"

    def can_handle(self, hit: PaperHit) -> bool:
        return bool(hit.external_ids.get("arxiv"))

    def fetch(self, hit: PaperHit) -> FetchResult | None:
        arxiv_id = hit.external_ids.get("arxiv")
        if not arxiv_id:
            return None
        url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        return _fetch_generic(self.name, url, oa_status="green")


# ---------------------------------------------------------------------------
# Ordered registry (stdlib-first: PDF is the last resort, not the hot
# path). Appending a future tier-2 AuthedCrawlProvider here is the entire
# integration (the seam accommodates it without rework).
# ---------------------------------------------------------------------------

def default_fetch_providers(*, unpaywall_email: str = "") -> list[FetchProvider]:
    return [
        PMCProvider(),
        S2OAProvider(),
        UnpaywallProvider(unpaywall_email),
        OpenAlexOAProvider(),
        ArxivPDFProvider(),
    ]


def providers_from_config(cfg: Any) -> list[FetchProvider]:
    """Build the ordered provider registry from a ``Config``'s ``[fulltext]``
    block. ``unpaywall_email`` absent -> UnpaywallProvider.can_handle always
    False (self-skip), never a crash — surfaced by the caller's run log, not
    silently."""
    email = ""
    if cfg is not None:
        email = (getattr(cfg, "fulltext", {}) or {}).get("unpaywall_email", "") or ""
    return default_fetch_providers(unpaywall_email=email)


# ---------------------------------------------------------------------------
# File cache — notes/literature/.fulltext/<identity-sha>.{txt,json},
# gitignored, identity-keyed (reuses dedup.identity_key, reuse). Disposable:
# deleting the dir costs only re-fetch time, never provenance.
# ---------------------------------------------------------------------------

def _cache_paths(cache_dir: Path, hit: PaperHit) -> tuple[Path, Path]:
    sha = hashlib.sha256(identity_key(hit).encode("utf-8")).hexdigest()
    return cache_dir / f"{sha}.txt", cache_dir / f"{sha}.json"


def _read_cache(cache_dir: Path, hit: PaperHit) -> FetchResult | None:
    text_path, meta_path = _cache_paths(cache_dir, hit)
    if not text_path.is_file() or not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        text = text_path.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError, KeyError):
        return None
    try:
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
    except (KeyError, ValueError):
        return None
    return FetchResult(
        text=text,
        provider=meta.get("provider", ""),
        url=meta.get("url", ""),
        oa_status=meta.get("oa_status", "unknown"),
        content_kind=meta.get("content_kind", ""),
        fetched_at=fetched_at,
        chars=meta.get("chars", len(text)),
    )


def _write_cache(cache_dir: Path, hit: PaperHit, result: FetchResult) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    text_path, meta_path = _cache_paths(cache_dir, hit)
    text_path.write_text(result.text, encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "provider": result.provider,
                "url": result.url,
                "oa_status": result.oa_status,
                "content_kind": result.content_kind,
                "fetched_at": result.fetched_at.isoformat(),
                "chars": result.chars,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def enrich_hit(
    hit: PaperHit,
    *,
    providers: list[FetchProvider] | None = None,
    cache_dir: Path | None = None,
) -> FetchResult | None:
    """Materialize OA full text for *hit*, trying *providers* in order
    (default: :func:`default_fetch_providers`).

    First non-``None``, non-junk result wins — the rest are the fallback
    chain. All decline / all junk -> ``None`` (caller degrades to abstract,
    exactly today's behavior, no regression).

    Cache-backed when *cache_dir* is given: a hit that resolves to a cache
    entry (identity-keyed) is returned WITHOUT re-fetching or re-trying
    providers.
    """
    if cache_dir is not None:
        cached = _read_cache(cache_dir, hit)
        if cached is not None:
            return cached

    for provider in providers if providers is not None else default_fetch_providers():
        if not provider.can_handle(hit):
            continue
        result = provider.fetch(hit)
        if result is None or result.junk_reason:
            continue  # declined or junk — try the next provider
        if cache_dir is not None:
            _write_cache(cache_dir, hit, result)
        return result
    return None
