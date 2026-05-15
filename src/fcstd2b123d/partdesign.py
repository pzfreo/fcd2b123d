"""PartDesign feature translators.

Tier-2 scope:
  - PartDesign::Body — walks Group, chains features into a running shape.
  - PartDesign::Pad — extrude (with Reversed option).
  - PartDesign::Pocket — subtractive extrude (Reversed, ThroughAll).
  - PartDesign::Revolution — revolve around X/Y/Z or sketch-local axes
    (with Reversed). Handled both inside a Body and as a top-level atomic
    feature (some legacy library files have a free-standing Revolution at
    document level).
  - PartDesign::Groove — subtractive revolution. Structurally a Revolution
    but subtracts from the running body. The single biggest unimplemented
    feature in the FreeCAD Parts Library (15.3% of in-scope files).

Tier-3 scope:
  - PartDesign::Fillet — round the named edges of a parent feature.
  - PartDesign::Chamfer — bevel the named edges of a parent feature.

Tier-4 scope:
  - PartDesign::LinearPattern — N copies along an axis/direction.
  - PartDesign::PolarPattern — N copies around a rotation axis.
  - PartDesign::Mirrored — one mirror copy across a reference plane.

Patterns work by replicating the Original feature's "delta" (the added or
subtracted prism) at each transformed location and chaining them onto the
running body. v1 supports a single Original that is a Pad or Pocket with
``Type='Length'``; the Original may have Reversed and Midplane set.

Edge selection for fillet/chamfer: FreeCAD references edges by index
('Edge8'), which only makes sense in FreeCAD's BRep evaluation. To select
the same edges in build123d we read each referenced edge's geometric
midpoint from FreeCAD's evaluated shape, then emit a build123d expression
that picks edges whose midpoints match. This is the central mechanic
validating ADR-0001's "use FreeCAD-runtime" decision.

Not in scope: TwoLengths / Midplane Pad/Pocket modes, UpToFace, revolutions
around arbitrary axes, Hole, Groove, sweep / loft / helix features.
"""

from __future__ import annotations

import math

from .context import TranslationContext
from .emitter import TranslationUnit, format_value, vfmt
from .errors import UnsupportedFeatureError
from .freecad_properties import extract_properties
from .parametric import resolve_property
from .sketch import translate_sketch


def _value(obj, prop_name: str, ctx: TranslationContext, value_or_obj=None):
    """Resolve a property to either a parametric expression (str) or a literal (float).

    When ctx.parameters is set and obj has an ExpressionEngine binding for
    ``prop_name`` that maps to a known parameter, return the rewritten
    expression string. Otherwise fall back to the literal value.
    """
    if ctx.parameters is not None:
        expr = resolve_property(obj, prop_name, ctx.parameters)
        if expr is not None:
            return expr
    if value_or_obj is None:
        value_or_obj = getattr(obj, prop_name)
    if hasattr(value_or_obj, "Value"):
        return float(value_or_obj.Value)
    return float(value_or_obj)


def _negate(v):
    """Negate a value or expression. Handles both literals and strings."""
    if isinstance(v, str):
        return f"-({v})"
    return -v

_TOL = 1e-9

_BODY_INFRASTRUCTURE = {
    "App::Origin", "App::Line", "App::Plane", "App::Part",
    "PartDesign::CoordinateSystem", "PartDesign::Plane",
    "PartDesign::Line", "PartDesign::Point",
}

# Body's Origin axis names → build123d Axis constants. Used by Revolution.
_AXIS_OF_ORIGIN = {"X_Axis": "Axis.X", "Y_Axis": "Axis.Y", "Z_Axis": "Axis.Z"}


def _body_placement_is_identity(body) -> bool:
    p = body.Placement
    return (
        abs(p.Rotation.Angle) < _TOL
        and abs(p.Base.x) < _TOL
        and abs(p.Base.y) < _TOL
        and abs(p.Base.z) < _TOL
    )


def _body_placement_emit(body, current_var: str) -> tuple[str, set[str], str] | None:
    """Build a ``Pos(base) * Rot(...) * <current_var>`` expression for a
    Body whose Placement is non-identity.

    Returns ``(expr, imports, comment)`` or ``None`` for identity.
    Composition is right-to-left: the inner ``Rot(X=…)`` is applied to
    the body first (about Body-local origin), then the outer ``Pos`` (or
    ``Rot(Z=…) * Rot(Y=…) * Rot(X=…)`` for multi-axis) places the body
    in the world frame — matching FreeCAD's Placement semantics
    (rotation about local origin, then translation).
    """
    p = body.Placement
    base = (p.Base.x, p.Base.y, p.Base.z)
    rot_nonidentity = abs(p.Rotation.Angle) > _TOL
    base_nonidentity = any(abs(c) > _TOL for c in base)
    if not rot_nonidentity and not base_nonidentity:
        return None

    parts: list[str] = []
    imports: set[str] = set()
    if base_nonidentity:
        parts.append(f"Pos({format_value(base[0])}, {format_value(base[1])}, {format_value(base[2])})")
        imports.add("Pos")
    if rot_nonidentity:
        yaw, pitch, roll = p.Rotation.toEuler()
        # FreeCAD's toEuler is intrinsic Z-Y'-X'' (Tait-Bryan); compose
        # Rot(Z) * Rot(Y) * Rot(X) so build123d's right-to-left
        # composition applies Rx first, then Ry, then Rz — matching.
        rot_parts: list[tuple[str, float]] = []
        if abs(yaw) > _TOL:
            rot_parts.append(("Z", yaw))
        if abs(pitch) > _TOL:
            rot_parts.append(("Y", pitch))
        if abs(roll) > _TOL:
            rot_parts.append(("X", roll))
        if rot_parts:
            parts.append(
                " * ".join(f"Rot({axis}={format_value(v)})" for axis, v in rot_parts)
            )
            imports.add("Rot")
    parts.append(current_var)
    expr = " * ".join(parts)
    return expr, imports, f"Body Placement: base={base}, rotation angle={math.degrees(p.Rotation.Angle):.3f}°"


def translate_body(body, ctx: TranslationContext) -> list[TranslationUnit]:
    """Walk a PartDesign::Body and emit units in feature order.

    Subtractive / dressup features consume the running body shape; we track
    the last "result" variable name as we go.
    """
    units: list[TranslationUnit] = []
    current_var: str | None = None
    for child in body.Group:
        tid = child.TypeId
        if tid in _BODY_INFRASTRUCTURE:
            continue
        if tid == "Sketcher::SketchObject":
            units.extend(translate_sketch(child, ctx))
        elif tid == "PartDesign::Pad":
            # Inside a Body, consecutive Pads chain additively — each adds
            # material to the running body shape.
            unit = _translate_pad(child, ctx, base_var=current_var)
            units.append(unit)
            current_var = unit.var_name
        elif tid == "PartDesign::Pocket":
            if current_var is None:
                raise UnsupportedFeatureError(
                    tid,
                    f"{child.Label} (Pocket with no preceding solid in body)",
                )
            unit = _translate_pocket(child, current_var, ctx)
            units.append(unit)
            current_var = unit.var_name
        elif tid == "PartDesign::Revolution":
            unit = _translate_revolution(child, current_var, ctx)
            units.append(unit)
            current_var = unit.var_name
        elif tid == "PartDesign::Groove":
            if current_var is None:
                raise UnsupportedFeatureError(
                    tid,
                    f"{child.Label} (Groove with no preceding solid in body)",
                )
            unit = _translate_groove(child, current_var, ctx)
            units.append(unit)
            current_var = unit.var_name
        elif tid == "PartDesign::Fillet":
            if current_var is None:
                raise UnsupportedFeatureError(
                    tid,
                    f"{child.Label} (Fillet with no preceding solid in body)",
                )
            units.extend(_translate_fillet(child, current_var, ctx))
            current_var = child.Name
        elif tid == "PartDesign::Chamfer":
            if current_var is None:
                raise UnsupportedFeatureError(
                    tid,
                    f"{child.Label} (Chamfer with no preceding solid in body)",
                )
            units.extend(_translate_chamfer(child, current_var, ctx))
            current_var = child.Name
        elif tid in (
            "PartDesign::LinearPattern",
            "PartDesign::PolarPattern",
            "PartDesign::Mirrored",
        ):
            if current_var is None:
                raise UnsupportedFeatureError(
                    tid,
                    f"{child.Label} (Pattern feature with no preceding solid in body)",
                )
            unit = _translate_pattern(child, current_var, ctx)
            units.append(unit)
            current_var = unit.var_name
        else:
            raise UnsupportedFeatureError(
                tid,
                f"{child.Label} (feature kind not supported in tier 2/3; "
                f"only Sketch / Pad / Pocket / Revolution / Groove / Fillet / Chamfer)",
            )

    # Wrap the body's final shape with its world Placement, when non-identity.
    # FreeCAD's Body.Placement is applied to the body's locally-built shape;
    # build123d's right-to-left composition mirrors this when we emit
    # ``Pos(base) * Rot(...) * <body_var>``.
    if current_var is not None:
        wrap = _body_placement_emit(body, current_var)
        if wrap is not None:
            expr, extra_imports, comment = wrap
            placed_var = f"{body.Name}_placed"
            units.append(
                TranslationUnit(
                    var_name=placed_var,
                    imports=extra_imports,
                    lines=[f"{placed_var} = {expr}"],
                    comment=comment,
                )
            )
    return units


