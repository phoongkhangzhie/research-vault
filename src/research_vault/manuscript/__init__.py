"""manuscript — SR-MS-1a: manuscript OKF type + rv manuscript verbs.

The manuscript OKF type is a LaTeX-native POINTER note: metadata + provenance
that points to the LaTeX artifacts in manuscripts/<id>/. Prose lives in .tex;
the note records lineage and provenance.

Provides:
  - cmd_new: scaffold a manuscript OKF note + manuscripts/<id>/ tree + drafting-DAG manifest
  - cmd_list: list manuscript notes for a project

The DAG manifest (5J.2 shape) has 16 nodes by default:
  13 agent sections + 3 human-go gates (approve-thesis, approve-framing, approve-manuscript).
Optional sections (background, ethics-impacts, data-code-availability) are enabled via flags.

Stdlib only.
sr: SR-MS-1a
"""
from __future__ import annotations

import datetime
import importlib.resources
import json
import re
from pathlib import Path
from typing import Any

from research_vault.config import Config, load_config
from research_vault.note import (
    OKF_TYPES,
    _parse_frontmatter,   # noqa: WPS301 (private — in-package reuse)
    _render_frontmatter,  # noqa: WPS301
    scaffold_okf_dirs,
)
from research_vault.manuscript.style import (
    get_active_sections,
    get_section_tips,
    get_style_preamble,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.date.today().isoformat()


def _slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:80] or "manuscript"


def _manuscript_dir(project: str, cfg: Config) -> Path:
    """The OKF note directory for manuscript notes (project_notes_dir/manuscript/)."""
    return cfg.project_notes_dir(project) / "manuscript"


def _manuscripts_tree_root(project: str, ms_id: str, cfg: Config) -> Path:
    """Root of the LaTeX artifact tree: project_notes_dir/manuscripts/<id>/."""
    return cfg.project_notes_dir(project) / "manuscripts" / ms_id


# ---------------------------------------------------------------------------
# DAG manifest building
# ---------------------------------------------------------------------------

