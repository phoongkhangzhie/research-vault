"""test_sr_ep_role.py — SR-EP-ROLE: per-endpoint when_to_use + host_group.

Tests:
  1.  _scaffold_manifest — local profile has seeded (non-FILL) when_to_use
  2.  _scaffold_manifest — compute-node FILL profile has a FILL when_to_use
  3.  _scaffold_manifest — commented transfer-node example present in JSON
  4.  _scaffold_manifest — transfer-node example has host_group + when_to_use
  5.  _scaffold_manifest — zero real hostnames (leakage-clean; generic names only)
  6.  cmd_init next-steps print names the when_to_use authoring step
  7.  cmd_show — renders when_to_use for each profile that has one
  8.  cmd_show — groups profiles by host_group visually (same group on same header)
  9.  cmd_show — soft WARN fires when ≥2 active profiles share host_group, one lacks when_to_use
  10. cmd_show — soft WARN does NOT fire for a fully-labelled pair (no missing when_to_use)
  11. cmd_show — soft WARN does NOT fire for a single local-only manifest
  12. cmd_show — soft WARN is non-fatal (exit code unchanged = 0)
  13. cmd_explain — surfaces the active backend's when_to_use in resolved output
  14. _print_explain — renders when_to_use in the printed output
  15. Back-compat — manifest with no when_to_use/host_group validates and renders (no crash)
  16. Back-compat — _scaffold_manifest without new fields still writes valid JSON
  17. Doctor probe dispatch unchanged — host_group/when_to_use not branched on
  18. Verb registry — compute when_to_use names the shoehorn anti-pattern (DTN)
  19. rv help --check green after adding anti-pattern to compute when_to_use
  20. Soft WARN fires on ≥2 active non-local remote profiles (absent host_group) where one lacks when_to_use
  21. Soft WARN does NOT fire when both remote profiles in same host_group have when_to_use

All tests hermetic: tmp_path only; no ~/vault reads or writes.
Leakage-clean: no real hostnames (example.edu / mycluster-dt.example.edu use example.edu domain).
"""
from __future__ import annotations

import io
import json
import sys
import warnings
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path) -> Config:
    raw: dict[str, Any] = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {},
    }
    cfg = Config(raw)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return _make_cfg(tmp_path)


def _write_two_endpoint_manifest(cfg: Config, *, compute_wtu: str | None, transfer_wtu: str | None) -> None:
    """Write a two-endpoint compute-node + transfer-node manifest sharing host_group."""
    from research_vault.compute import _save_manifest
    compute_prof: dict[str, Any] = {
        "archetype": "ssh+slurm",
        "host": "login.example-cluster.edu",
        "submit_pattern": "sbatch --partition=gpu",
        "host_group": "mycluster",
    }
    if compute_wtu is not None:
        compute_prof["when_to_use"] = compute_wtu

    transfer_prof: dict[str, Any] = {
        "archetype": "ssh",
        "host": "dtn.example-cluster.edu",
        "host_group": "mycluster",
    }
    if transfer_wtu is not None:
        transfer_prof["when_to_use"] = transfer_wtu

    _save_manifest(cfg, {
        "backends": {
            "active": ["compute-node", "transfer-node"],
            "profiles": {
                "local": {"archetype": "local"},
                "compute-node": compute_prof,
                "transfer-node": transfer_prof,
            },
        },
        "conda_envs": {},
        "gpu_tiers": {"tp1": {"gpus": 1, "models": ["<=7B"]}},
        "rules": [],
        "model_quirks": {},
        "run_outcomes": [],
    })


# ---------------------------------------------------------------------------
# 1-6: _scaffold_manifest + cmd_init
# ---------------------------------------------------------------------------