# ---------------------------------------------------------------------------
# Pad
# ---------------------------------------------------------------------------


def _translate_pad(
    pad, ctx: TranslationContext, base_var: str | None = None
) -> TranslationUnit:
    """Emit a Pad. When ``base_var`` is set, the Pad's extrusion is unioned
    with the running body shape — that's how FreeCAD chains multiple Pads
    inside a Body (each adds material to the previous result).
    """
    pad_type = str(getattr(pad, "Type", "Length"))
    if pad_type not in ("Length", "TwoLengths"):
        raise UnsupportedFeatureError(
            pad.TypeId,
            f"{pad.Label} (Pad.Type={pad.Type!r}; supports 'Length' / 'TwoLengths')",
        )

    profile = pad.Profile
    if isinstance(profile, (list, tuple)):
        profile = profile[0]
    sketch_var = profile.Name

    length = _value(pad, "Length", ctx)
    reversed_ = bool(getattr(pad, "Reversed", False))
    midplane = bool(getattr(pad, "Midplane", False))

    helpers: set[str] = set()
    imports = {"extrude"}

    if pad_type == "TwoLengths":
        # Forward by Length, backward by Length2 from the sketch plane.
        # Reversed flips both directions. Build two extrudes and fuse via
        # the BuildPart-backed union helper (same pattern as pattern emit).
        length2 = _value(pad, "Length2", ctx)
        if reversed_:
            fwd_amt = _negate(length)
            bwd_amt = length2
        else:
            fwd_amt = length
            bwd_amt = _negate(length2)
        fwd = f"extrude({sketch_var}, amount={format_value(fwd_amt)})"
        bwd = f"extrude({sketch_var}, amount={format_value(bwd_amt)})"
        helpers.add("_pattern_union")
        if base_var is None:
            line = f"{pad.Name} = _pattern_union({fwd}, {bwd})"
            deps = [sketch_var]
        else:
            line = f"{pad.Name} = _pattern_union({base_var}, {fwd}, {bwd})"
            deps = [base_var, sketch_var]
        comment = (
            f"PartDesign::Pad {pad.Label!r}: TwoLengths fwd={length} bwd={length2}"
            + (" (reversed)" if reversed_ else "")
        )
    else:
        # Midplane extrudes total ``length`` symmetrically about the sketch
        # plane → build123d's both=True with amount=length/2 produces the
        # same result.
        if midplane:
            half = f"{length} / 2" if isinstance(length, str) else length / 2
            extrude_args = f"{sketch_var}, amount={format_value(half)}, both=True"
        else:
            amount = _negate(length) if reversed_ else length
            extrude_args = f"{sketch_var}, amount={format_value(amount)}"
        if base_var is None:
            line = f"{pad.Name} = extrude({extrude_args})"
            deps = [sketch_var]
        else:
            line = f"{pad.Name} = {base_var} + extrude({extrude_args})"
            deps = [base_var, sketch_var]
        comment = (
            f"PartDesign::Pad {pad.Label!r}: length={length}"
            + (" (reversed)" if reversed_ else "")
        )

    unit = TranslationUnit(
        var_name=pad.Name,
        imports=imports,
        lines=[line],
        comment=comment,
        helpers=helpers,
    )
    ctx.add_step(
        feature_type="pad",
        feature_name=pad.Name,
        depends_on=deps,
        renamed_from_default=(pad.Label != pad.Name),
        build123d_code=unit.lines[0],
        properties=extract_properties(getattr(pad, "Shape", None)),
    )
    return unit


# ---------------------------------------------------------------------------
# Pocket
# ---------------------------------------------------------------------------


_THROUGH_ALL_LENGTH = 1_000_000.0


def _resolve_pocket_uptoface_length(pocket) -> float:
    """For Pocket.Type in ('UpToFirst', 'UpToFace'), compute the effective
    carve length by inspecting the FreeCAD-evaluated body shape.

    ``carved_volume = BaseFeature.Shape.Volume - Pocket.Shape.Volume``;
    dividing by the sketch profile's area gives the depth the carve
    actually went. This lets us emit ``UpToFirst`` / ``UpToFace`` as a
    regular ``Type='Length'`` extrude — the translator never needs to
    track or resolve build123d's face-selection API.

    Assumes the carve is a prism (no taper, no curved bottom). Holds for
    the way both modes are used in the library: a sketch + planar normal.
    """
    import Part  # lazy
    base_feature = pocket.BaseFeature
    if base_feature is None or not hasattr(base_feature, "Shape"):
        raise UnsupportedFeatureError(
            pocket.TypeId,
            f"{pocket.Label} ({pocket.Type} Pocket without resolvable BaseFeature)",
        )
    base_vol = float(base_feature.Shape.Volume)
    own_vol = float(pocket.Shape.Volume)
    carved = base_vol - own_vol
    if carved <= 0:
        raise UnsupportedFeatureError(
            pocket.TypeId,
            f"{pocket.Label} ({pocket.Type} Pocket: carved volume non-positive — "
            f"can't resolve effective length)",
        )
    profile = pocket.Profile
    if isinstance(profile, (list, tuple)):
        profile = profile[0]
    try:
        wires = profile.Shape.Wires
        faces = [Part.Face(w) for w in wires]
        area = sum(f.Area for f in faces)
    except Exception as exc:
        raise UnsupportedFeatureError(
            pocket.TypeId,
            f"{pocket.Label} ({pocket.Type} Pocket: can't compute "
            f"sketch profile area — {exc})",
        )
    if area <= 0:
        raise UnsupportedFeatureError(
            pocket.TypeId,
            f"{pocket.Label} ({pocket.Type} Pocket: zero-area sketch profile)",
        )
    return carved / area


