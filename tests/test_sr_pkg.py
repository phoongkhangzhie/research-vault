"""test_sr_pkg.py — SR-PKG: packaging-data fix acceptance tests.

These tests prove the core guarantee of SR-PKG: a pip/uv wheel-installed
research-vault package correctly scaffolds REAL doctrine + REAL multi-node loop
DAGs via rv init, with no __file__-based repo-root fallbacks.

Two test layers:

A. Unit tests (run in the regular pytest suite):
   - The three loaders resolve to paths inside the installed package.
   - rv init into a tmpdir copies REAL doctrine (>1 file, not just the skeleton
     README) and REAL loop manifests (multiple nodes, no placeholder node).

B. Isolated wheel smoke test (load-bearing acceptance; marked slow):
   - uv build + fresh isolated venv + rv init from OUTSIDE the repo + assert
     real doctrine + real multi-node loops.
   - This test MUST fail on the pre-fix __file__ code and PASS after the fix.
     A test that passes on both is vacuous and proves nothing.
   - Run with: pytest -m slow tests/test_sr_pkg.py
   - CI wires this as a standing job (SR-META).

Execution notes:
  - All tests are hermetic (tmp dirs, no ~/vault, no network).
  - The slow marker requires 'uv' on PATH (skips if absent).
"""
from __future__ import annotations

import importlib.resources
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# A. Unit tests
# ---------------------------------------------------------------------------

class TestLoaderResolvesInsidePackage:
    """After SR-PKG, the data loaders resolve under the installed package,
    not under an arbitrary repo root."""

    def test_data_root_resolves_via_importlib(self):
        """importlib.resources.files('research_vault') / 'data' resolves
        to a directory that exists inside the package."""
        pkg_data = importlib.resources.files("research_vault") / "data"
        with importlib.resources.as_file(pkg_data) as p:
            assert p.is_dir(), (
                f"Package data root must exist as a directory. Got: {p}"
            )

    def test_doctrine_is_inside_package(self):
        """data/doctrine/ resolves UNDER the installed package, not repo root."""
        pkg_data = importlib.resources.files("research_vault") / "data"
        with importlib.resources.as_file(pkg_data / "doctrine") as doctrine_path:
            assert doctrine_path.is_dir(), (
                f"data/doctrine/ must exist inside the installed package. Got: {doctrine_path}"
            )
            # Must be under src/research_vault/ (editable) or site-packages/research_vault/
            # NOT at repo_root/doctrine/
            repo_root_doctrine = REPO_ROOT / "doctrine"
            assert doctrine_path != repo_root_doctrine, (
                f"doctrine path must NOT be the repo-root doctrine/. "
                f"Got {doctrine_path} — this is the __file__-based fallback, not the package data."
            )

    def test_examples_is_inside_package(self):
        """data/examples/ resolves UNDER the installed package."""
        pkg_data = importlib.resources.files("research_vault") / "data"
        with importlib.resources.as_file(pkg_data / "examples") as examples_path:
            assert examples_path.is_dir(), (
                f"data/examples/ must exist inside the installed package. Got: {examples_path}"
            )
            repo_root_examples = REPO_ROOT / "examples"
            assert examples_path != repo_root_examples, (
                f"examples path must NOT be the repo-root examples/. "
                f"Got {examples_path} — this is the __file__-based fallback, not the package data."
            )

    def test_templates_is_inside_package(self):
        """data/templates/QUICKSTART.md resolves UNDER the installed package."""
        pkg_data = importlib.resources.files("research_vault") / "data"
        with importlib.resources.as_file(pkg_data / "templates" / "QUICKSTART.md") as qs:
            assert qs.is_file(), (
                f"data/templates/QUICKSTART.md must exist inside the installed package. Got: {qs}"
            )


