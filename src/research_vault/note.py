"""note.py — OKF note creation and listing for a project.

When to use: use `rv note <project> <type> …` to create or list OKF notes for a project.
Notes follow the Open Knowledge Format: markdown + YAML frontmatter with a required `type` field.
The type determines the subdirectory: literature/, concepts/, methods/, experiments/,
findings/, mocs/, datasets/, figures/, manuscript/.

Path resolution: always via Config — zero hardcoded paths.
Stdlib only.
"""

import argparse
import datetime
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config

# ---------------------------------------------------------------------------
# OKF note types
# ---------------------------------------------------------------------------

OKF_TYPES = frozenset({
    "literature",
    "concepts",
    "methods",
    "experiments",
    "findings",
    "mocs",
    "datasets",    # SR-8: provenance note for data artifacts (points to data, never contains it)
    "figures",     # SR-FIG: provenance note for publication figures (points to image, never embeds)
    "manuscript",  # SR-MS-1a: LaTeX-native POINTER note (metadata+provenance; points to manuscripts/<id>/)
    "gaps",        # SR-LR-2: typed research gap record (§5L.7-5L.8); project-scoped; first-class lifecycle
})

# SR-RESOLVE-SCOPE: the sole SHARED (cross-project) OKF type — lives in cfg.datasets_root.
# All other OKF types are PROJECT-SCOPED (cfg.project_notes_dir / type_dir).
# SSOT for the project-scoped-vs-shared split.
# Consumed by: wait_for (note: resolver), dag/verbs (_check_project_scoped_note).
# Do NOT duplicate this — import from here.
OKF_SHARED_TYPES: frozenset[str] = frozenset({"datasets"})

# SR-FIG: figures are PROJECT-SCOPED — deliberately NOT a shared root like datasets.
# A figures note for project A lives in project_notes_dir(A)/figures/, not in a shared root.
# datasets/ is the sole exception to project scoping (see SR-8); figures follows the
# standard 6-type pattern.
_FIGURES_REQUIRED_FIELDS = frozenset({"source_experiment", "experiment_results_hash"})

# SR-PLAN-2: valid values for stance + plan_role on child experiment notes.
_VALID_STANCE: frozenset[str] = frozenset({"confirmatory", "exploratory"})
_VALID_PLAN_ROLE: frozenset[str] = frozenset({
    "main", "supporting_ablation", "conditional_ablation"
})

# ---------------------------------------------------------------------------
# SR-EXP-REPRO: experiment reproducibility schema
# ---------------------------------------------------------------------------

# Sentinel value for all repro_* fields that are not (yet) populated.
# Anti-fabrication contract: NEVER write blank/guessed — write this visible hole.
# Doctrine: "OKF frontmatter is flat ^(\w+): — to attach structured/nested data,
# use a hashed artifact + promoted flat scalars, never inline JSON in frontmatter."
REPRO_SENTINEL = "not-recorded-in-provenance"

# Full ordered list of all repro_* fields (§5J.14 — 22 fields).
# Layer 1: hashed full-config artifact (tamper-evident ground truth).
REPRO_LAYER1 = [
    "repro_config_location",   # path to <exp>.config.json (full dict(run.config) dump)
    "repro_config_hash",       # sha256:<hex> of the config artifact
]
# Layer 2 — AUTO from run.config via alias table:
REPRO_AUTO_CONFIG = [
    "repro_seed",
    "repro_model_id",
    "repro_model_revision",
    "repro_decode_temperature",
    "repro_decode_top_p",
    "repro_decode_max_tokens",
    "repro_num_fewshot",
    "repro_tokenizer",
]
# Layer 2 — AUTO from run.metadata:
REPRO_AUTO_META = [
    "repro_env_packages",
    "repro_env_python",
    "repro_cost_gpu_hours",
]
# Layer 2 — AUTO from SR-6 manifest (deferred — do NOT re-probe):
REPRO_AUTO_HW = ["repro_hw"]
# Layer 2 — AUTO from linked SR-8 dataset note (links note + inherits its hash):
REPRO_AUTO_DATASET = ["repro_dataset_id", "repro_dataset_hash"]
# Layer 2 — AUTO from results_commit (only if in-repo):
REPRO_AUTO_HARNESS = ["repro_eval_harness"]
# Layer 2 — MANUAL (fabrication-risk surface — flag LOUDLY):
# Includes the cross-lingual trio (absent from generic checklists; critical for
# multilingual/cross-lingual evaluation):
#   repro_prompt_lang: BCP-47 code for instruction/exemplar language (≠ target lang)
#   repro_translation_provenance: "human" or "MT:<engine@ver>"
REPRO_MANUAL = [
    "repro_prompt_lang",
    "repro_translation_provenance",
    "repro_prompt_version",
    "repro_dataset_split",
    "repro_metric",
]

# All 22 fields in canonical order:
REPRO_ALL_FIELDS: list[str] = (
    REPRO_LAYER1
    + REPRO_AUTO_CONFIG
    + REPRO_AUTO_META
    + REPRO_AUTO_HW
    + REPRO_AUTO_DATASET
    + REPRO_AUTO_HARNESS
    + REPRO_MANUAL
)