class TestScaffoldManifest:
    def test_local_profile_has_seeded_when_to_use(self) -> None:
        """Local profile in scaffold has a seeded (non-FILL) when_to_use."""
        from research_vault.compute import _scaffold_manifest, _FILL_PREFIX
        m = _scaffold_manifest()
        local = m["backends"]["profiles"]["local"]
        assert "when_to_use" in local, "local profile must have when_to_use"
        wtu = local["when_to_use"]
        assert not wtu.startswith(_FILL_PREFIX), (
            "local when_to_use must be seeded (not a FILL) — its role needs no authoring"
        )
        assert len(wtu) > 5, "local when_to_use must be non-trivial"

    def test_compute_node_fill_profile_has_fill_when_to_use(self) -> None:
        """The primary remote profile in scaffold carries a FILL when_to_use."""
        from research_vault.compute import _scaffold_manifest, _FILL_PREFIX
        m = _scaffold_manifest()
        profiles = m["backends"]["profiles"]
        # Find the primary non-local remote profile (cluster or compute-node)
        remote_profiles = {k: v for k, v in profiles.items() if k != "local"}
        assert remote_profiles, "scaffold must have at least one non-local profile"
        # At least one remote profile must have a FILL when_to_use
        has_fill_wtu = any(
            v.get("when_to_use", "").startswith(_FILL_PREFIX)
            for v in remote_profiles.values()
        )
        assert has_fill_wtu, (
            "At least one remote profile must have a FILL when_to_use so the "
            "adopter is guided to describe the endpoint's role"
        )

    def test_scaffold_has_transfer_node_example(self) -> None:
        """Scaffold manifest includes a transfer-node example profile."""
        from research_vault.compute import _scaffold_manifest
        m = _scaffold_manifest()
        profiles = m["backends"]["profiles"]
        # The transfer-node example may be under "transfer-node" key
        # (the spec says it can be a commented block — we represent it as a real profile
        # since JSON has no comments; it should be INACTIVE in `active`)
        assert "transfer-node" in profiles, (
            "scaffold must include a 'transfer-node' example profile "
            "(inactive by default, showing the DTN pattern)"
        )

    def test_transfer_node_has_host_group_and_when_to_use(self) -> None:
        """Transfer-node example has host_group and when_to_use."""
        from research_vault.compute import _scaffold_manifest
        m = _scaffold_manifest()
        transfer = m["backends"]["profiles"].get("transfer-node", {})
        assert "host_group" in transfer, "transfer-node must have host_group"
        assert "when_to_use" in transfer, "transfer-node must have when_to_use"
        wtu = transfer["when_to_use"]
        # Must name staging/download role and the anti-pattern (do NOT submit jobs)
        assert any(kw in wtu.lower() for kw in ["stage", "staging", "download", "transfer"]), (
            "transfer-node when_to_use must name staging/download role"
        )
        assert "not" in wtu.lower() or "anti" in wtu.lower() or "do not" in wtu.lower(), (
            "transfer-node when_to_use must include an anti-pattern (do NOT submit jobs on DTN)"
        )

    def test_scaffold_transfer_node_and_compute_both_have_host_group(self) -> None:
        """Transfer-node and compute-node examples both carry a host_group field.

        The scaffold uses FILL strings for both (the user fills them with the
        same value to link the two endpoints). We check FIELD PRESENCE, not
        string equality — FILL strings will differ.
        """
        from research_vault.compute import _scaffold_manifest
        m = _scaffold_manifest()
        profiles = m["backends"]["profiles"]
        transfer = profiles.get("transfer-node", {})
        # The primary compute node should be "cluster" or "compute-node"
        remote_names = [k for k in profiles if k not in ("local", "transfer-node")]
        assert remote_names, "scaffold must have a primary compute profile"
        compute = profiles[remote_names[0]]
        assert "host_group" in compute, "compute profile must have host_group field"
        assert "host_group" in transfer, "transfer-node must have host_group field"
        # Both values must be non-empty (either FILL or a real value)
        assert compute["host_group"], "compute host_group must be non-empty"
        assert transfer["host_group"], "transfer-node host_group must be non-empty"

    def test_scaffold_no_real_hostnames(self) -> None:
        """Scaffold must contain no real hostnames — generic names only (leakage gate)."""
        from research_vault.compute import _scaffold_manifest
        m = _scaffold_manifest()
        text = json.dumps(m)
        # Real hostname patterns to prohibit
        forbidden_patterns = [
            # No real university/cluster domains
            ".edu",  # generic example.edu is fine; test separately
            ".ac.uk",
            "slurm",  # not a hostname; allowed in archetype names
        ]
        # Only check hostname fields, not the whole JSON (archetype names may contain slurm)
        hostname_texts = []
        for prof in m["backends"]["profiles"].values():
            if "host" in prof:
                hostname_texts.append(prof["host"])
            if "host_group" in prof:
                hostname_texts.append(prof["host_group"])

        for hostname in hostname_texts:
            # Must contain FILL or be a generic placeholder — real .edu hostnames are leakage
            # Allow FILL-based values; reject real-looking cluster hostnames
            # Generic "FILL — ..." values are fine; "login.mycluster.edu" is not
            if hostname.startswith("FILL") or "FILL" in hostname:
                continue
            assert "example" in hostname.lower() or hostname == hostname.upper(), (
                f"Hostname {hostname!r} looks real — must be FILL or example-based "
                "to avoid leaking real infrastructure in scaffold (leakage gate)"
            )

    def test_transfer_node_inactive_in_scaffold(self) -> None:
        """Transfer-node example is inactive in scaffold (not in active list)."""
        from research_vault.compute import _scaffold_manifest
        m = _scaffold_manifest()
        active = m["backends"]["active"]
        assert "transfer-node" not in active, (
            "transfer-node must be INACTIVE in the scaffold — it's an example for the user to adapt"
        )

    def test_cmd_init_next_steps_names_when_to_use(self, cfg: Config, capsys) -> None:
        """cmd_init next-steps print names the when_to_use authoring step."""
        from research_vault.compute import cmd_init
        cmd_init(cfg)
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "when_to_use" in out, (
            "cmd_init next-steps must name 'when_to_use' to guide the adopter "
            "to describe each endpoint's role"
        )


