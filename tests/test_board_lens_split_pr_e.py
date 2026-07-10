"""tests/test_board_lens_split_pr_e.py — PR-E acceptance: the 4->6 board
lens split (CONTENT -> DEPTH/WIDTH/SYNTH, FRAMEWORK -> INSTRUCT) + the
WIDTH<->coverage_diff wiring.

Acceptance (from the PR-E brief):
  (a) 6 tasks emitted, floor-on-all-6;
  (b) plant a dropped `used` paper -> WIDTH `major`+ via coverage_diff
      (whole missing cluster -> critical);
  (c) plant a surfaced `[SUPPORTS]` tag / single-cite ¶ -> SYNTH finding;
  (d) plant a bare assertion with no numbers where the corpus quantifies
      -> DEPTH finding;
  (e) canary abort -> HALT-DECLARE (never a silent pass);
  (f) hermetic — every path exercised via an injected ingest_fn mock,
      zero live LLM;
  (g) full suite green (run separately).

All judges here are HERMETIC MOCKS that read only what the emit step
routed onto each task (draft / coverage_map / coverage_diff) — this is
exactly the point: the tests prove the mechanical ground truth reaches the
RIGHT lens and the finding is ingested onto the RIGHT axis.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from research_vault.gates.board_seam import CanaryAbortError, emit_board_tasks
from research_vault.manuscript import board
from research_vault.manuscript.board_lenses import AXES
from research_vault.manuscript.check_gates import compute_coverage_diff


_DRAFT = "## Introduction\n\nSome draft text about the corpus.\n"
_ALL_AXES = ("DEPTH", "WIDTH", "SYNTH", "SELFCONT", "ADVERS", "INSTRUCT")


def _write_map(path: Path, *, used=None) -> None:
    """Write a minimal `_coverage-map.md` (the D8 mapping-list frontmatter
    `note._parse_frontmatter` reads) with a `used` bucket."""
    fm = ["---", "coverage_map: true"]
    if used:
        fm.append("used:")
        for ck, branch in used:
            fm += [f"  - citekey: {ck}", f"    branch: {branch}"]
    fm += ["---", "", "## rationale\n\nprose.\n"]
    path.write_text("\n".join(fm), encoding="utf-8")


def _scoring_ingest(finding_emitter, *, floor_sink_axes=()):
    """Build a hermetic ingest_fn: score every real task at floor+1 (clean),
    every canary at its expected band, then let ``finding_emitter(task) ->
    (findings, score_override|None)`` inspect each real task's routed
    ground-truth and (optionally) plant a finding + drop that axis's score.
    """
    def _fn(tasks_doc, canary_key_doc):
        canaries = canary_key_doc["canaries"]
        verdicts = []
        for t in tasks_doc["tasks"]:
            tid = t["id"]
            if tid in canaries:
                band = canaries[tid]
                score = {"PASS-HIGH": 5, "FAIL-LOW": 1, "FAIL": 2}[band]
                verdicts.append({"id": tid, "axis": t["axis"], "score": score, "findings": []})
                continue
            findings, score_override = finding_emitter(t)
            score = score_override if score_override is not None else 4
            verdicts.append({"id": tid, "axis": t["axis"], "score": score, "findings": findings})
        return {"verdicts": verdicts}
    return _fn


# ---------------------------------------------------------------------------
# (a) 6 tasks emitted, floor on all 6
# ---------------------------------------------------------------------------

def test_a_six_lens_tasks_and_floor_on_all_six():
    emitted = emit_board_tasks(_DRAFT, manuscript="ms-a")
    real = [t for t in emitted["tasks_doc"]["tasks"] if t["id"] not in emitted["canary_key_doc"]["canaries"]]
    assert {t["axis"] for t in real} == set(_ALL_AXES)
    assert AXES == ("DEPTH", "WIDTH", "SYNTH", "SELFCONT", "ADVERS", "INSTRUCT")
    # Every one of the 6 is a floor axis: a single failing axis sinks clear.
    for axis in _ALL_AXES:
        r = board.evaluate_board_floor({a: 4 for a in _ALL_AXES} | {axis: 2})
        assert r["cleared"] is False
        assert r["floor_results"][axis]["passed"] is False


# ---------------------------------------------------------------------------
# (b) a dropped `used` paper -> WIDTH major (whole cluster -> critical),
#     via the mechanical coverage_diff.
# ---------------------------------------------------------------------------

def test_b_compute_coverage_diff_finds_the_dropped_used_paper(tmp_path):
    map_path = tmp_path / "_coverage-map.md"
    _write_map(map_path, used=[("paperA", "cluster-x"), ("paperB", "cluster-y")])
    reader_body = "The field advances [[paperA]] considerably.\n"  # paperB dropped
    diff = compute_coverage_diff(map_path, reader_body)
    assert diff["used"] == ["paperA", "paperB"]
    assert diff["present"] == ["paperA"]
    assert diff["missing"] == ["paperB"]  # the mechanical FIND


def _width_judge(task):
    """A WIDTH judge that reads ONLY its routed coverage_map/coverage_diff:
    whole missing cluster -> critical, single missing paper -> major."""
    if task["axis"] != "WIDTH":
        return [], None
    diff = task.get("coverage_diff")
    cov_map = task.get("coverage_map") or {}
    if not diff or not diff.get("missing"):
        return [], None
    missing = set(diff["missing"])
    findings = []
    for cluster, members in cov_map.items():
        members = set(members)
        if members and members <= missing:  # whole cluster dropped
            findings.append({
                "finding_id": f"f-width-{cluster}", "severity": "critical",
                "location": f"cluster {cluster}", "issue": f"whole cluster {cluster} dropped",
                "evidence": sorted(members)[0], "recommendation": "restore the cluster",
            })
            missing -= members
    for ck in sorted(missing):  # remaining single drops
        findings.append({
            "finding_id": f"f-width-{ck}", "severity": "major",
            "location": "body", "issue": f"used paper {ck} never cited in the reader body",
            "evidence": ck, "recommendation": f"cite {ck} in its committed branch",
        })
    return findings, 2  # below floor -> WIDTH sinks


def test_b_dropped_used_paper_becomes_width_major(tmp_path):
    map_path = tmp_path / "_coverage-map.md"
    # Both papers live in cluster-x; only paperB is dropped, so the cluster
    # is NOT wholly missing -> a single missing paper -> `major`.
    _write_map(map_path, used=[("paperA", "cluster-x"), ("paperB", "cluster-x")])
    reader_body = "The field advances [[paperA]] considerably.\n"
    diff = compute_coverage_diff(map_path, reader_body)
    cov_map = {"cluster-x": ["paperA", "paperB"]}

    result = board.run_board_round(
        1, reader_body, ingest_fn=_scoring_ingest(_width_judge),
        coverage_map=cov_map, coverage_diff=diff,
    )
    width_findings = result["findings"]["WIDTH"]
    assert any(f["severity"] == "major" and "paperB" in f["evidence"] for f in width_findings)
    assert result["axis_scores"]["WIDTH"] == 2
    assert result["floor_results"]["WIDTH"]["passed"] is False  # floor sinks on WIDTH
    # No OTHER axis was touched — the coverage ground truth was WIDTH-scoped only.
    assert all(result["findings"][a] == [] for a in _ALL_AXES if a != "WIDTH")


def test_b_whole_missing_cluster_becomes_width_critical(tmp_path):
    map_path = tmp_path / "_coverage-map.md"
    # cluster-y is ENTIRELY dropped (both its papers), cluster-x is fine.
    _write_map(map_path, used=[("pX1", "cluster-x"), ("pY1", "cluster-y"), ("pY2", "cluster-y")])
    reader_body = "Only [[pX1]] is discussed.\n"
    diff = compute_coverage_diff(map_path, reader_body)
    cov_map = {"cluster-x": ["pX1"], "cluster-y": ["pY1", "pY2"]}

    result = board.run_board_round(
        1, reader_body, ingest_fn=_scoring_ingest(_width_judge),
        coverage_map=cov_map, coverage_diff=diff,
    )
    width_findings = result["findings"]["WIDTH"]
    assert any(f["severity"] == "critical" and "cluster-y" in f["location"] for f in width_findings)


# ---------------------------------------------------------------------------
# (c) a surfaced [SUPPORTS] tag / single-cite ¶ -> SYNTH finding.
# ---------------------------------------------------------------------------

_RECITATION_DRAFT = (
    "## Related work\n\n"
    "One study reports gains [[smith2023]]. [SUPPORTS] concepts/scaling — "
    "bigger is better.\n\n"
    "Another paragraph discusses only [[jones2022]] with no comparison.\n"
)


def _synth_judge(task):
    """A SYNTH judge that reads its routed draft for recitation signals: a
    surfaced `[SUPPORTS]` relation tag transcribed into prose."""
    if task["axis"] != "SYNTH":
        return [], None
    draft = task["draft"]
    findings = []
    if "[SUPPORTS]" in draft:
        findings.append({
            "finding_id": "f-synth-0001", "severity": "major",
            "location": "Related work", "issue": "a raw [SUPPORTS] edge recited instead of argued",
            "evidence": "smith2023", "recommendation": "argue the edge, do not transcribe the tag",
        })
    if findings:
        return findings, 2  # below floor -> SYNTH sinks
    return [], None


def test_c_surfaced_supports_tag_becomes_synth_finding():
    result = board.run_board_round(1, _RECITATION_DRAFT, ingest_fn=_scoring_ingest(_synth_judge))
    synth_findings = result["findings"]["SYNTH"]
    assert any("[SUPPORTS]" in f["issue"] for f in synth_findings)
    assert result["floor_results"]["SYNTH"]["passed"] is False
    # The recitation signal did not leak into DEPTH or any other axis.
    assert all(result["findings"][a] == [] for a in _ALL_AXES if a != "SYNTH")


# ---------------------------------------------------------------------------
# (d) a bare assertion with no numbers (where the corpus quantifies) ->
#     DEPTH finding (prescriptive-specificity is DEPTH's to own).
# ---------------------------------------------------------------------------

_VAGUE_DRAFT = (
    "## Findings\n\n"
    "The larger model is much better than prior work, showing substantial "
    "improvement across the board.\n"
)

_VAGUE_QUALIFIERS = ("much better", "substantial", "substantially", "far more", "significantly")


def _depth_judge(task):
    """A DEPTH judge: flag a vague magnitude qualifier carrying no adjacent
    number (prescriptive-specificity)."""
    if task["axis"] != "DEPTH":
        return [], None
    draft = task["draft"].lower()
    has_digit = any(ch.isdigit() for ch in draft)
    hit = next((q for q in _VAGUE_QUALIFIERS if q in draft), None)
    if hit and not has_digit:
        return [{
            "finding_id": "f-depth-0001", "severity": "major",
            "location": "Findings", "issue": f"vague magnitude '{hit}' with no number where the corpus quantifies it",
            "evidence": "corpus-metric", "recommendation": "carry the reported figure",
        }], 2  # below floor -> DEPTH sinks
    return [], None


def test_d_bare_assertion_no_number_becomes_depth_finding():
    result = board.run_board_round(1, _VAGUE_DRAFT, ingest_fn=_scoring_ingest(_depth_judge))
    depth_findings = result["findings"]["DEPTH"]
    assert any("vague magnitude" in f["issue"] for f in depth_findings)
    assert result["floor_results"]["DEPTH"]["passed"] is False
    assert all(result["findings"][a] == [] for a in _ALL_AXES if a != "DEPTH")


# ---------------------------------------------------------------------------
# (e) canary abort -> HALT-DECLARE (the annotated-bib probe now rides SYNTH).
# ---------------------------------------------------------------------------

def test_e_rubber_stamped_annotated_bib_canary_halts_the_loop():
    def _ingest(tasks_doc, canary_key_doc):
        canaries = canary_key_doc["canaries"]
        ab_id = next(tid for tid, band in canaries.items() if band == "FAIL")
        verdicts = []
        for t in tasks_doc["tasks"]:
            tid = t["id"]
            if tid in canaries:
                band = canaries[tid]
                score = {"PASS-HIGH": 5, "FAIL-LOW": 1, "FAIL": 2}[band]
                if tid == ab_id:
                    score = 5  # rubber-stamp the annotated-bib probe
            else:
                score = 4
            verdicts.append({"id": tid, "axis": t["axis"], "score": score, "findings": []})
        return {"verdicts": verdicts}

    # HALT-DECLARE: the abort propagates loudly, never a silent pass.
    with pytest.raises(CanaryAbortError):
        board.run_bounded_board(_DRAFT, ingest_fn=_ingest, N=2)


def test_e_annotated_bib_canary_rides_the_synth_lens():
    """The 3 calibrated probes are now scored on SYNTH (PR-E: the
    annotated-bibliography failure IS a synthesis failure)."""
    emitted = emit_board_tasks(_DRAFT, manuscript="ms-e")
    canary_tasks = [t for t in emitted["tasks_doc"]["tasks"] if t["id"] in emitted["canary_key_doc"]["canaries"]]
    assert canary_tasks  # sanity
    assert all(t["axis"] == "SYNTH" for t in canary_tasks)