def _build_manifest(
    project: str,
    ms_id: str,
    thesis: str,
    scope: list[str],
    project_notes_dir: Path,
    tree_root: Path,
    *,
    include_optional: bool = False,
    include_venue_optional: bool = False,
    section_tip_override: dict[str, str] | None = None,
    style_preamble_override: str | None = None,
    config: "Any | None" = None,
) -> dict[str, Any]:
    """Build the drafting-DAG manifest (§5J.2 shape).

    Returns a manifest dict ready for validate_manifest and JSON serialization.

    Node count by default: 16 (13 required agent sections + 3 human-go gates).
    Optional sections add nodes when enabled.

    reads: pointers are project_root-RELATIVE (fold-in SR-MS-1b) for portability
    and SR-SCOPE convention match. The resolver (reads.py:resolve_reads_pointer)
    handles relative paths via project_root / file_part.
    The scaffolder creates all pointed-to directories so they resolve immediately.
    """
    active_sections = get_active_sections(
        include_optional=include_optional,
        include_venue_optional=include_venue_optional,
    )
    # Wire config= to the style seam (fold-in SR-MS-1b — [manuscript_style] TOML override)
    tips = get_section_tips(override=section_tip_override, config=config)
    preamble = get_style_preamble(override=style_preamble_override, config=config)

    # Project-root-relative paths for reads: pointers (fold-in SR-MS-1b).
    # project_root for reads resolution = project_notes_dir.
    # OKF type-dirs are directly under project_notes_dir → bare "findings" etc.
    # sections/ dir is under manuscripts/<id>/ → "manuscripts/<id>/sections".
    try:
        sections_rel = str((tree_root / "sections").relative_to(project_notes_dir))
        tree_rel = str(tree_root.relative_to(project_notes_dir))
    except ValueError:
        # Fallback: absolute paths if tree_root is outside project_notes_dir
        sections_rel = str(tree_root / "sections")
        tree_rel = str(tree_root)

    def _rel(okf_type: str) -> str:
        """Return project_root-relative path for an OKF type-dir."""
        return okf_type  # e.g. "findings" resolves to project_notes_dir / "findings"

    def _spec(section_key: str) -> str:
        """Build the spec string: preamble + section tip."""
        tip = tips.get(section_key, f"Write the {section_key} section.")
        return preamble.rstrip() + "\n\n---\n\n" + tip

    # ── Reads contracts by section ───────────────────────────────────────────
    # Per §5J.2 gotcha ruling: point at the OKF type-dir + sections/ dir,
    # NOT at specific unwritten .tex files. sections/ exists after scaffolding.
    _sections = sections_rel

    section_reads: dict[str, list[str]] = {
        "gather-scope": [
            _rel("findings"),
            _rel("experiments"),
            _rel("methods"),
            _rel("concepts"),
        ],
        "related-work": [
            _rel("literature"),
            _sections,
        ],
        "background": [
            _rel("concepts"),
            _rel("methods"),
            _sections,
        ],
        "method": [
            _rel("methods"),
            _sections,
        ],
        # SR-MS-AUDIENCE: title node reads abstract + sections (body is known)
        "title": [_sections],
        "experimental-setup": [
            _rel("experiments"),
            _rel("datasets"),
            _sections,
        ],
        "results-discussion": [
            _rel("experiments"),
            _rel("findings"),
            _sections,
        ],
        "limitations": [
            _rel("findings"),
            _sections,
        ],
        "ethics-impacts": [
            _rel("findings"),
            _rel("methods"),
            _sections,
        ],
        "conclusion": [_sections],
        "introduction": [_sections],
        "abstract": [_sections],
        "appendix-repro": [
            _rel("experiments"),
            _sections,
        ],
        "data-code-availability": [
            _rel("experiments"),
            _rel("datasets"),
            _sections,
        ],
        "assemble": [_sections],
        "compile": [tree_rel],
        "critic": [tree_rel],
        # SR-MS-AUDIENCE: cold-read reads the compiled tree (needs rendered .tex)
        "cold-read": [tree_rel],
    }

    # ── Build nodes ──────────────────────────────────────────────────────────
    # The topology follows the §5J.2 diagram:
    # gather-scope → [HG approve-thesis] → related-work → [HG approve-framing]
    #   → method chain → ... → abstract
    # appendix-repro branches off approve-thesis (skips Gate 2)
    # abstract + appendix-repro → assemble → compile → critic → [HG approve-manuscript]
    #
    # Optional sections insert into the chain at their defined positions.

    nodes: list[dict[str, Any]] = []

    # Helper: afterok edge dict
    def _afterok(from_id: str) -> dict[str, str]:
        return {"from": from_id, "edge": "afterok"}

    # 1. gather-scope (no upstream dependencies)
    nodes.append({
        "id": "gather-scope",
        "type": "agent",
        "label": "Gather scope — emit inclusion ledger + draft thesis",
        "spec": _spec("gather-scope"),
        "reads": section_reads["gather-scope"],
        "needs": [],
    })

    # 2. Gate 1: approve-thesis (human-go, downstream of gather-scope)
    nodes.append({
        "id": "approve-thesis",
        "type": "human-go",
        "label": "Gate 1: Approve thesis + inclusion ledger",
        "needs": [_afterok("gather-scope")],
    })

    # 3. appendix-repro (branches off approve-thesis, SKIPS Gate 2)
    if "appendix-repro" in active_sections:
        nodes.append({
            "id": "appendix-repro",
            "type": "agent",
            "label": "Appendix — reproducibility table (machine-populated)",
            "spec": _spec("appendix-repro"),
            "reads": section_reads["appendix-repro"],
            "needs": [_afterok("approve-thesis")],
        })

    # 4. related-work (downstream of approve-thesis)
    if "related-work" in active_sections:
        nodes.append({
            "id": "related-work",
            "type": "agent",
            "label": "Related work — closed .bib, stated deltas",
            "spec": _spec("related-work"),
            "reads": section_reads["related-work"],
            "needs": [_afterok("approve-thesis")],
        })

    # 5. Gate 2: approve-framing (downstream of related-work)
    nodes.append({
        "id": "approve-framing",
        "type": "human-go",
        "label": "Gate 2: Approve framing + related-work relationship table",
        "needs": [_afterok("related-work")],
    })

    # 6. background (OPTIONAL — between approve-framing and method)
    prev_body = "approve-framing"
    if "background" in active_sections:
        nodes.append({
            "id": "background",
            "type": "agent",
            "label": "Background — formalism/notation for Method only",
            "spec": _spec("background"),
            "reads": section_reads["background"],
            "needs": [_afterok(prev_body)],
        })
        prev_body = "background"

    # 7. method
    if "method" in active_sections:
        nodes.append({
            "id": "method",
            "type": "agent",
            "label": "Method — reconciled against results_commit",
            "spec": _spec("method"),
            "reads": section_reads["method"],
            "needs": [_afterok(prev_body)],
        })
        prev_body = "method"

    # 8. experimental-setup
    if "experimental-setup" in active_sections:
        nodes.append({
            "id": "experimental-setup",
            "type": "agent",
            "label": "Experimental setup — captured facts only",
            "spec": _spec("experimental-setup"),
            "reads": section_reads["experimental-setup"],
            "needs": [_afterok(prev_body)],
        })
        prev_body = "experimental-setup"

    # 9. results-discussion
    if "results-discussion" in active_sections:
        nodes.append({
            "id": "results-discussion",
            "type": "agent",
            "label": "Results and discussion — macro-only numbers",
            "spec": _spec("results-discussion"),
            "reads": section_reads["results-discussion"],
            "needs": [_afterok(prev_body)],
        })
        prev_body = "results-discussion"

    # 10. limitations
    if "limitations" in active_sections:
        nodes.append({
            "id": "limitations",
            "type": "agent",
            "label": "Limitations — seeded from findings Caveats/Confidence",
            "spec": _spec("limitations"),
            "reads": section_reads["limitations"],
            "needs": [_afterok(prev_body)],
        })
        prev_body = "limitations"

    # 11. ethics-impacts (VENUE-OPTIONAL — after limitations)
    if "ethics-impacts" in active_sections:
        nodes.append({
            "id": "ethics-impacts",
            "type": "agent",
            "label": "Ethics and broader impacts — harms if work succeeds",
            "spec": _spec("ethics-impacts"),
            "reads": section_reads["ethics-impacts"],
            "needs": [_afterok(prev_body)],
        })
        prev_body = "ethics-impacts"

    # 12. conclusion
    if "conclusion" in active_sections:
        nodes.append({
            "id": "conclusion",
            "type": "agent",
            "label": "Conclusion — claim subset + future work first-class",
            "spec": _spec("conclusion"),
            "reads": section_reads["conclusion"],
            "needs": [_afterok(prev_body)],
        })
        prev_body = "conclusion"

    # 13. introduction (written LATE — after body is known)
    if "introduction" in active_sections:
        nodes.append({
            "id": "introduction",
            "type": "agent",
            "label": "Introduction (LATE — written after body sections)",
            "spec": _spec("introduction"),
            "reads": section_reads["introduction"],
            "needs": [_afterok(prev_body)],
        })
        prev_body = "introduction"

    # 14. abstract (written LAST)
    if "abstract" in active_sections:
        nodes.append({
            "id": "abstract",
            "type": "agent",
            "label": "Abstract (LAST — strict subset of body)",
            "spec": _spec("abstract"),
            "reads": section_reads["abstract"],
            "needs": [_afterok(prev_body)],
        })
        prev_abstract = "abstract"
    else:
        prev_abstract = prev_body

    # 15a. title (SR-MS-AUDIENCE §5J.16.4 — afterok abstract, before assemble)
    if "title" in active_sections:
        nodes.append({
            "id": "title",
            "type": "agent",
            "label": "Title — 3–5 reader-facing candidates (editorial, not run-id)",
            "spec": _spec("title"),
            "reads": section_reads["title"],
            "needs": [_afterok(prev_abstract)],
        })
        prev_title = "title"
    else:
        prev_title = prev_abstract

    # 15. assemble (joins abstract + title + appendix-repro)
    assemble_needs: list[dict[str, str]] = [_afterok(prev_title)]
    if "appendix-repro" in active_sections:
        assemble_needs.append(_afterok("appendix-repro"))

    # data-code-availability (VENUE-OPTIONAL — near appendix, joins assemble)
    if "data-code-availability" in active_sections:
        nodes.append({
            "id": "data-code-availability",
            "type": "agent",
            "label": "Data and code availability — roadmap into appendix",
            "spec": _spec("data-code-availability"),
            "reads": section_reads["data-code-availability"],
            "needs": [_afterok("approve-thesis")],  # parallel to appendix
        })
        assemble_needs.append(_afterok("data-code-availability"))

    if "assemble" in active_sections:
        nodes.append({
            "id": "assemble",
            "type": "agent",
            "label": "Assemble — join sections into main.tex",
            "spec": _spec("assemble"),
            "reads": section_reads["assemble"],
            "needs": assemble_needs,
        })
        prev_compile = "assemble"
    else:
        prev_compile = prev_abstract

    # 16. compile
    if "compile" in active_sections:
        nodes.append({
            "id": "compile",
            "type": "agent",
            "label": "Compile — exec-guarded chktex + pdflatex fix-loop",
            "spec": _spec("compile"),
            "reads": section_reads["compile"],
            "needs": [_afterok(prev_compile)],
        })
        prev_critic = "compile"
    else:
        prev_critic = prev_compile

    # 17. critic
    if "critic" in active_sections:
        nodes.append({
            "id": "critic",
            "type": "agent",
            "label": "Critic — anti-positivity-bias, worst-three mandatory",
            "spec": _spec("critic"),
            "reads": section_reads["critic"],
            "needs": [_afterok(prev_critic)],
        })
        prev_gate3 = "critic"
    else:
        prev_gate3 = prev_critic

    # 17b. cold-read (SR-MS-AUDIENCE §5J.16.3 Layer-1 — afterok compile, parallel to critic)
    # Runs the deterministic body leak-scan before approve-manuscript.
    # Layer-2 LLM judge is SR-MS-COLDREAD (separate SR, separate node).
    gate3_needs: list[dict[str, str]] = [_afterok(prev_gate3)]
    if "cold-read" in active_sections:
        nodes.append({
            "id": "cold-read",
            "type": "agent",
            "label": "Cold-read Layer-1 — deterministic body leak-scan (no LLM)",
            "spec": _spec("cold-read"),
            "reads": section_reads["cold-read"],
            "needs": [_afterok(prev_critic)],  # afterok compile (same as critic)
        })
        gate3_needs.append(_afterok("cold-read"))

    # 17c. Review-board round blocks (SR-MS-REVIEW-a §5J.17.2)
    # N pre-declared round-blocks chained afterok (bounded acyclic unroll):
    #   per round r: K reviewer-r-L{k} nodes (afterok prior gate) →
    #                meta-review-r (afterok K reviewers) →
    #                [r<N] revise-r (afterok meta-review-r)
    # "Cleared" skip short-circuit = node-level check on RunState.meta["review_board"]
    # (zero new walker mechanism — the existing inject_results early-return pattern).
    # N and K are FROZEN at scaffold time (stopping rule — see review_config in manifest).
    from research_vault.manuscript.review_board import get_review_config, get_reviewer_lens_spec

    review_cfg = get_review_config(config)
    _N = review_cfg["max_rounds"]
    _K = review_cfg["reviewers_per_round"]

    # reads: for reviewer nodes = only the compiled tree (the rendered PDF text).
    # ANTI-ANCHORING: reviewer nodes do NOT read the thesis, the ms_id note, or prior
    # round reviews/rebuttals. This is the fresh-by-construction boundary — the reads:
    # list is the ONLY channel available; excluding those paths enforces the invariant.
    reviewer_reads = [tree_rel]

    # Previous gate before first review round = cold-read OR critic (whichever is last)
    prev_review_gate: str = "cold-read" if "cold-read" in active_sections else prev_gate3

    for r in range(1, _N + 1):
        # K parallel reviewer nodes (fan-out)
        reviewer_ids: list[str] = []
        for k in range(1, _K + 1):
            reviewer_id = f"reviewer-{r}-L{k}"
            reviewer_ids.append(reviewer_id)

            # Which node gates this reviewer?
            if r == 1:
                reviewer_upstream = prev_review_gate
            else:
                # Round r+1 is gated on revise-(r-1), NOT on meta-review-(r-1).
                # This enforces fresh-by-construction: round r+1 reviewers cannot
                # see round r's meta-review or the prior rebuttal (they depend only
                # on revise-(r-1) completing, which writes a new compiled draft).
                reviewer_upstream = f"revise-{r - 1}"

            tip_key = f"reviewer-round-{r}-L{k}"
            # SR-MS-REVIEW-b: prepend the lens-specific posture to the reviewer spec.
            # The lens biases WHERE to dig first; all 7 dims are still scored.
            # K=2 fallback: L1+L3 (floor-carrying pair). K=1: L1 only.
            lens_posture = get_reviewer_lens_spec(k=k, K=_K)
            reviewer_spec = (
                f"[REVIEW-BOARD ROUND {r} / LENS {k}]\n\n"
                f"{lens_posture}\n\n"
                f"You are a FRESH, INDEPENDENT adversarial reviewer. You have NOT seen any "
                f"prior review or rebuttal — you see ONLY the compiled paper text. "
                f"Score all 7 dimensions using the rubric and emit machine-parseable bracket "
                f"tokens: [SOUND:N] [CONTRIB:N] [CLARITY:N] [ORIG:N] [LIMIT:N] [REPRO:N] [ETHICS:N]. "
                f"Every score must be justified in text (ARR rule). "
                f"Node-level short-circuit: first check RunState.meta['review_board']['cleared_at'] "
                f"— if set, emit [SKIPPED] and exit immediately (no LLM call needed). "
                f"Reads: only the compiled PDF (tree_root) — NOT the thesis note, NOT prior reviews."
            )

            nodes.append({
                "id": reviewer_id,
                "type": "agent",
                "label": f"Review-board round {r}, lens {k} — adversarial fresh reviewer",
                "spec": reviewer_spec,
                "reads": reviewer_reads,
                "needs": [_afterok(reviewer_upstream)],
            })

        # Meta-review join (fan-in afterok all K reviewers)
        meta_review_id = f"meta-review-{r}"
        meta_review_spec = (
            f"[META-REVIEW {r}]\n\n"
            f"Aggregate the {_K} reviewer scores by MIN (worst reviewer gates — one strong "
            f"objection is never averaged away). Evaluate the floor predicate: "
            f"cleared ⟺ MIN(SOUND)≥{review_cfg['floor_value']} AND MIN(REPRO)≥{review_cfg['floor_value']}. "
            f"If cleared: write RunState.meta['review_board']['cleared_at'] = {r}. "
            f"Synthesize worst-three findings. Run the canary scaffold. "
            f"Node-level skip: if cleared_at already set → NO-OP."
        )
        nodes.append({
            "id": meta_review_id,
            "type": "agent",
            "label": f"Meta-review round {r} — MIN aggregation + threshold predicate",
            "spec": meta_review_spec,
            "reads": reviewer_reads,  # reads same as reviewers (tree only; no prior-round data)
            "needs": [_afterok(rid) for rid in reviewer_ids],
        })

        # Revise node (only for non-last rounds — no revise after the last review)
        if r < _N:
            revise_id = f"revise-{r}"
            revise_spec = (
                f"[REVISE {r}]\n\n"
                f"(a) Record the author's rebuttal to meta-review-{r} (artifact, not verdict). "
                f"(b) Re-draft ONLY the sections identified as failing in meta-review-{r}. "
                f"(c) Recompile via rv manuscript compile. "
                f"(d) RE-FIRE support-matcher (SR-MS-2) + cold-read (SR-MS-COLDREAD) on the "
                f"revised draft — POSTCONDITION: revised draft STILL passes both honesty gates. "
                f"If re-fire BLOCKs (un-grounded or re-leaked) → reject the revision and "
                f"surface the conflict: a revision CANNOT un-ground to please a reviewer. "
                f"Node-level skip: if RunState.meta['review_board']['cleared_at'] set → NO-OP."
            )
            nodes.append({
                "id": revise_id,
                "type": "agent",
                "label": f"Revise round {r} — re-fire honesty gates (anti-gaming c)",
                "spec": revise_spec,
                "reads": [tree_rel],
                "needs": [_afterok(meta_review_id)],
            })

    # gate3_needs: approve-manuscript needs the last round's meta-review
    last_meta_review_id = f"meta-review-{_N}"
    # Also still needs the honesty-gate nodes (critic, cold-read)
    # gate3_needs already has critic + cold-read; add the final meta-review
    gate3_needs.append(_afterok(last_meta_review_id))

    # 18. Gate 3: approve-manuscript (final human-go — needs BOTH honesty gates AND review board)
    nodes.append({
        "id": "approve-manuscript",
        "type": "human-go",
        "label": (
            "Gate 3: Approve manuscript — BLOCK/WARN counts + worst-three + body-clean "
            "+ review-board verdict (NOT-CLEARED or cleared at round r)"
        ),
        "needs": gate3_needs,
    })

    manifest: dict[str, Any] = {
        "run_id": f"ms-{ms_id}-draft",
        "name": f"Manuscript drafting: {ms_id} — {thesis[:60]}{'…' if len(thesis) > 60 else ''}",
        "global_cap": 1,  # sections are sequential by design (DAG enforces order)
        "nodes": nodes,
        # SR-MS-REVIEW-a: N and K frozen at scaffold (stopping rule — §5J.17.6)
        "review_config": review_cfg,
    }
    return manifest


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cmd_new(
    project: str,
    ms_id: str,
    *,
    thesis: str,
    scope: list[str],
    config: Config | None = None,
    include_optional: bool = False,
    include_venue_optional: bool = False,
    section_tip_override: dict[str, str] | None = None,
    style_preamble_override: str | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Scaffold a manuscript OKF note + LaTeX artifact tree + drafting-DAG manifest.

    When to use: use `rv manuscript new <project> <id> --thesis '...'` to scaffold a
    new grounded manuscript. This is the ONLY path that creates the closed-bib + DAG
    framework — hand-writing a .tex skips the grounding teeth.

    Anti-pattern: do NOT hand-write a .tex and hand-type citations/numbers.
    Use this command so the draft carries a closed .bib from your literature/ notes,
    machine-injected results, and structural \\cite → source verification.

    Args:
        project: project slug (must be registered in config).
        ms_id: manuscript identifier (slug, e.g. "ms-001" or "icml-2026").
        thesis: one-sentence claim the paper argues (set by --thesis).
        scope: list of OKF note ids synthesized (e.g. ["findings/find-q1"]).
        config: optional Config (loaded if None).
        include_optional: include OPTIONAL sections (e.g. background).
        include_venue_optional: include VENUE-OPTIONAL sections (e.g. ethics-impacts).
        section_tip_override: optional per-section tip overrides (venue customization).
        style_preamble_override: optional replacement for the style preamble.

    Returns:
        (note_path, tree_root, manifest) where:
          note_path: path to the OKF manuscript note (manuscript/<id>.md)
          tree_root: path to the LaTeX artifact tree root (manuscripts/<id>/)
          manifest:  the drafting-DAG manifest dict (also saved as drafting-dag.json)

    sr: SR-MS-1a
    """
    cfg = config or load_config()
    project_notes_dir = cfg.project_notes_dir(project)

    # ── Scaffold OKF type dirs ────────────────────────────────────────────────
    # Ensures findings/, literature/, methods/, etc. exist so reads: pointers
    # in the manifest resolve at run-time. This is idempotent (exist_ok=True).
    scaffold_okf_dirs(project_notes_dir)

    # ── Write the OKF note ────────────────────────────────────────────────────
    ms_dir = _manuscript_dir(project, cfg)
    ms_dir.mkdir(parents=True, exist_ok=True)

    note_path = ms_dir / f"{ms_id}.md"
    if note_path.exists():
        # Avoid silent overwrite (parallel to note.py convention)
        note_path = ms_dir / f"{ms_id}-{_today()}.md"

    scope_str = ", ".join(scope) if scope else ""
    fields: dict[str, str] = {
        "type": "manuscript",
        "title": thesis[:120] if thesis else ms_id,
        "created": _today(),
        "manuscript_location": "",   # fill in: path to manuscripts/<id>/main.tex
        "manuscript_pdf": "",        # fill in: path to compiled <id>.pdf (set by compile)
        "manuscript_hash": "",       # fill in: sha256:<hex> of the compiled PDF
        "thesis": thesis,
        "synthesized_okf": scope_str,
        "section_outline": "",       # filled by rv manuscript new after DAG is emitted
        "dag_run": f"ms-{ms_id}-draft",
    }

    body = (
        "\n"
        "<!-- Manuscript provenance note (SR-MS-1a) -->\n"
        "<!-- Use `rv manuscript new <project> <id> --thesis '...'` for richer creation. -->\n"
        "<!-- That command also scaffolds manuscripts/<id>/{main.tex,sections/,refs.bib,results.tex} -->\n"
        "<!-- and emits the drafting-DAG manifest — use `rv dag run` to drive the loop. -->\n"
        "<!-- NEVER hand-type citations or results numbers — use the closed .bib + results macros. -->\n"
        "\n"
        "## Thesis\n\n"
        f"<!-- {thesis} -->\n\n"
        "## Scope\n\n"
        "<!-- OKF notes synthesized: findings/, experiments/, methods/, concepts/ notes. -->\n"
        f"<!-- synthesized_okf: {scope_str or '(none specified)'} -->\n\n"
        "## Provenance\n\n"
        "<!-- Filled by rv manuscript compile: manuscript_hash = sha256 of the compiled PDF. -->\n"
        "<!-- dag_run = the drafting-DAG run_id whose afterok lineage produced the sections. -->\n"
    )

    note_path.write_text(_render_frontmatter(fields) + "\n" + body, encoding="utf-8")

    # ── Scaffold the LaTeX artifact tree ──────────────────────────────────────
    tree_root = _manuscripts_tree_root(project, ms_id, cfg)
    tree_root.mkdir(parents=True, exist_ok=True)
    sections_dir = tree_root / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)

    # main.tex — neutral article-class template stub
    _write_main_tex(tree_root, ms_id, thesis)

    # refs.bib — empty BibTeX (populated by rv manuscript compile from literature/ notes)
    refs_bib = tree_root / "refs.bib"
    if not refs_bib.exists():
        refs_bib.write_text(
            "% refs.bib — auto-populated by `rv manuscript compile` from literature/ notes.\n"
            "% Do NOT hand-edit citekeys here; run `rv cite check` to verify coverage.\n",
            encoding="utf-8",
        )

    # results.tex — empty macros stub (populated by rv manuscript compile from results/)
    results_tex = tree_root / "results.tex"
    if not results_tex.exists():
        results_tex.write_text(
            "% results.tex — auto-populated by `rv manuscript compile`.\n"
            "% Each \\result* macro is injected from hash-verified experiment results.\n"
            "% The LLM must reference macros (\\resultAcc), never type literal numbers.\n",
            encoding="utf-8",
        )

    # ── Section stub files ────────────────────────────────────────────────────
    # Create a minimal LaTeX stub for each section that main.tex \input{}-s.
    # Without these, pdflatex immediately aborts on "File not found."
    # The stubs are intentionally empty content (single comment) — DAG agents
    # overwrite them as part of the drafting loop.
    _STUB_SECTIONS = [
        "abstract",
        "introduction",
        "related-work",
        "method",
        "experimental-setup",
        "results-discussion",
        "limitations",
        "conclusion",
        "appendix-repro",
        # Optional sections (created only if flags active — template has them commented out
        # so they won't be \input-ed unless uncommented, but stubs don't hurt)
        "background",
        "ethics-impacts",
        "data-code-availability",
    ]
    for stub_name in _STUB_SECTIONS:
        stub_path = sections_dir / f"{stub_name}.tex"
        if not stub_path.exists():
            stub_path.write_text(
                f"% {stub_name}.tex — populated by rv dag run.\n",
                encoding="utf-8",
            )

    # ── Build and save the drafting-DAG manifest ──────────────────────────────
    manifest = _build_manifest(
        project=project,
        ms_id=ms_id,
        thesis=thesis,
        scope=scope,
        project_notes_dir=project_notes_dir,
        tree_root=tree_root,
        include_optional=include_optional,
        include_venue_optional=include_venue_optional,
        section_tip_override=section_tip_override,
        style_preamble_override=style_preamble_override,
        config=cfg,
    )

    manifest_path = tree_root / "drafting-dag.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Update note with manuscript_location
    _update_note_field(note_path, "manuscript_location", str(tree_root / "main.tex"))

    return note_path, tree_root, manifest


def _write_main_tex(tree_root: Path, ms_id: str, thesis: str) -> None:
    """Write a neutral article-class main.tex stub to tree_root."""
    main_tex = tree_root / "main.tex"
    if main_tex.exists():
        return  # idempotent — don't overwrite existing
    # SR-PKG: templates/ relocated to data/templates/ inside the wheel.
    # Use importlib.resources + as_file() for zipimport safety.
    pkg_data = importlib.resources.files("research_vault") / "data"
    with importlib.resources.as_file(pkg_data / "templates" / "manuscript.tex") as tmpl_path:
        if not tmpl_path.is_file():
            raise RuntimeError(
                "Package data missing: data/templates/manuscript.tex. "
                "The wheel is incomplete — reinstall research-vault."
            )
        content = tmpl_path.read_text(encoding="utf-8")
    content = content.replace("{{MS_ID}}", ms_id)
    content = content.replace("{{THESIS}}", thesis)
    main_tex.write_text(content, encoding="utf-8")


def _minimal_main_tex(ms_id: str, thesis: str) -> str:
    """Fallback minimal LaTeX main.tex when template is unavailable."""
    return (
        "\\documentclass[11pt]{article}\n"
        "\\usepackage[utf8]{inputenc}\n"
        "\\usepackage[T1]{fontenc}\n"
        "\\usepackage{hyperref}\n"
        "\\usepackage{natbib}\n"
        "\n"
        "% Machine-injected results macros (populated by rv manuscript compile)\n"
        "\\input{results}\n"
        "\n"
        "\\begin{document}\n"
        "\n"
        f"\\title{{{thesis}}}\n"
        "\\author{}\n"
        "\\date{}\n"
        "\\maketitle\n"
        "\n"
        "\\begin{abstract}\n"
        "\\input{sections/abstract}\n"
        "\\end{abstract}\n"
        "\n"
        "% Body sections (populated by rv dag run)\n"
        "% \\input{sections/related-work}\n"
        "% \\input{sections/method}\n"
        "% \\input{sections/experimental-setup}\n"
        "% \\input{sections/results-discussion}\n"
        "% \\input{sections/limitations}\n"
        "% \\input{sections/conclusion}\n"
        "\n"
        "\\bibliographystyle{plainnat}\n"
        "\\bibliography{refs}\n"
        "\n"
        "% Appendix\n"
        "\\appendix\n"
        "% \\input{sections/appendix-repro}\n"
        "\n"
        "\\end{document}\n"
    )


def _update_note_field(note_path: Path, field: str, value: str) -> None:
    """Update a single frontmatter field in an existing note file.

    Uses ``[ \\t]*`` (NOT ``\\s*``) after the colon to avoid consuming the
    trailing newline — which would eat the next frontmatter field into group 1
    and silently delete it on substitution.
    """
    if not note_path.exists():
        return
    text = note_path.read_text(encoding="utf-8")
    # Replace the field line if it exists (flat frontmatter contract).
    # [ \t]* matches only horizontal whitespace — never eats the newline
    # or the next YAML key (which \s* would silently consume).
    import re as _re
    pattern = _re.compile(rf"^({_re.escape(field)}:[ \t]*)(.*)$", _re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(rf"\g<1>{value}", text, count=1)
        note_path.write_text(text, encoding="utf-8")


def cmd_list(
    project: str,
    *,
    config: Config | None = None,
) -> list[dict[str, Any]]:
    """List manuscript OKF notes for the given project.

    When to use: `rv manuscript list [--project <slug>]` to enumerate
    all manuscript notes for a project.

    Returns:
        List of {path, fields} dicts, one per manuscript note found.
        Empty list when no manuscript notes exist yet.

    sr: SR-MS-1a
    """
    cfg = config or load_config()
    ms_dir = _manuscript_dir(project, cfg)
    if not ms_dir.exists():
        return []

    results: list[dict[str, Any]] = []
    for p in sorted(ms_dir.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        fields, _ = _parse_frontmatter(text)
        if fields.get("type") == "manuscript":
            results.append({"path": p, "fields": fields})
    return results


def cmd_prep(
    project: str,
    ms_id: str,
    *,
    config: Config | None = None,
) -> dict[str, Any]:
    """Run grounding-builders prep step (no pdflatex). Idempotent.

    When to use: ``rv manuscript compile --prep-only <project> <id>`` to populate
    refs.bib, results.tex, and sections/appendix-repro.tex so that a drafting agent
    (e.g. ``results-discussion``) can reference ``\\resultAcc`` macros BEFORE the
    full compile at the end of the DAG.

    Execution order (anti-fabrication contract — same as cmd_compile Phase 1):
      1. build_refs_bib — exports closed .bib from library.json.
         Hard-fails on any unmatched \\cite.
      2. inject_results — writes hash-verified \\newcommand macros into results.tex.
         Hard-fails on results_hash mismatch.
      3. inject_appendix — machine-populates sections/appendix-repro.tex.

    Does NOT require pdflatex/bibtex — works without texlive.

    Idempotent: running prep twice, or prep then compile, produces the same
    grounded output as compile alone — builders overwrite, never append.

    Returns:
        dict with "exit_code", "message", "pdf_path" (always None), "builder_warnings".

    sr: SR-MS-1c
    """
    from research_vault.manuscript.compile import run_prep

    cfg = config or load_config()
    ms_dir = _manuscript_dir(project, cfg)
    note_path = ms_dir / f"{ms_id}.md"
    tree_root = _manuscripts_tree_root(project, ms_id, cfg)

    # Resolve library_path from project config ("refs" key) or standard default.
    library_path: Path | None = None
    try:
        proj_rec = cfg.project(project)
        refs = proj_rec.get("refs")
        if refs:
            library_path = Path(refs).expanduser()
    except (KeyError, TypeError):
        pass
    if library_path is None:
        library_path = cfg.project_notes_dir(project) / "library.json"

    return run_prep(note_path, tree_root, library_path=library_path)


def cmd_compile(
    project: str,
    ms_id: str,
    *,
    config: Config | None = None,
) -> dict[str, Any]:
    """Run grounding-builders then the exec-guarded LaTeX compile loop.

    When to use: ``rv manuscript compile <project> <id>`` to produce a
    grounded, machine-injected PDF from the manuscript's main.tex.

    Execution order (anti-fabrication contract §5J.3/§5J.4):
      1. build_refs_bib — exports closed .bib from library.json.
         Hard-fails on any unmatched \\cite (never render an ungrounded PDF).
      2. inject_results — writes hash-verified \\newcommand macros into results.tex.
         Hard-fails on results_hash mismatch.
      3. inject_appendix — machine-populates sections/appendix-repro.tex.
      4. pdflatex → bibtex → pdflatex × 2 + chktex fix-loop.

    If pdflatex/bibtex are absent: returns friendly message, exit_code=1.

    Resolves library.json from the project's ``refs`` config key, falling back
    to project_notes_dir/library.json (the default layout from ``rv project new``).

    Resolves experiment notes automatically from the manuscript note's
    ``synthesized_okf`` field (``experiments/<id>`` entries).

    Returns:
        dict with "exit_code", "message", "log", "chktex", "pdf_path",
        "builder_warnings" (non-fatal builder issues, e.g. missing library).

    sr: SR-MS-1b
    """
    from research_vault.manuscript.compile import run_compile

    cfg = config or load_config()
    ms_dir = _manuscript_dir(project, cfg)
    note_path = ms_dir / f"{ms_id}.md"
    tree_root = _manuscripts_tree_root(project, ms_id, cfg)

    # Resolve library_path from project config ("refs" key) or standard default.
    library_path: Path | None = None
    try:
        proj_rec = cfg.project(project)
        refs = proj_rec.get("refs")
        if refs:
            library_path = Path(refs).expanduser()
    except (KeyError, TypeError):
        pass
    if library_path is None:
        # Standard default: project_notes_dir/library.json (set by rv project new)
        library_path = cfg.project_notes_dir(project) / "library.json"

    # experiment_notes: resolved automatically from synthesized_okf inside run_compile
    return run_compile(note_path, tree_root, library_path=library_path)


def cmd_check(
    project: str,
    ms_id: str,
    *,
    config: Config | None = None,
) -> dict[str, Any]:
    """Run structural gates for a manuscript (rv manuscript check <id>).

    When to use: ``rv manuscript check <project> <id>`` to run the structural
    grounding gates before DAG dispatch or the approve-manuscript gate:
      - Unmatched \\cite resolution (against refs.bib)
      - Figure-file existence (\\includegraphics → file exists)
      - Compile-success (passive PDF existence check)
      - Data-code-availability sentinel cross-check

    Does NOT run support-matcher / critic / semantic gates (→ SR-MS-2).

    Returns:
        dict with "errors" (hard gate failures), "warnings" (soft flags),
        and "all_ok" (True iff no errors).

    sr: SR-MS-1b
    """
    from research_vault.manuscript.check_gates import check_manuscript

    cfg = config or load_config()
    ms_dir = _manuscript_dir(project, cfg)
    note_path = ms_dir / f"{ms_id}.md"
    tree_root = _manuscripts_tree_root(project, ms_id, cfg)
    return check_manuscript(note_path, tree_root, config=cfg)
