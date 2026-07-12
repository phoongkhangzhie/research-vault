# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/ledger.py: the additive, single-writer ``_corpus_ledger.md``
assembler (the fourth handoff property: LEDGERED).

Introduced 2026-07-10, building on the
handoff-contract properties COMPLETE / CLEAN / CANONICALLY-KEYED /
LEDGERED. Provenance for a completed review is currently scattered across
``_search_hits.md``, ``_walk.md``, ``_coverage-gaps.md``, the
relevance-verify verdict artifact, and ``_corpus.md`` — this module
consolidates it into ONE machine-readable artifact so the manuscript stage
and reader-facing methods sections consume a single, verifiable record.

**Additive by design (L-D1 DECIDED):** this module is a single-writer
ASSEMBLER that only READS the existing durable artifacts already written by
the Q/P/K stages — it never retires or mutates them. Re-running
``write_corpus_ledger`` is idempotent (byte-identical output for an
unchanged source state) and safe to call repeatedly (e.g. after a
backtrack round appends new rows) because every source it reads is
append-only by convention.

Follows the flat-frontmatter doctrine used throughout ``review/*``: flat
scalars in frontmatter (the gate/methods-consumable summary) + structured
detail as markdown body TABLES (parseable the same way ``_corpus.md``/
``_search_hits.md`` already are) — never inline JSON in frontmatter.

Fail-closed: a value with no traceable source is NEVER
guessed. Any source artifact that is missing or malformed for a section
that's expected to exist emits a loud ``> [LEDGER-GAP] <section>: <what
was missing/malformed>`` line in that section AND flips the top-level
``ledger_complete`` scalar to ``false`` — never a silently-partial ledger
that reads as complete.

Stdlib only (+ intra-package imports). (pre-publish #55 blocker)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..note import _parse_frontmatter

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
CITEKEY_CONVENTION = "authorYearWord"


# ---------------------------------------------------------------------------
# Small readers over the existing durable artifacts (never re-implemented —
# each defers to the SAME function the owning stage already uses).
# ---------------------------------------------------------------------------

def _read_text_or_none(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _q_block(protocol_path: Path, walk_path: Path, gaps_path: Path) -> dict[str, Any]:
    """Assemble the Q (search-breadth/walk-coverage) block.

    Sources: ``_protocol.md`` (frozen matrix, via ``corpus_freeze``'s own
    canonicalization helpers — reused, not re-derived), ``_walk.md``
    (via ``review.check_walk_terminal`` — the SAME whitelist-only
    parser the coverage-gate itself reads), ``_coverage-gaps.md`` (agent-
    authored free prose — best-effort verbatim residue, never a fabricated
    structured extraction of "the" open poles since no such schema exists
    on that artifact).
    """
    from .corpus_freeze import hash_query_matrix_bytes
    from ..sources.sweep import parse_angle_matrix
    from . import check_walk_terminal

    gaps: list[str] = []
    protocol_text = _read_text_or_none(protocol_path)
    if protocol_text is None:
        gaps.append("Q: _protocol.md not found — matrix_hash/angles_searched/"
                     "distinct_query_count/matrix_band_ok cannot be computed.")
        matrix_hash = ""
        angles_searched = ""
        distinct_query_count = 0
        matrix_band_ok = False
    else:
        # 0.3.1 tiered-hash split: the ledger's Q block is about the ACTUAL
        # searched query matrix — repointed to the query-TEXT tier
        # (``hash_query_matrix_bytes``), which is what changes across a
        # ``within-facet-query-append`` remediation round. The frozen-tier
        # bright line (``hash_criteria_bytes``) is a DIFFERENT, human-gated
        # hash, not what this audit field describes.
        matrix_hash = hash_query_matrix_bytes(protocol_path)
        angle_matrix = parse_angle_matrix(protocol_text)
        angles_searched = ", ".join(sorted(angle_matrix.keys()))
        # distinct_query_count: POST-DEDUP on the actual query STRING values
        # (parse_angle_matrix already flattens the nested facet form into
        # one key per enumerated query — dedup here catches the same query
        # string reused under two different angle keys).
        distinct_query_count = len({str(v).strip() for v in angle_matrix.values() if str(v).strip()})
        matrix_band_ok = 40 <= distinct_query_count <= 100

    walk_info = check_walk_terminal(walk_path)
    if not walk_info["exists"]:
        gaps.append("Q: _walk.md not found — stop_reason/walk_bounded unknown.")
    stop_reason = walk_info["stop_reason"]
    # True only for a budget-terminated walk — the one surviving residue
    # case (0.3.1: walk-complete:N-hops and neighborhood-exhausted are both
    # clean terminals, never "bounded" in this sense).
    walk_bounded = stop_reason.lower().startswith("budget:")

    open_counter_poles = ""
    gaps_text = _read_text_or_none(gaps_path)
    if gaps_text is not None:
        # Best-effort verbatim bullets — _coverage-gaps.md is agent-authored
        # free prose with no fixed schema (review/style.py's
        # review_curate_tips), so this is NOT a claim of pole-name
        # precision; it is the raw residue the artifact actually declared,
        # comma-joined for the frontmatter scalar. The full text is also
        # carried into the body's "Open coverage residue" section verbatim.
        bullets = [
            m.group(1).strip()
            for m in re.finditer(r"^\s*[-*]\s+(.+?)\s*$", gaps_text, re.MULTILINE)
        ]
        open_counter_poles = "; ".join(bullets)

    return {
        "matrix_hash": matrix_hash,
        "angles_searched": angles_searched,
        "distinct_query_count": distinct_query_count,
        "matrix_band_ok": matrix_band_ok,
        "stop_reason": stop_reason,
        "walk_bounded": walk_bounded,
        "open_counter_poles": open_counter_poles,
        "gaps_text": gaps_text,
        "_gaps": gaps,
    }


def _p_block(relevance_payload: dict[str, Any] | None) -> dict[str, Any]:
    """Assemble the P (relevance) block from ``check_relevance_verifier``'s
    return-shape payload (reused directly — see ``review.relevance``).

    ``relevance_payload is None`` is an honest "this manifest never wired
    review-relevance-verify" no-op (a pre- manifest) — NOT a gap; see
    ``dag/verbs.py``'s own optional-collaborator handling of the same node.
    """
    from .relevance import classify_relevance_verdict, OFF_DOMAIN, UNCERTAIN

    gaps: list[str] = []
    if relevance_payload is None:
        return {
            "relevance_verdict_total": 0,
            "off_domain_count": 0,
            "uncertain_count": 0,
            "off_domain_fraction": 0.0,
            "relevance_disposition": "",
            "relevance_canary_ok": True,
            "pruned_off_domain": 0,
            "_gaps": gaps,
        }

    if not relevance_payload.get("exists", False):
        gaps.append("P: _relevance-verdict.md not found — relevance gate never ran.")

    verdicts: dict[str, str] = relevance_payload.get("verdicts", {})
    total = len(verdicts)
    off_domain_count = sum(1 for v in verdicts.values() if v == OFF_DOMAIN)
    uncertain_count = sum(1 for v in verdicts.values() if v == UNCERTAIN)
    off_domain_fraction = (off_domain_count / total) if total else 0.0

    result = classify_relevance_verdict(relevance_payload)
    pruned_off_domain = (
        len(result.evidence.get("off_domain_citekeys", []))
        if result.disposition == "GO-WITH-RESIDUE"
        else 0
    )

    canary_ok = not relevance_payload.get("canary_aborted", False)
    if not canary_ok:
        gaps.append(
            f"P: relevance-verify canary aborted — {relevance_payload.get('canary_detail', '')}"
        )

    return {
        "relevance_verdict_total": total,
        "off_domain_count": off_domain_count,
        "uncertain_count": uncertain_count,
        "off_domain_fraction": off_domain_fraction,
        "relevance_disposition": result.disposition,
        "relevance_canary_ok": canary_ok,
        "pruned_off_domain": pruned_off_domain,
        "_verdicts": verdicts,
        "_gaps": gaps,
    }


def _corpus_rows(corpus_path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Return ``[(annotation, citekey), ...]`` for every row of ``_corpus.md``
    + a ``gaps`` list. Uses the ONE shared bracket-annotation grammar
    (``review.relevance.corpus_row_annotation_tags`` — see that function's
    convergence note; this was previously an independent exact-string-match
    ``annotation.upper() == "[NEW]"`` check that silently undercounted a
    COMPOUND annotation like ``[LEG-1][NEW]``, flagging it as a gap/
    "malformed" row instead of counting it — the same undercount class
    ``review._parse_corpus_citekeys`` was already fixed for). A malformed
    bracket annotation (bracket-shaped but neither NEW nor IN-CORPUS) is
    surfaced as a ledger gap here rather than raising, since the ledger's
    job is to report state honestly, never to crash the gate it's attached
    to (the gate itself already enforces ``CorpusSchemaError`` upstream)."""
    from .relevance import corpus_row_annotation_tags

    gaps: list[str] = []
    if not corpus_path.exists():
        gaps.append("K/corpus: _corpus.md not found.")
        return [], gaps

    text = corpus_path.read_text(encoding="utf-8")
    rows: list[tuple[str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cols = [c.strip() for c in stripped.split("|") if c.strip()]
        if len(cols) < 2:
            continue
        annotation = cols[0]
        tags = corpus_row_annotation_tags(annotation)
        if not tags:
            continue
        is_new = "NEW" in tags
        is_in_corpus = any(t.startswith("IN-CORPUS") for t in tags)
        if is_new or is_in_corpus:
            rows.append((annotation, cols[1]))
            continue
        gaps.append(
            f"K/corpus: {corpus_path.name}:{lineno}: malformed row annotation "
            f"{annotation!r} — excluded from counts."
        )
    return rows, gaps


def _literature_note_for_citekey(literature_root: Path | None, citekey: str) -> Path | None:
    """Resolve a corpus citekey to its CENTRAL-CORE note path (the
    two-layer store — intrinsic fields like doi/arxiv_id, and the paper->
    paper edge graph, live on the core, not the per-project overlay; every
    caller of this function reads intrinsic content, so it resolves against
    ``literature_root``, not a project's ``literature/`` overlay dir).

    Tries the filename-==-citekey convention first (the common case — see
    ``note._cmd_new_two_layer``'s slug convention / the K-stage canonical
    stamp), then falls back to scanning ``literature_root/*.md`` frontmatter
    for a matching ``citekey:`` field (a note filed under a different
    filename slug — the same divergence ``research._note_citekey``
    documents)."""
    if literature_root is None:
        return None
    lit_root = Path(literature_root)
    if not lit_root.exists():
        return None

    direct = lit_root / f"{citekey}.md"
    if direct.exists():
        return direct

    for note_path in sorted(lit_root.glob("*.md")):
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fields, _ = _parse_frontmatter(text)
        ck = str(fields.get("citekey") or "").strip()
        if ck == citekey:
            return note_path
    return None


def _resolving_ids_for_note(note_path: Path) -> str:
    """Best-effort ``doi:.../arxiv:...`` resolving-id string for a
    literature note — reuses ``research.py``'s id-extraction helpers
    (declared ``doi:``/``arxiv_id:`` fields, falling back to a ``url:``-
    derived id) rather than re-implementing the same regexes."""
    from ..research import _normalize_doi, _normalize_arxiv, _doi_from_url, _arxiv_from_url

    text = note_path.read_text(encoding="utf-8")
    fields, _ = _parse_frontmatter(text)
    url = fields.get("url") or None

    ids: list[str] = []
    doi = _normalize_doi(fields.get("doi") or None) or _doi_from_url(url)
    if doi:
        ids.append(f"doi:{doi}")
    arxiv = _normalize_arxiv(fields.get("arxiv_id") or None) or _arxiv_from_url(url)
    if arxiv:
        ids.append(f"arxiv:{arxiv}")
    return ", ".join(ids)


def _citekey_migrated_count(
    literature_dir: Path | None, corpus_citekeys: set[str],
) -> int | str:
    """ fix-round (CHANGE 2): ``rv research migrate-citekeys`` (K-3)
    DOES record a per-project, append-only provenance artifact —
    ``literature/_citekey_migration_ledger.json`` (``research.py``'s
    ``_CITEKEY_MIGRATION_LEDGER_NAME``) — so a bare ``0`` here would be a
    fabricated fact, not an honest "not derived".

    When the ledger file exists: count DISTINCT migration-ledger entries
    whose ``new`` citekey appears in THIS review's ``_corpus.md`` — the
    real, traceable intersection of "migrated" x "in this review's corpus".

    When ``literature_dir`` is ``None`` or the ledger file is absent
    (``rv research migrate-citekeys`` never ran for this project): return
    the literal string ``"untracked"`` — an honest sentinel, never a
    fabricated count. This mirrors ``_p_block``'s honest-no-op pattern for
    an optional pass that was never wired/run; it does NOT feed into the
    ledger's ``_gaps`` list (non-gating, per fix-round dispatch) —
    "migrate-citekeys was never run for this project" is not itself an
    incompleteness of THIS review's ledger.
    """
    import json as _json
    from .. import research as _research

    if literature_dir is None:
        return "untracked"
    ledger_path = Path(literature_dir) / _research._CITEKEY_MIGRATION_LEDGER_NAME
    if not ledger_path.is_file():
        return "untracked"
    try:
        entries = _json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "untracked"
    if not isinstance(entries, list):
        return "untracked"

    migrated_new_keys = {
        str(e.get("new")).strip()
        for e in entries
        if isinstance(e, dict) and e.get("new")
    }
    return len(migrated_new_keys & corpus_citekeys)


def _not_yet_distilled_block(
    deviations_path: Path, literature_root: Path | None,
) -> dict[str, Any]:
    """ derivation: ``not_yet_distilled_count`` — every remediation-
    added corpus citekey (a ``within-criteria-append`` deviation's ``added``
    row) that has NO materialized paper->paper edge in its literature note
    (or no literature note at all). At a clean coverage-gate GO the
    incremental-relate loop has written a bidirectional edge for every
    remediation-added paper, so this is 0; a paper the loop terminated on
    before relating (HALT snapshot, or an un-distilled corpus row) surfaces
    here as a > 0 completeness gap.

    This is DERIVED (not a stored counter): it re-reads ``_deviations.md``'s
    declared adds (via the SAME parser ``autonomy`` writes/reads them with —
    ``_parse_deviation_citekey_deltas``) and joins them against
    the materialized edge graph (``relate_check.parse_paper_relations`` over
    each note body). The paper->paper edge graph (``## Related papers``) is
    CORE-only content — ``incremental_relate.append_bidirectional_edge``
    writes it to the two-layer store's CENTRAL CORE, so this resolves
    against ``literature_root`` (``cfg.literature_root``), not a project's
    overlay dir. ``literature_root is None`` -> every remediation-added
    paper is un-resolvable, so all count as not-yet-distilled (honest,
    never a fabricated 0).

    Non-gating by construction: a legitimate mid-backtrack snapshot has
    un-related adds; only the caller's GO context asserts == 0. Returned as
    a scalar + the list of the specific un-distilled citekeys (auditable),
    never folded into ``ledger_complete`` here.
    """
    from .autonomy import _parse_deviation_citekey_deltas
    from .relate_check import parse_paper_relations

    _removed, added = _parse_deviation_citekey_deltas(deviations_path)
    not_distilled: list[str] = []
    for citekey in sorted(added):
        note_path = _literature_note_for_citekey(literature_root, citekey)
        if note_path is None:
            not_distilled.append(citekey)
            continue
        _fields, body = _parse_frontmatter(note_path.read_text(encoding="utf-8"))
        parsed = parse_paper_relations(body)
        if not parsed.edges:
            not_distilled.append(citekey)
    return {
        "remediation_added_count": len(added),
        "not_yet_distilled_count": len(not_distilled),
        "not_yet_distilled_citekeys": not_distilled,
    }


def _k_block(
    corpus_path: Path,
    literature_dir: Path | None,
    literature_root: Path | None = None,
) -> dict[str, Any]:
    """Assemble the K (canonical-citekey) block + the corpus counts +
    the canonical-key-map body rows.

    ``citekey_migrated_count``: see ``_citekey_migrated_count`` — traces to
    the real ``_citekey_migration_ledger.json`` when present; an honest
    ``"untracked"`` sentinel (never a fabricated ``0``) when absent. That
    ledger is a project-level bookkeeping JSON (not an OKF note), so it
    still resolves against ``literature_dir`` (the project's overlay dir),
    unaffected by the two-layer split.

    ``resolving_ids`` (doi:/arxiv:) is CORE-only content — resolves
    against ``literature_root`` (the central store).
    """
    from ..cite import CITEKEY_RE

    from .relevance import corpus_row_annotation_tags

    rows, gaps = _corpus_rows(corpus_path)
    in_corpus_count = sum(
        1 for ann, _ck in rows
        if any(t.startswith("IN-CORPUS") for t in corpus_row_annotation_tags(ann))
    )
    new_count = len(rows) - in_corpus_count

    conformant = 0
    nonconformant = 0
    key_map_rows: list[tuple[str, str, bool]] = []
    for _ann, citekey in rows:
        ok = bool(CITEKEY_RE.match(citekey))
        if ok:
            conformant += 1
        else:
            nonconformant += 1
        note_path = _literature_note_for_citekey(literature_root, citekey)
        resolving_ids = _resolving_ids_for_note(note_path) if note_path is not None else ""
        if note_path is None:
            gaps.append(f"K/key-map: no literature note found for citekey {citekey!r}.")
        key_map_rows.append((citekey, resolving_ids, ok))

    corpus_citekeys = {citekey for _ann, citekey in rows}
    migrated_count = _citekey_migrated_count(literature_dir, corpus_citekeys)

    return {
        "citekey_convention": CITEKEY_CONVENTION,
        "citekey_conformant_count": conformant,
        "citekey_nonconformant_count": nonconformant,
        "citekey_migrated_count": migrated_count,
        "accepted": len(rows),
        "in_corpus": in_corpus_count,
        "new": new_count,
        "key_map_rows": key_map_rows,
        "_gaps": gaps,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fm_line(key: str, value: Any) -> str:
    if isinstance(value, bool):
        value = "true" if value else "false"
    return f"{key}: {value}"


def _render_search_plan_table(search_hits_path: Path | None) -> tuple[list[str], list[str]]:
    gaps: list[str] = []
    lines = ["## Search plan provenance", "", "| Facet/angle | Source | Hits | Error |", "|---|---|---|---|"]
    text = _read_text_or_none(search_hits_path)
    if text is None:
        gaps.append("Search plan provenance: _search_hits.md not found.")
        lines.append("| _(no _search_hits.md found)_ | | | |")
        lines.append("")
        return lines, gaps
    row_re = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(\d+)\s*\|\s*(.*?)\s*\|\s*$")
    in_cells = False
    for line in text.splitlines():
        if line.strip() == "## Cells":
            in_cells = True
            continue
        if in_cells:
            if line.strip().startswith("|") and not line.strip().startswith("|---"):
                m = row_re.match(line.strip())
                if m and m.group(1) != "Angle":
                    lines.append(f"| {m.group(1)} | {m.group(2)} | {m.group(3)} | {m.group(4)} |")
            elif line.strip() == "" and lines[-1] != "|---|---|---|---|":
                break
    lines.append("")
    return lines, gaps


def _render_walk_table(walk_path: Path | None) -> tuple[list[str], list[str]]:
    gaps: list[str] = []
    lines = [
        "## Citation-neighbor walk", "",
        "| Hop | New (forward) | New (backward) | New independent | Cumulative | Direction-starved |",
        "|---|---|---|---|---|---|",
    ]
    text = _read_text_or_none(walk_path)
    if text is None:
        gaps.append("Walk: _walk.md not found.")
        lines.append("| _(no _walk.md found)_ | | | | | |")
        lines.append("")
        return lines, gaps
    row_re = re.compile(
        r"^\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(.*?)\s*\|\s*$"
    )
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and not stripped.startswith("|---") and "Hop" not in stripped.split("|")[1]:
            m = row_re.match(stripped)
            if m:
                lines.append(
                    f"| {m.group(1)} | {m.group(2)} | {m.group(3)} | {m.group(4)} | "
                    f"{m.group(5)} | {m.group(6)} |"
                )
    lines.append("")
    return lines, gaps


def _render_relevance_table(relevance_payload: dict[str, Any] | None) -> list[str]:
    lines = [
        "## Relevance-gate dispositions", "",
        "| Citekey | Verdict (IN/OFF_DOMAIN/UNCERTAIN) | Action (kept/pruned/flagged) |",
        "|---|---|---|",
    ]
    if relevance_payload is None:
        lines.append("| _(review-relevance-verify not wired for this run)_ | | |")
        lines.append("")
        return lines
    verdicts: dict[str, str] = relevance_payload.get("verdicts", {})
    if not verdicts:
        lines.append("| _(no verdicts recorded)_ | | |")
    for ck in sorted(verdicts):
        v = verdicts[ck]
        action = {"IN": "kept", "OFF_DOMAIN": "pruned", "UNCERTAIN": "flagged"}.get(v, "flagged")
        lines.append(f"| {ck} | {v} | {action} |")
    lines.append("")
    return lines


def _render_key_map_table(key_map_rows: list[tuple[str, str, bool]]) -> list[str]:
    lines = [
        "## Canonical-key map", "",
        "| Citekey | Resolving id(s) | Conformant? |",
        "|---|---|---|",
    ]
    if not key_map_rows:
        lines.append("| _(no corpus rows)_ | | |")
    for citekey, ids, ok in key_map_rows:
        lines.append(f"| {citekey} | {ids} | {'yes' if ok else 'no'} |")
    lines.append("")
    return lines


def _render_residue_section(gaps_text: str | None) -> list[str]:
    lines = ["## Open coverage residue", ""]
    if gaps_text is None:
        lines.append("_(no _coverage-gaps.md — no open residue declared.)_")
    else:
        lines.append(gaps_text.strip())
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# The single-writer assembler
# ---------------------------------------------------------------------------

def write_corpus_ledger(
    review_dir: Path,
    *,
    review_scope: str | None = None,
    literature_dir: Path | None = None,
    literature_root: Path | None = None,
    relevance_payload: dict[str, Any] | None = None,
    critic_backtrack_rounds: int = 0,
    halt_reason: str | None = None,
    out_path: Path | None = None,
) -> Path:
    """Assemble ``_corpus_ledger.md`` from the review's existing durable
    artifacts. Additive: reads ``_protocol.md``/``_search_hits.md``/
    ``_walk.md``/``_coverage-gaps.md``/``_corpus.md`` under
    ``review_dir`` — never writes to any of them.

    Args:
        review_dir: the ``reviews/<scope>/`` directory.
        review_scope: defaults to ``review_dir.name``.
        literature_dir: the project's ``literature/`` (overlay) dir — used
            ONLY for the project-level citekey-migration ledger JSON
            (``_citekey_migrated_count``), which is not an OKF note and is
            unaffected by the two-layer split.
        literature_root: the CENTRAL store (``cfg.literature_root``),
            for the canonical-key-map's resolving-id lookup + the
            not-yet-distilled edge-graph check — both CORE-only content.
            ``None`` is an honest no-op (every key-map row's resolving
            id(s) column is blank, a surfaced gap, not a guess).
        relevance_payload: ``review.relevance.check_relevance_verifier``'s
            return dict, or ``None`` if the node was never wired for this
            manifest (honest no-op, not a gap).
        critic_backtrack_rounds: the pole-directed critic-backtrack loop's
            round count (``run_state.meta["critic_backtrack_state"]["rounds_used"]``,
            an UNRELATED mechanism from the deleted coverage-gate
            remediation loop — see ``review/remediation.py``), passed in by
            the caller since the ledger has no run-state access.
        halt_reason: when the coverage-gate disposition is HALT-DECLARE,
            the human-readable reason — folded into ``ledger_complete:
            false`` + a top-level gap line, so a HALT snapshot is still an
            honest, auditable artifact (never silently omitted).
        out_path: defaults to ``review_dir / "_corpus_ledger.md"``.

    Returns:
        The path written.
    """
    review_dir = Path(review_dir)
    scope = review_scope or review_dir.name
    protocol_path = review_dir / "_protocol.md"
    search_hits_path = review_dir / "_search_hits.md"
    walk_path = review_dir / "_walk.md"
    gaps_path = review_dir / "_coverage-gaps.md"
    corpus_path = review_dir / "_corpus.md"
    out = out_path or (review_dir / "_corpus_ledger.md")

    deviations_path = review_dir / "_deviations.md"

    q = _q_block(protocol_path, walk_path, gaps_path)
    p = _p_block(relevance_payload)
    k = _k_block(corpus_path, literature_dir, literature_root)
    nd = _not_yet_distilled_block(deviations_path, literature_root)

    search_lines, search_gaps = _render_search_plan_table(search_hits_path)
    walk_lines, walk_gaps = _render_walk_table(walk_path)
    relevance_lines = _render_relevance_table(relevance_payload)
    key_map_lines = _render_key_map_table(k["key_map_rows"])
    residue_lines = _render_residue_section(q["gaps_text"])

    all_gaps: list[str] = (
        list(q["_gaps"]) + list(p["_gaps"]) + list(k["_gaps"])
        + search_gaps + walk_gaps
    )
    if halt_reason:
        all_gaps.append(f"HALT: {halt_reason}")

    ledger_complete = not all_gaps

    fm_lines = [
        "---",
        _fm_line("type", "corpus-ledger"),
        _fm_line("review_scope", scope),
        _fm_line("schema_version", SCHEMA_VERSION),
        _fm_line("ledger_complete", ledger_complete),
        # Q block
        _fm_line("matrix_hash", q["matrix_hash"]),
        _fm_line("angles_searched", q["angles_searched"]),
        _fm_line("distinct_query_count", q["distinct_query_count"]),
        _fm_line("matrix_band_ok", q["matrix_band_ok"]),
        _fm_line("stop_reason", q["stop_reason"]),
        _fm_line("walk_bounded", q["walk_bounded"]),
        _fm_line("open_counter_poles", q["open_counter_poles"]),
        _fm_line("critic_backtrack_rounds", critic_backtrack_rounds),
        # P block
        _fm_line("relevance_verdict_total", p["relevance_verdict_total"]),
        _fm_line("off_domain_count", p["off_domain_count"]),
        _fm_line("uncertain_count", p["uncertain_count"]),
        _fm_line("off_domain_fraction", round(p["off_domain_fraction"], 4)),
        _fm_line("relevance_disposition", p["relevance_disposition"]),
        _fm_line("relevance_canary_ok", p["relevance_canary_ok"]),
        _fm_line("pruned_off_domain", p["pruned_off_domain"]),
        # K block
        _fm_line("citekey_convention", k["citekey_convention"]),
        _fm_line("citekey_conformant_count", k["citekey_conformant_count"]),
        _fm_line("citekey_nonconformant_count", k["citekey_nonconformant_count"]),
        _fm_line("citekey_migrated_count", k["citekey_migrated_count"]),
        # corpus counts
        _fm_line("accepted", k["accepted"]),
        _fm_line("in_corpus", k["in_corpus"]),
        _fm_line("new", k["new"]),
        # relate-completeness audit (derived — see _not_yet_distilled_block)
        _fm_line("remediation_added_count", nd["remediation_added_count"]),
        _fm_line("not_yet_distilled_count", nd["not_yet_distilled_count"]),
        _fm_line("not_yet_distilled_citekeys", ", ".join(nd["not_yet_distilled_citekeys"])),
        "---",
        "",
        "# Corpus ledger\n",
    ]

    if all_gaps:
        fm_lines.append("> [LEDGER-GAP] this ledger is INCOMPLETE — see gaps below:\n")
        for g in all_gaps:
            fm_lines.append(f"> [LEDGER-GAP] {g}")
        fm_lines.append("")

    body = (
        fm_lines
        + search_lines
        + walk_lines
        + relevance_lines
        + key_map_lines
        + residue_lines
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(body), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# (methods relocation): render the reader-facing PRISMA-style methods
# write-up by CONSUMING _corpus_ledger.md — never re-deriving a number the
# ledger already computed.
# ---------------------------------------------------------------------------

def render_methods_from_ledger(ledger_path: Path) -> str:
    """Render the PRISMA-style methods flow from ``_corpus_ledger.md``
    — frontmatter scalars for the count/search/walk-coverage summary,
    the body's already-rendered tables (search plan, citation-neighbor walk,
    relevance, canonical-key map) verbatim.

    Every number here traces to the ledger — this function parses, it never
    recomputes a count (never fabricate; the ledger is the
    single source of truth built for exactly this consumer). Per the
    gold-settled decision (no Appendix in the reader-facing document), this
    output is written to the project's DEVLOG/control note, NEVER joined
    into ``_report.md``/``report.md`` — the caller (``source_transform``'s
    ``appendix-methods`` tip) already carries that routing instruction.

    Args:
        ledger_path: the review's ``_corpus_ledger.md`` (``reviews/<slug>/``).

    Returns:
        Markdown. An absent ledger is an honest "not available yet" no-op
        (never a fabricated methods section) — the manuscript's methods
        node runs downstream of the review's coverage-gate, which is where
        ``write_corpus_ledger`` is called; a ledger that hasn't landed yet
        means the review hasn't reached that gate.

    """
    if not ledger_path.exists():
        return (
            "## PRISMA scope & method\n\n"
            "_No `_corpus_ledger.md` found for this manuscript yet — it is "
            "written by the review's coverage-gate; run "
            "`rv review <project> expand <scope>` through to that gate "
            "first._\n"
        )

    text = ledger_path.read_text(encoding="utf-8")
    fields, body = _parse_frontmatter(text)

    def _f(key: str, default: str = "") -> str:
        return str(fields.get(key, default))

    lines = ["## PRISMA scope & method\n"]

    if str(fields.get("ledger_complete", "true")).strip().lower() != "true":
        lines.append(
            "> [LEDGER-GAP] this ledger was INCOMPLETE when generated — see "
            "the control-note `_corpus_ledger.md` for the specific gaps.\n"
        )

    lines.append("| Category | Count |")
    lines.append("| --- | --- |")
    lines.append(f"| Accepted (frozen corpus) | {_f('accepted')} |")
    lines.append(f"| In-corpus (previously known) | {_f('in_corpus')} |")
    lines.append(f"| New | {_f('new')} |")
    lines.append(f"| Citekey conformant | {_f('citekey_conformant_count')} |")
    lines.append(f"| Citekey non-conformant | {_f('citekey_nonconformant_count')} |")
    lines.append("")
    lines.append(
        f"Search breadth: {_f('distinct_query_count')} distinct quer"
        f"{'y' if _f('distinct_query_count') == '1' else 'ies'} across "
        f"angles: {_f('angles_searched')}."
    )
    lines.append(
        f"Citation-neighbor walk stop reason: {_f('stop_reason')} "
        f"(walk_bounded: {_f('walk_bounded')})."
    )
    if _f("open_counter_poles"):
        lines.append(f"Open counter-poles: {_f('open_counter_poles')}.")
    lines.append("")

    # The body already carries the fully-rendered detail tables (search
    # plan, citation-neighbor walk, relevance dispositions, canonical-key
    # map, open residue) — reuse verbatim rather than re-parsing/
    # re-rendering them a second time.
    body_stripped = body.strip()
    if body_stripped.startswith("# Corpus ledger"):
        # Drop the ledger's own H1 title — this is a subsection of the
        # methods write-up, not a standalone document.
        body_stripped = body_stripped.split("\n", 1)[1].lstrip() if "\n" in body_stripped else ""
    if body_stripped:
        lines.append(body_stripped)

    return "\n".join(lines).rstrip() + "\n"
