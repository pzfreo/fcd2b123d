"""Programmatically generate the synthetic test fixtures.

Runs in the FreeCAD env. Idempotent — re-running overwrites existing files.
Used once to seed the fixture set covering tiers that FreeCAD's bundled
examples don't include (tier 1 primitives beyond Box, tier 3 fillets/chamfers,
tier 6 spreadsheets).

Usage:
    PYTHONPATH=$CONDA_PREFIX/lib python tools/synthesize_fixtures.py [--out tests/fixtures]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import FreeCAD
import Part
import PartDesign
import Sketcher


def _save(doc, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.recompute()
    doc.saveAs(str(path))
    print(f"  {path}")
    FreeCAD.closeDocument(doc.Name)


# --- Tier 1: primitives (Part workbench) ---

def make_cylinder(out: Path) -> None:
    doc = FreeCAD.newDocument("cyl")
    cyl = doc.addObject("Part::Cylinder", "Cyl")
    cyl.Radius = 10
    cyl.Height = 30
    _save(doc, out / "tier1_primitives/cylinder_r10_h30.FCStd")


def make_sphere(out: Path) -> None:
    doc = FreeCAD.newDocument("sph")
    sph = doc.addObject("Part::Sphere", "Sph")
    sph.Radius = 15
    _save(doc, out / "tier1_primitives/sphere_r15.FCStd")


def make_cone(out: Path) -> None:
    doc = FreeCAD.newDocument("cone")
    c = doc.addObject("Part::Cone", "Cone")
    c.Radius1 = 10
    c.Radius2 = 5
    c.Height = 20
    _save(doc, out / "tier1_primitives/cone_r10_r5_h20.FCStd")


def make_torus(out: Path) -> None:
    doc = FreeCAD.newDocument("tor")
    t = doc.addObject("Part::Torus", "Tor")
    t.Radius1 = 20  # major
    t.Radius2 = 5   # minor
    _save(doc, out / "tier1_primitives/torus_R20_r5.FCStd")


# --- Tier 2: minimal PartDesign Body + Sketch + Pad ---

def make_simple_pad(out: Path) -> None:
    doc = FreeCAD.newDocument("pad")
    body = doc.addObject("PartDesign::Body", "Body")
    sketch = body.newObject("Sketcher::SketchObject", "Profile")
    sketch.AttachmentSupport = (body.Origin.OutList[3], [""])  # XY plane
    sketch.MapMode = "FlatFace"
    # 20x10 rectangle centered at origin
    import FreeCAD as FC
    v = FC.Vector
    sketch.addGeometry(Part.LineSegment(v(-10, -5, 0), v(10, -5, 0)), False)
    sketch.addGeometry(Part.LineSegment(v(10, -5, 0), v(10, 5, 0)), False)
    sketch.addGeometry(Part.LineSegment(v(10, 5, 0), v(-10, 5, 0)), False)
    sketch.addGeometry(Part.LineSegment(v(-10, 5, 0), v(-10, -5, 0)), False)
    sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 1, 1))
    sketch.addConstraint(Sketcher.Constraint("Coincident", 1, 2, 2, 1))
    sketch.addConstraint(Sketcher.Constraint("Coincident", 2, 2, 3, 1))
    sketch.addConstraint(Sketcher.Constraint("Coincident", 3, 2, 0, 1))
    sketch.addConstraint(Sketcher.Constraint("Horizontal", 0))
    sketch.addConstraint(Sketcher.Constraint("Horizontal", 2))
    sketch.addConstraint(Sketcher.Constraint("Vertical", 1))
    sketch.addConstraint(Sketcher.Constraint("Vertical", 3))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sketch
    pad.Length = 8
    _save(doc, out / "tier2_partdesign/simple_pad.FCStd")


def make_pad_twolengths(out: Path) -> None:
    """Minimal fixture for #29: Pad with Type='TwoLengths' (fwd + bwd extrude)."""
    doc = FreeCAD.newDocument("pad2l")
    body = doc.addObject("PartDesign::Body", "Body")
    sketch = body.newObject("Sketcher::SketchObject", "Profile")
    sketch.AttachmentSupport = (body.Origin.OutList[3], [""])
    sketch.MapMode = "FlatFace"
    v = FreeCAD.Vector
    # 20x10 rectangle centered at origin
    sketch.addGeometry(Part.LineSegment(v(-10, -5, 0), v(10, -5, 0)), False)
    sketch.addGeometry(Part.LineSegment(v(10, -5, 0), v(10, 5, 0)), False)
    sketch.addGeometry(Part.LineSegment(v(10, 5, 0), v(-10, 5, 0)), False)
    sketch.addGeometry(Part.LineSegment(v(-10, 5, 0), v(-10, -5, 0)), False)
    for i in range(4):
        sketch.addConstraint(
            Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1)
        )
    sketch.addConstraint(Sketcher.Constraint("Horizontal", 0))
    sketch.addConstraint(Sketcher.Constraint("Horizontal", 2))
    sketch.addConstraint(Sketcher.Constraint("Vertical", 1))
    sketch.addConstraint(Sketcher.Constraint("Vertical", 3))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sketch
    pad.Type = "TwoLengths"
    pad.Length = 6   # forward
    pad.Length2 = 4  # backward
    _save(doc, out / "tier2_partdesign/pad_twolengths.FCStd")


