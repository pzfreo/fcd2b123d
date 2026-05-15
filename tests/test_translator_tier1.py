"""End-to-end translator tests for tier-1 primitives.

Each test:
1. Calls the translator in a FreeCAD-enabled subprocess (since the translator
   imports FreeCAD per ADR-0001).
2. Exec()s the emitted build123d source in this process (build123d env).
3. Extracts geometric properties from the resulting Part.
4. Compares to the committed .expected.json snapshot.

The subprocess requires two env vars:
    FCSTD2B123D_FREECAD_PYTHON      path to a Python with FreeCAD installed
    FCSTD2B123D_FREECAD_PYTHONPATH  PYTHONPATH entries to find FreeCAD.so

When either is unset, the whole module skips. This keeps the fast CI job
(no FreeCAD) green while the slow CI job (with FreeCAD configured) runs the
real translator tests.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fcstd2b123d.verify import assert_equivalent, extract_build123d
from fcstd2b123d.properties import Properties


FIXTURE_ROOT = Path("tests/fixtures")
FIXTURES = sorted(
    p for d in FIXTURE_ROOT.glob("tier1_*") for p in d.glob("*.FCStd")
)


def _freecad_python() -> str:
    p = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not p:
        pytest.skip(
            "FCSTD2B123D_FREECAD_PYTHON not set; translator needs a FreeCAD env. "
            "Locally: export FCSTD2B123D_FREECAD_PYTHON=.conda/envs/freecad/bin/python"
        )
    return p


def _translate(fcstd_path: Path) -> str:
    py = _freecad_python()
    fc_pythonpath = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    # Make our `src/` reachable too, so `python -m fcstd2b123d` resolves.
    repo_src = str(Path(__file__).parent.parent / "src")
    pythonpath = ":".join(p for p in (repo_src, fc_pythonpath) if p)

    env = {**os.environ, "PYTHONPATH": pythonpath}
    result = subprocess.run(
        [py, "-m", "fcstd2b123d", str(fcstd_path)],
        capture_output=True, text=True, env=env, check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"Translator exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout


@pytest.mark.parametrize("fcstd_path", FIXTURES, ids=lambda p: p.stem)
def test_tier1_primitive(fcstd_path: Path):
    source = _translate(fcstd_path)

    namespace: dict = {}
    try:
        exec(source, namespace)
    except Exception as exc:
        pytest.fail(f"Generated source failed to execute: {exc}\n\nSource:\n{source}")

    assert "result" in namespace, (
        "Translator must bind the final shape to `result`.\n\nSource:\n" + source
    )

    part = namespace["result"]
    actual = extract_build123d(part)
    expected = Properties.from_file(fcstd_path.with_suffix(".expected.json"))
    assert_equivalent(
        actual, expected,
        actual_part=part,
        pointcloud_path=fcstd_path.with_suffix(".pointcloud.json"),
    )
