"""test_compute_wizard.py — the guided `run_compute_wizard` flow + 6 safety asserts.

The wizard mutates ONLY the compute manifest (at cfg.state_dir), reads ~/.ssh/config
strictly read-only, and never writes before the explicit [y/N] confirm.
"""
from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path):
    """Build a real Config whose state_dir is under tmp_path (F7-safe injection)."""
    from research_vault.config import Config, _default_config, _expand_paths

    root = tmp_path / "instance"
    root.mkdir(parents=True, exist_ok=True)
    raw = _default_config()
    raw["instance_root"] = str(root)
    raw = _expand_paths(raw, root)
    cfg = Config(raw)
    return cfg


def _queue_input(answers):
    """input_fn that pops answers in order; blank on exhaustion (accept default/skip)."""
    it = iter(list(answers))

    def _input(_prompt):
        try:
            return next(it)
        except StopIteration:
            return ""

    return _input


def _no_scheduler(_name):
    return None


def _ssh_config(tmp_path, body):
    cfg = tmp_path / "ssh_config"
    cfg.write_text(body, encoding="utf-8")
    return cfg


# Answer sequence for a full happy path: configure one ssh+slurm compute-node
# using detected alias "sc", accept templates/defaults, add W&B, confirm write.
_HAPPY = [
    "y",        # Configure a compute endpoint now?
    "2",        # archetype: ssh+slurm
    "1",        # role: compute-node
    "",         # when_to_use: accept template
    "1",        # host: pick alias #1 (sc)
    "",         # submit: accept default
    "n",        # Configure another endpoint? no
    "myent",    # W&B entity (project is no longer prompted — auto per-run slug)
    "y",        # Write this manifest? yes
]


# ---------------------------------------------------------------------------
# Happy path — manifest assembled + written to cfg.state_dir
# ---------------------------------------------------------------------------

def test_happy_path_writes_manifest_to_state_dir(tmp_path, capsys):
    from research_vault.compute_wizard import run_compute_wizard
    from research_vault.compute import _manifest_path

    cfg = _make_cfg(tmp_path)
    ssh = _ssh_config(tmp_path, "Host sc\n    HostName login.cluster.edu\n    User alice\n")

    rc = run_compute_wizard(
        cfg, interactive=True,
        input_fn=_queue_input(_HAPPY),
        ssh_config_path=ssh, which_fn=_no_scheduler, env={},
    )
    assert rc == 0

    path = _manifest_path(cfg)
    assert path.exists(), "manifest must land at cfg.state_dir/compute_manifest.json"
    m = json.loads(path.read_text())
    profiles = m["backends"]["profiles"]
    assert "compute-node" in profiles
    cn = profiles["compute-node"]
    assert cn["archetype"] == "ssh+slurm"
    # host stores the ALIAS, never the HostName.
    assert cn["host"] == "sc"
    assert cn["host"] != "login.cluster.edu"
    assert cn["submit_pattern"].startswith("sbatch")
    assert cn["secrets_forward"] == ["WANDB_API_KEY"]
    assert "compute-node" in m["backends"]["active"]
    assert m["results"]["wandb"] == {"entity": "myent"}


def test_archetype_preselect_from_scheduler(tmp_path):
    """which_fn finding sbatch preselects ssh+slurm (blank archetype answer)."""
    from research_vault.compute_wizard import run_compute_wizard
    from research_vault.compute import _manifest_path

    cfg = _make_cfg(tmp_path)
    ssh = _ssh_config(tmp_path, "Host sc\n")

    def which_sbatch(name):
        return "/usr/bin/sbatch" if name == "sbatch" else None

    answers = [
        "y",   # configure endpoint
        "",    # archetype: accept preselected (ssh+slurm)
        "1",   # role compute-node
        "",    # when_to_use
        "1",   # host sc
        "",    # submit default
        "n",   # another? no
        "",    # wandb entity blank
        "y",   # write
    ]
    run_compute_wizard(
        cfg, interactive=True, input_fn=_queue_input(answers),
        ssh_config_path=ssh, which_fn=which_sbatch, env={},
    )
    m = json.loads(_manifest_path(cfg).read_text())
    assert m["backends"]["profiles"]["compute-node"]["archetype"] == "ssh+slurm"


