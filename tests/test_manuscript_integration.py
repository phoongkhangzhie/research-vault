"""test_manuscript_integration.py — the manuscript-wave INTEGRATION PR.

Real end-to-end proof that the gates M2/M3/M4/M6 built in parallel are
ACTUALLY assembled and wired, not just individually unit-tested:

  cmd_new --type lit-review -> (freeze the spine, simulating approve-framework)
    -> cmd_expand -> build_approve_payload

Coverage (the four scenarios named in the integration brief):
  (a) a dangling ``\\cite{}`` -> the hermetic-.bib gate BLOCKs.
  (b) a dropped MARKED-CRITICAL equation -> the equation gate SIGNALs,
      never blocks (D-MS-2).
  (c) M6's ``source_transform`` output (the comparison table + the frozen
      framework branches) ACTUALLY appears in the Phase-2 manifest's
      ``prisma-scope``/``references``/``framework``/``thematic-sections``
      section specs — proving PR-M4's dead-code wiring is now live.
  (d) with NO judge configured (no ``RV_JUDGE_MODEL``/``ANTHROPIC_API_KEY``,
      no ``judge_fn``), the LLM gates (support-matcher, cold-read) land in
      ``not_run`` and are surfaced LOUDLY — never silently skipped, never a
      green pass on an unchecked citation-fidelity floor.

Also covers the REAL ``rv dag approve`` wiring at ``approve-manuscript``
(mirrors test_manuscript_m6_lit_review.py's ``TestApproveFrameworkGateWiring``
pattern for ``approve-framework``) — not just calling
``build_approve_payload`` directly.

sr: manuscript-integration
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def cfg(tmp_instance):
    from research_vault.config import load_config
    return load_config(reload=True)


def _write_literature_note_with_equation(project_notes_dir: Path) -> None:
    """A literature/ note carrying a marked-critical equation (PR-L1 shape,
    consumed by manuscript/equations.py) + a citekey for the comparison
    table (M6's source_transform)."""
    lit_dir = project_notes_dir / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)
    (lit_dir / "kingma2013.md").write_text(
        "---\n"
        "type: literature\n"
        "title: Auto-Encoding Variational Bayes\n"
        "citekey: kingma2013\n"
        "year: 2013\n"
        "venue: ICLR\n"
        "repo: https://github.com/example/vae\n"
        "key_equations:\n"
        "  - label: eq:elbo\n"
        "    critical: true\n"
        "---\n"
        "## Key equations\n\n"
        "### [eq:elbo] Evidence lower bound *(critical)*\n"
        "$$ \\log p(x) \\ge \\mathbb{E}_{q}[\\log p(x,z) - \\log q(z)] $$\n",
        encoding="utf-8",
    )


def _freeze_spine(note_path: Path, *, spine_shape: str, branches: list[str]) -> None:
    """Simulate ``approve-framework`` having frozen the spine — writes
    ``spine_shape``/``branches`` directly into ``_manuscript.md`` frontmatter
    (the structural gate itself, ``check_framework_gate``, is unit-tested in
    test_manuscript_m6_lit_review.py; this test exercises what happens
    AFTER a real freeze)."""
    text = note_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    end = text.index("\n---\n", 4) + 5
    frontmatter, body = text[:end], text[end:]
    lines = frontmatter.split("\n")
    new_lines = []
    skip = False
    for line in lines:
        if line.startswith("spine_shape:") or line.startswith("branches:"):
            skip = True
            continue
        if skip and line.startswith("  - "):
            continue
        skip = False
        new_lines.append(line)
    # Insert the frozen fields before the closing "---" delimiter line
    # (find its index explicitly — the split can leave a trailing "" after
    # the delimiter when the frontmatter block ends in a newline).
    insert_at = max(i for i, ln in enumerate(new_lines) if ln == "---")
    branch_lines = [f"spine_shape: {spine_shape}", "branches:"]
    branch_lines.extend(f"  - {b}" for b in branches)
    new_lines[insert_at:insert_at] = branch_lines
    note_path.write_text("\n".join(new_lines) + body, encoding="utf-8")


