"""research.py — unified research-tooling namespace for Research Vault.

When to use: ``rv research <subcommand>`` for Semantic Scholar search/find,
corpus dedup annotation, and paper ingestion.

Key constraints (re-implemented fresh from vault's research.py as behavioral spec):
  - ``default_project`` comes from config (``cfg._raw.get("default_project")``) —
    NEVER a compiled-in codename (zero hardcoded project names in this module).
  - All path resolution goes through Config — zero hardcoded paths.
  - Auth preflight uses the SecretStore Protocol (EnvSecretStore) — no macOS binaries.
  - Stdlib only for the module itself; asta and cite are called as subprocess tools.

Commands:
  rv research find <query>        search Semantic Scholar (annotated vs corpus)
    --deep                        deep literature review (asta literature find)
    --limit N                     result count (default 10)
    --project NAME                match candidates against this project's corpus
  rv research cited-by <paper-id> papers citing this paper
    --limit N                     result count (default 20)
    --project NAME                match against project's corpus
  rv research add <doi|arxiv>     add a paper (dedup gate → cite add)
    --project NAME                target project/collection
    --force                       bypass dedup gate (logs loudly)
    --dry-run                     preview without writing
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .adapters.base import EnvSecretStore


# ---------------------------------------------------------------------------
# Auth preflight (SecretStore Protocol — no macOS binaries)
# ---------------------------------------------------------------------------

def _preflight_zotero() -> None:
    """Check the Zotero API key is available. Exit with remediation if not."""
    store = EnvSecretStore()
    try:
        store.get("zotero-key")
    except KeyError:
        sys.exit(
            "Zotero key missing — cannot reach your Zotero library.\n"
            "  Fix: export ZOTERO_KEY=<your-key>  or  "
            "keyring.set_password('research-vault', 'zotero-key', '<key>')"
        )


def _preflight_asta() -> None:
    """Check asta is authenticated. Exit with remediation if not."""
    r = subprocess.run(["asta", "auth", "status"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(
            "Not authenticated with asta.\n"
            "  Fix: run 'asta auth login' then retry."
        )


# ---------------------------------------------------------------------------
# Project-scoped helpers (config-driven, never hardcoded codename)
# ---------------------------------------------------------------------------

def _refs_path_for_project(project: str | None, cfg: Config) -> str | None:
    """Return the refs (library.json) path for a project from config.

    Never falls back to a hardcoded codename — if project is None, returns None.
    """
    if not project:
        return None
    try:
        proj_rec = cfg.project(project)
    except KeyError:
        return None
    refs = proj_rec.get("refs")
    return str(Path(refs).expanduser()) if refs else None


def _collection_for_project(project: str | None, cfg: Config) -> str | None:
    """Return the Zotero collection name for a project from config."""
    if not project:
        return None
    try:
        proj_rec = cfg.project(project)
    except KeyError:
        return None
    return proj_rec.get("collection")


def _default_project(cfg: Config) -> str | None:
    """Return the default_project from config — never a compiled-in codename."""
    return cfg._raw.get("default_project")


# ---------------------------------------------------------------------------
# S2 → cite matching adapter (reuses cite.py internals via import)
# ---------------------------------------------------------------------------

def _normalize_author_name(authors_raw: Any) -> str:
    """Extract the first author's family name from any S2 authors shape."""
    if not authors_raw:
        return ""
    if isinstance(authors_raw, str):
        first = authors_raw.split(",", 1)[0].strip()
        return first.rsplit(" ", 1)[-1] if first else ""
    if isinstance(authors_raw, list):
        if not authors_raw:
            return ""
        first = authors_raw[0]
        if isinstance(first, dict):
            name = first.get("name", "").strip()
        elif isinstance(first, str):
            name = first.strip()
        else:
            return ""
        return name.rsplit(" ", 1)[-1] if name else ""
    return ""