class TestRvInitCopiesRealContent:
    """After SR-PKG, rv init copies REAL doctrine and REAL loop manifests,
    not skeleton/placeholder fallbacks."""

    def test_init_copies_real_doctrine_not_skeleton(self, tmp_path):
        """rv init must produce doctrine/ with MORE than 1 file.

        The skeleton fallback writes exactly 1 file (README.md). The real
        doctrine has multiple files. If we get 1 file, the fallback fired.
        """
        from research_vault.init import cmd_init_in_dir

        target = tmp_path / "vault-doctrine-test"
        rc = cmd_init_in_dir(str(target))
        assert rc == 0

        doctrine_dst = target / "doctrine"
        assert doctrine_dst.is_dir()

        doctrine_files = list(doctrine_dst.rglob("*.md"))
        assert len(doctrine_files) > 1, (
            f"FAIL: doctrine/ has only {len(doctrine_files)} .md file(s). "
            "Expected >1 — the skeleton fallback (README.md only) must have fired. "
            "This means the doctrine data is not inside the package. "
            "Fix: relocate doctrine/ to src/research_vault/data/doctrine/ "
            "and load via importlib.resources."
        )

        # Also assert a KNOWN doctrine file is present (not just any .md)
        known_files = ["agent-charter.md", "standards.md", "tooling.md"]
        found_known = [
            f for f in doctrine_files
            if f.name in known_files
        ]
        assert found_known, (
            f"FAIL: none of the expected doctrine files {known_files} found in doctrine/. "
            f"Found files: {[f.name for f in doctrine_files]}. "
            "The skeleton fallback does not contain these files."
        )

    def test_init_copies_real_loop_not_placeholder(self, tmp_path):
        """rv init must produce real multi-node loop DAGs, NOT single-node placeholders.

        The placeholder fallback writes a single-node manifest with id='placeholder'.
        The real research loop has many nodes (>4). If we get 1 node or a node with
        id='placeholder', the fallback fired.
        """
        from research_vault.init import cmd_init_in_dir

        target = tmp_path / "vault-loop-test"
        rc = cmd_init_in_dir(str(target))
        assert rc == 0

        research_loop = target / "examples" / "demo-research" / "research-loop.json"
        litreview_loop = target / "examples" / "demo-litreview" / "lit-review-loop.json"

        assert research_loop.exists(), "research-loop.json must be placed by init"
        assert litreview_loop.exists(), "lit-review-loop.json must be placed by init"

        with open(research_loop, encoding="utf-8") as f:
            m1 = json.load(f)
        with open(litreview_loop, encoding="utf-8") as f:
            m2 = json.load(f)

        # Not a placeholder
        assert not any(n["id"] == "placeholder" for n in m1["nodes"]), (
            "FAIL: research-loop.json contains a placeholder node. "
            "The _write_placeholder_manifest fallback must have fired. "
            "Fix: relocate examples/ to src/research_vault/data/examples/ "
            "and load via importlib.resources."
        )
        assert not any(n["id"] == "placeholder" for n in m2["nodes"]), (
            "FAIL: lit-review-loop.json contains a placeholder node."
        )

        # Real DAGs have multiple nodes
        assert len(m1["nodes"]) > 4, (
            f"FAIL: research-loop.json has only {len(m1['nodes'])} nodes. "
            "Expected >4 (the real loop has many). Placeholder has 1."
        )
        assert len(m2["nodes"]) > 4, (
            f"FAIL: lit-review-loop.json has only {len(m2['nodes'])} nodes. Expected >4."
        )

    def test_init_no_fallback_functions_exist(self):
        """After SR-PKG, the fallback functions must be removed from init.py.

        _write_placeholder_manifest masks the data miss with a silent skeleton.
        Its presence proves the fallback was not deleted (charter section 2).
        """
        import research_vault.init as init_mod
        assert not hasattr(init_mod, "_write_placeholder_manifest"), (
            "FAIL: _write_placeholder_manifest still exists in init.py. "
            "Delete it — the function masks package-data misses with silent placeholders."
        )
        # Also check that __file__-based loaders are gone
        assert not hasattr(init_mod, "_package_doctrine_dir"), (
            "FAIL: _package_doctrine_dir still exists in init.py. "
            "It uses __file__-based repo-root paths that miss the wheel."
        )
        assert not hasattr(init_mod, "_package_examples_dir"), (
            "FAIL: _package_examples_dir still exists in init.py."
        )


# ---------------------------------------------------------------------------
# B. Isolated wheel smoke test (load-bearing acceptance)
# ---------------------------------------------------------------------------