def _translate_pocket(
    pocket, base_var: str, ctx: TranslationContext
) -> TranslationUnit:
    pocket_type = str(getattr(pocket, "Type", "Length"))
    if pocket_type not in {"Length", "ThroughAll", "UpToFirst", "UpToFace"}:
        raise UnsupportedFeatureError(
            pocket.TypeId,
            f"{pocket.Label} (Pocket.Type={pocket_type!r}; tier-2 supports "
            f"'Length', 'ThroughAll', 'UpToFirst', 'UpToFace')",
        )

    profile = pocket.Profile
    if isinstance(profile, (list, tuple)):
        profile = profile[0]
    sketch_var = profile.Name
    reversed_ = bool(getattr(pocket, "Reversed", False))
    midplane = bool(getattr(pocket, "Midplane", False))

    if pocket_type == "ThroughAll":
        length = _THROUGH_ALL_LENGTH
        if midplane:
            # ThroughAll + Midplane: carve effectively-infinite in both
            # directions about the sketch plane.
            line = (
                f"{pocket.Name} = {base_var} - "
                f"extrude({sketch_var}, amount={format_value(length / 2)}, both=True)"
            )
            note = "ThroughAll (midplane)"
        else:
            amount = length if reversed_ else -length
            line = (
                f"{pocket.Name} = {base_var} - "
                f"extrude({sketch_var}, amount={format_value(amount)})"
            )
            note = "ThroughAll" + (" (reversed)" if reversed_ else "")
    else:
        if pocket_type in ("UpToFirst", "UpToFace"):
            # Resolve to a numeric length by inspecting FreeCAD's evaluated
            # body shape (BaseFeature.Volume - Pocket.Volume / sketch area).
            length = _resolve_pocket_uptoface_length(pocket)
            length_note = f"{pocket_type} → length={length:.4g}"
        else:
            length = _value(pocket, "Length", ctx)
            length_note = f"length={length}"
        if midplane:
            half = f"{length} / 2" if isinstance(length, str) else length / 2
            line = (
                f"{pocket.Name} = {base_var} - "
                f"extrude({sketch_var}, amount={format_value(half)}, both=True)"
            )
            note = f"{length_note} (midplane)"
        else:
            amount = length if reversed_ else _negate(length)
            line = (
                f"{pocket.Name} = {base_var} - "
                f"extrude({sketch_var}, amount={format_value(amount)})"
            )
            note = length_note + (" (reversed)" if reversed_ else "")

    unit = TranslationUnit(
        var_name=pocket.Name,
        imports={"extrude"},
        lines=[line],
        comment=f"PartDesign::Pocket {pocket.Label!r}: {note}",
    )
    ctx.add_step(
        feature_type="pocket",
        feature_name=pocket.Name,
        depends_on=[base_var, sketch_var],
        renamed_from_default=(pocket.Label != pocket.Name),
        build123d_code=unit.lines[0],
        properties=extract_properties(getattr(pocket, "Shape", None)),
    )
    return unit


# ---------------------------------------------------------------------------
# Revolution
# ---------------------------------------------------------------------------


def _axis_expr_from_reference(rev) -> tuple[str, set[str]]:
    ref = rev.ReferenceAxis
    if not ref:
        raise UnsupportedFeatureError(
            rev.TypeId, f"{rev.Label} (Revolution has no ReferenceAxis set)"
        )
    obj, subs = ref

    name = getattr(obj, "Name", "") or ""
    label = getattr(obj, "Label", "") or ""
    for candidate in (name, label):
        if candidate in _AXIS_OF_ORIGIN:
            return _AXIS_OF_ORIGIN[candidate], {"Axis"}

    if getattr(obj, "TypeId", "") == "Sketcher::SketchObject":
        return _sketch_axis_expr(obj, subs, rev)

    raise UnsupportedFeatureError(
        rev.TypeId,
        f"{rev.Label} (ReferenceAxis={name!r}/{label!r} not supported in tier-2)",
    )


def _sketch_axis_expr(sketch, subs, rev) -> tuple[str, set[str]]:
    import FreeCAD  # lazy

    sub = (subs[0] if subs else "") or ""
    if sub in ("", "H_Axis"):
        local = FreeCAD.Vector(1, 0, 0)
    elif sub == "V_Axis":
        local = FreeCAD.Vector(0, 1, 0)
    else:
        raise UnsupportedFeatureError(
            rev.TypeId,
            f"{rev.Label} (Sketch axis subelement {sub!r} not supported)",
        )

    rot = sketch.Placement.Rotation
    direction = rot.multVec(local)
    origin = sketch.Placement.Base

    for axis_name, vec in (
        ("Axis.X", FreeCAD.Vector(1, 0, 0)),
        ("Axis.Y", FreeCAD.Vector(0, 1, 0)),
        ("Axis.Z", FreeCAD.Vector(0, 0, 1)),
    ):
        if (
            abs(direction.x - vec.x) < 1e-9
            and abs(direction.y - vec.y) < 1e-9
            and abs(direction.z - vec.z) < 1e-9
            and abs(origin.x) < 1e-9
            and abs(origin.y) < 1e-9
            and abs(origin.z) < 1e-9
        ):
            return axis_name, {"Axis"}

    return (
        f"Axis(({vfmt(origin.x, origin.y, origin.z)}), "
        f"({vfmt(direction.x, direction.y, direction.z)}))"
    ), {"Axis"}


def _translate_revolution(
    rev, base: str | None, ctx: TranslationContext
) -> TranslationUnit:
    if str(getattr(rev, "Type", "Angle")) != "Angle":
        raise UnsupportedFeatureError(
            rev.TypeId,
            f"{rev.Label} (Revolution.Type={rev.Type!r}; only 'Angle' supported)",
        )
    if bool(getattr(rev, "Midplane", False)):
        raise UnsupportedFeatureError(
            rev.TypeId, f"{rev.Label} (Midplane Revolution not yet supported)"
        )

    profile = rev.Profile
    if isinstance(profile, (list, tuple)):
        profile = profile[0]
    sketch_var = profile.Name
    angle = _value(rev, "Angle", ctx)
    reversed_ = bool(getattr(rev, "Reversed", False))
    axis_expr, axis_imports = _axis_expr_from_reference(rev)
    if reversed_:
        angle = _negate(angle)

    imports = {"revolve"} | axis_imports

    if base is None:
        line = (
            f"{rev.Name} = revolve({sketch_var}, axis={axis_expr}, "
            f"revolution_arc={format_value(angle)})"
        )
        depends = [sketch_var]
    else:
        line = (
            f"{rev.Name} = {base} + revolve({sketch_var}, axis={axis_expr}, "
            f"revolution_arc={format_value(angle)})"
        )
        depends = [base, sketch_var]

    unit = TranslationUnit(
        var_name=rev.Name,
        imports=imports,
        lines=[line],
        comment=f"PartDesign::Revolution {rev.Label!r}: angle={angle}"
                + (" (reversed)" if reversed_ else ""),
    )
    ctx.add_step(
        feature_type="revolution",
        feature_name=rev.Name,
        depends_on=depends,
        renamed_from_default=(rev.Label != rev.Name),
        build123d_code=unit.lines[0],
        properties=extract_properties(getattr(rev, "Shape", None)),
    )
    return unit


# ---------------------------------------------------------------------------
# Groove — subtractive revolution (tier 2)
# ---------------------------------------------------------------------------


def _translate_groove(
    grv, base: str, ctx: TranslationContext
) -> TranslationUnit:
    """Emit a Groove: subtract a revolved sketch from the running body.

    Same property layout as Revolution (Profile / ReferenceAxis / Angle /
    Reversed / Midplane), but the result is ``base - revolve(...)`` instead
    of ``base + revolve(...)``. Reuses ``_axis_expr_from_reference``.
    """
    if str(getattr(grv, "Type", "Angle")) != "Angle":
        raise UnsupportedFeatureError(
            grv.TypeId,
            f"{grv.Label} (Groove.Type={grv.Type!r}; only 'Angle' supported)",
        )
    if bool(getattr(grv, "Midplane", False)):
        raise UnsupportedFeatureError(
            grv.TypeId, f"{grv.Label} (Midplane Groove not yet supported)"
        )

    profile = grv.Profile
    if isinstance(profile, (list, tuple)):
        profile = profile[0]
    sketch_var = profile.Name
    angle = _value(grv, "Angle", ctx)
    reversed_ = bool(getattr(grv, "Reversed", False))
    axis_expr, axis_imports = _axis_expr_from_reference(grv)
    if reversed_:
        angle = _negate(angle)

    imports = {"revolve"} | axis_imports
    line = (
        f"{grv.Name} = {base} - revolve({sketch_var}, axis={axis_expr}, "
        f"revolution_arc={format_value(angle)})"
    )

    unit = TranslationUnit(
        var_name=grv.Name,
        imports=imports,
        lines=[line],
        comment=f"PartDesign::Groove {grv.Label!r}: angle={angle}"
                + (" (reversed)" if reversed_ else ""),
    )
    ctx.add_step(
        feature_type="groove",
        feature_name=grv.Name,
        depends_on=[base, sketch_var],
        renamed_from_default=(grv.Label != grv.Name),
        build123d_code=line,
        properties=extract_properties(getattr(grv, "Shape", None)),
    )
    return unit