def test_e2e_dangling_cite_blocks_dropped_equation_signals_transform_wired(cfg):
    from research_vault.manuscript import cmd_new, cmd_expand
    from research_vault.manuscript.check_gates import build_approve_payload
    from research_vault.manuscript.types import get_type

    project = "demo-research"
    slug = "survey-integration"
    project_notes_dir = cfg.project_notes_dir(project)

    _write_literature_note_with_equation(project_notes_dir)

    note_path, tree_root, _ = cmd_new(project, slug, ms_type_key="lit-review", config=cfg)
    _freeze_spine(note_path, spine_shape="pipeline", branches=["representation-learning"])

    manifest = cmd_expand(project, slug, config=cfg)

    # ── (c) source_transform is WIRED, not dead code ────────────────────────
    node_specs = {n["id"]: n.get("spec", "") for n in manifest["nodes"]}
    assert "kingma2013" in node_specs["references"], (
        "M6's comparison-table rows (source_transform) never reached the "
        "'references' section spec — source_transform is still dead code."
    )
    assert "representation-learning" in node_specs["framework"], (
        "The frozen framework branches never reached the 'framework' section spec."
    )
    assert "representation-learning" in node_specs["thematic-sections"], (
        "The frozen framework branches never reached the 'thematic-sections' spec "
        "— the writer wouldn't know how many branches to draft."
    )
    # The PRISMA ledger renders (honestly, no frozen corpus in this test) —
    # proves the renderer's output flows into the spec at all.
    assert "PRISMA scope & method" in node_specs["prisma-scope"]

    # ── Simulate a drafted manuscript: a dangling \cite AND a dropped
    #    marked-critical equation (never reproduced). ───────────────────────
    (tree_root / "sections").mkdir(parents=True, exist_ok=True)
    (tree_root / "sections" / "thematic-sections.tex").write_text(
        "Prior work \\cite{missingpaper2024} explored related representations, "
        "but never formalized the ELBO the way kingma2013 did.\n",
        encoding="utf-8",
    )

    ms_type = get_type("lit-review")

    # ── (d) no judge configured — the LLM gates must NOT silently run. ──────
    old_judge = os.environ.pop("RV_JUDGE_MODEL", None)
    old_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        payload = build_approve_payload(tree_root, project_notes_dir, ms_type)
    finally:
        if old_judge is not None:
            os.environ["RV_JUDGE_MODEL"] = old_judge
        if old_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = old_key

    # (a) the dangling \cite{missingpaper2024} -> hard BLOCK.
    assert payload["ok"] is False
    assert any("missingpaper2024" in b for b in payload["blocking"]), payload["blocking"]
    assert any("hermetic-bib" in b for b in payload["blocking"])

    # (b) the dropped marked-critical eq:elbo -> SIGNAL, never BLOCK.
    assert any("eq:elbo" in s for s in payload["signals"]), payload["signals"]
    assert any("equation-fidelity" in s and "critical" in s for s in payload["signals"])
    assert not any("eq:elbo" in b for b in payload["blocking"])

    # (d) LLM gates land in not_run, surfaced loudly — never silently skipped,
    # never treated as a pass.
    assert any("support-matcher" in n and "cold-read" in n for n in payload["not_run"]), (
        payload["not_run"]
    )
    assert not any("support-matcher" in b for b in payload["blocking"])
    assert not any("cold-read" in s for s in payload["signals"])


def test_no_dangling_cite_and_equation_present_only_signals_or_clean(cfg):
    """Sanity control: a clean draft (no dangling cite, equation reproduced)
    -> hermetic-bib passes (no BLOCK from it) and the equation gate has no
    finding for eq:elbo. Proves the BLOCK/SIGNAL above is a real distinction,
    not an artifact of every draft always failing."""
    from research_vault.manuscript import cmd_new, cmd_expand
    from research_vault.manuscript.check_gates import build_approve_payload
    from research_vault.manuscript.types import get_type

    project = "demo-research"
    slug = "survey-clean"
    project_notes_dir = cfg.project_notes_dir(project)

    _write_literature_note_with_equation(project_notes_dir)

    note_path, tree_root, _ = cmd_new(project, slug, ms_type_key="lit-review", config=cfg)
    _freeze_spine(note_path, spine_shape="pipeline", branches=["representation-learning"])
    cmd_expand(project, slug, config=cfg)

    (tree_root / "sections").mkdir(parents=True, exist_ok=True)
    (tree_root / "sections" / "thematic-sections.tex").write_text(
        "As kingma2013 \\cite{kingma2013} showed, "
        "$$ \\log p(x) \\ge \\mathbb{E}_{q}[\\log p(x,z) - \\log q(z)] $$ "
        "is the evidence lower bound.\n",
        encoding="utf-8",
    )

    ms_type = get_type("lit-review")
    old_judge = os.environ.pop("RV_JUDGE_MODEL", None)
    old_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        payload = build_approve_payload(tree_root, project_notes_dir, ms_type)
    finally:
        if old_judge is not None:
            os.environ["RV_JUDGE_MODEL"] = old_judge
        if old_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = old_key

    assert not any("hermetic-bib" in b for b in payload["blocking"]), payload["blocking"]
    assert not any("eq:elbo" in s for s in payload["signals"]), payload["signals"]


# ---------------------------------------------------------------------------
# Real `rv dag approve` wiring at approve-manuscript (mirrors M6's
# TestApproveFrameworkGateWiring pattern for approve-framework).
# ---------------------------------------------------------------------------

def _approve_env_cfg_file(tmp_path: Path) -> Path:
    f = tmp_path / "research_vault.toml"
    f.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{tmp_path / "notes"}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
        '[approval]\nenforce = false\n',
        encoding="utf-8",
    )
    return f


def _set_run_env(tmp_path: Path):
    cfg_file = _approve_env_cfg_file(tmp_path)
    old = os.environ.get("RESEARCH_VAULT_CONFIG")
    os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)
    return old


def _restore_env(old):
    if old is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old


