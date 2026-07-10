# SPDX-License-Identifier: AGPL-3.0-or-later
"""tests/test_pr_f_cold_judge.py — PR-F acceptance: per-axis board canaries,
the counter-facet emit/ingest fan-out, and the experiment-path-intact proof.

Covers acceptance items:
  (e) a planted rubber-stamp WIDTH judge -> board HALT (per-axis canary), and
      a planted rubber-stamp support-matcher judge -> CanaryAbortError.
  (f) the counter-facet guard runs via emit/ingest (no env-var judge read).
  (d) ANTHROPIC_API_KEY still reaches experiment compute via model_client.
"""
from __future__ import annotations

import pytest

from research_vault.gates import board_seam
from research_vault.gates.board_seam import emit_board_tasks, ingest_board_verdicts
from research_vault.gates.judge_seam import CanaryAbortError


# ---------------------------------------------------------------------------
# (e) PER-AXIS canary — a rubber-stamp on ONLY the WIDTH judge trips HALT.
# ---------------------------------------------------------------------------

def _correct_score_for_band(band: str, floor: int = 3) -> int:
    """The score a correctly-calibrated judge returns for a canary band."""
    if band == "PASS-HIGH":
        return floor + 1
    if band == "FAIL-LOW":
        return floor - 2
    if band == "FAIL":
        return floor - 1
    raise AssertionError(band)


def test_per_axis_width_rubber_stamp_trips_halt():
    """PR-F ★: WIDTH (the csb dropped-cluster catcher) has its OWN canary. A
    judge that scores every REAL axis fine but rubber-stamps the WIDTH canary
    high must trip CanaryAbortError — a SYNTH-only canary (the pre-PR-F
    design) would NEVER catch a width-specific rubber-stamp."""
    emitted = emit_board_tasks("A draft body [[smith2020]].", manuscript="m1", floor_value=3)
    tasks = emitted["tasks_doc"]["tasks"]
    canaries = emitted["canary_key_doc"]["canaries"]  # id -> band

    # There IS a WIDTH canary (per-axis).
    width_canary_ids = [t["id"] for t in tasks if t["id"] in canaries and t["axis"] == "WIDTH"]
    assert width_canary_ids, "expected a per-axis WIDTH canary"

    verdicts = []
    for t in tasks:
        tid = t["id"]
        if tid in canaries:
            band = canaries[tid]
            # Correct on every canary EXCEPT the WIDTH one, which we
            # rubber-stamp with a high (passing) score.
            score = 5 if t["axis"] == "WIDTH" else _correct_score_for_band(band)
        else:
            score = 4  # real tasks all clear
        verdicts.append({"id": tid, "axis": t["axis"], "score": score, "verdict": "PASS", "findings": []})

    with pytest.raises(CanaryAbortError):
        ingest_board_verdicts(
            emitted["tasks_doc"], emitted["canary_key_doc"],
            {"schema": "rv-board-verdicts/v1", "verdicts": verdicts}, floor_value=3,
        )


def test_per_axis_depth_rubber_stamp_trips_halt():
    """Same, for DEPTH (bare-assertion canary): a rubber-stamp on ONLY DEPTH
    trips HALT."""
    emitted = emit_board_tasks("A draft.", manuscript="m1", floor_value=3)
    tasks = emitted["tasks_doc"]["tasks"]
    canaries = emitted["canary_key_doc"]["canaries"]
    verdicts = []
    for t in tasks:
        tid = t["id"]
        if tid in canaries:
            score = 5 if t["axis"] == "DEPTH" else _correct_score_for_band(canaries[tid])
        else:
            score = 4
        verdicts.append({"id": tid, "axis": t["axis"], "score": score, "verdict": "PASS", "findings": []})
    with pytest.raises(CanaryAbortError):
        ingest_board_verdicts(
            emitted["tasks_doc"], emitted["canary_key_doc"],
            {"schema": "rv-board-verdicts/v1", "verdicts": verdicts}, floor_value=3,
        )


def test_all_canaries_correct_passes():
    """The positive control: a correctly-calibrated judge on EVERY axis
    canary passes (no abort) — proves the per-axis canaries are not
    unconditionally tripping."""
    emitted = emit_board_tasks("A draft.", manuscript="m1", floor_value=3)
    tasks = emitted["tasks_doc"]["tasks"]
    canaries = emitted["canary_key_doc"]["canaries"]
    verdicts = []
    for t in tasks:
        tid = t["id"]
        score = _correct_score_for_band(canaries[tid]) if tid in canaries else 4
        verdicts.append({"id": tid, "axis": t["axis"], "score": score, "verdict": "PASS", "findings": []})
    result = ingest_board_verdicts(
        emitted["tasks_doc"], emitted["canary_key_doc"],
        {"schema": "rv-board-verdicts/v1", "verdicts": verdicts}, floor_value=3,
    )
    assert result["canary_aborted"] is False
    assert result["halt"] is False


def test_board_canary_bank_covers_every_axis():
    """Each of the 6 board axes carries at least one interleaved canary."""
    bank = board_seam._canary_bank(3)
    axes = {task["axis"] for task, _ in bank}
    assert axes == {"DEPTH", "WIDTH", "SYNTH", "SELFCONT", "ADVERS", "INSTRUCT"}
    # WIDTH's probe carries its mechanical coverage-diff ground truth.
    width = [t for t, _ in bank if t["axis"] == "WIDTH"][0]
    assert width["coverage_diff"]["missing"]  # non-empty dropped cluster


# ---------------------------------------------------------------------------
# (f) counter-facet guard runs via emit/ingest (no env-var judge read).
# ---------------------------------------------------------------------------