def _translate_atomic_revolution(
    rev, ctx: TranslationContext
) -> list[TranslationUnit]:
    """Top-level (non-Body) Revolution. Its sketch is translated separately
    via the Sketcher::SketchObject top-level handler in document order."""
    return [_translate_revolution(rev, base=None, ctx=ctx)]


# ---------------------------------------------------------------------------
# Fillet / Chamfer — tier 3
# ---------------------------------------------------------------------------


def _edge_midpoint(edge) -> tuple[float, float, float]:
    """Geometric midpoint of an OCC edge in world coordinates."""
    t = (edge.FirstParameter + edge.LastParameter) / 2.0
    p = edge.valueAt(t)
    return (p.x, p.y, p.z)


def _resolve_edge_midpoints(parent_shape, edge_names) -> list[tuple[float, float, float]]:
    """Convert FreeCAD edge / face references into geometric midpoints.

    FreeCAD references shape elements by index — 'Edge<N>' picks an edge
    directly; 'Face<N>' means "all edges of face N" (used when filleting
    every edge of a flat surface). For 'Face<N>' we expand to the face's
    contour edges.

    The midpoints are then emitted into build123d code as the selector
    targets — that's the mechanic ADR-0001 validates.
    """
    midpoints: list[tuple[float, float, float]] = []
    edges = parent_shape.Edges
    faces = parent_shape.Faces
    for name in edge_names:
        if name.startswith("Edge"):
            idx = int(name[len("Edge"):]) - 1
            if idx < 0 or idx >= len(edges):
                raise UnsupportedFeatureError(
                    "PartDesign::Fillet",
                    f"edge index {idx + 1} out of range (shape has {len(edges)} edges)",
                )
            midpoints.append(_edge_midpoint(edges[idx]))
        elif name.startswith("Face"):
            idx = int(name[len("Face"):]) - 1
            if idx < 0 or idx >= len(faces):
                raise UnsupportedFeatureError(
                    "PartDesign::Fillet",
                    f"face index {idx + 1} out of range (shape has {len(faces)} faces)",
                )
            for fe in faces[idx].Edges:
                midpoints.append(_edge_midpoint(fe))
        else:
            raise UnsupportedFeatureError(
                "PartDesign::Fillet",
                f"reference {name!r} not understood (expected 'Edge<N>' or 'Face<N>')",
            )
    return midpoints


def _format_midpoints(midpoints: list[tuple[float, float, float]]) -> str:
    return "[" + ", ".join(f"({vfmt(x, y, z)})" for x, y, z in midpoints) + "]"


def _dressup_unit(
    obj,
    base_var: str,
    builder: str,
    radius_attr: str,
    ctx: TranslationContext,
    feature_type: str,
) -> list[TranslationUnit]:
    """Common emit shape for Fillet and Chamfer.

    Both reference edges of a parent feature by name and apply a single
    scalar (radius for Fillet, Size for Chamfer) to all selected edges.
    Uses the module-level ``_edges_at`` helper for selection.
    """
    base = obj.Base  # (parent_object, [edge_names])
    if isinstance(base, (list, tuple)):
        parent, edge_names = base
    else:
        raise UnsupportedFeatureError(
            obj.TypeId, f"{obj.Label} (Base property has unexpected shape)"
        )

    parent_shape = parent.Shape
    midpoints = _resolve_edge_midpoints(parent_shape, edge_names)
    if not midpoints:
        raise UnsupportedFeatureError(
            obj.TypeId,
            f"{obj.Label} (no edges referenced)",
        )

    radius = _value(obj, radius_attr, ctx)
    midpoints_repr = _format_midpoints(midpoints)
    var = obj.Name

    if builder == "fillet":
        line = (
            f"{var} = fillet(_edges_at({base_var}, {midpoints_repr}), "
            f"radius={format_value(radius)})"
        )
    else:
        line = (
            f"{var} = chamfer(_edges_at({base_var}, {midpoints_repr}), "
            f"length={format_value(radius)})"
        )

    unit = TranslationUnit(
        var_name=var,
        imports={builder},
        lines=[line],
        comment=f"{obj.TypeId} {obj.Label!r}: "
                f"{radius_attr.lower()}={radius} on {len(edge_names)} edges of {parent.Name}",
        helpers={"_edges_at"},
    )
    ctx.add_step(
        feature_type=feature_type,
        feature_name=obj.Name,
        depends_on=[base_var],
        renamed_from_default=(obj.Label != obj.Name),
        build123d_code=line,
        properties=extract_properties(getattr(obj, "Shape", None)),
    )
    return [unit]


def _translate_fillet(
    fil, base_var: str, ctx: TranslationContext
) -> list[TranslationUnit]:
    return _dressup_unit(fil, base_var, "fillet", "Radius", ctx, "fillet")


def _translate_chamfer(
    cha, base_var: str, ctx: TranslationContext
) -> list[TranslationUnit]:
    # PartDesign::Chamfer's main attribute is named "Size" (the bevel length).
    return _dressup_unit(cha, base_var, "chamfer", "Size", ctx, "chamfer")


# ---------------------------------------------------------------------------
# Pattern (LinearPattern / PolarPattern / Mirrored) — tier 4
# ---------------------------------------------------------------------------

# Body-origin plane references → build123d Plane constants (for Mirrored).
_PLANE_OF_ORIGIN = {
    "XY_Plane": "Plane.XY",
    "XZ_Plane": "Plane.XZ",
    "YZ_Plane": "Plane.YZ",
}


def _prism_expression(orig, ctx: TranslationContext):
    """Build the build123d expression for a Pad/Pocket Original's prism.

    Returns ``(sign, expr, imports)`` where:
      - ``sign`` is ``'+'`` for Pad (additive) or ``'-'`` for Pocket
        (subtractive).
      - ``expr`` is an ``extrude(...)`` call producing the prism solid that
        the Original added or removed at its source location.
      - ``imports`` is the build123d names the expression references.

    The pattern emit composes the original's body with i*step-translated
    copies of this prism so each pattern copy reproduces the Original's
    material change. Restricted to Type='Length' Pad/Pocket in v1.
    """
    tid = orig.TypeId
    if tid not in ("PartDesign::Pad", "PartDesign::Pocket"):
        raise UnsupportedFeatureError(
            "PartDesign::Pattern",
            f"Pattern Original is {tid!r}; v1 only supports Pad / Pocket",
        )
    orig_type = str(getattr(orig, "Type", "Length"))
    if orig_type not in ("Length", "ThroughAll"):
        raise UnsupportedFeatureError(
            "PartDesign::Pattern",
            f"Pattern Original {orig.Label!r} has Type={orig.Type!r}; "
            f"v1 supports Type='Length' / 'ThroughAll'",
        )

    profile = orig.Profile
    if isinstance(profile, (list, tuple)):
        profile = profile[0]
    sketch_var = profile.Name

    reversed_ = bool(getattr(orig, "Reversed", False))
    midplane = bool(getattr(orig, "Midplane", False))

    # ThroughAll uses the same sentinel length the standalone Pocket
    # translator uses (matches FreeCAD's "extrude a very long prism, OCCT
    # clips to body" pattern).
    if orig_type == "ThroughAll":
        length = _THROUGH_ALL_LENGTH
    else:
        length = _value(orig, "Length", ctx)

    if midplane:
        half = f"{length} / 2" if isinstance(length, str) else length / 2
        expr = f"extrude({sketch_var}, amount={format_value(half)}, both=True)"
    else:
        if tid == "PartDesign::Pad":
            amt = _negate(length) if reversed_ else length
        else:  # Pocket
            amt = length if reversed_ else _negate(length)
        expr = f"extrude({sketch_var}, amount={format_value(amt)})"

    sign = "+" if tid == "PartDesign::Pad" else "-"
    return sign, expr, {"extrude"}


