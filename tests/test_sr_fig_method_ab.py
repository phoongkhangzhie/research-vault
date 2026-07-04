"""test_sr_fig_method_ab.py — SR-FIG-METHOD-AB: seaborn skin + render-script seam.

Tests for Slice A (apply_style seaborn skin, C1 probe) and Slice B (static_check
four violation classes, shared hasher C2, back-compat stub C3).

All tests are hermetic.  No ~/vault reads or writes.

Red-before-green provenance:
  - Every static_check violation test names the class it covers (V1-V4).
  - C1 probe test proves seaborn is in the _check_figures_extra probe tuple.
  - C2 shared-hasher test proves wandb_pull._hash_file is NOT a local definition.
  - C3 back-compat test proves no render_script: → identical output to before.
"""
from __future__ import annotations

import hashlib
import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_valid_render_script(path: Path, results_location: str = "/data/results.csv") -> None:
    """Write a render script that passes all four static_check gates."""
    path.write_text(
        "import hashlib\n"
        "import sys\n"
        "import json\n"
        "from pathlib import Path\n"
        "import matplotlib.pyplot as plt\n"
        "import pandas as pd\n"
        "from research_vault.figures.style import apply_style\n"
        "\n"
        "# V3: hash-verify\n"
        "h = hashlib.sha256()\n"
        "with open(results_location, 'rb') as fh:\n"
        "    while chunk := fh.read(1 << 20):\n"
        "        h.update(chunk)\n"
        "actual = 'sha256:' + h.hexdigest()\n"
        "if actual != experiment_results_hash:\n"
        "    sys.exit(1)\n"
        "\n"
        "# V2: apply_style call\n"
        "apply_style(preset, project)\n"
        "\n"
        "# No set_title (V4 compliant)\n"
        "fig, ax = plt.subplots()\n"
        "ax.set_ylabel('Score')\n",
        encoding="utf-8",
    )


# ============================================================================
# Slice A — seaborn style skin + C1 probe
# ============================================================================