def test_wandb_prefill_from_env(tmp_path):
    from research_vault.compute_wizard import run_compute_wizard
    from research_vault.compute import _manifest_path

    cfg = _make_cfg(tmp_path)
    ssh = _ssh_config(tmp_path, "Host sc\n")
    answers = [
        "y", "2", "1", "", "1", "", "n",
        "",  # accept env entity prefill (blank; project is no longer prompted)
        "y",
    ]
    run_compute_wizard(
        cfg, interactive=True, input_fn=_queue_input(answers),
        ssh_config_path=ssh, which_fn=_no_scheduler,
        env={"WANDB_ENTITY": "envent", "WANDB_PROJECT": "envproj"},
    )
    m = json.loads(_manifest_path(cfg).read_text())
    # project is NOT scaffolded even when WANDB_PROJECT is set in the shell env —
    # it defaults per-run to the project slug via resolve_run_logging_target.
    assert m["results"]["wandb"] == {"entity": "envent"}


# ---------------------------------------------------------------------------
# SAFETY ASSERT #4 — Confirm=No / EOF ⇒ nothing written
# ---------------------------------------------------------------------------

def test_confirm_no_writes_nothing(tmp_path):
    from research_vault.compute_wizard import run_compute_wizard
    from research_vault.compute import _manifest_path

    cfg = _make_cfg(tmp_path)
    ssh = _ssh_config(tmp_path, "Host sc\n")
    answers = list(_HAPPY[:-1]) + ["n"]  # decline the write
    rc = run_compute_wizard(
        cfg, interactive=True, input_fn=_queue_input(answers),
        ssh_config_path=ssh, which_fn=_no_scheduler, env={},
    )
    assert rc == 0
    assert not _manifest_path(cfg).exists(), "declining the confirm must write nothing"


def test_eof_mid_flow_writes_nothing(tmp_path):
    from research_vault.compute_wizard import run_compute_wizard
    from research_vault.compute import _manifest_path

    cfg = _make_cfg(tmp_path)
    ssh = _ssh_config(tmp_path, "Host sc\n")

    def eof_input(_prompt):
        raise EOFError()

    rc = run_compute_wizard(
        cfg, interactive=True, input_fn=eof_input,
        ssh_config_path=ssh, which_fn=_no_scheduler, env={},
    )
    assert rc == 0
    assert not _manifest_path(cfg).exists(), "EOF abort must write nothing"


# ---------------------------------------------------------------------------
# SAFETY ASSERT #5 — non-TTY never mutates without confirm
# ---------------------------------------------------------------------------