def _resolve_direction(ref, feature):
    """Resolve a LinearPattern Direction reference to a unit (x, y, z) tuple.

    Supports:
      * Body-origin axes (App::Line labelled X_Axis / Y_Axis / Z_Axis).
      * Sketch H_Axis / V_Axis / N_Axis when the sketch's rotation is identity.
    """
    if not ref:
        raise UnsupportedFeatureError(
            feature.TypeId, f"{feature.Label} (Direction reference is empty)"
        )
    obj, subs = ref
    sub = (subs[0] if subs else "") or ""

    name = getattr(obj, "Name", "") or ""
    label = getattr(obj, "Label", "") or ""
    type_id = getattr(obj, "TypeId", "")

    # Body-origin straight axes — the App::Line objects under BodyOrigin.
    canonical = {
        "X_Axis": (1.0, 0.0, 0.0),
        "Y_Axis": (0.0, 1.0, 0.0),
        "Z_Axis": (0.0, 0.0, 1.0),
    }
    for candidate in (name, label):
        if candidate in canonical:
            return canonical[candidate]

    if type_id == "Sketcher::SketchObject":
        return _resolve_sketch_axis(obj, sub, feature)

    raise UnsupportedFeatureError(
        feature.TypeId,
        f"{feature.Label} (Direction {name!r}/{label!r} subelement {sub!r} "
        f"not supported in v1)",
    )


def _resolve_sketch_axis(sketch, sub, feature):
    """Map a sketch's H_Axis / V_Axis / N_Axis to a world-frame unit vector.

    Restricted to sketches with identity rotation in v1 — non-axis-aligned
    sketches would need full rotation handling that adds complexity beyond
    the typical Parts-Library file.
    """
    import FreeCAD  # lazy

    rot = sketch.Placement.Rotation
    if abs(rot.Angle) > _TOL:
        raise UnsupportedFeatureError(
            feature.TypeId,
            f"{feature.Label} (Sketch '{sketch.Label}' has non-identity rotation; "
            f"v1 supports sketch axes only on axis-aligned sketches)",
        )
    local = {
        "H_Axis": FreeCAD.Vector(1, 0, 0),
        "V_Axis": FreeCAD.Vector(0, 1, 0),
        "N_Axis": FreeCAD.Vector(0, 0, 1),
    }.get(sub)
    if local is None:
        raise UnsupportedFeatureError(
            feature.TypeId,
            f"{feature.Label} (Sketch subelement {sub!r} not understood; "
            f"expected H_Axis / V_Axis / N_Axis)",
        )
    return (float(local.x), float(local.y), float(local.z))


def _resolve_mirror_plane(ref, feature) -> str:
    """Resolve a Mirrored Plane reference to a build123d Plane expression.

    Handles three reference kinds:
      * Body Origin ``App::Plane`` (XY/XZ/YZ) — canonical Plane.XY etc.
      * ``PartDesign::Plane`` (DatumPlane) — placement-derived plane.
      * ``Sketcher::SketchObject`` — the plane the sketch is drawn on (its
        Placement). FreeCAD treats the sketch's local XY as the mirror plane.
    """
    if not ref:
        raise UnsupportedFeatureError(
            feature.TypeId, f"{feature.Label} (MirrorPlane reference is empty)"
        )
    obj, _subs = ref
    name = getattr(obj, "Name", "") or ""
    label = getattr(obj, "Label", "") or ""

    # Body Origin App::Plane → Plane.XY / Plane.XZ / Plane.YZ
    for candidate in (name, label):
        if candidate in _PLANE_OF_ORIGIN:
            return _PLANE_OF_ORIGIN[candidate]

    # User-created DatumPlanes (PartDesign::Plane or App::Plane*nnn*) and
    # Sketcher sketches both expose a Placement whose +Z is the mirror normal.
    # App::Plane is also the TypeId of the Body Origin planes — those were
    # caught above by name lookup, so reaching here means it's a copy.
    type_id = getattr(obj, "TypeId", "")
    if type_id in ("PartDesign::Plane", "App::Plane", "Sketcher::SketchObject"):
        return _plane_from_placement(obj.Placement)

    raise UnsupportedFeatureError(
        feature.TypeId,
        f"{feature.Label} (MirrorPlane {name!r}/{label!r}: TypeId={type_id!r} "
        f"not supported; v1 handles Body Origin / App::Plane / "
        f"PartDesign::Plane / Sketch references)",
    )


def _plane_from_placement(placement) -> str:
    """Convert a FreeCAD Placement to a build123d ``Plane(...)`` expression.

    The local frame's +Z is the plane normal. If the placement is identity
    (origin at world origin, no rotation), the result is ``Plane.XY``.
    Otherwise emit ``Plane(origin=(x, y, z), z_dir=(nx, ny, nz))``.
    """
    import FreeCAD

    b = placement.Base
    n = placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))

    is_origin = (
        abs(b.x) < _TOL and abs(b.y) < _TOL and abs(b.z) < _TOL
    )
    canonical = {
        (1.0, 0.0, 0.0): "Plane.YZ",
        (0.0, 1.0, 0.0): "Plane.XZ",
        (0.0, 0.0, 1.0): "Plane.XY",
    }
    key = (round(n.x, 9), round(n.y, 9), round(n.z, 9))
    if is_origin and key in canonical:
        return canonical[key]
    return (
        f"Plane(origin=({vfmt(b.x, b.y, b.z)}), "
        f"z_dir=({vfmt(n.x, n.y, n.z)}))"
    )


def _location_for_linear(direction, step) -> str:
    """Build a build123d Pos(...) expression for one linear-pattern copy."""
    dx, dy, dz = direction
    if isinstance(step, str):
        return f"Pos({format_value(dx)} * ({step}), {format_value(dy)} * ({step}), {format_value(dz)} * ({step}))"
    return f"Pos({vfmt(dx * step, dy * step, dz * step)})"


def _rotation_for_polar(direction, angle_deg) -> str:
    """Build a build123d Rot(...) expression for one polar-pattern copy.

    direction is a unit vector indicating which axis to rotate about.
    angle_deg is signed (already reflects Reversed).
    """
    dx, dy, dz = direction
    if abs(dx - 1) < _TOL and abs(dy) < _TOL and abs(dz) < _TOL:
        return f"Rot(X={format_value(angle_deg)})"
    if abs(dy - 1) < _TOL and abs(dx) < _TOL and abs(dz) < _TOL:
        return f"Rot(Y={format_value(angle_deg)})"
    if abs(dz - 1) < _TOL and abs(dx) < _TOL and abs(dy) < _TOL:
        return f"Rot(Z={format_value(angle_deg)})"
    # Negative axes — Rot Y=-θ is equivalent to rotating about +Y by -θ.
    if abs(dx + 1) < _TOL and abs(dy) < _TOL and abs(dz) < _TOL:
        return f"Rot(X={format_value(-angle_deg)})"
    if abs(dy + 1) < _TOL and abs(dx) < _TOL and abs(dz) < _TOL:
        return f"Rot(Y={format_value(-angle_deg)})"
    if abs(dz + 1) < _TOL and abs(dx) < _TOL and abs(dy) < _TOL:
        return f"Rot(Z={format_value(-angle_deg)})"
    raise UnsupportedFeatureError(
        "PartDesign::PolarPattern",
        f"PolarPattern axis direction ({dx},{dy},{dz}) is non-canonical; "
        f"v1 supports axis-aligned rotation only",
    )


def _quantity_value(q) -> float:
    return float(q.Value) if hasattr(q, "Value") else float(q)


def _linear_step(pat, occurrences: int) -> float:
    """Per-copy distance for a LinearPattern.

    Mode='length': step = Length / (Occurrences - 1).
    Mode='offset': step = Offset (per-copy distance set explicitly).
    """
    if occurrences <= 1:
        return 0.0
    mode = str(getattr(pat, "Mode", "length"))
    if mode == "length":
        return _quantity_value(pat.Length) / (occurrences - 1)
    if mode == "offset":
        return _quantity_value(pat.Offset)
    raise UnsupportedFeatureError(
        pat.TypeId,
        f"{pat.Label} (LinearPattern Mode={mode!r}; v1 supports 'length' / 'offset')",
    )


