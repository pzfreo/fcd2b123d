"""Verify the translator emits code that passes ruff lint checks.

This is a smoke test against the *translator*: it makes sure the emitter
doesn't drift toward unused imports, shadowed builtins, or other smells that
would make the output worse for LLM-driven iteration (the project's purpose
per SPEC §1).

Runs in the build123d env. Translator output is captured via subprocess into
a FreeCAD-enabled Python, so the same skip applies as test_translator_tier1.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_translator_tier1 import _translate

FIXTURE = Path("tests/fixtures/tier1_primitives/box_10x20x30.FCStd")


def test_emitted_box_passes_ruff_check():
    if shutil.which("ruff") is None:
        pytest.skip("ruff binary not on PATH; install with `uv sync --extra dev`")
    if not os.environ.get("FCSTD2B123D_FREECAD_PYTHON"):
        pytest.skip("FCSTD2B123D_FREECAD_PYTHON not set; can't translate")

    source = _translate(FIXTURE)

    # `--no-cache` keeps the test self-contained. `-` reads from stdin.
    # Default rule set is fine — we're catching gross emitter regressions,
    # not auditing for every style nit.
    result = subprocess.run(
        ["ruff", "check", "--no-cache", "-"],
        input=source,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"ruff check failed on translated source:\n"
        f"--- stderr ---\n{result.stderr}\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- source ---\n{source}"
    )