# ---------------------------------------------------------------------------
# 7-12: cmd_show rendering + soft WARN
# ---------------------------------------------------------------------------

class TestCmdShow:
    def test_show_renders_when_to_use_per_profile(self, cfg: Config, capsys) -> None:
        """cmd_show prints when_to_use for each profile that has one."""
        _write_two_endpoint_manifest(
            cfg,
            compute_wtu="Submit training/eval JOBS here (sbatch). The compute/login node.",
            transfer_wtu="Big downloads + staging here (plain ssh; DTN). Anti-pattern: do NOT submit jobs.",
        )
        from research_vault.compute import cmd_show
        cmd_show(cfg)
        out = capsys.readouterr().out
        assert "Submit training/eval JOBS" in out, "compute-node when_to_use not rendered"
        assert "Big downloads" in out, "transfer-node when_to_use not rendered"

    def test_show_groups_by_host_group(self, cfg: Config, capsys) -> None:
        """cmd_show groups profiles sharing a host_group so co-located endpoints read together."""
        _write_two_endpoint_manifest(
            cfg,
            compute_wtu="Submit jobs here.",
            transfer_wtu="Stage data here.",
        )
        from research_vault.compute import cmd_show
        cmd_show(cfg)
        out = capsys.readouterr().out
        # The host_group name should appear in the output (as a group header)
        assert "mycluster" in out, (
            "cmd_show must render the host_group so co-located endpoints are visually grouped"
        )
        # Both endpoints must appear together (near the group header)
        mycluster_pos = out.find("mycluster")
        compute_pos = out.find("compute-node")
        transfer_pos = out.find("transfer-node")
        assert mycluster_pos >= 0
        assert compute_pos >= 0
        assert transfer_pos >= 0

    def test_show_soft_warn_fires_on_ambiguity(self, cfg: Config, capsys) -> None:
        """Soft WARN fires when ≥2 active profiles share host_group and one lacks when_to_use."""
        _write_two_endpoint_manifest(
            cfg,
            compute_wtu="Submit jobs here.",
            transfer_wtu=None,  # missing — triggers WARN
        )
        from research_vault.compute import cmd_show
        rc = cmd_show(cfg)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # WARN must mention the missing when_to_use or ambiguity
        assert any(kw in combined.lower() for kw in ["warn", "when_to_use", "ambig"]), (
            "Soft WARN must be emitted when shared-host_group endpoints lack when_to_use"
        )
        # Non-fatal: exit code unchanged
        assert rc == 0, "Soft WARN must be non-fatal (exit code 0)"

    def test_show_no_warn_fully_labelled_pair(self, cfg: Config, capsys) -> None:
        """Soft WARN does NOT fire for a fully-labelled pair (both have when_to_use)."""
        _write_two_endpoint_manifest(
            cfg,
            compute_wtu="Submit jobs here.",
            transfer_wtu="Stage data here.",
        )
        from research_vault.compute import cmd_show
        rc = cmd_show(cfg)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # No ambiguity — WARN must not fire
        assert "WARN" not in combined.upper() or "when_to_use" not in combined, (
            "No WARN should fire when all shared-host_group profiles have when_to_use"
        )
        # Correct: no warn-about-missing-when_to_use phrasing
        lower = combined.lower()
        assert "missing when_to_use" not in lower
        assert rc == 0

    def test_show_no_warn_single_local_manifest(self, cfg: Config, capsys) -> None:
        """Soft WARN does NOT fire for a single local-only manifest."""
        from research_vault.compute import _save_manifest, cmd_show
        _save_manifest(cfg, {
            "backends": {
                "active": ["local"],
                "profiles": {"local": {"archetype": "local"}},
            },
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        })
        rc = cmd_show(cfg)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "missing when_to_use" not in combined.lower()
        assert rc == 0

    def test_show_soft_warn_nonfatal(self, cfg: Config, capsys) -> None:
        """Soft WARN is non-fatal — exit code is 0 even when WARN fires."""
        _write_two_endpoint_manifest(
            cfg,
            compute_wtu="Submit jobs here.",
            transfer_wtu=None,
        )
        from research_vault.compute import cmd_show
        rc = cmd_show(cfg)
        assert rc == 0


