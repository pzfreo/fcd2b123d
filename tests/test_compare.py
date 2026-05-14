"""Tests for the comparison utility and the build123d property extractor."""

import math

import pytest

from tests.utils.properties import Properties
from tests.utils.compare import (
    Tolerances,
    assert_equivalent,
    compare,
)


def _props(**kw):
    base = dict(
        volume=1000.0,
        surface_area=600.0,
        center_of_mass=(0.0, 0.0, 0.0),
        principal_moi=(100.0, 100.0, 100.0),
    )
    base.update(kw)
    return Properties(**base)


# --- Comparison logic (no build123d) ---

def test_identical_passes():
    result = compare(_props(), _props())
    assert result.passed
    assert all(r.passed for r in result.results)


def test_volume_within_tolerance_passes():
    expected = _props(volume=1000.0)
    actual = _props(volume=1000.0 + 1000.0 * 5e-7)  # below rel_tol=1e-6
    assert compare(actual, expected).passed


def test_volume_beyond_tolerance_fails_with_diagnostics():
    expected = _props(volume=1000.0)
    actual = _props(volume=1001.0)  # 1e-3 relative error, well beyond 1e-6
    result = compare(actual, expected)
    assert not result.passed
    failed = [r for r in result.results if not r.passed]
    assert len(failed) == 1
    assert failed[0].name == "volume"
    assert "rel_err" in failed[0].detail


def test_surface_area_tolerance_independent_of_volume():
    expected = _props(surface_area=600.0)
    actual = _props(surface_area=601.0)
    result = compare(actual, expected)
    assert not result.passed
    assert any(r.name == "surface_area" and not r.passed for r in result.results)


def test_moi_unordered_inputs_still_match():
    """The comparator must sort both sides before comparing eigenvalues."""
    expected = _props(principal_moi=(100.0, 200.0, 300.0))
    actual = _props(principal_moi=(300.0, 100.0, 200.0))
    assert compare(actual, expected).passed


def test_com_tolerance_scales_with_size():
    # Small object: char_length = volume^(1/3) = 1, abs_tol = 1e-5
    small_expected = Properties(volume=1.0, surface_area=6.0,
                                 center_of_mass=(0.0, 0.0, 0.0),
                                 principal_moi=(1.0, 1.0, 1.0))
    small_actual = Properties(volume=1.0, surface_area=6.0,
                               center_of_mass=(5e-6, 0.0, 0.0),
                               principal_moi=(1.0, 1.0, 1.0))
    assert compare(small_actual, small_expected).passed

    # Large object: char_length = 100, abs_tol = 1e-3
    big_expected = Properties(volume=1e6, surface_area=6e4,
                               center_of_mass=(0.0, 0.0, 0.0),
                               principal_moi=(1e10, 1e10, 1e10))
    big_actual = Properties(volume=1e6, surface_area=6e4,
                             center_of_mass=(5e-4, 0.0, 0.0),  # within 1e-3
                             principal_moi=(1e10, 1e10, 1e10))
    assert compare(big_actual, big_expected).passed

    # Same absolute COM offset should fail for the small object
    small_actual_far = Properties(volume=1.0, surface_area=6.0,
                                   center_of_mass=(5e-4, 0.0, 0.0),
                                   principal_moi=(1.0, 1.0, 1.0))
    assert not compare(small_actual_far, small_expected).passed


def test_assert_raises_on_failure():
    expected = _props()
    actual = _props(volume=expected.volume * 1.1)
    with pytest.raises(AssertionError) as exc:
        assert_equivalent(actual, expected)
    msg = str(exc.value)
    # Failure message names all properties for diagnostic context
    for name in ("volume", "surface_area", "center_of_mass", "principal_moi"):
        assert name in msg


def test_custom_tolerances_can_relax_or_tighten():
    expected = _props(volume=1000.0)
    actual = _props(volume=1001.0)
    # Default 1e-6 fails
    assert not compare(actual, expected).passed
    # Relaxed 1e-2 passes
    assert compare(actual, expected, Tolerances(volume_rel=1e-2)).passed


# --- build123d extractor: requires build123d at import time ---

build123d = pytest.importorskip("build123d")


def test_extract_unit_cube_centered_on_origin():
    from tests.utils.compare import extract_build123d

    box = build123d.Box(10, 10, 10)
    props = extract_build123d(box)

    assert math.isclose(props.volume, 1000.0, rel_tol=1e-9)
    assert math.isclose(props.surface_area, 600.0, rel_tol=1e-9)
    for c in props.center_of_mass:
        assert abs(c) < 1e-9
    # Uniform 10x10x10 cube about its center: I = (1/12) m (a^2 + b^2) with m=1000
    expected_moi = 1000.0 * (100.0 + 100.0) / 12.0
    for moi in props.principal_moi:
        assert math.isclose(moi, expected_moi, rel_tol=1e-6)


def test_extract_rectangular_box_principal_moments_distinct():
    from tests.utils.compare import extract_build123d

    box = build123d.Box(10, 20, 30)  # 6000 mm^3
    props = extract_build123d(box)

    assert math.isclose(props.volume, 6000.0, rel_tol=1e-9)
    # Surface area = 2*(10*20 + 20*30 + 10*30) = 2200
    assert math.isclose(props.surface_area, 2200.0, rel_tol=1e-9)
    # I_x = 1/12 * 6000 * (20^2 + 30^2) = 650000
    # I_y = 1/12 * 6000 * (10^2 + 30^2) = 500000
    # I_z = 1/12 * 6000 * (10^2 + 20^2) = 250000
    # Sorted ascending:
    expected = (250000.0, 500000.0, 650000.0)
    for actual, e in zip(props.principal_moi, expected):
        assert math.isclose(actual, e, rel_tol=1e-6)


def test_extract_then_compare_round_trip():
    """Extract from a build123d shape, store via Properties, compare against a fresh extraction.

    Proves the comparison path works end-to-end on a real shape.
    """
    from tests.utils.compare import extract_build123d

    box = build123d.Box(10, 20, 30)
    p1 = extract_build123d(box)
    p2 = extract_build123d(box)
    result = compare(p1, p2)
    assert result.passed, result.format()