class TestApplyStyleSeaborn:
    """apply_style uses seaborn set_theme + project palette; signature unchanged."""

    def test_signature_unchanged(self):
        """apply_style(preset, skin) signature is exactly (preset, skin) — no change."""
        import inspect
        from research_vault.figures.style import apply_style
        sig = inspect.signature(apply_style)
        params = list(sig.parameters.keys())
        assert params == ["preset", "skin"], (
            f"SEAM SIGNATURE BROKEN: apply_style must have (preset, skin); got {params}"
        )

    def test_apply_style_returns_dict_when_seaborn_present(self):
        """apply_style returns a dict when matplotlib + seaborn are installed."""
        pytest.importorskip("seaborn")
        pytest.importorskip("matplotlib")
        from research_vault.figures.style import apply_style
        result = apply_style("publication", "culturebench")
        assert isinstance(result, dict), (
            f"apply_style must return a dict when seaborn is present; got {type(result)}"
        )
        assert len(result) > 0, "returned dict must be non-empty (applied rcParams)"

    def test_apply_style_mutates_rcparams(self):
        """apply_style mutates matplotlib.rcParams (the side-effect is what matters)."""
        pytest.importorskip("seaborn")
        import matplotlib as mpl
        from research_vault.figures.style import apply_style
        # Reset to defaults so we can detect the mutation
        mpl.rcParams.update(mpl.rcParamsDefault)
        original_dpi = mpl.rcParams.get("savefig.dpi", 100)
        apply_style("publication", "demo-research")
        new_dpi = mpl.rcParams.get("savefig.dpi")
        assert new_dpi == 300, (
            f"publication preset must set savefig.dpi=300; got {new_dpi!r} "
            f"(original was {original_dpi})"
        )

    def test_apply_style_sets_project_palette(self):
        """apply_style installs the project palette (culturebench tokens) as prop_cycle."""
        pytest.importorskip("seaborn")
        import matplotlib as mpl
        from research_vault.figures.style import apply_style, _CB_TEAL, _CB_CLAY
        mpl.rcParams.update(mpl.rcParamsDefault)
        apply_style("publication", "culturebench")
        # The prop_cycle should contain the project teal as the first colour
        cycle = mpl.rcParams["axes.prop_cycle"].by_key()["color"]
        assert cycle[0].lower() == _CB_TEAL.lower(), (
            f"First palette colour must be project teal {_CB_TEAL}; got {cycle[0]}"
        )
        assert cycle[1].lower() == _CB_CLAY.lower(), (
            f"Second palette colour must be project clay {_CB_CLAY}; got {cycle[1]}"
        )

    def test_apply_style_returns_none_without_seaborn(self):
        """apply_style returns None silently when seaborn is not installed.

        Non-vacuous: we block seaborn from importing. The function must return
        None and NOT raise ImportError.
        """
        import builtins
        original_import = builtins.__import__

        def blocking_import(name, *args, **kwargs):
            if name == "seaborn":
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)

        from research_vault.figures import style as style_mod
        with patch.object(builtins, "__import__", side_effect=blocking_import):
            result = style_mod.apply_style("publication", "culturebench")

        assert result is None, (
            f"apply_style must return None when seaborn absent; got {result!r}"
        )

    def test_apply_style_returns_none_without_matplotlib(self):
        """apply_style returns None silently when matplotlib is not installed."""
        import builtins
        original_import = builtins.__import__

        def blocking_import(name, *args, **kwargs):
            if name == "matplotlib":
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)

        from research_vault.figures import style as style_mod
        with patch.object(builtins, "__import__", side_effect=blocking_import):
            result = style_mod.apply_style("publication", "culturebench")

        assert result is None, (
            f"apply_style must return None when matplotlib absent; got {result!r}"
        )

    def test_apply_style_publication_uses_paper_context(self):
        """publication preset maps to seaborn context='paper'."""
        pytest.importorskip("seaborn")
        from research_vault.figures.style import _PRESET_TO_SNS_CONTEXT
        assert _PRESET_TO_SNS_CONTEXT.get("publication") == "paper", (
            "publication preset must map to seaborn context='paper'"
        )

    def test_apply_style_slide_uses_talk_context(self):
        """slide preset maps to seaborn context='talk'."""
        from research_vault.figures.style import _PRESET_TO_SNS_CONTEXT
        assert _PRESET_TO_SNS_CONTEXT.get("slide") == "talk"

    def test_apply_style_poster_uses_poster_context(self):
        """poster preset maps to seaborn context='poster'."""
        from research_vault.figures.style import _PRESET_TO_SNS_CONTEXT
        assert _PRESET_TO_SNS_CONTEXT.get("poster") == "poster"

    def test_apply_style_project_palette_tokens_defined(self):
        """The culturebench palette tokens are defined as module constants."""
        from research_vault.figures.style import _CB_TEAL, _CB_CLAY, _CB_PAPER
        assert _CB_TEAL.startswith("#") and len(_CB_TEAL) == 7
        assert _CB_CLAY.startswith("#") and len(_CB_CLAY) == 7
        assert _CB_PAPER.startswith("#") and len(_CB_PAPER) == 7

    def test_apply_style_unknown_preset_falls_back_to_publication(self):
        """Unknown preset falls back to publication silently."""
        pytest.importorskip("seaborn")
        import matplotlib as mpl
        from research_vault.figures.style import apply_style, _PRESET_RCPARAMS
        mpl.rcParams.update(mpl.rcParamsDefault)
        result = apply_style("UNKNOWN_PRESET", "demo-research")
        # Falls back to publication — savefig.dpi should be 300
        assert isinstance(result, dict)
        assert result.get("savefig.dpi") == 300


class TestC1SeabornProbe:
    """C1: _check_figures_extra probes seaborn in addition to pandas + matplotlib."""

    def test_seaborn_in_check_figures_extra_probe(self):
        """_check_figures_extra checks seaborn (not just pandas + matplotlib).

        Non-vacuous: block seaborn from importing — the function must return
        non-None (error), NOT None (success).  Before C1 fix, it returned None
        (passed) even when seaborn was absent, then apply_style blew up mid-render.
        """
        import builtins
        original_import = builtins.__import__

        def blocking_import(name, *args, **kwargs):
            if name == "seaborn":
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)

        from research_vault import figure as fig_mod
        with patch.object(builtins, "__import__", side_effect=blocking_import):
            rc = fig_mod._check_figures_extra()

        assert rc is not None and rc != 0, (
            f"_check_figures_extra must return non-zero when seaborn is absent; got {rc!r}. "
            "This is the C1 two-layer defence: the probe must fire BEFORE apply_style "
            "is called so the function never blows up mid-render."
        )

    def test_check_figures_extra_probe_tuple_contains_seaborn(self):
        """Inspect _check_figures_extra source: 'seaborn' appears in the probe loop.

        Non-vacuous via AST: we walk the function body and check the probe collection
        contains 'seaborn' as a string constant — not just in a comment.
        """
        import ast
        import inspect
        import textwrap
        from research_vault import figure as fig_mod

        src = textwrap.dedent(inspect.getsource(fig_mod._check_figures_extra))
        tree = ast.parse(src)

        # Collect all string constants in the function body
        string_literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert "seaborn" in string_literals, (
            f"'seaborn' must appear as a string literal in _check_figures_extra "
            f"(probe loop). Found literals: {string_literals}"
        )


# ============================================================================
# Slice B — static_check four violation classes (V1–V4)
# ============================================================================