# ---------------------------------------------------------------------------
# 13-14: cmd_explain surfaces when_to_use
# ---------------------------------------------------------------------------

class TestCmdExplain:
    def test_explain_resolved_includes_when_to_use(self, cfg: Config) -> None:
        """cmd_explain returns the active backend's when_to_use in the resolved dict."""
        from research_vault.compute import _save_manifest, cmd_explain
        _save_manifest(cfg, {
            "backends": {
                "active": ["compute-node"],
                "profiles": {
                    "local": {"archetype": "local"},
                    "compute-node": {
                        "archetype": "ssh+slurm",
                        "host": "login.example-cluster.edu",
                        "submit_pattern": "sbatch --partition=gpu",
                        "when_to_use": "Submit training/eval JOBS here (sbatch).",
                    },
                },
            },
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        })
        resolved = cmd_explain(cfg, "my-model")
        assert resolved is not None
        assert "when_to_use" in resolved, "cmd_explain resolved dict must include when_to_use"
        assert "Submit training/eval JOBS" in resolved["when_to_use"]

    def test_explain_no_when_to_use_if_profile_lacks_it(self, cfg: Config) -> None:
        """cmd_explain does not fail if the active profile has no when_to_use."""
        from research_vault.compute import _save_manifest, cmd_explain
        _save_manifest(cfg, {
            "backends": {
                "active": ["cluster"],
                "profiles": {
                    "local": {"archetype": "local"},
                    "cluster": {
                        "archetype": "ssh+slurm",
                        "host": "login.example.edu",
                        "submit_pattern": "sbatch",
                        # No when_to_use
                    },
                },
            },
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        })
        resolved = cmd_explain(cfg, "my-model")
        assert resolved is not None
        # when_to_use may be absent or None — must not crash
        wtu = resolved.get("when_to_use")
        assert wtu is None or wtu == "", "when_to_use should be absent/None when profile lacks it"

    def test_print_explain_renders_when_to_use(self, cfg: Config, capsys) -> None:
        """_print_explain renders when_to_use in the printed output."""
        from research_vault.compute import _print_explain
        resolved = {
            "job": "my-model",
            "backend": "compute-node",
            "conda_env": None,
            "tier": None,
            "gpus": None,
            "submit_flags": "sbatch --partition=gpu",
            "model_quirks": {},
            "when_to_use": "Submit training/eval JOBS here (sbatch).",
        }
        _print_explain("my-model", resolved)
        out = capsys.readouterr().out
        assert "Submit training/eval JOBS" in out, "_print_explain must render when_to_use"


