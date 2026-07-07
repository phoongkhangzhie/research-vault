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

def render_prisma_ledger(coverage: dict[str, Any]) -> str:
    """Render a PRISMA-style inclusion/exclusion ledger from a coverage report.

    ``coverage`` is the dict shape returned by ``review.coverage_report()``
    (F16+F17: keyed by citekey; ``counts`` summary). Byte-deterministic —
    no LLM, no invented numbers; this is a sibling to ``coverage_report``
    itself (design §4).

    Args:
        coverage: a ``review.coverage_report()``-shaped dict, or ``{}`` if no
            frozen corpus exists yet (renders an honest "no corpus" ledger).

    Returns:
        Markdown PRISMA-style ledger.

    sr: PR-M6
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
        scope id; degrades to an honest "no corpus" ledger otherwise)
      - the comparison-table rows (``index_literature_rows`` /
        ``render_comparison_table``)
      - the frozen framework's branches (from ``spine`` — the `_manuscript.md`
        fields written at ``approve-framework``)

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

    sr: PR-M6
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

    return {
        "prisma-scope": render_prisma_ledger(coverage),
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
    "cold-read critic (PR-M3) flags this as a single-cite ¶ (SIGNAL, dim 4). "
    "Never write one.\n"
    "2. REQUIRE a theme-claim + AT LEAST TWO papers compared per synthesis "
    "unit: `claim -> the >=2 papers marshalled -> the critical comparison "
    "(which wins where, and why)`. The claim comes from a `concepts/` atom; "
    "the papers from that concept's linked `literature/` notes.\n"
    "3. Relationships ('X builds on Y', 'X contradicts Y') are drawn ONLY from "
    "note link-fields (`stance`, `[SUPPORTS]/[CONTRADICTS]/[PARTIAL]` edges) — "
    "NEVER invented. The support-matcher (PR-M3) re-fires this.\n"
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
        "Introduce the survey's scope and why-now, previewing the organizing "
        "framework (`spine_shape:`/`branches:` — already frozen at "
        "`approve-framework`, never re-derive it here) and stating the "
        "contributions. Draw scope framing from `mocs/` and open questions "
        "from `gaps/` — never invent a gap not anchored in a real `gaps/` note."
    ),
    "prisma-scope": (
        "Render the PRISMA-style scope & method section from the injected "
        "PRISMA ledger (mechanical — counts come from `rv review coverage`, "
        "never estimated by you): inclusion/exclusion criteria, corpus "
        "assembly process, and the counts table. State the corpus hash "
        "verbatim from `_manuscript.md`'s `corpus_hash:` field — never "
        "recompute or approximate it."
    ),
    "framework": (
        "Write up the organizing framework/taxonomy section. The spine is "
        "ALREADY FROZEN (`spine_shape:`+`branches:` in `_manuscript.md`, "
        "written at `approve-framework`) — render it faithfully (the figure/ "
        "table + its defense from the MOCs), do NOT alter or re-derive the "
        "shape here. If the frozen spine is nested (D5's 'bigger spine'), "
        "render the nesting explicitly."
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
        "in as if framework-derived (the cold-read critic flags an "
        "unanchored gap, PR-M3 dim 6)."
    ),
    "conclusion": (
        "Restate the thesis against what the survey actually showed — no new "
        "claims here, only a synthesis of what was already argued in the "
        "thematic sections and cross-cutting analysis."
    ),
    "references": (
        "This section is MECHANICAL, not prose: the reference list is built "
        "from the hermetic `.bib` (PR-M2, from `literature/` frontmatter) — "
        "never hand-type or invent an entry. Until PR-M2's build lands, use "
        "the injected comparison-table citekey list verbatim."
    ),
    "abstract": (
        "Write the abstract LAST, after every other section is drafted — it "
        "is a one-sentence thesis + framework preview and MUST be a strict "
        "subset of claims already made in the body (the support-matcher, "
        "PR-M3, gates this: an abstract claim absent from the body is a "
        "fidelity failure). Never introduce a new claim here."
    ),
    "assemble": (
        "Join the drafted sections into `main.tex` in reading order: Abstract, "
        "Introduction, PRISMA scope & method, Framework, Thematic sections, "
        "Cross-cutting analysis, Open problems, Conclusion, References — even "
        "though Abstract and References were DRAFTED in a different order "
        "(Abstract last, so it could summarize the finished body; References "
        "mechanically from the `.bib`). Do not reorder or drop a section."
    ),
}


# ---------------------------------------------------------------------------
# §3 — the survey's real 9-row section-set
# ---------------------------------------------------------------------------
# Chain order (this tuple) is the Phase-2 DAG's drafting order (each afterok
# the previous) — NOT the final document order (see the "assemble" brief
# above). Abstract is drafted LAST (assembly class "S (last)", design §3):
# it must be a subset of the finished body, so it needs the body written
# first. References is mechanical (M) and has no prose dependency, so it
# runs right before Abstract for simplicity.
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
        name="prisma-scope",
        assembly_class="M",
        source_atoms=("literature", "reviews"),
        brief_key="prisma-scope",
    ),
    SectionSpec(
        name="framework",
        assembly_class="H",
        source_atoms=("mocs", "concepts", "gaps"),
        brief_key="framework",
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
        name="abstract",
        assembly_class="S",
        source_atoms=(),
        brief_key="abstract",
    ),
)


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
)

register_type(LIT_REVIEW)