def _polar_step(pat, occurrences: int) -> float:
    """Per-copy angle (degrees) for a PolarPattern.

    Mode='angle' with abs(Angle) ≈ 360°: full revolution → step = Angle / Occurrences.
    Mode='angle' partial sweep:           last copy at Angle → step = Angle / (Occurrences - 1).
    Mode='offset': step = Offset directly.

    Note ``pat.Offset`` is unreliable in Mode='angle' files (FreeCAD doesn't
    update it when the user edits Angle / Occurrences), so we compute from
    the source-of-truth fields instead.
    """
    if occurrences <= 1:
        return 0.0
    mode = str(getattr(pat, "Mode", "angle"))
    if mode == "angle":
        angle = _quantity_value(pat.Angle)
        if abs(abs(angle) - 360.0) < _TOL:
            return angle / occurrences
        return angle / (occurrences - 1)
    if mode == "offset":
        return _quantity_value(pat.Offset)
    raise UnsupportedFeatureError(
        pat.TypeId,
        f"{pat.Label} (PolarPattern Mode={mode!r}; v1 supports 'angle' / 'offset')",
    )


def _translate_pattern(
    pat, current_var: str, ctx: TranslationContext
) -> TranslationUnit:
    """Emit a LinearPattern / PolarPattern / Mirrored.

    The pattern operates on one *or more* Originals (Pad / Pocket). Each
    Original's prism is added/subtracted at every i=1..N-1 transformed
    location, chained onto the current body shape (which already includes
    the i=0 case from when each Original was translated earlier in body order).

    All Originals must share the same additive/subtractive sense (all Pads
    or all Pockets) — mixed Pad+Pocket Originals are uncommon enough in the
    Parts Library to leave for a future iteration.
    """
    originals = list(pat.Originals)
    if not originals:
        raise UnsupportedFeatureError(
            pat.TypeId,
            f"{pat.Label} (no Originals)",
        )
    prisms = [_prism_expression(o, ctx) for o in originals]
    signs = {p[0] for p in prisms}
    if len(signs) > 1:
        raise UnsupportedFeatureError(
            pat.TypeId,
            f"{pat.Label} (Originals mix Pad and Pocket; v1 requires all-additive "
            f"or all-subtractive Originals)",
        )
    sign = next(iter(signs))
    prism_exprs = [p[1] for p in prisms]
    prism_imports: set[str] = set()
    for _, _, imp in prisms:
        prism_imports.update(imp)

    tid = pat.TypeId
    imports = set(prism_imports)
    helpers: set[str] = set()
    extra_terms: list[str] = []

    if tid == "PartDesign::LinearPattern":
        occurrences = int(getattr(pat, "Occurrences", 1))
        if occurrences < 1:
            raise UnsupportedFeatureError(
                tid, f"{pat.Label} (Occurrences={occurrences})"
            )
        direction = _resolve_direction(pat.Direction, pat)
        offset = _linear_step(pat, occurrences)
        if bool(getattr(pat, "Reversed", False)):
            offset = -offset
        for i in range(1, occurrences):
            step = i * offset
            loc = _location_for_linear(direction, step)
            for prism_expr in prism_exprs:
                extra_terms.append(f"{loc} * {prism_expr}")
        imports.add("Pos")
        note = (
            f"LinearPattern along ({direction[0]:g}, {direction[1]:g}, {direction[2]:g}), "
            f"step={offset}, occurrences={occurrences}"
            + (f", {len(originals)} originals" if len(originals) > 1 else "")
        )

    elif tid == "PartDesign::PolarPattern":
        occurrences = int(getattr(pat, "Occurrences", 1))
        if occurrences < 1:
            raise UnsupportedFeatureError(
                tid, f"{pat.Label} (Occurrences={occurrences})"
            )
        direction = _resolve_direction(pat.Axis, pat)
        offset = _polar_step(pat, occurrences)
        if bool(getattr(pat, "Reversed", False)):
            offset = -offset
        for i in range(1, occurrences):
            angle = i * offset
            rot = _rotation_for_polar(direction, angle)
            for prism_expr in prism_exprs:
                extra_terms.append(f"{rot} * {prism_expr}")
        imports.add("Rot")
        note = (
            f"PolarPattern around ({direction[0]:g}, {direction[1]:g}, {direction[2]:g}), "
            f"step={offset}deg, occurrences={occurrences}"
            + (f", {len(originals)} originals" if len(originals) > 1 else "")
        )

    else:  # PartDesign::Mirrored
        plane_expr = _resolve_mirror_plane(pat.MirrorPlane, pat)
        for prism_expr in prism_exprs:
            extra_terms.append(f"mirror({prism_expr}, about={plane_expr})")
        imports.add("mirror")
        imports.add("Plane")
        note = (
            f"Mirrored across {plane_expr}"
            + (f", {len(originals)} originals" if len(originals) > 1 else "")
        )

    if not extra_terms:
        # No additional copies — occurrences=1 — pattern is a no-op.
        # Emit a trivial assignment so downstream features see the new name.
        line = f"{pat.Name} = {current_var}"
    else:
        # Route through _pattern_union / _pattern_difference helpers rather
        # than chained ``+`` / ``-``. The chained form returns a Compound
        # without boolean fusion, which gives the wrong volume for patterns
        # whose copies overlap (e.g. sprocket teeth meeting at the hub).
        helper = "_pattern_union" if sign == "+" else "_pattern_difference"
        helpers.add(helper)
        args = ", ".join([current_var, *extra_terms])
        line = f"{pat.Name} = {helper}({args})"

    unit = TranslationUnit(
        var_name=pat.Name,
        imports=imports,
        lines=[line],
        comment=f"{pat.TypeId} {pat.Label!r}: {note}",
        helpers=helpers,
    )
    ctx.add_step(
        feature_type="pattern",
        feature_name=pat.Name,
        depends_on=[current_var],
        renamed_from_default=(pat.Label != pat.Name),
        build123d_code=line,
        properties=extract_properties(getattr(pat, "Shape", None)),
    )
    return unit


# ---------------------------------------------------------------------------
# Top-level atomic handlers (Body-less legacy PartDesign + Part-workbench
# equivalents). The Parts Library has many old files where PartDesign features
# live directly in the document without a containing Body.
# ---------------------------------------------------------------------------


def _previous_solid_in_doc(obj) -> str | None:
    """Find the most recent solid-producing object before obj in doc order."""
    found = _previous_solid_in_doc_with_typeid(obj)
    return found[0] if found else None


def _previous_solid_in_doc_with_typeid(obj) -> tuple[str, str] | None:
    """As above, plus the previous object's TypeId — needed because some
    chaining rules depend on whether the previous solid came from Part or
    PartDesign workbench (see body-less Pocket Length semantics)."""
    doc = obj.Document
    prev: tuple[str, str] | None = None
    for o in doc.Objects:
        if o.Name == obj.Name:
            return prev
        try:
            shape = getattr(o, "Shape", None)
            if shape is not None and not shape.isNull() and shape.Solids:
                prev = (o.Name, o.TypeId)
        except Exception:
            pass
    return None


_BOOLEAN_REFERENCE_TYPES = {
    "Part::Cut", "Part::Fuse", "Part::Common",
    "Part::MultiFuse", "Part::MultiCommon", "Part::Compound",
    "Part::Mirroring",
}


def _is_referenced_by_top_level_boolean(pad) -> bool:
    """True if any document object of a top-level boolean / composition
    type references this Pad as Base / Tool / Source / Links.

    Used by ``_translate_atomic_pad`` to decide whether the Pad stands
    alone (referenced separately by a downstream Cut/Fuse/etc.) or
    chains into the next Pad. Steel-sheets-3000mm is the canonical
    standalone case: two Pads of identical sketch area feeding a Cut
    that subtracts one from the other; the Pads must NOT chain.
    """
    doc = pad.Document
    for obj in doc.Objects:
        if obj.TypeId not in _BOOLEAN_REFERENCE_TYPES:
            continue
        for prop in ("Base", "Tool", "Source"):
            ref = getattr(obj, prop, None)
            if ref is pad:
                return True
        links = getattr(obj, "Links", None) or []
        for link in links:
            if link is pad:
                return True
    return False


