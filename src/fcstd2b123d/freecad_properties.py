"""Extract geometric properties from a FreeCAD shape.

Same fields the comparison utility uses (volume, surface area, COM,
principal MOI). Returns None when the shape is null or 2D-only (a sketch's
Shape is a compound of edges with no volume).

Shared between the snapshot tool and the translator's TranslationContext.
"""

from __future__ import annotations


def extract_properties(shape) -> dict | None:
    """Return a property dict, or None if the shape has no 3D content."""
    import numpy as np

    if shape is None or shape.isNull():
        return None

    # PartDesign containers wrap their result in a Compound even when there
    # is exactly one solid inside.
    if shape.ShapeType == "Compound":
        if not shape.Solids:
            return None
        if len(shape.Solids) > 1:
            # Multi-solid not supported by the comparison story for now.
            return None
        shape = shape.Solids[0]

    if not getattr(shape, "Volume", None) or shape.Volume <= 0:
        return None

    moi = shape.MatrixOfInertia
    M = np.array([
        [moi.A11, moi.A12, moi.A13],
        [moi.A21, moi.A22, moi.A23],
        [moi.A31, moi.A32, moi.A33],
    ])
    eigenvalues = np.linalg.eigvalsh(M)
    principal_moi = sorted(float(v) for v in eigenvalues)

    com = shape.CenterOfMass
    return {
        "volume": float(shape.Volume),
        "surface_area": float(shape.Area),
        "center_of_mass": [float(com.x), float(com.y), float(com.z)],
        "principal_moi": principal_moi,
    }


def freecad_version() -> str | None:
    """Best-effort FreeCAD version string for provenance."""
    try:
        import FreeCAD

        v = FreeCAD.Version()
        return ".".join(v[:3])
    except Exception:
        return None
