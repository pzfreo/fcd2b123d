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
from .errors import UnsupportedFeatureError


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


# Tolerance for treating a Placement rotation as identity. FreeCAD stores
# Rotation as a quaternion; .Angle returns the rotation magnitude in radians.
_ROTATION_TOL = 1e-9


def _placement_offset(obj) -> tuple[float, float, float]:
    """Return (x, y, z) translation from obj.Placement.

    Raises UnsupportedFeatureError if Placement.Rotation is non-identity.
    Tier-1 v1 handles translation only — rotation will land when a fixture
    forces the work (build123d's `Loc` + axis-angle conversion from the
    FreeCAD quaternion is straightforward but needs its own test).
    """
    p = obj.Placement
    if abs(p.Rotation.Angle) > _ROTATION_TOL:
        raise UnsupportedFeatureError(
            obj.TypeId,
            f"{obj.Label} (Placement has non-identity rotation; "
            f"angle={p.Rotation.Angle:.6f} rad — not yet handled in v1)",
        )
    return (p.Base.x, p.Base.y, p.Base.z)


def _pos_expr(x: float, y: float, z: float) -> str | None:
    """Return ``Pos(x, y, z)`` or None when the offset is zero."""
    if x == 0 and y == 0 and z == 0:
        return None
    return f"Pos({x}, {y}, {z})"


def _wrap(shape_expr: str, pos: str | None) -> tuple[str, set[str]]:
    """Compose ``Pos(...) * shape_expr`` and return the expression + needed imports."""
    if pos is None:
        return shape_expr, set()
    return f"{pos} * {shape_expr}", {"Pos"}


def translate_box(obj) -> TranslationUnit:
    """Part::Box → Pos(x+L/2, y+W/2, z+H/2) * Box(L, W, H).

    FreeCAD's Part::Box has its corner at the origin in its native frame;
    build123d's Box is centred. The offset accounts for that *and* the
    Placement.Base translation.
    """
    L = float(obj.Length.Value)
    W = float(obj.Width.Value)
    H = float(obj.Height.Value)
    px, py, pz = _placement_offset(obj)
    expr, extra_imports = _wrap(
        f"Box({L}, {W}, {H})",
        _pos_expr(px + L / 2, py + W / 2, pz + H / 2),
    )
    var = _safe_var(obj.Name)
    return TranslationUnit(
        var_name=var,
        imports={"Box"} | extra_imports,
        lines=[f"{var} = {expr}"],
        comment=f"Part::Box {obj.Label!r}: Length={L}, Width={W}, Height={H}",
    )


def translate_cylinder(obj) -> TranslationUnit:
    """Part::Cylinder → Pos(x, y, z+H/2) * Cylinder(R, H).

    FreeCAD's Part::Cylinder has its base at z=0; build123d's Cylinder is
    centred at the origin. Both have +Z as the axis by default. Placement.Base
    composes additively with the inherent z+H/2 offset.

    The ``Angle`` sweep property (partial cylinder) is not handled in v1.
    """
    R = float(obj.Radius.Value)
    H = float(obj.Height.Value)
    px, py, pz = _placement_offset(obj)
    expr, extra_imports = _wrap(
        f"Cylinder({R}, {H})",
        _pos_expr(px, py, pz + H / 2),
    )
    var = _safe_var(obj.Name)
    return TranslationUnit(
        var_name=var,
        imports={"Cylinder"} | extra_imports,
        lines=[f"{var} = {expr}"],
        comment=f"Part::Cylinder {obj.Label!r}: Radius={R}, Height={H}",
    )


def translate_sphere(obj) -> TranslationUnit:
    """Part::Sphere → Sphere(R). Both libraries centre at the origin."""
    R = float(obj.Radius.Value)
    px, py, pz = _placement_offset(obj)
    expr, extra_imports = _wrap(f"Sphere({R})", _pos_expr(px, py, pz))
    var = _safe_var(obj.Name)
    return TranslationUnit(
        var_name=var,
        imports={"Sphere"} | extra_imports,
        lines=[f"{var} = {expr}"],
        comment=f"Part::Sphere {obj.Label!r}: Radius={R}",
    )


def translate_cone(obj) -> TranslationUnit:
    """Part::Cone → Pos(x, y, z+H/2) * Cone(R1, R2, H).

    Like Cylinder, FreeCAD places the base at z=0 while build123d centres
    the geometry along its axis.
    """
    R1 = float(obj.Radius1.Value)
    R2 = float(obj.Radius2.Value)
    H = float(obj.Height.Value)
    px, py, pz = _placement_offset(obj)
    expr, extra_imports = _wrap(
        f"Cone({R1}, {R2}, {H})",
        _pos_expr(px, py, pz + H / 2),
    )
    var = _safe_var(obj.Name)
    return TranslationUnit(
        var_name=var,
        imports={"Cone"} | extra_imports,
        lines=[f"{var} = {expr}"],
        comment=f"Part::Cone {obj.Label!r}: Radius1={R1}, Radius2={R2}, Height={H}",
    )


def translate_torus(obj) -> TranslationUnit:
    """Part::Torus → Torus(R_major, R_minor). Both libraries centre at origin."""
    Rmaj = float(obj.Radius1.Value)
    Rmin = float(obj.Radius2.Value)
    px, py, pz = _placement_offset(obj)
    expr, extra_imports = _wrap(f"Torus({Rmaj}, {Rmin})", _pos_expr(px, py, pz))
    var = _safe_var(obj.Name)
    return TranslationUnit(
        var_name=var,
        imports={"Torus"} | extra_imports,
        lines=[f"{var} = {expr}"],
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