def test_non_interactive_detects_but_never_mutates(tmp_path, capsys):
    from research_vault.compute_wizard import run_compute_wizard
    from research_vault.compute import _manifest_path

    cfg = _make_cfg(tmp_path)
    ssh = _ssh_config(tmp_path, "Host sc\n    HostName s.edu\n")

    def boom_input(_prompt):
        raise AssertionError("non-interactive path must NEVER prompt")

    rc = run_compute_wizard(
        cfg, interactive=False, input_fn=boom_input,
        ssh_config_path=ssh, which_fn=_no_scheduler, env={},
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "sc" in out, "detected alias should be displayed"
    assert not _manifest_path(cfg).exists(), "non-interactive must not write the manifest"


# ---------------------------------------------------------------------------
# SAFETY ASSERT #2 — the manifest is the ONLY mutation
# ---------------------------------------------------------------------------

def _install_write_spy(monkeypatch, writes: list[str]):
    """Record every write-mode target across builtins.open AND pathlib.Path.

    ``Path.write_text`` bypasses ``builtins.open`` (it goes through ``io.open``),
    so we must patch the pathlib methods too — otherwise the spy is vacuous.
    """
    real_open = builtins.open
    real_wt = Path.write_text
    real_wb = Path.write_bytes
    real_popen = Path.open

    def spy_open(file, mode="r", *args, **kwargs):
        if any(m in mode for m in ("w", "a", "x", "+")):
            writes.append(str(Path(str(file)).resolve()))
        return real_open(file, mode, *args, **kwargs)

    def spy_wt(self, *a, **kw):
        writes.append(str(self.resolve()))
        return real_wt(self, *a, **kw)

    def spy_wb(self, *a, **kw):
        writes.append(str(self.resolve()))
        return real_wb(self, *a, **kw)

    def spy_popen(self, mode="r", *a, **kw):
        if any(m in mode for m in ("w", "a", "x", "+")):
            writes.append(str(self.resolve()))
        return real_popen(self, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(Path, "write_text", spy_wt)
    monkeypatch.setattr(Path, "write_bytes", spy_wb)
    monkeypatch.setattr(Path, "open", spy_popen)


def test_only_manifest_path_is_written(tmp_path, monkeypatch):
    from research_vault import compute_wizard
    from research_vault.compute import _manifest_path

    cfg = _make_cfg(tmp_path)
    ssh = _ssh_config(tmp_path, "Host sc\n")
    manifest_path = str(_manifest_path(cfg).resolve())

    writes: list[str] = []
    _install_write_spy(monkeypatch, writes)
    compute_wizard.run_compute_wizard(
        cfg, interactive=True, input_fn=_queue_input(_HAPPY),
        ssh_config_path=ssh, which_fn=_no_scheduler, env={},
    )
    assert writes, "the manifest should have been written"
    for w in writes:
        assert w == manifest_path, f"unexpected write to {w} (only the manifest may be written)"


# ---------------------------------------------------------------------------
# SAFETY ASSERT #1 — ~/.ssh/config opened read-only, bytes+mtime stable
# ---------------------------------------------------------------------------

def test_ssh_config_untouched_bytes_and_mtime(tmp_path, monkeypatch):
    from research_vault import compute_wizard

    ssh_dir = tmp_path / ".ssh"
    (ssh_dir / "conf.d").mkdir(parents=True)
    (ssh_dir / "conf.d" / "x.conf").write_text("Host xx\n", encoding="utf-8")
    ssh = ssh_dir / "config"
    ssh.write_text("Include conf.d/*.conf\nHost sc\n    HostName s.edu\n", encoding="utf-8")
    cfg = _make_cfg(tmp_path)

    before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in ssh_dir.rglob("*") if p.is_file()}

    # Spy across builtins.open AND pathlib writes; fail if any target is under the
    # ssh dir (path-scoped, since Include is followed).
    writes: list[str] = []
    _install_write_spy(monkeypatch, writes)
    compute_wizard.run_compute_wizard(
        cfg, interactive=True, input_fn=_queue_input(_HAPPY),
        ssh_config_path=ssh, which_fn=_no_scheduler, env={},
    )
    ssh_dir_resolved = ssh_dir.resolve()
    for w in writes:
        target = Path(w)
        under = ssh_dir_resolved == target or ssh_dir_resolved in target.parents
        assert not under, f"write under ssh dir: {w}"
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns)
             for p in ssh_dir.rglob("*") if p.is_file()}
    assert before == after, "ssh-config dir must be byte/mtime identical after a full run"


# ---------------------------------------------------------------------------
# SAFETY ASSERT #3 — malformed / cyclic / missing ssh config ⇒ no crash
# ---------------------------------------------------------------------------

def test_malformed_ssh_config_no_crash(tmp_path):
    from research_vault.compute_wizard import run_compute_wizard
    from research_vault.compute import _manifest_path

    cfg = _make_cfg(tmp_path)
    ssh = tmp_path / "ssh_config"
    ssh.write_bytes(b"\xff\xfe garbage Host \x00 not valid ==== \n")

    # No aliases detected → host step falls to "blank to skip".
    answers = ["y", "4", "2", "", "", "n", "", "y"]  # archetype ssh, role transfer-node, skip host
    rc = run_compute_wizard(
        cfg, interactive=True, input_fn=_queue_input(answers),
        ssh_config_path=ssh, which_fn=_no_scheduler, env={},
    )
    assert rc == 0
    # Wrote a manifest (host left as FILL sentinel since skipped).
    m = json.loads(_manifest_path(cfg).read_text())
    tn = m["backends"]["profiles"]["transfer-node"]
    assert str(tn["host"]).startswith("FILL")
    # Unwired host ⇒ not in active.
    assert "transfer-node" not in m["backends"]["active"]