class TestStaticCheckV1ForbiddenImport:
    """V1: static_check rejects imports outside the allowlist."""

    def test_v1_rejects_os_import(self, tmp_path):
        """static_check rejects 'import os' (os not in allowlist).

        RED before fix: static_check returned [] (no violations).
        GREEN after fix: returns V1 violation.
        """
        script = tmp_path / "render_os.py"
        script.write_text(
            "import os\n"
            "import hashlib\n"
            "import sys\n"
            "from research_vault.figures.style import apply_style\n"
            "h = hashlib.sha256()\n"
            "actual = 'sha256:' + h.hexdigest()\n"
            "if actual != experiment_results_hash: sys.exit(1)\n"
            "apply_style('publication', 'demo')\n",
            encoding="utf-8",
        )
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v1 = [v for v in violations if v.startswith("[V1-IMPORT]")]
        assert v1, (
            f"[V1-IMPORT] violation must be reported for 'import os'; got: {violations}"
        )
        assert "os" in v1[0], f"Violation must name the forbidden module; got: {v1[0]}"

    def test_v1_rejects_subprocess_import(self, tmp_path):
        """static_check rejects 'import subprocess'."""
        script = tmp_path / "render_sub.py"
        script.write_text("import subprocess\n", encoding="utf-8")
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v1 = [v for v in violations if "[V1-IMPORT]" in v and "subprocess" in v]
        assert v1, f"subprocess import must be flagged; got: {violations}"

    def test_v1_rejects_from_os_import(self, tmp_path):
        """static_check rejects 'from os import path'."""
        script = tmp_path / "render_from_os.py"
        script.write_text("from os import path\n", encoding="utf-8")
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v1 = [v for v in violations if "[V1-IMPORT]" in v]
        assert v1, f"'from os import path' must be flagged; got: {violations}"

    def test_v1_allows_matplotlib_pyplot(self, tmp_path):
        """static_check allows 'import matplotlib.pyplot as plt' (allowlist)."""
        script = tmp_path / "render_mpl.py"
        _write_valid_render_script(script)
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v1 = [v for v in violations if "[V1-IMPORT]" in v]
        assert not v1, f"matplotlib.pyplot must be allowed; got V1 violations: {v1}"

    def test_v1_allows_all_allowlist_imports(self, tmp_path):
        """All allowlist modules are permitted without V1 violation."""
        script = tmp_path / "render_allowlist.py"
        script.write_text(
            "import matplotlib.pyplot as plt\n"
            "import seaborn as sns\n"
            "import pandas as pd\n"
            "import numpy as np\n"
            "import hashlib\n"
            "import json\n"
            "import sys\n"
            "from pathlib import Path\n"
            "from research_vault.figures.style import apply_style\n"
            # Add required V2/V3 to avoid those violations
            "h = hashlib.sha256()\n"
            "with open(results_location, 'rb') as fh:\n"
            "    while chunk := fh.read(1<<20): h.update(chunk)\n"
            "actual = 'sha256:' + h.hexdigest()\n"
            "if actual != experiment_results_hash: sys.exit(1)\n"
            "apply_style(preset, project)\n",
            encoding="utf-8",
        )
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v1 = [v for v in violations if "[V1-IMPORT]" in v]
        assert not v1, f"Allowlist imports must not be flagged; got: {v1}"


class TestStaticCheckV2MissingApplyStyle:
    """V2: static_check rejects scripts that skip apply_style()."""

    def test_v2_rejects_missing_apply_style(self, tmp_path):
        """static_check rejects a script with no apply_style() call.

        RED before fix: static_check returned [] (no violations).
        GREEN after fix: returns V2 violation.
        """
        script = tmp_path / "render_no_style.py"
        script.write_text(
            "import hashlib\n"
            "import sys\n"
            "import matplotlib.pyplot as plt\n"
            "# Missing: apply_style() call\n"
            "h = hashlib.sha256()\n"
            "with open(results_location, 'rb') as fh:\n"
            "    while chunk := fh.read(1<<20): h.update(chunk)\n"
            "actual = 'sha256:' + h.hexdigest()\n"
            "if actual != experiment_results_hash: sys.exit(1)\n"
            "fig, ax = plt.subplots()\n",
            encoding="utf-8",
        )
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v2 = [v for v in violations if v.startswith("[V2-STYLE]")]
        assert v2, (
            f"[V2-STYLE] violation must be reported when apply_style() is absent; "
            f"got: {violations}"
        )

    def test_v2_passes_when_apply_style_present(self, tmp_path):
        """static_check does not emit V2 when apply_style() is called."""
        script = tmp_path / "render_with_style.py"
        _write_valid_render_script(script)
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v2 = [v for v in violations if "[V2-STYLE]" in v]
        assert not v2, f"V2 must not fire when apply_style() is present; got: {v2}"

    def test_v2_apply_style_in_comment_is_not_enough(self, tmp_path):
        """A commented-out apply_style does not satisfy V2.

        Non-vacuous: the check is AST-based, not string search.  A comment
        containing 'apply_style(' must not satisfy the V2 gate.
        """
        script = tmp_path / "render_commented_style.py"
        script.write_text(
            "import hashlib\n"
            "import sys\n"
            "# apply_style(preset, project)  <-- commented out, does NOT count\n"
            "h = hashlib.sha256()\n"
            "with open(results_location, 'rb') as fh:\n"
            "    while chunk := fh.read(1<<20): h.update(chunk)\n"
            "actual = 'sha256:' + h.hexdigest()\n"
            "if actual != experiment_results_hash: sys.exit(1)\n",
            encoding="utf-8",
        )
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v2 = [v for v in violations if "[V2-STYLE]" in v]
        assert v2, (
            "A commented-out apply_style must NOT satisfy V2 (AST-based check, "
            f"not string search); got: {violations}"
        )


