"""test_sr_hardening.py — SR-HARDENING: 3 targeted fixes from #34/#35 gate findings.

Covers:
  Fix 1  — native_env value guard: space/comma/semicolon/quote in env value →
            loud ValueError, not silent corruption (adapters/remote.py)
  Fix 2  — container + native_env flag ordering: --export/--chdir land BEFORE
            the apptainer/singularity invocation, not after (adapters/remote.py)
  Fix 3a — config-time slug-collision guard: registering a project slug that
            matches an OKF type name → ValueError (config.py)
  Fix 3b — note.py OKF_SHARED_TYPES self-consumption: routing sites consume
            OKF_SHARED_TYPES SSOT instead of hardcoded "datasets" (note.py)

All hermetic: mocked subprocess; no real ssh/cluster; no ~/vault reads.
Leakage-clean: all host names are example.com aliases only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config, reset_config_cache


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path, backend: str = "slurm",
              projects: dict | None = None) -> Config:
    raw: dict[str, Any] = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": backend, "secrets": "env"},
        "projects": projects or {},
    }
    cfg = Config(raw)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def _write_manifest(cfg: Config, manifest: dict[str, Any]) -> None:
    from research_vault.compute import MANIFEST_FILE
    p = cfg.state_dir / MANIFEST_FILE
    p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _slurm_manifest_native_env(host: str = "example-cluster",
                                container: dict | None = None) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "archetype": "ssh+slurm",
        "host": host,
        "submit_pattern": "sbatch --partition=gpu",
        "jobid_parse": r"Submitted batch job (\d+)",
        "native_env": True,
    }
    if container:
        profile["container"] = container
    return {
        "backends": {
            "active": ["slurm-cluster"],
            "profiles": {"slurm-cluster": profile},
        },
        "conda_envs": {},
        "gpu_tiers": {},
        "rules": [],
        "model_quirks": {},
        "run_outcomes": [],
    }


def _mock_run(stdout: str = "Submitted batch job 1\n",
              returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.stderr = ""
    m.returncode = returncode
    return m


# ---------------------------------------------------------------------------
# Fix 1 — native_env value guard
# ---------------------------------------------------------------------------

class TestNativeEnvValueGuard:
    """native_env mode rejects env values containing unsafe characters loudly."""

    @pytest.fixture
    def rb_native(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, _slurm_manifest_native_env())
        from research_vault.adapters.remote import RemoteBackend
        return RemoteBackend(cfg)

    @pytest.mark.parametrize("match_text,bad_value", [
        ("space",     "val with space"),
        ("comma",     "val,with,comma"),
        ("semicolon", "val;injection"),
        ("quote",     "val'with'quote"),
        ("quote",     'val"with"dquote'),
    ])
    def test_bad_env_value_raises_value_error(self, rb_native, match_text, bad_value):
        """native_env mode with a dangerous env value raises ValueError loudly."""
        with pytest.raises(ValueError, match=match_text):
            with patch("subprocess.run", return_value=_mock_run()):
                rb_native.submit(["python", "run.py"], env={"MY_VAR": bad_value})

    def test_safe_env_value_passes_through(self, tmp_path):
        """native_env mode with a safe value does not raise."""
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, _slurm_manifest_native_env())
        from research_vault.adapters.remote import RemoteBackend
        rb = RemoteBackend(cfg)
        with patch("subprocess.run", return_value=_mock_run()) as mock_run:
            rb.submit(["python", "run.py"], env={"KEY": "safe-value_123"})
        call_argv = mock_run.call_args[0][0]
        # --export must be present
        export_flags = [a for a in call_argv if a.startswith("--export=")]
        assert export_flags, "--export flag must appear for safe native_env values"
        assert "KEY=safe-value_123" in export_flags[0]

    def test_native_env_false_does_not_check(self, tmp_path):
        """native_env: false falls back to sh -c and does NOT apply the guard."""
        # Build a manifest WITHOUT native_env (defaults to false)
        manifest = {
            "backends": {
                "active": ["slurm-cluster"],
                "profiles": {
                    "slurm-cluster": {
                        "archetype": "ssh+slurm",
                        "host": "example-cluster",
                        "submit_pattern": "sbatch",
                        "jobid_parse": r"Submitted batch job (\d+)",
                        # no native_env key → defaults to False
                    }
                },
            },
            "conda_envs": {}, "gpu_tiers": {}, "rules": [],
            "model_quirks": {}, "run_outcomes": [],
        }
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, manifest)
        from research_vault.adapters.remote import RemoteBackend
        rb = RemoteBackend(cfg)
        # A value with a comma in non-native mode must NOT raise (sh -c quotes it)
        with patch("subprocess.run", return_value=_mock_run()):
            rb.submit(["myapp"], env={"KEY": "val,with,comma"})


# ---------------------------------------------------------------------------
# Fix 2 — container + native_env flag ordering
# ---------------------------------------------------------------------------

class TestContainerNativeEnvOrdering:
    """--export/--chdir must appear BEFORE the container runtime in ssh_argv."""

    @pytest.fixture
    def rb_container_native(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        container = {"runtime": "apptainer", "image": "/img/myenv.sif"}
        _write_manifest(cfg, _slurm_manifest_native_env(container=container))
        from research_vault.adapters.remote import RemoteBackend
        return RemoteBackend(cfg)

    def test_export_before_container_invocation(self, rb_container_native):
        """--export= appears before 'apptainer' in the ssh argv."""
        with patch("subprocess.run", return_value=_mock_run()) as mock_run:
            rb_container_native.submit(
                ["python", "train.py"],
                env={"EPOCHS": "10"},
            )
        argv = mock_run.call_args[0][0]
        assert "apptainer" in argv, "container runtime must be present"
        appt_idx = argv.index("apptainer")
        export_flags = [i for i, a in enumerate(argv) if a.startswith("--export=")]
        assert export_flags, "--export= must be present"
        export_idx = export_flags[0]
        assert export_idx < appt_idx, (
            f"--export= (idx {export_idx}) must come BEFORE apptainer (idx {appt_idx}); "
            f"full argv: {argv}"
        )

    def test_chdir_before_container_invocation(self, rb_container_native):
        """--chdir= appears before 'apptainer' in the ssh argv."""
        with patch("subprocess.run", return_value=_mock_run()) as mock_run:
            rb_container_native.submit(
                ["python", "train.py"],
                cwd="/remote/workdir",
            )
        argv = mock_run.call_args[0][0]
        appt_idx = argv.index("apptainer")
        chdir_flags = [i for i, a in enumerate(argv) if a.startswith("--chdir=")]
        assert chdir_flags, "--chdir= must be present"
        chdir_idx = chdir_flags[0]
        assert chdir_idx < appt_idx, (
            f"--chdir= (idx {chdir_idx}) must come BEFORE apptainer (idx {appt_idx}); "
            f"full argv: {argv}"
        )

    def test_container_before_double_dash(self, rb_container_native):
        """Container runtime appears BEFORE '--' (cmd separator)."""
        with patch("subprocess.run", return_value=_mock_run()) as mock_run:
            rb_container_native.submit(["python", "run.py"], env={"X": "1"})
        argv = mock_run.call_args[0][0]
        appt_idx = argv.index("apptainer")
        dash_idx = argv.index("--")
        assert appt_idx < dash_idx, (
            "apptainer must appear before the '--' cmd separator"
        )

    def test_container_no_native_env_unaffected(self, tmp_path):
        """container without native_env still works (regression guard)."""
        # Plain container, no native_env
        manifest = {
            "backends": {
                "active": ["slurm-cluster"],
                "profiles": {
                    "slurm-cluster": {
                        "archetype": "ssh+slurm",
                        "host": "example-cluster",
                        "submit_pattern": "sbatch",
                        "jobid_parse": r"Submitted batch job (\d+)",
                        "container": {"runtime": "apptainer", "image": "/img/env.sif"},
                    }
                },
            },
            "conda_envs": {}, "gpu_tiers": {}, "rules": [],
            "model_quirks": {}, "run_outcomes": [],
        }
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, manifest)
        from research_vault.adapters.remote import RemoteBackend
        rb = RemoteBackend(cfg)
        with patch("subprocess.run", return_value=_mock_run()) as mock_run:
            rb.submit(["myapp"])
        argv = mock_run.call_args[0][0]
        assert "apptainer" in argv
        assert "--" in argv
        appt_idx = argv.index("apptainer")
        dash_idx = argv.index("--")
        assert appt_idx < dash_idx


# ---------------------------------------------------------------------------
# Fix 3a — config-time slug-collision guard
# ---------------------------------------------------------------------------

class TestSlugCollisionGuard:
    """Registering a project slug that collides with an OKF type name is rejected."""

    @pytest.mark.parametrize("reserved_slug", [
        "datasets",
        "experiments",
        "literature",
        "concepts",
        "methodology",
        "findings",
        "mocs",
        # SR-RM-FIGMS: figures and manuscript removed from OKF_TYPES
    ])
    def test_reserved_slug_raises_value_error(self, tmp_path, reserved_slug):
        """Config raises ValueError when a project slug matches an OKF type name."""
        raw: dict[str, Any] = {
            "instance_root": str(tmp_path),
            "notes_root": str(tmp_path / "notes"),
            "state_dir": str(tmp_path / "state"),
            "agents_dir": str(tmp_path / ".agents"),
            "tasks_dir": str(tmp_path / "tasks"),
            "control_dir": str(tmp_path / "control"),
            "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
            "projects": {
                reserved_slug: {
                    "source_dir": str(tmp_path / "projects" / reserved_slug),
                }
            },
        }
        with pytest.raises(ValueError, match=reserved_slug):
            Config(raw)

    def test_normal_slug_accepted(self, tmp_path):
        """A slug that doesn't collide with OKF types is accepted normally."""
        raw: dict[str, Any] = {
            "instance_root": str(tmp_path),
            "notes_root": str(tmp_path / "notes"),
            "state_dir": str(tmp_path / "state"),
            "agents_dir": str(tmp_path / ".agents"),
            "tasks_dir": str(tmp_path / "tasks"),
            "control_dir": str(tmp_path / "control"),
            "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
            "projects": {
                "my-research": {"source_dir": str(tmp_path / "projects" / "my-research")},
                "eval-2024": {"source_dir": str(tmp_path / "projects" / "eval-2024")},
            },
        }
        cfg = Config(raw)
        assert "my-research" in cfg.projects
        assert "eval-2024" in cfg.projects

    def test_reserved_slugs_derived_from_note_ssot_not_hardcoded(self):
        """config.py must NOT have a module-level _OKF_RESERVED_SLUGS constant
        (a hardcoded fork of note.OKF_TYPES ∪ OKF_SHARED_TYPES).

        The guard must consume the live SSOT via a call-time import inside
        Config.__init__, so that a future 10th OKF type added to note.py is
        automatically rejected — no drift possible.

        Red: if _OKF_RESERVED_SLUGS still exists as a module attribute, this fails.
        Green: after removing the constant and wiring the lazy import, it passes.
        """
        import research_vault.config as config_mod
        assert not hasattr(config_mod, "_OKF_RESERVED_SLUGS"), (
            "config._OKF_RESERVED_SLUGS must not exist as a module-level constant — "
            "the slug guard must derive its reserved set from note.OKF_TYPES | "
            "note.OKF_SHARED_TYPES via a call-time import in Config.__init__, "
            "not from a hardcoded fork that can silently drift."
        )

    def test_error_message_mentions_slug(self, tmp_path):
        """The ValueError names both the offending slug and says 'OKF type'."""
        raw: dict[str, Any] = {
            "instance_root": str(tmp_path),
            "notes_root": str(tmp_path / "notes"),
            "state_dir": str(tmp_path / "state"),
            "agents_dir": str(tmp_path / ".agents"),
            "tasks_dir": str(tmp_path / "tasks"),
            "control_dir": str(tmp_path / "control"),
            "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
            "projects": {"datasets": {}},
        }
        with pytest.raises(ValueError) as exc_info:
            Config(raw)
        msg = str(exc_info.value)
        assert "datasets" in msg
        assert "OKF" in msg or "reserved" in msg.lower()