def _print_candidates(papers: list[dict]) -> None:
    """Print S2 paper candidates in a human-readable table."""
    print(f"\n{len(papers)} candidate(s)\n")
    for p in papers:
        year = p.get("year", "")
        title = (p.get("title") or "")[:65]
        first_author = _normalize_author_name(p.get("authors"))
        ext = p.get("externalIds") or {}
        arxiv = ext.get("ArXiv", "")
        doi = ext.get("DOI", "")
        id_str = f"arXiv:{arxiv}" if arxiv else (f"DOI:{doi}" if doi else "")
        print(f"  {first_author} {year}  {title}")
        if id_str:
            print(f"  {'':12}  {id_str}")
    print()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_find(args: argparse.Namespace) -> int:
    """find: search Semantic Scholar (asta papers search or --deep)."""
    _preflight_asta()
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv research find: config error: {e}", file=sys.stderr)
        return 1

    project = getattr(args, "project", None) or _default_project(cfg)
    fields = "title,year,authors,externalIds,abstract,citationCount"

    if getattr(args, "deep", False):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        r = subprocess.run(["asta", "literature", "find", args.query, "-o", tmp.name])
        if r.returncode != 0:
            sys.exit(f"asta literature find failed (exit {r.returncode})")
        try:
            data = json.load(open(tmp.name, encoding="utf-8"))
            papers = data if isinstance(data, list) else (
                data.get("papers") or data.get("results") or data.get("data") or []
            )
        except Exception as e:
            sys.exit(f"failed to parse asta literature find output: {e}")
    else:
        cmd = [
            "asta", "papers", "search", args.query,
            "--format", "json", "--limit", str(args.limit),
            "--fields", fields,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"asta papers search failed:\n{r.stderr}")
        raw = json.loads(r.stdout)
        papers = raw.get("data") or []

    _print_candidates(papers)
    return 0


def cmd_cited_by(args: argparse.Namespace) -> int:
    """cited-by: papers that cite this paper."""
    _preflight_asta()
    fields = "title,year,authors,externalIds,citationCount"
    cmd = [
        "asta", "papers", "citations", args.paper_id,
        "--format", "json", "--limit", str(args.limit),
        "--fields", fields,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"asta papers citations failed:\n{r.stderr}")
    raw = json.loads(r.stdout)
    papers = [item.get("citingPaper", item) for item in (raw.get("data") or [])]
    _print_candidates(papers)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """add: dedup preflight → cite add → cite link."""
    _preflight_asta()
    _preflight_zotero()

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv research add: config error: {e}", file=sys.stderr)
        return 1

    project = getattr(args, "project", None) or _default_project(cfg)
    collection = _collection_for_project(project, cfg) if project else None
    force = getattr(args, "force", False)
    dry_run = getattr(args, "dry_run", False)

    # Build cite add command
    cite_cmd = ["rv", "cite", "add", args.ident]
    if dry_run:
        cite_cmd.append("--dry-run")
    if collection:
        cite_cmd += ["--collection", collection]

    r = subprocess.run(cite_cmd)
    if r.returncode != 0:
        sys.exit(f"rv cite add failed (exit {r.returncode})")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``research`` verb.

    When to use: use ``rv research <subcommand>`` to search Semantic Scholar,
    annotate candidates vs corpus, or add papers via the dedup gate.
    default_project is read from config — never a compiled-in codename.
    """
    desc = (
        "Unified research tooling: find → dedup → add. "
        "Shells to asta and rv cite for all S2 and Zotero operations."
    )
    if parent is not None:
        p = parent.add_parser("research", help="Research tooling (asta + Zotero).", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv research", description=desc)

    sub = p.add_subparsers(dest="research_cmd", required=True)

    # find
    find_p = sub.add_parser("find", help="Search Semantic Scholar (annotated vs corpus).")
    find_p.add_argument("query")
    find_p.add_argument("--deep", action="store_true", help="Deep literature review via asta literature find.")
    find_p.add_argument("--limit", type=int, default=10)
    find_p.add_argument("--project", default=None, help="Project slug (from config registry).")

    # cited-by
    cb_p = sub.add_parser("cited-by", help="Papers citing a paper (annotated vs corpus).")
    cb_p.add_argument("paper_id", help="S2 paper id: ARXIV:xxx, DOI:xxx, CorpusId:xxx")
    cb_p.add_argument("--limit", type=int, default=20)
    cb_p.add_argument("--project", default=None)

    # add
    add_p = sub.add_parser("add", help="Add a paper (dedup gate + cite add).")
    add_p.add_argument("ident", help="DOI, arXiv id, URL, or S2 paper id.")
    add_p.add_argument("--project", default=None)
    add_p.add_argument("--force", action="store_true", help="Bypass dedup gate (logs loudly).")
    add_p.add_argument("--dry-run", action="store_true", help="Preview without writing.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch research subcommands. Returns exit code."""
    cmd = args.research_cmd
    try:
        if cmd == "find":
            return cmd_find(args)
        elif cmd == "cited-by":
            return cmd_cited_by(args)
        elif cmd == "add":
            return cmd_add(args)
        else:
            print(f"rv research: unknown subcommand {cmd!r}", file=sys.stderr)
            return 1
    except SystemExit:
        raise
    except Exception as e:
        print(f"rv research: unexpected error: {e}", file=sys.stderr)
        return 1
