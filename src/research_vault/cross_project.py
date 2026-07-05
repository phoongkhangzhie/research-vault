"""cross_project.py — cross-project discovery and corroboration for Research Vault.

When to use:
  - ``corroborate_across_projects`` — search DECLARED peer projects' OKF notes for
    evidence matching a claim (SR-XPB: gated to hub-declared edges).
  - ``list_projects`` — enumerate all registered projects (slug, code, roster,
    source_dir) as structured records; the discovery substrate for agents.
  - ``rank_candidates`` — score and sort corroboration candidates by relevance
    (TF-IDF cosine; stdlib Jaccard fallback if sklearn absent).

Design (SR-XPB Wren D1–D5):
  D1: Sidecar JSON edge store (project_edges.py) backs the reach-permission gate.
  D2: Undirected edges with required ``kind``.
  D3: ``corroborate`` requires ``from_slug``; ``against`` ⊆ peers.
  D4: Judge-gated assert — rank narrows, judge confirms, human reviews.
  D5: Hub declares edges (rv project relate); crew reads peers_of.

  Everything in research-vault is public by construction. There is NO
  intra-vault disclosure boundary. The only confidentiality membrane is the
  ~/vault → research-vault boundary, enforced by the SR-4 leakage scanner
  (which stays UNTOUCHED). Cross-project reads here are plain filesystem reads
  within the public framework.

Stdlib only — no third-party deps (TF-IDF is a lazy-guarded optional).
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
# Anchor extraction
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def _extract_anchor(text: str, match_start: int) -> str:
    """Return the nearest preceding markdown heading at or before ``match_start``.

    Falls back to ``line-N`` (1-indexed) if no heading precedes the match.
    """
    best_heading = ""
    best_pos = -1
    for m in _HEADING_RE.finditer(text):
        if m.start() <= match_start:
            best_heading = m.group(2).strip()
            best_pos = m.start()
        else:
            break  # text is scanned in order; first heading past position → done
    if best_heading:
        return best_heading
    # Fallback: line number
    line_num = text[:match_start].count("\n") + 1
    return f"line-{line_num}"


# ---------------------------------------------------------------------------
# Relevance ranker (Slice 4 — TF-IDF cosine; Jaccard fallback)
# ---------------------------------------------------------------------------

def _jaccard(query: str, doc: str) -> float:
    """Stdlib-only token-overlap (Jaccard) similarity between two strings."""
    q_tokens = set(query.lower().split())
    d_tokens = set(doc.lower().split())
    if not q_tokens or not d_tokens:
        return 0.0
    intersection = q_tokens & d_tokens
    union = q_tokens | d_tokens
    return len(intersection) / len(union)


def rank_candidates(
    claim: str,
    candidates: list[dict[str, Any]],
    *,
    min_score: float = 0.05,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Score and sort corroboration candidates by relevance to ``claim``.

    Scoring strategy:
      1. TF-IDF cosine similarity (``sklearn`` lazy-imported from Tier-1 extras).
      2. If ``sklearn`` is unavailable, falls back to Jaccard token-overlap and
         prints a notice — surfaces the degradation, never silently degrades.

    Parameters
    ----------
    claim:
        The claim string used as the query.
    candidates:
        List of hit dicts from ``corroborate_across_projects`` (must have
        ``body`` key populated — callers are responsible).
    min_score:
        Minimum similarity score threshold (default 0.05).
    top_k:
        Maximum number of candidates to return (default 10).

    Returns
    -------
    Candidates filtered by ``min_score``, sorted descending by score,
    truncated to ``top_k``.  Each dict gains a ``score`` key.
    """
    if not candidates:
        return []

    bodies = [c.get("body", c.get("excerpt", "")) for c in candidates]
    docs = [claim] + bodies  # index 0 = query

    scored: list[dict[str, Any]] = []

    # Try TF-IDF (sklearn Tier-1 optional)
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import]
        from sklearn.metrics.pairwise import cosine_similarity  # type: ignore[import]

        vec = TfidfVectorizer(min_df=1, stop_words=None)
        tfidf = vec.fit_transform(docs)
        query_vec = tfidf[0]
        sims = cosine_similarity(query_vec, tfidf[1:]).flatten()
        for c, sim in zip(candidates, sims):
            scored.append({**c, "score": float(sim), "ranker": "tfidf"})
    except (ImportError, Exception):
        # Stdlib fallback — Jaccard token overlap
        print(
            "ranker: lexical-fallback (install research-vault[analysis] for tf-idf)",
            file=sys.stderr,
        )
        for c in candidates:
            body = c.get("body", c.get("excerpt", ""))
            score = _jaccard(claim, body)
            scored.append({**c, "score": score, "ranker": "jaccard"})

    # Filter by min_score, sort descending, truncate to top_k
    filtered = [c for c in scored if c["score"] >= min_score]
    filtered.sort(key=lambda c: c["score"], reverse=True)
    return filtered[:top_k]


