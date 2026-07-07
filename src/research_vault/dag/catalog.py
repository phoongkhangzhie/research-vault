"""dag/catalog.py — static catalog SSOT for the two built-in research loops (SR-HUB-DAG).

Purpose
-------
A structured registry that makes every loop discoverable WITHOUT running anything.
Captures:
  - key            : stable slug
  - entry_verb     : the command to start a new DAG run (after the manifest exists)
  - scaffolder     : the command that EMITS the manifest + registers the run
                     (None when no scaffold verb exists)
  - human_go_gates : ordered list of human-go node IDs from the real scaffolders,
                     annotated with the freeze/verify action that each gate triggers
  - topology_summary : one-line description of the loop shape

★ GROUNDING — every gate node_id is taken from the REAL scaffolder emit code,
not from memory or design docs. Verified against:
  experiment : data/examples/demo-research/research-loop.json
               (human-go-plan, human-go-conditionals-main*, human-go-findings)
  lit-review : review/__init__.py _build_phase1_manifest + _build_phase2_manifest
               Phase-1 gates: approve-protocol, coverage-gate
               Phase-2 gate:  approve-review

A grounding test (test_sr_hub_dag_rails.py::TestCatalogGrounding) asserts every
human_go_gate.node_id appears as a real "human-go" typed node in the corresponding
shipped manifest or scaffolded manifest — this test is the canonical drift detector.

Stdlib only.
sr: SR-HUB-DAG
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Gate descriptor
# ---------------------------------------------------------------------------

class LoopGate:
    """One human-go gate in a loop manifest.

    Attributes
    ----------
    node_id : str
        The exact node id used in the manifest (grounded in the real scaffolders).
    label : str
        Short human-readable description of what is approved here.
    freeze_action : str | None
        If this gate triggers a freeze/verify action, the exact ``rv`` command
        pattern to run after approval (e.g. ``"rv plan freeze <run_id> <plan-note>"``).
        None when no freeze is associated.
    """

    __slots__ = ("node_id", "label", "freeze_action")

    def __init__(
        self,
        node_id: str,
        label: str,
        freeze_action: str | None = None,
    ) -> None:
        self.node_id = node_id
        self.label = label
        self.freeze_action = freeze_action

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "label": self.label,
            "freeze_action": self.freeze_action,
        }


# ---------------------------------------------------------------------------
# Loop descriptor
# ---------------------------------------------------------------------------

class LoopEntry:
    """One entry in the loop catalog.

    Attributes
    ----------
    key : str
        Stable slug (``"experiment"``, ``"lit-review"``).
    entry_verb : str
        The ``rv`` command to start a DAG run once the manifest exists.
    scaffolder : str | None
        The ``rv`` command that EMITS the manifest AND registers the run.
        None when the manifest is created manually (legacy / advanced path).
    human_go_gates : list[LoopGate]
        Ordered list of human-go gates from the REAL shipped manifest shape.
        Empty list = no human-go nodes (loop is fully automated).
    topology_summary : str
        One-line description of the overall loop structure.
    """

    __slots__ = (
        "key",
        "entry_verb",
        "scaffolder",
        "human_go_gates",
        "topology_summary",
    )

    def __init__(
        self,
        key: str,
        entry_verb: str,
        scaffolder: str | None,
        human_go_gates: list[LoopGate],
        topology_summary: str,
    ) -> None:
        self.key = key
        self.entry_verb = entry_verb
        self.scaffolder = scaffolder
        self.human_go_gates = human_go_gates
        self.topology_summary = topology_summary

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "entry_verb": self.entry_verb,
            "scaffolder": self.scaffolder,
            "human_go_gates": [g.as_dict() for g in self.human_go_gates],
            "topology_summary": self.topology_summary,
        }


# ---------------------------------------------------------------------------
# The catalog  (SR-HUB-DAG §A1 — grounded in real scaffolders, NOT design docs)
# ---------------------------------------------------------------------------
#
# Grounding sources (read these if you need to verify or update gates):
#   experiment : src/research_vault/data/examples/demo-research/research-loop.json
#   lit-review : src/research_vault/review/__init__.py
#                _build_phase1_manifest (approve-protocol, coverage-gate)
#                _build_phase2_manifest (approve-review)
#
# ★ Do NOT update gate IDs from memory or design docs — read the source files
# and update the grounding test (TestCatalogGrounding) in parallel.

LOOP_CATALOG: list[LoopEntry] = [

    LoopEntry(
        key="experiment",
        entry_verb="rv dag run <project-notes-dir>/experiments/<id>-loop.json",
        scaffolder="rv experiment <project> new <id> --question '...' [--mains N] [--shared-harness]",
        human_go_gates=[
            LoopGate(
                node_id="human-go-plan",
                label=(
                    "Plan quality approved, pre-registration filed, covers:-hash frozen "
                    "(K-3: run `rv plan freeze <run_id> <plan-note>` immediately after approval)"
                ),
                freeze_action="rv plan freeze <run_id> <plan-note>",
            ),
            # SR-HARNESS-P2: per-main harness gate between plan and run
            LoopGate(
                node_id="human-go-harness-main1",
                label=(
                    "Main 1 eval harness reviewed and approved "
                    "(run `rv plan freeze-harness <run_id> <plan-note> "
                    "--scope main1 --harness-commit <sha>` after approval)"
                ),
                freeze_action=(
                    "rv plan freeze-harness <run_id> <plan-note> "
                    "--scope main1 --harness-commit <sha>"
                ),
            ),
            LoopGate(
                node_id="human-go-conditionals-main1",
                label="Main 1 results + conditional triggers ratified (decision-not-diff)",
                freeze_action=None,
            ),
            LoopGate(
                node_id="human-go-findings",
                label=(
                    "All findings reviewed; covers:-hash re-verified (K-3: automatic on "
                    "`rv dag approve <run_id> human-go-findings`)"
                ),
                freeze_action=None,
            ),
        ],
        topology_summary=(
            "plan → plan-critic → [HG:human-go-plan] → "
            "{per-main: harness→harness-review→[HG:human-go-harness-main<k>] → "
            "run→score→analyze (+ablation-run→score→analyze)} → "
            "[HG:human-go-conditionals-main*] → [HG:human-go-findings] → methods-update"
        ),
    ),

    # Lit-review gates grounded in review/__init__.py:
    #   Phase-1: approve-protocol (line ~152), coverage-gate (line ~199)
    #   Phase-2: approve-review (line ~356)
    LoopEntry(
        key="lit-review",
        entry_verb="rv dag run <project-notes-dir>/reviews/<scope>/phase1-dag.json",
        scaffolder="rv review <project> new <scope> --question '...'",
        human_go_gates=[
            LoopGate(
                node_id="approve-protocol",
                label=(
                    "Review protocol approved (counter-position required before search fires — "
                    "L-2 anti-fishing gate)"
                ),
                freeze_action=None,
            ),
            LoopGate(
                node_id="coverage-gate",
                label=(
                    "OKF coverage gate — every in-scope paper has a relate slot or is "
                    "MENTION-ONLY; Phase-2 fan-out authorized here "
                    "(run `rv review <project> expand <scope>` after approval)"
                ),
                freeze_action=None,
            ),
            LoopGate(
                node_id="approve-review",
                label="Gate 3: Approve review — [BLOCK] count + counter-position verdict",
                freeze_action=None,
            ),
        ],
        topology_summary=(
            "review-scope → [HG:approve-protocol] → review-search → review-snowball → "
            "[HG:coverage-gate] → (Phase-2) relate-* → review-synthesize → "
            "review-coverage-critic → [HG:approve-review]"
        ),
    ),

    # PR-M1: the type-generic manuscript loop, re-instantiated with a type system.
    # Gate grounded in manuscript/__init__.py _build_phase2_manifest (approve-manuscript,
    # the terminal node emitted for every registered type — see TestCatalogGrounding).
    # A type's own Phase-1 is type-optional (``phase1_builder=None`` = pass-through,
    # e.g. a future ``experiment-paper``). PR-M6 fills the FIRST-SHIPPED type's real
    # Phase-1 (lit-review's framework-selection sub-loop, design §5): its
    # ``approve-framework`` gate is grounded here too, per design §2 ("Catalog: add a
    # manuscript LoopEntry whose human_go_gates reflect the first-shipped type's
    # manifest (lit-review: approve-framework, approve-manuscript)").
    LoopEntry(
        key="manuscript",
        entry_verb="rv dag run <project-notes-dir>/manuscripts/<slug>/phase2-dag.json",
        scaffolder="rv manuscript <project> new <slug> --type <type>",
        human_go_gates=[
            LoopGate(
                node_id="approve-framework",
                label=(
                    "lit-review Phase-1 gate: approve the organizing framework "
                    "(spine_shape + branches frozen into _manuscript.md — design §5, "
                    "D5; type-specific, only for types with a framework Phase-1)"
                ),
                freeze_action=None,
            ),
            LoopGate(
                node_id="approve-manuscript",
                label=(
                    "Approve manuscript draft (structural/fidelity gates PR-M2/M3, "
                    "equation gate PR-M4, and the review-revise board PR-M5 plug in "
                    "ahead of this gate as they land)"
                ),
                freeze_action=None,
            ),
        ],
        topology_summary=(
            "new --type <type> → (type Phase-1: lit-review = scope → "
            "framework-propose → [HG:approve-framework], design §5) → "
            "expand → section(s) (type-generic, from ManuscriptType.section_set) → "
            "assemble → [HG:approve-manuscript]"
        ),
    ),

]


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def get_loop(key: str) -> LoopEntry | None:
    """Return the LoopEntry for ``key``, or None if not found."""
    for entry in LOOP_CATALOG:
        if entry.key == key:
            return entry
    return None


def all_keys() -> list[str]:
    """Return all loop keys in catalog order."""
    return [e.key for e in LOOP_CATALOG]
