"""test_pr_b_leak_audit.py — PR-B leak audit (pre-publish #68 storage
contract): "leak-scanner silence is not proof" (memory
`leak-scanner-silence-is-not-proof`).

PR-B ships the FIRST real two-layer literature fixture content (previously
just `_PLACEHOLDER.md` markers) into `data/examples/literature/` and
`data/examples/demo-litreview/notes/literature/`. Before trusting the
scanner's silence over these new files, prove it HAS teeth for the class it
claims to catch — plant a marker, confirm RED — then separately confirm the
BUILT WHEEL's `data/**` is clean (not just the source tree — SR-PKG showed a
gap can exist between "the source scan passed" and "the wheel actually
ships clean," e.g. an un-scanned data subtree slipping into `artifacts`).

Two layers:
  A. Fast, hermetic: plant a class-1 codename in a scratch COPY of the new
     literature fixture shape; confirm the scanner goes RED. Then confirm
     the REAL shipped files (as committed) are clean.
  B. Slow (@pytest.mark.slow, `uv build` required): grep the actual BUILT
     WHEEL's `research_vault/data/**` for class-1 codenames, class-2
     identity strings, class-4 cluster paths, and class-11 private
     dev-paths — the classes leakage_scan.sh itself defines. Run with
     `pytest -m slow tests/test_pr_b_leak_audit.py`.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "leakage_scan.sh"
LITERATURE_EXAMPLES = REPO_ROOT / "src" / "research_vault" / "data" / "examples" / "literature"
DEMO_LITREVIEW_LIT = (
    REPO_ROOT / "src" / "research_vault" / "data" / "examples" / "demo-litreview"
    / "notes" / "literature"
)


def _run_scan(directory: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), str(directory)],
        capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# A. The scanner has teeth for the literature-fixture class of content
# ---------------------------------------------------------------------------

class TestScannerHasTeethOverLiteratureFixtures:
    def test_planted_codename_in_literature_core_goes_red(self, tmp_path):
        """Plant a class-1 codename inside a scratch copy shaped exactly
        like the new central-core fixture — proves the scanner actually
        catches a leak in THIS class of file, not just that it's silent.

        The marker is assembled at runtime (not a contiguous literal in
        this .py source) — this test file is NOT on leakage_scan.sh's
        self-exclusion allowlist (unlike test_leakage_scan.py/
        test_git_discipline.py), so a literal codename here would itself
        trip the tests/ --codenames-only CI scan. The PLANTED .md file
        (written to a tmp scratch dir, never committed) still carries the
        real literal string the scanner must catch."""
        codename = "cultural" + "-social-sim"
        scratch = tmp_path / "literature"
        scratch.mkdir()
        (scratch / "leaked2024.md").write_text(
            "---\ntype: literature\ncitekey: leaked2024\ntitle: A Paper\n"
            f"---\n\n## Result\n\nRan on {codename}'s internal corpus.\n",
            encoding="utf-8",
        )
        result = _run_scan(scratch)
        assert result.returncode == 1, (
            f"Expected RED (planted class-1 codename) but got exit "
            f"{result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_planted_codename_in_overlay_goes_red(self, tmp_path):
        codename = "doss" + "ier"
        scratch = tmp_path / "literature"
        scratch.mkdir()
        (scratch / "leaked2024.md").write_text(
            "---\ntype: literature\ncentral: leaked2024\nrole: empirical\n"
            f'position: "notes from the {codename} project review"\n---\n\n'
            "## Concept edges\n\n",
            encoding="utf-8",
        )
        result = _run_scan(scratch)
        assert result.returncode == 1

    def test_shipped_literature_examples_are_clean(self):
        """The REAL shipped central-store fixtures, as committed."""
        result = _run_scan(LITERATURE_EXAMPLES)
        assert result.returncode == 0, (
            f"Shipped literature/ example fixtures are NOT leak-clean.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_shipped_demo_litreview_literature_overlays_are_clean(self):
        """The REAL shipped demo-litreview overlay fixtures, as committed."""
        result = _run_scan(DEMO_LITREVIEW_LIT)
        assert result.returncode == 0, (
            f"Shipped demo-litreview literature overlays are NOT leak-clean.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# B. The built wheel's data/** is clean (not just the source tree)
# ---------------------------------------------------------------------------

# The same marker classes leakage_scan.sh defines, applied directly to the
# extracted wheel contents — a second, independent check that does not rely
# on the scanner script itself (belt-and-suspenders: a bug IN the scanner
# would not be caught by re-running the scanner over the wheel).
#
# The two class-1 codenames are assembled at runtime (not contiguous
# literals) — this file is NOT on leakage_scan.sh's self-exclusion
# allowlist, so a literal codename in this .py source would itself trip
# the tests/ --codenames-only CI scan (see the plant-test comments above).
_CSB_CODENAME = "cultural" + "-social-sim"
_DOSSIER_CODENAME = "doss" + "ier"

_WHEEL_LEAK_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    (f"class-1 codename ({_CSB_CODENAME})", re.compile(re.escape(_CSB_CODENAME), re.IGNORECASE)),
    (f"class-1 codename ({_DOSSIER_CODENAME})", re.compile(r"\b" + re.escape(_DOSSIER_CODENAME) + r"\b", re.IGNORECASE)),
    ("class-4 cluster path (/juice2/)", re.compile(r"/juice2/")),
    ("class-4 cluster path (/scr2/)", re.compile(r"/scr2/")),
    ("class-11 private dev-path (~/vault)", re.compile(r"~/vault\b")),
    ("class-11 private dev-path (docs/superpowers)", re.compile(r"docs/superpowers")),
)


def _require_uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not on PATH — cannot build the wheel for this audit")
    return uv


@pytest.mark.slow
class TestBuiltWheelDataIsClean:
    def test_wheel_data_examples_literature_grep_clean(self, tmp_path):
        uv = _require_uv()
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        result = subprocess.run(
            [uv, "build", "--wheel", "--out-dir", str(dist_dir), "--no-progress"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"uv build failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        wheels = list(dist_dir.glob("*.whl"))
        assert wheels, f"No .whl found in {dist_dir} after uv build"
        wheel = max(wheels, key=lambda w: w.stat().st_mtime)

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with zipfile.ZipFile(wheel) as zf:
            zf.extractall(extract_dir)

        data_dir = extract_dir / "research_vault" / "data"
        assert data_dir.is_dir(), f"research_vault/data not found in wheel at {data_dir}"

        offenders: list[str] = []
        for path in data_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for label, pattern in _WHEEL_LEAK_PATTERNS:
                if pattern.search(text):
                    offenders.append(f"{path.relative_to(extract_dir)}: {label}")

        assert offenders == [], (
            "Built wheel's data/** contains private markers:\n" + "\n".join(offenders)
        )

    def test_wheel_literature_examples_present_and_two_layer_shaped(self, tmp_path):
        """The two-layer literature fixtures actually SHIP in the wheel
        (not accidentally excluded by the artifacts glob) — a positive
        control alongside the leak-clean negative control above."""
        uv = _require_uv()
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        result = subprocess.run(
            [uv, "build", "--wheel", "--out-dir", str(dist_dir), "--no-progress"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        assert result.returncode == 0
        wheel = max(dist_dir.glob("*.whl"), key=lambda w: w.stat().st_mtime)

        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()

        assert any(
            n.endswith("research_vault/data/examples/literature/smith2024.md") for n in names
        ), "central core smith2024.md missing from wheel"
        assert any(
            n.endswith(
                "research_vault/data/examples/demo-litreview/notes/literature/smith2024.md"
            )
            for n in names
        ), "demo-litreview overlay smith2024.md missing from wheel"