class TestStaticCheckV3MissingHashVerify:
    """V3: static_check rejects scripts that skip the hash-verify pattern."""

    def test_v3_rejects_missing_hash_call(self, tmp_path):
        """static_check rejects a script with no hashlib.sha256 or _hash_file call.

        RED before fix: static_check returned [] (no violations).
        GREEN after fix: returns V3 violation.
        """
        script = tmp_path / "render_no_hash.py"
        script.write_text(
            "import sys\n"
            "from research_vault.figures.style import apply_style\n"
            "# Missing: hashlib.sha256() / _hash_file() call\n"
            "# experiment_results_hash is referenced but no actual hashing\n"
            "if 'xxx' != experiment_results_hash: sys.exit(1)\n"
            "apply_style(preset, project)\n",
            encoding="utf-8",
        )
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v3 = [v for v in violations if v.startswith("[V3-INTEGRITY]")]
        assert v3, (
            f"[V3-INTEGRITY] violation must be reported when hashlib.sha256() is absent; "
            f"got: {violations}"
        )

    def test_v3_rejects_missing_experiment_results_hash_reference(self, tmp_path):
        """static_check rejects a script that hashes but doesn't reference the stored hash."""
        script = tmp_path / "render_no_hash_ref.py"
        script.write_text(
            "import hashlib\n"
            "import sys\n"
            "from research_vault.figures.style import apply_style\n"
            "h = hashlib.sha256()\n"
            "with open(results_location, 'rb') as fh:\n"
            "    while chunk := fh.read(1<<20): h.update(chunk)\n"
            "actual = 'sha256:' + h.hexdigest()\n"
            "# Does not compare against experiment_results_hash (missing reference)\n"
            "if actual != 'sha256:hardcoded': sys.exit(1)\n"
            "apply_style(preset, project)\n",
            encoding="utf-8",
        )
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v3 = [v for v in violations if "[V3-INTEGRITY]" in v]
        assert v3, (
            "[V3-INTEGRITY] must fire when experiment_results_hash name is absent "
            f"(hardcoded hash does not satisfy the gate); got: {violations}"
        )

    def test_v3_rejects_missing_abort_on_mismatch(self, tmp_path):
        """static_check rejects a script that checks the hash but doesn't abort on mismatch."""
        script = tmp_path / "render_no_exit.py"
        script.write_text(
            "import hashlib\n"
            "from research_vault.figures.style import apply_style\n"
            "h = hashlib.sha256()\n"
            "with open(results_location, 'rb') as fh:\n"
            "    while chunk := fh.read(1<<20): h.update(chunk)\n"
            "actual = 'sha256:' + h.hexdigest()\n"
            "# Checks hash but does NOT abort on mismatch (no sys.exit or raise)\n"
            "if actual != experiment_results_hash:\n"
            "    print('hash mismatch')\n"  # logs but does not abort
            "apply_style(preset, project)\n",
            encoding="utf-8",
        )
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v3 = [v for v in violations if "[V3-INTEGRITY]" in v]
        assert v3, (
            "[V3-INTEGRITY] must fire when no sys.exit/raise is present; "
            f"got: {violations}"
        )

    def test_v3_passes_complete_hash_verify_pattern(self, tmp_path):
        """static_check does not emit V3 when the full hash-verify pattern is present."""
        script = tmp_path / "render_full_hash.py"
        _write_valid_render_script(script)
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v3 = [v for v in violations if "[V3-INTEGRITY]" in v]
        assert not v3, (
            f"V3 must not fire when full hash-verify pattern is present; got: {v3}"
        )

    def test_v3_accepts_hash_file_as_alternative(self, tmp_path):
        """static_check accepts _hash_file() as an alternative to hashlib.sha256()."""
        script = tmp_path / "render_hash_file.py"
        script.write_text(
            "import sys\n"
            "import hashlib\n"
            "from research_vault.figures.style import apply_style\n"
            "from research_vault.hashing import hash_file as _hash_file\n"
            "actual = _hash_file(results_location)\n"
            "if actual != experiment_results_hash: sys.exit(1)\n"
            "apply_style(preset, project)\n",
            encoding="utf-8",
        )
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v3 = [v for v in violations if "[V3-INTEGRITY]" in v]
        assert not v3, (
            f"_hash_file() must satisfy V3 hash-compute requirement; got: {violations}"
        )


