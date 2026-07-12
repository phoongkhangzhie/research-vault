# SPDX-License-Identifier: AGPL-3.0-or-later
"""note.py — OKF note creation and listing for a project.

When to use: use `rv note <project> <type> …` to create or list OKF notes for a project.
Notes follow the Open Knowledge Format: markdown + YAML frontmatter with a required `type` field.
The type determines the subdirectory: literature/, concepts/, methods/, experiments/,
findings/, mocs/, datasets/.

Path resolution: always via Config — zero hardcoded paths.
Stdlib only.
"""

import argparse
import dataclasses
import datetime
import re
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .hashing import hash_file as _hash_file  # canonical hasher — never duplicate

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
    "datasets",    # provenance note for data artifacts (points to data, never contains it)
    "gaps",        # typed research gap record; project-scoped; first-class lifecycle
})

# The sole SHARED (cross-project) OKF type — lives in cfg.datasets_root.
# All other OKF types are PROJECT-SCOPED (cfg.project_notes_dir / type_dir)
# UNLESS they are two-layer (see OKF_TWO_LAYER_TYPES below).
# SSOT for the project-scoped-vs-shared split.
# Consumed by: wait_for (note: resolver), dag/verbs (_check_project_scoped_note).
# Do NOT duplicate this — import from here.
OKF_SHARED_TYPES: frozenset[str] = frozenset({"datasets"})

# The TWO-LAYER (cross-project core + per-project overlay) OKF type(s).
# `datasets` is purely shared (one file, no per-project part); `literature`
# is two-layer: an intrinsic core (paper facts + inter-paper edges, distilled
# once) lives at cfg.literature_root/<citekey>.md, and a thin per-project
# overlay (role/position/concept-edges + a `central:` pointer) lives at
# cfg.project_notes_dir(project)/literature/<citekey>.md — same location a
# literature note has always lived at, now carrying only the RQ-relative
# layer. See (2026-07-10-central-note-store-cross-project-design.md
#). A third routing class — NOT membership in
# OKF_SHARED_TYPES, which would collapse the per-project overlay away.
# SSOT: consumed by cmd_new/cmd_list/cmd_check's three-arm routing + the
# note.load_literature_note resolver. Do NOT duplicate this — import from
# here.
OKF_TWO_LAYER_TYPES: frozenset[str] = frozenset({"literature"})

# Every OKF type that is plain PROJECT-SCOPED — i.e. neither shared nor
# two-layer. Derived (not hand-maintained) so it can never drift out of
# sync with OKF_TYPES / OKF_SHARED_TYPES / OKF_TWO_LAYER_TYPES as new types
# are added. The three classes are pairwise-disjoint and union to
# OKF_TYPES (see tests/test_note.py's SSOT class-partition test).
OKF_PROJECT_TYPES: frozenset[str] = OKF_TYPES - OKF_SHARED_TYPES - OKF_TWO_LAYER_TYPES

# Valid values for stance + plan_role on child experiment notes.
_VALID_STANCE: frozenset[str] = frozenset({"confirmatory", "exploratory"})
_VALID_PLAN_ROLE: frozenset[str] = frozenset({
    "main", "supporting_ablation", "conditional_ablation"
})

# ---------------------------------------------------------------------------
# Experiment reproducibility schema
# ---------------------------------------------------------------------------

# Sentinel value for all repro_* fields that are not (yet) populated.
# Anti-fabrication contract: NEVER write blank/guessed — write this visible hole.
# Doctrine: "OKF frontmatter is flat ^(\w+): — to attach structured/nested data,
# use a hashed artifact + promoted flat scalars, never inline JSON in frontmatter."
REPRO_SENTINEL = "not-recorded-in-provenance"

# Explicit not-applicable value for repro_* fields on PROXY/no-run analyses.
# Distinct from REPRO_SENTINEL:
#   REPRO_SENTINEL  = "I had a model run but this field was not recorded" (WARN: fill it!)
#   REPRO_NOT_APPLICABLE = "No model run took place; field is genuinely N/A" (SKIP lint)
# Use case: a proxy analysis that aggregates published results sets results_hash on the
# aggregated CSV, but repro_model_id/repro_seed/etc. are not applicable (no run occurred).
REPRO_NOT_APPLICABLE = "not-applicable"

# Full ordered list of all repro_* fields (22 fields).
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
# Layer 2 — AUTO from the compute manifest (deferred — do NOT re-probe):
REPRO_AUTO_HW = ["repro_hw"]
# Layer 2 — AUTO from linked dataset note (links note + inherits its hash):
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
# (D-CC-2 / CHECK-4b): tolerance taxonomy — declares the comparison a
# golden-rerun test should apply against this experiment's recorded
# scores[].hash, so an exact-hash gate never fails-forever on a legitimately
# nondeterministic (GPU/stochastic) pipeline. Values: exact | tol:<eps> |
# stochastic. Scaffolded default is the strict "exact" (design R3), NOT the
# REPRO_SENTINEL — a default is a complete, safe declaration here, not a
# fabrication-risk hole, so this field is deliberately kept OUT of
# REPRO_LINT_REQUIRED (it never contributes sentinel-lint noise). The static
# gate does not validate the value; a registered golden-rerun test (deferred,
# 0.2.0 soft) is the consumer. See doctrine/code-conventions.md §5.
REPRO_TOLERANCE = ["repro_determinism"]

# All repro_* fields in canonical order (22 provenance-chain fields +
# repro_determinism, the tolerance-taxonomy field added):
REPRO_ALL_FIELDS: list[str] = (
    REPRO_LAYER1
    + REPRO_AUTO_CONFIG
    + REPRO_AUTO_META
    + REPRO_AUTO_HW
    + REPRO_AUTO_DATASET
    + REPRO_AUTO_HARNESS
    + REPRO_MANUAL
    + REPRO_TOLERANCE
)

