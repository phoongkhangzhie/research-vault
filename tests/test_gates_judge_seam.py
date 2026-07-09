"""tests/test_gates_judge_seam.py — NG-4 shared low-level primitives:
schema constants, id assignment, canary interleave, canary check
(fail-closed missing==failed), and the fail-closed fill.

sr: NG-4
"""
from __future__ import annotations

import pytest

from research_vault.gates.judge_seam import (
    CANARY_KEY_SCHEMA,
    TASKS_SCHEMA,
    VERDICTS_SCHEMA,
    CanaryAbortError,
    check_canaries,
    fail_closed_fill,
    fanout_incomplete,
    interleave_with_canaries,
    make_task_id,
    now_iso,
    read_json_or_none,
    write_json,
)


def test_schema_constants_are_versioned():
    assert TASKS_SCHEMA == "rv-judge-tasks/v1"
    assert CANARY_KEY_SCHEMA == "rv-judge-canary-key/v1"
    assert VERDICTS_SCHEMA == "rv-judge-verdicts/v1"


def test_make_task_id_is_zero_padded_and_stable():
    assert make_task_id(1) == "t0001"
    assert make_task_id(23) == "t0023"
    assert make_task_id(10000) == "t10000"


def test_now_iso_shape():
    ts = now_iso()
    assert ts.endswith("Z")
    assert "T" in ts


# ---------------------------------------------------------------------------
# interleave_with_canaries
# ---------------------------------------------------------------------------

def test_interleave_assigns_sequential_ids_no_marker_field():
    real = [{"kind": "support", "claim": f"claim {i}"} for i in range(5)]
    canaries = [
        ({"kind": "support", "claim": "canary A"}, "SUPPORTS"),
        ({"kind": "support", "claim": "canary B"}, "ABSENT"),
    ]
    combined, canary_key = interleave_with_canaries(real, canaries)

    assert len(combined) == 7
    ids = [t["id"] for t in combined]
    assert ids == [make_task_id(i + 1) for i in range(7)]
    # No task carries a field marking it as a canary — indistinguishable.
    for t in combined:
        assert "_is_canary_expected" not in t
        assert "is_canary" not in t
        assert "canary" not in t
    assert len(canary_key) == 2
    # Every canary_key id resolves to a real entry in combined.
    combined_by_id = {t["id"]: t for t in combined}
    for cid in canary_key:
        assert cid in combined_by_id


def test_interleave_canary_positions_not_all_at_the_end():
    # A cheap-but-real regression guard against "canaries always appended
    # last" (a positional tell that would defeat "no marker" in spirit).
    real = [{"kind": "support", "claim": f"claim {i}"} for i in range(9)]
    canaries = [
        ({"kind": "support", "claim": "canary A"}, "SUPPORTS"),
        ({"kind": "support", "claim": "canary B"}, "ABSENT"),
        ({"kind": "support", "claim": "canary C"}, "CONTRADICTS"),
    ]
    combined, canary_key = interleave_with_canaries(real, canaries)
    n = len(combined)
    canary_indices = sorted(
        i for i, t in enumerate(combined) if t["id"] in canary_key
    )
    # Not all three canaries in the final 3 positions.
    assert canary_indices != [n - 3, n - 2, n - 1]


def test_interleave_zero_canaries_is_honest_noop():
    real = [{"kind": "support", "claim": "only one"}]
    combined, canary_key = interleave_with_canaries(real, [])
    assert len(combined) == 1
    assert combined[0]["id"] == "t0001"
    assert canary_key == {}


def test_interleave_is_deterministic_across_calls():
    real = [{"kind": "support", "claim": f"claim {i}"} for i in range(6)]
    canaries = [
        ({"kind": "support", "claim": "canary A"}, "SUPPORTS"),
        ({"kind": "support", "claim": "canary B"}, "ABSENT"),
    ]
    combined1, key1 = interleave_with_canaries(list(real), list(canaries))
    combined2, key2 = interleave_with_canaries(list(real), list(canaries))
    assert combined1 == combined2
    assert key1 == key2


# ---------------------------------------------------------------------------
# check_canaries — fail-closed, missing counts as failed
# ---------------------------------------------------------------------------

def test_check_canaries_all_correct_passes_silently():
    check_canaries({"t0003": "SUPPORTS", "t0007": "ABSENT"},
                    {"t0001": "SUPPORTS", "t0003": "SUPPORTS", "t0007": "ABSENT"})


def test_check_canaries_mismatched_raises_canary_abort():
    with pytest.raises(CanaryAbortError, match="t0003"):
        check_canaries({"t0003": "SUPPORTS"}, {"t0003": "ABSENT"})


