"""result.py — rv result: predicate-assertion verb (SR-PLAN-2, §5K.7).

When to use: use ``rv result assert <exp-note-path> --metric M --op OP --value V``
to evaluate a frozen predicate against a hash-verified experiment note.

Primary use: the ``watch: "cmd:rv result assert ..."`` predicate in a conditional
DAG node — if the command exits 0, the conditional fires; if it exits 1, the
watch never resolves and the conditional stays pending.

Subcommands:
  rv result assert <exp-note-path> --metric M --op OP --value V
      Evaluate metric M (from the results JSON at results_location) against V
      using comparison operator OP.  Ops: gt, lt, ge, le, eq, ne.
      If results_hash is set in the experiment note, the hash is verified before
      reading the file (tamper-evident evaluation, §5K.5.4).
      Exit 0 if the predicate is TRUE; exit 1 if FALSE or if an error occurs
      (file not found, metric key absent, hash mismatch).

      Optional logging:
        --run-id <id>     DAG run id; when given, the predicate string + SHA-256
                          hash + result + metric_actual are logged to run state
                          in meta["predicate_log"][<node-id>] (§5K.5.4).
        --node-id <id>    Node key in predicate_log (default: "default").

Metric extraction:
  results_location must point to a local JSON or JSONL file.
  - JSON file: the metric key is looked up directly in the top-level dict.
  - JSONL file: the last non-empty line is parsed; the metric key is looked up.
  - Dot-path keys (e.g. ``metrics.accuracy``) extract nested JSON values.

Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter — mirrors note.py._parse_frontmatter contract."""
    import re
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = re.match(r"^(\w[\w_-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith(("'", '"')) and val.endswith(val[0]):
                val = val[1:-1]
            fields[key] = val
    return fields, body


def _verify_results_hash(results_location: str, results_hash: str) -> str | None:
    """Verify results_location file hash matches results_hash.

    Returns None on success; an error string on failure.
    Only checks local files (not URLs/DOIs — zero-infra, trusts recorded hash).
    Only verifies when results_hash is non-empty and starts with 'sha256:'.
    """
    if not results_hash:
        return None  # No hash to verify — skip

    lower = results_location.lower()
    for prefix in ("http://", "https://", "ftp://", "s3://", "gs://", "doi:", "hdfs://"):
        if lower.startswith(prefix):
            return None  # Remote — trust the recorded hash (zero-infra)

    if not results_hash.startswith("sha256:"):
        return None  # Unknown hash format — skip

    artifact = Path(results_location)
    if not artifact.exists():
        return f"results artifact not found: {results_location}"

    expected_hex = results_hash[len("sha256:"):]
    try:
        h = hashlib.sha256()
        with open(artifact, "rb") as fh:
            while chunk := fh.read(1 << 20):
                h.update(chunk)
        actual_hex = h.hexdigest()
    except OSError as e:
        return f"cannot read results artifact: {e}"

    if actual_hex != expected_hex:
        return (
            f"results hash mismatch "
            f"(expected sha256:{expected_hex[:12]}…, "
            f"actual sha256:{actual_hex[:12]}…) — "
            f"the results file may have been modified after recording"
        )
    return None


def _extract_metric(results_location: str, metric: str) -> tuple[float | None, str | None]:
    """Extract metric value from the results JSON/JSONL file.

    Args:
        results_location: path to the results file.
        metric: key name, optionally dot-path (e.g. 'metrics.accuracy').

    Returns:
        (value, None) on success; (None, error_message) on failure.
    """
    p = Path(results_location)
    if not p.exists():
        return None, f"results file not found: {results_location}"

    try:
        raw = p.read_text(encoding="utf-8").strip()
    except OSError as e:
        return None, f"cannot read results file: {e}"

    # Detect JSONL (multiple non-empty lines each parseable as JSON) vs single JSON
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) > 1:
        # JSONL — use the last non-empty line
        try:
            data = json.loads(lines[-1])
        except json.JSONDecodeError as e:
            return None, f"cannot parse last JSONL line as JSON: {e}"
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, f"cannot parse results file as JSON: {e}"

    # Navigate dot-path
    parts = metric.split(".")
    obj = data
    for part in parts:
        if not isinstance(obj, dict):
            return None, (
                f"metric path {metric!r}: expected dict at {part!r}, "
                f"got {type(obj).__name__}"
            )
        if part not in obj:
            available = list(obj.keys()) if isinstance(obj, dict) else []
            return None, (
                f"metric key {metric!r} not found in results. "
                f"Available keys: {available}"
            )
        obj = obj[part]

    try:
        value = float(obj)
    except (TypeError, ValueError):
        return None, f"metric {metric!r} value {obj!r} is not numeric"

    return value, None


