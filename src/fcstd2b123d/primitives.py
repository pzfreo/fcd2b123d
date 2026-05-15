"""Tier-1 primitive translators (Part workbench primitives).

Each function takes a FreeCAD object and returns a TranslationUnit. The
emitter's job is to compose units into a build123d source module.

Offset conventions: FreeCAD's Part-workbench primitives place a corner or
base at the origin in their native frame, while build123d's primitives
centre their geometry. Each translator computes the offset that makes the
emitted build123d geometry match FreeCAD's placement.
"""

from __future__ import annotations

from .emitter import TranslationUnit


# Lazy import wrappers: FreeCAD/Part objects passed in are duck-typed (we
# only access named attributes), so we don't actually need to import the
# FreeCAD or Part modules here. That keeps this file importable in any env.


# build123d names we import into the translated module. A FreeCAD object's
# Name will sometimes match one of these (e.g. an unrenamed Part::Cone is
# called "Cone"). Suffixing avoids `Cone = Pos(...) * Cone(...)` shadowing
# the class.
_BUILD123D_RESERVED = frozenset({
    "Box", "Cylinder", "Sphere", "Cone", "Torus",
    "Pos", "Rot", "Loc", "Plane", "Axis",
})


def _safe_var(name: str) -> str:
    return name + "_" if name in _BUILD123D_RESERVED else name


def translate_box(obj) -> TranslationUnit:
    """Part::Box → Pos(L/2, W/2, H/2) * Box(L, W, H).

    FreeCAD's Part::Box has its corner at the origin in its native frame;
    build123d's Box is centred. The offset aligns them.
    """
    L = float(obj.Length.Value)
    W = float(obj.Width.Value)
    H = float(obj.Height.Value)
    var = _safe_var(obj.Name)
    return TranslationUnit(
        var_name=var,
        imports={"Box", "Pos"},
        lines=[f"{var} = Pos({L/2}, {W/2}, {H/2}) * Box({L}, {W}, {H})"],
        comment=f"Part::Box {obj.Label!r}: Length={L}, Width={W}, Height={H}",
    )


def translate_cylinder(obj) -> TranslationUnit:
    """Part::Cylinder → Pos(0, 0, H/2) * Cylinder(R, H).

    FreeCAD's Part::Cylinder has its base at z=0; build123d's Cylinder is
    centred at the origin. Both have +Z as the axis by default.

    Part::Cylinder's Angle (sweep) property is not handled in v1 — most
    cylinders are full 360°. A partial cylinder would need a different
    build123d construction (a revolved sketch).
    """
    R = float(obj.Radius.Value)
    H = float(obj.Height.Value)
    var = _safe_var(obj.Name)
    return TranslationUnit(
        var_name=var,
        imports={"Cylinder", "Pos"},
        lines=[f"{var} = Pos(0, 0, {H/2}) * Cylinder({R}, {H})"],
        comment=f"Part::Cylinder {obj.Label!r}: Radius={R}, Height={H}",
    )


def translate_sphere(obj) -> TranslationUnit:
    """Part::Sphere → Sphere(R). Both libraries centre at the origin."""
    R = float(obj.Radius.Value)
    var = _safe_var(obj.Name)
    return TranslationUnit(
        var_name=var,
        imports={"Sphere"},
        lines=[f"{var} = Sphere({R})"],
        comment=f"Part::Sphere {obj.Label!r}: Radius={R}",
    )


def translate_cone(obj) -> TranslationUnit:
    """Part::Cone → Pos(0, 0, H/2) * Cone(R1, R2, H).

    Like Cylinder, FreeCAD places the base at z=0 while build123d centres
    the geometry along its axis.
    """
    R1 = float(obj.Radius1.Value)
    R2 = float(obj.Radius2.Value)
    H = float(obj.Height.Value)
    var = _safe_var(obj.Name)
    return TranslationUnit(
        var_name=var,
        imports={"Cone", "Pos"},
        lines=[f"{var} = Pos(0, 0, {H/2}) * Cone({R1}, {R2}, {H})"],
        comment=f"Part::Cone {obj.Label!r}: Radius1={R1}, Radius2={R2}, Height={H}",
    )


def translate_torus(obj) -> TranslationUnit:
    """Part::Torus → Torus(R_major, R_minor). Both libraries centre at origin."""
    Rmaj = float(obj.Radius1.Value)
    Rmin = float(obj.Radius2.Value)
    var = _safe_var(obj.Name)
    return TranslationUnit(
        var_name=var,
        imports={"Torus"},
        lines=[f"{var} = Torus({Rmaj}, {Rmin})"],
        comment=f"Part::Torus {obj.Label!r}: Radius1={Rmaj}, Radius2={Rmin}",
    )


# Dispatch table — TypeId -> handler.
TIER1_HANDLERS = {
    "Part::Box": translate_box,
    "Part::Cylinder": translate_cylinder,
    "Part::Sphere": translate_sphere,
    "Part::Cone": translate_cone,
    "Part::Torus": translate_torus,
}
