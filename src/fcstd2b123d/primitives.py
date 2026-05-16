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
# Tolerance for treating an Euler component as zero when deciding whether to
# emit a single-axis Rot(X=…) / Rot(Y=…) / Rot(Z=…) (clean) vs. the
# general Rotation(..., ordering=Intrinsic.ZYX) form.
_EULER_ZERO_TOL = 1e-9


def _placement(obj) -> tuple[tuple[float, float, float], tuple[float, float, float] | None]:
    """Return ``(base, euler_xyz_or_none)`` for this object's Placement.

    Euler form follows FreeCAD's ``Rotation.toEuler()`` convention
    (yaw=Z, pitch=Y, roll=X, in degrees, intrinsic Z-Y-X). When the
    rotation is identity within ``_ROTATION_TOL``, returns ``None`` so
    the caller can emit the simpler no-rotation form.
    """
    p = obj.Placement
    base = (p.Base.x, p.Base.y, p.Base.z)
    if abs(p.Rotation.Angle) <= _ROTATION_TOL:
        return base, None
    yaw, pitch, roll = p.Rotation.toEuler()
    return base, (roll, pitch, yaw)


def _rot_expr(euler_xyz: tuple[float, float, float]) -> tuple[str, set[str]]:
    """Render FreeCAD's (roll, pitch, yaw) as a build123d rotation expression.

    FreeCAD's ``Rotation.toEuler()`` returns intrinsic Z-Y'-X'' angles
    (yaw=Z, pitch=Y, roll=X), giving a combined rotation matrix
    R = Rz(yaw) · Ry(pitch) · Rx(roll). The equivalent build123d emit is
    ``Rot(Z=yaw) * Rot(Y=pitch) * Rot(X=roll) * shape`` — composition is
    right-to-left so Rx is applied first, then Ry, then Rz, matching the
    intrinsic convention.

    Components within ``_EULER_ZERO_TOL`` are elided; if only one axis is
    non-zero, a single ``Rot(axis=…)`` is emitted.
    """
    roll, pitch, yaw = euler_xyz
    parts = []
    if abs(yaw) > _EULER_ZERO_TOL:
        parts.append(("Z", yaw))
    if abs(pitch) > _EULER_ZERO_TOL:
        parts.append(("Y", pitch))
    if abs(roll) > _EULER_ZERO_TOL:
        parts.append(("X", roll))
    if not parts:
        # All components below tolerance; treat as identity (caller should
        # already have skipped this path, but be defensive).
        return "", set()
    return (
        " * ".join(f"Rot({axis}={format_value(v)})" for axis, v in parts),
        {"Rot"},
    )


def _pos_expr(x: float, y: float, z: float) -> str | None:
    if x == 0 and y == 0 and z == 0:
        return None
    return f"Pos({vfmt(x, y, z)})"


def _wrap(shape_expr: str, pos: str | None) -> tuple[str, set[str]]:
    if pos is None:
        return shape_expr, set()
    return f"{pos} * {shape_expr}", {"Pos"}


def _wrap_with_rotation(
    shape_expr: str,
    centering: tuple[float, float, float],
    base: tuple[float, float, float],
    euler: tuple[float, float, float],
) -> tuple[str, set[str]]:
    """Compose ``Pos(base) * Rot(...) * Pos(centering) * shape``.

    Used when the FreeCAD primitive's Placement has a non-identity
    rotation. FreeCAD applies rotation about the primitive's *native*
    local origin (the corner for Box, the base centre for Cylinder/Cone,
    the centre for Sphere/Torus), so ``Pos(centering)`` shifts the
    build123d-centered shape so that origin coincides with FreeCAD's
    local origin *before* rotation.
    """
    rot_str, rot_imports = _rot_expr(euler)
    imports = rot_imports
    parts: list[str] = []
    bx, by, bz = base
    if bx != 0 or by != 0 or bz != 0:
        parts.append(f"Pos({vfmt(bx, by, bz)})")
        imports.add("Pos")
    parts.append(rot_str)
    cx, cy, cz = centering
    if cx != 0 or cy != 0 or cz != 0:
        parts.append(f"Pos({vfmt(cx, cy, cz)})")
        imports.add("Pos")
    parts.append(shape_expr)
    return " * ".join(parts), imports


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
    base, euler = _placement(obj)
    px, py, pz = base

    box_args = f"{format_value(L)}, {format_value(W)}, {format_value(H)}"
    box_expr = f"Box({box_args})"

    if euler is not None:
        if _is_any_string(L, W, H):
            raise UnsupportedFeatureError(
                obj.TypeId,
                f"{obj.Label} (parametric dimensions + non-identity rotation "
                "— not yet supported)",
            )
        full, rot_imports = _wrap_with_rotation(
            box_expr,
            centering=(L / 2, W / 2, H / 2),
            base=base,
            euler=euler,
        )
        imports = {"Box"} | rot_imports
    elif _is_any_string(L, W, H):
        # Parametric: build the offset expression as a string so the
        # dimensions appear as variable references.
        pos = (
            f"Pos({add_expr(px, half_expr(L))}, "
            f"{add_expr(py, half_expr(W))}, "
            f"{add_expr(pz, half_expr(H))})"
        )
        full = f"{pos} * {box_expr}"
        imports = {"Box", "Pos"}
    else:
        expr, extra_imports = _wrap(
            box_expr,
            _pos_expr(px + L / 2, py + W / 2, pz + H / 2),
        )
        full = expr
        imports = {"Box"} | extra_imports

    var = _safe_var(obj.Name)
    unit = TranslationUnit(
        var_name=var,
        label=obj.Label,
        imports=imports,
        lines=[f"{var} = {full}"],
        comment=f"Part::Box {obj.Label!r}: Length={L}, Width={W}, Height={H}",
    )
    _record(ctx, obj, "box", unit)
    return [unit]