_OPS = {
    "gt": lambda a, b: a > b,
    "lt": lambda a, b: a < b,
    "ge": lambda a, b: a >= b,
    "le": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
}


def _evaluate_predicate(
    actual: float,
    op: str,
    value: float,
) -> bool:
    """Evaluate ``actual OP value``."""
    fn = _OPS.get(op)
    if fn is None:
        raise ValueError(f"Unknown op {op!r}. Valid: {list(_OPS)}")
    return fn(actual, value)


def _build_predicate_str(
    exp_note_path: str,
    metric: str,
    op: str,
    value: str,
    run_id: str | None,
    node_id: str | None,
) -> str:
    """Reconstruct the canonical predicate string for hashing and logging."""
    parts = [
        "rv result assert", exp_note_path,
        "--metric", metric,
        "--op", op,
        "--value", value,
    ]
    if run_id:
        parts += ["--run-id", run_id]
    if node_id:
        parts += ["--node-id", node_id]
    return " ".join(parts)


def _log_to_run_state(
    run_id: str,
    node_id: str,
    predicate_str: str,
    metric: str,
    op: str,
    value: str,
    metric_actual: float,
    result: bool,
    *,
    state_dir: "Path | None" = None,
) -> str | None:
    """Log predicate evaluation to the DAG run state meta (§5K.5.4).

    Stores in run_state.meta["predicate_log"][node_id]:
      {predicate, predicate_hash, metric, op, value, metric_actual,
       result, evaluated_at}

    Args:
        run_id:         DAG run id.
        node_id:        Key in predicate_log for this assertion.
        predicate_str:  Canonical predicate command string (for hashing).
        metric, op, value: Predicate components (stored verbatim).
        metric_actual:  Evaluated metric value.
        result:         True if predicate is satisfied.
        state_dir:      Optional explicit state directory; if None, resolved
                        from the active config.  Useful in tests.

    Returns None on success; an error string on failure.
    """
    predicate_hash = hashlib.sha256(predicate_str.encode("utf-8")).hexdigest()

    try:
        from research_vault.dag.store import RunStore
        if state_dir is not None:
            store = RunStore(state_dir)
        else:
            from research_vault.config import load_config
            cfg = load_config()
            store = RunStore.from_config(cfg)
    except Exception as e:
        return f"cannot load run store: {e}"

    try:
        run_state = store.load(run_id)
    except Exception as e:
        return f"cannot load run state for {run_id!r}: {e}"

    if "predicate_log" not in run_state.meta:
        run_state.meta["predicate_log"] = {}

    run_state.meta["predicate_log"][node_id] = {
        "predicate": predicate_str,
        "predicate_hash": predicate_hash,
        "metric": metric,
        "op": op,
        "value": value,
        "metric_actual": metric_actual,
        "result": result,
        "evaluated_at": time.time(),
    }

    try:
        store.save(run_state)
    except Exception as e:
        return f"cannot save run state: {e}"

    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(parent: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = parent.add_parser(
        "result",
        help="Predicate-assertion verb for conditional DAG nodes (§5K.7). "
             "Use as a watch: cmd: predicate.",
    )
    sub = p.add_subparsers(dest="result_subcommand", metavar="<subcommand>")

    assert_p = sub.add_parser(
        "assert",
        help=(
            "Assert metric M OP value V holds in an experiment note's results. "
            "Exit 0 = predicate true; exit 1 = predicate false or error. "
            "Use as: watch: \"cmd:rv result assert <note> --metric M --op gt --value V\""
        ),
    )
    assert_p.add_argument(
        "exp_note",
        metavar="<exp-note-path>",
        help="Path to the experiment note (experiments/<id>.md).",
    )
    assert_p.add_argument(
        "--metric",
        required=True,
        metavar="<key>",
        help=(
            "Metric key to extract from the results JSON. "
            "Use dot-path for nested keys (e.g. metrics.accuracy)."
        ),
    )
    assert_p.add_argument(
        "--op",
        required=True,
        choices=list(_OPS),
        metavar="<op>",
        help="Comparison operator: gt, lt, ge, le, eq, ne.",
    )
    assert_p.add_argument(
        "--value",
        required=True,
        metavar="<threshold>",
        help="Numeric threshold to compare against.",
    )
    assert_p.add_argument(
        "--run-id",
        default=None,
        metavar="<run-id>",
        help=(
            "DAG run id. When given, log the predicate + hash + result "
            "to run state meta (§5K.5.4)."
        ),
    )
    assert_p.add_argument(
        "--node-id",
        default="default",
        metavar="<node-id>",
        help="Key in predicate_log for this assertion (default: 'default').",
    )
    return p


def run(args: argparse.Namespace) -> int:
    subcommand = getattr(args, "result_subcommand", None)
    if subcommand == "assert":
        return _run_assert(args)
    print(
        "rv result: missing subcommand. Use `rv result assert <note> --metric M --op OP --value V`.",
        file=sys.stderr,
    )
    return 1


def _run_assert(args: argparse.Namespace) -> int:
    """Execute rv result assert."""
    exp_note_path = Path(args.exp_note)
    metric = args.metric
    op = args.op
    value_str = args.value
    run_id = getattr(args, "run_id", None)
    node_id = getattr(args, "node_id", "default")

    # Parse threshold
    try:
        threshold = float(value_str)
    except ValueError:
        print(
            f"rv result assert: --value {value_str!r} is not a number.",
            file=sys.stderr,
        )
        return 1

    # Load experiment note
    if not exp_note_path.exists():
        print(
            f"rv result assert: experiment note not found: {exp_note_path}",
            file=sys.stderr,
        )
        return 1

    try:
        text = exp_note_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"rv result assert: cannot read experiment note: {e}", file=sys.stderr)
        return 1

    fields, _ = _parse_frontmatter(text)
    results_location = fields.get("results_location", "").strip()
    results_hash = fields.get("results_hash", "").strip()

    if not results_location:
        print(
            f"rv result assert: experiment note {exp_note_path.name} has no "
            f"results_location — run rv wandb pull or set results_location manually.",
            file=sys.stderr,
        )
        return 1

    # Verify hash (if set)
    hash_err = _verify_results_hash(results_location, results_hash)
    if hash_err is not None:
        print(f"rv result assert: {hash_err}", file=sys.stderr)
        return 1

    # Extract metric
    metric_actual, extract_err = _extract_metric(results_location, metric)
    if extract_err is not None:
        print(f"rv result assert: {extract_err}", file=sys.stderr)
        return 1

    # Evaluate predicate
    result = _evaluate_predicate(metric_actual, op, threshold)

    # Build predicate string (for logging + human output)
    predicate_str = _build_predicate_str(
        str(exp_note_path), metric, op, value_str, run_id, node_id,
    )

    # Log to run state (§5K.5.4)
    if run_id:
        log_err = _log_to_run_state(
            run_id, node_id, predicate_str, metric, op, value_str,
            metric_actual, result,
        )
        if log_err is not None:
            # Log failure is non-fatal: print a warning but don't override the
            # predicate result (the DAG watch still resolves based on the predicate).
            print(
                f"rv result assert: WARNING — could not log to run state: {log_err}",
                file=sys.stderr,
            )

    # Print result summary
    status = "TRUE" if result else "FALSE"
    exit_code = 0 if result else 1
    op_symbol = {"gt": ">", "lt": "<", "ge": ">=", "le": "<=", "eq": "==", "ne": "!="}[op]
    print(
        f"rv result assert: {status} — "
        f"{metric}={metric_actual} {op_symbol} {threshold}"
    )

    return exit_code
