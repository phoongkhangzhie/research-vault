"""fulltext.py — read-time OA full-text enrichment CLI (tier 1, 0.3.0).

``rv research fulltext <project> <citekey> [identifiers...]`` is the tool the
relate-<key> subagent (``review/style.py`` ``per_paper_relate_tips``) calls
while reading a paper: given the identifiers the subagent already resolved
during search/discovery, it runs the OA fetch waterfall
(``sources/enrich.py``), caches the result, and — if the paper's literature
note already exists — stamps the read-basis provenance into its frontmatter
(§5 of the design doc). All decline -> prints the abstract-only degrade
message and exits 0 (never an error; this is the expected, honest tier-1
fallback, not a failure).

This is deliberately a THIN CLI wrapper: all the real logic (providers, the
junk/login-wall screen, the ordered fallback, the cache) lives in
``sources/enrich.py`` — this module only builds a ``PaperHit`` from CLI-
supplied identifiers, calls ``enrich_hit``, and handles the two side effects
(cache write — already inside enrich_hit — and note frontmatter stamping).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .config import Config, load_config
from .sources.base import PaperHit
from .sources.enrich import FetchResult, enrich_hit, providers_from_config


def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    desc = (
        "Read-time OA full-text enrichment: fetch + cache the paper's open-"
        "access full text (tier 1 — stdlib-first provider ordering, PDF as "
        "last resort), stamping read_basis provenance into the literature "
        "note's frontmatter when it already exists."
    )
    if parent is not None:
        p = parent.add_parser(
            "fulltext", help="Fetch + cache OA full text for a paper (tier 1).", description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv research fulltext", description=desc)

    p.add_argument("project", help="Project slug (from config registry).")
    p.add_argument("citekey", help="The paper's citekey (matches literature/<citekey>.md if filed).")
    p.add_argument("--title", default="", help="Paper title (used as the fallback identity key).")
    p.add_argument("--doi", default=None, help="DOI (enables the unpaywall provider).")
    p.add_argument("--arxiv", default=None, help="arXiv id (enables the arxiv-pdf provider).")
    p.add_argument("--pmid", default=None, help="PubMed id.")
    p.add_argument("--pmcid", default=None, help="PMC id (enables the pmc provider — full-text XML).")
    p.add_argument("--openalex", default=None, help="OpenAlex work id.")
    p.add_argument(
        "--source", default="", dest="oa_source",
        help="Which adapter surfaced the oa_url below (semantic-scholar|openalex). "
             "Required for --oa-url to be usable by the s2-oa/openalex-oa providers.",
    )
    p.add_argument("--oa-url", default=None, dest="oa_url", help="A previously-captured OA pointer URL.")
    p.add_argument("--oa-status", default=None, dest="oa_status", help="gold|green|hybrid|bronze|unknown.")
    return p


def _hit_from_args(args: argparse.Namespace) -> PaperHit:
    external_ids: dict[str, str] = {}
    if args.doi:
        external_ids["doi"] = args.doi
    if args.arxiv:
        external_ids["arxiv"] = args.arxiv
    if args.pmid:
        external_ids["pmid"] = args.pmid
    if args.pmcid:
        external_ids["pmcid"] = args.pmcid
    if args.openalex:
        external_ids["openalex"] = args.openalex

    return PaperHit(
        title=args.title or args.citekey,
        year=None,
        authors=[],
        external_ids=external_ids,
        abstract="",
        citation_count=0,
        source=args.oa_source or "unknown",
        oa_url=args.oa_url,
        oa_status=args.oa_status,
        oa_source=args.oa_source or None,
    )


def _cache_dir_for(cfg: Config, project: str) -> Path:
    return cfg.project_notes_dir(project) / "literature" / ".fulltext"


def _note_path_for(cfg: Config, project: str, citekey: str) -> Path:
    return cfg.project_notes_dir(project) / "literature" / f"{citekey}.md"


def stamp_note_frontmatter(note_path: Path, fields: dict[str, str]) -> bool:
    """Stamp/replace scalar frontmatter *fields* in *note_path* in place.

    Regex-replaces an existing ``key: value`` line if present; otherwise
    injects a new ``key: value`` line just before the closing ``---``
    delimiter. Mirrors the existing stamp-or-inject convention used
    elsewhere in the codebase (e.g. review's ``status:`` stamp) rather than
    reserializing the whole frontmatter (which would risk corrupting fields
    this module doesn't know about).

    Returns False (no-op) if *note_path* does not exist — the caller treats
    this as "note not filed yet, nothing to stamp" (not an error; the
    subagent will file the note itself with these fields, or call this tool
    again after filing).
    """
    if not note_path.is_file():
        return False
    text = note_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False

    lines = text.split("\n")
    # Locate the closing '---' (second occurrence).
    delim_idxs = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if len(delim_idxs) < 2:
        return False

    for key, value in fields.items():
        pattern = re.compile(rf"^({re.escape(key)}:\s*).*$", re.MULTILINE)
        if pattern.search(text) is not None:
            # Existing field — replace in place (never a string-equality
            # check: the new value can legitimately equal the old one, which
            # would falsely read as "no match" and duplicate-inject).
            text = pattern.sub(lambda m, v=value: f"{m.group(1)}{v}", text, count=1)
        else:
            # Inject before the closing delimiter.
            lines = text.split("\n")
            delim_idxs = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
            close_idx = delim_idxs[1]
            lines.insert(close_idx, f"{key}: {value}")
            text = "\n".join(lines)

    note_path.write_text(text, encoding="utf-8")
    return True


def _provenance_fields(result: FetchResult | None) -> dict[str, str]:
    if result is None:
        return {"read_basis": "abstract-only"}
    return {
        "read_basis": "full-text",
        "full_text_provider": result.provider,
        "oa_status": result.oa_status,
        "full_text_url": result.url,
    }


def cmd_fulltext(args: argparse.Namespace) -> int:
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv research fulltext: config error: {e}", file=sys.stderr)
        return 1

    hit = _hit_from_args(args)
    cache_dir = _cache_dir_for(cfg, args.project)
    providers = providers_from_config(cfg)

    result = enrich_hit(hit, providers=providers, cache_dir=cache_dir)
    prov = _provenance_fields(result)

    note_path = _note_path_for(cfg, args.project, args.citekey)
    stamped = stamp_note_frontmatter(note_path, prov)

    if result is None:
        print(
            f"rv research fulltext: no OA full text found for {args.citekey!r} "
            f"(all providers declined/junk) — degrading to abstract-only read. "
            f"read_basis=abstract-only",
        )
        if stamped:
            print(f"Stamped read_basis: abstract-only into {note_path}")
        return 0

    text_file, _meta_file = _cache_text_and_meta_paths(cache_dir, hit)
    print(json.dumps({
        "citekey": args.citekey,
        "provider": result.provider,
        "url": result.url,
        "oa_status": result.oa_status,
        "content_kind": result.content_kind,
        "chars": result.chars,
        "cached_text_path": str(text_file),
    }, indent=2))
    if stamped:
        print(f"Stamped read_basis/full_text_provider/oa_status/full_text_url into {note_path}")
    else:
        print(
            f"Note {note_path} does not exist yet — file it with `rv note new "
            f"{args.project} literature {args.citekey}` and re-run this command "
            f"(cache hit, no re-fetch) to stamp provenance.",
        )
    return 0


def _cache_text_and_meta_paths(cache_dir: Path, hit: PaperHit) -> tuple[Path, Path]:
    """Re-derive the cache file paths for the printed pointer (§enrich.py's
    ``_cache_paths`` is private — this stays a thin, testable re-derivation
    rather than reaching into enrich.py's internals)."""
    import hashlib
    from .sources.dedup import identity_key

    sha = hashlib.sha256(identity_key(hit).encode("utf-8")).hexdigest()
    return cache_dir / f"{sha}.txt", cache_dir / f"{sha}.json"


def run(args: argparse.Namespace) -> int:
    return cmd_fulltext(args)
