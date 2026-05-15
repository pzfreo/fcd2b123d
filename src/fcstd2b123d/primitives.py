"""Tier-1 primitive translators (Part workbench primitives).

Each function takes a FreeCAD object plus a TranslationContext and:
1. Returns one or more TranslationUnits (consumed by the emitter for the .py
   output).
2. Records a step in the context (consumed for the structured JSON sidecar,
   SPEC §14).

Offset conventions: FreeCAD's Part-workbench primitives place a corner or
base at the origin in their native frame, while build123d's primitives
centre their geometry. Each translator computes the offset that makes the
emitted build123d geometry match FreeCAD's placement.
"""

from __future__ import annotations

from .context import TranslationContext
from .emitter import TranslationUnit, format_value, vfmt
from .errors import UnsupportedFeatureError
from .freecad_properties import extract_properties


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
    p = obj.Placement
    if abs(p.Rotation.Angle) > _ROTATION_TOL:
        raise UnsupportedFeatureError(
            obj.TypeId,
            f"{obj.Label} (Placement has non-identity rotation; "
            f"angle={p.Rotation.Angle:.6f} rad — not yet handled in v1)",
        )
    return (p.Base.x, p.Base.y, p.Base.z)


def _pos_expr(x: float, y: float, z: float) -> str | None:
    if x == 0 and y == 0 and z == 0:
        return None
    return f"Pos({vfmt(x, y, z)})"


def _wrap(shape_expr: str, pos: str | None) -> tuple[str, set[str]]:
    if pos is None:
        return shape_expr, set()
    return f"{pos} * {shape_expr}", {"Pos"}


def _record(
    ctx: TranslationContext,
    obj,
    feature_type: str,
    unit: TranslationUnit,
) -> None:
    """Append a step record reflecting this primitive translation."""
    ctx.add_step(
        feature_type=feature_type,
        feature_name=obj.Name,
        renamed_from_default=(obj.Label != obj.Name),
        build123d_code=unit.lines[0] if unit.lines else "",
        properties=extract_properties(getattr(obj, "Shape", None)),
    )


def translate_box(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    L = float(obj.Length.Value)
    W = float(obj.Width.Value)
    H = float(obj.Height.Value)
    px, py, pz = _placement_offset(obj)
    expr, extra_imports = _wrap(
        f"Box({vfmt(L, W, H)})",
        _pos_expr(px + L / 2, py + W / 2, pz + H / 2),
    )
    var = _safe_var(obj.Name)
    unit = TranslationUnit(
        var_name=var,
        imports={"Box"} | extra_imports,
        lines=[f"{var} = {expr}"],
        comment=f"Part::Box {obj.Label!r}: Length={L}, Width={W}, Height={H}",
    )
    _record(ctx, obj, "box", unit)
    return [unit]


def translate_cylinder(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    R = float(obj.Radius.Value)
    H = float(obj.Height.Value)
    px, py, pz = _placement_offset(obj)
    expr, extra_imports = _wrap(
        f"Cylinder({vfmt(R, H)})",
        _pos_expr(px, py, pz + H / 2),
    )
    var = _safe_var(obj.Name)
    unit = TranslationUnit(
        var_name=var,
        imports={"Cylinder"} | extra_imports,
        lines=[f"{var} = {expr}"],
        comment=f"Part::Cylinder {obj.Label!r}: Radius={R}, Height={H}",
    )
    _record(ctx, obj, "cylinder", unit)
    return [unit]


def translate_sphere(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    R = float(obj.Radius.Value)
    px, py, pz = _placement_offset(obj)
    expr, extra_imports = _wrap(f"Sphere({format_value(R)})", _pos_expr(px, py, pz))
    var = _safe_var(obj.Name)
    unit = TranslationUnit(
        var_name=var,
        imports={"Sphere"} | extra_imports,
        lines=[f"{var} = {expr}"],
        comment=f"Part::Sphere {obj.Label!r}: Radius={R}",
    )
    _record(ctx, obj, "sphere", unit)
    return [unit]


def translate_cone(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    R1 = float(obj.Radius1.Value)
    R2 = float(obj.Radius2.Value)
    H = float(obj.Height.Value)
    px, py, pz = _placement_offset(obj)
    expr, extra_imports = _wrap(
        f"Cone({vfmt(R1, R2, H)})",
        _pos_expr(px, py, pz + H / 2),
    )
    var = _safe_var(obj.Name)
    unit = TranslationUnit(
        var_name=var,
        imports={"Cone"} | extra_imports,
        lines=[f"{var} = {expr}"],
        comment=f"Part::Cone {obj.Label!r}: Radius1={R1}, Radius2={R2}, Height={H}",
    )
    _record(ctx, obj, "cone", unit)
    return [unit]


def translate_torus(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    Rmaj = float(obj.Radius1.Value)
    Rmin = float(obj.Radius2.Value)
    px, py, pz = _placement_offset(obj)
    expr, extra_imports = _wrap(f"Torus({vfmt(Rmaj, Rmin)})", _pos_expr(px, py, pz))
    var = _safe_var(obj.Name)
    unit = TranslationUnit(
        var_name=var,
        imports={"Torus"} | extra_imports,
        lines=[f"{var} = {expr}"],
        comment=f"Part::Torus {obj.Label!r}: Radius1={Rmaj}, Radius2={Rmin}",
    )
    _record(ctx, obj, "torus", unit)
    return [unit]


TIER1_HANDLERS = {
    "Part::Box": translate_box,
    "Part::Cylinder": translate_cylinder,
    "Part::Sphere": translate_sphere,
    "Part::Cone": translate_cone,
    "Part::Torus": translate_torus,
}