# Fields required for the lint (warn when results_hash is set but these are still sentinel):
# All non-dataset fields (dataset linking is optional; hw deferral is acceptable).
# repro_determinism is deliberately EXCLUDED (CHECK-4b): it scaffolds
# to a complete default ("exact"), not the sentinel, so it is never a
# completeness gap the lint should flag.
# (R1): repro_seed is PROMOTED out of this soft WARN list — a seedless
# claimed result is not reproducible, so it is now enforced HARD inside
# check_provenance_chain (CHECK-1/CHECK-4a). The other repro_* fields
# (including the Layer-1 config pair, folded again into CHECK-1 as CHECK-2)
# stay WARN here — completeness-nudges, not chain-critical.
REPRO_LINT_REQUIRED: list[str] = (
    REPRO_LAYER1
    + [f for f in REPRO_AUTO_CONFIG if f != "repro_seed"]
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


def _parse_frontmatter(
    text: str,
) -> "tuple[dict[str, str | list[str] | list[dict[str, str]]], str]":
    """Parse YAML-like frontmatter between --- delimiters.

    Handles scalar fields, YAML indented scalar-list fields, and (D8)
    YAML indented mapping-list fields:
    - Scalar:  ``key: value`` → ``{"key": "value"}``
    - List:    ``key:\\n  - a\\n  - b`` → ``{"key": ["a", "b"]}``
    - Mapping-list (D8): ``key:\\n  - k1: v1\\n    k2: v2\\n  - k1: v3`` →
      ``{"key": [{"k1": "v1", "k2": "v2"}, {"k1": "v3"}]}``. A ``  - `` item
      whose remainder matches ``key: value`` opens a new dict entry; a
      following 4-space-indented (non-``  - ``) ``key: value`` line is folded
      into that same dict as a continuation. A ``  - `` item with **no**
      ``key:`` shape stays a plain scalar (unchanged) — this is the
      backward-compat guard: existing scalar-list callers (``backed_by``,
      ``supported_by``, ``contradicted_by``, the new ``runs:``) have no
      ``key:``-shaped items, so they are wholly untouched by this extension.
    - Inline ``key: []`` syntax stays as the literal string ``"[]"`` (not a list).

    #26 convergence: this canonical parser now replaces the local
    ``gap_scan._parse_frontmatter_gap`` duplicate (STOP decision lifted).
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
    fields: "dict[str, Any]" = {}
    current_list_key: "str | None" = None
    # D8: the dict currently being built by a mapping-list item, so a following
    # 4-space continuation line ("    key: value", no "  - " prefix) can be
    # folded into it. None when the last list item was a plain scalar (or no
    # list is open) — scalar-list callers never populate this, so they never
    # take the continuation branch below.
    current_mapping_item: "dict[str, str] | None" = None
    for line in fm_block.splitlines():
        # D8 continuation: a 4-space-indented "key: value" line immediately
        # following an open mapping-list item folds into that dict.
        if (
            current_list_key is not None
            and current_mapping_item is not None
            and line.startswith("    ")
            and not line.startswith("  - ")
        ):
            cont_m = re.match(r"^(\w[\w_-]*):\s*(.*)$", line.strip())
            if cont_m:
                ck, cv = cont_m.group(1), cont_m.group(2).strip()
                if cv.startswith(("'", '"')) and cv.endswith(cv[0]):
                    cv = cv[1:-1]
                current_mapping_item[ck] = cv
                continue
            # Unrecognized continuation shape — fall through to normal handling.

        # YAML indented list item: "  - item" (two-space indent + dash + space)
        if line.startswith("  - ") and current_list_key is not None:
            # Lazy-promote: first list item converts "" → [] before appending.
            # This preserves the old behaviour for empty keys that have NO list items
            # (they stay as "") — only keys WITH  - item lines become list[str].
            existing = fields[current_list_key]
            if isinstance(existing, str):
                fields[current_list_key] = []
            cast_list = fields[current_list_key]
            remainder = line[4:].strip()
            # D8: does the remainder itself look like "key: value"? If so, this
            # item opens a new mapping-list dict entry (not a plain scalar).
            # Fix 3 (PR followup): require WHITESPACE after the colon (or
            # end-of-string for an empty value), matching YAML flow-map
            # semantics ("key: value", not "key:value") — otherwise a
            # URL/DOI-shaped scalar-list item containing a bare colon (e.g.
            # "http://x.com/run:5", a DOI "10.1234/x:5") is mis-parsed as a
            # mapping key. `\s*` (zero-or-more) wrongly matched those; `\s+`
            # (one-or-more) or end-of-string does not.
            item_m = re.match(r"^(\w[\w_-]*):(?:\s+(.*))?$", remainder)
            if item_m:
                ik, iv = item_m.group(1), (item_m.group(2) or "").strip()
                if iv.startswith(("'", '"')) and iv.endswith(iv[0]):
                    iv = iv[1:-1]
                new_item: "dict[str, str]" = {ik: iv}
                if isinstance(cast_list, list):
                    cast_list.append(new_item)
                current_mapping_item = new_item
            else:
                if remainder.startswith(("'", '"')) and len(remainder) >= 2 and remainder.endswith(remainder[0]):
                    remainder = remainder[1:-1]
                if isinstance(cast_list, list):
                    cast_list.append(remainder)
                current_mapping_item = None
            continue
        current_list_key = None
        current_mapping_item = None
        m = re.match(r"^(\w[\w_-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val == "":
                # Empty value after colon → tentatively empty string; may become
                # list[str] (or list[dict[str, str]]) if  - item lines follow
                # (lazy-promote on first item).
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

    For note_type == 'datasets', the template includes placeholder fields:
      location — path/URL/DOI of the actual data artifact (fill this in)
      hash     — content hash in sha256:<hex> format (fill this in)
    Anti-pattern: do NOT hand-copy a data path into a finding — file a datasets/
    provenance note and afterok on it, so lineage is structural.

    """
    if note_type not in OKF_TYPES:
        raise ValueError(
            f"Unknown note type {note_type!r}. Valid types: {sorted(OKF_TYPES)}"
        )
    cfg = config or load_config()

    # Two-layer types (OKF_TWO_LAYER_TYPES — literature) split into a
    # cross-project CENTRAL core (intrinsic paper facts, distilled once —
    # cfg.literature_root) + a thin per-project OVERLAY (role/position/
    # concept-edges + a `central:` pointer — cfg.project_notes_dir/<type>).
    # See. Handled as its own branch (below) — never falls
    # through to the plain project-scoped/shared branches.
    #
    # Shared types (OKF_SHARED_TYPES) live in cfg.datasets_root, not in
    # the project-scoped notes directory. A shared-type note filed for one project
    # is visible and lineage-gatable from any other project.
    # Use OKF_SHARED_TYPES SSOT — not a hardcoded "datasets"
    # string — so a 2nd shared type automatically routes correctly here.
    if note_type in OKF_TWO_LAYER_TYPES:
        return _cmd_new_two_layer(cfg, project, note_type, title, note_id=note_id, tags=tags)

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

    # datasets notes carry provenance-specific placeholder fields
    if note_type == "datasets":
        fields["location"] = ""   # fill in: path/URL/DOI of the data artifact
        fields["hash"] = ""       # fill in: sha256:<hex> content hash of the artifact

    # experiments notes carry the generalized results
    # attachment — EMPTY runs:/scores: lists (zero items, not blank placeholder
    # entries: an entry with blank fields would falsely trip the per-entry
    # "location empty" check in check_result_provenance). Empty = not-yet-run
    # → the gate skips, same semantics as the old flat results_hash: "".
    # The deprecated flat results_location/results_hash/results_wandb_run
    # fields are NO LONGER scaffolded (still read via the _normalize_results
    # shim for legacy notes) — new notes use the lists exclusively.
    if note_type == "experiments":
        fields["runs"] = ""       # the executions (any N) — scalar list of run refs
        fields["scores"] = ""     # the computed outputs (any M) — list of {location, hash, label}
        fields["results_commit"] = ""     # git SHA of the code that produced the run
        # Reproducibility schema — flat repro_* fields.
        # Sentinel = "not-recorded-in-provenance" (NEVER blank, NEVER guessed).
        # Layer 1 (auto via rv wandb pull): hashed full-config artifact.
        # Layer 2 (auto via rv wandb pull alias table): promoted flat scalars.
        # MANUAL fields: cross-lingual trio + eval params — fill by hand; sentinel = honest hole.
        for repro_field in REPRO_ALL_FIELDS:
            fields[repro_field] = REPRO_SENTINEL
        # (D-CC-2 / R3): repro_determinism scaffolds to the strict safe
        # default "exact", NOT the sentinel — a stochastic/GPU pipeline must
        # explicitly relax it. Overridden after the loop (not a REPRO_SENTINEL
        # hole, so it must not be forced to fill like the completeness fields).
        fields["repro_determinism"] = "exact"

    if tags:
        fields["tags"] = "[" + ", ".join(tags) + "]"

    if note_type == "datasets":
        body = (
            "\n"
            "<!-- Datasets provenance note -->\n"
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
            "<!-- Experiments provenance note (results + reproducibility schema) -->\n"
            "<!-- Run `rv wandb pull <run-id> --experiment <id> --project <slug>` to fill -->\n"
            "<!-- runs:/scores:/results_commit, plus all auto repro_* fields -->\n"
            "<!-- (Layer 1 + Layer 2 alias map) — or fill by hand for CSV/manual fallback. -->\n"
            "<!-- -->\n"
            "<!-- The results attachment models ALL N-runs -> M-scores cardinalities -->\n"
            "<!-- (1->1, N->1, 1->M, N->M) via two lists: -->\n"
            "<!--   runs:   the executions (any N) — scalar list, no hash (evidence trail): -->\n"
            "<!--     runs:\\n  - myteam/myproject/run-01\\n  - myteam/myproject/run-02 -->\n"
            "<!--   scores: the computed outputs (any M) — EACH independently hash-anchored: -->\n"
            "<!--     scores:\\n  - location: results/scores/hfs-landscape.csv\\n -->\n"
            "<!--       hash: sha256:<hex>\\n    label: hfs-landscape  (optional) -->\n"
            "<!-- -->\n"
            "<!-- Legacy mapping (migrating an existing note by hand): -->\n"
            "<!--   old flat  wandb: <list of run ids>        -> runs: -->\n"
            "<!--   old flat  runs: <jsonl globs>              -> results/runs/ bytes, -->\n"
            "<!--                                                  referenced from the body -->\n"
            "<!--                                                  (not frontmatter-anchored) -->\n"
            "<!--   multiple score CSVs                        -> scores: list, one -->\n"
            "<!--                                                  hash-anchored entry each -->\n"
            "<!-- -->\n"
            "<!-- MANUAL repro_* fields: repro_prompt_lang (BCP-47), -->\n"
            "<!--   repro_translation_provenance (human / MT:<engine@ver>), -->\n"
            "<!--   repro_prompt_version, repro_dataset_split, repro_metric. -->\n"
            "<!-- Anti-fabrication: use 'not-recorded-in-provenance' not blank/guessed. -->\n"
            "<!-- -->\n"
            "<!-- repro_determinism (D-CC-2): the tolerance taxonomy a -->\n"
            "<!--   golden-rerun test uses to pick its comparison. Values: -->\n"
            "<!--     exact       — bit-for-bit reproducible (default; strictest) -->\n"
            "<!--     tol:<eps>   — reproducible within a numeric epsilon, e.g. tol:1e-6 -->\n"
            "<!--     stochastic  — inherently nondeterministic (GPU nondeterminism, -->\n"
            "<!--                   sampling); a rerun compares distributional, not exact -->\n"
            "<!--   Relax away from 'exact' only when the pipeline genuinely cannot -->\n"
            "<!--   reproduce bit-for-bit — see doctrine/code-conventions.md #5. -->\n"
            "\n"
            "## Hypothesis\n\n"
            "<!-- What were you testing? -->\n\n"
            "## Setup\n\n"
            "<!-- Model, dataset, hyperparameters, cluster config. -->\n\n"
            "## Analysis\n\n"
            "<!-- What do the results mean? -->\n"
        )
    else:
        body = "\n<!-- Write your note here -->\n"

    note_path.write_text(_render_frontmatter(fields) + "\n" + body, encoding="utf-8")
    return note_path


# ---------------------------------------------------------------------------
# Two-layer literature store — write path
# ---------------------------------------------------------------------------

def _literature_core_body() -> str:
    """Body template for a freshly-distilled central core note."""
    return (
        "\n"
        "<!-- Literature note — CENTRAL CORE (intrinsic paper facts). -->\n"
        "<!-- Two-layer store: this file is shared across every project that -->\n"
        "<!-- reads this paper — do NOT put role:/position:/concept-edges here; -->\n"
        "<!-- those belong in the per-project overlay (see the sibling note at -->\n"
        "<!-- project_notes_dir/literature/<citekey>.md carrying central: <citekey>). -->\n"
        "<!-- key_equations: is a criticality ledger keyed by label -- fill by hand as: -->\n"
        "<!--   key_equations:\\n  - label: eq:elbo\\n    critical: true -->\n"
        "<!-- repo: the paper's code repo URL (leave empty if none published) -->\n"
        "<!-- artifacts: scalar list of \"label: url\" pointers (dataset, project page, checkpoint) -->\n"
        "\n"
        "## Result\n\n"
        "<!-- Filled when result_reported: yes (Move 3). -->\n\n"
        "## Key equations\n\n"
        "<!-- One labeled block per pivotal equation this paper's argument turns on. -->\n"
        "<!-- ### [eq:elbo] Evidence lower bound  *(critical)* -->\n"
        "<!-- $$ \\log p(x) \\ge \\mathbb{E}_{q}[\\log p(x,z) - \\log q(z)] $$ -->\n"
        "<!-- Leave this section empty for papers with no pivotal equations. -->\n\n"
        "## Related papers\n\n"
        "<!-- Typed paper->paper edges (Move 4), e.g.: -->\n"
        "<!-- - [Baltaji 2024](/literature/baltaji2024.md) — SUPPORTS: reason -->\n"
    )


def _literature_overlay_body() -> str:
    """Body template for a freshly-created per-project overlay note."""
    return (
        "\n"
        "<!-- Literature note — PER-PROJECT OVERLAY (RQ-relative layer only). -->\n"
        "<!-- Two-layer store: intrinsic paper facts live in the central core -->\n"
        "<!-- (central: pointer above) — do NOT put doi:/contribution_kind:/ -->\n"
        "<!-- result_reported:/## Result/## Related papers here. -->\n"
        "<!-- role: one of methodological/empirical/theoretical/counter-position -->\n"
        "<!-- position: free-form narrative — how this paper relates to THIS RQ -->\n"
        "\n"
        "## Concept edges\n\n"
        "<!-- Paper->concept typed edges, THIS project's concepts/ only, e.g.: -->\n"
        "<!-- - [my-concept](/concepts/my-concept.md) — SUPPORTS: reason -->\n"
    )


def _cmd_new_two_layer(
    cfg: Config, project: str, note_type: str, title: str, *,
    note_id: str | None = None,
    tags: list[str] | None = None,
) -> Path:
    """Create a two-layer OKF note (currently just ``literature``):
    a cross-project CENTRAL CORE (intrinsic paper facts, distilled once)
    plus a thin per-project OVERLAY (role/position/concept-edges + a
    ``central:`` pointer). Returns the OVERLAY path — the project-facing
    artifact, same shape callers have always gotten back (``path.parent.name
    == note_type``; see test_note.py's ``test_new_all_types_accepted``).

    Slug/identity note: the central-core FILENAME uses the same
    note_id-or-slugify(title) convention every other OKF type uses — NOT a
    re-derived identity precedence (item 4: use the SHIPPED
    ``authorYearWord`` convention / ``cite.CITEKEY_RE``, don't invent a
    new one). The canonical ``citekey:`` frontmatter field is stamped
    later by ``rv research citekey`` once title/authors/year are known —
    identical to the pre- monolithic-note behavior.

    If a core already exists for this slug (another project already
    distilled this paper), it is NOT overwritten or duplicated — this
    project's overlay is simply wired to point at the existing core
    ("distilled but not adopted" — now "distilled AND adopted").
    Cross-id-collision detection (same slug, DIFFERENT paper) is the
    identity-resolution work deferred to the amortization fast-follow
    (D6) — out of scope for this write path.
    """
    core_dir = cfg.literature_root
    core_dir.mkdir(parents=True, exist_ok=True)
    overlay_dir = cfg.project_notes_dir(project) / note_type
    overlay_dir.mkdir(parents=True, exist_ok=True)

    slug = note_id or _slugify(title)
    overlay_path = overlay_dir / f"{slug}.md"
    if overlay_path.exists():
        # This project already has a note at this slug — bump like any
        # other OKF type. The core slug follows the (possibly-bumped)
        # overlay slug so the pair always shares one identity.
        slug = f"{slug}-{_today()}"
        overlay_path = overlay_dir / f"{slug}.md"
    core_path = core_dir / f"{slug}.md"

    if not core_path.exists():
        core_fields: dict[str, str] = {
            "type": note_type,
            "title": title,
            "created": _today(),
        }
        # Fix #32: literature notes carry optional doi/arxiv_id placeholders so the
        # notes-based corpus-dedup index (_load_notes_index in research.py) can match
        # an S2 candidate to a filed note without requiring Zotero library.json sync.
        # Fill these in after rv note new to enable [IN-CORPUS] annotation for the note.
        #
        # (§K-2): the canonical BibTeX citekey (K-D1: authorYearWord —
        # familyShorttitleYear, see cite.CITEKEY_RE). The FILENAME may stay
        # an arbitrary id (arXiv id, S2 id, slug — whatever this note was
        # filed under); this field is now the ONE convention downstream
        # readers cite by. Left blank here (title/authors/year aren't known
        # yet at `rv note new` time) — computed + stamped by
        # `rv research citekey <project> <note-id>` once those fields are
        # filled in (research.compute_and_stamp_citekey). An unresolvable
        # note gets the visible `cite.CITEKEY_SENTINEL`, never a guess.
        core_fields["citekey"] = ""    # fill by hand, or `rv research citekey <project> <id>`
        core_fields["doi"] = ""        # fill in: DOI of the paper (e.g. 10.1234/example)
        core_fields["arxiv_id"] = ""   # fill in: ArXiv id (e.g. 2005.14165, NOT arXiv:...)
        # identifier-persistence: the fuller external-id set (sources/identifiers.py) —
        # stamped automatically by `rv research add` when this note is already filed;
        # fill by hand otherwise. Absence is never a cmd_check violation (same
        # optional-field precedent as doi/arxiv_id above).
        core_fields["pmcid"] = ""      # fill in: PMC id (enables the pmc OA-fulltext provider)
        core_fields["openalex"] = ""   # fill in: OpenAlex work id
        core_fields["pmid"] = ""       # fill in: PubMed id
        core_fields["s2"] = ""         # fill in: Semantic Scholar corpus id
        # the lit-review ingestion enrichment — three OPTIONAL
        # fields, populated by the relate-<key> node (review/style.py
        # per_paper_relate_tips) or filled by hand. Absence is never a
        # cmd_check violation (doi/arxiv_id precedent — no gate added).
        core_fields["key_equations"] = ""  # fill in: D8 mapping-list criticality ledger, e.g.
                                            #   key_equations:
                                            #     - label: eq:elbo
                                            #       critical: true
        core_fields["repo"] = ""           # fill in: the paper's code repo URL (empty if none)
        core_fields["artifacts"] = ""      # fill in: scalar list of "label: url" pointers
        core_path.write_text(
            _render_frontmatter(core_fields) + "\n" + _literature_core_body(),
            encoding="utf-8",
        )

    overlay_fields: dict[str, str] = {
        "type": note_type,
        "title": title,
        "created": _today(),
        # The pointer — resolved to core_dir/<central>.md by the resolver
        # (literature_core_path). A dangling value here fails closed
        # (load_literature_note raises DanglingCentralPointerError).
        "central": slug,
    }
    if tags:
        overlay_fields["tags"] = "[" + ", ".join(tags) + "]"

    overlay_path.write_text(
        _render_frontmatter(overlay_fields) + "\n" + _literature_overlay_body(),
        encoding="utf-8",
    )
    return overlay_path


# ---------------------------------------------------------------------------
# Two-layer literature store — the canonical resolver (SINGLE read seam)
# ---------------------------------------------------------------------------
#
# Every literature reader routes through here (item 6). Do NOT
# glob literature/*.md and parse frontmatter directly for intrinsic
# (core-only) fields — that path re-introduces representation-coupling
# the resolver exists to prevent.

class DanglingCentralPointerError(ValueError):
    """Raised when an overlay's ``central:`` pointer does not resolve to an
    existing central-core note. A surfaced violation (charter §2) — never
    a silent empty/partial note."""


@dataclasses.dataclass
class AssembledNote:
    """The merged read-view of a two-layer literature note: central core
    (intrinsic wins) + per-project overlay (role/position/concept-edges).

    ``fields`` merges overlay first, core second (core wins on any key
    collision — there should be none by construction, since the two
    layers own disjoint field sets; see ``check_two_layer_invariants``).
    ``body`` concatenates core body + overlay body so heading-based
    parsers (``## Result``/``## Related papers`` in core, ``## Concept
    edges`` in overlay) keep working unchanged against the assembled text.
    """

    citekey: str
    fields: dict[str, str]
    body: str
    core_path: Path
    overlay_path: Path


def literature_core_path(cfg: Config, citekey: str) -> Path:
    """The central-core note path for *citekey* — cfg.literature_root/<citekey>.md."""
    return cfg.literature_root / f"{citekey}.md"


def literature_overlay_path(cfg: Config, project: str, citekey: str) -> Path:
    """The per-project overlay note path for *citekey* — same location a
    literature note has always lived at (project_notes_dir/literature/)."""
    return cfg.project_notes_dir(project) / "literature" / f"{citekey}.md"


def load_literature_note(cfg: Config, project: str, citekey: str) -> AssembledNote:
    """Load + merge a two-layer literature note for *project*.

    Fail-closed:
      - overlay absent for this project -> ``FileNotFoundError`` (the
        paper may be central-distilled by ANOTHER project — "distilled
        but not adopted by X" — but THIS project never adopted it; that
        is a distinct, non-violation state you get by NOT calling this
        for a project that hasn't adopted the paper).
      - overlay present but ``central:`` pointer absent/dangling ->
        ``DanglingCentralPointerError`` (a real integrity violation,
        surfaced loudly — never a silent empty note).
    """
    overlay_path = literature_overlay_path(cfg, project, citekey)
    if not overlay_path.exists():
        raise FileNotFoundError(
            f"load_literature_note: no overlay for citekey {citekey!r} in "
            f"project {project!r} at {overlay_path} — this project has not "
            f"adopted this paper (it may still be central-distilled by "
            f"another project; that is a valid, separate state)."
        )
    overlay_text = overlay_path.read_text(encoding="utf-8")
    overlay_fields, overlay_body = _parse_frontmatter(overlay_text)

    central = str(overlay_fields.get("central") or "").strip()
    if not central:
        raise DanglingCentralPointerError(
            f"load_literature_note: overlay {overlay_path} carries no "
            f"'central:' pointer — every literature overlay must point at "
            f"its central core."
        )
    core_path = literature_core_path(cfg, central)
    if not core_path.exists():
        raise DanglingCentralPointerError(
            f"load_literature_note: overlay {overlay_path} points to "
            f"central: {central!r} but no central core exists at "
            f"{core_path} — dangling pointer."
        )
    core_text = core_path.read_text(encoding="utf-8")
    core_fields, core_body = _parse_frontmatter(core_text)

    # Intrinsic (core) wins on any key collision — overlay first, core
    # second. By construction the two layers own disjoint field sets
    # (check_two_layer_invariants gates this); this merge order is a
    # safety net, not the primary correctness mechanism.
    merged_fields: dict[str, str] = {**overlay_fields, **core_fields}
    merged_body = core_body + "\n" + overlay_body

    return AssembledNote(
        citekey=central,
        fields=merged_fields,
        body=merged_body,
        core_path=core_path,
        overlay_path=overlay_path,
    )


def iter_literature_notes(cfg: Config, project: str):
    """Yield an ``AssembledNote`` for every overlay this project has
    adopted (globs the overlay dir — the per-project registry).
    A core with no overlay in this project is correctly invisible here —
    "distilled but not adopted", not an error.
    """
    overlay_dir = cfg.project_notes_dir(project) / "literature"
    if not overlay_dir.exists():
        return
    for overlay_file in sorted(overlay_dir.glob("*.md")):
        yield load_literature_note(cfg, project, overlay_file.stem)


# ---------------------------------------------------------------------------
# Two-layer invariant lint (item 8 — GATING, not a nicety)
# ---------------------------------------------------------------------------
#
# Frontmatter misplacement is a hard BLOCK (unambiguous, and never touched
# by the deferred incremental_relate edge-write mechanism — see below).
# Body-section misplacement of '## Related papers' into the overlay is a
# WARN: incremental_relate.append_bidirectional_edge still physically
# writes paper->paper edges to the project's literature/<key>.md file
# (the overlay's location) by explicit scope-fence deferral (
# "rewiring edge-writes to target the central core is fast-follow, not
# this PR") — so this is a KNOWN, DOCUMENTED, currently-expected surface
# until that fast-follow lands, not a bug this lint should hard-fail on.
# '## Concept edges' appearing in the core IS a hard BLOCK: nothing in the
# shipped write path ever puts it there, so its presence is a genuine
# misauthoring, not a deferred-mechanism artifact.

_CORE_ONLY_FIELDS: frozenset[str] = frozenset({
    "citekey", "doi", "arxiv_id", "pmcid", "openalex", "pmid", "s2",
    "key_equations", "repo", "artifacts",
    "contribution_kind", "result_reported",
    "distilled", "read_basis",
})

_OVERLAY_ONLY_FIELDS: frozenset[str] = frozenset({
    "central", "in_corpus_of", "role", "position",
})

_CORE_ONLY_BODY_HEADINGS_RE = re.compile(
    r"^#{2,3}\s+(Result|Key equations|Related papers)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_OVERLAY_ONLY_BODY_HEADING_RE = re.compile(
    r"^#{2,3}\s+Concept edges\s*$", re.IGNORECASE | re.MULTILINE,
)


def check_two_layer_invariants(core_path: Path, overlay_path: Path) -> list[str]:
    """The invariant lint (acceptance item 3): no intrinsic field
    authored in an overlay, no position/role/concept-edge in a core.

    Returns a list of violation strings. Hard violations (frontmatter
    misplacement, '## Concept edges' in a core) carry no ``WARN`` marker
    and are meant to flip a caller's exit code / fail a pytest assertion
    (GATING). Body-section '## Related papers' in an overlay is prefixed
    ``[two-layer-lint] WARN:`` (degrades like note.py's other WARN
    classes — see ``run()``'s ``_WARN_PREFIXES``) — a documented, expected
    surface until the incremental_relate edge-write fast-follow lands.
    """
    violations: list[str] = []
    if not core_path.exists() or not overlay_path.exists():
        return violations  # nothing to check — presence is a separate gate

    core_fields, core_body = _parse_frontmatter(core_path.read_text(encoding="utf-8"))
    overlay_fields, overlay_body = _parse_frontmatter(overlay_path.read_text(encoding="utf-8"))

    for key in sorted(set(overlay_fields) & _CORE_ONLY_FIELDS):
        if str(overlay_fields.get(key) or "").strip():
            violations.append(
                f"[two-layer-lint] BLOCK: {overlay_path.name}: intrinsic "
                f"field {key!r} is authored in the overlay — belongs in "
                f"the central core ({core_path})."
            )

    for key in sorted(set(core_fields) & _OVERLAY_ONLY_FIELDS):
        if str(core_fields.get(key) or "").strip():
            violations.append(
                f"[two-layer-lint] BLOCK: {core_path.name}: overlay-only "
                f"field {key!r} is authored in the core — belongs in the "
                f"per-project overlay ({overlay_path})."
            )

    if _CORE_ONLY_BODY_HEADINGS_RE.search(overlay_body):
        violations.append(
            f"[two-layer-lint] WARN: {overlay_path.name}: a core-only body "
            f"section (Result/Key equations/Related papers) was found in "
            f"the overlay — expected until the incremental_relate "
            f"edge-write rewiring fast-follow retargets writes at the "
            f"central core ({core_path}); see scope fence."
        )

    if _OVERLAY_ONLY_BODY_HEADING_RE.search(core_body):
        violations.append(
            f"[two-layer-lint] BLOCK: {core_path.name}: a '## Concept "
            f"edges' section was found in the core — per-project concept "
            f"edges belong in the overlay ({overlay_path})."
        )

    return violations


def cmd_list(project: str, note_type: str | None = None, *,
             config: Config | None = None) -> list[dict[str, Any]]:
    """List OKF notes for the given project.

    If note_type is given, list only that type's subdirectory.
    Returns list of {path, fields} dicts.

    datasets are SHARED — cmd_list for note_type='datasets' scans
    cfg.datasets_root rather than the project-scoped notes directory.
    """
    cfg = config or load_config()
    base = cfg.project_notes_dir(project)

    if note_type:
        types_to_scan = [note_type]
    else:
        types_to_scan = sorted(OKF_TYPES)

    notes = []
    for t in types_to_scan:
        # Shared types live in the shared root, not
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
    - datasets notes (scanned from cfg.datasets_root) have non-empty
      `location` and `hash` fields. The type-dir check is skipped for datasets
      since datasets_root may have any directory name.

    Note: datasets are SHARED across projects. cmd_check scans
    cfg.datasets_root for the datasets type (same root for all projects);
    the 7 other OKF types remain project-scoped in project_notes_dir.

    Note: for experiments notes, cmd_check now also:
    - (plan masters) resolves each covers: child, verifies it EXISTS at the
      experiments/ directory, and checks it has valid stance (confirmatory|
      exploratory) + valid plan_role (main|supporting_ablation|
      conditional_ablation) — BLOCKS on any violation.
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
        # Shared types live in the shared root.
        # Use OKF_SHARED_TYPES SSOT — not a hardcoded "datasets" — so a 2nd
        # shared type is handled automatically.
        if t in OKF_SHARED_TYPES:
            subdir = cfg.datasets_root
        else:
            subdir = base / t
        if not subdir.exists():
            continue

        # Pre-pass for experiments — collect covered_ids from all plan
        # masters so child notes can be checked for absent-from-covers.
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
                # Use OKF_SHARED_TYPES SSOT + `t` for the
                # inner check, so a 2nd shared type is handled automatically.
                if note_type != t:
                    violations.append(
                        f"{p}: expected type={t!r}, got {note_type!r}"
                    )
                # datasets notes must have location and hash filled in.
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
                # validate results_* provenance when results_hash is filled
                # (empty = not yet pulled — not a violation)
                result_issues = check_result_provenance(p)
                violations.extend(result_issues)
                # CHECK-1 (flagship, HARD): the provenance-chain
                # completeness gate — results_commit/repro_seed/repro_config_*
                # (hash-verified)/dataset-link, all non-sentinel when a result
                # is claimed. No _WARN_PREFIXES prefix: this BLOCKS (flips exit).
                chain_issues = check_provenance_chain(p)
                violations.extend(chain_issues)
                # warn when results_hash is set but repro_* are still sentinel
                # (surfaces manual gaps right after the run, not at paper-writing time)
                repro_warnings = check_repro_sentinel_lint(p)
                violations.extend(repro_warnings)
                # F24: warn when experiment ran but dataset provenance is unrecorded
                # (SURFACE, never block — researcher records via `rv note <p> new datasets`)
                dataset_warnings = check_dataset_provenance_warn(p)
                violations.extend(dataset_warnings)
                # covers: link-validation for plan master notes
                # (plan_kind: preregistration); resolves each covers: child,
                # checks stance ∈ {confirmatory, exploratory} and plan_role ∈
                # {main, supporting_ablation, conditional_ablation}.
                if fields.get("plan_kind") == "preregistration":
                    covers_issues = check_covers_links(p, fields, subdir)
                    violations.extend(covers_issues)
                # child note checks — plan_role/stance presence +
                # supports_main target existence + absent-from-covers warning
                # (only for notes with plan_role set).
                child_issues = check_plan_child_links(p, fields, subdir, covered_ids)
                violations.extend(child_issues)
            elif t == "gaps":
                # Standard OKF type-dir contract for gaps.
                if note_type != t:
                    violations.append(
                        f"{p}: type={note_type!r} but file is in {t!r} directory"
                    )
                # check that open/reopened gap anchor: notes still exist.
                # Isomorphic to the covers: resolution check — degrade-to-WARN (not BLOCK).
                anchor_issues = check_gap_anchor(p, fields, base)
                violations.extend(anchor_issues)
            elif t == "literature":
                # Standard OKF type-dir contract for literature. `p` here is
                # the per-project OVERLAY note (t is a two-layer type —
                # OKF_TWO_LAYER_TYPES); intrinsic checks (citekey conformance,
                # the invariant lint) resolve the central CORE via
                # `central:`, never read intrinsic fields off the overlay
                # directly (the resolver is the single read seam).
                if note_type != t:
                    violations.append(
                        f"{p}: type={note_type!r} but file is in {t!r} directory"
                    )
                central = str(fields.get("central") or "").strip()
                if not central:
                    violations.append(
                        f"{p}: literature overlay missing 'central:' pointer "
                        f"(every literature overlay must point at its central core)"
                    )
                else:
                    core_path = literature_core_path(cfg, central)
                    if not core_path.exists():
                        violations.append(
                            f"{p}: dangling 'central:' pointer {central!r} — "
                            f"no central core exists at {core_path}"
                        )
                    else:
                        core_fields, _ = _parse_frontmatter(
                            core_path.read_text(encoding="utf-8")
                        )
                        # citekey conformance (DECIDED K-D2: WARN this
                        # release — promotion to a coverage-gate BLOCK is DEFERRED).
                        # An absent or non-conformant citekey never flips the exit
                        # code; it surfaces so a human can migrate it
                        # (`rv research migrate-citekeys`) or fill it in.
                        citekey_warnings = check_citekey_conformance(p, core_fields)
                        violations.extend(citekey_warnings)
                        # item 8: the two-layer invariant lint — GATING
                        # (BLOCK entries flip the exit code; WARN entries
                        # degrade like the other WARN classes below).
                        violations.extend(check_two_layer_invariants(core_path, p))
            else:
                # Standard OKF type-dir contract for the other project-scoped types
                if note_type != t:
                    violations.append(
                        f"{p}: type={note_type!r} but file is in {t!r} directory"
                    )

    return violations


# ---------------------------------------------------------------------------
# Vanished-anchor check for gap notes
# ---------------------------------------------------------------------------

#: Gap statuses that count toward open_gap_count — the actionable ones that
#: need a live anchor.  Closed/proven-open/promoted gaps are resolved; their
#: anchor vanishing is low-urgency and would create noise for cleaned-up notes.
_ACTIONABLE_GAP_STATUSES: frozenset[str] = frozenset({"open", "reopened"})


def check_gap_anchor(
    gap_note_path: Path,
    fields: dict[str, str],
    project_notes_dir: Path,
) -> list[str]:
    """Warn when an open/reopened gap's anchor: note no longer exists.

    A gap note carries an ``anchor:`` field — an OKF path relative to
    ``project_notes_dir`` (e.g. ``findings/slug``, ``literature/citekey``).  When
    the anchored artifact is deleted or renamed, the gap inflates ``open_gap_count``
    with a dead reference.

    This check is isomorphic to the ``covers:`` resolution check in
    ``check_covers_links`` — resolve the referenced path; if it no longer resolves,
    degrade-to-WARN (not BLOCK).  The warning surfaces the stale reference so the
    human can re-anchor, re-scan, or close the gap.

    Only ``open`` and ``reopened`` gaps are checked (the statuses that count toward
    ``open_gap_count``).  Closed/proven-open/promoted gaps are skipped — their
    anchor vanishing is less urgent and would produce noise for resolved gaps
    whose source notes have been cleaned up.

    Args:
        gap_note_path:      path to the gap note (for error messages).
        fields:             frontmatter dict of the gap note.
        project_notes_dir:  project root used to resolve ``anchor:`` paths
                            (e.g. ``cfg.project_notes_dir(project)``).

    Returns:
        list of warning strings prefixed with ``[gap-hygiene] WARN:``.
        Empty = clean (live anchor or non-actionable status).
    """
    status = fields.get("status", "open").strip()
    if status not in _ACTIONABLE_GAP_STATUSES:
        return []

    anchor = fields.get("anchor", "").strip()
    if not anchor:
        return []

    anchor_path = project_notes_dir / f"{anchor}.md"
    if anchor_path.exists():
        return []

    return [
        f"[gap-hygiene] WARN: {gap_note_path.name}: anchor {anchor!r} no longer "
        f"exists at {anchor_path} — re-scan (rv review gap-scan), "
        f"re-anchor, or close this gap (rv review gap-close {gap_note_path.stem})"
    ]


# ---------------------------------------------------------------------------
# citekey conformance (WARN this release — see cite.CITEKEY_RE)
# ---------------------------------------------------------------------------

def check_citekey_conformance(
    lit_note_path: Path,
    fields: dict[str, str],
) -> list[str]:
    """WARN when a literature note's ``citekey:`` is absent or non-conformant.

    DECIDED K-D2: this is a WARN-only lint this release — it never
    flips ``rv note check``'s exit code. Promotion to a coverage-gate BLOCK
    is DEFERRED to a future release (once enough of the corpus has been
    migrated via ``rv research migrate-citekeys`` that a hard gate wouldn't
    just fire on every pre-existing note).

    Conformance is ``cite.CITEKEY_RE`` — the single ``familyShorttitleYear``
    convention (K-D1). The visible unresolvable-metadata sentinel
    (``cite.CITEKEY_SENTINEL``) also fails conformance on purpose — a note
    stuck at "citekey could not be computed" should keep surfacing until a
    human fills in title/authors/year and re-runs the stamp.
    """
    from .cite import CITEKEY_RE  # local import: note.py is the OKF-check SSOT; avoid a cite.py module-level dep

    citekey = (fields.get("citekey") or "").strip()
    if not citekey:
        return [
            f"[citekey-lint] WARN: {lit_note_path.name}: missing 'citekey' field "
            f"— fill by hand or run `rv research citekey <project> "
            f"{lit_note_path.stem}` once title/authors/year are filled in"
        ]
    if not CITEKEY_RE.match(citekey):
        return [
            f"[citekey-lint] WARN: {lit_note_path.name}: citekey {citekey!r} does "
            f"not conform to the familyShorttitleYear convention — re-run "
            f"`rv research citekey <project> {lit_note_path.stem}` or "
            f"`rv research migrate-citekeys <project>` to fix it"
        ]
    return []


# ---------------------------------------------------------------------------
# covers:/stance link-validation helpers
# ---------------------------------------------------------------------------

def _parse_covers_list(covers_str: str) -> list[str]:
    """Parse a flat inline YAML list string like '[a, b, c]' into Python list.

    Mirrors plan/freeze.py's _parse_covers_list — kept private to note.py so
    plan/ stays note.py-free.  The two implementations are intentionally
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
    """Validate that each covers: entry exists with valid stance + plan_role.

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
                f"from covers: (link-validation)"
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
                f""
            )
        elif stance not in _VALID_STANCE:
            violations.append(
                f"{plan_note_path}: covers: child {child_id!r} has "
                f"invalid stance={stance!r} "
                f"(expected: confirmatory or exploratory)"
            )

        if not plan_role:
            violations.append(
                f"{plan_note_path}: covers: child {child_id!r} is missing "
                f"'plan_role' field "
                f"(expected: main, supporting_ablation, or conditional_ablation)"
            )
        elif plan_role not in _VALID_PLAN_ROLE:
            violations.append(
                f"{plan_note_path}: covers: child {child_id!r} has "
                f"invalid plan_role={plan_role!r} "
                f"(expected: main, supporting_ablation, or conditional_ablation)"
            )

    return violations


