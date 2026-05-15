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
from .emitter import TranslationUnit, add_expr, format_value, half_expr, vfmt
from .errors import UnsupportedFeatureError
from .freecad_properties import extract_properties
from .parametric import resolve_property


def _value(obj, prop_name: str, ctx: TranslationContext):
    """Parametric-aware property reader (same shape as partdesign._value)."""
    if ctx.parameters is not None:
        expr = resolve_property(obj, prop_name, ctx.parameters)
        if expr is not None:
            return expr
    raw = getattr(obj, prop_name)
    return float(raw.Value) if hasattr(raw, "Value") else float(raw)


def _is_any_string(*values) -> bool:
    return any(isinstance(v, str) for v in values)


# build123d names we import into the translated module. A FreeCAD object's
# Name will sometimes match one of these (e.g. an unrenamed Part::Cone is
# (Previously suffixed names like ``Cone_`` to avoid shadowing the build123d
# class. The snake_case post-pass in emitter.py now handles this -- the
# variable becomes ``cone`` (lowercase) which coexists with ``Cone`` (the
# class) since Python is case-sensitive. So _safe_var is just a passthrough.)


def _safe_var(name: str) -> str:
    return name


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
    L = _value(obj, "Length", ctx)
    W = _value(obj, "Width", ctx)
    H = _value(obj, "Height", ctx)
    px, py, pz = _placement_offset(obj)

    box_args = f"{format_value(L)}, {format_value(W)}, {format_value(H)}"
    if _is_any_string(L, W, H):
        # Parametric: build the offset expression as a string so the
        # dimensions appear as variable references.
        pos = (
            f"Pos({add_expr(px, half_expr(L))}, "
            f"{add_expr(py, half_expr(W))}, "
            f"{add_expr(pz, half_expr(H))})"
        )
        full = f"{pos} * Box({box_args})"
        imports = {"Box", "Pos"}
    else:
        expr, extra_imports = _wrap(
            f"Box({box_args})",
            _pos_expr(px + L / 2, py + W / 2, pz + H / 2),
        )
        full = expr
        imports = {"Box"} | extra_imports

    var = _safe_var(obj.Name)
    unit = TranslationUnit(
        var_name=var,
        imports=imports,
        lines=[f"{var} = {full}"],
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
