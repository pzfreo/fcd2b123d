"""Validate the --json-out structured feature record (SPEC §14).

Runs the translator with --json-out against a couple of fixtures and asserts
the emitted JSON conforms to the schema we documented.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.test_translator_tier1 import _freecad_python  # reuses the skip


def _run(fcstd_path: Path, tmp_path: Path) -> dict:
    py = _freecad_python()
    fc_pythonpath = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    repo_src = str(Path(__file__).parent.parent / "src")
    pythonpath = ":".join(p for p in (repo_src, fc_pythonpath) if p)

    py_out = tmp_path / "out.py"
    json_out = tmp_path / "out.features.json"
    env = {**os.environ, "PYTHONPATH": pythonpath}
    result = subprocess.run(
        [py, "-m", "fcstd2b123d", str(fcstd_path),
         "-o", str(py_out), "--json-out", str(json_out)],
        capture_output=True, text=True, env=env, check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"Translator exited {result.returncode}:\n{result.stderr}")
    return json.loads(json_out.read_text())


def test_sidecar_basic_shape(tmp_path):
    data = _run(Path("tests/fixtures/tier1_primitives/box_10x20x30.FCStd"), tmp_path)

    # Required top-level fields
    assert data["schema_version"] == "1"
    assert data["exporter_version"].startswith("fcstd2b123d-")
    assert data["source"]["path"].endswith("box_10x20x30.FCStd")
    assert data["source"]["freecad_version"]  # non-empty string

    # One step for one Part::Box
    assert data["summary"]["feature_count"] == 1
    assert data["summary"]["primitives_used"] == ["box"]
    assert len(data["steps"]) == 1

    step = data["steps"][0]
    assert step["feature_type"] == "box"
    assert step["feature_name"] == "TestBox"
    # The field is always a bool; the synthesized fixture happens to have
    # Name == Label so the value is False here. Real-world files where the
    # author renamed the Label after creation will be True.
    assert isinstance(step["renamed_from_default"], bool)
    assert "Box(10, 20, 30)" in step["build123d_code"]

    # Properties present for a 3D primitive
    props = step["properties"]
    assert props is not None
    assert props["volume"] == 6000.0
    assert props["surface_area"] == 2200.0
    assert props["center_of_mass"] == [5.0, 10.0, 15.0]


def test_sidecar_multi_feature_body(tmp_path):
    data = _run(
        Path("tests/fixtures/tier2_partdesign/partdesign_example.FCStd"), tmp_path
    )

    # Sketches don't produce a 3D solid → properties is None for them
    sketch_steps = [s for s in data["steps"] if s["feature_type"] == "sketch"]
    assert sketch_steps
    for s in sketch_steps:
        assert s["properties"] is None

    # Features that produce a solid have properties populated
    solid_steps = [
        s for s in data["steps"]
        if s["feature_type"] in {"pad", "pocket", "revolution"}
    ]
    assert solid_steps
    for s in solid_steps:
        assert s["properties"] is not None
        assert s["properties"]["volume"] > 0

    # depends_on chain is non-empty for pockets
    pockets = [s for s in data["steps"] if s["feature_type"] == "pocket"]
    assert pockets
    for p in pockets:
        assert len(p["depends_on"]) >= 1


def test_sidecar_fillet_records_dependency(tmp_path):
    data = _run(
        Path("tests/fixtures/tier3_filletchamfer/box_with_fillet.FCStd"), tmp_path
    )
    fillets = [s for s in data["steps"] if s["feature_type"] == "fillet"]
    assert len(fillets) == 1
    fil = fillets[0]
    assert fil["depends_on"] == ["Pad"]
    assert "fillet" in fil["build123d_code"]
    # Fillet is a body-on-solid step → has properties
    assert fil["properties"] is not None