def _require_uv() -> str:
    """Return uv path or skip the test."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not on PATH — cannot run isolated wheel smoke test")
    return uv


@pytest.mark.slow
class TestIsolatedWheelSmoke:
    """Build the wheel, install into a FRESH isolated venv (NOT editable),
    run rv init from OUTSIDE the repo, and assert real content.

    This test MUST:
    - FAIL on the pre-fix code (wheel has no data/, skeleton fires)
    - PASS after the fix (wheel has data/doctrine/ + data/examples/, real content copied)

    Wren's warning: if run on a dev tree / editable install / cwd inside the repo,
    it SILENTLY PASSES on broken code. The test MUST use an isolated venv and
    run from OUTSIDE the repo.
    """

    def test_wheel_install_rv_init_copies_real_doctrine_and_loops(self, tmp_path):
        """Full isolated smoke: build + install + rv init from outside repo + assert real content."""
        uv = _require_uv()

        # ── 1. Build the wheel ──────────────────────────────────────────────
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        result = subprocess.run(
            [uv, "build", "--wheel", "--out-dir", str(dist_dir), "--no-progress"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"uv build failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        wheels = list(dist_dir.glob("*.whl"))
        assert wheels, f"No .whl found in {dist_dir} after uv build"
        wheel = max(wheels, key=lambda w: w.stat().st_mtime)

        # ── 2. Create a FRESH isolated venv (NOT editable, NOT repo-adjacent) ─
        venv_dir = tmp_path / "isolated-venv"
        result = subprocess.run(
            [uv, "venv", str(venv_dir), "--python", "3.12"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"uv venv failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

        # ── 3. Install the wheel (no deps; core is stdlib-only) ─────────────
        python_bin = venv_dir / "bin" / "python"
        result = subprocess.run(
            [uv, "pip", "install",
             "--python", str(python_bin),
             "--no-deps",
             str(wheel)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"uv pip install failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

        rv_bin = venv_dir / "bin" / "rv"
        assert rv_bin.exists(), f"rv binary not found at {rv_bin}"

        # ── 4. Run rv init from OUTSIDE the repo ─────────────────────────────
        # cwd is tmp_path (NOT anywhere inside REPO_ROOT).
        # This is the Wren-critical requirement: isolate from the dev tree.
        project_dir = tmp_path / "test-instance"
        result = subprocess.run(
            [str(rv_bin), "init", str(project_dir)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),  # OUTSIDE the repo — critical
        )
        assert result.returncode == 0, (
            f"rv init failed with exit {result.returncode}:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

        # ── 5. Assert REAL doctrine (not skeleton) ───────────────────────────
        doctrine_dir = project_dir / "doctrine"
        assert doctrine_dir.is_dir(), "doctrine/ must exist after rv init"

        doctrine_md_files = list(doctrine_dir.rglob("*.md"))
        assert len(doctrine_md_files) > 1, (
            f"FAIL (smoke): doctrine/ has only {len(doctrine_md_files)} .md file(s). "
            "The skeleton fallback (README.md only) fired — data/doctrine/ is not in the wheel. "
            "After SR-PKG fix, the wheel must include src/research_vault/data/doctrine/."
        )

        known_files = ["agent-charter.md", "standards.md", "tooling.md"]
        found_known = [f for f in doctrine_md_files if f.name in known_files]
        assert found_known, (
            f"FAIL (smoke): none of {known_files} found in installed doctrine/. "
            f"Files present: {[f.name for f in doctrine_md_files]}"
        )

        # ── 6. Assert REAL loop manifests (not placeholder) ─────────────────
        research_loop = project_dir / "examples" / "demo-research" / "research-loop.json"
        litreview_loop = project_dir / "examples" / "demo-litreview" / "lit-review-loop.json"

        assert research_loop.exists(), "research-loop.json must be placed by rv init"
        assert litreview_loop.exists(), "lit-review-loop.json must be placed by rv init"

        with open(research_loop, encoding="utf-8") as f:
            m1 = json.load(f)
        with open(litreview_loop, encoding="utf-8") as f:
            m2 = json.load(f)

        assert not any(n["id"] == "placeholder" for n in m1["nodes"]), (
            "FAIL (smoke): research-loop.json has placeholder node — "
            "_write_placeholder_manifest fallback fired. data/examples/ not in the wheel."
        )
        assert not any(n["id"] == "placeholder" for n in m2["nodes"]), (
            "FAIL (smoke): lit-review-loop.json has placeholder node."
        )
        assert len(m1["nodes"]) > 4, (
            f"FAIL (smoke): research-loop has {len(m1['nodes'])} nodes, expected >4."
        )
        assert len(m2["nodes"]) > 4, (
            f"FAIL (smoke): lit-review-loop has {len(m2['nodes'])} nodes, expected >4."
        )

        # ── 7. Sanity: rv init stdout does NOT mention skeleton or placeholder ─
        combined_output = result.stdout + result.stderr
        assert "skeleton" not in combined_output.lower(), (
            f"FAIL (smoke): 'skeleton' in rv init output — the fallback printed its marker.\n"
            f"Output: {combined_output}"
        )
        assert "placeholder manifests" not in combined_output.lower(), (
            f"FAIL (smoke): 'placeholder manifests' in rv init output — fallback fired.\n"
            f"Output: {combined_output}"
        )