def check_plan_child_links(
    exp_note_path: Path,
    fields: dict[str, str],
    notes_root: Path,
    covered_ids: "set[str]",
) -> list[str]:
    """Validate plan_role / stance / supports_main on a child note.

    Checks performed only when ``plan_role`` is set (i.e., the note is a plan child):
    - ``stance`` must be present (confirmatory or exploratory)
    - If ``stance: confirmatory`` AND covered_ids is non-empty, the note's ID must
      appear in at least one plan master's ``covers:`` list
    - If ``supports_main`` is set, the target note must exist

    The absent-from-covers check is *skipped* when covered_ids is empty (no plan
    masters in this project) — degrade-to-skip, not degrade-to-block.

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
                f"add stance: confirmatory or stance: exploratory "
            )
        elif stance == "confirmatory" and covered_ids and note_id not in covered_ids:
            violations.append(
                f"{exp_note_path}: stance=confirmatory but {note_id!r} "
                f"is not in any plan master's covers: list — "
                f"add it to the plan master or check for a typo "
            )

    if supports_main:
        target = notes_root / f"{supports_main}.md"
        if not target.exists():
            violations.append(
                f"{exp_note_path}: supports_main={supports_main!r} target "
                f"note not found at {target} "
            )

    return violations


# ---------------------------------------------------------------------------
# Experiment-results provenance validation
# ---------------------------------------------------------------------------

def _is_local_results_path(location: str) -> bool:
    """Return True if results_location looks like a local filesystem path (not URL)."""
    lower = location.lower()
    for prefix in ("http://", "https://", "ftp://", "s3://", "gs://", "doi:", "hdfs://"):
        if lower.startswith(prefix):
            return False
    return True


def _normalize_results(fields: "dict[str, Any]") -> "dict[str, list]":
    """ (D2) read-shim: the ONE canonical results reader.

    Returns ``{"runs": list[str], "scores": list[dict[str, str]]}`` — the
    generalized N runs -> M scores schema (of the CS-project-structure
    spec), covering every cardinality (1->1, N->1, 1->M, N->M).

    Folds the deprecated flat scalar fields (``results_location``,
    ``results_hash``, ``results_wandb_run``) into 1-element lists when the new
    list fields (``scores:``, ``runs:``) are absent/empty — so every existing
    flat note (and rv's demo-research examples) verifies UNCHANGED. There is
    exactly one canonical form (the lists) plus this read-only shim, never two
    co-equal forms.

    A legacy ``results_hash`` value of "" or the REPRO_SENTINEL placeholder is
    treated as "not yet run" (no scores entry synthesized) — matching the
    existing not-yet-run semantics of check_dataset_provenance_warn /
    check_repro_sentinel_lint, which is the standing "empty run" gate.
    A legacy `results_wandb_run` is folded the same way (empty ->
    no runs entry).

    Shared by note.py (check_result_provenance + the sibling lints) and
    result.py (rv result assert's metric/hash reads) — one source of truth.
    """
    scores_raw = fields.get("scores", [])
    scores: list[dict[str, str]] = []
    if isinstance(scores_raw, list):
        scores = [item for item in scores_raw if isinstance(item, dict)]

    if not scores:
        legacy_hash = fields.get("results_hash", "")
        legacy_hash = legacy_hash.strip() if isinstance(legacy_hash, str) else ""
        if legacy_hash == REPRO_SENTINEL:
            legacy_hash = ""  # sentinel = "not-recorded" placeholder, not a real hash
        legacy_location = fields.get("results_location", "")
        legacy_location = (
            legacy_location.strip() if isinstance(legacy_location, str) else ""
        )
        # Trigger on results_hash SET only (spec D2) — NOT "either field
        # present". A location-only legacy note (results_location filled,
        # results_hash still empty — the not-yet-run stub shape, e.g. rv's
        # shipped demo-research q1-main1-cabl-Y.md conditional-ablation note
        # whose trigger hasn't fired) is "not yet run", matching pre-
        # behaviour: check_result_provenance skips ([]), it is not a
        # violation. (Reviewer-caught regression, PR the earlier
        # `legacy_hash or legacy_location` superset flagged this shipped
        # not-yet-run state as "scores entry missing 'hash'".)
        if legacy_hash:
            scores = [{"location": legacy_location, "hash": legacy_hash}]

    runs_raw = fields.get("runs", [])
    runs: list[str] = []
    if isinstance(runs_raw, list):
        runs = [item for item in runs_raw if isinstance(item, str) and item.strip()]

    if not runs:
        legacy_run = fields.get("results_wandb_run", "")
        legacy_run = legacy_run.strip() if isinstance(legacy_run, str) else ""
        if legacy_run:
            runs = [legacy_run]

    return {"runs": runs, "scores": scores}


def check_result_provenance(exp_note_path: Path) -> list[str]:
    """Validate the results attachment (``scores:`` list, D2/D8) of an experiment note.

    When to use: called by cmd_check (rv note check) to validate that every
    filled score anchor is hash-consistent.

    Reworked to iterate ``_normalize_results(fields)["scores"]`` — the
    generalized N->M results schema — instead of a single flat
    results_location/results_hash pair:
      - Empty scores list -> not yet run -> [] (unchanged skip semantics;
        empty is NOT a violation).
      - Per entry: ``location`` and ``hash`` both required (fail-closed on
        either missing); local path -> file exists AND sha256 matches;
        URL/DOI/remote -> trust the recorded hash (zero-infra).
      - ALL per-entry violations are aggregated (every bad score is reported,
        not just the first) — the DAG complete-gate now enforces every score
        anchor, not one.

    Legacy flat notes (results_location/results_hash) verify unchanged via the
    _normalize_results shim (folded into a 1-element scores list).

    Returns a list of violation strings (empty = OK, gate passes).
    Reuses the streaming hash pattern from
    wait_for._verify_local_file_hash.
    """
    if not exp_note_path.exists():
        return [f"experiment note does not exist: {exp_note_path}"]

    try:
        text = exp_note_path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"cannot read experiment note {exp_note_path}: {e}"]

    fields, _ = _parse_frontmatter(text)
    scores = _normalize_results(fields)["scores"]

    # Empty scores list → not yet run, skip validation (unchanged semantics)
    if not scores:
        return []

    violations: list[str] = []
    for i, entry in enumerate(scores):
        label = entry.get("label") or entry.get("location") or f"scores[{i}]"
        score_hash = (entry.get("hash") or "").strip()
        location = (entry.get("location") or "").strip()

        if not score_hash:
            violations.append(
                f"{exp_note_path.name}: scores entry {label!r} is missing 'hash'"
            )
            continue

        if not location:
            violations.append(
                f"{exp_note_path.name}: scores entry {label!r}: "
                f"hash is set but 'location' is empty"
            )
            continue

        # For URL / DOI / remote: trust the recorded hash (zero-infra, no fetch)
        if not _is_local_results_path(location):
            continue

        # Local file: verify existence + hash
        artifact = Path(location)
        if not artifact.exists():
            violations.append(
                f"{exp_note_path.name}: results artifact not found: {location}"
            )
            continue

        if score_hash.startswith("sha256:"):
            expected_hex = score_hash[len("sha256:"):]
            try:
                actual_hex = _hash_file(artifact)[len("sha256:"):]
            except OSError as e:
                violations.append(
                    f"{exp_note_path.name}: cannot read results artifact {location}: {e}"
                )
                continue

            if actual_hex != expected_hex:
                violations.append(
                    f"{exp_note_path.name}: results hash mismatch for {location} "
                    f"(expected sha256:{expected_hex[:12]}…, "
                    f"actual sha256:{actual_hex[:12]}…)"
                )

    return violations


# ---------------------------------------------------------------------------
# provenance-chain completeness gate ★ FLAGSHIP (note-plane, HARD)
# ---------------------------------------------------------------------------

def check_provenance_chain(exp_note_path: Path) -> list[str]:
    """CHECK-1 (+ folded CHECK-2, CHECK-3a): provenance-chain completeness.

    When to use: called by cmd_check (rv note check) for experiments notes,
    immediately after check_result_provenance. Also invoked at the DAG
    complete-gate (dag/verbs.py::cmd_complete) for any produces.result /
    produces.note node whose note type is "experiments" — so a claimed result
    cannot be marked complete with a broken provenance chain.

     CHECK-1/CHECK-2/CHECK-3a. Zero new field, zero new walker — every
    field asserted here already exists in the reproducibility schema.

    Rule: when _normalize_results(fields)["scores"] is non-empty (a result is
    claimed), ALL of the following must be non-sentinel and non-empty:
      - results_commit                          (git SHA of the producing code)
      - repro_seed                               (R1: promoted from WARN to HARD —
                                                   a seedless claimed result is not
                                                   reproducible)
      - repro_config_location + repro_config_hash, AND the file at that path
        hashes to repro_config_hash              (CHECK-2, folded in)
      - at least one of repro_dataset_id or repro_dataset_hash (dataset link)

    Per-field REPRO_NOT_APPLICABLE exemption — TIGHTENED (operator review call,
    2026-07-07): the exemption does NOT apply uniformly to every field.

      - results_commit and repro_seed are ALWAYS required once a result is
        claimed. A result-claiming note always has a producing commit and a
        seed; REPRO_NOT_APPLICABLE is REJECTED on these two fields (same as
        missing/sentinel — HARD block). This preserves the gate's core
        guarantee: every claimed result traces to a commit + seed. Widening
        this to "any field can escape via not-applicable" would let a note
        dodge the entire chain by marking results_commit itself N/A, which
        defeats the point of the gate.
      - repro_config_location/repro_config_hash (config-artifact pair) and
        the dataset link (repro_dataset_id/repro_dataset_hash) REMAIN
        exemptible via REPRO_NOT_APPLICABLE — a legitimately-no-config
        (in-memory) analysis or a no-external-dataset study can honestly
        declare not-applicable without being forced to fabricate a field it
        doesn't have. This is the mitigation for the one risk the design
        calls out: CHECK-1 riding the complete-gate must not block a
        legitimate proxy/no-run finding on fields where N/A is honest. The
        repro_config_location/repro_config_hash pair is treated as ONE unit:
        either being REPRO_NOT_APPLICABLE exempts the whole config-artifact
        requirement (hash-match is meaningless without both).

    CHECK-3a (notebook invariant, D-CC-1): no scores[] entry's location may end
    in ".ipynb" — a claimed result's number must never be notebook-sourced.

    Backward-compat: a note with no claimed result (empty scores list, e.g. rv's
    shipped demo-research stub notes, or any not-yet-run note) is skipped
    entirely — unchanged pre-CHECK-1 semantics.

    Returns a list of violation strings (empty = OK, gate passes). Always HARD
    — never carries a _WARN_PREFIXES prefix (no "[repro-lint]"/"[gap-hygiene]"/
    "[dataset-provenance]"), so cmd_check's run() flips exit 1 on any violation.
    """
    if not exp_note_path.exists():
        return [f"experiment note does not exist: {exp_note_path}"]

    try:
        text = exp_note_path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"cannot read experiment note {exp_note_path}: {e}"]

    fields, _ = _parse_frontmatter(text)
    scores = _normalize_results(fields)["scores"]

    # No result claimed → not yet run → skip (unchanged pre-CHECK-1 semantics)
    if not scores:
        return []

    name = exp_note_path.name
    violations: list[str] = []

    # CHECK-3a: no claimed score may be notebook-sourced (D-CC-1)
    for i, entry in enumerate(scores):
        location = (entry.get("location") or "").strip()
        if location.lower().endswith(".ipynb"):
            label = entry.get("label") or location or f"scores[{i}]"
            violations.append(
                f"{name}: scores entry {label!r} location is a notebook "
                f"({location}) — a claimed result must never be notebook-sourced "
                f"(CHECK-3a, D-CC-1); move the computation into code/src/ and re-run"
            )

    # results_commit — git SHA of the producing code. ALWAYS required — the
    # REPRO_NOT_APPLICABLE exemption does NOT apply here (tightened 2026-07-07):
    # a result-claiming note always has a producing commit, so not-applicable
    # is rejected exactly like missing/sentinel.
    commit = fields.get("results_commit", "").strip()
    if not commit or commit == REPRO_SENTINEL or commit == REPRO_NOT_APPLICABLE:
        violations.append(
            f"{name}: results claimed (scores set) but 'results_commit' is "
            f"missing/sentinel/not-applicable — record the git SHA of the "
            f"producing code (CHECK-1); this field is always required and "
            f"cannot be marked {REPRO_NOT_APPLICABLE!r}"
        )

    # repro_seed — R1: promoted from the soft sentinel-lint into this HARD
    # chain. ALWAYS required — same tightening as results_commit: a
    # result-claiming note always has a seed, so not-applicable is rejected.
    seed = fields.get("repro_seed", "").strip()
    if not seed or seed == REPRO_SENTINEL or seed == REPRO_NOT_APPLICABLE:
        violations.append(
            f"{name}: results claimed but 'repro_seed' is "
            f"missing/sentinel/not-applicable — a seedless claimed result is "
            f"not reproducible (CHECK-1/CHECK-4a, R1); record the seed — "
            f"this field is always required and cannot be marked "
            f"{REPRO_NOT_APPLICABLE!r}"
        )

    # repro_config_location + repro_config_hash + config-hash-match (CHECK-2)
    config_location = fields.get("repro_config_location", "").strip()
    config_hash = fields.get("repro_config_hash", "").strip()
    if config_location == REPRO_NOT_APPLICABLE or config_hash == REPRO_NOT_APPLICABLE:
        pass  # proxy/no-run: honor the exemption on the whole config-artifact pair
    else:
        if not config_location or config_location == REPRO_SENTINEL:
            violations.append(
                f"{name}: results claimed but 'repro_config_location' is "
                f"missing/sentinel — record the path to the run config artifact "
                f"(CHECK-1/CHECK-2)"
            )
        if not config_hash or config_hash == REPRO_SENTINEL:
            violations.append(
                f"{name}: results claimed but 'repro_config_hash' is "
                f"missing/sentinel — record the sha256 of the config artifact "
                f"(CHECK-1/CHECK-2)"
            )
        if (
            config_location and config_location != REPRO_SENTINEL
            and config_hash and config_hash != REPRO_SENTINEL
        ):
            # For URL / DOI / remote: trust the recorded hash (zero-infra, no
            # fetch) — same policy as check_result_provenance's local-path guard.
            if _is_local_results_path(config_location):
                artifact = Path(config_location)
                if not artifact.exists():
                    violations.append(
                        f"{name}: repro_config_location artifact not found: "
                        f"{config_location} (CHECK-2 config-hash-match)"
                    )
                elif config_hash.startswith("sha256:"):
                    expected_hex = config_hash[len("sha256:"):]
                    try:
                        actual_hex = _hash_file(artifact)[len("sha256:"):]
                    except OSError as e:
                        violations.append(
                            f"{name}: cannot read repro_config_location "
                            f"artifact {config_location}: {e}"
                        )
                    else:
                        if actual_hex != expected_hex:
                            violations.append(
                                f"{name}: repro_config_hash mismatch for "
                                f"{config_location} (expected "
                                f"sha256:{expected_hex[:12]}…, actual "
                                f"sha256:{actual_hex[:12]}…) (CHECK-2)"
                            )

    # Dataset link: at least one of repro_dataset_id / repro_dataset_hash
    dataset_id = fields.get("repro_dataset_id", "").strip()
    dataset_hash = fields.get("repro_dataset_hash", "").strip()
    if dataset_id == REPRO_NOT_APPLICABLE:
        pass  # explicit proxy/no-external-dataset exemption — matches
        # check_dataset_provenance_warn's existing honored escape exactly
    else:
        id_ok = bool(dataset_id) and dataset_id != REPRO_SENTINEL
        hash_ok = bool(dataset_hash) and dataset_hash != REPRO_SENTINEL
        if not (id_ok or hash_ok):
            violations.append(
                f"{name}: results claimed but no dataset link recorded "
                f"(repro_dataset_id/repro_dataset_hash both missing/sentinel) "
                f"— set one, or repro_dataset_id: {REPRO_NOT_APPLICABLE!r} if no "
                f"external dataset was used (CHECK-1)"
            )

    return violations


# ---------------------------------------------------------------------------
# Repro sentinel lint
# ---------------------------------------------------------------------------

def check_dataset_provenance_warn(exp_note_path: Path) -> list[str]:
    """F24: warn when a ran experiment has unrecorded dataset provenance.

    Fires when:
      - _normalize_results(fields)["scores"] is non-empty (any score recorded
         retarget: any score anchor, list form or the legacy flat-field
        shim, counts as "ran")
      - repro_dataset_id is still the sentinel (no datasets note linked)

    This is a SURFACE, never a BLOCK — INFO/WARN only.
    The researcher can silence it by:
      (a) creating a datasets provenance note and setting
          repro_dataset_id: datasets/<slug>, OR
      (b) setting repro_dataset_id: not-applicable (proxy/no-external-dataset run)

    Called by cmd_check alongside check_repro_sentinel_lint for experiments notes.
    """
    if not exp_note_path.exists():
        return []

    try:
        text = exp_note_path.read_text(encoding="utf-8")
    except OSError:
        return []

    fields, _ = _parse_frontmatter(text)

    # No results yet → warn does not fire (experiment not yet run). Gate
    # on the normalized scores list (any score recorded), not the raw flat
    # results_hash field — this is what makes the warn fire for list-form
    # scores: too, while _normalize_results' own REPRO_SENTINEL exclusion
    # preserves the legacy "results_hash == sentinel" not-yet-run case.
    if not _normalize_results(fields)["scores"]:
        return []

    dataset_id = fields.get("repro_dataset_id", "").strip()

    # Explicitly not-applicable (proxy/no-external-data) → no warn
    if dataset_id == REPRO_NOT_APPLICABLE:
        return []

    # Filled (not sentinel, not empty) → no warn
    if dataset_id and dataset_id != REPRO_SENTINEL:
        return []

    # Warn: ran but no dataset provenance note linked
    return [
        f"[dataset-provenance] WARN: {exp_note_path.name}: "
        f"experiment ran (results_hash set) but repro_dataset_id is still the sentinel "
        f"({REPRO_SENTINEL!r}) — record dataset provenance via "
        f"`rv note <project> new datasets <title>` then set "
        f"repro_dataset_id: datasets/<slug>, or set "
        f"repro_dataset_id: {REPRO_NOT_APPLICABLE!r} if no external dataset was used"
    ]


def check_repro_sentinel_lint(exp_note_path: Path) -> list[str]:
    """Warn when a score is recorded but required repro_* fields are still the sentinel.

    When to use: called by cmd_check (rv note check) alongside check_result_provenance
    for experiments notes. Surfaces manual gaps RIGHT AFTER the run, not at paper-writing
    time — when the information is still fresh and accessible.

    Lint fires only when:
      - _normalize_results(fields)["scores"] is non-empty (retarget: any
        score recorded, list form or the legacy flat-field shim)
      - At least one REPRO_LINT_REQUIRED field is still the sentinel

    No scores recorded (experiment not yet run) → no lint (not a violation).

    Returns a list of warning strings prefixed with "[repro-lint] WARN:" (empty = clean).
    Anti-fabrication: the sentinel is an honest hole, not a guessed value.
    """
    if not exp_note_path.exists():
        return []

    try:
        text = exp_note_path.read_text(encoding="utf-8")
    except OSError:
        return []

    fields, _ = _parse_frontmatter(text)

    # No results yet → lint does not fire
    if not _normalize_results(fields)["scores"]:
        return []

    warnings: list[str] = []
    for field in REPRO_LINT_REQUIRED:
        val = fields.get(field, "").strip()
        # Only warn when the field is EXPLICITLY the sentinel — never on absent/empty fields.
        # "Absence of the whole block is not a violation (optional, like results_*)".
        # A visible sentinel is the honest hole left by cmd_new; it warns RIGHT AFTER the run.
        #
        # Two distinct non-warning states (must not be confused):
        #   REPRO_SENTINEL       → default hole from cmd_new — WARN to fill
        #   REPRO_NOT_APPLICABLE → explicit proxy/no-run marker — SKIP (genuinely N/A)
        if val == REPRO_NOT_APPLICABLE:
            continue  # Explicit proxy/no-run annotation: skip lint for this field
        if val == REPRO_SENTINEL:
            warnings.append(
                f"[repro-lint] WARN: {exp_note_path.name}: "
                f"results_hash is set but {field!r} is still the sentinel "
                f"({REPRO_SENTINEL!r}) — fill in, or set to {REPRO_NOT_APPLICABLE!r} "
                f"if this is a proxy/no-run analysis where the field is genuinely not applicable"
            )

    return warnings


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(parent: argparse._SubParsersAction | None = None) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Build the argument parser for the `note` verb.

    When to use: use `rv note <project> <subcommand>` to create or inspect OKF notes.
    Notes are typed markdown files (literature, concepts, methods, experiments, findings,
    mocs, datasets) stored under the project's notes directory. The type field in
    frontmatter is enforced. datasets notes are provenance metadata — they POINT to
    data artifacts (path/URL/DOI + content-hash), never contain the data itself.
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
            # Separate hard violations from soft warnings.
            # Prefixes that degrade-to-warn (shown but do not flip exit code):
            #   [repro-lint] WARN: — repro-sentinel lint
            #   [gap-hygiene] WARN: — vanished anchor on open/reopened gap
            #   [dataset-provenance] WARN: — unrecorded dataset provenance on a ran
            #     experiment (F24). check_dataset_provenance_warn's own docstring
            #     states this is "SURFACE, never a BLOCK — INFO/WARN only"; it must
            #     degrade like the other two WARN classes, not hard-fail.
            #   [citekey-lint] WARN: — literature note citekey absent/non-conformant
            #     (DECIDED K-D2: WARN this release, BLOCK deferred).
            #   [two-layer-lint] WARN: — '## Related papers' found in an
            #     overlay; expected until the incremental_relate edge-write
            #     fast-follow lands. Note: "[two-layer-lint]
            #     BLOCK:" entries do NOT match this (more specific) prefix,
            #     so they stay hard.
            _WARN_PREFIXES = (
                "[repro-lint]", "[gap-hygiene]", "[dataset-provenance]", "[citekey-lint]",
                "[two-layer-lint] WARN:",
            )
            hard = [v for v in violations if not v.startswith(_WARN_PREFIXES)]
            warnings = [v for v in violations if v.startswith(_WARN_PREFIXES)]
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