def make_pad_with_bspline(out: Path) -> None:
    """Tear-drop profile (line + line + BSpline) padded — #56 BSpline support.

    Two straight edges down the long axis form a 'V' from (0,0) to (10,5)
    and back to (10,-5); a degree-2 B-spline from (10,-5) through (15,0)
    to (10,5) closes the loop with a smooth curved end.
    """
    doc = FreeCAD.newDocument("padbsp")
    body = doc.addObject("PartDesign::Body", "Body")
    sketch = body.newObject("Sketcher::SketchObject", "Profile")
    sketch.AttachmentSupport = (body.Origin.OutList[3], [""])
    sketch.MapMode = "FlatFace"
    v = FreeCAD.Vector
    # Two straight edges forming a V opening to +X.
    sketch.addGeometry(Part.LineSegment(v(0, 0, 0), v(10, 5, 0)), False)
    sketch.addGeometry(Part.LineSegment(v(10, -5, 0), v(0, 0, 0)), False)
    # Degree-2 (quadratic) B-spline closing the V on the +X side.
    poles = [v(10, 5, 0), v(20, 0, 0), v(10, -5, 0)]
    bsp = Part.BSplineCurve()
    bsp.buildFromPoles(poles, False, 2)  # not periodic, degree 2
    sketch.addGeometry(bsp, False)
    # Endpoint coincidence so the chain closes.
    sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 2, 2, 1))
    sketch.addConstraint(Sketcher.Constraint("Coincident", 2, 2, 1, 1))
    sketch.addConstraint(Sketcher.Constraint("Coincident", 1, 2, 0, 1))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sketch
    pad.Length = 4
    _save(doc, out / "tier2_partdesign/pad_with_bspline.FCStd")


# --- Tier 3: Pad + Fillet (the topological-naming test) ---

def make_box_with_fillet(out: Path) -> None:
    doc = FreeCAD.newDocument("fil")
    body = doc.addObject("PartDesign::Body", "Body")
    sketch = body.newObject("Sketcher::SketchObject", "Profile")
    sketch.AttachmentSupport = (body.Origin.OutList[3], [""])
    sketch.MapMode = "FlatFace"
    v = FreeCAD.Vector
    sketch.addGeometry(Part.LineSegment(v(0, 0, 0), v(30, 0, 0)), False)
    sketch.addGeometry(Part.LineSegment(v(30, 0, 0), v(30, 20, 0)), False)
    sketch.addGeometry(Part.LineSegment(v(30, 20, 0), v(0, 20, 0)), False)
    sketch.addGeometry(Part.LineSegment(v(0, 20, 0), v(0, 0, 0)), False)
    for i in range(4):
        sketch.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    sketch.addConstraint(Sketcher.Constraint("Horizontal", 0))
    sketch.addConstraint(Sketcher.Constraint("Horizontal", 2))
    sketch.addConstraint(Sketcher.Constraint("Vertical", 1))
    sketch.addConstraint(Sketcher.Constraint("Vertical", 3))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sketch
    pad.Length = 15
    doc.recompute()

    # Fillet all four vertical edges of the pad (the side edges, length 15).
    fillet = body.newObject("PartDesign::Fillet", "Fillet")
    pad_shape = pad.Shape
    vertical_edges = []
    for idx, edge in enumerate(pad_shape.Edges, start=1):
        # Z-axis aligned edges have a vertex Z difference of ~15 (the pad length)
        v0, v1 = edge.Vertexes[0].Point, edge.Vertexes[1].Point
        if abs(v0.x - v1.x) < 1e-6 and abs(v0.y - v1.y) < 1e-6 and abs(v0.z - v1.z) > 1:
            vertical_edges.append(f"Edge{idx}")
    fillet.Base = (pad, vertical_edges)
    fillet.Radius = 3
    _save(doc, out / "tier3_filletchamfer/box_with_fillet.FCStd")