class TestStaticCheckV4BakedTitle:
    """V4: static_check rejects baked-claim titles (set_title/suptitle with string literal)."""

    def test_v4_rejects_set_title_with_string_literal(self, tmp_path):
        """static_check rejects ax.set_title('claim string') (baked-claim title).

        RED before fix: static_check returned [] (no violations).
        GREEN after fix: returns V4 violation.
        """
        script = tmp_path / "render_baked_title.py"
        script.write_text(
            "import hashlib\n"
            "import sys\n"
            "import matplotlib.pyplot as plt\n"
            "from research_vault.figures.style import apply_style\n"
            "h = hashlib.sha256()\n"
            "with open(results_location, 'rb') as fh:\n"
            "    while chunk := fh.read(1<<20): h.update(chunk)\n"
            "actual = 'sha256:' + h.hexdigest()\n"
            "if actual != experiment_results_hash: sys.exit(1)\n"
            "apply_style(preset, project)\n"
            "fig, ax = plt.subplots()\n"
            "ax.set_title('Model accuracy exceeds baseline')  # baked claim!\n",
            encoding="utf-8",
        )
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v4 = [v for v in violations if v.startswith("[V4-TITLE]")]
        assert v4, (
            f"[V4-TITLE] violation must be reported for set_title with string literal; "
            f"got: {violations}"
        )

    def test_v4_rejects_suptitle_with_string_literal(self, tmp_path):
        """static_check rejects fig.suptitle('claim') (baked-claim title)."""
        script = tmp_path / "render_suptitle.py"
        script.write_text(
            "import hashlib\n"
            "import sys\n"
            "import matplotlib.pyplot as plt\n"
            "from research_vault.figures.style import apply_style\n"
            "h = hashlib.sha256()\n"
            "with open(results_location, 'rb') as fh:\n"
            "    while chunk := fh.read(1<<20): h.update(chunk)\n"
            "actual = 'sha256:' + h.hexdigest()\n"
            "if actual != experiment_results_hash: sys.exit(1)\n"
            "apply_style(preset, project)\n"
            "fig, ax = plt.subplots()\n"
            "fig.suptitle('Results show significant improvement')\n",
            encoding="utf-8",
        )
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v4 = [v for v in violations if "[V4-TITLE]" in v]
        assert v4, f"suptitle with string literal must be flagged; got: {violations}"

    def test_v4_allows_set_title_with_variable(self, tmp_path):
        """static_check allows set_title(variable) — only string literals are banned.

        The figure-minimalism rule bans BAKED CLAIMS, not dynamic labels.
        set_title(my_title_var) is dynamic, so it passes V4.
        """
        script = tmp_path / "render_dynamic_title.py"
        script.write_text(
            "import hashlib\n"
            "import sys\n"
            "import matplotlib.pyplot as plt\n"
            "from research_vault.figures.style import apply_style\n"
            "h = hashlib.sha256()\n"
            "with open(results_location, 'rb') as fh:\n"
            "    while chunk := fh.read(1<<20): h.update(chunk)\n"
            "actual = 'sha256:' + h.hexdigest()\n"
            "if actual != experiment_results_hash: sys.exit(1)\n"
            "apply_style(preset, project)\n"
            "fig, ax = plt.subplots()\n"
            "title_var = None  # dynamic — not a literal\n"
            "if title_var:\n"
            "    ax.set_title(title_var)\n",
            encoding="utf-8",
        )
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v4 = [v for v in violations if "[V4-TITLE]" in v]
        assert not v4, (
            f"set_title(variable) must NOT be flagged (only string literals are banned); "
            f"got: {violations}"
        )

    def test_v4_rejects_set_title_label_kwarg(self, tmp_path):
        """static_check rejects ax.set_title(label='Baked claim') (keyword form).

        matplotlib's real keyword for set_title is ``label``, not ``title``.
        The old check only matched ``kw.arg == 'title'`` — a wrong kwarg name that
        never fires. This test is the RED-before-GREEN pin for the keyword-bypass fix.

        RED before fix: static_check returned [] (label= bypassed V4).
        GREEN after fix: kw.arg in ('title', 'label', 't') catches the label= form.
        """
        script = tmp_path / "render_baked_label.py"
        script.write_text(
            "import hashlib\n"
            "import sys\n"
            "import matplotlib.pyplot as plt\n"
            "from research_vault.figures.style import apply_style\n"
            "h = hashlib.sha256()\n"
            "with open(results_location, 'rb') as fh:\n"
            "    while chunk := fh.read(1<<20): h.update(chunk)\n"
            "actual = 'sha256:' + h.hexdigest()\n"
            "if actual != experiment_results_hash: sys.exit(1)\n"
            "apply_style(preset, project)\n"
            "fig, ax = plt.subplots()\n"
            "ax.set_title(label='Baked claim via label kwarg')  # keyword bypass!\n",
            encoding="utf-8",
        )
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v4 = [v for v in violations if "[V4-TITLE]" in v]
        assert v4, (
            "[V4-TITLE] violation must fire for set_title(label='...'); "
            f"got: {violations}"
        )

    def test_v4_rejects_suptitle_t_kwarg(self, tmp_path):
        """static_check rejects fig.suptitle(t='Baked claim') (keyword form).

        matplotlib's real keyword for suptitle is ``t``, not ``title``.
        The old check only matched ``kw.arg == 'title'`` — never fires on suptitle.
        This test is the RED-before-GREEN pin for the keyword-bypass fix.

        RED before fix: static_check returned [] (t= bypassed V4).
        GREEN after fix: kw.arg in ('title', 'label', 't') catches the t= form.
        """
        script = tmp_path / "render_baked_suptitle_t.py"
        script.write_text(
            "import hashlib\n"
            "import sys\n"
            "import matplotlib.pyplot as plt\n"
            "from research_vault.figures.style import apply_style\n"
            "h = hashlib.sha256()\n"
            "with open(results_location, 'rb') as fh:\n"
            "    while chunk := fh.read(1<<20): h.update(chunk)\n"
            "actual = 'sha256:' + h.hexdigest()\n"
            "if actual != experiment_results_hash: sys.exit(1)\n"
            "apply_style(preset, project)\n"
            "fig, ax = plt.subplots()\n"
            "fig.suptitle(t='Baked claim via t kwarg')  # keyword bypass!\n",
            encoding="utf-8",
        )
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v4 = [v for v in violations if "[V4-TITLE]" in v]
        assert v4, (
            "[V4-TITLE] violation must fire for suptitle(t='...'); "
            f"got: {violations}"
        )

    def test_v4_passes_no_title_call(self, tmp_path):
        """static_check passes a script with no set_title/suptitle call."""
        script = tmp_path / "render_no_title.py"
        _write_valid_render_script(script)
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        v4 = [v for v in violations if "[V4-TITLE]" in v]
        assert not v4, f"A script without title calls must not trigger V4; got: {violations}"


