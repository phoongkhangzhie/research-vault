"""test_relate_presence_gate.py — Wave 0 (Reading) PR-1: the cmd_complete ride.

Covers the DAG complete-gate wiring for the relate-<key> presence check
(dag/verbs.py::_check_relate_presence): a succeeded relate-<key> node whose
produced literature note is missing a mandatory checklist answer BLOCKS at
`rv dag complete`, mirroring the existing OKF-type / provenance-chain gates'
structural posture (§ PR-CC-1 pattern reused, zero new mechanism).

the overlay unwind (0.3.2): literature is shared-canonical — a
relate-<key> node's `produces: {"note": "literature/<key>.md"}` resolves
against `cfg.literature_root` directly (no per-project overlay dir, no
`central:` merge — see dag/verbs.py's shared-type produces routing).

sr: NG-lit-review-wave0 (PR-1)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from research_vault.config import load_config


def _write_note(path: Path, fields: dict[str, str], body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append(body)
    path.write_text("\n".join(lines), encoding="utf-8")


_COMPLETE_FIELDS = {
    "type": "literature",
    "citekey": "xiong2023-stepwise",
    "title": "Test Paper",
    "contribution_kind": "theory-bound",
    "role": "theoretical",
    "position": "This is the counter-position to the safe-exploration rebuttals.",
    "result_reported": "no",
    "paper_relations_sought": "no",
}


class TestRelatePresenceGateRide:
    def _run_dag(self, manifest_path: Path) -> None:
        from research_vault.dag.verbs import cmd_run
        args = argparse.Namespace(manifest=str(manifest_path))
        rc = cmd_run(args)
        assert rc == 0, f"cmd_run failed: rc={rc}"

    def _agent_node(self, nid, produces):
        return {"id": nid, "type": "agent", "spec": "task://test#stub", "produces": produces}

    def _argns(self, **kwargs):
        ns = argparse.Namespace()
        defaults = {"status": "succeeded", "manifest": None, "run_id": None, "node_id": None}
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(ns, k, v)
        return ns

    def test_incomplete_relate_note_blocks_complete_gate(self, tmp_instance):
        from research_vault.dag.verbs import cmd_complete
        cfg = load_config(reload=True)
        run_id = "test-relate-incomplete"
        m = {
            "run_id": run_id,
            "project": "demo-litreview",
            "nodes": [self._agent_node(
                "relate-xiong2023-stepwise", {"note": "literature/xiong2023-stepwise.md"}
            )],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        self._run_dag(mf)

        note = cfg.literature_root / "xiong2023-stepwise.md"
        # missing role/position/result_reported/paper_relations_sought/contribution_kind
        _write_note(note, {"type": "literature", "citekey": "xiong2023-stepwise", "title": "T"})

        rc = cmd_complete(self._argns(
            run_id=run_id, node_id="relate-xiong2023-stepwise", status="succeeded"
        ))
        assert rc == 1

    def test_complete_relate_note_passes_complete_gate(self, tmp_instance):
        from research_vault.dag.verbs import cmd_complete
        cfg = load_config(reload=True)
        run_id = "test-relate-complete"
        m = {
            "run_id": run_id,
            "project": "demo-litreview",
            "nodes": [self._agent_node(
                "relate-xiong2023-stepwise", {"note": "literature/xiong2023-stepwise.md"}
            )],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        self._run_dag(mf)

        note = cfg.literature_root / "xiong2023-stepwise.md"
        _write_note(note, _COMPLETE_FIELDS)

        rc = cmd_complete(self._argns(
            run_id=run_id, node_id="relate-xiong2023-stepwise", status="succeeded"
        ))
        assert rc == 0

    def test_shared_canonical_note_reads_its_own_fields_directly(self, tmp_instance):
        """the overlay unwind (0.3.2): a single shared note carries
        EVERY checklist field (intrinsic + role/position) directly — no
        merge, no `central:` indirection. This is the direct successor of
        the pre-unwind 'two-layer merge' regression pin: the gate must read
        the ONE note's own fields correctly."""
        from research_vault.dag.verbs import cmd_complete
        cfg = load_config(reload=True)
        run_id = "test-relate-shared"
        m = {
            "run_id": run_id,
            "project": "demo-litreview",
            "nodes": [self._agent_node(
                "relate-xiong2023-stepwise", {"note": "literature/xiong2023-stepwise.md"}
            )],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        self._run_dag(mf)

        note_path = cfg.literature_root / "xiong2023-stepwise.md"
        _write_note(note_path, {
            "type": "literature",
            "citekey": "xiong2023-stepwise",
            "title": "Test Paper",
            "contribution_kind": "theory-bound",
            "result_reported": "no",
            "paper_relations_sought": "no",
            "role": "theoretical",
            "position": "This is the counter-position to the safe-exploration rebuttals.",
        })

        rc = cmd_complete(self._argns(
            run_id=run_id, node_id="relate-xiong2023-stepwise", status="succeeded"
        ))
        assert rc == 0

    def test_missing_intrinsic_field_still_blocks(self, tmp_instance):
        """A real gap (contribution_kind absent) still BLOCKs even when
        role/position are fully answered — the overlay unwind's single note has
        nothing to hide behind."""
        from research_vault.dag.verbs import cmd_complete
        cfg = load_config(reload=True)
        run_id = "test-relate-missing-intrinsic"
        m = {
            "run_id": run_id,
            "project": "demo-litreview",
            "nodes": [self._agent_node(
                "relate-xiong2023-stepwise", {"note": "literature/xiong2023-stepwise.md"}
            )],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        self._run_dag(mf)

        note_path = cfg.literature_root / "xiong2023-stepwise.md"
        _write_note(note_path, {
            "type": "literature", "citekey": "xiong2023-stepwise", "title": "T",
            # contribution_kind/result_reported/paper_relations_sought MISSING
            "role": "theoretical", "position": "A real position statement here.",
        })

        rc = cmd_complete(self._argns(
            run_id=run_id, node_id="relate-xiong2023-stepwise", status="succeeded"
        ))
        assert rc == 1

    def test_non_relate_node_unaffected(self, tmp_instance):
        """A non-relate- agent node producing a literature note (e.g. a
        hand-authored note-new node) is NOT gated by the relate presence
        check — the gate only fires for the relate-<key> fan-out nodes."""
        from research_vault.dag.verbs import cmd_complete
        cfg = load_config(reload=True)
        run_id = "test-non-relate"
        m = {
            "run_id": run_id,
            "project": "demo-litreview",
            "nodes": [self._agent_node(
                "file-a-note", {"note": "literature/hand-authored.md"}
            )],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        self._run_dag(mf)

        note = cfg.literature_root / "hand-authored.md"
        # deliberately incomplete by relate-check standards
        _write_note(note, {"type": "literature", "citekey": "hand-authored", "title": "T"})

        rc = cmd_complete(self._argns(
            run_id=run_id, node_id="file-a-note", status="succeeded"
        ))
        assert rc == 0
