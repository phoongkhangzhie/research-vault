"""experiment.py — `rv experiment new` scaffolder (SR-HUB-DAG §B).

Purpose
-------
The experiment loop is the ONLY built-in research loop that had NO scaffolder —
``rv review`` scaffolds its DAG and registers the run.  The experiment loop was run
ad-hoc, meaning:
  - No ``rv plan freeze`` had a ``run_id`` to hash.
  - The K-3 covers:-hash guarantee was silently lost.

This module closes that gap.

``rv experiment new <project> <id> --question "..." [--mains N] [--scope ...]``
  1. Authors the pre-registration plan note skeleton
     at ``experiments/<id>-plan.md`` with ``plan_kind: preregistration``.
  2. Emits a REGISTERED experiment DAG manifest (mirroring research-loop.json
     topology) to ``experiments/<id>-loop.json`` inside the project notes dir.
  3. PRINTS the exact next commands so the freeze cannot be silently skipped:
       rv dag run <manifest>
       rv dag approve <run_id> human-go-plan && rv plan freeze <run_id> <plan-note>

The topology emitted (for N=2 mains, each with 1 ablation) is:
  plan → plan-critic → [HG:human-go-plan]
      → {per-main: <id>-main<k>-run → <id>-main<k>-score → <id>-main<k>-analyze
                   + <id>-main<k>-abl-A-run → … → <id>-main<k>-abl-A-analyze}
      → [HG:human-go-conditionals-main<k>]
  → [HG:human-go-findings]
  → methods-update

ZERO new DAG mechanism — composes the existing walker/schema/store.
All node IDs, gate names, and manifest fields mirror the SHIPPED research-loop.json.

Stdlib only.
sr: SR-HUB-DAG
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

from .config import load_config
from .dag.schema import validate_manifest, ManifestError, dump_manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.date.today().isoformat()


def _plan_note_skeleton(
    project_id: str,
    exp_id: str,
    question: str,
    n_mains: int,
    scope: list[str],
) -> str:
    """Return the pre-registration plan note content (skeleton).

    Mirrors the q1-plan.md shape from data/examples/demo-research/notes/experiments/.
    The researcher fills in the blanks after scaffolding.
    """
    today = _today()
    # Build covers: list — main + abl-A per main
    covers_items: list[str] = []
    for k in range(1, n_mains + 1):
        covers_items.append(f"{exp_id}-main{k}")
        covers_items.append(f"{exp_id}-main{k}-abl-A")
    covers_str = "[" + ", ".join(covers_items) + "]"

    scope_note = ""
    if scope:
        scope_note = "scope: [" + ", ".join(scope) + "]\n"

    frontmatter = (
        f"type: experiments\n"
        f"citekey: {exp_id}-plan\n"
        f"title: \"{exp_id} Pre-Registration Plan\"\n"
        f"plan_kind: preregistration\n"
        f"covers: {covers_str}\n"
        f"status: draft\n"
        f"date: {today}\n"
    )
    if scope_note:
        frontmatter += scope_note

    body = f"""# {exp_id} Pre-Registration Plan

**plan_kind:** preregistration
**covers:** {", ".join(covers_items)}
**Freeze gate:** human-go-plan (K-3 covers:-hash stored on approval)
**Research question:** {question}

All confirmatory child notes listed in `covers:` must be written as stubs
before any run fires.  The confirmatory set is frozen at `human-go-plan`;
exploratory experiments may be added after the freeze with `stance: exploratory`.

---

"""

    for k in range(1, n_mains + 1):
        main_id = f"{exp_id}-main{k}"
        abl_id = f"{exp_id}-main{k}-abl-A"
        body += f"""## Main {k} — {main_id}: [claim to fill in]

### Claim arrow

`[manipulation] → [outcome]` under condition `[model/eval/dataset]`

Exact manipulation: [describe the specific change].
Specific outcome: [name the metric and evaluation set].

### Pre-registered analysis

- **Estimand:** [metric description]
- **Test statistic:** [test name, sample size]
- **Comparison baseline:** [frozen at run dispatch]
- **Units:** [e.g. accuracy points 0–1]
- **Decision threshold:** [Δmetric ≥ N]
- **Noise floor:** [seed variance estimate from pilot seeds]

### Falsifier

A result of [concrete number] would refute the claim.

### Planned artifact