# ---------------------------------------------------------------------------
# Fix 3b — note.py OKF_SHARED_TYPES self-consumption
# ---------------------------------------------------------------------------

class TestOKFSharedTypesSelfConsumption:
    """Routing sites in note.py use OKF_SHARED_TYPES, not the hardcoded string 'datasets'."""

    def test_okf_shared_types_contains_datasets(self):
        """OKF_SHARED_TYPES is defined and contains 'datasets'."""
        from research_vault.note import OKF_SHARED_TYPES
        assert "datasets" in OKF_SHARED_TYPES

    def test_cmd_new_datasets_routes_to_shared_root(self, tmp_instance):
        """cmd_new for 'datasets' still routes to cfg.datasets_root (routing unchanged)."""
        from research_vault.config import load_config
        from research_vault import note as note_mod
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "datasets", "Test Dataset", config=cfg)
        assert path.exists()
        # Must live in datasets_root (shared), not in project_notes_dir/datasets/
        assert path.parent == cfg.datasets_root

    def test_cmd_list_datasets_scans_shared_root(self, tmp_instance):
        """cmd_list for 'datasets' scans cfg.datasets_root (routing unchanged)."""
        from research_vault.config import load_config
        from research_vault import note as note_mod
        cfg = load_config(reload=True)
        # Create a note in the shared root
        note_mod.cmd_new("demo-research", "datasets", "Shared Dataset", config=cfg)
        # List should find it
        notes = note_mod.cmd_list("demo-research", "datasets", config=cfg)
        assert len(notes) == 1
        assert notes[0]["fields"].get("type") == "datasets"

    def test_cmd_check_datasets_reports_no_violation_for_valid_note(self, tmp_instance):
        """cmd_check for a valid datasets note (location+hash filled) returns no violations."""
        from research_vault.config import load_config
        from research_vault import note as note_mod
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "datasets", "Valid Dataset", config=cfg)
        # Fill required fields
        content = path.read_text()
        content = content.replace("location: ", "location: /data/file.csv")
        content = content.replace("hash: ", "hash: sha256:abc123")
        path.write_text(content)
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert not violations, f"Unexpected violations: {violations}"

    def test_cmd_new_non_shared_type_routes_to_project_dir(self, tmp_instance):
        """cmd_new for 'findings' routes to project_notes_dir/findings/ (regression guard)."""
        from research_vault.config import load_config
        from research_vault import note as note_mod
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "findings", "A Finding", config=cfg)
        assert path.exists()
        expected_parent = cfg.project_notes_dir("demo-research") / "findings"
        assert path.parent == expected_parent

    def test_routing_condition_uses_membership_not_equality(self):
        """The if-condition that routes notes to a shared root must use
        `in OKF_SHARED_TYPES`, never `== "datasets"` (a hardcoded string comparison).

        0.3.2 added a second shared type (concepts), so the routing sites now
        dispatch to `cfg.shared_type_root(t)` (a per-type lookup) instead of the
        single hardcoded `cfg.datasets_root` attribute access. This test detects
        the (now more general) `if X: <var> = cfg.shared_type_root(...)` shape.

        Strategy: parse each function with AST, find every such routing node,
        extract ONLY the condition source via ast.get_source_segment (comment-free
        by definition — AST nodes carry no comment text), then assert the
        hardcoded equality pattern is absent.

        Non-vacuousness: this test FAILS if any routing branch is reverted to
        `== "datasets"` — comments containing 'OKF_SHARED_TYPES' cannot rescue it
        because we assert the NEGATIVE on the comment-free condition segment, not the
        positive on raw getsource (which was the previous vacuous approach).
        """
        import ast
        import inspect
        import textwrap
        from research_vault import note as note_mod

        def _routing_if_conditions(func):
            """Return comment-free source segments of if-conditions that route to
            cfg.shared_type_root(...) (i.e. `if X: <something> = cfg.shared_type_root(t)`)."""
            src = inspect.getsource(func)
            src = textwrap.dedent(src)
            tree = ast.parse(src)
            found = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.If):
                    continue
                for stmt in node.body:
                    if not isinstance(stmt, ast.Assign):
                        continue
                    val = stmt.value
                    if (
                        isinstance(val, ast.Call)
                        and isinstance(val.func, ast.Attribute)
                        and val.func.attr == "shared_type_root"
                        and isinstance(val.func.value, ast.Name)
                        and val.func.value.id == "cfg"
                    ):
                        cond_src = ast.get_source_segment(src, node.test) or ""
                        found.append(cond_src)
            return found

        for func_name, func in [
            ("cmd_new", note_mod.cmd_new),
            ("cmd_list", note_mod.cmd_list),
            ("cmd_check", note_mod.cmd_check),
        ]:
            conditions = _routing_if_conditions(func)
            assert conditions, (
                f"{func_name}: no routing if-condition found — expected at least one "
                f"`if X: <var> = cfg.shared_type_root(...)` block"
            )
            for cond in conditions:
                assert '== "datasets"' not in cond and "== 'datasets'" not in cond, (
                    f"{func_name}: routing condition {cond!r} uses a hardcoded string "
                    f"comparison — must use `in OKF_SHARED_TYPES` instead"
                )