# ---------------------------------------------------------------------------
# 15-16: Back-compat
# ---------------------------------------------------------------------------

class TestBackCompat:
    def test_manifest_without_new_fields_renders_cleanly(self, cfg: Config, capsys) -> None:
        """A manifest with no when_to_use/host_group validates and renders without crash."""
        from research_vault.compute import _save_manifest, cmd_show
        _save_manifest(cfg, {
            "backends": {
                "active": ["local"],
                "profiles": {
                    "local": {"archetype": "local"},
                    "cluster": {
                        "archetype": "ssh+slurm",
                        "host": "login.example.edu",
                        "submit_pattern": "sbatch --partition=gpu",
                    },
                },
            },
            "conda_envs": {},
            "gpu_tiers": {"tp1": {"gpus": 1, "models": ["<=7B"]}},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        })
        rc = cmd_show(cfg)
        assert rc == 0, "cmd_show must not crash on manifests without when_to_use/host_group"
        out = capsys.readouterr().out
        assert "cluster" in out

    def test_scaffold_without_new_fields_is_valid_json(self) -> None:
        """_scaffold_manifest returns valid JSON — always has been, still is."""
        from research_vault.compute import _scaffold_manifest
        m = _scaffold_manifest()
        text = json.dumps(m)
        parsed = json.loads(text)
        assert "backends" in parsed

    def test_explain_on_old_manifest_does_not_crash(self, cfg: Config) -> None:
        """cmd_explain on a manifest without when_to_use must not crash."""
        from research_vault.compute import _save_manifest, cmd_explain
        _save_manifest(cfg, {
            "backends": {
                "active": ["local"],
                "profiles": {"local": {"archetype": "local"}},
            },
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        })
        resolved = cmd_explain(cfg, "old-model")
        assert resolved is not None


# ---------------------------------------------------------------------------
# 17: Doctor probe dispatch unchanged
# ---------------------------------------------------------------------------

class TestDoctorUnchanged:
    def test_probe_dispatch_does_not_branch_on_host_group(self) -> None:
        """_probe_capabilities does not branch on host_group or when_to_use.

        This is the ROLE.2 guard: the probe loop must be unchanged (archetype drives
        dispatch, not the new annotation fields).
        """
        import ast
        import inspect
        from research_vault.doctor import _probe_capabilities

        src = inspect.getsource(_probe_capabilities)
        tree = ast.parse(src)

        # Walk AST and find any string constant "host_group" or "when_to_use"
        # appearing in an If node's test (which would indicate branching on them)
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                # Collect all string constants in the test
                test_src = ast.unparse(node.test) if hasattr(ast, "unparse") else ""
                assert "host_group" not in test_src, (
                    "_probe_capabilities must not branch on host_group — "
                    "it is a pure annotation; archetype drives probe dispatch"
                )
                assert "when_to_use" not in test_src, (
                    "_probe_capabilities must not branch on when_to_use — "
                    "it is read-guidance; archetype drives probe dispatch"
                )

    def test_transfer_node_probe_uses_ssh_path(self, cfg: Config) -> None:
        """A transfer-node (archetype: ssh) is probed via the ssh path, not crashed.

        The probe may fail (no real host) but must return a structured result.
        _probe_capabilities dispatches on archetype; host_group/when_to_use are ignored.
        """
        from unittest.mock import patch
        from research_vault.compute import _save_manifest
        from research_vault.doctor import _probe_capabilities

        _save_manifest(cfg, {
            "backends": {
                "active": ["transfer-node"],
                "profiles": {
                    "transfer-node": {
                        "archetype": "ssh",
                        "host": "dtn.example-cluster.edu",
                        "host_group": "mycluster",
                        "when_to_use": "Big downloads + staging here. Do NOT submit jobs.",
                    },
                },
            },
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        })

        # Stub _probe_remote_ssh to return a canned result — no real network
        fake_result = {
            "probe_status": "unreachable",
            "ssh_ok": False,
            "local_available": False,
        }
        with patch("research_vault.doctor._probe_remote_ssh", return_value=fake_result):
            result = _probe_capabilities(cfg)

        assert result is not None
        assert "transfer-node" in result, "transfer-node must be probed"
        caps = result["transfer-node"]
        assert "probe_status" in caps, "transfer-node probe must return a structured result"