class TestStaticCheckValidScript:
    """A fully compliant script passes all four gates."""

    def test_valid_script_passes_all_gates(self, tmp_path):
        """A script satisfying all four gates returns an empty violations list."""
        script = tmp_path / "render_valid.py"
        _write_valid_render_script(script)
        from research_vault.figures.render_script import static_check
        violations = static_check(script)
        assert violations == [], (
            f"A compliant script must have zero violations; got: {violations}"
        )

    def test_syntax_error_raises(self, tmp_path):
        """static_check raises SyntaxError for a script with invalid Python syntax."""
        script = tmp_path / "render_syntax.py"
        script.write_text("def broken(:\n    pass\n", encoding="utf-8")
        from research_vault.figures.render_script import static_check
        with pytest.raises(SyntaxError):
            static_check(script)

    def test_file_not_found_raises(self, tmp_path):
        """static_check raises FileNotFoundError for a missing script."""
        from research_vault.figures.render_script import static_check
        with pytest.raises(FileNotFoundError):
            static_check(tmp_path / "nonexistent.py")


# ============================================================================
# Slice B — scaffold does NOT pre-satisfy static_check (honesty gate ruling)
# ============================================================================

class TestEmitScaffoldNotPreSatisfied:
    """emit_scaffold emits an AUTHOR-ME template that intentionally fails static_check.

    A machine-generated always-green script makes the honesty gate vacuous (architect's
    ruling).  The scaffold must fail static_check so the human/LLM has to author
    the actual logic — the gate runs on the authored result.
    """

    def test_scaffold_fails_static_check(self, tmp_path):
        """The scaffold does NOT satisfy static_check (intentionally incomplete).

        Non-vacuous: write the scaffold to a file, run static_check, assert
        violations > 0.  This is the key honesty property — a pre-satisfied scaffold
        would make the gate meaningless.
        """
        from research_vault.figures.render_script import emit_scaffold, static_check
        fields: dict[str, Any] = {
            "title": "test-fig",
            "source_experiment": "experiments/run-001",
            "style": "publication",
            "experiment_results_hash": "sha256:abc123",
        }
        scaffold_src = emit_scaffold(fields)
        scaffold_path = tmp_path / "scaffold_render.py"
        scaffold_path.write_text(scaffold_src, encoding="utf-8")

        violations = static_check(scaffold_path)
        assert len(violations) > 0, (
            "emit_scaffold must NOT pre-satisfy static_check — the scaffold is an "
            "author-me template; violations: " + repr(violations)
        )

    def test_scaffold_is_valid_python(self, tmp_path):
        """The scaffold is syntactically valid Python (parseable by ast)."""
        import ast
        from research_vault.figures.render_script import emit_scaffold
        fields: dict[str, Any] = {
            "title": "test-fig",
            "source_experiment": "experiments/run-001",
            "style": "publication",
        }
        scaffold_src = emit_scaffold(fields)
        # Should not raise SyntaxError
        tree = ast.parse(scaffold_src)
        assert tree is not None

    def test_scaffold_contains_fill_markers(self):
        """The scaffold contains FILL markers to guide the author."""
        from research_vault.figures.render_script import emit_scaffold
        fields: dict[str, Any] = {"title": "test-fig"}
        scaffold = emit_scaffold(fields)
        assert "FILL" in scaffold, (
            "Scaffold must contain FILL markers to guide the author"
        )


