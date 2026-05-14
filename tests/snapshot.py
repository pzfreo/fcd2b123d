"""Snapshot tool: open a .FCStd in FreeCAD, write expected.json next to it.

Runs in a FreeCAD-enabled Python environment (conda-forge freecad).
Not imported by the test suite — invoked manually or by a periodic refresh job.

Usage (regular python with FreeCAD's lib on PYTHONPATH; recommended):
    PYTHONPATH=$CONDA_PREFIX/lib python tests/snapshot.py path/to/file.FCStd [output.expected.json]

Or via freecadcmd, which has its own arg handling — pass script args after --pass:
    freecadcmd tests/snapshot.py --pass path/to/file.FCStd

If no output path is given, writes <input>.expected.json beside the input.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

# Make `from utils.properties import ...` work whether invoked as `python -m tests.snapshot`
# or `python tests/snapshot.py`.
sys.path.insert(0, str(Path(__file__).parent))
from utils.properties import Properties  # noqa: E402


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


def _select_target(doc):
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


def snapshot(input_path: Path, output_path: Path) -> None:
    import FreeCAD

    doc = FreeCAD.openDocument(str(input_path))
    try:
        doc.recompute()
        target = _select_target(doc)
        props = extract_freecad(target)
        props.to_file(output_path)
        print(f"Wrote {output_path} (target: {target.Label} [{target.TypeId}])")
    finally:
        FreeCAD.closeDocument(doc.Name)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("input", type=Path, help="Path to .FCStd input")
    p.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=None,
        help="Output JSON path (default: <input>.expected.json)",
    )
    args = p.parse_args()

    out = args.output if args.output is not None else args.input.with_suffix(".expected.json")
    snapshot(args.input, out)


if __name__ == "__main__":
    main()