# Fields required for the lint (warn when results_hash is set but these are still sentinel):
# All non-dataset fields (dataset linking is optional; hw deferral is acceptable).
REPRO_LINT_REQUIRED: list[str] = (
    REPRO_LAYER1
    + REPRO_AUTO_CONFIG
    + REPRO_AUTO_META
    + REPRO_MANUAL
)


def scaffold_okf_dirs(base: Path) -> None:
    """Create OKF note-type subdirectories under *base*.

    This is the canonical helper — callers (init, project new) MUST use this
    instead of re-listing the types, so note.OKF_TYPES stays the SSOT.
    """
    for note_type in OKF_TYPES:
        (base / note_type).mkdir(parents=True, exist_ok=True)


def _today() -> str:
    return datetime.date.today().isoformat()


def _slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:80] or "note"


def _render_frontmatter(fields: dict[str, str]) -> str:
    lines = ["---"]
    for key, val in fields.items():
        lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines)


def _parse_frontmatter(text: str) -> "tuple[dict[str, str | list[str]], str]":
    """Parse YAML-like frontmatter between --- delimiters.

    Handles both scalar fields and YAML indented-list fields (``  - item``):
    - Scalar: ``key: value`` → ``{"key": "value"}``
    - List:   ``key:\\n  - a\\n  - b`` → ``{"key": ["a", "b"]}``
    - Inline ``key: []`` syntax stays as the literal string ``"[]"`` (not a list).

    #26 convergence: this canonical parser now replaces the local
    ``gap_scan._parse_frontmatter_gap`` duplicate (SR-LR-2 STOP decision lifted).
    The extension is backwards-compatible for all existing callers: callers that
    do ``.strip()`` on results only access SCALAR fields (``synthesized_okf``,
    ``confidence``, ``plan_kind``, etc.); none of them access list-valued fields
    (``backed_by``, ``supported_by``, ``contradicted_by``), which are exclusively
    used by gap_scan.  Audit verified in #26 grep-before-extend pass.

    Return: (fields_dict, body_text)
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    fields: "dict[str, str | list[str]]" = {}
    current_list_key: "str | None" = None
    for line in fm_block.splitlines():
        # YAML indented list item: "  - item" (two-space indent + dash + space)
        if line.startswith("  - ") and current_list_key is not None:
            # Lazy-promote: first list item converts "" → [] before appending.
            # This preserves the old behaviour for empty keys that have NO list items
            # (they stay as "") — only keys WITH  - item lines become list[str].
            existing = fields[current_list_key]
            if isinstance(existing, str):
                fields[current_list_key] = []
            cast_list = fields[current_list_key]
            if isinstance(cast_list, list):
                cast_list.append(line[4:].strip())
            continue
        current_list_key = None
        m = re.match(r"^(\w[\w_-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val == "":
                # Empty value after colon → tentatively empty string; may become
                # list[str] if  - item lines follow (lazy-promote on first item).
                current_list_key = key
                fields[key] = ""
            else:
                if val.startswith(("'", '"')) and val.endswith(val[0]):
                    val = val[1:-1]
                fields[key] = val
    return fields, body


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_new(project: str, note_type: str, title: str, *,
            config: Config | None = None,
            note_id: str | None = None,
            tags: list[str] | None = None) -> Path:
    """Create a new OKF note of the given type for the given project.

    Returns the path to the created note file.
    Raises ValueError if note_type is not a valid OKF type.

    SR-8: for note_type == 'datasets', the template includes placeholder fields:
      location — path/URL/DOI of the actual data artifact (fill this in)
      hash     — content hash in sha256:<hex> format (fill this in)
    Anti-pattern: do NOT hand-copy a data path into a finding — file a datasets/
    provenance note and afterok on it, so lineage is structural.

    SR-FIG: for note_type == 'figures', use `rv figure new` (richer arguments).
    `rv note new figures` creates a skeleton note with placeholder fields.
    figures are PROJECT-SCOPED (not shared like datasets).
    The PRIMARY source is an experiments/ note (results_location/results_hash from SR-WB).
    """
    if note_type not in OKF_TYPES:
        raise ValueError(
            f"Unknown note type {note_type!r}. Valid types: {sorted(OKF_TYPES)}"
        )
    cfg = config or load_config()

    # SR-8: shared types (OKF_SHARED_TYPES) live in cfg.datasets_root, not in
    # the project-scoped notes directory. A shared-type note filed for one project
    # is visible and lineage-gatable from any other project.
    # SR-FIG: figures are PROJECT-SCOPED — live in project_notes_dir(project)/figures/
    # like the standard 6 types. This is a deliberate divergence from SR-8's shared root.
    # SR-HARDENING (fix 3b): use OKF_SHARED_TYPES SSOT — not a hardcoded "datasets"
    # string — so a 2nd shared type automatically routes correctly here.
    if note_type in OKF_SHARED_TYPES:
        notes_dir = cfg.datasets_root
    else:
        notes_dir = cfg.project_notes_dir(project) / note_type
    notes_dir.mkdir(parents=True, exist_ok=True)

    slug = note_id or _slugify(title)
    note_path = notes_dir / f"{slug}.md"
    if note_path.exists():
        slug = f"{slug}-{_today()}"
        note_path = notes_dir / f"{slug}.md"

    fields: dict[str, str] = {
        "type": note_type,
        "title": title,
        "created": _today(),
    }

    # SR-8: datasets notes carry provenance-specific placeholder fields
    if note_type == "datasets":
        fields["location"] = ""   # fill in: path/URL/DOI of the data artifact
        fields["hash"] = ""       # fill in: sha256:<hex> content hash of the artifact

    # SR-WB: experiments notes carry results provenance placeholder fields.
    # These are the PRIMARY results source — populated by `rv wandb pull --experiment`.
    # Flat prefixed fields (NOT nested block) — matches _parse_frontmatter contract.
    if note_type == "experiments":
        fields["results_location"] = ""   # path/URL of the metrics artifact
        fields["results_hash"] = ""       # sha256:<hex> of the artifact (for integrity)
        fields["results_wandb_run"] = ""  # W&B run id that produced these metrics
        fields["results_commit"] = ""     # git SHA of the code that produced the run
        # SR-EXP-REPRO: reproducibility schema — 22 flat repro_* fields.
        # Sentinel = "not-recorded-in-provenance" (NEVER blank, NEVER guessed).
        # Layer 1 (auto via rv wandb pull): hashed full-config artifact.
        # Layer 2 (auto via rv wandb pull alias table): promoted flat scalars.
        # MANUAL fields: cross-lingual trio + eval params — fill by hand; sentinel = honest hole.
        for repro_field in REPRO_ALL_FIELDS:
            fields[repro_field] = REPRO_SENTINEL

    # SR-FIG: figures notes carry provenance-specific placeholder fields.
    # Use `rv figure new` for richer creation (fills source_experiment + experiment_results_hash).
    if note_type == "figures":
        fields["source_experiment"] = ""          # fill in: experiments/<id> OKF link
        fields["experiment_results_hash"] = ""    # fill in: sha256:<hex> from experiment note
        fields["benchmark_dataset"] = ""          # optional: datasets/<id> for comparison overlay
        fields["select"] = ""                     # optional: comma-separated column list
        fields["filter"] = ""                     # optional: filter expression
        fields["plot_type"] = "line"              # default plot type
        fields["style"] = "publication"           # style preset: publication | slide | poster
        fields["rendered"] = "false"              # set to true after rv figure render

    # SR-MS-1a: manuscript notes are LaTeX-native POINTER notes — metadata + provenance.
    # Prose lives in .tex files; this note records lineage and points to the artifacts.
    # Use `rv manuscript new` for richer creation (scaffolds the DAG + tree).
    # All fields are FLAT prefixed — matches _parse_frontmatter contract (note.py:76).
    if note_type == "manuscript":
        fields["manuscript_location"] = ""  # fill in: path to manuscripts/<id>/main.tex
        fields["manuscript_pdf"] = ""       # fill in: path to compiled <id>.pdf (set by compile)
        fields["manuscript_hash"] = ""      # fill in: sha256:<hex> of the compiled PDF
        fields["thesis"] = ""              # fill in: one-sentence claim the paper argues
        fields["synthesized_okf"] = ""     # fill in: comma-list of OKF note ids synthesized
        fields["section_outline"] = ""     # fill in: ordered section ids (DAG section nodes)
        fields["dag_run"] = ""             # fill in: drafting-DAG run_id (provenance)

    if tags:
        fields["tags"] = "[" + ", ".join(tags) + "]"

    if note_type == "datasets":
        body = (
            "\n"
            "<!-- Datasets provenance note (SR-8) -->\n"
            "<!-- Fill in 'location' and 'hash' above before completing the DAG node. -->\n"
            "<!--   location: /path/to/data.csv  OR  https://...  OR  doi:10.xxx/... -->\n"
            "<!--   hash: sha256:<hex>  (run: sha256sum <file>) -->\n"
            "\n"
            "## What this dataset is\n\n"
            "<!-- Describe the dataset: domain, size, format, collection method. -->\n\n"
            "## Provenance\n\n"
            "<!-- Which step/commit/input-datasets produced this? -->\n\n"
            "## Schema\n\n"
            "<!-- Column/field descriptions (optional — used for schema-shape validation). -->\n"
        )
    elif note_type == "experiments":
        body = (
            "\n"
            "<!-- Experiments provenance note (SR-WB + SR-EXP-REPRO) -->\n"
            "<!-- Run `rv wandb pull <run-id> --experiment <id> --project <slug>` -->\n"
            "<!-- to fill results_location/results_hash/results_wandb_run/results_commit, -->\n"
            "<!-- plus all auto repro_* fields (Layer 1 + Layer 2 alias map). -->\n"
            "<!-- Or fill them by hand for CSV/manual fallback (results_hash = sha256:<hex>). -->\n"
            "<!-- MANUAL repro_* fields: repro_prompt_lang (BCP-47), -->\n"
            "<!--   repro_translation_provenance (human / MT:<engine@ver>), -->\n"
            "<!--   repro_prompt_version, repro_dataset_split, repro_metric. -->\n"
            "<!-- Anti-fabrication: use 'not-recorded-in-provenance' not blank/guessed. -->\n"
            "\n"
            "## Hypothesis\n\n"
            "<!-- What were you testing? -->\n\n"
            "## Setup\n\n"
            "<!-- Model, dataset, hyperparameters, cluster config. -->\n\n"
            "## Analysis\n\n"
            "<!-- What do the results mean? -->\n"
        )
    elif note_type == "figures":
        body = (
            "\n"
            "<!-- Figures provenance note (SR-FIG) -->\n"
            "<!-- Use `rv figure new <fig-id> --experiment <experiments/id>` for richer creation. -->\n"
            "<!-- Fill in 'source_experiment' and 'experiment_results_hash' from the experiment note. -->\n"
            "\n"
            "## What this figure shows\n\n"
            "<!-- Describe the figure: what it plots, the key message. -->\n\n"
            "## Render lineage\n\n"
            "<!-- Filled by `rv figure render` — rv version, timestamp, image paths. -->\n"
        )
    elif note_type == "manuscript":
        body = (
            "\n"
            "<!-- Manuscript provenance note (SR-MS-1a) -->\n"
            "<!-- Use `rv manuscript new <project> <id> --thesis '...'` for richer creation. -->\n"
            "<!-- That command also scaffolds manuscripts/<id>/{main.tex,sections/,refs.bib,results.tex} -->\n"
            "<!-- and emits the drafting-DAG manifest — use `rv dag run` to drive the loop. -->\n"
            "<!-- NEVER hand-type citations or results numbers — use the closed .bib + results macros. -->\n"
            "\n"
            "## Thesis\n\n"
            "<!-- The one-sentence claim this paper argues (set by --thesis). -->\n\n"
            "## Scope\n\n"
            "<!-- OKF notes synthesized: findings/, experiments/, methods/, concepts/ notes. -->\n"
            "<!-- Fill synthesized_okf above with comma-separated ids. -->\n\n"
            "## Provenance\n\n"
            "<!-- Filled by rv manuscript compile: manuscript_hash = sha256 of the compiled PDF. -->\n"
            "<!-- dag_run = the drafting-DAG run_id whose afterok lineage produced the sections. -->\n"
        )
    else:
        body = "\n<!-- Write your note here -->\n"

    note_path.write_text(_render_frontmatter(fields) + "\n" + body, encoding="utf-8")
    return note_path


def cmd_list(project: str, note_type: str | None = None, *,
             config: Config | None = None) -> list[dict[str, Any]]:
    """List OKF notes for the given project.

    If note_type is given, list only that type's subdirectory.
    Returns list of {path, fields} dicts.

    SR-8: datasets are SHARED — cmd_list for note_type='datasets' scans
    cfg.datasets_root rather than the project-scoped notes directory.
    SR-FIG: figures are PROJECT-SCOPED — scanned from project_notes_dir/figures/.
    """
    cfg = config or load_config()
    base = cfg.project_notes_dir(project)

    if note_type:
        types_to_scan = [note_type]
    else:
        types_to_scan = sorted(OKF_TYPES)

    notes = []
    for t in types_to_scan:
        # SR-8 / SR-HARDENING (fix 3b): shared types live in the shared root, not
        # project_notes_dir/<type>/. Use OKF_SHARED_TYPES SSOT, not a hardcoded
        # "datasets" string, so a 2nd shared type routes correctly automatically.
        if t in OKF_SHARED_TYPES:
            subdir = cfg.datasets_root
        else:
            subdir = base / t
        if not subdir.exists():
            continue
        for p in sorted(subdir.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            fields, _ = _parse_frontmatter(text)
            notes.append({"path": p, "fields": fields})
    return notes


def cmd_check(project: str, *, config: Config | None = None) -> list[str]:
    """Validate OKF notes for the given project.

    Checks that:
    - Each note has a `type` frontmatter field
    - The `type` value matches its parent directory name (non-datasets types)
    - The `type` is a known OKF type
    - SR-8: datasets notes (scanned from cfg.datasets_root) have non-empty
      `location` and `hash` fields. The type-dir check is skipped for datasets
      since datasets_root may have any directory name.
    - SR-FIG: figures notes (scanned from project_notes_dir/figures/) have non-empty
      `source_experiment` and `experiment_results_hash` fields (provenance required).

    SR-8 note: datasets are SHARED across projects. cmd_check scans
    cfg.datasets_root for the datasets type (same root for all projects);
    the 8 other OKF types remain project-scoped in project_notes_dir.

    SR-FIG note: figures are PROJECT-SCOPED (unlike datasets). Each project's
    figures are scanned from project_notes_dir(project)/figures/ independently.

    SR-MS-1a note: manuscript notes are PROJECT-SCOPED. When manuscript_pdf is
    non-empty, cmd_check verifies the PDF exists and its sha256 matches
    manuscript_hash (the PDF-hash provenance branch; parallel to SR-WB's
    check_result_provenance). Empty pdf/hash fields are NOT violations (unfilled).

    SR-PLAN-2 note: for experiments notes, cmd_check now also:
    - (plan masters) resolves each covers: child, verifies it EXISTS at the
      experiments/ directory, and checks it has valid stance (confirmatory|
      exploratory) + valid plan_role (main|supporting_ablation|
      conditional_ablation) — BLOCKS on any violation (§5K.7).
    - (child notes) BLOCKS when plan_role is set but stance is missing; BLOCKS
      when stance=confirmatory but the note is not in any plan master's covers:
      (degrade-to-skip when no plan masters exist); BLOCKS when supports_main
      points to a non-existent note.

    Returns a list of violation strings (empty = all clear).
    """
    cfg = config or load_config()
    base = cfg.project_notes_dir(project)
    violations = []

    for t in OKF_TYPES:
        # SR-8 / SR-HARDENING (fix 3b): shared types live in the shared root.
        # Use OKF_SHARED_TYPES SSOT — not a hardcoded "datasets" — so a 2nd
        # shared type is handled automatically.
        if t in OKF_SHARED_TYPES:
            subdir = cfg.datasets_root
        else:
            subdir = base / t
        if not subdir.exists():
            continue

        # SR-PLAN-2: pre-pass for experiments — collect covered_ids from all plan
        # masters so child notes can be checked for absent-from-covers (§5K.7).
        # Skipped for non-experiments types (covered_ids stays empty → no checks).
        covered_ids: set[str] = set()
        if t == "experiments":
            for _pre_p in sorted(subdir.glob("*.md")):
                try:
                    _pre_text = _pre_p.read_text(encoding="utf-8")
                except OSError:
                    continue
                _pre_fields, _ = _parse_frontmatter(_pre_text)
                if _pre_fields.get("plan_kind") == "preregistration":
                    for _cid in _parse_covers_list(
                        _pre_fields.get("covers", "")
                    ):
                        covered_ids.add(_cid)

        for p in sorted(subdir.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            fields, _ = _parse_frontmatter(text)
            note_type = fields.get("type", "")

            if not note_type:
                violations.append(f"{p}: missing 'type' frontmatter field")
                continue

            if note_type not in OKF_TYPES:
                violations.append(f"{p}: unknown type {note_type!r}")
                continue

            if t in OKF_SHARED_TYPES:
                # For shared types, the directory name may differ from the type name
                # (datasets_root can have any directory name) — check type == t.
                # SR-HARDENING (fix 3b): use OKF_SHARED_TYPES SSOT + `t` for the
                # inner check, so a 2nd shared type is handled automatically.
                if note_type != t:
                    violations.append(
                        f"{p}: expected type={t!r}, got {note_type!r}"
                    )
                # SR-8: datasets notes must have location and hash filled in.
                # Nested under `if t == "datasets"` because these fields are
                # datasets-specific; a future 2nd shared type has its own fields.
                if t == "datasets":
                    if not fields.get("location", "").strip():
                        violations.append(
                            f"{p}: datasets note missing 'location' field "
                            f"(path/URL/DOI of the actual data artifact)"
                        )
                    if not fields.get("hash", "").strip():
                        violations.append(
                            f"{p}: datasets note missing 'hash' field "
                            f"(content hash in sha256:<hex> format)"
                        )
            elif t == "experiments":
                # Standard OKF type-dir contract
                if note_type != t:
                    violations.append(
                        f"{p}: type={note_type!r} but file is in {t!r} directory"
                    )
                # SR-WB: validate results_* provenance when results_hash is filled
                # (empty = not yet pulled — not a violation)
                result_issues = check_result_provenance(p)
                violations.extend(result_issues)
                # SR-EXP-REPRO: warn when results_hash is set but repro_* are still sentinel
                # (surfaces manual gaps right after the run, not at paper-writing time)
                repro_warnings = check_repro_sentinel_lint(p)
                violations.extend(repro_warnings)
                # SR-PLAN-2: covers: link-validation for plan master notes
                # (plan_kind: preregistration); resolves each covers: child,
                # checks stance ∈ {confirmatory, exploratory} and plan_role ∈
                # {main, supporting_ablation, conditional_ablation} (§5K.7).
                if fields.get("plan_kind") == "preregistration":
                    covers_issues = check_covers_links(p, fields, subdir)
                    violations.extend(covers_issues)
                # SR-PLAN-2: child note checks — plan_role/stance presence +
                # supports_main target existence + absent-from-covers warning
                # (only for notes with plan_role set, §5K.7).
                child_issues = check_plan_child_links(p, fields, subdir, covered_ids)
                violations.extend(child_issues)
            elif t == "figures":
                # SR-FIG: project-scoped type-dir contract + provenance fields required.
                if note_type != "figures":
                    violations.append(
                        f"{p}: type={note_type!r} but file is in {t!r} directory"
                    )
                # figures notes must have source_experiment and experiment_results_hash filled in
                if not fields.get("source_experiment", "").strip():
                    violations.append(
                        f"{p}: figures note missing 'source_experiment' field "
                        f"(OKF link to the experiments note, e.g. 'experiments/run-007')"
                    )
                if not fields.get("experiment_results_hash", "").strip():
                    violations.append(
                        f"{p}: figures note missing 'experiment_results_hash' field "
                        f"(content hash from the experiment results in sha256:<hex> format)"
                    )
            elif t == "manuscript":
                # SR-MS-1a: project-scoped type-dir contract.
                # Provenance fields (manuscript_pdf + manuscript_hash) are OPTIONAL at creation
                # time (filled by rv manuscript compile) — not required to be non-empty.
                # OPTIONAL check: when manuscript_pdf is filled in, verify the PDF exists
                # and its sha256 matches manuscript_hash (the PDF-hash provenance branch).
                if note_type != "manuscript":
                    violations.append(
                        f"{p}: type={note_type!r} but file is in {t!r} directory"
                    )
                manuscript_issues = _check_manuscript_pdf_hash(p, fields)
                violations.extend(manuscript_issues)
            else:
                # Standard OKF type-dir contract for the other project-scoped types
                if note_type != t:
                    violations.append(
                        f"{p}: type={note_type!r} but file is in {t!r} directory"
                    )

    return violations


# ---------------------------------------------------------------------------
# SR-PLAN-2: covers:/stance link-validation helpers
# ---------------------------------------------------------------------------

def _parse_covers_list(covers_str: str) -> list[str]:
    """Parse a flat inline YAML list string like '[a, b, c]' into Python list.

    Mirrors plan/freeze.py's _parse_covers_list — kept private to note.py so
    plan/ stays note.py-free (§5K.10).  The two implementations are intentionally
    independent; the SSOT for the list-format contract is the OKF flat-frontmatter
    spec, not a shared function.
    """
    s = covers_str.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [item.strip() for item in s.split(",") if item.strip()]


def check_covers_links(
    plan_note_path: Path,
    fields: dict[str, str],
    notes_root: Path,
) -> list[str]:
    """SR-PLAN-2: validate that each covers: entry exists with valid stance + plan_role.

    Called by cmd_check for experiments notes with ``plan_kind: preregistration``.

    BLOCKs on:
    - A child note referenced in covers: that cannot be found at notes_root/<child_id>.md
    - A child note missing the ``stance`` field
    - A child note with an invalid ``stance`` value (not confirmatory|exploratory)
    - A child note missing the ``plan_role`` field
    - A child note with an invalid ``plan_role``
      (not main|supporting_ablation|conditional_ablation)

    Args:
        plan_note_path: path to the plan master note (for error messages).
        fields:         frontmatter dict of the plan master note.
        notes_root:     directory where child notes live (<child_id>.md files).

    Returns:
        list of violation strings.  Empty = all clear.
    """
    covers_str = fields.get("covers", "")
    if not covers_str:
        return []

    child_ids = _parse_covers_list(covers_str)
    violations: list[str] = []

    for child_id in child_ids:
        child_path = notes_root / f"{child_id}.md"
        if not child_path.exists():
            violations.append(
                f"{plan_note_path}: covers: child {child_id!r} not found "
                f"at {child_path} — create the experiment note or remove it "
                f"from covers: (§5K.7 link-validation)"
            )
            continue

        try:
            child_text = child_path.read_text(encoding="utf-8")
        except OSError as e:
            violations.append(
                f"{plan_note_path}: cannot read covers: child {child_id!r}: {e}"
            )
            continue

        child_fields, _ = _parse_frontmatter(child_text)
        stance = child_fields.get("stance", "").strip()
        plan_role = child_fields.get("plan_role", "").strip()

        if not stance:
            violations.append(
                f"{plan_note_path}: covers: child {child_id!r} is missing "
                f"'stance' field — add stance: confirmatory or exploratory "
                f"(§5K.1, §5K.7)"
            )
        elif stance not in _VALID_STANCE:
            violations.append(
                f"{plan_note_path}: covers: child {child_id!r} has "
                f"invalid stance={stance!r} "
                f"(expected: confirmatory or exploratory, §5K.1)"
            )

        if not plan_role:
            violations.append(
                f"{plan_note_path}: covers: child {child_id!r} is missing "
                f"'plan_role' field "
                f"(expected: main, supporting_ablation, or conditional_ablation, §5K.1)"
            )
        elif plan_role not in _VALID_PLAN_ROLE:
            violations.append(
                f"{plan_note_path}: covers: child {child_id!r} has "
                f"invalid plan_role={plan_role!r} "
                f"(expected: main, supporting_ablation, or conditional_ablation, §5K.1)"
            )

    return violations


def check_plan_child_links(
    exp_note_path: Path,
    fields: dict[str, str],
    notes_root: Path,
    covered_ids: "set[str]",
) -> list[str]:
    """SR-PLAN-2: validate plan_role / stance / supports_main on a child note.

    Checks performed only when ``plan_role`` is set (i.e., the note is a plan child):
    - ``stance`` must be present (confirmatory or exploratory)
    - If ``stance: confirmatory`` AND covered_ids is non-empty, the note's ID must
      appear in at least one plan master's ``covers:`` list
    - If ``supports_main`` is set, the target note must exist

    The absent-from-covers check is *skipped* when covered_ids is empty (no plan
    masters in this project) — degrade-to-skip, not degrade-to-block (§5K.7).

    Args:
        exp_note_path: path to the experiment note being checked.
        fields:        frontmatter dict of the experiment note.
        notes_root:    directory where experiment notes live (for supports_main
                       resolution).
        covered_ids:   set of child IDs mentioned in any plan master's covers: in
                       this project.  Empty when no plan masters exist.

    Returns:
        list of violation strings.  Empty = all clear.
    """
    violations: list[str] = []
    plan_role = fields.get("plan_role", "").strip()
    stance = fields.get("stance", "").strip()
    supports_main = fields.get("supports_main", "").strip()
    note_id = exp_note_path.stem

    if plan_role:
        if not stance:
            violations.append(
                f"{exp_note_path}: plan_role={plan_role!r} is set but "
                f"'stance' field is missing — "
                f"add stance: confirmatory or stance: exploratory (§5K.1)"
            )
        elif stance == "confirmatory" and covered_ids and note_id not in covered_ids:
            violations.append(
                f"{exp_note_path}: stance=confirmatory but {note_id!r} "
                f"is not in any plan master's covers: list — "
                f"add it to the plan master or check for a typo (§5K.7)"
            )

    if supports_main:
        target = notes_root / f"{supports_main}.md"
        if not target.exists():
            violations.append(
                f"{exp_note_path}: supports_main={supports_main!r} target "
                f"note not found at {target} (§5K.7)"
            )

    return violations


# ---------------------------------------------------------------------------
# SR-WB: experiment-results provenance validation
# ---------------------------------------------------------------------------

def _is_local_results_path(location: str) -> bool:
    """Return True if results_location looks like a local filesystem path (not URL)."""
    lower = location.lower()
    for prefix in ("http://", "https://", "ftp://", "s3://", "gs://", "doi:", "hdfs://"):
        if lower.startswith(prefix):
            return False
    return True


def check_result_provenance(exp_note_path: Path) -> list[str]:
    """Validate the results_* frontmatter fields in an experiment note.

    When to use: called by cmd_check (rv note check) and the DAG complete gate
    to validate that a filled results attachment is hash-consistent.

    Checks (only when results_hash is non-empty):
      1. results_location is non-empty
      2. For local file paths: the file exists AND sha256 matches results_hash

    Empty fields (un-pulled, unfilled) are skipped — not a violation.
    URL/remote results_location trusts the recorded hash (zero-infra, like dataset:).

    Returns a list of violation strings (empty = OK, gate passes).
    SR-WB. Reuses the streaming hash pattern from wait_for._verify_local_file_hash.
    """
    if not exp_note_path.exists():
        return [f"experiment note does not exist: {exp_note_path}"]

    try:
        text = exp_note_path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"cannot read experiment note {exp_note_path}: {e}"]

    fields, _ = _parse_frontmatter(text)
    results_hash = fields.get("results_hash", "").strip()
    results_location = fields.get("results_location", "").strip()

    # Empty hash → not yet filled, skip validation
    if not results_hash:
        return []

    # Hash is set → location must also be set
    if not results_location:
        return [
            f"{exp_note_path.name}: results_hash is set but results_location is empty"
        ]

    # For URL / DOI / remote: trust the recorded hash (zero-infra, no fetch)
    if not _is_local_results_path(results_location):
        return []

    # Local file: verify existence + hash
    artifact = Path(results_location)
    if not artifact.exists():
        return [
            f"{exp_note_path.name}: results artifact not found: {results_location}"
        ]

    if results_hash.startswith("sha256:"):
        expected_hex = results_hash[len("sha256:"):]
        try:
            h = hashlib.sha256()
            with open(artifact, "rb") as fh:
                while chunk := fh.read(1 << 20):  # streaming, 1 MiB chunks
                    h.update(chunk)
            actual_hex = h.hexdigest()
        except OSError as e:
            return [f"{exp_note_path.name}: cannot read results artifact: {e}"]

        if actual_hex != expected_hex:
            return [
                f"{exp_note_path.name}: results hash mismatch "
                f"(expected sha256:{expected_hex[:12]}…, "
                f"actual sha256:{actual_hex[:12]}…)"
            ]

    return []


# ---------------------------------------------------------------------------
# SR-EXP-REPRO: repro sentinel lint
# ---------------------------------------------------------------------------

def check_repro_sentinel_lint(exp_note_path: Path) -> list[str]:
    """Warn when results_hash is set but required repro_* fields are still the sentinel.

    When to use: called by cmd_check (rv note check) alongside check_result_provenance
    for experiments notes. Surfaces manual gaps RIGHT AFTER the run, not at paper-writing
    time — when the information is still fresh and accessible.

    Lint fires only when:
      - results_hash is non-empty AND not the sentinel (results exist)
      - At least one REPRO_LINT_REQUIRED field is still the sentinel

    Empty results_hash (experiment not yet run) → no lint (not a violation).

    Returns a list of warning strings prefixed with "[repro-lint] WARN:" (empty = clean).
    SR-EXP-REPRO. Anti-fabrication: the sentinel is an honest hole, not a guessed value.
    """
    if not exp_note_path.exists():
        return []

    try:
        text = exp_note_path.read_text(encoding="utf-8")
    except OSError:
        return []

    fields, _ = _parse_frontmatter(text)
    results_hash = fields.get("results_hash", "").strip()

    # No results yet → lint does not fire
    if not results_hash or results_hash == REPRO_SENTINEL:
        return []

    warnings: list[str] = []
    for field in REPRO_LINT_REQUIRED:
        val = fields.get(field, "").strip()
        # Only warn when the field is EXPLICITLY the sentinel — never on absent/empty fields.
        # "Absence of the whole block is not a violation (optional, like results_*)".
        # A visible sentinel is the honest hole left by cmd_new; it warns RIGHT AFTER the run.
        if val == REPRO_SENTINEL:
            warnings.append(
                f"[repro-lint] WARN: {exp_note_path.name}: "
                f"results_hash is set but {field!r} is still the sentinel "
                f"({REPRO_SENTINEL!r}) — fill or confirm not applicable"
            )

    return warnings


# ---------------------------------------------------------------------------
# SR-MS-1a: manuscript PDF-hash provenance check
# ---------------------------------------------------------------------------

def _check_manuscript_pdf_hash(note_path: Path, fields: dict[str, str]) -> list[str]:
    """Validate the manuscript PDF-hash provenance (optional branch).

    When to use: called by cmd_check for each manuscript note. Parallel to
    check_result_provenance for experiment notes — reuses the streaming hash pattern.

    Checks (ONLY when manuscript_pdf is non-empty):
      1. manuscript_hash is also non-empty
      2. The PDF file exists on disk
      3. sha256 of the PDF matches manuscript_hash

    Empty manuscript_pdf (not yet compiled) → SKIP, not a violation.
    URL / remote paths → trust the recorded hash (zero-infra).

    Returns a list of violation strings (empty = OK).
    SR-MS-1a.
    """
    pdf_path_str = fields.get("manuscript_pdf", "").strip()
    if not pdf_path_str:
        # Not yet compiled — skip (empty fields are NOT a violation)
        return []

    ms_hash = fields.get("manuscript_hash", "").strip()
    if not ms_hash:
        return [
            f"{note_path.name}: manuscript_pdf is set but manuscript_hash is empty "
            f"(run `rv manuscript compile` to fill the hash)"
        ]

    # URL / remote — trust the recorded hash
    lower = pdf_path_str.lower()
    for prefix in ("http://", "https://", "ftp://", "s3://", "gs://"):
        if lower.startswith(prefix):
            return []

    pdf = Path(pdf_path_str)
    if not pdf.exists():
        return [
            f"{note_path.name}: manuscript_pdf not found: {pdf_path_str}"
        ]

    if ms_hash.startswith("sha256:"):
        expected_hex = ms_hash[len("sha256:"):]
        try:
            h = hashlib.sha256()
            with open(pdf, "rb") as fh:
                while chunk := fh.read(1 << 20):
                    h.update(chunk)
            actual_hex = h.hexdigest()
        except OSError as e:
            return [f"{note_path.name}: cannot read manuscript PDF: {e}"]

        if actual_hex != expected_hex:
            return [
                f"{note_path.name}: manuscript_hash mismatch "
                f"(expected sha256:{expected_hex[:12]}…, "
                f"actual sha256:{actual_hex[:12]}…)"
            ]

    return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(parent: argparse._SubParsersAction | None = None) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Build the argument parser for the `note` verb.

    When to use: use `rv note <project> <subcommand>` to create or inspect OKF notes.
    Notes are typed markdown files (literature, concepts, methods, experiments, findings,
    mocs, datasets, figures) stored under the project's notes directory. The type field in
    frontmatter is enforced. datasets notes are SR-8 provenance metadata — they POINT to
    data artifacts (path/URL/DOI + content-hash), never contain the data itself.
    figures notes are SR-FIG provenance metadata — they POINT to image files, never embed them.
    For figures, prefer `rv figure new` (richer arguments — experiment link + filter recipe).
    Anti-pattern: do NOT hand-copy a data path into a finding — file a datasets/
    provenance note and afterok on it so lineage is structural.
    """
    desc = "Create and list OKF notes for a project."
    if parent is not None:
        p = parent.add_parser("note", help="OKF note management.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv note", description=desc)

    p.add_argument("project", help="Project slug.")

    sub = p.add_subparsers(dest="note_cmd", required=True)

    # new
    new_p = sub.add_parser("new", help="Create a new OKF note.")
    new_p.add_argument("type", choices=sorted(OKF_TYPES), help="OKF note type.")
    new_p.add_argument("title", help="Note title.")
    new_p.add_argument("--id", dest="note_id", default=None,
                       help="Override the auto-generated slug.")
    new_p.add_argument("--tags", nargs="*", default=None,
                       help="Optional tags.")

    # list
    list_p = sub.add_parser("list", help="List OKF notes for a project.")
    list_p.add_argument("--type", dest="note_type", default=None,
                        choices=sorted(OKF_TYPES), help="Filter by OKF type.")

    # check
    sub.add_parser("check", help="Validate OKF note frontmatter.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch note subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv note: config error: {e}", file=sys.stderr)
        return 1

    try:
        if args.note_cmd == "new":
            path = cmd_new(
                args.project, args.type, args.title,
                config=cfg,
                note_id=args.note_id,
                tags=args.tags,
            )
            print(f"Created: {path}")
            return 0

        elif args.note_cmd == "list":
            notes = cmd_list(args.project, args.note_type, config=cfg)
            if not notes:
                msg = f"No notes for {args.project!r}"
                if args.note_type:
                    msg += f" (type={args.note_type!r})"
                print(msg + ".")
                return 0
            print(f"Notes for {args.project!r}:")
            for note in notes:
                t = note["fields"].get("type", "?")
                title = note["fields"].get("title", note["path"].stem)
                print(f"  [{t:<12}] {note['path'].stem}: {title}")
            return 0

        elif args.note_cmd == "check":
            violations = cmd_check(args.project, config=cfg)
            if not violations:
                print(f"rv note check: OK — {args.project!r}")
                return 0
            # Separate hard violations from repro-lint warnings (§5J.14).
            # Warnings (prefixed "[repro-lint] WARN:") are shown but do not flip exit code.
            hard = [v for v in violations if not v.startswith("[repro-lint]")]
            warnings = [v for v in violations if v.startswith("[repro-lint]")]
            for v in hard:
                print(f"  VIOLATION: {v}")
            for w in warnings:
                print(f"  {w}")
            return 1 if hard else 0

    except (ValueError, KeyError) as e:
        print(f"rv note: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv note: unexpected error: {e}", file=sys.stderr)
        return 1

    return 0