# --- Tier 4: Patterns ---

def _add_rectangle(sketch, x0, y0, x1, y1) -> None:
    """Append a 4-edge rectangle with coincidence + H/V constraints."""
    v = FreeCAD.Vector
    base = len(sketch.Geometry)
    sketch.addGeometry(Part.LineSegment(v(x0, y0, 0), v(x1, y0, 0)), False)
    sketch.addGeometry(Part.LineSegment(v(x1, y0, 0), v(x1, y1, 0)), False)
    sketch.addGeometry(Part.LineSegment(v(x1, y1, 0), v(x0, y1, 0)), False)
    sketch.addGeometry(Part.LineSegment(v(x0, y1, 0), v(x0, y0, 0)), False)
    for i in range(4):
        sketch.addConstraint(
            Sketcher.Constraint("Coincident", base + i, 2, base + (i + 1) % 4, 1)
        )
    sketch.addConstraint(Sketcher.Constraint("Horizontal", base + 0))
    sketch.addConstraint(Sketcher.Constraint("Horizontal", base + 2))
    sketch.addConstraint(Sketcher.Constraint("Vertical", base + 1))
    sketch.addConstraint(Sketcher.Constraint("Vertical", base + 3))


def _add_circle(sketch, cx, cy, r) -> None:
    v = FreeCAD.Vector
    sketch.addGeometry(Part.Circle(v(cx, cy, 0), v(0, 0, 1), r), False)


def make_linear_pattern_holes(out: Path) -> None:
    """Plate (60x20x5) with a single Pocket hole patterned 4× along +X."""
    doc = FreeCAD.newDocument("lpattern")
    body = doc.addObject("PartDesign::Body", "Body")

    # Base plate sketch on XY
    s1 = body.newObject("Sketcher::SketchObject", "Plate")
    s1.AttachmentSupport = (body.Origin.OutList[3], [""])
    s1.MapMode = "FlatFace"
    _add_rectangle(s1, -30, -10, 30, 10)
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = s1
    pad.Length = 5
    doc.recompute()

    # Hole sketch on the top face — go through the plate.
    s2 = body.newObject("Sketcher::SketchObject", "Hole")
    s2.AttachmentSupport = (body.Origin.OutList[3], [""])
    s2.MapMode = "FlatFace"
    _add_circle(s2, -22, 0, 2)
    pocket = body.newObject("PartDesign::Pocket", "Pocket")
    pocket.Profile = s2
    pocket.Length = 10
    pocket.Reversed = True  # sketch on XY (z=0); body is +Z, carve into it
    doc.recompute()

    lp = body.newObject("PartDesign::LinearPattern", "LinearPattern")
    lp.Originals = [pocket]
    lp.Direction = (body.Origin.OutList[0], ["X_Axis"])
    lp.Length = 44
    lp.Occurrences = 4
    body.Tip = lp
    _save(doc, out / "tier4_patterns/linear_pattern_holes.FCStd")


