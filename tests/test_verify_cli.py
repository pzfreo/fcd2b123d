"""Smoke test for the --verify translator flag and the fcstd2b123d-verify CLI.

End-to-end check that the full workflow works:
    1. ``fcstd2b123d --verify foo.FCStd -o foo.py`` writes the .py plus the
       FreeCAD-side .expected.json and .pointcloud.json sidecars.
    2. ``fcstd2b123d-verify foo.py foo.expected.json`` exits 0 with a PASS
       message, exits 1 with FAIL when handed a mismatched snapshot.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_translator_tier1 import _freecad_python


FIXTURE = Path("tests/fixtures/tier1_primitives/box_10x20x30.FCStd")


def _freecad_pythonpath() -> str:
    repo_src = str(Path(__file__).parent.parent / "src")
    fc = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    return ":".join(p for p in (repo_src, fc) if p)


def test_verify_flag_emits_sidecars(tmp_path: Path):
    """`fcstd2b123d --verify` writes .py + .expected.json + .pointcloud.json."""
    py = _freecad_python()
    output = tmp_path / "box.py"
    env = {**os.environ, "PYTHONPATH": _freecad_pythonpath()}

    result = subprocess.run(
        [py, "-m", "fcstd2b123d", str(FIXTURE), "-o", str(output), "--verify"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert result.returncode == 0, (
        f"Translator failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert output.exists()
    assert (tmp_path / "box.expected.json").exists()
    assert (tmp_path / "box.pointcloud.json").exists()

    # The expected.json should be valid Properties JSON.
    data = json.loads((tmp_path / "box.expected.json").read_text())
    assert "volume" in data and "principal_moi" in data


def test_verify_cli_pass(tmp_path: Path):
    """`fcstd2b123d-verify` exits 0 and prints PASS for a fresh emit."""
    py = _freecad_python()
    output = tmp_path / "box.py"
    env = {**os.environ, "PYTHONPATH": _freecad_pythonpath()}
    subprocess.run(
        [py, "-m", "fcstd2b123d", str(FIXTURE), "-o", str(output), "--verify"],
        check=True, env=env, capture_output=True,
    )

    # Now run the verify CLI in this (build123d) interpreter.
    result = subprocess.run(
        [sys.executable, "-m", "fcstd2b123d.verify_cli",
         str(output), str(tmp_path / "box.expected.json")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"Verify failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "PASS" in result.stderr


def test_verify_cli_fail_on_tampered_snapshot(tmp_path: Path):
    """`fcstd2b123d-verify` exits 1 and prints FAIL when properties disagree."""
    py = _freecad_python()
    output = tmp_path / "box.py"
    env = {**os.environ, "PYTHONPATH": _freecad_pythonpath()}
    subprocess.run(
        [py, "-m", "fcstd2b123d", str(FIXTURE), "-o", str(output), "--verify"],
        check=True, env=env, capture_output=True,
    )

    # Bump the snapshot's volume by 50% — should not match any longer.
    expected = tmp_path / "box.expected.json"
    data = json.loads(expected.read_text())
    data["volume"] *= 1.5
    expected.write_text(json.dumps(data))

    result = subprocess.run(
        [sys.executable, "-m", "fcstd2b123d.verify_cli",
         str(output), str(expected)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "FAIL" in result.stderr