# ---------------------------------------------------------------------------
# Cross-project corroboration (SR-XPB D3 — gated to declared peers)
# ---------------------------------------------------------------------------

def corroborate_across_projects(
    claim: str,
    cfg: Config,
    from_slug: str | None = None,
    against_slugs: list[str] | None = None,
    *,
    min_score: float = 0.05,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Search declared peer projects' OKF notes for findings that match a claim.

    Gate (SR-XPB D3): ``from_slug`` is REQUIRED.  The default universe is the
    set of declared peers (``peers_of(cfg, from_slug)`` from the edge store),
    NOT all registered projects.  ``against_slugs``, if supplied, must be a
    subset of declared peers — a ValueError is raised otherwise.

    If no edges are declared for ``from_slug``, returns an empty list so that
    ``cmd_corroborate`` can print the discovery nudge.

    Parameters
    ----------
    claim:
        The claim or query string to corroborate.
    cfg:
        Loaded Config (registry + edge store source of truth).
    from_slug:
        REQUIRED.  The originating project slug (excluded from search).
        Raises ValueError if None.
    against_slugs:
        Explicit list of project slugs to search.  Must be ⊆ declared peers
        of ``from_slug``.  Raises ValueError for non-peer slugs.
    min_score:
        Minimum TF-IDF (or Jaccard) relevance score.  Candidates below this
        threshold are dropped (default 0.05).
    top_k:
        Maximum number of ranked candidates to return (default 10).

    Returns
    -------
    list of hit dicts, each with:
        project (str)      — slug of the project containing the match
        note_path (str)    — absolute path to the matching note
        note_rel (str)     — path relative to source_dir
        body (str)         — full note body (used by the ranker)
        excerpt (str)      — first matching line (up to 120 chars)
        anchor (str)       — nearest preceding heading, or ``line-N`` fallback
        provenance (str)   — ``@slug:note_rel:anchor``
        score (float)      — relevance score (TF-IDF cosine or Jaccard)
        ranker (str)       — ``"tfidf"`` or ``"jaccard"``
    """
    from .project_edges import peers_of

    if from_slug is None:
        raise ValueError(
            "corroborate_across_projects: from_slug is REQUIRED (SR-XPB D3). "
            "Pass the originating project slug via --from <slug>."
        )

    claim_lower = claim.lower()
    if not claim_lower:
        return []

    # Declared peers — the allowed universe
    declared_peers = peers_of(cfg, from_slug)

    if against_slugs is not None:
        # Validate: against_slugs must be ⊆ declared peers
        non_peers = [s for s in against_slugs if s not in declared_peers]
        if non_peers:
            raise ValueError(
                f"--against slugs {non_peers!r} are not declared peers of {from_slug!r}. "
                f"Declared peers: {sorted(declared_peers) or '(none)'}. "
                f"Declare an edge first: rv project relate {from_slug} <peer> --kind <why>"
            )
        search_slugs = [s for s in against_slugs if s in cfg.all_project_slugs()]
    else:
        # Default: all declared peers that are registered
        search_slugs = [s for s in declared_peers if s in cfg.all_project_slugs()]

    # No declared peers → return empty (caller prints nudge)
    if not search_slugs:
        return []

    candidates: list[dict[str, Any]] = []

    for slug in sorted(search_slugs):
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

            if claim_lower not in text.lower():
                continue

            # Find the first matching position for anchor extraction
            text_lower = text.lower()
            match_pos = text_lower.find(claim_lower)

            # Excerpt: first matching line (up to 120 chars)
            excerpt = ""
            for line in text.splitlines():
                if claim_lower in line.lower():
                    excerpt = line.strip()[:120]
                    break

            # Anchor: nearest preceding heading or line-N fallback
            anchor = _extract_anchor(text, match_pos)

            try:
                note_rel = str(note_path.relative_to(source_dir))
            except ValueError:
                note_rel = str(note_path)

            candidates.append({
                "project": slug,
                "note_path": str(note_path),
                "note_rel": note_rel,
                "body": text,
                "excerpt": excerpt,
                "anchor": anchor,
                "provenance": f"@{slug}:{note_rel}:{anchor}",
            })

    # Rank candidates by relevance
    return rank_candidates(claim, candidates, min_score=min_score, top_k=top_k)
