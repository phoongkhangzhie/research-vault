"""cross_project.py — cross-project discovery and corroboration for Research Vault.

When to use:
  - ``corroborate_across_projects`` — search another project's OKF notes for
    evidence matching a claim (free cross-project reads, no gate).
  - ``list_projects`` — enumerate all registered projects (slug, code, roster,
    source_dir) as structured records; the discovery substrate for agents.

Design:
  Everything in research-vault is public by construction. There is NO
  intra-vault disclosure boundary. The only confidentiality membrane is the
  ~/vault → research-vault boundary, enforced by the SR-4 leakage scanner
  (which stays UNTOUCHED). Cross-project reads here are plain filesystem reads
  within the public framework.

Stdlib only — no third-party deps.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from .config import Config


# ---------------------------------------------------------------------------
# Project listing (discovery substrate)
# ---------------------------------------------------------------------------

def list_projects(cfg: Config) -> list[dict[str, Any]]:
    """Return all registered projects as structured records.

    Each record has:
        slug (str)        — the registry key
        code (str)        — short identifier
        source_dir (str)  — absolute path to the project's source directory
        roster (list[str]) — registered roles

    No ``disclosure`` field — that concept was reversed (D9).
    """
    result = []
    for slug in cfg.all_project_slugs():
        proj = cfg.projects[slug]
        result.append({
            "slug": slug,
            "code": proj.get("code", ""),
            "source_dir": proj.get("source_dir", ""),
            "roster": list(proj.get("roster", [])),
        })
    return result


# ---------------------------------------------------------------------------
# Cross-project corroboration
# ---------------------------------------------------------------------------

def corroborate_across_projects(
    claim: str,
    cfg: Config,
    from_slug: str | None = None,
    against_slugs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Search peer projects' OKF notes for findings that match a claim.

    Performs a free cross-project read — no gate, no disclosure scoping.
    Everything inside research-vault is public by construction.

    Parameters
    ----------
    claim:
        The claim or query string to corroborate. Matched as case-insensitive
        substring against each note's text content.
    cfg:
        Loaded Config (registry + path source of truth).
    from_slug:
        The originating project slug (excluded from the corroboration search
        so we don't find the claim in its own notes). If None, all projects
        are searched.
    against_slugs:
        Explicit list of project slugs to search. If None, all registered
        projects except ``from_slug`` are searched.

    Returns
    -------
    list of hit dicts, each with:
        project (str)      — slug of the project containing the match
        note_path (str)    — absolute path to the matching note
        note_rel (str)     — path relative to source_dir
        excerpt (str)      — first matching line (up to 120 chars)
        provenance (str)   — cross-project reference string ``@slug:rel_path``
    """
    claim_lower = claim.lower()
    if not claim_lower:
        return []

    # Determine the search universe
    all_slugs = cfg.all_project_slugs()
    if against_slugs is not None:
        search_slugs = [s for s in against_slugs if s in all_slugs]
    else:
        search_slugs = [s for s in all_slugs if s != from_slug]

    hits: list[dict[str, Any]] = []

    for slug in search_slugs:
        proj = cfg.projects[slug]
        source_dir_str = proj.get("source_dir", "")
        if not source_dir_str:
            continue
        source_dir = Path(source_dir_str)
        if not source_dir.exists():
            continue

        # Scan all .md files in the project's source directory
        for note_path in sorted(source_dir.rglob("*.md")):
            try:
                text = note_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            if claim_lower in text.lower():
                # Find the first matching line for the excerpt
                excerpt = ""
                for line in text.splitlines():
                    if claim_lower in line.lower():
                        excerpt = line.strip()[:120]
                        break

                try:
                    note_rel = str(note_path.relative_to(source_dir))
                except ValueError:
                    note_rel = str(note_path)

                hits.append({
                    "project": slug,
                    "note_path": str(note_path),
                    "note_rel": note_rel,
                    "excerpt": excerpt,
                    "provenance": f"@{slug}:{note_rel}",
                })

    return hits