def make_polar_pattern_holes(out: Path) -> None:
    """Disc with Pocket hole patterned 6× around +Z."""
    doc = FreeCAD.newDocument("ppattern")
    body = doc.addObject("PartDesign::Body", "Body")

    s1 = body.newObject("Sketcher::SketchObject", "Disc")
    s1.AttachmentSupport = (body.Origin.OutList[3], [""])
    s1.MapMode = "FlatFace"
    _add_circle(s1, 0, 0, 25)
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = s1
    pad.Length = 6
    doc.recompute()

    s2 = body.newObject("Sketcher::SketchObject", "Hole")
    s2.AttachmentSupport = (body.Origin.OutList[3], [""])
    s2.MapMode = "FlatFace"
    _add_circle(s2, 18, 0, 2)
    pocket = body.newObject("PartDesign::Pocket", "Pocket")
    pocket.Profile = s2
    pocket.Length = 12
    pocket.Reversed = True  # carve +Z into the body
    doc.recompute()

    pp = body.newObject("PartDesign::PolarPattern", "PolarPattern")
    pp.Originals = [pocket]
    pp.Axis = (body.Origin.OutList[2], ["Z_Axis"])
    pp.Angle = 360
    pp.Occurrences = 6
    body.Tip = pp
    _save(doc, out / "tier4_patterns/polar_pattern_holes.FCStd")


def make_mirrored_tab(out: Path) -> None:
    """Plate with an off-center tab Pad, mirrored across YZ plane."""
    doc = FreeCAD.newDocument("mirror")
    body = doc.addObject("PartDesign::Body", "Body")

    s1 = body.newObject("Sketcher::SketchObject", "Plate")
    s1.AttachmentSupport = (body.Origin.OutList[3], [""])
    s1.MapMode = "FlatFace"
    _add_rectangle(s1, -20, -10, 20, 10)
    pad1 = body.newObject("PartDesign::Pad", "Pad")
    pad1.Profile = s1
    pad1.Length = 4
    doc.recompute()

    s2 = body.newObject("Sketcher::SketchObject", "Tab")
    s2.AttachmentSupport = (body.Origin.OutList[3], [""])
    s2.MapMode = "FlatFace"
    _add_rectangle(s2, 12, -3, 18, 3)
    pad2 = body.newObject("PartDesign::Pad", "Tab")
    pad2.Profile = s2
    pad2.Length = 8
    doc.recompute()

    mr = body.newObject("PartDesign::Mirrored", "Mirrored")
    mr.Originals = [pad2]
    mr.MirrorPlane = (body.Origin.OutList[5], ["YZ_Plane"])
    body.Tip = mr
    _save(doc, out / "tier4_patterns/mirrored_tab.FCStd")


# --- Tier 6: Spreadsheet-driven primitive ---

def make_spreadsheet_box(out: Path) -> None:
    doc = FreeCAD.newDocument("sprd")
    sheet = doc.addObject("Spreadsheet::Sheet", "Params")
    sheet.set("A1", "width")
    sheet.set("B1", "25")
    sheet.setAlias("B1", "width")
    sheet.set("A2", "depth")
    sheet.set("B2", "15")
    sheet.setAlias("B2", "depth")
    sheet.set("A3", "height")
    sheet.set("B3", "10")
    sheet.setAlias("B3", "height")
    doc.recompute()

    box = doc.addObject("Part::Box", "Box")
    box.setExpression("Length", "<<Params>>.width")
    box.setExpression("Width", "<<Params>>.depth")
    box.setExpression("Height", "<<Params>>.height")
    _save(doc, out / "tier6_parametric/spreadsheet_box.FCStd")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures"))
    args = parser.parse_args()

    print(f"Synthesizing fixtures under {args.out}")
    make_cylinder(args.out)
    make_sphere(args.out)
    make_cone(args.out)
    make_torus(args.out)
    make_simple_pad(args.out)
    make_pad_twolengths(args.out)
    make_pad_with_bspline(args.out)
    make_box_with_fillet(args.out)
    make_linear_pattern_holes(args.out)
    make_polar_pattern_holes(args.out)
    make_mirrored_tab(args.out)
    make_spreadsheet_box(args.out)
    print("Done.")


if __name__ == "__main__":
    main()