- Run note: `experiments/{main_id}.md`
- Results file: `results/{main_id}/scores.jsonl`
- SHA: to be filled at run dispatch.

### Main {k} Diagnosis Table

| Outcome range | Named conclusion | Committed action |
|---|---|---|
| [above threshold] | [named conclusion — e.g. claim supported] | [committed action] |
| [ambiguous range] | [named conclusion — e.g. effect below threshold] | [committed action] |
| [below noise floor] | [named conclusion — e.g. null result] | [committed action] |

---

## Main {k} — Supporting Ablation A: isolates [component]

**Purpose:** rule out [confound] — isolates exactly ONE component.

Component manipulated: [name the single component].

### Ablation A Diagnosis Table

| Outcome range | Named conclusion | Committed action |
|---|---|---|
| Effect maintained | [conclusion] | [action] |
| Effect reduced | [conclusion] | [action] |
| Effect eliminated | [conclusion] | [action] |

### Planned artifact

- Run note: `experiments/{abl_id}.md`
- Results file: `results/{abl_id}/scores.jsonl`
- SHA: to be filled at run dispatch.

---

"""

    return f"---\n{frontmatter}---\n\n{body}"


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------

def _build_experiment_manifest(
    project: str,
    exp_id: str,
    question: str,
    n_mains: int,
    plan_note_path: Path,
    notes_dir: Path,
) -> dict[str, Any]:
    """Build the experiment DAG manifest (mirrors research-loop.json topology).

    Nodes (for N mains, each with 1 supporting ablation):
      plan → plan-critic → [HG:human-go-plan]
          → {per-main k:
               <id>-main<k>-run → <id>-main<k>-score → <id>-main<k>-analyze
               + <id>-main<k>-abl-A-run → <id>-main<k>-abl-A-score
                 → <id>-main<k>-abl-A-analyze}
          → [HG:human-go-conditionals-main<k>]
      → [HG:human-go-findings]
      → methods-update

    All node IDs and gate names mirror the shipped research-loop.json SSOT.
    spec: pointers use plan_note_path for plan node; doctrine pointers for others.
    reads: uses absolute paths (Fix #34 pattern from review scaffold).

    Zero new walker/schema mechanism — standard afterok edges throughout.
    """

    def _abs(okf_type: str) -> str:
        """Absolute OKF type-dir pointer (Fix #34 pattern — review/__init__.py)."""
        return str(notes_dir / okf_type)

    def _afterok(from_id: str) -> dict[str, Any]:
        return {"from": from_id, "edge": "afterok"}

    run_id = f"{exp_id}-loop"
    nodes: list[dict[str, Any]] = []

    # 1. plan — researcher authors the pre-registration master + child stubs
    nodes.append({
        "id": "plan",
        "type": "agent",
        "label": (
            f"Plan experiments + write pre-registration master + child stubs "
            f"(researcher): {question[:60]}"
        ),
        "role": "researcher",
        "spec": str(plan_note_path),
        "produces": {"note": f"experiments/{exp_id}-plan.md"},
        "needs": [],
        "reads": [
            _abs("doctrine/plan-critic-spec.md") + "#plan-critic spec"
            if (notes_dir.parent / "doctrine" / "plan-critic-spec.md").exists()
            else "doctrine/plan-critic-spec.md#plan-critic spec",
            _abs("experiments"),
        ],
    })

    # 2. plan-critic — independent reviewer critiques the pre-registration plan
    nodes.append({
        "id": "plan-critic",
        "type": "agent",
        "label": "Critique pre-registration plan — independent review (reviewer)",
        "role": "reviewer",
        "spec": "doctrine/plan-critic-spec.md",
        "needs": [_afterok("plan")],
        "reads": [
            "doctrine/plan-critic-spec.md",
            "doctrine/roles/reviewer.md",
        ],
    })

    # 3. human-go-plan — K-3 freeze gate
    nodes.append({
        "id": "human-go-plan",
        "type": "human-go",
        "label": (
            "Human approval gate — plan quality, pre-registration filed, "
            "covers:-hash frozen (run `rv plan freeze <run_id> <plan-note>` after approval)"
        ),
        "needs": [_afterok("plan-critic")],
    })

    # 4. Per-main branches
    for k in range(1, n_mains + 1):
        main_id = f"{exp_id}-main{k}"
        abl_id = f"{exp_id}-main{k}-abl-A"

        # main run
        nodes.append({
            "id": f"{main_id}-run",
            "type": "agent",
            "label": f"Run Main {k} — {main_id} (researcher)",
            "role": "researcher",
            "spec": (
                f"Run the pre-registered Main {k} experiment: {main_id}.\n\n"
                f"Research question: {question}\n\n"
                f"Your task:\n"
                f"1. Read the pre-registration plan note (experiments/{exp_id}-plan.md) "
                f"— specifically the 'Main {k} — {main_id}' section.\n"
                f"2. Follow the exact protocol declared there (manipulation, baseline, "
                f"evaluation set, run configuration).\n"
                f"3. Execute the run following the compute recipe "
                f"(see reads: doctrine/compute-run-recipe.md).\n"
                f"4. Record the run provenance in the experiment note: "
                f"experiments/{main_id}.md (type: experiments, "
                f"results_hash: sha256 of the results file, "
                f"run_id: job id from the scheduler).\n"
                f"5. Return ⟦RETURN⟧ then: "
                f"rv dag complete <run_id> {main_id}-run"
            ),
            "produces": {"note": f"experiments/{main_id}.md"},
            "needs": [
                _afterok("human-go-plan"),
                {
                    "from": "plan",
                    "edge": "afterok",
                    "watch": f"note:experiments/{main_id}.md+fresh",
                },
            ],
            "reads": [_abs("experiments"), "doctrine/compute-run-recipe.md#how to run here"],
        })

        # main score
        nodes.append({
            "id": f"{main_id}-score",
            "type": "agent",
            "label": f"Score Main {k} — {main_id} (researcher)",
            "role": "researcher",
            "spec": (
                f"Score the completed Main {k} experiment: {main_id}.\n\n"
                f"Your task:\n"
                f"1. Read the experiment note experiments/{main_id}.md — confirm "
                f"run provenance fields (results_hash, run_id) are filled.\n"
                f"2. Run the pre-registered scoring procedure (as declared in the "
                f"plan note experiments/{exp_id}-plan.md, Main {k} section).\n"
                f"3. Verify the results_hash matches the results file on disk "
                f"(hash it yourself to confirm).\n"
                f"4. Attach the scored metrics to the experiment note.\n"
                f"5. Return ⟦RETURN⟧ then: "
                f"rv dag complete <run_id> {main_id}-score"
            ),
            "needs": [_afterok(f"{main_id}-run")],
            "reads": [_abs("experiments")],
        })

        # main analyze
        nodes.append({
            "id": f"{main_id}-analyze",
            "type": "agent",
            "label": f"Analyze Main {k} + write findings note (researcher)",
            "role": "researcher",
            "spec": (
                f"Analyze Main {k} results and write the findings note: {main_id}.\n\n"
                f"Your task:\n"
                f"1. Read experiments/{main_id}.md (scored metrics) and the plan's "
                f"Main {k} diagnosis table (experiments/{exp_id}-plan.md).\n"
                f"2. Apply the pre-registered decision threshold — follow the "
                f"diagnosis table rows exactly (no post-hoc reinterpretation).\n"
                f"3. Write the findings note at findings/{main_id}.md (type: findings). "
                f"Include: the named conclusion from the diagnosis table, the committed "
                f"action, effect size, and backed_by: [{main_id}].\n"
                f"4. Return ⟦RETURN⟧ then: "
                f"rv dag complete <run_id> {main_id}-analyze"
            ),
            "produces": {"note": f"findings/{main_id}.md"},
            "needs": [_afterok(f"{main_id}-score")],
            "reads": [_abs("experiments"), _abs("findings")],
        })

        # ablation A run
        nodes.append({
            "id": f"{abl_id}-run",
            "type": "agent",
            "label": f"Run ablation A of Main {k} — isolates one component (researcher)",
            "role": "researcher",
            "spec": (
                f"Run the pre-registered ablation A of Main {k}: {abl_id}.\n\n"
                f"Ablation purpose: isolate EXACTLY ONE component to rule out a confound "
                f"(as declared in the plan note, Supporting Ablation A section).\n\n"
                f"Your task:\n"
                f"1. Read the plan note experiments/{exp_id}-plan.md — specifically "
                f"the 'Main {k} — Supporting Ablation A' section.\n"
                f"2. Execute the ablation run — vary only the ONE declared component; "
                f"all other conditions identical to {main_id}.\n"
                f"3. Follow the compute recipe (see reads: "
                f"doctrine/compute-run-recipe.md).\n"
                f"4. Record run provenance in experiments/{abl_id}.md "
                f"(type: experiments, results_hash, run_id).\n"
                f"5. Return ⟦RETURN⟧ then: "
                f"rv dag complete <run_id> {abl_id}-run"
            ),
            "produces": {"note": f"experiments/{abl_id}.md"},
            "needs": [
                _afterok("human-go-plan"),
                {
                    "from": "plan",
                    "edge": "afterok",
                    "watch": f"note:experiments/{abl_id}.md+fresh",
                },
            ],
            "reads": [_abs("experiments"), "doctrine/compute-run-recipe.md#how to run here"],
        })

        # ablation A score
        nodes.append({
            "id": f"{abl_id}-score",
            "type": "agent",
            "label": f"Score ablation A of Main {k} (researcher)",
            "role": "researcher",
            "spec": (
                f"Score the completed ablation A of Main {k}: {abl_id}.\n\n"
                f"Your task:\n"
                f"1. Read experiments/{abl_id}.md — confirm provenance fields filled.\n"
                f"2. Run the pre-registered scoring procedure for this ablation "
                f"(same procedure as {main_id}-score; ablation shares the metric).\n"
                f"3. Verify results_hash matches the on-disk results file.\n"
                f"4. Attach scored metrics to the experiment note.\n"
                f"5. Return ⟦RETURN⟧ then: "
                f"rv dag complete <run_id> {abl_id}-score"
            ),
            "needs": [_afterok(f"{abl_id}-run")],
            "reads": [_abs("experiments")],
        })

        # ablation A analyze
        nodes.append({
            "id": f"{abl_id}-analyze",
            "type": "agent",
            "label": f"Analyze ablation A of Main {k} + write findings note (researcher)",
            "role": "researcher",
            "spec": (
                f"Analyze ablation A of Main {k} and write the findings note: {abl_id}.\n\n"
                f"Your task:\n"
                f"1. Read experiments/{abl_id}.md (scored) and the plan's "
                f"ablation diagnosis table (experiments/{exp_id}-plan.md).\n"
                f"2. Apply the pre-registered ablation diagnosis table rows: "
                f"'Effect maintained / Effect reduced / Effect eliminated'.\n"
                f"3. Write findings/{abl_id}.md (type: findings). Include: "
                f"named conclusion, committed action, component isolated, "
                f"backed_by: [{abl_id}].\n"
                f"4. Cross-reference against {main_id} findings — the ablation "
                f"should isolate exactly one factor; note if the effect "
                f"disappeared (component necessary) or persisted (not the cause).\n"
                f"5. Return ⟦RETURN⟧ then: "
                f"rv dag complete <run_id> {abl_id}-analyze"
            ),
            "produces": {"note": f"findings/{abl_id}.md"},
            "needs": [_afterok(f"{abl_id}-score")],
            "reads": [_abs("experiments"), _abs("findings")],
        })

        # human-go-conditionals per main
        nodes.append({
            "id": f"human-go-conditionals-main{k}",
            "type": "human-go",
            "label": (
                f"Human ratification gate — Main {k} results + "
                f"conditional triggers (decision-not-diff)"
            ),
            "needs": [
                _afterok(f"{main_id}-analyze"),
                _afterok(f"{abl_id}-analyze"),
            ],
        })

    # 5. human-go-findings — K-3 re-verify gate
    findings_needs: list[dict[str, Any]] = []
    for k in range(1, n_mains + 1):
        main_id = f"{exp_id}-main{k}"
        abl_id = f"{exp_id}-main{k}-abl-A"
        findings_needs.append(_afterok(f"human-go-conditionals-main{k}"))
        findings_needs.append(_afterok(f"{main_id}-analyze"))
        findings_needs.append(_afterok(f"{abl_id}-analyze"))

    nodes.append({
        "id": "human-go-findings",
        "type": "human-go",
        "label": (
            "Human review gate — all findings reviewed; "
            "covers:-hash re-verified (K-3, automatic on approval)"
        ),
        "needs": findings_needs,
    })

    # 6. methods-update — soft, non-blocking
    nodes.append({
        "id": "methods-update",
        "type": "agent",
        "label": "Update methods note if protocol changed (researcher — soft, non-blocking)",
        "role": "researcher",
        "spec": (
            f"Update the methods note for experiment {exp_id} if the protocol "
            f"deviated from the pre-registration plan.\n\n"
            f"This is a SOFT, NON-BLOCKING node — it fires after all findings are "
            f"reviewed (human-go-findings) but does not block any downstream work.\n\n"
            f"Your task:\n"
            f"1. Compare the approved plan (experiments/{exp_id}-plan.md) against "
            f"the actual run notes (experiments/{exp_id}-main*.md) to identify any "
            f"protocol deviations.\n"
            f"2. If there were NO deviations: write methods/method-{exp_id}.md "
            f"(type: methods) with a brief summary confirming adherence.\n"
            f"3. If there WERE deviations: document them explicitly — what changed, "
            f"why, and what downstream interpretation impact they carry. "
            f"Tag deviations as 'stance: exploratory' in the methods note.\n"
            f"4. Return ⟦RETURN⟧ then: "
            f"rv dag complete <run_id> methods-update"
        ),
        "produces": {"note": f"methods/method-{exp_id}.md"},
        "needs": [{"from": "human-go-findings", "edge": "soft"}],
        "reads": [_abs("experiments"), _abs("findings")],
    })

    return {
        "run_id": run_id,
        "name": f"Experiment loop — {exp_id} (pre-registration: {n_mains} mains)",
        "global_cap": 4,
        "nodes": nodes,
    }


# ---------------------------------------------------------------------------
# cmd_new
# ---------------------------------------------------------------------------

def cmd_new(
    project: str,
    exp_id: str,
    question: str,
    n_mains: int = 1,
    scope: list[str] | None = None,
    config: Any = None,
) -> tuple[Path, Path]:
    """Scaffold a pre-registration plan note + experiment DAG manifest.

    Returns
    -------
    (plan_note_path, manifest_path)
    """
    from .config import load_config as _load_config
    cfg = config or _load_config()

    # Resolve project notes dir
    try:
        notes_dir = cfg.project_notes_dir(project)
    except KeyError as e:
        raise ValueError(f"Unknown project {project!r}: {e}") from e

    # Ensure experiments/ dir exists
    experiments_dir = notes_dir / "experiments"
    experiments_dir.mkdir(parents=True, exist_ok=True)

    # Plan note path
    plan_note_path = experiments_dir / f"{exp_id}-plan.md"
    if plan_note_path.exists():
        raise FileExistsError(
            f"Plan note already exists: {plan_note_path}  "
            f"(use a different id or delete the existing note)"
        )

    # Manifest path
    manifest_path = experiments_dir / f"{exp_id}-loop.json"
    if manifest_path.exists():
        raise FileExistsError(
            f"Manifest already exists: {manifest_path}  "
            f"(use a different id or delete the existing manifest)"
        )

    # Write plan note skeleton
    plan_content = _plan_note_skeleton(
        project_id=project,
        exp_id=exp_id,
        question=question,
        n_mains=n_mains,
        scope=scope or [],
    )
    plan_note_path.write_text(plan_content, encoding="utf-8")

    # Build + validate manifest
    manifest = _build_experiment_manifest(
        project=project,
        exp_id=exp_id,
        question=question,
        n_mains=n_mains,
        plan_note_path=plan_note_path,
        notes_dir=notes_dir,
    )

    try:
        validate_manifest(manifest)
    except ManifestError as e:
        # Remove the plan note we just wrote (keep state consistent)
        plan_note_path.unlink(missing_ok=True)
        raise ManifestError(f"Scaffolded manifest is invalid: {e}") from e

    # Write manifest
    dump_manifest(manifest, manifest_path)

    return plan_note_path, manifest_path


# ---------------------------------------------------------------------------
# CLI builder
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``experiment`` verb.

    When to use: use ``rv experiment new <project> <id> --question '...'`` to scaffold
    a pre-registration plan note skeleton + a REGISTERED experiment DAG manifest so
    ``rv plan freeze`` has a run_id to hash (K-3 covers:-guarantee).

    Anti-pattern: do NOT run a pre-registered study as ad-hoc crew dispatches —
    ``rv experiment new`` registers the DAG so ``rv plan freeze`` has a run_id to
    hash; hand-dispatching silently loses the pre-registration guarantee.

    sr: SR-HUB-DAG
    """
    desc = (
        "Scaffold a pre-registered experiment loop (plan note + DAG manifest).\n"
        "'rv experiment new' is the ONLY path that registers the DAG so\n"
        "'rv plan freeze' has a run_id to hash (K-3 covers:-guarantee).\n"
        "Hand-dispatching crew without this step loses the pre-registration guarantee.\n"
        "Use 'rv dag run <manifest>' to start the loop after scaffolding."
    )
    if parent is not None:
        p = parent.add_parser(
            "experiment",
            help="Scaffold a pre-registered experiment loop (plan note + DAG manifest).",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv experiment", description=desc)

    p.add_argument("project", help="Project slug.")

    sub = p.add_subparsers(dest="experiment_cmd", required=True)

    # ── new ──────────────────────────────────────────────────────────────────
    new_p = sub.add_parser(
        "new",
        help=(
            "Create a pre-registration plan note skeleton + DAG manifest. "
            "Registers the DAG so `rv plan freeze` has a run_id to hash."
        ),
    )
    new_p.add_argument(
        "exp_id",
        metavar="id",
        help="Experiment identifier slug (e.g. 'q1' or 'xling-transfer'). No spaces.",
    )
    new_p.add_argument(
        "--question",
        required=True,
        metavar="QUESTION",
        help=(
            "One-sentence research question. Stored in the plan note and manifest. "
            "Example: 'Does prompt language drive cross-lingual accuracy in NLI?'"
        ),
    )
    new_p.add_argument(
        "--mains",
        type=int,
        default=1,
        metavar="N",
        help=(
            "Number of first-class main experiments (each gets a supporting ablation). "
            "Default: 1. Mirrors the research-loop.json topology for N mains."
        ),
    )
    new_p.add_argument(
        "--scope",
        nargs="*",
        default=[],
        metavar="OKF-ID",
        help=(
            "OKF note ids that scope this experiment "
            "(e.g. literature/smith2024 concepts/concept-A). "
            "Optional — stored in the plan note's scope: field."
        ),
    )

    return p


# ---------------------------------------------------------------------------
# run() dispatcher
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    """Dispatch experiment subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv experiment: config error: {e}", file=sys.stderr)
        return 1

    if args.experiment_cmd == "new":
        n_mains = getattr(args, "mains", 1)
        if n_mains < 1:
            print(
                f"rv experiment new: --mains must be >= 1, got {n_mains}",
                file=sys.stderr,
            )
            return 1
        if n_mains > 8:
            print(
                f"rv experiment new: --mains > 8 is not supported (got {n_mains}). "
                f"Start with <= 8 mains; add exploratory experiments after the freeze.",
                file=sys.stderr,
            )
            return 1

        try:
            plan_note_path, manifest_path = cmd_new(
                args.project,
                args.exp_id,
                question=args.question,
                n_mains=n_mains,
                scope=getattr(args, "scope", []) or [],
                config=cfg,
            )
        except (ValueError, FileExistsError, ManifestError, OSError) as e:
            print(f"rv experiment new: {e}", file=sys.stderr)
            return 1

        # Load the run_id from the manifest for the printed commands
        try:
            manifest_text = manifest_path.read_text(encoding="utf-8")
            import json as _json
            run_id = _json.loads(manifest_text).get("run_id", f"{args.exp_id}-loop")
        except Exception:
            run_id = f"{args.exp_id}-loop"

        print(f"rv experiment new: plan note  → {plan_note_path}")
        print(f"rv experiment new: manifest   → {manifest_path}")
        print(f"rv experiment new: run_id     = {run_id!r}")
        print()
        print("Next steps (in order — DO NOT skip the freeze):")
        print()
        print("  1. Fill in the plan note (claim arrows, decision thresholds, falsifiers,")
        print("     diagnosis tables for each main + ablation):")
        print(f"     Edit: {plan_note_path}")
        print()
        print("  2. Start the DAG run:")
        print(f"     rv dag run {manifest_path}")
        print()
        print("  3. After the plan and plan-critic nodes complete,")
        print("     FREEZE the covers:-hash at the human-go-plan gate:")
        print(f"     rv dag approve {run_id} human-go-plan")
        print(f"     rv plan freeze {run_id} {plan_note_path}")
        print()
        print("  (The K-3 covers:-hash is RE-VERIFIED automatically at")
        print(f"  human-go-findings — 'rv dag approve {run_id} human-go-findings'.)")
        return 0

    print(f"rv experiment: unknown subcommand {args.experiment_cmd!r}", file=sys.stderr)
    return 1