def _translate_atomic_pad(pad, ctx: TranslationContext) -> list[TranslationUnit]:
    # Top-level Pads in legacy Body-less files sometimes chain additively
    # onto the previous solid (Winch fixture from batch 2 demonstrates
    # this) and sometimes stand alone (steel-sheets-3000mm has two Pads
    # whose downstream Part::Cut subtracts one from the other — they
    # must NOT chain). The distinguishing signal is whether the Pad is
    # referenced by a downstream Part-workbench boolean / composition:
    # if so, FreeCAD treats it as an independent solid.
    if _is_referenced_by_top_level_boolean(pad):
        base_var = None
    else:
        base_var = _previous_solid_in_doc(pad)
    return [_translate_pad(pad, ctx, base_var=base_var)]


def _translate_atomic_pocket(pocket, ctx: TranslationContext) -> list[TranslationUnit]:
    """A Pocket outside a Body. FreeCAD's actual behaviour for this legacy
    case is to *not* subtract — Pocket.Shape is just the extruded profile,
    same as a Pad. The "subtract from running body" semantic only applies
    inside a PartDesign::Body.

    See the M42 screw corpus fixture: its body-less Pocket has Shape = hex
    prism (not the bolt-with-hex-socket the designer presumably wanted).
    """
    pocket_type = str(getattr(pocket, "Type", "Length"))
    if pocket_type not in {"Length", "ThroughAll"}:
        raise UnsupportedFeatureError(
            pocket.TypeId,
            f"{pocket.Label} (atomic Pocket.Type={pocket_type!r}; tier-2 atomic "
            f"Pocket only supports 'Length' and 'ThroughAll')",
        )

    profile = pocket.Profile
    if isinstance(profile, (list, tuple)):
        profile = profile[0]
    sketch_var = profile.Name
    reversed_ = bool(getattr(pocket, "Reversed", False))

    if pocket_type == "ThroughAll":
        # Body-less ThroughAll Pocket: FreeCAD subtracts from the previous
        # solid in document order (the only way "through-all" makes sense
        # without a Body to be "through"). The ANSI hex cap screw fixture
        # exhibits this — Pocket.Shape = Revolution - hex carve.
        base_var = _previous_solid_in_doc(pocket)
        if base_var is None:
            raise UnsupportedFeatureError(
                pocket.TypeId,
                f"{pocket.Label} (atomic ThroughAll Pocket with no previous solid)",
            )
        length = _THROUGH_ALL_LENGTH
        amount = length if reversed_ else -length
        unit = TranslationUnit(
            var_name=pocket.Name,
            imports={"extrude"},
            lines=[
                f"{pocket.Name} = {base_var} - extrude({sketch_var}, amount={amount})"
            ],
            comment=f"PartDesign::Pocket {pocket.Label!r} (body-less, ThroughAll)"
                    + (" reversed" if reversed_ else ""),
        )
        deps = [base_var, sketch_var]
    else:
        # Body-less Length Pocket: FreeCAD's behaviour depends on the
        # workbench of the previous solid.
        # - Previous solid is from PartDesign (e.g. PartDesign::Revolution
        #   in the F623ZZ Ball Bearing fixture): chain — Pocket subtracts
        #   from the previous solid.
        # - Previous solid is from Part workbench (e.g. Part::Revolution in
        #   the M42 screw fixture): standalone — Pocket.Shape is just the
        #   extruded prism, no subtraction.
        # This is the inconsistency reported in batch 2's KNOWN_ISSUES.
        length = float(pocket.Length.Value)
        amount = length if reversed_ else -length
        prev = _previous_solid_in_doc_with_typeid(pocket)
        if prev is not None and prev[1].startswith("PartDesign::"):
            base_var = prev[0]
            unit = TranslationUnit(
                var_name=pocket.Name,
                imports={"extrude"},
                lines=[
                    f"{pocket.Name} = {base_var} - "
                    f"extrude({sketch_var}, amount={format_value(amount)})"
                ],
                comment=f"PartDesign::Pocket {pocket.Label!r} "
                        f"(body-less, chained from {prev[1]}): length={length}"
                        + (" (reversed)" if reversed_ else ""),
            )
            deps = [base_var, sketch_var]
        else:
            unit = TranslationUnit(
                var_name=pocket.Name,
                imports={"extrude"},
                lines=[
                    f"{pocket.Name} = extrude({sketch_var}, amount={format_value(amount)})"
                ],
                comment=f"PartDesign::Pocket {pocket.Label!r} "
                        f"(body-less, standalone): length={length}"
                        + (" (reversed)" if reversed_ else ""),
            )
            deps = [sketch_var]

    ctx.add_step(
        feature_type="pocket",
        feature_name=pocket.Name,
        depends_on=deps,
        renamed_from_default=(pocket.Label != pocket.Name),
        build123d_code=unit.lines[0],
        properties=extract_properties(getattr(pocket, "Shape", None)),
    )
    return [unit]


def _translate_atomic_fillet(fil, ctx: TranslationContext) -> list[TranslationUnit]:
    base = fil.Base
    if not isinstance(base, (list, tuple)) or not base or base[0] is None:
        # Fall back to previous solid in doc if Base is unset
        base_var = _previous_solid_in_doc(fil)
        if base_var is None:
            raise UnsupportedFeatureError(
                fil.TypeId,
                f"{fil.Label} (Fillet has no Base feature)",
            )
    else:
        base_var = base[0].Name
    return _translate_fillet(fil, base_var, ctx)


def _translate_atomic_chamfer(cha, ctx: TranslationContext) -> list[TranslationUnit]:
    base = cha.Base
    if not isinstance(base, (list, tuple)) or not base or base[0] is None:
        base_var = _previous_solid_in_doc(cha)
        if base_var is None:
            raise UnsupportedFeatureError(
                cha.TypeId,
                f"{cha.Label} (Chamfer has no Base feature)",
            )
    else:
        base_var = base[0].Name
    return _translate_chamfer(cha, base_var, ctx)


def _translate_atomic_pattern(pat, ctx: TranslationContext) -> list[TranslationUnit]:
    """LinearPattern / PolarPattern / Mirrored at document level.

    Older Parts Library files apply patterns to a body-less Pad/Pocket. The
    base for the chain comes from the previous solid in document order
    (which the Pattern's Originals reference). Mirrors ``_translate_atomic_fillet``.
    """
    base_var = _previous_solid_in_doc(pat)
    if base_var is None:
        raise UnsupportedFeatureError(
            pat.TypeId,
            f"{pat.Label} (atomic Pattern with no previous solid in document)",
        )
    return [_translate_pattern(pat, base_var, ctx)]


# ---------------------------------------------------------------------------
# Part workbench Extrusion / Revolution (equivalent to Pad / Revolution but
# in the Part workbench rather than PartDesign).
# ---------------------------------------------------------------------------