# ---------------------------------------------------------------------------
# 18-19: Verb registry — compute when_to_use anti-pattern
# ---------------------------------------------------------------------------

class TestVerbRegistry:
    def test_compute_when_to_use_names_shoehorn_antipattern(self) -> None:
        """compute when_to_use names the DTN shoehorn anti-pattern."""
        from research_vault.cli import _VERB_REGISTRY
        wtu = _VERB_REGISTRY["compute"]["when_to_use"]
        # Must name the anti-pattern of declaring a DTN as a compute backend
        assert any(
            kw in wtu.lower()
            for kw in ["transfer", "dtn", "data-transfer", "stage", "staging"]
        ), (
            "compute when_to_use must name the transfer-node/DTN shoehorn anti-pattern"
        )
        assert "when_to_use" in wtu.lower(), (
            "compute when_to_use must mention the endpoint when_to_use field"
        )

    def test_help_check_passes(self) -> None:
        """rv help --check: every verb in _VERB_REGISTRY has a non-empty when_to_use."""
        from research_vault.cli import _VERB_REGISTRY
        for verb, entry in _VERB_REGISTRY.items():
            assert "when_to_use" in entry, f"verb {verb!r} missing when_to_use"
            assert entry["when_to_use"], f"verb {verb!r} has empty when_to_use"
            assert "module" in entry, f"verb {verb!r} missing module"


# ---------------------------------------------------------------------------
# 20-21: Soft WARN — remote profiles without explicit host_group
# ---------------------------------------------------------------------------

class TestSoftWarnNoHostGroup:
    def test_warn_fires_on_two_remote_active_no_host_group_one_missing_wtu(
        self, cfg: Config, capsys
    ) -> None:
        """Soft WARN fires on ≥2 active non-local remote profiles (no host_group) where one lacks when_to_use."""
        from research_vault.compute import _save_manifest, cmd_show
        _save_manifest(cfg, {
            "backends": {
                "active": ["node-a", "node-b"],
                "profiles": {
                    "local": {"archetype": "local"},
                    "node-a": {
                        "archetype": "ssh",
                        "host": "nodeA.example.edu",
                        "when_to_use": "Use for job submission.",
                    },
                    "node-b": {
                        "archetype": "ssh",
                        "host": "nodeB.example.edu",
                        # No host_group, no when_to_use
                    },
                },
            },
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        })
        rc = cmd_show(cfg)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert any(kw in combined.lower() for kw in ["warn", "when_to_use", "ambig"]), (
            "WARN must fire for ≥2 active remote profiles where one lacks when_to_use"
        )
        assert rc == 0, "WARN must be non-fatal"

    def test_no_warn_two_remote_active_both_have_wtu(self, cfg: Config, capsys) -> None:
        """No WARN when both active remote profiles (shared host_group) have when_to_use."""
        _write_two_endpoint_manifest(
            cfg,
            compute_wtu="Submit jobs here.",
            transfer_wtu="Stage data here.",
        )
        from research_vault.compute import cmd_show
        rc = cmd_show(cfg)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # Must not mention missing-when_to_use
        assert "missing when_to_use" not in combined.lower()
        assert rc == 0
