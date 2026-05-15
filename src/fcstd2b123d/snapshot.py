"""FreeCAD-side property extraction.

Runs in a FreeCAD-enabled Python environment. Imports FreeCAD lazily so this
module is importable from any code path (the imports only resolve when the
extraction functions are called).

Used by the translator CLI's ``--verify`` flag and by the standalone
``tests/snapshot.py`` script.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .properties import Properties


_POINTCLOUD_MAX = 1000


def extract_freecad(obj) -> Properties:
    """Compute properties from a FreeCAD object's Shape (world frame)."""
    import numpy as np

    shape = obj.Shape
    # PartDesign::Body and similar containers wrap their result in a Compound
    # even when there is exactly one solid. Unwrap so MatrixOfInertia works.
    if shape.ShapeType == "Compound":
        if len(shape.Solids) == 1:
            shape = shape.Solids[0]
        else:
            raise RuntimeError(
                f"Compound contains {len(shape.Solids)} solids; multi-solid "
                f"targets not supported in v1"
            )
    moi = shape.MatrixOfInertia
    M = np.array(
        [
            [moi.A11, moi.A12, moi.A13],
            [moi.A21, moi.A22, moi.A23],
            [moi.A31, moi.A32, moi.A33],
        ]
    )
    eigenvalues = np.linalg.eigvalsh(M)
    principal_moi = tuple(sorted(float(v) for v in eigenvalues))

    com = shape.CenterOfMass
    return Properties(
        volume=float(shape.Volume),
        surface_area=float(shape.Area),
        center_of_mass=(float(com.x), float(com.y), float(com.z)),
        principal_moi=principal_moi,
        source=f"freecad-{_freecad_version()}",
        snapshot_date=date.today().isoformat(),
    )


def _freecad_version() -> str:
    import FreeCAD

    v = FreeCAD.Version()
    return f"{v[0]}.{v[1]}.{v[2]}"


def select_target(doc):
    """Pick the object to snapshot.

    Heuristic: prefer the last PartDesign::Body; otherwise the last object
    with a non-null Shape. Real fixtures should contain one clear target.
    Multi-body selection logic comes later (tier 5+).
    """
    bodies = [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]
    if bodies:
        return bodies[-1]

    for o in reversed(doc.Objects):
        if hasattr(o, "Shape") and o.Shape and not o.Shape.isNull():
            return o

    raise RuntimeError("No object with a Shape found in document")


def tessellate(shape, tolerance: float = 1.0) -> list[tuple[float, float, float]]:
    """Tessellate a FreeCAD shape and return its mesh vertices.

    Used by the Hausdorff-distance fallback (paired with the .expected.json).
    Coarse tolerance keeps file size manageable; complex shapes with too
    many vertices are downsampled to ``_POINTCLOUD_MAX`` via stride.
    Hausdorff between equally-sampled clouds is robust to mirror flips
    and topology errors at the bbox scale; we don't need millions of
    points to catch those.
    """
    if shape.ShapeType == "Compound":
        if len(shape.Solids) == 1:
            shape = shape.Solids[0]
        elif not shape.Solids:
            return []
        else:
            raise RuntimeError(
                f"Compound contains {len(shape.Solids)} solids; multi-solid "
                f"targets not supported in v1"
            )
    verts, _faces = shape.tessellate(tolerance)
    points = [(float(v.x), float(v.y), float(v.z)) for v in verts]
    if len(points) > _POINTCLOUD_MAX:
        step = len(points) / _POINTCLOUD_MAX
        points = [points[int(i * step)] for i in range(_POINTCLOUD_MAX)]
    return points


def snapshot_fcstd(
    input_path: Path,
    expected_path: Path | None = None,
    pointcloud_path: Path | None = None,
) -> tuple[Path, Path, int]:
    """Open a .FCStd, pick the target object, write expected.json + pointcloud.json.

    Returns ``(expected_path, pointcloud_path, vertex_count)``.

    Defaults the output paths to siblings of ``input_path``:
      * ``<stem>.expected.json``
      * ``<stem>.pointcloud.json``
    """
    import FreeCAD

    expected_path = expected_path or input_path.with_suffix(".expected.json")
    pointcloud_path = pointcloud_path or input_path.with_suffix(".pointcloud.json")

    doc = FreeCAD.openDocument(str(input_path))
    try:
        doc.recompute()
        target = select_target(doc)
        props = extract_freecad(target)
        props.to_file(expected_path)

        pointcloud = tessellate(target.Shape)
        pointcloud_path.write_text(json.dumps(pointcloud) + "\n")
        return expected_path, pointcloud_path, len(pointcloud)
    finally:
        FreeCAD.closeDocument(doc.Name)
