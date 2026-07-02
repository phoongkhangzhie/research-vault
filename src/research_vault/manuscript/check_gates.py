"""check_gates.py — Structural + semantic gates for rv manuscript check.

HONEST BOUNDARY (from §5J.13-B):
  STRUCTURAL (deterministic, sound, no LLM):
    1. Unmatched-\\cite resolution — every \\cite{key} must be in refs.bib.
    2. Figure-file existence — every \\includegraphics{f} → f exists.
    3. Compile-success check — PDF exists (if manuscript_pdf is set).
    4. Data-code-availability sentinel cross-check.
    5. Dedup — repeated \\cite{} or duplicate .bib entry-keys.
    6. Page-limit — configurable; uses pdftotext (graceful if absent).
    7. (B) Citekey-provenance — every .bib entry backed by \\cite must carry
       a well-formed DOI/arXiv/S2 id (BLOCK) or be human-vouched (PASS, listed).
    8. Hash-drift re-verify — re-checks stamped results_hash at approve-manuscript.

  SEMANTIC (LLM-judged, via support_matcher.py):
    J-1 — low-confidence completeness (confidence: low findings in limitations).
    J-2 — stance/covers membership as matcher input.
    K-1 — preregistration completeness (every plan_role: main accounted for).
    Strength-monotonicity — non-increasing claim strength across sections.
    Support-matcher tally — (claim, citekey) pairs judged by Opus-tier judge.

  We do NOT guarantee "no hallucinated references in prose." For prose we assist
  the clear cases (naked_cite.py) and spotlight the rest for human adjudication.
  Document this boundary; never claim a guarantee we cannot make.

Stdlib only.
sr: SR-MS-1b (structural gates 1–4); SR-MS-2 (gates 5–8, semantic gates, approve payload)
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# Opus-tier model for semantic judgment gates (SR-MS-2 D-MS-4).
# Resolved via RV_JUDGE_MODEL env var; never pinned to a versioned ID in source.
_DEFAULT_JUDGE_MODEL: str = os.environ.get("RV_JUDGE_MODEL", "")


# ---------------------------------------------------------------------------
# Gate 1: unmatched \\cite resolution
# ---------------------------------------------------------------------------

# Matches @entry{citekey, in refs.bib
_BIB_ENTRY_KEY_RE = re.compile(r"^@\w+\{([^,\s]+)", re.MULTILINE)

# Same pattern as bib.py (inline to avoid circular import)
_CITE_RE = re.compile(r"\\cite[a-z]*\*?\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")


def _collect_bib_keys(refs_bib: Path) -> set[str]:
    """Return the set of citekeys declared in refs.bib."""
    if not refs_bib.exists():
        return set()
    try:
        text = refs_bib.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {m.group(1).strip() for m in _BIB_ENTRY_KEY_RE.finditer(text)}


def _strip_comments(text: str) -> str:
    """Strip LaTeX line comments (% to end of line, excluding \\%)."""
    lines: list[str] = []
    for line in text.split("\n"):
        stripped = line
        i = 0
        while i < len(line):
            if line[i] == "%" and (i == 0 or line[i - 1] != "\\"):
                stripped = line[:i]
                break
            i += 1
        lines.append(stripped)
    return "\n".join(lines)


def _collect_cited_keys(tex_files: list[Path]) -> set[str]:
    r"""Collect all citekeys from \cite{} in the given .tex files (excluding comments)."""
    keys: set[str] = set()
    for tex in tex_files:
        if not tex.exists():
            continue
        try:
            text = tex.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = _strip_comments(text)
        for m in _CITE_RE.finditer(text):
            for k in m.group(1).split(","):
                k = k.strip()
                if k:
                    keys.add(k)
    return keys


def check_cite_resolution(
    tree_root: Path,
    tex_files: list[Path] | None = None,
) -> list[str]:
    r"""Check that every \cite{key} resolves against refs.bib.

    Returns a list of error strings (empty = all cites resolved).
    Each error names the unmatched citekey.
    """
    refs_bib = tree_root / "refs.bib"
    bib_keys = _collect_bib_keys(refs_bib)

    if tex_files is None:
        tex_files = list(tree_root.rglob("*.tex"))

    cited_keys = _collect_cited_keys(tex_files)
    errors: list[str] = []
    for key in sorted(cited_keys):
        if key not in bib_keys:
            errors.append(
                f"unmatched \\cite{{{key}}}: '{key}' not in refs.bib — "
                f"run `rv manuscript compile` to export the closed .bib, "
                f"or `rv cite add <doi>` if the reference is missing from library.json."
            )
    return errors


# ---------------------------------------------------------------------------
# Gate 2: figure-file existence
# ---------------------------------------------------------------------------

# Matches \includegraphics[opts]{path} and \includegraphics{path}
_INCLUDEGRAPHICS_RE = re.compile(
    r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}"
)


def check_figure_existence(
    tree_root: Path,
    tex_files: list[Path] | None = None,
) -> list[str]:
    r"""Check that every \includegraphics{path} resolves to an existing file.

    Resolves relative to tree_root (the manuscript's artifact directory).
    Returns a list of error strings (empty = all figures exist).
    """
    if tex_files is None:
        tex_files = list(tree_root.rglob("*.tex"))

    errors: list[str] = []
    for tex in tex_files:
        if not tex.exists():
            continue
        try:
            text = tex.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = _strip_comments(text)  # skip commented-out \includegraphics examples
        for m in _INCLUDEGRAPHICS_RE.finditer(text):
            fig_path_str = m.group(1).strip()
            # Try the path relative to tree_root
            fig_path = tree_root / fig_path_str
            # Also try adding common extensions if the path has none
            candidates: list[Path] = [fig_path]
            if not fig_path.suffix:
                for ext in (".pdf", ".png", ".eps", ".jpg", ".jpeg", ".svg"):
                    candidates.append(fig_path.with_suffix(ext))
            if not any(c.exists() for c in candidates):
                errors.append(
                    f"missing figure: \\includegraphics{{{fig_path_str}}} — "
                    f"'{fig_path_str}' not found relative to {tree_root}."
                )
    return errors


# ---------------------------------------------------------------------------
# Gate 3: compile success (optional — checks PDF existence)
# ---------------------------------------------------------------------------

def check_compile_success(note_path: Path, tree_root: Path) -> list[str]:
    """Check compile success: if manuscript_pdf is set, verify the PDF exists.

    This is a passive check (does not run the compiler). If manuscript_pdf
    is unset (manuscript not yet compiled), no error is returned.
    """
    from research_vault.note import _parse_frontmatter
    if not note_path.exists():
        return [f"manuscript note not found: {note_path}"]
    text = note_path.read_text(encoding="utf-8")
    fields, _ = _parse_frontmatter(text)
    pdf_str = fields.get("manuscript_pdf", "").strip()
    if not pdf_str:
        return []  # Not yet compiled — not an error at check time
    pdf = Path(pdf_str)
    if not pdf.exists():
        return [
            f"compile check: manuscript_pdf is set to '{pdf_str}' but the file "
            f"does not exist — run `rv manuscript compile` to produce the PDF."
        ]
    return []


# ---------------------------------------------------------------------------
# Gate 4: data-code-availability sentinel cross-check
# ---------------------------------------------------------------------------

# Phrases indicating a "fully available" claim in the availability section.
_AVAILABILITY_CLAIM_RE = re.compile(
    r"\b(fully available|all .{0,20} available|publicly available|"
    r"code .{0,10} available|data .{0,10} available|open[- ]source)\b",
    re.IGNORECASE,
)

_SENTINEL = "not-recorded-in-provenance"

# Repro fields that are REQUIRED for a "fully available" claim to be credible.
_REQUIRED_FOR_AVAIL = frozenset({
    "repro_seed",
    "repro_model_id",
    "repro_eval_harness",
    "repro_dataset_id",
    "repro_dataset_hash",
    "repro_metric",
})


def check_availability_sentinel(
    tree_root: Path,
    experiment_notes: list[Path],
) -> list[str]:
    """Cross-check data-code-availability claim against repro sentinel fields.

    Structurally-checkable gate: if the data-code-availability section contains
    a "fully available" claim AND at least one required repro field is still at
    sentinel in any scoped experiment note → flag as a warning.

    This catches the specific anti-pattern: writing "all code and data available"
    when reproducibility fields haven't been filled in yet.

    Returns a list of warning/flag strings (empty = no cross-check issue).
    """
    from research_vault.note import _parse_frontmatter

    avail_section = tree_root / "sections" / "data-code-availability.tex"
    if not avail_section.exists():
        return []  # Section not present — no check needed

    try:
        avail_text = avail_section.read_text(encoding="utf-8")
    except OSError:
        return []

    if not _AVAILABILITY_CLAIM_RE.search(avail_text):
        return []  # No availability claim — no cross-check needed

    # Check experiment notes for sentinel repro fields
    flags: list[str] = []
    for exp_note in experiment_notes:
        if not exp_note.exists():
            continue
        try:
            text = exp_note.read_text(encoding="utf-8")
        except OSError:
            continue
        fields, _ = _parse_frontmatter(text)
        sentinel_fields = [
            f for f in _REQUIRED_FOR_AVAIL
            if fields.get(f, "").strip() == _SENTINEL
        ]
        if sentinel_fields:
            flags.append(
                f"availability sentinel cross-check: "
                f"data-code-availability.tex claims data/code availability but "
                f"{exp_note.name} has repro fields still at 'not-recorded-in-provenance': "
                f"{', '.join(sentinel_fields)}. "
                f"Fill these fields (via `rv wandb pull`) or qualify the availability claim."
            )

    return flags


# ---------------------------------------------------------------------------
# Main gate runner
# ---------------------------------------------------------------------------

def check_manuscript(
    note_path: Path,
    tree_root: Path,
    *,
    experiment_notes: list[Path] | None = None,
    tex_files: list[Path] | None = None,
) -> dict[str, Any]:
    """Run all structural gates for rv manuscript check.

    When to use: ``rv manuscript check <id>`` — run the structural grounding
    gates BEFORE the semantic ones (SR-MS-2). Structural gates are cheap,
    binary, and do not require an LLM.

    Args:
        note_path: path to the manuscript/<id>.md OKF note.
        tree_root: path to manuscripts/<id>/ artifact tree.
        experiment_notes: list of scoped experiments/ note paths (for the
            availability cross-check). When None, resolved from the note's
            synthesized_okf field relative to the project notes dir.
        tex_files: list of .tex files to scan. When None, rglob tree_root.

    Returns:
        dict with:
          "errors": list of hard error strings (unmatched cite, missing figure)
          "warnings": list of warning strings (availability cross-check)
          "all_ok": bool (True iff errors is empty)
    """
    from research_vault.note import _parse_frontmatter
    from research_vault.config import load_config

    errors: list[str] = []
    warnings: list[str] = []

    # ── Resolve experiment notes if not provided ───────────────────────────
    if experiment_notes is None:
        experiment_notes = []
        if note_path.exists():
            text = note_path.read_text(encoding="utf-8")
            fields, _ = _parse_frontmatter(text)
            scope_str = fields.get("synthesized_okf", "").strip()
            if scope_str:
                try:
                    cfg = load_config()
                    # Extract project from note path (heuristic: manuscript/<id>.md
                    # lives under project_notes_dir/<project>/manuscript/)
                    # Walk up to find project_notes_dir
                    for scope_item in scope_str.split(","):
                        scope_item = scope_item.strip()
                        if scope_item.startswith("experiments/"):
                            exp_name = scope_item[len("experiments/"):]
                            # Try to find the experiment note relative to the
                            # manuscript note's project dir
                            candidate = note_path.parent.parent / "experiments" / f"{exp_name}.md"
                            if candidate.exists():
                                experiment_notes.append(candidate)
                except Exception:
                    pass

    if tex_files is None:
        tex_files = list(tree_root.rglob("*.tex"))

    # ── Gate 1: unmatched \\cite ───────────────────────────────────────────
    errors.extend(check_cite_resolution(tree_root, tex_files))

    # ── Gate 2: figure-file existence ─────────────────────────────────────
    errors.extend(check_figure_existence(tree_root, tex_files))

    # ── Gate 3: compile success (passive) ─────────────────────────────────
    errors.extend(check_compile_success(note_path, tree_root))

    # ── Gate 4: data-code-availability sentinel cross-check ───────────────
    warnings.extend(check_availability_sentinel(tree_root, experiment_notes))

    return {
        "errors": errors,
        "warnings": warnings,
        "all_ok": len(errors) == 0,
    }


# ---------------------------------------------------------------------------
# Gate 5: dedup — repeated \cite / duplicate .bib entry-keys (SR-MS-2)
# ---------------------------------------------------------------------------

def check_dedup(
    tree_root: Path,
    tex_files: list[Path] | None = None,
) -> tuple[list[str], list[str]]:
    r"""Check for duplicate \cite{} uses and duplicate .bib entry keys.

    Returns (errors, warnings):
      - errors: duplicate .bib entry-keys (hard: the .bib is malformed)
      - warnings: keys cited more than once (soft: may be legitimate repeat,
        but flagged for human review per §5J.13-A (1))
    """
    refs_bib = tree_root / "refs.bib"
    if tex_files is None:
        tex_files = list(tree_root.rglob("*.tex"))

    errors: list[str] = []
    warnings: list[str] = []

    # Duplicate .bib entry keys
    bib_keys_seen: dict[str, int] = {}
    if refs_bib.exists():
        try:
            bib_text = refs_bib.read_text(encoding="utf-8", errors="replace")
        except OSError:
            bib_text = ""
        for m in _BIB_ENTRY_KEY_RE.finditer(bib_text):
            key = m.group(1).strip()
            bib_keys_seen[key] = bib_keys_seen.get(key, 0) + 1
        for key, count in bib_keys_seen.items():
            if count > 1:
                errors.append(
                    f"dedup: duplicate .bib entry key '{key}' appears {count} times in refs.bib."
                )

    # Repeated \cite{} uses across the manuscript
    cite_counts: dict[str, int] = {}
    for tex in tex_files:
        if not tex.exists():
            continue
        try:
            text = tex.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = _strip_comments(text)
        for m in _CITE_RE.finditer(text):
            for k in m.group(1).split(","):
                k = k.strip()
                if k:
                    cite_counts[k] = cite_counts.get(k, 0) + 1
    for key, count in sorted(cite_counts.items()):
        if count > 1:
            warnings.append(
                f"dedup: \\cite{{{key}}} appears {count} times — "
                f"review for redundancy or consolidate citations."
            )

    return errors, warnings


# ---------------------------------------------------------------------------
# Gate 6: page-limit via pdftotext (SR-MS-2)
# ---------------------------------------------------------------------------

def check_page_limit(
    tree_root: Path,
    *,
    page_limit: int | None = None,
    config: "Any | None" = None,
) -> list[str]:
    """Check the compiled PDF's page count against the configured venue limit.

    Uses pdftotext (if available) to count pages. Gracefully skips if:
      - pdftotext is absent (returns a warning, not an error)
      - page_limit is None and no config key is set
      - No compiled PDF exists

    Page-limit config key: [manuscript_check] page_limit in research_vault.toml.
    Returns a list of error/warning strings (empty = OK or skipped).
    """
    import subprocess
    import shutil

    # Resolve page_limit from config if not passed directly
    effective_limit = page_limit
    if effective_limit is None and config is not None:
        raw = getattr(config, "_raw", {})
        ms_check = raw.get("manuscript_check", {})
        if isinstance(ms_check, dict):
            cfg_limit = ms_check.get("page_limit")
            if isinstance(cfg_limit, int) and cfg_limit > 0:
                effective_limit = cfg_limit

    if effective_limit is None:
        return []  # No limit configured — skip

    # Find PDF
    pdf_candidates = list(tree_root.glob("*.pdf"))
    if not pdf_candidates:
        return []  # No compiled PDF yet — skip (compile-success gate handles this)

    pdf_path = pdf_candidates[0]

    # Check pdftotext availability
    if shutil.which("pdftotext") is None:
        return [
            f"page-limit: pdftotext is absent — cannot count pages for the {effective_limit}-page limit. "
            f"Install poppler-utils to enable this check."
        ]

    try:
        result = subprocess.run(
            ["pdftotext", str(pdf_path), "-"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return [f"page-limit: pdftotext failed: {e}"]

    if result.returncode != 0:
        return [
            f"page-limit: pdftotext exited {result.returncode} on {pdf_path.name}. "
            f"Cannot verify page count."
        ]

    # pdftotext outputs a form-feed (\x0c) at each page break
    page_count = result.stdout.count("\x0c") + 1
    if page_count > effective_limit:
        return [
            f"page-limit: {pdf_path.name} is {page_count} pages, "
            f"exceeds the configured limit of {effective_limit}."
        ]
    return []


# ---------------------------------------------------------------------------
# Gate 7 (B): citekey-provenance hard gate (SR-MS-2 §5J.13-B)
# ---------------------------------------------------------------------------

# Well-formed DOI pattern (10.XXXX/...)
_DOI_RE = re.compile(r"^10\.\d{4,}[/\S]+$")

# Well-formed arXiv id patterns (e.g. "2005.14165", "cs.CL/0701001", "arXiv:2005.14165")
_ARXIV_RE = re.compile(
    r"(?:arXiv:?)?\d{4}\.\d{4,5}(?:v\d+)?|"
    r"(?:arXiv:)?[a-z]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?",
    re.IGNORECASE,
)

# Semantic Scholar corpus id (numeric, typically 9+ digits)
_S2_RE = re.compile(r"^\d{8,}$")

# Human-vouch marker in Zotero extra field (D-MS-6: adopter-configurable vouch token)
_HUMAN_VOUCH_RE = re.compile(
    r"rv-provenance:\s*verified-no-machine-id",
    re.IGNORECASE,
)

# Matches .bib entry blocks: @TYPE{citekey, ...}
_BIB_BLOCK_RE = re.compile(
    r"@\w+\{([^,\s]+),(.*?)(?=\n@|\Z)",
    re.DOTALL,
)

# Field extractors within a .bib block
_BIB_FIELD_RE = re.compile(r"(\w+)\s*=\s*[\{\"']?(.*?)[\}\"']?\s*(?:,|\n@|\Z)", re.DOTALL)


def _check_entry_has_provenance_id(block_text: str) -> tuple[bool, bool]:
    """Check if a .bib block has a well-formed external id or a human-vouch marker.

    Returns (has_id, has_vouch):
      has_id:   True if DOI, arXiv, or S2 corpus id is present and well-formed.
      has_vouch: True if a human-vouch marker (rv-provenance: verified-no-machine-id)
                 is present in the 'note' or 'annote' fields.
    """
    has_id = False
    has_vouch = False

    # Check for vouch marker in the block text
    if _HUMAN_VOUCH_RE.search(block_text):
        has_vouch = True

    for fm in _BIB_FIELD_RE.finditer(block_text):
        fname = fm.group(1).lower().strip()
        fval = fm.group(2).strip()
        # Remove nested braces
        fval_clean = re.sub(r"[{}]", "", fval).strip()

        if fname == "doi" and fval_clean:
            if _DOI_RE.match(fval_clean):
                has_id = True
        elif fname in ("archiveid", "eprint", "arxivid") and fval_clean:
            if _ARXIV_RE.match(fval_clean):
                has_id = True
        elif fname in ("url", "note") and fval_clean:
            # Check if the url/note contains a DOI or arXiv id
            if re.search(r"doi\.org/10\.\d{4}", fval_clean):
                has_id = True
            elif re.search(r"arxiv\.org/abs/", fval_clean, re.IGNORECASE):
                has_id = True
        elif fname in ("annote", "note") and _HUMAN_VOUCH_RE.search(fval_clean):
            has_vouch = True

    return has_id, has_vouch


def check_cite_provenance(
    tree_root: Path,
    tex_files: list[Path] | None = None,
) -> tuple[list[str], list[str]]:
    r"""Gate (B): every .bib entry backing a \\cite must have a well-formed external id.

    Provenance check: DOI, arXiv id, or S2 corpus id must be present and
    well-formed (offline pattern-match only — no network lookup, zero-infra).

    D-MS-6: BLOCK on missing id, EXCEPT an entry with a human-vouch marker
    (rv-provenance: verified-no-machine-id in the Zotero extra/note field)
    → downgrades to PASS but is LISTED in the returned vouch_list.

    Returns (errors, vouch_list):
      errors:     BLOCK-level: .bib entry with no id AND no vouch
      vouch_list: entries that pass via human-vouch (listed in decision payload)

    HERMETIC: purely offline. No network call. No live lookup.
    """
    refs_bib = tree_root / "refs.bib"
    if tex_files is None:
        tex_files = list(tree_root.rglob("*.tex"))

    if not refs_bib.exists():
        return [], []

    # Only check entries that are actually cited in the tex files
    cited_keys = _collect_cited_keys(tex_files)
    if not cited_keys:
        return [], []

    try:
        bib_text = refs_bib.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], []

    errors: list[str] = []
    vouch_list: list[str] = []

    for m in _BIB_BLOCK_RE.finditer(bib_text):
        citekey = m.group(1).strip()
        if citekey not in cited_keys:
            continue  # Only check entries that are actually cited
        block = m.group(2)
        has_id, has_vouch = _check_entry_has_provenance_id(block)
        if has_id:
            continue  # Good — has a well-formed external id
        if has_vouch:
            vouch_list.append(citekey)  # Passes via human-vouch, listed in payload
        else:
            errors.append(
                f"cite-provenance (B): \\cite{{{citekey}}} — .bib entry has no well-formed "
                f"DOI / arXiv / S2 id. Add a DOI/arXiv id, or add "
                f"'rv-provenance: verified-no-machine-id' to the Zotero extra field if "
                f"the paper legitimately has no machine-readable id."
            )

    return errors, vouch_list


# ---------------------------------------------------------------------------
# Gate 8: hash-drift re-verify at approve-manuscript (SR-MS-2 §5J.13-A (4))
# ---------------------------------------------------------------------------

def check_hash_drift(
    note_path: Path,
    tree_root: Path,
    experiment_note_paths: list[Path] | None = None,
) -> list[str]:
    """Re-verify stamped results_hash values at the approve-manuscript gate.

    Reads the results-provenance-stamp block from the manuscript note and
    re-checks each stamped experiment note's results_hash against its artifact.

    A drifted hash (artifact changed since compile) → BLOCK + recompile required.
    Content-hash, not mtime (same as the compile-time check).

    Returns a list of error strings (empty = no drift detected).
    """
    from research_vault.note import check_result_provenance

    if not note_path.exists():
        return []

    try:
        note_text = note_path.read_text(encoding="utf-8")
    except OSError:
        return []

    # Locate the stamp block
    stamp_start = note_text.find("<!-- results-provenance-stamp-start -->")
    stamp_end = note_text.find("<!-- results-provenance-stamp-end -->")
    if stamp_start == -1 or stamp_end == -1:
        return []  # No stamp — nothing to re-verify

    stamp_block = note_text[stamp_start:stamp_end]

    # Extract experiment note paths from the stamp table rows
    # Format: | ExperimentName | sha256:... | commit |
    row_re = re.compile(r"\|\s*([^|]+)\s*\|\s*sha256:[0-9a-f]+\s*\|", re.IGNORECASE)
    stamped_exp_ids: list[str] = []
    for row_m in row_re.finditer(stamp_block):
        exp_id = row_m.group(1).strip()
        if exp_id and exp_id not in ("Experiment", "---"):
            stamped_exp_ids.append(exp_id)

    if not stamped_exp_ids:
        return []

    # Resolve experiment note paths
    notes_root = note_path.parent.parent  # manuscript/<id>.md → project root
    exp_notes_to_check: list[Path] = []
    if experiment_note_paths:
        exp_notes_to_check = experiment_note_paths
    else:
        for exp_id in stamped_exp_ids:
            # Try experiments/<exp_id>.md
            candidate = notes_root / "experiments" / f"{exp_id}.md"
            if not candidate.exists():
                candidate = notes_root / "experiments" / f"{exp_id}"
            if candidate.exists():
                exp_notes_to_check.append(candidate)

    errors: list[str] = []
    for exp_note in exp_notes_to_check:
        violations = check_result_provenance(exp_note)
        for v in violations:
            errors.append(
                f"hash-drift at approve-manuscript: {v} — "
                f"results artifact has changed since compile; run `rv manuscript compile` again."
            )

    return errors


# ---------------------------------------------------------------------------
# Gate J-1: confidence-completeness (SR-MS-2 §5J.13-A (5))
# ---------------------------------------------------------------------------

def check_confidence_completeness(
    note_path: Path,
    tree_root: Path,
    *,
    findings_notes: list[Path] | None = None,
) -> list[str]:
    """J-1: every in-scope confidence: low finding must appear in limitations.tex.

    Ground truth = the inclusion ledger from gather-scope + each finding's
    confidence field. A silently-dropped caveat is the top integrity risk.

    Returns a list of BLOCK-level error strings.
    Graceful when no inclusion ledger exists (returns []).
    """
    from research_vault.note import _parse_frontmatter

    # Read the limitations section
    limitations_tex = tree_root / "sections" / "limitations.tex"
    if not limitations_tex.exists():
        return []  # Limitations section not written yet — skip

    try:
        limitations_text = limitations_tex.read_text(encoding="utf-8").lower()
    except OSError:
        return []

    if findings_notes is None:
        # Resolve from the manuscript note's synthesized_okf field
        findings_notes = []
        if note_path.exists():
            try:
                text = note_path.read_text(encoding="utf-8")
                fields, _ = _parse_frontmatter(text)
                scope_str = fields.get("synthesized_okf", "").strip()
                notes_root = note_path.parent.parent
                if scope_str:
                    for item in scope_str.split(","):
                        item = item.strip()
                        if item.startswith("findings/"):
                            find_name = item[len("findings/"):]
                            candidate = notes_root / "findings" / f"{find_name}.md"
                            if candidate.exists():
                                findings_notes.append(candidate)
            except Exception:
                pass

    errors: list[str] = []
    for finding_note in findings_notes:
        if not finding_note.exists():
            continue
        try:
            text = finding_note.read_text(encoding="utf-8")
        except OSError:
            continue
        fields, _ = _parse_frontmatter(text)
        confidence = fields.get("confidence", "").strip().lower()
        if confidence != "low":
            continue
        # This finding must appear in limitations.tex
        # Match by file stem (id) or title
        finding_id = finding_note.stem.lower()
        title = fields.get("title", "").strip().lower()
        if finding_id not in limitations_text and (not title or title not in limitations_text):
            errors.append(
                f"J-1 confidence-completeness: finding '{finding_note.stem}' has "
                f"confidence: low but does not appear in limitations.tex — "
                f"BLOCK. Every low-confidence finding must be named in limitations. "
                f"A silently-dropped caveat is the top integrity risk."
            )

    return errors


# ---------------------------------------------------------------------------
# Gate K-1: preregistration completeness (SR-MS-2 §5J.13-A (5))
# ---------------------------------------------------------------------------

def check_preregistration_completeness(
    note_path: Path,
    *,
    plan_note_path: Path | None = None,
    notes_root: Path | None = None,
) -> list[str]:
    """K-1: every plan_role: main child in the preregistration covers: must be accounted for.

    Ground truth = the plan master's covers: set filtered to plan_role: main.
    BLOCK if any main child is absent from both the synthesized_okf scope AND
    the inclusion ledger with an explicit reason.

    Graceful when absent: no preregistration master → no check.
    """
    from research_vault.note import _parse_frontmatter

    if plan_note_path is None:
        return []  # No preregistration master in scope — K-1 passes trivially

    if not plan_note_path.exists():
        return []

    try:
        plan_text = plan_note_path.read_text(encoding="utf-8")
    except OSError:
        return []

    plan_fields, _ = _parse_frontmatter(plan_text)
    plan_kind = plan_fields.get("plan_kind", "").strip().lower()
    if plan_kind != "preregistration":
        return []  # Not a preregistration master

    covers_raw = plan_fields.get("covers", "").strip()
    if not covers_raw:
        return []

    # Parse the covers: list (flat YAML inline list "[a, b, c]" or comma-separated)
    if covers_raw.startswith("["):
        covers_raw = covers_raw[1:]
    if covers_raw.endswith("]"):
        covers_raw = covers_raw[:-1]
    covers_ids = [c.strip() for c in covers_raw.split(",") if c.strip()]

    if not covers_ids:
        return []

    # Find children with plan_role: main
    main_children: list[str] = []
    _notes_root = notes_root or (note_path.parent.parent if note_path else None)
    for child_id in covers_ids:
        if _notes_root is None:
            break
        child_path = _notes_root / f"{child_id}.md"
        if not child_path.exists():
            # Try sub-dirs
            for sub in ("experiments", "findings"):
                cand = _notes_root / sub / f"{child_id.split('/')[-1]}.md"
                if cand.exists():
                    child_path = cand
                    break
        if not child_path.exists():
            continue
        try:
            ctext = child_path.read_text(encoding="utf-8")
        except OSError:
            continue
        cf, _ = _parse_frontmatter(ctext)
        if cf.get("plan_role", "").strip().lower() == "main":
            main_children.append(child_id)

    if not main_children:
        return []

    # Check which main children appear in synthesized_okf
    scope_str = ""
    if note_path and note_path.exists():
        try:
            note_text = note_path.read_text(encoding="utf-8")
            nf, _ = _parse_frontmatter(note_text)
            scope_str = nf.get("synthesized_okf", "").strip().lower()
        except Exception:
            pass

    errors: list[str] = []
    for child_id in main_children:
        child_base = child_id.split("/")[-1].lower()
        if child_base in scope_str or child_id.lower() in scope_str:
            continue  # In scope — OK
        # Check inclusion ledger in the gather-scope section (best-effort text search)
        # Derive tree_root from note_path: manuscript/<id>.md → manuscripts/<id>/
        _tree_root: Path | None = None
        if note_path and note_path.exists():
            _ms_id = note_path.stem
            _tree_root = note_path.parent.parent / "manuscripts" / _ms_id
        gather_tex = (_tree_root / "sections" / "gather-scope.tex") if _tree_root else None
        if gather_tex and gather_tex.exists():
            try:
                gt = gather_tex.read_text(encoding="utf-8").lower()
                if child_base in gt or child_id.lower() in gt:
                    continue  # In ledger (may be EXCLUDED with reason)
            except OSError:
                pass

        errors.append(
            f"K-1 preregistration completeness: plan_role: main child '{child_id}' "
            f"is absent from both synthesized_okf scope and the inclusion ledger — "
            f"BLOCK. Either include it in synthesis or explicitly exclude it with a reason "
            f"in the gather-scope section."
        )

    return errors


# ---------------------------------------------------------------------------
# Semantic gate: strength-monotonicity (SR-MS-2 §5J.13-C)
# ---------------------------------------------------------------------------

# Section ordering (strongest claims → weakest intro framing allowed)
# findings → results-discussion → conclusion → introduction → abstract
# Monotonic: no claim in a later section should be STRONGER than in an earlier one.
_SECTION_ORDER = [
    "results-discussion",
    "limitations",
    "conclusion",
    "introduction",
    "abstract",
]

# Explicit strength-inversion patterns: hedged finding rendered as unhedged abstract claim.
# These are D-MS-5 BLOCK triggers when found in abstract/intro but not in body.
_HEDGE_IN_BODY_RE = re.compile(
    r"\b(suggests?|may indicate|is consistent with|appears? to|"
    r"we observe(d)?|tentatively|might|could be|possibly)\b",
    re.IGNORECASE,
)
_UNHEDGED_IN_INTRO_RE = re.compile(
    r"\b(we show|we establish|we prove|we demonstrate|"
    r"definitively|unambiguously|it is clear that|we confirm)\b",
    re.IGNORECASE,
)


def check_strength_monotonicity(
    tree_root: Path,
    *,
    judge_fn: "Any | None" = None,
    judge_model: str = _DEFAULT_JUDGE_MODEL,
    config: "Any | None" = None,
) -> tuple[list[str], list[str]]:
    """Strength-monotonicity check (SR-MS-2 §5J.13-C).

    D-MS-5 RESOLVED:
      - WARN by default for general drift (slightly stronger paraphrase)
      - BLOCK on an explicit strength inversion (hedged finding → unhedged abstract claim)

    The structural check (lexical patterns) catches clear inversions without an LLM call.
    For subtler drift, the judge_fn is used if provided.

    Returns (errors, warnings):
      errors:   BLOCK-level inversions
      warnings: WARN-level drift
    """
    sections_dir = tree_root / "sections"
    if not sections_dir.exists():
        return [], []

    errors: list[str] = []
    warnings: list[str] = []

    # Read body sections (findings/results) and compare against abstract/intro
    body_text = ""
    for section_name in ("results-discussion", "limitations"):
        p = sections_dir / f"{section_name}.tex"
        if p.exists():
            try:
                body_text += p.read_text(encoding="utf-8") + "\n"
            except OSError:
                pass

    abstract_path = sections_dir / "abstract.tex"
    intro_path = sections_dir / "introduction.tex"

    abstract_text = ""
    intro_text = ""
    if abstract_path.exists():
        try:
            abstract_text = abstract_path.read_text(encoding="utf-8")
        except OSError:
            pass
    if intro_path.exists():
        try:
            intro_text = intro_path.read_text(encoding="utf-8")
        except OSError:
            pass

    if not body_text or (not abstract_text and not intro_text):
        return [], []  # Not enough sections to check yet

    # Lexical structural check:
    # Body has hedged claims → abstract/intro has unhedged → BLOCK (D-MS-5 inversion)
    body_has_hedges = bool(_HEDGE_IN_BODY_RE.search(body_text))
    abstract_unhedged = bool(_UNHEDGED_IN_INTRO_RE.search(abstract_text))
    intro_unhedged = bool(_UNHEDGED_IN_INTRO_RE.search(intro_text))

    if body_has_hedges and abstract_unhedged:
        errors.append(
            "strength-monotonicity BLOCK: the results/limitations sections contain "
            "hedged claims (suggests/may indicate/appears) but the abstract contains "
            "unhedged confirmatory language (we show/establishes/proves). "
            "A hedged finding rendered as an unhedged abstract claim is a D-MS-5 inversion. "
            "Qualify the abstract claim to match the body's confidence level."
        )
    elif body_has_hedges and intro_unhedged:
        errors.append(
            "strength-monotonicity BLOCK: the results/limitations sections contain "
            "hedged claims but the introduction contains unhedged confirmatory language. "
            "Qualify the introduction claim to match the body's confidence level."
        )
    elif abstract_unhedged and not body_has_hedges:
        # No hedges in body, unhedged abstract — WARN only (could be legitimately high confidence)
        warnings.append(
            "strength-monotonicity WARN: abstract contains strong confirmatory language. "
            "Verify this matches the body's confidence level."
        )

    return errors, warnings


# ---------------------------------------------------------------------------
# Semantic gate: support-matcher tally (SR-MS-2 §5J.13-A (3))
# ---------------------------------------------------------------------------

def check_support_tally(
    tree_root: Path,
    *,
    notes_root: Path | None = None,
    judge_fn: "Any | None" = None,
    judge_model: str = _DEFAULT_JUDGE_MODEL,
    rubric_override: str | None = None,
    config: "Any | None" = None,
) -> "dict[str, Any]":
    r"""Run the claim→source support-matcher on all (sentence, \\cite{key}) pairs.

    For each sentence containing a \\cite{}, calls match_support() with the
    cited literature/ note's structured fields.

    Returns a dict with:
      "verdicts":     list of SupportVerdict (one per (sentence, citekey) pair)
      "n_sentences":  int
      "m_citations":  int
      "k_block":      int (ABSENT or CONTRADICTS)
      "j_warn":       int (PARTIAL)
      "honest_report": str — "N sentences, M citations, k BLOCK, j WARN"
      "errors":       list of BLOCK-level strings (for check_manuscript return)
      "warnings":     list of WARN-level strings

    BLOCK on [ABSENT] / [CONTRADICTS]; WARN on [PARTIAL].
    Honest output: 'N sentences, M citations, k BLOCK, j WARN' — never 'verified'.

    When notes_root is None: tries to infer from tree_root (sibling 'literature/' dir).
    """
    from research_vault.manuscript.support_matcher import match_support, SupportVerdict

    tex_files = list(tree_root.rglob("*.tex"))
    if not tex_files:
        return {
            "verdicts": [], "n_sentences": 0, "m_citations": 0,
            "k_block": 0, "j_warn": 0,
            "honest_report": "0 sentences, 0 citations, 0 BLOCK, 0 WARN",
            "errors": [], "warnings": [],
        }

    # Infer notes_root: tree_root is manuscripts/<id>/, notes_root is project notes dir
    # (the parent of manuscripts/, typically the project_notes_dir)
    _notes_root = notes_root
    if _notes_root is None:
        _notes_root = tree_root.parent.parent  # manuscripts/<id>/ → project root

    # Collect all sentences with \cite{} (sentence = text line containing a \cite)
    # Simple sentence heuristic: split on periods / newlines (for performance)
    all_items: list[tuple[str, str]] = []  # (sentence, citekey)
    for tex in tex_files:
        if not tex.exists():
            continue
        try:
            text = tex.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = _strip_comments(text)
        # Split into sentences (rough heuristic)
        sentences = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            for cm in _CITE_RE.finditer(sent):
                for k in cm.group(1).split(","):
                    k = k.strip()
                    if k:
                        all_items.append((sent, k))

    verdicts: list[Any] = []
    errors: list[str] = []
    warnings: list[str] = []
    n_sentences = len({item[0] for item in all_items})
    m_citations = len(all_items)

    for sentence, citekey in all_items:
        # Find the note: try literature/<citekey>.md
        note_path = _notes_root / "literature" / f"{citekey}.md"
        if not note_path.exists():
            note_path = _notes_root / f"{citekey}.md"

        # Read stance/plan_role for J-2 input
        stance: str | None = None
        plan_role: str | None = None
        if note_path.exists():
            try:
                ntext = note_path.read_text(encoding="utf-8")
            except OSError:
                ntext = ""
            from research_vault.note import _parse_frontmatter as _pfm
            nf, _ = _pfm(ntext)
            stance = nf.get("stance") or None
            plan_role = nf.get("plan_role") or None

        v = match_support(
            claim=sentence,
            citekey=citekey,
            note_path=note_path,
            stance=stance,
            plan_role=plan_role,
            rubric_override=rubric_override,
            config=config,
            judge_fn=judge_fn,
            judge_model=judge_model,
        )
        verdicts.append(v)

        if v.blocks:
            errors.append(
                f"support-matcher [{v.verdict}] BLOCK: \\cite{{{citekey}}} — "
                f"claim: '{sentence[:120]}' — "
                f"quoted span: {v.verbatim_span or 'none'} — "
                f"reasoning: {v.reasoning[:200]}"
            )
        elif v.warns:
            warnings.append(
                f"support-matcher [PARTIAL] WARN: \\cite{{{citekey}}} — "
                f"claim: '{sentence[:120]}' — "
                f"reasoning: {v.reasoning[:200]}"
            )

    k_block = sum(1 for v in verdicts if v.blocks)
    j_warn = sum(1 for v in verdicts if v.warns)

    return {
        "verdicts": verdicts,
        "n_sentences": n_sentences,
        "m_citations": m_citations,
        "k_block": k_block,
        "j_warn": j_warn,
        "honest_report": (
            f"{n_sentences} sentences, {m_citations} citations, {k_block} BLOCK, {j_warn} WARN"
        ),
        "errors": errors,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Critic node logic (SR-MS-2 §5J.13-A (2))
# ---------------------------------------------------------------------------

def run_critic(
    tree_root: Path,
    *,
    judge_fn: "Any | None" = None,
    judge_model: str = _DEFAULT_JUDGE_MODEL,
    config: "Any | None" = None,
) -> dict[str, Any]:
    """Critic node: worst-three anti-positivity review of the compiled manuscript.

    Anti-positivity moves (§5J.13-A (2)):
      (1) Disconfirming-read-first — explicitly seek overclaims and elided caveats.
      (2) Do NOT use the paper's own abstract/thesis as a prior.
      (3) Two-sided rubric — assess both supporting and contradicting evidence.
      (4) Worst-three-even-on-a-clean-draft rule — always emit ≥3 findings.

    Reads the compiled PDF via pdftotext (if available) and main.tex.
    Returns:
      "findings":  list of ≥3 strings (worst-three findings, even on a clean draft)
      "errors":    BLOCK-level (if judge_fn raises and no fallback)
      "warnings":  WARN-level
      "raw_response": the judge's full response
    """
    import shutil
    import subprocess

    # Collect manuscript text
    ms_text_parts: list[str] = []

    # Try pdftotext first (reads compiled PDF — most faithful)
    pdf_files = list(tree_root.glob("*.pdf"))
    if pdf_files and shutil.which("pdftotext"):
        try:
            r = subprocess.run(
                ["pdftotext", str(pdf_files[0]), "-"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0 and r.stdout.strip():
                ms_text_parts.append(r.stdout[:8000])
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Fallback: read main.tex + key sections
    if not ms_text_parts:
        main_tex = tree_root / "main.tex"
        if main_tex.exists():
            try:
                ms_text_parts.append(main_tex.read_text(encoding="utf-8")[:4000])
            except OSError:
                pass
        for sname in ("abstract", "introduction", "results-discussion", "limitations"):
            sp = tree_root / "sections" / f"{sname}.tex"
            if sp.exists():
                try:
                    ms_text_parts.append(sp.read_text(encoding="utf-8")[:2000])
                except OSError:
                    pass

    if not ms_text_parts:
        return {
            "findings": [
                "Critic cannot run: no compiled PDF or manuscript sections found. "
                "Run `rv manuscript compile` first."
            ],
            "errors": [],
            "warnings": [
                "critic: no manuscript content available — run compile before critic."
            ],
            "raw_response": "",
        }

    ms_text = "\n\n".join(ms_text_parts)[:10000]

    # Build critic prompt (anti-positivity baked in)
    critic_prompt = (
        "You are a rigorous academic critic performing an anti-positivity review.\n\n"
        "MANDATORY ANTI-POSITIVITY MOVES:\n"
        "(1) DISCONFIRMING READ FIRST: before anything else, actively search for where "
        "the paper overclaims, elides a caveat, or cites a source that may not support "
        "the claim.\n"
        "(2) DO NOT USE THE PAPER'S OWN ABSTRACT/THESIS AS A PRIOR. Judge each claim "
        "against the cited literature fields and the stated results.\n"
        "(3) TWO-SIDED RUBRIC: for each finding, assess both what supports AND what "
        "contradicts the paper's claims before settling on a critique.\n"
        "(4) WORST-THREE MANDATORY: you MUST report your three worst findings even if "
        "the draft looks good. 'Looks good' is NOT a permitted output. If you cannot "
        "find genuine problems, report the weakest claims as your worst-three.\n\n"
        f"=== MANUSCRIPT TEXT ===\n{ms_text}\n\n"
        "=== CRITIC REPORT ===\n"
        "Report exactly three or more findings in this format:\n"
        "FINDING 1: [PARTIAL|ABSENT|CONTRADICTS] — <description>\n"
        "FINDING 2: [PARTIAL|ABSENT|CONTRADICTS] — <description>\n"
        "FINDING 3: [PARTIAL|ABSENT|CONTRADICTS] — <description>\n"
        "(add more as needed)\n"
        "SUMMARY: <one-sentence overall assessment>"
    )

    _judge = judge_fn if judge_fn is not None else None

    if _judge is None:
        # If no judge_fn, try the default (may fail if no API key)
        from research_vault.manuscript.support_matcher import _default_judge_fn
        _judge = _default_judge_fn

    try:
        raw_response = _judge(critic_prompt)
    except Exception as e:  # noqa: BLE001
        return {
            "findings": [
                f"Critic judge call failed: {e}. "
                "Ensure ANTHROPIC_API_KEY is set or pass judge_fn= for testing."
            ],
            "errors": [],
            "warnings": [f"critic: judge call failed: {e}"],
            "raw_response": "",
        }

    # Parse findings from response
    findings: list[str] = []
    for fm in re.finditer(r"FINDING \d+:\s*(.+?)(?=FINDING \d+:|SUMMARY:|$)", raw_response, re.DOTALL):
        finding_text = fm.group(1).strip()
        if finding_text:
            findings.append(finding_text[:400])

    if len(findings) < 3:
        # Fallback: the whole response as one finding (anti-positivity: never suppress)
        findings = [raw_response[:400]]

    return {
        "findings": findings,
        "errors": [],
        "warnings": [],
        "raw_response": raw_response,
    }


# ---------------------------------------------------------------------------
# Decision payload assembly for approve-manuscript (SR-MS-2 §5J.13-D)
# ---------------------------------------------------------------------------

def build_approve_payload(
    note_path: Path,
    tree_root: Path,
    *,
    notes_root: Path | None = None,
    plan_note_path: Path | None = None,
    findings_notes: list[Path] | None = None,
    experiment_notes: list[Path] | None = None,
    judge_fn: "Any | None" = None,
    judge_model: str = _DEFAULT_JUDGE_MODEL,
    rubric_override: str | None = None,
    config: "Any | None" = None,
    page_limit: int | None = None,
) -> dict[str, Any]:
    """Assemble the full approve-manuscript human-go DECISION payload (§5J.13-D).

    This is the gate that presents a DECISION, not a diff. It runs:
      1. Support-matcher tally (§5J.13-D.1)
      2. Hash-drift re-verify (§5J.13-D.2)
      3. Critic worst-three (§5J.13-D.3)
      4. Naked-citation candidates: auto-links + surfaced (§5J.13-D.4)
      5. Strength-monotonicity flags (§5J.13-D.5)
      6. J-1 / K-1 completeness (§5J.13-D.6)

    Also runs the structural extension gates (dedup, page-limit, cite-provenance).

    Returns a dict with all payload sections, all_ok, errors, warnings,
    and meta_dict (for RunState.meta['support_matcher'] logging).

    The machine spotlights + tallies; the human judges.
    crew-cannot-self-approve: a green payload still requires the human's explicit go.

    sr: SR-MS-2
    """
    from research_vault.manuscript.naked_cite import resolve_naked_citations

    tex_files = list(tree_root.rglob("*.tex"))
    errors: list[str] = []
    warnings: list[str] = []

    # ── Structural extensions ─────────────────────────────────────────────────

    # Gate 5: dedup
    dup_errors, dup_warnings = check_dedup(tree_root, tex_files)
    errors.extend(dup_errors)
    warnings.extend(dup_warnings)

    # Gate 6: page-limit
    pg_issues = check_page_limit(tree_root, page_limit=page_limit, config=config)
    # Differentiate: "exceeds" → error, "pdftotext absent" → warning
    for pg in pg_issues:
        if "exceeds" in pg:
            errors.append(pg)
        else:
            warnings.append(pg)

    # Gate 7 (B): cite provenance
    prov_errors, vouch_list = check_cite_provenance(tree_root, tex_files)
    errors.extend(prov_errors)

    # Gate 8: hash-drift
    drift_errors = check_hash_drift(note_path, tree_root, experiment_notes)
    errors.extend(drift_errors)

    # ── Semantic gates ────────────────────────────────────────────────────────

    # J-1: confidence-completeness
    j1_errors = check_confidence_completeness(note_path, tree_root, findings_notes=findings_notes)
    errors.extend(j1_errors)

    # K-1: preregistration completeness
    k1_errors = check_preregistration_completeness(
        note_path,
        plan_note_path=plan_note_path,
        notes_root=notes_root or (note_path.parent.parent if note_path else None),
    )
    errors.extend(k1_errors)

    # Strength-monotonicity
    mono_errors, mono_warnings = check_strength_monotonicity(
        tree_root, judge_fn=judge_fn, judge_model=judge_model, config=config,
    )
    errors.extend(mono_errors)
    warnings.extend(mono_warnings)

    # Support-matcher tally
    tally = check_support_tally(
        tree_root,
        notes_root=notes_root,
        judge_fn=judge_fn,
        judge_model=judge_model,
        rubric_override=rubric_override,
        config=config,
    )
    errors.extend(tally["errors"])
    warnings.extend(tally["warnings"])

    # Critic worst-three
    critic_result = run_critic(
        tree_root, judge_fn=judge_fn, judge_model=judge_model, config=config,
    )
    warnings.extend(critic_result["warnings"])

    # Naked-citation candidates
    refs_bib = tree_root / "refs.bib"
    _notes_root = notes_root or (tree_root.parent.parent if tree_root else None)
    naked_results: list[Any] = []
    for tex in tex_files:
        if not tex.exists():
            continue
        try:
            tex_text = tex.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tex_text = _strip_comments(tex_text)
        sentences = re.split(r"(?<=[.!?])\s+|\n{2,}", tex_text)
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            results = resolve_naked_citations(
                sent, refs_bib,
                notes_root=_notes_root,
                judge_fn=judge_fn,
                judge_model=judge_model,
                rubric_override=rubric_override,
                config=config,
            )
            naked_results.extend(results)
            # Warn on unresolved naked citations
            for r in results:
                if r.status.startswith("warn"):
                    warnings.append(f"naked-cite: {r.payload_line}")

    # ── Assemble payload ──────────────────────────────────────────────────────
    payload: dict[str, Any] = {
        # §5J.13-D.1
        "support_tally": tally["honest_report"],
        "support_verdicts": [
            {
                "verdict": v.verdict,
                "citekey": v.citekey,
                "claim_snippet": v.claim[:120],
                "verbatim_span": v.verbatim_span,
                "j2_escalation": v.j2_escalation,
            }
            for v in tally["verdicts"]
            if v.blocks or v.warns
        ],
        # §5J.13-D.2
        "hash_drift": drift_errors or ["no drift detected"],
        # §5J.13-D.3
        "critic_worst_three": critic_result["findings"][:3],
        "critic_all_findings": critic_result["findings"],
        # §5J.13-D.4
        "naked_cite_auto_links": [
            r.payload_line for r in naked_results if r.status in ("auto-linked", "disambiguated")
        ],
        "naked_cite_surfaced": [
            r.payload_line for r in naked_results if r.status.startswith("warn")
        ],
        # §5J.13-D.5
        "strength_monotonicity": mono_errors + mono_warnings,
        # §5J.13-D.6
        "j1_k1_blocks": j1_errors + k1_errors,
        # Human-vouch list (§5J.13-D, D-MS-6)
        "provenance_human_vouch": vouch_list,
        # Meta
        "errors": errors,
        "warnings": warnings,
        "all_ok": len(errors) == 0,
        "meta": tally.get("verdicts") and {
            "judge_model": judge_model,
            "n_sentences": tally["n_sentences"],
            "m_citations": tally["m_citations"],
        } or {},
    }
    return payload


# ---------------------------------------------------------------------------
# Extended check_manuscript (adds structural SR-MS-2 gates)
# ---------------------------------------------------------------------------

def check_manuscript(
    note_path: Path,
    tree_root: Path,
    *,
    experiment_notes: list[Path] | None = None,
    tex_files: list[Path] | None = None,
    page_limit: int | None = None,
    config: "Any | None" = None,
) -> dict[str, Any]:
    """Run all structural gates for rv manuscript check.

    When to use: ``rv manuscript check <project> <id>`` — run the structural
    grounding gates before the semantic ones. Structural gates are cheap,
    binary, and do not require an LLM.

    HONEST BOUNDARY: this function runs the STRUCTURAL gates only. For the
    full semantic check (support-matcher, J-1, J-2, K-1, strength-monotonicity,
    critic), call build_approve_payload(). The honest boundary is documented in
    the module docstring and the rv manuscript check help text.

    Gates run here:
      1. Unmatched \\cite resolution (SR-MS-1b)
      2. Figure-file existence (SR-MS-1b)
      3. Compile-success check (SR-MS-1b)
      4. Data-code-availability sentinel cross-check (SR-MS-1b)
      5. Dedup — repeated \\cite / duplicate .bib keys (SR-MS-2)
      6. Page-limit via pdftotext (SR-MS-2, optional)
      7. (B) Citekey-provenance — DOI/arXiv/S2 id OFFLINE check (SR-MS-2)

    Semantic gates (J-1, J-2, K-1, strength-mono, support-matcher, critic) are
    NOT run here — they require an LLM judge. Use build_approve_payload() for
    the full approve-manuscript gate.

    Args:
        note_path: path to the manuscript/<id>.md OKF note.
        tree_root: path to manuscripts/<id>/ artifact tree.
        experiment_notes: list of scoped experiments/ note paths.
        tex_files: list of .tex files to scan. When None, rglob tree_root.
        page_limit: optional page limit (int). Overrides config.
        config: optional Config for page_limit and other settings.

    Returns:
        dict with:
          "errors": list of hard error strings
          "warnings": list of warning strings
          "provenance_human_vouch": list of citekeys passing via human-vouch
          "all_ok": bool (True iff errors is empty)

    sr: SR-MS-1b (gates 1–4) + SR-MS-2 (gates 5–7)
    """
    from research_vault.note import _parse_frontmatter
    from research_vault.config import load_config

    errors: list[str] = []
    warnings: list[str] = []

    # ── Resolve experiment notes if not provided ───────────────────────────
    if experiment_notes is None:
        experiment_notes = []
        if note_path.exists():
            text = note_path.read_text(encoding="utf-8")
            fields, _ = _parse_frontmatter(text)
            scope_str = fields.get("synthesized_okf", "").strip()
            if scope_str:
                try:
                    cfg = load_config()
                    # Extract project from note path (heuristic: manuscript/<id>.md
                    # lives under project_notes_dir/<project>/manuscript/)
                    # Walk up to find project_notes_dir
                    for scope_item in scope_str.split(","):
                        scope_item = scope_item.strip()
                        if scope_item.startswith("experiments/"):
                            exp_name = scope_item[len("experiments/"):]
                            # Try to find the experiment note relative to the
                            # manuscript note's project dir
                            candidate = note_path.parent.parent / "experiments" / f"{exp_name}.md"
                            if candidate.exists():
                                experiment_notes.append(candidate)
                except Exception:
                    pass

    if tex_files is None:
        tex_files = list(tree_root.rglob("*.tex"))

    # ── Gate 1: unmatched \\cite ───────────────────────────────────────────
    errors.extend(check_cite_resolution(tree_root, tex_files))

    # ── Gate 2: figure-file existence ─────────────────────────────────────
    errors.extend(check_figure_existence(tree_root, tex_files))

    # ── Gate 3: compile success (passive) ─────────────────────────────────
    errors.extend(check_compile_success(note_path, tree_root))

    # ── Gate 4: data-code-availability sentinel cross-check ───────────────
    warnings.extend(check_availability_sentinel(tree_root, experiment_notes))

    # ── Gate 5: dedup (SR-MS-2) ────────────────────────────────────────────
    dup_errors, dup_warnings = check_dedup(tree_root, tex_files)
    errors.extend(dup_errors)
    warnings.extend(dup_warnings)

    # ── Gate 6: page-limit (SR-MS-2) ──────────────────────────────────────
    pg_issues = check_page_limit(tree_root, page_limit=page_limit, config=config)
    for pg in pg_issues:
        if "exceeds" in pg:
            errors.append(pg)
        else:
            warnings.append(pg)

    # ── Gate 7 (B): cite provenance (SR-MS-2) ─────────────────────────────
    prov_errors, vouch_list = check_cite_provenance(tree_root, tex_files)
    errors.extend(prov_errors)

    return {
        "errors": errors,
        "warnings": warnings,
        "provenance_human_vouch": vouch_list,
        "all_ok": len(errors) == 0,
    }