def test_missing_ssh_config_no_crash(tmp_path):
    from research_vault.compute_wizard import run_compute_wizard

    cfg = _make_cfg(tmp_path)
    rc = run_compute_wizard(
        cfg, interactive=False, input_fn=None,
        ssh_config_path=tmp_path / "does-not-exist", which_fn=_no_scheduler, env={},
    )
    assert rc == 0


# ---------------------------------------------------------------------------
# Re-runnability — REMOVE unwires (resets host to FILL), manifest-only
# ---------------------------------------------------------------------------

def test_rerun_remove_unwires_endpoint(tmp_path):
    from research_vault.compute_wizard import run_compute_wizard
    from research_vault.compute import _manifest_path, _save_manifest, _load_manifest

    cfg = _make_cfg(tmp_path)
    # Pre-seed a configured compute-node.
    m = _load_manifest(cfg)
    m["backends"]["profiles"]["compute-node"] = {
        "archetype": "ssh+slurm", "host": "sc", "submit_pattern": "sbatch ...",
    }
    m["backends"]["active"] = ["local", "compute-node"]
    _save_manifest(cfg, m)

    ssh = _ssh_config(tmp_path, "Host sc\n")
    answers = [
        "y",   # Unwire an existing endpoint? yes
        "1",   # pick compute-node
        "n",   # Configure a compute endpoint now? no
        "",    # wandb entity blank
        "y",   # write
    ]
    run_compute_wizard(
        cfg, interactive=True, input_fn=_queue_input(answers),
        ssh_config_path=ssh, which_fn=_no_scheduler, env={},
    )
    m2 = json.loads(_manifest_path(cfg).read_text())
    assert str(m2["backends"]["profiles"]["compute-node"]["host"]).startswith("FILL")
    assert "compute-node" not in m2["backends"]["active"]


# ---------------------------------------------------------------------------
# SAFETY ASSERT #6 — F7 regression: driven via onboard, manifest lands at the
# injected cfg.state_dir (proves _save_manifest(cfg, …), no stale-cfg re-entry).
# ---------------------------------------------------------------------------

def test_f7_onboard_manifest_lands_at_injected_state_dir(tmp_path, monkeypatch):
    from research_vault.onboard import cmd_onboard
    from research_vault.compute import _manifest_path

    cfg = _make_cfg(tmp_path)
    # Provider/key prompts start with "Add ..." → decline; compute prompts pop the queue.
    compute_answers = iter([
        "y",   # Configure a compute endpoint now?
        "1",   # archetype: local (no host/ssh dependency — deterministic)
        "",    # when_to_use: accept
        "n",   # another endpoint? no
        "onbent",  # W&B entity (project is no longer prompted)
        "y",   # write
    ])

    def onboard_input(prompt):
        if prompt.strip().startswith("Add"):
            return "n"
        try:
            return next(compute_answers)
        except StopIteration:
            return ""

    from unittest.mock import patch
    with patch("shutil.which", return_value="/usr/bin/claude"):
        rc = cmd_onboard(
            cfg, assume_tty=True,
            input_fn=onboard_input, getpass_fn=lambda q: "",
        )
    assert rc == 0
    path = _manifest_path(cfg)
    assert path.exists(), "F7: manifest must land at the injected cfg.state_dir"
    # It landed under tmp_path, not some stale/default location.
    assert str(tmp_path) in str(path.resolve())
    m = json.loads(path.read_text())
    assert m["results"]["wandb"] == {"entity": "onbent"}