_CF_PROTOCOL = """---
type: review-protocol
question: "Do LLM personas drift over multi-turn conversation?"
seed_queries:
  by-temporal:
    thesis:
      - "cultural drift multi-turn LLM persona"
    counter:
      - "persona stability multi-turn LLM value persistence mechanism"
  by-domain:
    thesis:
      - "value shift domain adaptation"
    counter:
      - "value persistence across domains stability tradition"
sources: [semantic-scholar, arxiv]
counter-position: "stability"
---
"""


def _cf_emit():
    from research_vault.review import counter_facet_guard as cf
    return cf, cf.emit_counter_facet_tasks(_CF_PROTOCOL, scope="s1")


def test_counter_facet_emit_ingest_happy_path():
    cf, emitted = _cf_emit()
    tasks = emitted["tasks_doc"]["tasks"]
    canaries = emitted["canary_key_doc"]["canaries"]
    real = [t for t in tasks if t["id"] not in canaries]
    assert len(real) == 2  # two facets with non-empty counter lists
    verdicts = [
        {"id": t["id"], "verdict": (canaries[t["id"]] if t["id"] in canaries else "STRONG")}
        for t in tasks
    ]
    res = cf.ingest_counter_facet_verdicts(
        emitted["tasks_doc"], emitted["canary_key_doc"], {"verdicts": verdicts},
    )
    assert res["ok"] is True and res["halt"] is False and not res["blocking"]


def test_counter_facet_rubber_stamp_canary_aborts():
    """A judge that answers the planted STRAWMAN canary as STRONG (rubber-
    stamp) trips CanaryAbortError."""
    cf, emitted = _cf_emit()
    tasks = emitted["tasks_doc"]["tasks"]
    verdicts = [{"id": t["id"], "verdict": "STRONG"} for t in tasks]  # rubber-stamp all
    with pytest.raises(CanaryAbortError):
        cf.ingest_counter_facet_verdicts(
            emitted["tasks_doc"], emitted["canary_key_doc"], {"verdicts": verdicts},
        )


def test_counter_facet_incomplete_fanout_halts():
    cf, emitted = _cf_emit()
    res = cf.ingest_counter_facet_verdicts(
        emitted["tasks_doc"], emitted["canary_key_doc"], {"verdicts": []},
    )
    assert res["halt"] is True and res["ok"] is False


def test_counter_facet_real_strawman_blocks():
    """Canaries pass; a real facet answered STRAWMAN -> blocking (not HALT)."""
    cf, emitted = _cf_emit()
    tasks = emitted["tasks_doc"]["tasks"]
    canaries = emitted["canary_key_doc"]["canaries"]
    verdicts = []
    for t in tasks:
        if t["id"] in canaries:
            verdicts.append({"id": t["id"], "verdict": canaries[t["id"]]})
        else:
            verdicts.append({"id": t["id"], "verdict": "STRAWMAN"})  # real facets fail
    res = cf.ingest_counter_facet_verdicts(
        emitted["tasks_doc"], emitted["canary_key_doc"], {"verdicts": verdicts},
    )
    assert res["ok"] is False and res["halt"] is False and res["blocking"]


# ---------------------------------------------------------------------------
# (source-routing) WIDTH's compute_coverage_diff must read the [[citekey]]
# SOURCE body, never the [N]-rendered output.
# ---------------------------------------------------------------------------

def _write_coverage_map(path, used):
    lines = ["---", "coverage_map: true", "used:"]
    for ck, branch in used:
        lines += [f"  - citekey: {ck}", f"    branch: {branch}"]
    lines += ["---", "", "## rationale\n\nprose.\n"]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_coverage_diff_source_routing(tmp_path):
    """PR-F fit-check: feeding the [[citekey]] SOURCE body diffs correctly;
    feeding the [N]-rendered body (where every [[citekey]] became [N]) finds
    ZERO citekeys and false-flags EVERY used paper as dropped — proving the
    board must consume the source artifact, never PR-D's numbered render."""
    from research_vault.manuscript.check_gates import compute_coverage_diff

    map_path = tmp_path / "_coverage-map.md"
    _write_coverage_map(map_path, [("paperA", "cx"), ("paperB", "cy")])

    # SOURCE body: [[citekey]] wikilinks — both papers present -> nothing missing.
    source_body = "The field advances [[paperA]] and also [[paperB]] here.\n"
    src_diff = compute_coverage_diff(map_path, source_body)
    assert src_diff["missing"] == []
    assert set(src_diff["present"]) == {"paperA", "paperB"}

    # [N]-RENDERED body: PR-D converted [[paperA]]->[1], [[paperB]]->[2]. The
    # citekey regex finds nothing -> the diff WRONGLY reports both as missing.
    rendered_body = "The field advances [1] and also [2] here.\n"
    rendered_diff = compute_coverage_diff(map_path, rendered_body)
    assert set(rendered_diff["missing"]) == {"paperA", "paperB"}, (
        "the [N]-rendered body must false-flag every paper as missing — this "
        "is the failure the source-routing guardrail exists to prevent"
    )


# ---------------------------------------------------------------------------
# (d) the experiment-compute path still resolves ANTHROPIC_API_KEY.
# ---------------------------------------------------------------------------

def test_experiment_path_still_resolves_anthropic_key():
    """PR-F deletes the JUDGE api path only; the keyed EXPERIMENT-compute path
    (adapters/model_client resolving the Anthropic key into env for litellm)
    is untouched — proves the deletion did not sever experiment inference."""
    from research_vault.adapters import model_client

    env_names = {env for _secret, env in model_client._PROVIDER_KEY_SECRETS}
    assert "ANTHROPIC_API_KEY" in env_names
    # And the secret->env mapping for anthropic is intact.
    assert ("anthropic-api-key", "ANTHROPIC_API_KEY") in model_client._PROVIDER_KEY_SECRETS