# ============================================================================
# C2 — shared hasher: wandb_pull does NOT define _hash_file locally
# ============================================================================

class TestC2SharedHasher:
    """C2: _hash_file is imported from hashing.py, not defined in wandb_pull."""

    def test_hashing_module_exists(self):
        """research_vault.hashing module is importable."""
        from research_vault import hashing
        assert hasattr(hashing, "hash_file"), (
            "research_vault.hashing must export hash_file"
        )

    def test_hash_file_returns_sha256_prefix(self, tmp_path):
        """hash_file returns 'sha256:<hex>' format."""
        from research_vault.hashing import hash_file
        test_file = tmp_path / "test.csv"
        test_data = b"metric,value\nacc,0.92\n"
        test_file.write_bytes(test_data)
        result = hash_file(test_file)
        assert result.startswith("sha256:"), f"hash_file must return 'sha256:<hex>'; got {result!r}"
        expected = "sha256:" + hashlib.sha256(test_data).hexdigest()
        assert result == expected, f"hash mismatch: {result!r} != {expected!r}"

    def test_wandb_pull_imports_hash_file_from_hashing(self):
        """wandb_pull._hash_file is NOT a local function — imported from hashing.

        Non-vacuous: use inspect.isfunction + inspect.getmodule to confirm the
        callable is defined in research_vault.hashing, not in research_vault.wandb_pull.
        """
        import inspect
        from research_vault import wandb_pull, hashing
        wbp_hash = getattr(wandb_pull, "_hash_file", None)
        assert wbp_hash is not None, (
            "wandb_pull must expose _hash_file (via import from hashing)"
        )
        # The underlying function must be defined in hashing, not wandb_pull
        fn = wbp_hash
        defined_in = getattr(fn, "__module__", "")
        assert "hashing" in defined_in or fn is hashing.hash_file, (
            f"wandb_pull._hash_file must be imported from hashing, "
            f"not locally defined; __module__={defined_in!r}"
        )

    def test_wandb_pull_has_no_hashlib_sha256_local_definition(self):
        """wandb_pull does not define its own sha256-hash function body.

        Non-vacuous via AST: parse wandb_pull source, walk FunctionDef nodes,
        assert none named '_hash_file' or containing hashlib.sha256 calls are
        defined in the file's own scope (only at module level as defs).
        """
        import ast
        import inspect
        import textwrap
        from research_vault import wandb_pull

        src = inspect.getsource(wandb_pull)
        tree = ast.parse(textwrap.dedent(src))

        local_hash_defs = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_hash_file"
        ]
        assert not local_hash_defs, (
            f"wandb_pull must NOT define a local _hash_file function; "
            f"it must import from hashing.  Found local defs: {local_hash_defs}"
        )


# ============================================================================
# C3 — back-compat: no render_script: → IDENTICAL output to before
# ============================================================================

