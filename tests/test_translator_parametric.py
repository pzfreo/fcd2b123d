"""Validate the function-wrapped emit for parametric (tier-6) fixtures.

For files with a Spreadsheet driving property expressions, the translator
emits ``def make_part(param1=…, param2=…, ...):`` so a downstream consumer
can call ``make_part(width=50)`` and get a variant.

This test confirms:
1. Calling make_part() with defaults matches the FreeCAD-snapshotted geometry.
2. Calling make_part(...) with overridden parameters changes the geometry
   in the expected direction.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_translator_tier1 import _translate
from tests.utils.compare import assert_equivalent, extract_build123d
from tests.utils.properties import Properties

SPREADSHEET_BOX = Path("tests/fixtures/tier6_parametric/spreadsheet_box.FCStd")


def test_parametric_emit_is_function_wrapped():
    source = _translate(SPREADSHEET_BOX)
    assert "def make_part(" in source, (
        "Parametric file should emit a make_part function.\n\nSource:\n" + source
    )
    assert "result = make_part()" in source


def test_parametric_defaults_match_snapshot():
    source = _translate(SPREADSHEET_BOX)
    namespace: dict = {}
    exec(source, namespace)
    part = namespace["result"]
    actual = extract_build123d(part)
    expected = Properties.from_file(SPREADSHEET_BOX.with_suffix(".expected.json"))
    assert_equivalent(
        actual, expected,
        actual_part=part,
        pointcloud_path=SPREADSHEET_BOX.with_suffix(".pointcloud.json"),
    )


def test_parametric_override_scales_geometry():
    """Doubling ``width`` should double the volume (for a box with two other
    dimensions unchanged)."""
    source = _translate(SPREADSHEET_BOX)
    namespace: dict = {}
    exec(source, namespace)

    make_part = namespace["make_part"]
    base = make_part()
    wide = make_part(width=50)  # source value was 25; doubled
    base_props = extract_build123d(base)
    wide_props = extract_build123d(wide)
    assert wide_props.volume == pytest.approx(base_props.volume * 2, rel=1e-9), (
        f"Expected volume to double when width doubled: base={base_props.volume}, "
        f"wide={wide_props.volume}"
    )
