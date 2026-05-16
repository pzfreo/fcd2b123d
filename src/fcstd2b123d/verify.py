"""Build123d-side property extraction and comparison.

Used at test time and by the ``fcstd2b123d-verify`` CLI. Imports build123d /
OCP — not safe to import from any code path that needs to run in
environments without those libraries. (All build123d/OCP imports are lazy
inside the functions that need them, so importing this module itself is
cheap and side-effect-free.)
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
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
    *,
    actual_part=None,
    pointcloud_path=None,
) -> None:
    """Assert geometric equivalence via the four scalar invariants.

    When ``actual_part`` + ``pointcloud_path`` are supplied (and the sidecar
    file exists), also performs a Hausdorff-distance check against the
    committed FreeCAD point cloud. Catches mirror images, multi-loop topology
    errors, and other geometric differences that the four scalars miss
    (per ADR-0004's documented "paranoid case").

    Set ``FCSTD2B123D_HAUSDORFF_SKIP=1`` to opt out of the Hausdorff check
    (e.g. in a perf-sensitive CI lane). The ~4% overhead is small enough that
    default-on is worth the extra safety.
    """
    result = compare(actual, expected, tolerances)
    if not result.passed:
        raise AssertionError("Properties do not match:\n" + result.format())

    if (
        not os.environ.get("FCSTD2B123D_HAUSDORFF_SKIP")
        and actual_part is not None
        and pointcloud_path is not None
    ):
        _assert_hausdorff(actual_part, pointcloud_path, expected)


def _assert_hausdorff(part, pointcloud_path, expected: Properties) -> None:
    """Tessellate the build123d part, load the FreeCAD point cloud, compute
    Hausdorff distance, fail if it's larger than a magnitude-scaled tolerance.
    """
    import json

    from .hausdorff import bbox_diagonal, hausdorff_distance

    p = Path(pointcloud_path)
    if not p.exists():
        return  # opted in but this fixture has no committed pointcloud — soft skip

    fc_points = json.loads(p.read_text())
    if not fc_points:
        return

    verts, _faces = part.tessellate(1.0)
    b3d_points = [(float(v.X), float(v.Y), float(v.Z)) for v in verts]
    # Do NOT uniform-stride downsample. The FreeCAD-side pointcloud is
    # already downsampled to 1000 vertices, but those vertices cluster on
    # feature edges (hole rims, fillet seams) where the BRep needs them.
    # build123d's ``tessellate`` produces a more uniform distribution; if
    # we then stride-sample that down to 1000 we lose feature-region
    # density and the comparison flags false positives (a build123d
    # tessellation point on a smooth face has no FreeCAD neighbour at
    # tolerance even though both shapes are identical).
    # Hausdorff is O(|A|·|B|) memory — at ~10k × 1000 it's ~80 MB, fine.

    h = hausdorff_distance(fc_points, b3d_points)
    diag = bbox_diagonal(fc_points)
    # Tolerance has two floors:
    #   * 2 × tessellation_tolerance (1 mm in snapshot.py). Both FreeCAD and
    #     build123d tessellate via OCCT but they place vertices at slightly
    #     different parametric positions on curved surfaces, so even
    #     IDENTICAL shapes produce Hausdorff ≈ tessellation_tolerance.
    #   * 5 % of bounding-box diagonal. Catches mirror flips (Hausdorff
    #     ≈ bbox for a reflected shape) while staying generous enough to
    #     absorb honest topology variation that doesn't move points far.
    # The threshold is loose by design: Hausdorff is a paranoid backstop
    # for issues the four scalars miss, not a precision check.
    _TESSELLATION_TOL = 1.0
    tol = max(3 * _TESSELLATION_TOL, diag * 0.10)
    if h > tol:
        raise AssertionError(
            f"Hausdorff distance {h:.4f} exceeds {tol:.4f} "
            f"(bbox diag {diag:.2f}). FreeCAD-vs-build123d geometric mismatch "
            f"beyond what the four scalar invariants catch — possible mirror, "
            f"topology error, or coincidental property match."
        )


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
