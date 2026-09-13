"""Validate the parametric emit for tier-6 (spreadsheet-driven) fixtures.

For files with a Spreadsheet driving property expressions, the translator
exposes the cell names as parameters. With ``--emit=function`` it produces
``def make_part(param1=…, ...):``; with ``--emit=class`` (the default
since #128) it produces ``class Foo(BasePartObject): __init__(param1=…)``.

This test covers:
1. ``--emit=function`` still emits the ``make_part`` wrapper (back-compat).
2. ``make_part(width=…)`` overrides scale geometry as expected.
3. ``--emit=class`` (the default) takes the same params as ``__init__``
   kwargs and produces equivalent geometry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_translator_tier1 import _translate
from fcstd2b123d.verify import assert_equivalent, extract_build123d
from fcstd2b123d.properties import Properties

SPREADSHEET_BOX = Path("tests/fixtures/tier6_parametric/spreadsheet_box.FCStd")


def test_parametric_emit_is_function_wrapped():
    """``--emit=function`` produces the ``make_part`` wrapper."""
    source = _translate(SPREADSHEET_BOX, emit="function")
    assert "def make_part(" in source, (
        "Parametric file should emit a make_part function under --emit=function.\n\nSource:\n" + source
    )
    assert "result = make_part()" in source


def test_parametric_defaults_match_snapshot():
    """Default emit (now class) instantiated with defaults matches the
    FreeCAD-snapshotted geometry."""
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


def test_parametric_override_scales_geometry_function():
    """Doubling ``width`` should double the volume — function-form emit."""
    source = _translate(SPREADSHEET_BOX, emit="function")
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


def test_parametric_override_scales_geometry_class():
    """Doubling ``width`` should double the volume — class-form emit
    (the default since #128)."""
    source = _translate(SPREADSHEET_BOX)
    namespace: dict = {}
    exec(source, namespace)

    # The class is the only non-builtin uppercase-named binding produced
    # by the emit. Find it dynamically so the test isn't coupled to the
    # exact class name (derived from the source filename stem).
    cls_candidates = [
        v for k, v in namespace.items()
        if k.startswith("Spreadsheet") and isinstance(v, type)
    ]
    assert cls_candidates, (
        f"Expected a Spreadsheet*-named class in namespace; got "
        f"{[k for k in namespace if not k.startswith('_')]}"
    )
    cls = cls_candidates[0]
    base = cls()
    wide = cls(width=50)
    base_props = extract_build123d(base)
    wide_props = extract_build123d(wide)
    assert wide_props.volume == pytest.approx(base_props.volume * 2, rel=1e-9), (
        f"Expected volume to double when width doubled (class form): "
        f"base={base_props.volume}, wide={wide_props.volume}"
    )


# The VarSet twin of spreadsheet_box: FreeCAD 1.0 replaced the parameter
# spreadsheet with App::VarSet, which holds its variables as dynamic
# properties. No Parts Library file can stand in here — all 29 that use a
# VarSet also carry Python feature types the translator refuses by design
# (SPEC 13.5) — so this fixture is hand-built the same way spreadsheet_box is.
VARSET_BOX = Path("tests/fixtures/tier6_parametric/varset_box.FCStd")


def test_varset_parameters_reach_the_signature():
    """A VarSet drives the emit exactly as a spreadsheet does."""
    source = _translate(VARSET_BOX, emit="function")
    assert "def make_part(" in source, (
        "VarSet-driven file should emit a make_part wrapper.\n\nSource:\n" + source
    )
    signature = source.split("def make_part(", 1)[1].split(")", 1)[0]
    for name in ("width", "depth", "height"):
        assert name in signature, (
            f"VarSet variable {name!r} missing from signature: {signature}"
        )
    # The fixture also holds a string variable; only dimensions are parameters.
    assert "material" not in source


def test_varset_defaults_match_snapshot():
    """Defaults reproduce the geometry FreeCAD itself builds."""
    source = _translate(VARSET_BOX)
    namespace: dict = {}
    exec(source, namespace)
    part = namespace["result"]
    assert_equivalent(
        extract_build123d(part),
        Properties.from_file(VARSET_BOX.with_suffix(".expected.json")),
        actual_part=part,
        pointcloud_path=VARSET_BOX.with_suffix(".pointcloud.json"),
    )


def test_varset_override_scales_geometry():
    """The emitted parameter is live, not a decorative default."""
    source = _translate(VARSET_BOX, emit="function")
    namespace: dict = {}
    exec(source, namespace)
    make_part = namespace["make_part"]
    base = extract_build123d(make_part())
    wide = extract_build123d(make_part(width=80))  # source value was 40
    assert wide.volume == pytest.approx(base.volume * 2, rel=1e-9), (
        f"Expected volume to double when width doubled: base={base.volume}, "
        f"wide={wide.volume}"
    )
