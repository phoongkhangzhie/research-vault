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
) -> dict[str, Any]:
    """Build the drafting-DAG manifest (§5J.2 shape).

    Returns a manifest dict ready for validate_manifest and JSON serialization.

    Node count by default: 16 (13 required agent sections + 3 human-go gates).
    Optional sections add nodes when enabled.

    reads: pointers use absolute paths so resolution is independent of project_root
    at run-time. The scaffolder ensures all pointed-to directories exist before
    returning the manifest.
    """
    active_sections = get_active_sections(
        include_optional=include_optional,
        include_venue_optional=include_venue_optional,
    )
    tips = get_section_tips(override=section_tip_override)
    preamble = get_style_preamble(override=style_preamble_override)

    # Absolute paths for reads: pointers — resolved at scaffolding time.
    # All these dirs are created by scaffold_okf_dirs + the tree scaffolding.
    sections_dir = tree_root / "sections"

    def _abs(rel: str) -> str:
        return str(project_notes_dir / rel)

    def _spec(section_key: str) -> str:
        """Build the spec string: preamble + section tip."""
        tip = tips.get(section_key, f"Write the {section_key} section.")
        return preamble.rstrip() + "\n\n---\n\n" + tip

    # ── Reads contracts by section ───────────────────────────────────────────
    # Per §5J.2 gotcha ruling: point at the OKF type-dir + sections/ dir,
    # NOT at specific unwritten .tex files. sections/ exists after scaffolding.
    _sections = str(sections_dir)

    section_reads: dict[str, list[str]] = {
        "gather-scope": [
            _abs("findings"),
            _abs("experiments"),
            _abs("methods"),
            _abs("concepts"),
        ],
        "related-work": [
            _abs("literature"),
            _sections,
        ],
        "background": [
            _abs("concepts"),
            _abs("methods"),
            _sections,
        ],
        "method": [
            _abs("methods"),
            _sections,
        ],
        "experimental-setup": [
            _abs("experiments"),
            _abs("datasets"),
            _sections,
        ],
        "results-discussion": [
            _abs("experiments"),
            _abs("findings"),
            _sections,
        ],
        "limitations": [
            _abs("findings"),
            _sections,
        ],
        "ethics-impacts": [
            _abs("findings"),
            _abs("methods"),
            _sections,
        ],
        "conclusion": [_sections],
        "introduction": [_sections],
        "abstract": [_sections],
        "appendix-repro": [
            _abs("experiments"),
            _sections,
        ],
        "data-code-availability": [
            _abs("experiments"),
            _abs("datasets"),
            _sections,
        ],
        "assemble": [_sections],
        "compile": [str(tree_root)],
        "critic": [str(tree_root)],
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

    # 15. assemble (joins abstract + appendix-repro)
    assemble_needs: list[dict[str, str]] = [_afterok(prev_abstract)]
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

    # 18. Gate 3: approve-manuscript (final human-go)
    nodes.append({
        "id": "approve-manuscript",
        "type": "human-go",
        "label": "Gate 3: Approve manuscript — BLOCK/WARN counts + worst-three",
        "needs": [_afterok(prev_gate3)],
    })

    manifest: dict[str, Any] = {
        "run_id": f"ms-{ms_id}-draft",
        "name": f"Manuscript drafting: {ms_id} — {thesis[:60]}{'…' if len(thesis) > 60 else ''}",
        "global_cap": 1,  # sections are sequential by design (DAG enforces order)
        "nodes": nodes,
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
    # Load from templates if available, else generate a minimal stub.
    # The template is the one-time template; per-section prose goes in sections/.
    src_root = Path(__file__).parent.parent
    template_path = src_root / "templates" / "manuscript.tex"
    if template_path.exists():
        content = template_path.read_text(encoding="utf-8")
        content = content.replace("{{MS_ID}}", ms_id)
        content = content.replace("{{THESIS}}", thesis)
    else:
        content = _minimal_main_tex(ms_id, thesis)
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
    """Update a single frontmatter field in an existing note file."""
    if not note_path.exists():
        return
    text = note_path.read_text(encoding="utf-8")
    # Replace the field line if it exists (flat frontmatter contract)
    import re as _re
    pattern = _re.compile(rf"^({_re.escape(field)}:\s*)(.*)$", _re.MULTILINE)
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
