"""Tier-5 graceful-degradation: Part::Feature / FeaturePython shape-import.

When a FreeCAD object's parametric history can't be translated (community
FeaturePython, imported BRep wrappers), the translator falls back to
exporting its evaluated shape as a STEP sidecar and emitting an
``import_step`` call. The geometry survives; the parametric handles don't.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fcstd2b123d.properties import Properties
from fcstd2b123d.verify import Tolerances, assert_equivalent, extract_build123d

# STEP round-trip introduces ~few-ppm drift on volume / area / inertia that
# isn't present in direct-translation tests. We loosen the relative
# tolerance from 1e-6 to 1e-4 (still 0.01%, well below any
# user-meaningful divergence) for shape-import only. The four-scalar check
# stays strict enough to catch a wrong shape; Hausdorff catches the
# topology cases per ADR-0004.
_SHAPE_IMPORT_TOL = Tolerances(
    volume_rel=1e-4, area_rel=1e-4, moi_rel=1e-4
)

FIXTURE = Path("tests/fixtures/tier5_shape_import/faucet_bancada.FCStd")


def _freecad_python() -> str:
    p = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not p:
        pytest.skip("FCSTD2B123D_FREECAD_PYTHON not set")
    return p


def _translate_to(tmp_path: Path) -> tuple[Path, str]:
    """Run the translator CLI; return the output .py and its source text."""
    py = _freecad_python()
    fc_pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    repo_src = str(Path(__file__).parent.parent / "src")
    pythonpath = ":".join(p for p in (repo_src, fc_pp) if p)
    env = {**os.environ, "PYTHONPATH": pythonpath}
    out_py = tmp_path / "faucet.py"
    subprocess.run(
        [py, "-m", "fcstd2b123d", str(FIXTURE), "-o", str(out_py)],
        check=True, capture_output=True, text=True, env=env,
    )
    return out_py, out_py.read_text()


def test_shape_import_emits_step_sidecars(tmp_path: Path):
    """`-o foo.py` writes the .py plus one .step per FeaturePython-class object."""
    out_py, source = _translate_to(tmp_path)
    assert out_py.exists()
    # Faucet_bancada has a single Part::Feature (the resolved compound).
    step_files = list(tmp_path.glob("*.step"))
    assert step_files, f"No STEP sidecars in {tmp_path}; emit was:\n{source}"
    # And the emit should reference import_step + Path + _HERE.
    assert "import_step" in source
    assert "_HERE" in source
    assert "from pathlib import Path" in source


def test_shape_import_geometry_matches_freecad(tmp_path: Path):
    """Exec the emit; the imported shape should match FreeCAD's snapshot."""
    out_py, source = _translate_to(tmp_path)

    # Exec from the directory containing the .py so the ``_HERE`` fallback
    # (which uses Path.cwd() when __file__ isn't bound) resolves correctly.
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        namespace: dict = {}
        exec(compile(source, str(out_py), "exec"), namespace)
    finally:
        os.chdir(cwd)

    assert "result" in namespace
    actual = extract_build123d(namespace["result"])
    expected = Properties.from_file(FIXTURE.with_suffix(".expected.json"))
    assert_equivalent(
        actual, expected,
        tolerances=_SHAPE_IMPORT_TOL,
        actual_part=namespace["result"],
        pointcloud_path=FIXTURE.with_suffix(".pointcloud.json"),
    )


def test_shape_import_without_output_path_errors(tmp_path: Path):
    """Without `-o`, the translator has nowhere to put the STEP sidecar;
    it should fail with a clear message rather than silently dropping the
    shape-import object."""
    py = _freecad_python()
    fc_pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    repo_src = str(Path(__file__).parent.parent / "src")
    pythonpath = ":".join(p for p in (repo_src, fc_pp) if p)
    env = {**os.environ, "PYTHONPATH": pythonpath}
    r = subprocess.run(
        [py, "-m", "fcstd2b123d", str(FIXTURE)],
        capture_output=True, text=True, env=env, check=False,
    )
    assert r.returncode != 0
    assert "shape-import fallback" in r.stderr or "shape-import" in r.stderr, r.stderr
