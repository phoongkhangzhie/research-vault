# SPDX-License-Identifier: AGPL-3.0-or-later
"""manuscript/types/lit_review.py — the ``lit-review`` ManuscriptType (PR-M6).

Fills the PR-M1 stub with the survey's real machinery (design §3-§5):
  - the real 9-row section-set (§3), abstract drafted LAST (assembly class
    "S (last)" — it must be a subset of the body, so it needs the body first).
  - the framework-selection Phase-1 (§5): scope -> framework-propose ->
    [HG:approve-framework], the 4 candidate shapes PROPOSED never forced, a
    corpus-hash stamp (injected, never agent-computed), and the
    ``check_framework_gate`` structural BLOCK on an empty spine.
  - the OKF -> survey ``source_transform`` (§4): a deterministic PRISMA-ledger
    renderer + comparison-table-row assembler, siblings to
    ``review.coverage_report`` — mechanical, zero-hallucination.
  - the §3.1 structurally-binding thematic-section brief contract.
  - the reframe-escalation payload builder (§5.1) — PROPOSES a reframe,
    never auto-applies one (the human commits via ``rv manuscript new
    --reframe``, a future CLI wiring — out of scope here, PR-M8/CLI-follow-on).

Explicitly OUT of scope here (type-generic core / other PRs):
  - the hermetic ``.bib`` build (PR-M2) and the equation-fidelity gate (PR-M4)
    — this module's ``equation_sources``/comparison-table rows are DATA those
    gates will consume once they land; nothing here re-implements them.
  - the review-revise board (PR-M5) and the rubric/canaries (PR-M8) — the
    reframe-escalation payload builder here is a pure, standalone function so
    PR-M5 can call it once the board exists; it does not itself run a round.
  - the exemplar few-shot loader (PR-M7/M8) — the brief contract below notes
    where the excerpts will be embedded; the loader/injector is not built here.

Design: docs/superpowers/specs/2026-07-07-survey-capability-design.md §3-§5.

sr: PR-M6
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from research_vault.config import Config
from research_vault.note import _parse_frontmatter

from . import ManuscriptType, SectionSpec, register_type

# ---------------------------------------------------------------------------
# §5 — the 4 candidate organizing-framework shapes (proposed, never forced)
# ---------------------------------------------------------------------------

FRAMEWORK_SHAPES: tuple[dict[str, str], ...] = (
    {
        "key": "pipeline",
        "name": "Pipeline / lifecycle",
        "description": (
            "Branches follow a processing pipeline or lifecycle stage "
            "(e.g. data -> training -> evaluation -> deployment). Fits a field "
            "organized around a sequence of stages each paper contributes to."
        ),
    },
    {
        "key": "evolution-arc",
        "name": "Maturity / evolution arc",
        "description": (
            "Branches follow the field's historical maturation (e.g. "
            "early heuristic methods -> statistical methods -> learned methods). "
            "Fits a field with a clear generational progression."
        ),
    },
    {
        "key": "n-axis",
        "name": "N-axis orthogonal taxonomy",
        "description": (
            "Branches are independent classification axes a paper can be "
            "placed on simultaneously (e.g. modality x supervision-level). "
            "Fits a field with no single dominant ordering — Nickerson's "
            "classic taxonomy shape."
        ),
    },
    {
        "key": "coupled-taxonomies",
        "name": "Coupled problem/solution taxonomies",
        "description": (
            "Two paired taxonomies — a problem-space taxonomy and a "
            "solution-space taxonomy — with an explicit mapping between them. "
            "Fits a field organized around distinct problem variants each "
            "answered by a family of solutions."
        ),
    },
)


def render_framework_candidates_menu() -> str:
    """Render the 4-shape candidate menu as prose for the ``framework-propose`` brief.

    Deterministic — the shape *names+descriptions* are fixed; the agent fills
    in which MOC(s)/branches/misfits apply for THIS corpus. Proposes a menu,
    never a verdict (the human, not the machine, discovers the framework).

    sr: PR-M6
    """
    lines = [
        "Propose ALL FOUR candidate organizing-framework shapes below, defended "
        "from this project's `mocs/` (+ concepts/gaps) — NEVER commit to one; "
        "write a `_framework-candidates.md` menu for the human to pick/shape/nest/"
        "go-custom from (design §5, D2). You MAY additionally propose a NESTED "
        "composition (a dominant top-level spine encapsulating smaller MOC-spines "
        "— D5's 'bigger spine').\n",
    ]
    for shape in FRAMEWORK_SHAPES:
        lines.append(f"- **{shape['name']}** (`{shape['key']}`): {shape['description']}")
    lines.append(
        "\nFor EACH shape, state: which `mocs/` region(s) it draws on, what the "
        "top-level branches would be, and what would NOT fit (misfits) — an "
        "honest per-shape assessment, not a sales pitch for one."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# §5 — check_framework_gate (structural BLOCK, wired into `rv dag approve`)
# ---------------------------------------------------------------------------

def check_framework_gate(manuscript_note_path: Path) -> tuple[bool, str]:
    """Structural gate: ``_manuscript.md`` must carry a non-empty ``spine_shape``
    AND a non-empty ``branches`` list before ``approve-framework`` may pass.

    Mirrors ``review.check_protocol_gate``'s shape (native rv enforcement, not
    prose-only convention) — wired into ``rv dag approve`` at the
    ``approve-framework`` node (design §5: "Framework choice is a human
    commitment (D5)").

    Args:
        manuscript_note_path: path to the manuscript's ``_manuscript.md``.

    Returns:
        (ok, message) — ok is False when the file is missing, or either
        ``spine_shape`` or ``branches`` is absent/empty/whitespace-only.

    sr: PR-M6
    """
    if not manuscript_note_path.exists():
        return False, (
            f"rv dag approve: framework gate BLOCKED — _manuscript.md not found "
            f"at {manuscript_note_path}. The manuscript's scope node must "
            f"produce this file before approve-framework can pass."
        )

    text = manuscript_note_path.read_text(encoding="utf-8")
    fields, _ = _parse_frontmatter(text)

    spine_shape = str(fields.get("spine_shape", "")).strip()
    branches_raw = fields.get("branches", "")
    if isinstance(branches_raw, list):
        branches_nonempty = len(branches_raw) > 0
    else:
        branches_nonempty = bool(str(branches_raw).strip())

    if not spine_shape or not branches_nonempty:
        return False, (
            f"rv dag approve: framework gate BLOCKED — {manuscript_note_path} "
            f"has an empty or missing 'spine_shape' and/or 'branches' "
            f"frontmatter field.\n"
            f"Design §5 (D5): framework choice is a human commitment — the "
            f"organizing framework cannot be reliably discovered by the "
            f"machine. Fix: edit {manuscript_note_path.name} to add a "
            f"non-empty 'spine_shape: <one of pipeline|evolution-arc|n-axis|"
            f"coupled-taxonomies|custom>' and a non-empty 'branches:' list "
            f"(the top-level branch names), then re-run "
            f"`rv dag approve <run_id> approve-framework`."
        )

    return True, "OK"


# ---------------------------------------------------------------------------
# §5.1 — the reframe-escalation payload (PROPOSES, never auto-reframes)
# ---------------------------------------------------------------------------

def build_reframe_escalation_payload(
    *,
    round_no: int,
    misfits: list[str],
    candidate_reframes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the reframe-escalation payload (design §5.1, D5 — reviewer pass 2 §C.2).

    When the framework/taxonomy critic (PR-M8's reviewer lens) judges the
    spine incoherent round after round (recurring misfits: same works don't
    fit, concepts orphan, gaps won't anchor), NO section polish fixes it. This
    builds the escalation payload the meta-review attaches to the approve
    payload — it PROPOSES candidate reframes, it NEVER commits one. The human
    commits the new spine and re-scaffolds via
    ``rv manuscript new --reframe <prior-slug>`` (a future CLI wiring;
    re-entering Phase-1 with these misfits/candidates pre-loaded).

    This is a pure, standalone function — PR-M5's review-revise board calls
    it once built; it does not itself run a review round or touch any note.

    Args:
        round_no: which review round triggered the escalation (1-indexed).
        misfits: recurring misfit descriptions (works that don't fit, orphan
            concepts, unanchored gaps) accumulated across rounds.
        candidate_reframes: candidate encapsulating reframes (each a dict,
            e.g. {"shape": "n-axis", "rationale": "..."}) — NEVER applied.

    Returns:
        A payload dict: ``cleared`` is always False (an escalation means the
        spine did not clear); ``action`` is always ``"propose-only"`` (never
        ``"auto-reframe"`` — there is no such action); ``escalation`` carries
        the round, misfits, and candidates for a human to read and act on.

    sr: PR-M6
    """
    return {
        "cleared": False,
        "action": "propose-only",
        "escalation": {
            "round": round_no,
            "misfits": list(misfits),
            "candidate_reframes": list(candidate_reframes),
            "message": (
                f"Framework judged incoherent after round {round_no}; recurring "
                f"misfits = {misfits}; candidate encapsulating reframes = "
                f"{[c.get('shape', c) for c in candidate_reframes]}. "
                f"The human commits the new spine via "
                f"`rv manuscript new --reframe <prior-slug>` — this payload "
                f"proposes, it does not commit."
            ),
        },
    }


# ---------------------------------------------------------------------------
# §5 — Phase-1: scope -> framework-propose -> [HG: approve-framework]
# ---------------------------------------------------------------------------

def _compute_corpus_hash_note(project: str, slug: str, project_notes_dir: Path) -> str:
    """Return an injected (never agent-computed) note about the corpus hash.

    Convention (documented, zero-config for the common case): a manuscript
    slug summarizing a completed lit-review loop shares its slug with that
    review's scope id (``reviews/<slug>/_corpus.md``). If that frozen corpus
    exists, hash it (results-inject discipline: the machine computes, the
    agent copies verbatim — never invents). If not, say so honestly; the
    field stays empty until a real corpus is frozen.

    sr: PR-M6
    """
    from research_vault.hashing import hash_file

    corpus_path = project_notes_dir / "reviews" / slug / "_corpus.md"
    if corpus_path.exists():
        digest = hash_file(corpus_path)
        return (
            f"CORPUS_HASH: {digest} (from {corpus_path}). "
            f"Copy this verbatim into _manuscript.md's `corpus_hash:` field — "
            f"never retype or re-derive it."
        )
    return (
        f"CORPUS_HASH: (none — no frozen corpus found at {corpus_path}). "
        f"If this manuscript summarizes a completed `rv review` loop, the "
        f"manuscript slug is expected to match that review's scope id. "
        f"Leave `corpus_hash:` empty until a real corpus is frozen."
    )


def phase1_builder(
    *,
    project: str,
    slug: str,
    project_notes_dir: Path,
    tree_root: Path,
    config: Any = None,
) -> dict[str, Any]:
    """Build the lit-review Phase-1 manifest: framework selection (design §5).

    Topology:
      scope -> framework-propose -> [HG: approve-framework]

    - ``scope``: agent; reads OKF atoms + `reviews/` (the convention above);
      renders the PRISMA inclusion ledger (mechanical — via
      ``source_transform``'s ``render_prisma_ledger`` once wired) and stamps
      the injected corpus hash (never agent-computed) into its brief.
    - ``framework-propose``: agent; reads `mocs/`+`concepts/`+`gaps/`; proposes
      the 4 candidate shapes (``render_framework_candidates_menu``) —
      produces `_framework-candidates.md`; NEVER commits.
    - ``approve-framework``: human-go; the human picks/shapes/nests/goes
      custom, writing `spine_shape`+`branches` into `_manuscript.md`.
      ``check_framework_gate`` (wired into `rv dag approve`) BLOCKs an empty
      spine.

    Matches the ``ManuscriptType.phase1_builder`` signature (types/__init__.py).

    sr: PR-M6
    """
    def _afterok(from_id: str) -> dict[str, str]:
        return {"from": from_id, "edge": "afterok"}

    def _rel(okf_type: str) -> str:
        return str(project_notes_dir / okf_type)

    candidates_path = str(tree_root / "_framework-candidates.md")
    manuscript_note_path = str(tree_root / "_manuscript.md")

    corpus_hash_note = _compute_corpus_hash_note(project, slug, project_notes_dir)

    nodes: list[dict[str, Any]] = [
        {
            "id": "scope",
            "type": "agent",
            "label": "Scope the survey: render PRISMA inclusion ledger + stamp corpus hash",
            "spec": (
                "Render the PRISMA-style inclusion ledger for this survey from "
                "the project's literature/mocs/reviews corpus (mechanical — "
                "counts must match `rv review coverage` for the same scope, "
                "never estimated). Then record the injected corpus hash "
                "below verbatim in `_manuscript.md`'s `corpus_hash:` field "
                "(the stale-corpus guard, design §4.5.5) — do not compute it "
                "yourself.\n\n"
                f"{corpus_hash_note}"
            ),
            "reads": [_rel("literature"), _rel("mocs"), _rel("reviews")],
            "needs": [],
        },
        {
            "id": "framework-propose",
            "type": "agent",
            "label": "Propose 4 candidate organizing-framework shapes (never commit)",
            "spec": render_framework_candidates_menu(),
            "reads": [_rel("mocs"), _rel("concepts"), _rel("gaps")],
            "produces": {"_framework-candidates.md": candidates_path},
            "needs": [_afterok("scope")],
        },
        {
            "id": "approve-framework",
            "type": "human-go",
            "label": (
                "Gate: Approve the organizing framework — pick/shape/nest/go-custom "
                "from `_framework-candidates.md`, writing `spine_shape:`+`branches:` "
                "into `_manuscript.md` (BLOCKED if either is empty — design §5, D5)."
            ),
            "needs": [_afterok("framework-propose")],
        },
    ]

    return {
        "run_id": f"manuscript-{slug}-phase1",
        "project": project,
        "name": f"Manuscript Phase-1 (lit-review framework selection): {slug}",
        "global_cap": 1,
        "nodes": nodes,
    }


# ---------------------------------------------------------------------------
# §4 — the OKF -> survey source_transform (deterministic pieces)
# ---------------------------------------------------------------------------

def _parse_deviation_blocks(deviations_path: Path) -> list[dict[str, Any]]:
    """Parse every ``## Deviation v(k-1) -> v(k) (...)`` block written by
    ``review.autonomy.record_deviation`` into a structured list, one dict
    per block: ``{version_from, version_to, removed, added, rationale}``.

    Scoped to exactly the fixed-format lines ``record_deviation`` writes —
    not a general markdown parser (mirrors
    ``review.autonomy._parse_deviation_citekey_deltas``'s scoping, but keeps
    each block's own removed/added/rationale separate instead of a single
    flattened union, since the PRISMA ledger needs to show EACH version's
    delta + reason, not just the aggregate).

    Returns ``[]`` if the file doesn't exist or carries no recognizable block
    — never raises (a malformed/absent deviation log degrades to "nothing to
    show", the honest no-op for a manuscript with no scope revisions).
    """
    if not deviations_path.exists():
        return []
    text = deviations_path.read_text(encoding="utf-8")
    blocks: list[dict[str, Any]] = []
    header_re = re.compile(r"^##\s+Deviation\s+v(\d+)\s*->\s*v(\d+)", re.MULTILINE)
    headers = list(header_re.finditer(text))
    for i, m in enumerate(headers):
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        section = text[start:end]

        def _line(label: str) -> str:
            lm = re.search(rf"^\*\*{label}:\*\*\s*(.*)$", section, re.MULTILINE)
            return lm.group(1).strip() if lm else ""

        def _citekeys(label: str) -> list[str]:
            raw = _line(label)
            if not raw or raw == "(none)":
                return []
            return [v.strip() for v in raw.split(",") if v.strip()]

        rationale_m = re.search(r"\*\*Rationale:\*\*\s*(.*)", section, re.DOTALL)
        rationale = rationale_m.group(1).strip() if rationale_m else ""

        # NG-6a: the optional **Kind:** line (record_deviation(kind=...)) —
        # "" for a pre-NG-6a / kind=None deviation (back-compat, no-op).
        kind_m = re.search(r"^\*\*Kind:\*\*\s*(.*)$", section, re.MULTILINE)
        kind = kind_m.group(1).strip() if kind_m else ""

        blocks.append({
            "version_from": int(m.group(1)),
            "version_to": int(m.group(2)),
            "removed": _citekeys("Removed citekeys"),
            "added": _citekeys("Added citekeys"),
            "rationale": rationale,
            "kind": kind,
        })
    return blocks


def render_prisma_ledger(
    coverage: dict[str, Any],
    *,
    deviations_path: Path | None = None,
) -> str:
    """Render a PRISMA-style inclusion/exclusion ledger from a coverage report.

    ``coverage`` is the dict shape returned by ``review.coverage_report()``
    (F16+F17: keyed by citekey; ``counts`` summary). Byte-deterministic —
    no LLM, no invented numbers; this is a sibling to ``coverage_report``
    itself (design §4).

    NG-4b item 5 (NG-6b, scoped — see the module-level test file's docstring
    for the grounded scoping note vs the design doc's full NG-6a dependency):
    when ``deviations_path`` points at a real ``_deviations.md``
    (``review.autonomy.record_deviation``'s output), every declared
    scope/membership deviation renders as an explicit denominator-change row
    — the PRISMA "records excluded, with reasons" row, made real (§1.5
    requirement 2). A corpus that changed size with NO deviation section is
    silently unremarkable (nothing to show is the correct no-op); the point
    is that a declared change is never buried behind a bare final count.

    Args:
        coverage: a ``review.coverage_report()``-shaped dict, or ``{}`` if no
            frozen corpus exists yet (renders an honest "no corpus" ledger).
        deviations_path: optional path to this review's ``_deviations.md``.
            ``None`` or a non-existent file is a correct no-op (no deviation
            section rendered) — most manuscripts never revise scope.

    Returns:
        Markdown PRISMA-style ledger.

    sr: PR-M6, NG-6b
    """
    counts = coverage.get("counts", {}) if coverage else {}
    if not coverage or not coverage.get("corpus_citekeys"):
        return (
            "## PRISMA scope & method\n\n"
            "_No frozen corpus found for this manuscript yet — the ledger "
            "will populate once `rv review <project> expand <scope>` has run "
            "and produced a frozen `_corpus.md` (design §5, the `reviews/` "
            "convention: manuscript slug == review scope id)._\n"
        )

    lines = [
        "## PRISMA scope & method\n",
        "| Category | Count |",
        "| --- | --- |",
        f"| Corpus (frozen citekeys) | {counts.get('corpus', 0)} |",
        f"| Materialized (has a `literature/` note) | {counts.get('materialized', 0)} |",
        f"| Unmaterialized (corpus citekey, no note yet) | {counts.get('unmaterialized', 0)} |",
        f"| Orphan (materialized, absent from every MOC) | {counts.get('orphan', 0)} |",
        "",
        f"Unmaterialized citekeys: {coverage.get('unmaterialized', [])}",
        f"Orphan citekeys: {coverage.get('orphan', [])}",
    ]

    deviation_blocks = _parse_deviation_blocks(deviations_path) if deviations_path else []
    if deviation_blocks:
        lines.append("")
        lines.append("### Deviations from the frozen protocol (records excluded, with reasons)\n")
        running = counts.get("corpus", 0) - sum(
            len(b["added"]) - len(b["removed"]) for b in deviation_blocks
        )
        for b in deviation_blocks:
            n_after = running + len(b["added"]) - len(b["removed"])
            kind_suffix = f" [{b['kind']}]" if b.get("kind") else ""
            lines.append(
                f"- **v{b['version_from']} → v{b['version_to']}**{kind_suffix}: "
                f"N₀={running} → N₁={n_after} "
                f"(−{len(b['removed'])}, +{len(b['added'])})"
            )
            if b["removed"]:
                lines.append(f"  - Excluded: {', '.join(b['removed'])}")
            if b["added"]:
                lines.append(f"  - Added: {', '.join(b['added'])}")
            if b["rationale"]:
                lines.append(f"  - Reason: {b['rationale']}")
            running = n_after
        lines.append("")

    return "\n".join(lines) + "\n"


def index_literature_rows(literature_dir: Path) -> list[dict[str, str]]:
    """Deterministic citekey-sorted row index over ``literature/`` frontmatter.

    One dict per note, columns drawn STRICTLY from frontmatter (no LLM, no
    invented cells) — the comparison-table's mechanical data source (design
    §4: "table rows byte-deterministic from frontmatter"). Includes the PR-L1
    ``repo``/``artifacts`` fields (empty string when the note predates the
    enrichment or the paper ships no code — never a fabricated value).

    Args:
        literature_dir: the project's ``literature/`` OKF dir.

    Returns:
        Rows sorted by citekey (falls back to filename stem when
        ``citekey:`` is absent — mirrors ``review._index_literature_notes_by_citekey``'s
        F17 convention). Empty list if the dir does not exist.

    sr: PR-M6
    """
    if not literature_dir.exists():
        return []

    rows: list[dict[str, str]] = []
    for note_path in sorted(literature_dir.glob("*.md")):
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fields, _ = _parse_frontmatter(text)
        citekey = str(fields.get("citekey", "")).strip() or note_path.stem
        rows.append({
            "citekey": citekey,
            "title": str(fields.get("title", "")).strip(),
            "year": str(fields.get("year", "")).strip(),
            "venue": str(fields.get("venue", "")).strip(),
            "repo": str(fields.get("repo", "")).strip(),
        })
    rows.sort(key=lambda r: r["citekey"])
    return rows


def render_comparison_table(rows: list[dict[str, str]]) -> str:
    """Render the deterministic comparison-table (design §4) from rows.

    Args:
        rows: as returned by ``index_literature_rows`` — never invented cells.

    Returns:
        A markdown table, byte-deterministic given the same rows.

    sr: PR-M6
    """
    if not rows:
        return (
            "_No `literature/` notes materialized yet — the comparison table "
            "populates as papers are related into the corpus._\n"
        )
    lines = [
        "| Citekey | Title | Year | Venue | Code |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        code = row["repo"] if row["repo"] else "—"
        lines.append(
            f"| {row['citekey']} | {row['title']} | {row['year']} | "
            f"{row['venue']} | {code} |"
        )
    return "\n".join(lines) + "\n"


def render_provenance_header() -> str:
    """Render the RD-3 provenance blockquote (2-3 lines, no hash/no counts).

    RD-3 (next-gen lit-review design §6): a short, mechanical, hash-free
    provenance statement for the top of the assembled document. The corpus
    hash and any reconciliation flags route to the control note / DEVLOG —
    NEVER the manuscript body (the RD-5 reader-hygiene leak-gate structurally
    enforces this: a literal ``sha256:...`` in reader prose is a hard BLOCK).

    This is deliberately static boilerplate, not survey-specific data — the
    per-survey counts/funnel/saturation-stop detail lives in Appendix A
    (``render_prisma_ledger``), never fabricated here.

    ★ NG-6a dependency (flagged, not silently resolved): the appendix's
    PRISMA counts are only as fresh as the frozen ``_corpus.md`` /
    ``coverage_report`` this reads — the known tool-vs-corpus count
    reconciliation bug (green-but-stale after a remediation append) is fixed
    by NG-6a's ``rv review refresh`` verb (Wave C), NOT by this wave. RD-3's
    appendix-move must not ship as if it silently fixed that bug — it only
    relocates WHERE an (already-correct-or-not) count is displayed.

    sr: NG-lit-review-waveB (RD-3)
    """
    return (
        "> This survey follows a pre-registered protocol — frozen inclusion/"
        "exclusion criteria, a documented multi-source search and snowball "
        "process, and a saturation-verified stopping rule. The full audit "
        "trail (PRISMA funnel, corpus provenance, any scope deviations) is "
        "in Appendix A and the project's control note."
    )


def source_transform(
    project: str,
    project_notes_dir: Path,
    tree_root: Path,
    spine: dict[str, Any],
    *,
    config: Config | None = None,
) -> dict[str, Any]:
    """The lit-review OKF -> survey transformation (design §4).

    Mechanical pieces (deterministic, zero-hallucination) computed here as
    DATA for the writer briefs to consume (results-inject discipline extended
    to the survey's structural artifacts — never let the LLM type a citekey,
    a count, or a table cell):
      - the PRISMA ledger (``render_prisma_ledger``, via ``review.coverage_report``
        under the ``reviews/<slug>/`` convention — manuscript slug == review
        scope id; degrades to an honest "no corpus" ledger otherwise) — RD-3:
        injected into the ``appendix-methods`` section, NOT a body section.
      - the comparison-table rows (``index_literature_rows`` /
        ``render_comparison_table``)
      - the frozen framework's branches (from ``spine`` — the `_manuscript.md`
        fields written at ``approve-framework``)
      - RD-3: a hash-free ``provenance_header`` blockquote for the top of the
        assembled document (``render_provenance_header``).

    Args:
        project: project slug.
        project_notes_dir: the project's OKF notes root.
        tree_root: this manuscript's folder (``manuscripts/<slug>/``).
        spine: the frozen spine fields (``spine_shape``, ``branches``) as read
            from ``_manuscript.md`` frontmatter — ``{}`` if not yet approved.
        config: optional Config.

    Returns:
        dict keyed by section name -> markdown/data ready for injection into
        that section's writer brief (consumed once PR-M2's hermetic build and
        the core's section-drafting wiring read this field — recorded here
        now so the transform is complete per the type contract, design §1).

    sr: PR-M6; RD-3 appendix-move (NG-lit-review-waveB)
    """
    slug = tree_root.name

    coverage: dict[str, Any] = {}
    corpus_path = project_notes_dir / "reviews" / slug / "_corpus.md"
    if corpus_path.exists():
        from research_vault.review import coverage_report
        try:
            coverage = coverage_report(project, slug, config=config)
        except Exception:
            coverage = {}

    rows = index_literature_rows(project_notes_dir / "literature")

    branches_raw = spine.get("branches", []) if spine else []
    if isinstance(branches_raw, str):
        branches = [b.strip() for b in branches_raw.split(",") if b.strip()]
    else:
        branches = list(branches_raw)

    deviations_path = project_notes_dir / "reviews" / slug / "_deviations.md"

    return {
        "appendix-methods": render_prisma_ledger(coverage, deviations_path=deviations_path),
        "provenance_header": render_provenance_header(),
        "references": render_comparison_table(rows),
        "framework_branches": branches,
        "spine_shape": spine.get("spine_shape", "") if spine else "",
    }


# ---------------------------------------------------------------------------
# §3.1 — the structurally-binding thematic-section brief contract
# ---------------------------------------------------------------------------

_THEMATIC_BRIEF = (
    "Draft the thematic sections — one per the frozen framework's top-level "
    "branches (`branches:` in `_manuscript.md`, approved at `approve-framework`). "
    "This brief is STRUCTURALLY BINDING (design §3.1 — the §5J rule, generalized "
    "to every thematic section):\n\n"
    "1. FORBID the per-paper paragraph. A paragraph citing exactly ONE source "
    "with no comparison is an annotated-bibliography unit, not a survey — the "
    "review board's SYNTHESIS-VS-ENUMERATION adversary (design §11.2) flags "
    "this as a single-cite ¶ and scores it LOW on SYNTH. Never write one.\n"
    "2. REQUIRE a theme-claim + AT LEAST TWO papers compared per synthesis "
    "unit: `claim -> the >=2 papers marshalled -> the critical comparison "
    "(which wins where, and why)`. The claim comes from a `concepts/` atom; "
    "the papers from that concept's linked `literature/` notes.\n"
    "3. Relationships ('X builds on Y', 'X contradicts Y') are drawn ONLY from "
    "note link-fields — `role`/`position` (PR-4) plus the typed paper→paper "
    "edges a `## Related papers` section carries (`[SUPPORTS]/[CONTRADICTS]/"
    "[PARTIAL]/[EXTENDS]`, Wave 0 PR-2 — run `rv review <project> relations "
    "<scope>` for the deterministic listing rather than re-deriving from "
    "prose) — NEVER invented. The support-matcher (PR-M3) re-fires this.\n"
    "4. Every cited claim carries a provenance pointer to its source note(s) — "
    "the citation-fidelity floor (PR-M3, design §10).\n"
    "5. Voice comes from few-shot REAL excerpts (design §8, PR-M7/M8), not a "
    "prose description of 'write in a synthesis style' — once the exemplar "
    "bundle lands, imitate the MOVE the excerpts demonstrate, never the words.\n"
    "6. Reproduce PIVOTAL equations: where a claim turns on a source note's "
    "critical equation (`key_equations:` with `critical: true`), reproduce it "
    "as block LaTeX (`\\begin{equation}...\\end{equation}`), not prose "
    "paraphrase (design §7, PR-M4).\n\n"
    "Anti-pattern this brief exists to forbid: 'Smith et al. (2023) showed X. "
    "Jones et al. (2022) showed Y. Lee et al. (2021) showed Z.' — three "
    "uncompared per-paper sentences in a row. Instead: 'Claim: <theme>. Smith "
    "and Jones both address <theme>, but Smith's <method> outperforms Jones's "
    "<method> on <axis> because <reason>; Lee's later work resolves neither.'"
)

STYLE_BRIEFS: dict[str, str] = {
    "introduction": (
        "RD-2 (reader-first): open on the THESIS, not the methods — the "
        "survey's central claim and why-now, in the first paragraph. Bold "
        "the topic sentence (RD-6). Do NOT lead with corpus size, search "
        "process, or a methods preamble — that is Appendix A's job now.\n\n"
        "RD-4: render ONLY a compact 'spine at a glance' orientation table "
        "(the frozen `spine_shape:`/`branches:` from `_manuscript.md`, "
        "approve-framework) + a 2-3 sentence organizing line explaining why "
        "this shape fits the corpus. Do NOT write a 'why this spine over "
        "rejected candidates' section — that reasoning stays internal, in "
        "`_framework-candidates.md` (never re-derive or alter the frozen "
        "shape here; the candidate-rejection defense is not reader-facing).\n\n"
        "Draw scope framing from `mocs/` and open questions from `gaps/` — "
        "never invent a gap not anchored in a real `gaps/` note. Preview the "
        "contributions, then hand off directly into the thematic sections — "
        "no PRISMA/methods detour (RD-2/RD-3)."
    ),
    "thematic-sections": _THEMATIC_BRIEF,
    "cross-cutting-analysis": (
        "Synthesize cross-cutting trends and tensions across the thematic "
        "sections — what the field collectively knows and where it disagrees. "
        "Same anti-enumeration discipline as the thematic-sections brief: no "
        "single-cite ¶, claims marshal >=2 papers, relationships only from "
        "note link-fields — never invented."
    ),
    "open-problems": (
        "Surface open problems and future directions ANCHORED to the "
        "framework: gaps entailed by empty cells or under-served branches in "
        "the frozen spine, drawn from real `gaps/` notes. Loose gaps not "
        "anchored to any branch are surfaced as such, never silently pasted "
        "in as if framework-derived (the review board's SYNTHESIS-VS-ENUMERATION "
        "adversary, design §11.2, flags an unanchored gap under GAP)."
    ),
    "conclusion": (
        "Restate the thesis against what the survey actually showed — no new "
        "claims here, only a synthesis of what was already argued in the "
        "thematic sections and cross-cutting analysis."
    ),
    "references": (
        "RD-1: this is `## Sources`, MECHANICAL not prose — the reference "
        "list is built from the hermetic citekey ledger (`refs.bib`, from "
        "`literature/` frontmatter) — never hand-type or invent an entry. "
        "Use the injected comparison-table citekey list verbatim. Cite in "
        "the body with `[[citekey]]` markdown wikilinks, never `\\cite{}` "
        "(RD-1 retires tex-macro citations from the reader path)."
    ),
    "appendix-methods": (
        "RD-3: render Appendix A — the full methods/audit-trail record "
        "(inclusion/exclusion criteria, PRISMA funnel table, saturation "
        "stop, counter-position list) from the injected PRISMA ledger "
        "(mechanical — counts come from `rv review coverage`, never "
        "estimated by you). This is the relocated `prisma-scope` content — "
        "reader-optional, never the reader's entry point. Do NOT include a "
        "corpus hash or any raw `sha256:` value here or anywhere in the "
        "reader body — hashes and reconciliation flags live in the control "
        "note / DEVLOG only (RD-3; the reader-hygiene leak-gate, RD-5, "
        "BLOCKs on a literal hash leaking into this document). Name every "
        "counter-position by its actual argument, never by an internal `CPk` "
        "handle (RD-6)."
    ),
    "abstract": (
        "Write the abstract LAST, after every other section is drafted — it "
        "is a one-sentence thesis + framework preview and MUST be a strict "
        "subset of claims already made in the body (the support-matcher, "
        "PR-M3, gates this: an abstract claim absent from the body is a "
        "fidelity failure). Never introduce a new claim here."
    ),
    "assemble": (
        "RD-1: join the drafted sections into `report.md` (markdown, not "
        "`main.tex`) in READER-FIRST reading order (RD-2): Abstract, "
        "Introduction (thesis + spine-at-a-glance), Thematic sections, "
        "Cross-cutting analysis, Open problems, Conclusion, Sources "
        "(References), Appendix A (Appendix-methods) — even though Abstract "
        "and Appendix-methods were DRAFTED in a different chain order "
        "(Abstract last, so it could summarize the finished body). Prepend "
        "the injected `provenance_header` blockquote (RD-3, hash-free) as "
        "the very first lines of `report.md`, before the Abstract. Do not "
        "reorder or drop a section."
    ),
}


# ---------------------------------------------------------------------------
# §3/§6 — the survey's reader-first 8-row section-set (RD-2/RD-4)
# ---------------------------------------------------------------------------
# Chain order (this tuple) is the Phase-2 DAG's drafting order (each afterok
# the previous) — NOT the final document order (see the "assemble" brief
# above). Abstract is drafted LAST (assembly class "S (last)", design §3):
# it must be a subset of the finished body, so it needs the body written
# first. References/appendix-methods are mechanical (M) and have no prose
# dependency, so they run right before Abstract for simplicity.
#
# RD-2/RD-4 (next-gen lit-review design §6): pre-Wave-B this tuple had 9 rows
# including `prisma-scope` and `framework` as BODY sections — a reader
# traversed ~475 lines of methodology/framework internals before the first
# survey sentence. Both are removed as body rows here:
#   - `prisma-scope` -> relocated to `appendix-methods` (RD-3), rendered LAST
#     in the READING order (see the "assemble" brief) — reader-optional.
#   - `framework` -> DELETED entirely (RD-4); the spine is now shown by
#     SECTION ORDER + a compact orientation table folded into `introduction`
#     (no "why this spine over rejected candidates" body section — that
#     defense stays internal, in `_framework-candidates.md`).
# Net: 9 -> 8 rows. `introduction` now leads on the thesis, not the corpus.
#
# "Thematic sections (N)" (design §3 row 5) is represented here as ONE
# section node covering all N branches (the frozen framework's top-level
# branches are read from `_manuscript.md` at draft time — see the
# thematic-sections brief). True per-branch DAG fan-out (a separate node per
# branch, §3's "N derived... not a free parameter") would require the
# type-generic core's Phase-2 builder to accept a per-manuscript dynamic
# section-set — that is core-level work out of PR-M6's scope (design table:
# "Section-set + assembly classes" is type-specific, but the FAN-OUT
# mechanism that would read a per-manuscript N is the core's Phase-2 builder,
# untouched here per the parallel-wave scope discipline). Documented honestly
# as the current simplification, not silently assumed.
SECTION_SET: tuple[SectionSpec, ...] = (
    SectionSpec(
        name="introduction",
        assembly_class="S",
        source_atoms=("mocs", "gaps"),
        brief_key="introduction",
    ),
    SectionSpec(
        name="thematic-sections",
        assembly_class="S",
        source_atoms=("concepts", "literature"),
        brief_key="thematic-sections",
    ),
    SectionSpec(
        name="cross-cutting-analysis",
        assembly_class="S",
        source_atoms=("concepts", "mocs"),
        brief_key="cross-cutting-analysis",
    ),
    SectionSpec(
        name="open-problems",
        assembly_class="S",
        source_atoms=("gaps",),
        brief_key="open-problems",
    ),
    SectionSpec(
        name="conclusion",
        assembly_class="S",
        source_atoms=(),
        brief_key="conclusion",
    ),
    SectionSpec(
        name="references",
        assembly_class="M",
        source_atoms=("literature",),
        brief_key="references",
    ),
    SectionSpec(
        name="appendix-methods",
        assembly_class="M",
        source_atoms=("literature", "reviews"),
        brief_key="appendix-methods",
    ),
    SectionSpec(
        name="abstract",
        assembly_class="S",
        source_atoms=(),
        brief_key="abstract",
    ),
)


# ---------------------------------------------------------------------------
# NG-7 (next-gen lit-review design §2): single-pass Phase-2 — outline -> draft
# -> assemble, replacing the type-generic per-section chain. SECTION_SET +
# STYLE_BRIEFS above stay the SOURCE DATA (one writer's brief now
# CONSOLIDATES them, rather than one DAG node per section — design §2.6:
# "the mechanical injections that were spread across per-section briefs ...
# now inject into ONE brief + the outline").
# ---------------------------------------------------------------------------

# The engineer's build-time number (design §2.4, D3: "start conservative,
# e.g. the point where the whole draft + injected inputs approaches the
# drafter's context budget"). Override via research_vault.toml:
#   [manuscript_lit_review]
#   single_pass_corpus_ceiling = 60
_DEFAULT_SINGLE_PASS_CORPUS_CEILING = 40

# RD-2's reader-first reading order (§6) — the order the consolidated draft
# brief presents each section's contract in, and the order `assemble` joins
# them in `report.md`. Abstract is listed first here (reading order) even
# though it is drafted conceptually last within the single pass (it must
# summarize the finished body — the single-pass writer holds the whole
# survey in view, so "drafted last" is a sequencing note inside one prompt,
# not a separate DAG node).
READING_ORDER: tuple[str, ...] = (
    "abstract", "introduction", "thematic-sections", "cross-cutting-analysis",
    "open-problems", "conclusion", "references", "appendix-methods",
)

# RD-6 (design §6) + HR-craft rec 1 (§7): drafting-style rules folded into
# the single consolidated draft brief (was spread across the 9-node chain's
# individual briefs pre-Wave-B).
_RD6_STYLE_RULES = (
    "Drafting-style rules (RD-6, binding across every section of this "
    "single pass):\n"
    "1. BOLD topic sentences — the first sentence of each paragraph states "
    "its claim in bold, not buried after the evidence.\n"
    "2. Define technical terms INLINE on first use — an "
    "undefined term the reader must chase elsewhere is a self-containment "
    "failure the review board's coherence scoring will catch.\n"
    "3. Prefer shorter paragraphs / bullets for enumerations over long "
    "unbroken prose blocks.\n"
    "4. Name every counter-position INLINE by its actual argument — 'X "
    "argue instead that...' — NEVER by an internal handle (`CPk`). The "
    "reader-hygiene leak-gate (RD-5) BLOCKs a literal `CPk`/`Qk` handle.\n\n"
    "HR-craft rec 1 — integrate-by-scoping, don't append-as-caveat: when "
    "counter-evidence lands, NARROW the claim's scope ('X holds in A; in B, "
    "Z changes the regime') instead of hedging ('X, though this may "
    "differ'). A narrowed claim sharpens the thesis; a hedge dissolves it — "
    "always prefer the former."
)


def _get_single_pass_corpus_ceiling(config: Any = None) -> int:
    """Resolve ``single_pass_corpus_ceiling`` (design §2.4, D3).

    Adopter override: ``[manuscript_lit_review] single_pass_corpus_ceiling``.
    Falls back to the conservative shipped default.

    sr: NG-lit-review-waveB (NG-7)
    """
    if config is not None:
        raw = getattr(config, "_raw", {})
        override = raw.get("manuscript_lit_review", {})
        if isinstance(override, dict):
            val = override.get("single_pass_corpus_ceiling")
            if isinstance(val, int) and val > 0:
                return val
    return _DEFAULT_SINGLE_PASS_CORPUS_CEILING


def _corpus_size(project_notes_dir: Path, slug: str) -> int:
    """Frozen corpus size (0 if no ``_corpus.md`` exists yet — honest, not an error)."""
    corpus_path = project_notes_dir / "reviews" / slug / "_corpus.md"
    if not corpus_path.exists():
        return 0
    from research_vault.review import _parse_corpus_citekeys

    return len(_parse_corpus_citekeys(corpus_path))


def render_relations_ledger(
    project: str,
    slug: str,
    *,
    config: Any = None,
) -> str:
    """PR-2's consume seam (Wave 0) for the single-pass draft brief (NG-7).

    Traverses the corpus-wide paper->paper typed-edge listing
    (``review.relations_report``) — mechanical, zero-hallucination DATA the
    drafter reads instead of re-deriving the comparative spine from prose
    (design §5's net simplification: "less inference, more structure").

    ``relations_report`` unavailable/erroring (e.g. no literature/ dir yet)
    degrades to an honest empty-ledger note, never an exception that blocks
    the whole manifest build.

    sr: NG-lit-review-waveB (NG-7)
    """
    from research_vault.review import relations_report

    try:
        report = relations_report(project, slug, config=config)
    except Exception:
        return (
            "_No paper->paper typed-edge data available yet "
            "(review.relations_report unavailable for this scope)._\n"
        )

    edges = report.get("edges", [])
    if not edges:
        return (
            "_No paper->paper typed edges found yet in this corpus "
            "(Wave 0 PR-2 — run `rv review <project> relate-check` on the "
            "corpus to populate them)._\n"
        )

    lines = [
        "## Paper -> paper typed edges (PR-2, Wave 0 — TRAVERSE, do not re-derive)\n",
        "Every claim comparing >=2 papers should ground its relation in one "
        "of these typed edges (or a note's own `role`/`position` fields), "
        "never invented:\n",
    ]
    for e in edges:
        lines.append(f"- [{e['type']}] {e['source']} -> {e['target']}: {e['reason']}")

    dangling = report.get("dangling", [])
    if dangling:
        lines.append(
            "\n_Dangling edges (target citekey not found in this project's "
            "corpus — a candidate typo/uningested paper, SIGNAL only, do "
            "NOT treat as a real relation):_"
        )
        for d in dangling:
            lines.append(f"- {d['source']} -> {d['target']}")

    return "\n".join(lines) + "\n"


def check_outline_gate(outline_path: Path, branches: list[str]) -> list[str]:
    """NG-7's cheap, rejects-only outline pre-pass gate (design §2.2/§3.2).

    A cheap screen that can only REJECT (charter §9): before the expensive
    whole-draft runs, confirm every frozen branch is anchored to something
    real in ``_outline.md`` — surfacing a framework/corpus problem in
    minutes, not after a full draft.

    Checks, per frozen branch:
      1. the branch name appears in the outline (an anchored thesis-claim).
      2. SOMEWHERE in the outline, an exemplar-move citation is present
         (design §3.2's enforcement hook — an ``eNN`` id reference, e.g.
         "imitates e07") — an outline section with no exemplar-move
         reference is incomplete.
      3. at least 2 distinct ``[[citekey]]`` paper references appear overall
         (design §2.2: "the >=2 papers it will compare").

    Args:
        outline_path: path to ``_outline.md``.
        branches: the frozen ``branches:`` list from ``_manuscript.md``.

    Returns:
        A list of finding strings (empty = OK). Never raises — a missing
        file is a finding, not an exception (mirrors the OKF-type/relate-
        presence gate's structural posture).

    sr: NG-lit-review-waveB (NG-7)
    """
    import re

    if not outline_path.exists():
        return [
            f"outline gate: {outline_path} not found — the outline pre-pass "
            f"must produce _outline.md before `draft` may proceed (design §2.2)."
        ]

    try:
        text = outline_path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"outline gate: cannot read {outline_path}: {e}"]

    text_lower = text.lower()
    issues: list[str] = []

    for branch in branches:
        b = str(branch).strip()
        if not b:
            continue
        if b.lower() not in text_lower:
            issues.append(
                f"outline gate: frozen branch {b!r} has no corresponding entry "
                f"in {outline_path.name} — every frozen branch must be anchored "
                f"to a thesis-claim before drafting proceeds."
            )

    if branches and not re.search(r"\be\d+\b", text_lower):
        issues.append(
            "outline gate: no exemplar-move citation (e.g. 'imitates e07') "
            "found anywhere in the outline — design §3.2's enforcement hook "
            "requires each section name the exemplar id whose move it imitates."
        )

    wikilink_count = len(re.findall(r"\[\[[\w.\-]+\]\]", text))
    if branches and wikilink_count < 2:
        issues.append(
            f"outline gate: fewer than 2 [[citekey]] paper references found "
            f"({wikilink_count}) — each thematic branch must marshal >=2 "
            f"papers to compare (design §2.2)."
        )

    return issues


def _build_consolidated_draft_brief(tips: dict[str, str]) -> str:
    """Consolidate the per-section tips (RD-2's reading order) into ONE
    single-pass draft brief (design §2.2/§2.6): "the mechanical injections
    ... now inject into ONE brief + the outline"."""
    parts: list[str] = []
    for key in READING_ORDER:
        if key in tips:
            parts.append(f"### Section: {key}\n\n{tips[key]}")
    parts.append(f"### Drafting-style rules\n\n{_RD6_STYLE_RULES}")
    return "\n\n---\n\n".join(parts)


def phase2_builder(
    *,
    project: str,
    slug: str,
    project_notes_dir: Path,
    tree_root: Path,
    manuscript_fields: dict[str, Any] | None = None,
    config: Any = None,
) -> dict[str, Any]:
    """NG-7: build the single-pass Phase-2 manifest — outline -> draft ->
    assemble -> [HG: approve-manuscript] (design §2.2).

    Default topology (corpus at/under ``single_pass_corpus_ceiling``, D3):
      outline -> draft -> assemble -> approve-manuscript

    Above the ceiling (D3's fan-out path, design §2.4): drafting itself fans
    out per-branch, with a coherence node (label-manifest check, design
    §2.5) that reads all branch drafts and revises for cross-section
    consistency before assemble:
      outline -> draft-<branch-1> ... draft-<branch-N> -> coherence -> assemble
      -> approve-manuscript

    Matches the ``ManuscriptType.phase2_builder`` signature
    (``manuscript/types/__init__.py``).

    sr: NG-lit-review-waveB (NG-7)
    """
    from research_vault.manuscript.style import (
        get_manuscript_section_tips,
        get_manuscript_style_preamble,
    )
    from research_vault.manuscript import equations as _equations
    from research_vault.manuscript import exemplars as _exemplars
    from research_vault.manuscript import _inject_source_transform_tips

    tips = get_manuscript_section_tips(LIT_REVIEW, config=config)
    preamble = get_manuscript_style_preamble(config=config)

    if LIT_REVIEW.equation_sources:
        ledger = _equations.extract_equation_ledger(project_notes_dir, LIT_REVIEW.equation_sources)
        tips = _equations.inject_equation_brief(
            tips, ledger, LIT_REVIEW.section_set, LIT_REVIEW.equation_sources
        )

    spine = {
        "spine_shape": (manuscript_fields or {}).get("spine_shape", ""),
        "branches": (manuscript_fields or {}).get("branches", ""),
    }
    transform = source_transform(project, project_notes_dir, tree_root, spine, config=config)
    tips = _inject_source_transform_tips(tips, transform)

    exemplar_blocks: list[dict[str, Any]] = []
    if LIT_REVIEW.exemplar_bundle:
        exemplar_blocks = _exemplars.load_exemplar_bundle(LIT_REVIEW.exemplar_bundle)
        tips = _exemplars.inject_exemplar_briefs(tips, exemplar_blocks)
        principle_block = _exemplars.build_principle_anchor_block(exemplar_blocks)
        if principle_block:
            preamble = preamble.rstrip() + "\n\n---\n\n" + principle_block
        # NG-8 §3.3: the same pre-dispatch presence assertion as the
        # generic-chain path — a hand-rolled consolidated brief that
        # dropped a mapped section's pointer fails loudly here too.
        for section_key in tips:
            ok, msg = _exemplars.check_exemplar_pointer_presence(section_key, tips[section_key], exemplar_blocks)
            if not ok:
                raise ValueError(f"rv manuscript expand: {msg}")

    branches_raw = spine.get("branches", [])
    if isinstance(branches_raw, str):
        branches = [b.strip() for b in branches_raw.split(",") if b.strip()]
    else:
        branches = list(branches_raw)

    relations_ledger = render_relations_ledger(project, slug, config=config)

    def _afterok(from_id: str) -> dict[str, str]:
        return {"from": from_id, "edge": "afterok"}

    def _rel(okf_type: str) -> str:
        return str(project_notes_dir / okf_type)

    exemplar_bundle_dir = _exemplars.resolve_exemplar_bundle_path(LIT_REVIEW.exemplar_bundle)
    sections_dir_abs = str(tree_root / "sections")
    outline_path = tree_root / "_outline.md"

    all_source_atoms = sorted({atom for s in LIT_REVIEW.section_set for atom in s.source_atoms})
    common_reads = [_rel(atom) for atom in all_source_atoms] + [sections_dir_abs]
    if exemplar_bundle_dir is not None:
        common_reads = common_reads + [str(exemplar_bundle_dir)]

    outline_spec = (
        preamble.rstrip() + "\n\n---\n\n"
        "Outline pre-pass (NG-7, design §2.2) — a CHEAP, rejects-only screen. "
        "For EACH frozen branch (`branches:` in `_manuscript.md`), write a "
        "block in `_outline.md` naming: (1) the section's thesis-claim, (2) "
        "the `concepts/`/`gaps/` anchors it will marshal, (3) the >=2 papers "
        "it will compare (as `[[citekey]]` wikilinks), and (4) WHICH "
        "exemplar-move it will imitate (cite the exemplar id, e.g. 'imitates "
        "e07 — comparison-synthesis move'). If a branch cannot be anchored "
        "to real concepts/gaps, FAIL here — surface the framework/corpus "
        "problem now, before the expensive whole-draft.\n\n"
        f"Frozen branches: {', '.join(branches) if branches else '(none frozen yet)'}"
    )
    draft_brief = _build_consolidated_draft_brief(tips)
    draft_spec = (
        preamble.rstrip() + "\n\n---\n\n"
        "Single-pass whole-survey draft (NG-7, design §2.2): ONE subagent "
        "drafts EVERY section below against `_outline.md`, holding the "
        "entire survey in view for coherence. Read `_outline.md` first — "
        "every frozen branch must already be anchored there (the outline "
        "gate FAILs otherwise).\n\n"
        + relations_ledger
        + "\n---\n\n"
        + draft_brief
    )
    assemble_spec = tips.get(
        "assemble",
        (
            "RD-1: join the drafted sections into `report.md` (markdown) in "
            "READER-FIRST reading order (RD-2): " + ", ".join(READING_ORDER) + ". "
            "Prepend the injected `provenance_header` blockquote (RD-3, "
            "hash-free) as the very first lines, before the Abstract."
        ),
    )

    corpus_size = _corpus_size(project_notes_dir, slug)
    ceiling = _get_single_pass_corpus_ceiling(config)

    nodes: list[dict[str, Any]] = [
        {
            "id": "outline",
            "type": "agent",
            "label": "Outline pre-pass — cheap, rejects-only (NG-7)",
            "spec": outline_spec,
            "reads": common_reads,
            "produces": {"_outline.md": str(outline_path)},
            "needs": [],
        },
    ]

    if corpus_size <= ceiling:
        # Default single-pass path (D3).
        nodes.append({
            "id": "draft",
            "type": "agent",
            "label": "Single-pass whole-survey draft (NG-7)",
            "spec": draft_spec,
            "reads": common_reads,
            "needs": [_afterok("outline")],
        })
        last_draft_id = "draft"
    else:
        # D3's fan-out-above-ceiling path (design §2.4): per-branch drafters
        # + a coherence node that reads all of them + a label-manifest check
        # (design §2.5 — the residual `\label`/`\ref` drift case).
        branch_ids: list[str] = []
        for branch in branches or ["survey"]:
            branch_slug = "".join(c if c.isalnum() or c == "-" else "-" for c in branch.lower())
            node_id = f"draft-{branch_slug}"
            nodes.append({
                "id": node_id,
                "type": "agent",
                "label": f"Fan-out draft for branch {branch!r} (above single_pass_corpus_ceiling, NG-7 §2.4)",
                "spec": draft_spec + f"\n\n---\n\nYou are drafting ONLY the {branch!r} branch this pass.",
                "reads": common_reads,
                "needs": [_afterok("outline")],
            })
            branch_ids.append(node_id)

        nodes.append({
            "id": "coherence",
            "type": "agent",
            "label": "Coherence pass — cross-section consistency + label-manifest check (NG-7 §2.5)",
            "spec": (
                preamble.rstrip() + "\n\n---\n\n"
                "Coherence pass (design §2.4/§2.5): read every branch draft "
                "under `sections/`, revise for cross-section consistency, and "
                "run the LABEL-MANIFEST CHECK — every `\\label{}`/`[[#anchor]]` "
                "a section declares vs. every one another section refs must "
                "match (a fan-out drift is caught HERE, not at compile). This "
                "check is ONLY required on this fan-out path — the default "
                "single-pass needs no label manifest (design §2.5)."
            ),
            "reads": common_reads,
            "needs": [_afterok(bid) for bid in branch_ids],
        })
        last_draft_id = "coherence"

    nodes.append({
        "id": "assemble",
        "type": "agent",
        "label": "Assemble — join drafted sections into report.md (RD-1)",
        "spec": preamble.rstrip() + "\n\n---\n\n" + assemble_spec,
        "reads": [sections_dir_abs],
        "needs": [_afterok(last_draft_id)],
    })

    nodes.append({
        "id": "approve-manuscript",
        "type": "human-go",
        "label": (
            "Gate: Approve manuscript draft (gated by "
            "manuscript/check_gates.py::build_approve_payload)"
        ),
        "needs": [_afterok("assemble")],
    })

    return {
        "run_id": f"manuscript-{slug}-phase2",
        "project": project,
        "name": f"Manuscript Phase-2 (lit-review, single-pass NG-7): {slug}",
        "global_cap": 1,
        "nodes": nodes,
    }


# ---------------------------------------------------------------------------
# The registered type
# ---------------------------------------------------------------------------

LIT_REVIEW = ManuscriptType(
    key="lit-review",
    section_set=SECTION_SET,
    phase1_builder=phase1_builder,          # design §5 — framework selection
    source_transform=source_transform,      # design §4 — OKF -> survey transform
    equation_sources=("concepts", "literature"),  # design §7 — consumed starting PR-M4
    style_briefs=STYLE_BRIEFS,               # design §3.1
    exemplar_bundle="lit-review",            # PR-M8: data/exemplars/manuscript/lit-review/
    rubric=None,                             # PR-M8: DEFAULT_LIT_REVIEW_RUBRIC (design §11.1)
    # PR-M5 (design §11.2): the 3 fresh reviewer lenses — coverage/scope
    # auditor, framework/taxonomy critic (WITH the reframe-escalation
    # trigger), synthesis-vs-enumeration adversary. PLACEHOLDER wording;
    # PR-M8 replaces with the researcher's authored lens prose — the lens STRUCTURE
    # (which dims each attacks, the escalation trigger) is locked here.
    reviewer_lenses=(
        "coverage-scope-auditor",
        "framework-taxonomy-critic",
        "synthesis-vs-enumeration-adversary",
    ),
    canaries=(),                             # PR-M8: strong / weak / annotated-bib (§11.3)
    phase2_builder=phase2_builder,          # NG-7 — single-pass outline->draft->assemble
)

register_type(LIT_REVIEW)
