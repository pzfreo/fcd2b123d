"""Build123d-side property extraction and comparison.

Used at test time. Imports build123d / OCP — not safe to import from any code
path that needs to run in environments without those libraries.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .properties import Properties


@dataclass
class Tolerances:
    volume_rel: float = 1e-6
    volume_abs: float = 1e-9
    area_rel: float = 1e-6
    area_abs: float = 1e-9
    com_abs_scale: float = 1e-5   # multiplied by characteristic length (volume^(1/3))
    moi_rel: float = 1e-5
    moi_abs: float = 1e-9


@dataclass
class PropertyResult:
    name: str
    expected: Any
    actual: Any
    passed: bool
    detail: str


@dataclass
class ComparisonResult:
    results: list[PropertyResult]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def format(self) -> str:
        lines = []
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(
                f"  {r.name:18s} {status}  expected={r.expected}  actual={r.actual}  ({r.detail})"
            )
        return "\n".join(lines)


def _compare_scalar(name, expected, actual, rel_tol, abs_tol) -> PropertyResult:
    passed = math.isclose(expected, actual, rel_tol=rel_tol, abs_tol=abs_tol)
    abs_err = abs(expected - actual)
    rel_err = abs_err / abs(expected) if expected != 0 else float("inf")
    return PropertyResult(
        name=name,
        expected=expected,
        actual=actual,
        passed=passed,
        detail=f"rel_err={rel_err:.2e}, abs_err={abs_err:.2e}, rel_tol={rel_tol:.0e}, abs_tol={abs_tol:.0e}",
    )


def _compare_vector_abs(name, expected, actual, abs_tol) -> PropertyResult:
    deltas = [abs(e - a) for e, a in zip(expected, actual)]
    max_delta = max(deltas) if deltas else 0.0
    passed = max_delta <= abs_tol
    return PropertyResult(
        name=name,
        expected=tuple(expected),
        actual=tuple(actual),
        passed=passed,
        detail=f"max_delta={max_delta:.2e}, abs_tol={abs_tol:.2e}",
    )


def _compare_sorted_triple(name, expected, actual, rel_tol, abs_tol) -> PropertyResult:
    exp = tuple(sorted(expected))
    act = tuple(sorted(actual))
    deltas = [abs(e - a) for e, a in zip(exp, act)]
    rel_errs = [d / abs(e) if e != 0 else float("inf") for d, e in zip(deltas, exp)]
    passed = all(
        math.isclose(e, a, rel_tol=rel_tol, abs_tol=abs_tol) for e, a in zip(exp, act)
    )
    return PropertyResult(
        name=name,
        expected=exp,
        actual=act,
        passed=passed,
        detail=f"max_rel_err={max(rel_errs):.2e}, max_abs_err={max(deltas):.2e}",
    )


def compare(
    actual: Properties,
    expected: Properties,
    tolerances: Tolerances | None = None,
) -> ComparisonResult:
    t = tolerances or Tolerances()
    char_length = expected.volume ** (1.0 / 3.0) if expected.volume > 0 else 1.0
    com_abs_tol = char_length * t.com_abs_scale

    results = [
        _compare_scalar(
            "volume", expected.volume, actual.volume, t.volume_rel, t.volume_abs
        ),
        _compare_scalar(
            "surface_area",
            expected.surface_area,
            actual.surface_area,
            t.area_rel,
            t.area_abs,
        ),
        _compare_vector_abs(
            "center_of_mass",
            expected.center_of_mass,
            actual.center_of_mass,
            com_abs_tol,
        ),
        _compare_sorted_triple(
            "principal_moi",
            expected.principal_moi,
            actual.principal_moi,
            t.moi_rel,
            t.moi_abs,
        ),
    ]
    return ComparisonResult(results=results)


def assert_equivalent(
    actual: Properties,
    expected: Properties,
    tolerances: Tolerances | None = None,
) -> None:
    result = compare(actual, expected, tolerances)
    if not result.passed:
        raise AssertionError("Properties do not match:\n" + result.format())


def extract_build123d(part) -> Properties:
    """Compute geometric properties of a build123d Part/Compound/Solid.

    Accepts anything with a `.wrapped` attribute returning a TopoDS_Shape.
    Drops directly to OCCT for the MOI tensor because build123d's high-level
    API doesn't expose the full inertia matrix in a stable place across
    versions.
    """
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    import numpy as np

    shape = part.wrapped

    vol_props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, vol_props)
    volume = vol_props.Mass()
    com = vol_props.CentreOfMass()
    moi_matrix = vol_props.MatrixOfInertia()

    surf_props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, surf_props)
    surface_area = surf_props.Mass()

    # OCCT gp_Mat is 1-indexed via Value(i, j)
    M = np.array(
        [[moi_matrix.Value(i, j) for j in range(1, 4)] for i in range(1, 4)]
    )
    eigenvalues = np.linalg.eigvalsh(M)
    principal_moi = tuple(sorted(float(v) for v in eigenvalues))

    return Properties(
        volume=float(volume),
        surface_area=float(surface_area),
        center_of_mass=(float(com.X()), float(com.Y()), float(com.Z())),
        principal_moi=principal_moi,
        source="build123d",
    )