def _phase2_manifest(run_id: str) -> dict:
    return {
        "run_id": run_id,
        "name": "test approve-manuscript wiring",
        "global_cap": 1,
        "nodes": [
            {"id": "assemble", "type": "agent", "spec": "task://demo#assemble", "needs": []},
            {
                "id": "approve-manuscript", "type": "human-go", "label": "Gate",
                "needs": [{"from": "assemble", "edge": "afterok"}],
            },
        ],
    }


def _make_awaiting_run(tmp_path: Path, run_id: str, manifest_dir: Path):
    from research_vault.dag.store import RunState, RunStore

    manifest = _phase2_manifest(run_id)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "phase2-dag.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    store = RunStore(tmp_path / "state")
    rs = RunState(run_id=run_id, manifest_path=str(manifest_path))
    rs.init_nodes(manifest)
    rs.set_node_status("assemble", "succeeded")
    rs.set_node_status("approve-manuscript", "awaiting-go")
    store.create(rs)
    return store


class TestApproveManuscriptGateWiring:
    def test_dangling_cite_blocks_real_approve_no_state_mutation(self, tmp_path):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            project_notes_dir = tmp_path / "notes" / "projects" / "demo-research"
            _write_literature_note_with_equation(project_notes_dir)

            manifest_dir = project_notes_dir / "manuscripts" / "survey-wiring-block"
            _manuscript_note_for_wiring(
                manifest_dir / "_manuscript.md",
                spine_shape="pipeline", branches=["representation-learning"],
            )
            (manifest_dir / "sections").mkdir(parents=True, exist_ok=True)
            (manifest_dir / "sections" / "thematic-sections.tex").write_text(
                "Prior work \\cite{missingpaper2024} explored this.\n", encoding="utf-8",
            )
            store = _make_awaiting_run(tmp_path, "ms-wiring-block", manifest_dir)

            args = argparse.Namespace(run_id="ms-wiring-block", node_id="approve-manuscript")
            rc = cmd_approve(args)

            assert rc != 0
            rs = store.load("ms-wiring-block")
            assert rs.node_status("approve-manuscript") == "awaiting-go"
        finally:
            _restore_env(old)

    def test_clean_draft_approves_cleanly_through_real_wiring(self, tmp_path):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            project_notes_dir = tmp_path / "notes" / "projects" / "demo-research"
            _write_literature_note_with_equation(project_notes_dir)

            manifest_dir = project_notes_dir / "manuscripts" / "survey-wiring-clean"
            _manuscript_note_for_wiring(
                manifest_dir / "_manuscript.md",
                spine_shape="pipeline", branches=["representation-learning"],
            )
            (manifest_dir / "sections").mkdir(parents=True, exist_ok=True)
            (manifest_dir / "sections" / "thematic-sections.tex").write_text(
                "As kingma2013 \\cite{kingma2013} showed, "
                "$$ \\log p(x) \\ge \\mathbb{E}_{q}[\\log p(x,z) - \\log q(z)] $$ "
                "is the evidence lower bound.\n",
                encoding="utf-8",
            )
            store = _make_awaiting_run(tmp_path, "ms-wiring-clean", manifest_dir)

            old_judge = os.environ.pop("RV_JUDGE_MODEL", None)
            old_key = os.environ.pop("ANTHROPIC_API_KEY", None)
            try:
                args = argparse.Namespace(run_id="ms-wiring-clean", node_id="approve-manuscript")
                rc = cmd_approve(args)
            finally:
                if old_judge is not None:
                    os.environ["RV_JUDGE_MODEL"] = old_judge
                if old_key is not None:
                    os.environ["ANTHROPIC_API_KEY"] = old_key

            assert rc == 0
            rs = store.load("ms-wiring-clean")
            assert rs.node_status("approve-manuscript") == "succeeded"
        finally:
            _restore_env(old)

    def test_reject_bypasses_the_gate(self, tmp_path):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            project_notes_dir = tmp_path / "notes" / "projects" / "demo-research"
            manifest_dir = project_notes_dir / "manuscripts" / "survey-wiring-reject"
            _manuscript_note_for_wiring(manifest_dir / "_manuscript.md", spine_shape="", branches=[])
            (manifest_dir / "sections").mkdir(parents=True, exist_ok=True)
            (manifest_dir / "sections" / "thematic-sections.tex").write_text(
                "\\cite{missingpaper2024}\n", encoding="utf-8",
            )
            store = _make_awaiting_run(tmp_path, "ms-wiring-reject", manifest_dir)

            args = argparse.Namespace(
                run_id="ms-wiring-reject", node_id="approve-manuscript", reject=True,
            )
            rc = cmd_approve(args)

            assert rc == 0
            rs = store.load("ms-wiring-reject")
            assert rs.node_status("approve-manuscript") == "blocked"
        finally:
            _restore_env(old)


def _manuscript_note_for_wiring(path: Path, *, spine_shape: str, branches: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_lines = ["type: manuscript", "manuscript_type: lit-review", f"spine_shape: {spine_shape}"]
    if branches:
        fm_lines.append("branches:")
        for b in branches:
            fm_lines.append(f"  - {b}")
    else:
        fm_lines.append("branches: ")
    fm = "\n".join(fm_lines) + "\n"
    path.write_text(f"---\n{fm}---\n\n## Scope\n", encoding="utf-8")
    return path