def test_check_canaries_missing_verdict_raises_canary_abort_fail_closed():
    """RED-before-green regression guard: a canary id ABSENT from the
    verdicts dict (the judge never answered it, or the batch was cut off
    mid-way) must be treated as a FAILED canary, not silently skipped."""
    with pytest.raises(CanaryAbortError, match="MISSING"):
        check_canaries({"t0009": "SUPPORTS"}, {"t0001": "SUPPORTS"})


def test_check_canaries_case_insensitive_compare():
    check_canaries({"t0001": "supports"}, {"t0001": "SUPPORTS"})


def test_check_canaries_empty_key_is_honest_noop():
    check_canaries({}, {"t0001": "SUPPORTS"})


# ---------------------------------------------------------------------------
# fail_closed_fill
# ---------------------------------------------------------------------------

_SUPPORT_VOCAB = frozenset({"SUPPORTS", "PARTIAL", "ABSENT", "CONTRADICTS"})


def test_fail_closed_fill_all_present_and_valid():
    filled, missing, unrecognized = fail_closed_fill(
        ["t0001", "t0002"],
        {"t0001": "SUPPORTS", "t0002": "PARTIAL"},
        _SUPPORT_VOCAB,
        "ABSENT",
    )
    assert filled == {"t0001": "SUPPORTS", "t0002": "PARTIAL"}
    assert missing == []
    assert unrecognized == []


def test_fail_closed_fill_missing_id_defaults_and_is_surfaced():
    filled, missing, unrecognized = fail_closed_fill(
        ["t0001", "t0002"],
        {"t0001": "SUPPORTS"},
        _SUPPORT_VOCAB,
        "ABSENT",
    )
    assert filled["t0002"] == "ABSENT"
    assert missing == ["t0002"]
    assert unrecognized == []


def test_fail_closed_fill_unrecognized_verdict_defaults_and_is_surfaced():
    filled, missing, unrecognized = fail_closed_fill(
        ["t0001"],
        {"t0001": "MAYBE"},
        _SUPPORT_VOCAB,
        "ABSENT",
    )
    assert filled["t0001"] == "ABSENT"
    assert missing == []
    assert unrecognized == ["t0001"]


def test_fail_closed_fill_never_widens_vocab():
    # Sweep of non-canonical spellings — none may pass through unchanged.
    # ("  supports" IS tolerated — whitespace/case tolerance, mirrors the
    # stop_reason whitelist lesson — but only the exact token, nothing else.)
    for bogus in ("supports!", "SUPPORT", "yes", "true", ""):
        filled, _, unrecognized = fail_closed_fill(
            ["t0001"], {"t0001": bogus}, _SUPPORT_VOCAB, "ABSENT",
        )
        assert filled["t0001"] == "ABSENT", f"leaked through: {bogus!r}"
        assert unrecognized == ["t0001"]


# ---------------------------------------------------------------------------
# fanout_incomplete — the §1.8 floor-gate NOT-RUN detector
# ---------------------------------------------------------------------------

def test_fanout_incomplete_when_verdicts_file_entirely_absent():
    tasks_doc = {"tasks": [{"id": "t0001"}]}
    assert fanout_incomplete(tasks_doc, None) is True


def test_fanout_incomplete_when_verdicts_list_empty():
    tasks_doc = {"tasks": [{"id": "t0001"}]}
    verdicts_doc = {"verdicts": []}
    assert fanout_incomplete(tasks_doc, verdicts_doc) is True


def test_fanout_incomplete_false_when_partial_coverage():
    tasks_doc = {"tasks": [{"id": "t0001"}, {"id": "t0002"}]}
    verdicts_doc = {"verdicts": [{"id": "t0001", "verdict": "SUPPORTS"}]}
    assert fanout_incomplete(tasks_doc, verdicts_doc) is False


def test_fanout_incomplete_false_when_zero_real_tasks():
    # A gate with nothing to check (e.g. no \cite in the draft) is a
    # correct no-op — never a HALT for having nothing to do.
    tasks_doc = {"tasks": []}
    assert fanout_incomplete(tasks_doc, None) is False


# ---------------------------------------------------------------------------
# JSON I/O
# ---------------------------------------------------------------------------

def test_write_then_read_json_roundtrip(tmp_path):
    p = tmp_path / "nested" / "_judge-tasks.json"
    doc = {"schema": TASKS_SCHEMA, "tasks": [{"id": "t0001"}]}
    write_json(p, doc)
    assert p.exists()
    assert read_json_or_none(p) == doc


def test_read_json_or_none_absent_file(tmp_path):
    assert read_json_or_none(tmp_path / "nope.json") is None


def test_read_json_or_none_malformed_file(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert read_json_or_none(p) is None