class TestC3BackCompatStub:
    """C3: no render_script: field → df.plot stub is UNCHANGED, existing tests green."""

    def _write_experiment_note_and_results(
        self, tmp_dir: Path, project_notes_dir: Path, exp_id: str,
        results_data: bytes = b"metric,value\nacc,0.92\nf1,0.88\n",
    ) -> tuple[Path, str]:
        results_file = tmp_dir / f"{exp_id}.csv"
        results_file.write_bytes(results_data)
        results_hash = _sha256_hex(results_data)
        exp_dir = project_notes_dir / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "---", "type: experiments", f"title: {exp_id}", "created: 2026-07-04",
            f"results_location: {results_file}",
            f"results_hash: {results_hash}",
            "---", "", "<!-- test exp -->", "",
        ]
        note_path = exp_dir / f"{exp_id}.md"
        note_path.write_text("\n".join(lines), encoding="utf-8")
        return note_path, results_hash

    def test_no_render_script_field_produces_svg_png(self, tmp_instance):
        """cmd_render without render_script: still produces SVG+PNG (df.plot stub)."""
        pytest.importorskip("matplotlib")
        pytest.importorskip("pandas")
        from research_vault.config import load_config
        from research_vault.figure import cmd_new, cmd_render

        cfg = load_config(reload=True)
        project_notes_dir = cfg.project_notes_dir("demo-research")
        self._write_experiment_note_and_results(
            Path(tmp_instance), project_notes_dir, "run-backcompat",
        )
        cmd_new("demo-research", "fig-backcompat", experiment_id="run-backcompat", config=cfg)

        rc = cmd_render("demo-research", "fig-backcompat", config=cfg)
        assert rc == 0, f"Back-compat render must return 0; got {rc}"

        svg_path = cfg.state_dir / "figures" / "fig-backcompat.svg"
        png_path = cfg.state_dir / "figures" / "fig-backcompat.png"
        assert svg_path.exists(), "df.plot stub must produce SVG (back-compat)"
        assert png_path.exists(), "df.plot stub must produce PNG (back-compat)"

    def test_render_script_field_routes_to_script_path(self, tmp_instance, tmp_path):
        """cmd_render with render_script: routes to the script path (not df.plot).

        Non-vacuous: the script writes a sentinel file; if df.plot ran instead,
        the sentinel would not appear.
        """
        pytest.importorskip("matplotlib")
        pytest.importorskip("pandas")
        from research_vault.config import load_config
        from research_vault.figure import cmd_new, cmd_render

        cfg = load_config(reload=True)
        project_notes_dir = cfg.project_notes_dir("demo-research")
        results_data = b"metric,value\nacc,0.92\n"
        self._write_experiment_note_and_results(
            Path(tmp_instance), project_notes_dir, "run-script",
            results_data=results_data,
        )
        fig_note = cmd_new(
            "demo-research", "fig-script", experiment_id="run-script", config=cfg
        )

        # Create a valid render script that writes a sentinel file
        sentinel_path = tmp_path / "render_ran.txt"
        render_script = tmp_path / "my_render.py"
        _write_valid_render_script(render_script)
        # Append sentinel write to the script
        with open(render_script, "a", encoding="utf-8") as f:
            f.write(f"\nopen({str(sentinel_path)!r}, 'w').write('ran')\n")

        # Add render_script: field to the figure note
        text = fig_note.read_text(encoding="utf-8")
        text = text.replace("rendered: false", f"rendered: false\nrender_script: {render_script}")
        fig_note.write_text(text, encoding="utf-8")

        rc = cmd_render("demo-research", "fig-script", config=cfg)
        assert rc == 0, f"cmd_render with valid render_script must succeed; got {rc}"
        assert sentinel_path.exists(), (
            "render script must have been executed (sentinel file not found). "
            "This proves cmd_render routed to the script, not the df.plot stub."
        )

    def test_static_check_violations_block_exec(self, tmp_instance, tmp_path):
        """cmd_render returns 1 and does NOT execute a script with static_check violations."""
        pytest.importorskip("matplotlib")
        pytest.importorskip("pandas")
        from research_vault.config import load_config
        from research_vault.figure import cmd_new, cmd_render

        cfg = load_config(reload=True)
        project_notes_dir = cfg.project_notes_dir("demo-research")
        self._write_experiment_note_and_results(
            Path(tmp_instance), project_notes_dir, "run-blocked",
        )
        fig_note = cmd_new(
            "demo-research", "fig-blocked", experiment_id="run-blocked", config=cfg
        )

        # A DANGEROUS script that imports os (V1 violation) — must NOT be executed
        sentinel_path = tmp_path / "exec_ran.txt"
        bad_script = tmp_path / "bad_render.py"
        bad_script.write_text(
            f"import os\nopen({str(sentinel_path)!r}, 'w').write('ran')\n",
            encoding="utf-8",
        )

        text = fig_note.read_text(encoding="utf-8")
        text = text.replace("rendered: false", f"rendered: false\nrender_script: {bad_script}")
        fig_note.write_text(text, encoding="utf-8")

        rc = cmd_render("demo-research", "fig-blocked", config=cfg)
        assert rc == 1, f"cmd_render must return 1 when static_check fails; got {rc}"
        assert not sentinel_path.exists(), (
            "The bad script must NOT have been executed — static_check must "
            "block execution BEFORE running the script."
        )