def translate_cylinder(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    R = float(obj.Radius.Value)
    H = float(obj.Height.Value)
    base, euler = _placement(obj)
    px, py, pz = base
    shape_expr = f"Cylinder({vfmt(R, H)})"
    if euler is not None:
        full, extra_imports = _wrap_with_rotation(
            shape_expr, centering=(0, 0, H / 2), base=base, euler=euler,
        )
    else:
        full, extra_imports = _wrap(shape_expr, _pos_expr(px, py, pz + H / 2))
    var = _safe_var(obj.Name)
    unit = TranslationUnit(
        var_name=var,
        label=obj.Label,
        imports={"Cylinder"} | extra_imports,
        lines=[f"{var} = {full}"],
        comment=f"Part::Cylinder {obj.Label!r}: Radius={R}, Height={H}",
    )
    _record(ctx, obj, "cylinder", unit)
    return [unit]


def translate_sphere(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    R = float(obj.Radius.Value)
    base, euler = _placement(obj)
    px, py, pz = base
    shape_expr = f"Sphere({format_value(R)})"
    # Sphere is rotation-invariant, but FreeCAD may still set a rotation
    # in the Placement; we honour it by emitting the rotation prefix even
    # though it has no geometric effect. Cheaper than special-casing.
    if euler is not None:
        full, extra_imports = _wrap_with_rotation(
            shape_expr, centering=(0, 0, 0), base=base, euler=euler,
        )
    else:
        full, extra_imports = _wrap(shape_expr, _pos_expr(px, py, pz))
    var = _safe_var(obj.Name)
    unit = TranslationUnit(
        var_name=var,
        label=obj.Label,
        imports={"Sphere"} | extra_imports,
        lines=[f"{var} = {full}"],
        comment=f"Part::Sphere {obj.Label!r}: Radius={R}",
    )
    _record(ctx, obj, "sphere", unit)
    return [unit]


def translate_cone(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    R1 = float(obj.Radius1.Value)
    R2 = float(obj.Radius2.Value)
    H = float(obj.Height.Value)
    base, euler = _placement(obj)
    px, py, pz = base
    shape_expr = f"Cone({vfmt(R1, R2, H)})"
    if euler is not None:
        full, extra_imports = _wrap_with_rotation(
            shape_expr, centering=(0, 0, H / 2), base=base, euler=euler,
        )
    else:
        full, extra_imports = _wrap(shape_expr, _pos_expr(px, py, pz + H / 2))
    var = _safe_var(obj.Name)
    unit = TranslationUnit(
        var_name=var,
        label=obj.Label,
        imports={"Cone"} | extra_imports,
        lines=[f"{var} = {full}"],
        comment=f"Part::Cone {obj.Label!r}: Radius1={R1}, Radius2={R2}, Height={H}",
    )
    _record(ctx, obj, "cone", unit)
    return [unit]


def translate_torus(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    Rmaj = float(obj.Radius1.Value)
    Rmin = float(obj.Radius2.Value)
    base, euler = _placement(obj)
    px, py, pz = base
    shape_expr = f"Torus({vfmt(Rmaj, Rmin)})"
    if euler is not None:
        full, extra_imports = _wrap_with_rotation(
            shape_expr, centering=(0, 0, 0), base=base, euler=euler,
        )
    else:
        full, extra_imports = _wrap(shape_expr, _pos_expr(px, py, pz))
    var = _safe_var(obj.Name)
    unit = TranslationUnit(
        var_name=var,
        label=obj.Label,
        imports={"Torus"} | extra_imports,
        lines=[f"{var} = {full}"],
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