def _translate_part_extrusion(ext, ctx: TranslationContext) -> list[TranslationUnit]:
    """Part::Extrusion → build123d extrude with explicit direction.

    Direction comes from the Dir vector. Length: LengthFwd if non-zero, else
    the magnitude of Dir (common convention in older Parts Library files).
    Symmetric / Reversed / TaperAngle / DirMode != Custom are not yet
    supported.
    """
    if str(getattr(ext, "DirMode", "Custom")) != "Custom":
        raise UnsupportedFeatureError(
            ext.TypeId,
            f"{ext.Label} (Part::Extrusion DirMode={ext.DirMode!r}; only "
            f"'Custom' supported)",
        )
    if bool(getattr(ext, "Symmetric", False)):
        raise UnsupportedFeatureError(
            ext.TypeId, f"{ext.Label} (Symmetric Extrusion not yet supported)"
        )
    if abs(float(getattr(ext, "TaperAngle", 0.0).Value
                  if hasattr(getattr(ext, "TaperAngle", 0.0), "Value")
                  else getattr(ext, "TaperAngle", 0.0))) > 1e-12:
        raise UnsupportedFeatureError(
            ext.TypeId, f"{ext.Label} (Tapered Extrusion not yet supported)"
        )

    base = ext.Base
    base_var = base.Name

    dir_vec = ext.Dir
    dir_mag = (dir_vec.x ** 2 + dir_vec.y ** 2 + dir_vec.z ** 2) ** 0.5
    if dir_mag < 1e-12:
        raise UnsupportedFeatureError(
            ext.TypeId, f"{ext.Label} (Extrusion Dir is zero)"
        )

    length_fwd_raw = getattr(ext, "LengthFwd", 0.0)
    length_fwd = float(length_fwd_raw.Value) if hasattr(length_fwd_raw, "Value") else float(length_fwd_raw)
    if length_fwd > 1e-12:
        amount = length_fwd
    else:
        amount = dir_mag

    if bool(getattr(ext, "Reversed", False)):
        amount = -amount

    ux = dir_vec.x / dir_mag
    uy = dir_vec.y / dir_mag
    uz = dir_vec.z / dir_mag

    # If direction aligns with the sketch's natural normal (+Z for an XY
    # sketch), omit `direction` for a cleaner emit; otherwise pass it.
    sketch_plane = base.Placement
    aligns_with_z = (
        abs(sketch_plane.Rotation.Angle) < 1e-9
        and abs(ux) < 1e-9 and abs(uy) < 1e-9 and abs(uz - 1) < 1e-9
    )
    if aligns_with_z:
        line = f"{ext.Name} = extrude({base_var}, amount={format_value(amount)})"
        imports = {"extrude"}
    else:
        line = (
            f"{ext.Name} = extrude({base_var}, amount={format_value(amount)}, "
            f"dir=({vfmt(ux, uy, uz)}))"
        )
        imports = {"extrude"}

    unit = TranslationUnit(
        var_name=ext.Name,
        imports=imports,
        lines=[line],
        comment=f"Part::Extrusion {ext.Label!r}: amount={amount}",
    )
    ctx.add_step(
        feature_type="extrusion",
        feature_name=ext.Name,
        depends_on=[base_var],
        renamed_from_default=(ext.Label != ext.Name),
        build123d_code=unit.lines[0],
        properties=extract_properties(getattr(ext, "Shape", None)),
    )
    return [unit]


def _translate_part_revolution(rev, ctx: TranslationContext) -> list[TranslationUnit]:
    """Part::Revolution → build123d revolve around an explicit axis.

    Axis: Base (origin) + Axis (direction). Angle: in degrees. Angle != 360
    is supported (unlike PartDesign::Revolution which v1 restricts).
    """
    if bool(getattr(rev, "Symmetric", False)):
        raise UnsupportedFeatureError(
            rev.TypeId, f"{rev.Label} (Symmetric Part::Revolution not yet supported)"
        )

    source = rev.Source
    source_var = source.Name

    angle_raw = rev.Angle
    angle = float(angle_raw.Value) if hasattr(angle_raw, "Value") else float(angle_raw)

    base_vec = rev.Base
    axis_vec = rev.Axis

    # Snap to Axis.X/.Y/.Z when origin is zero and axis is canonical.
    axis_expr: str
    imports = {"revolve", "Axis"}
    if (abs(base_vec.x) < 1e-9 and abs(base_vec.y) < 1e-9 and abs(base_vec.z) < 1e-9):
        canonical = {
            (1.0, 0.0, 0.0): "Axis.X",
            (0.0, 1.0, 0.0): "Axis.Y",
            (0.0, 0.0, 1.0): "Axis.Z",
        }
        key = (round(axis_vec.x, 9), round(axis_vec.y, 9), round(axis_vec.z, 9))
        axis_expr = canonical.get(key, (
            f"Axis(({vfmt(base_vec.x, base_vec.y, base_vec.z)}), "
            f"({vfmt(axis_vec.x, axis_vec.y, axis_vec.z)}))"
        ))
    else:
        axis_expr = (
            f"Axis(({vfmt(base_vec.x, base_vec.y, base_vec.z)}), "
            f"({vfmt(axis_vec.x, axis_vec.y, axis_vec.z)}))"
        )

    line = (
        f"{rev.Name} = revolve({source_var}, axis={axis_expr}, "
        f"revolution_arc={format_value(angle)})"
    )
    unit = TranslationUnit(
        var_name=rev.Name,
        imports=imports,
        lines=[line],
        comment=f"Part::Revolution {rev.Label!r}: angle={angle}",
    )
    ctx.add_step(
        feature_type="part_revolution",
        feature_name=rev.Name,
        depends_on=[source_var],
        renamed_from_default=(rev.Label != rev.Name),
        build123d_code=unit.lines[0],
        properties=extract_properties(getattr(rev, "Shape", None)),
    )
    return [unit]


def _translate_part_compound(comp, ctx: TranslationContext) -> list[TranslationUnit]:
    """Part::Compound → ``Compound([s1, s2, ...])`` over its Links.

    Each linked object is assumed already translated (forward-referenced
    by name). The build123d Compound aggregates them as a single shape;
    the verify harness aggregates volumes / inertia in parallel-axis
    over the multi-solid result.
    """
    links = list(getattr(comp, "Links", []) or [])
    if not links:
        raise UnsupportedFeatureError(
            comp.TypeId, f"{comp.Label} (empty Compound)"
        )
    part_vars = [obj.Name for obj in links]
    line = f"{comp.Name} = Compound([{', '.join(part_vars)}])"
    unit = TranslationUnit(
        var_name=comp.Name,
        imports={"Compound"},
        lines=[line],
        comment=f"Part::Compound {comp.Label!r}: {len(links)} parts",
    )
    ctx.add_step(
        feature_type="compound",
        feature_name=comp.Name,
        depends_on=part_vars,
        renamed_from_default=(comp.Label != comp.Name),
        build123d_code=line,
        properties=extract_properties(getattr(comp, "Shape", None)),
    )
    return [unit]


def _translate_part_mirroring(mir, ctx: TranslationContext) -> list[TranslationUnit]:
    """Part::Mirroring → ``mirror(source, about=Plane(origin, z_dir=normal))``.

    FreeCAD's Part::Mirroring mirrors ``Source`` across the plane defined
    by ``Base`` (a point on the plane) and ``Normal`` (the plane normal).
    build123d's ``mirror(shape, about=Plane)`` does the same.
    """
    source = getattr(mir, "Source", None)
    if source is None:
        raise UnsupportedFeatureError(
            mir.TypeId, f"{mir.Label} (Part::Mirroring with no Source)"
        )
    source_var = source.Name
    base = mir.Base
    normal = mir.Normal
    plane_expr = (
        f"Plane(origin=({format_value(base.x)}, {format_value(base.y)}, "
        f"{format_value(base.z)}), z_dir=({format_value(normal.x)}, "
        f"{format_value(normal.y)}, {format_value(normal.z)}))"
    )
    line = f"{mir.Name} = mirror({source_var}, about={plane_expr})"
    unit = TranslationUnit(
        var_name=mir.Name,
        imports={"mirror", "Plane"},
        lines=[line],
        comment=(
            f"Part::Mirroring {mir.Label!r}: "
            f"base=({base.x:g}, {base.y:g}, {base.z:g}), "
            f"normal=({normal.x:g}, {normal.y:g}, {normal.z:g})"
        ),
    )
    ctx.add_step(
        feature_type="mirroring",
        feature_name=mir.Name,
        depends_on=[source_var],
        renamed_from_default=(mir.Label != mir.Name),
        build123d_code=line,
        properties=extract_properties(getattr(mir, "Shape", None)),
    )
    return [unit]


TIER2_HANDLERS = {
    "PartDesign::Body": translate_body,
    # PartDesign features at the document level (no containing Body) —
    # common in older Parts Library files.
    "PartDesign::Pad": _translate_atomic_pad,
    "PartDesign::Pocket": _translate_atomic_pocket,
    "PartDesign::Revolution": _translate_atomic_revolution,
    "PartDesign::Fillet": _translate_atomic_fillet,
    "PartDesign::Chamfer": _translate_atomic_chamfer,
    "PartDesign::LinearPattern": _translate_atomic_pattern,
    "PartDesign::PolarPattern": _translate_atomic_pattern,
    "PartDesign::Mirrored": _translate_atomic_pattern,
    # Part workbench equivalents.
    "Part::Extrusion": _translate_part_extrusion,
    "Part::Revolution": _translate_part_revolution,
    "Part::Compound": _translate_part_compound,
    "Part::Mirroring": _translate_part_mirroring,
    # Standalone sketch at the document level.
    "Sketcher::SketchObject": translate_sketch,
}
