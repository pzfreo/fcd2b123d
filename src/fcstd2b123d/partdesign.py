"""PartDesign feature translators.

Tier-2 scope:
  - PartDesign::Body — walks Group, chains features into a running shape.
  - PartDesign::Pad — extrude (with Reversed option).
  - PartDesign::Pocket — subtractive extrude (Reversed, ThroughAll).
  - PartDesign::Revolution — revolve around X/Y/Z or sketch-local axes
    (with Reversed). Handled both inside a Body and as a top-level atomic
    feature (some legacy library files have a free-standing Revolution at
    document level).

Tier-3 scope:
  - PartDesign::Fillet — round the named edges of a parent feature.
  - PartDesign::Chamfer — bevel the named edges of a parent feature.

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

from .context import TranslationContext
from .emitter import TranslationUnit
from .errors import UnsupportedFeatureError
from .freecad_properties import extract_properties
from .sketch import translate_sketch

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


def translate_body(body, ctx: TranslationContext) -> list[TranslationUnit]:
    """Walk a PartDesign::Body and emit units in feature order.

    Subtractive / dressup features consume the running body shape; we track
    the last "result" variable name as we go.
    """
    if not _body_placement_is_identity(body):
        raise UnsupportedFeatureError(
            body.TypeId,
            f"{body.Label} (Body Placement non-identity; not yet supported)",
        )

    units: list[TranslationUnit] = []
    current_var: str | None = None
    for child in body.Group:
        tid = child.TypeId
        if tid in _BODY_INFRASTRUCTURE:
            continue
        if tid == "Sketcher::SketchObject":
            units.extend(translate_sketch(child, ctx))
        elif tid == "PartDesign::Pad":
            unit = _translate_pad(child, ctx)
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
        else:
            raise UnsupportedFeatureError(
                tid,
                f"{child.Label} (feature kind not supported in tier 2/3; "
                f"only Sketch / Pad / Pocket / Revolution / Fillet / Chamfer)",
            )
    return units


# ---------------------------------------------------------------------------
# Pad
# ---------------------------------------------------------------------------


def _translate_pad(pad, ctx: TranslationContext) -> TranslationUnit:
    if str(getattr(pad, "Type", "Length")) != "Length":
        raise UnsupportedFeatureError(
            pad.TypeId,
            f"{pad.Label} (Pad.Type={pad.Type!r}; only 'Length' supported)",
        )
    if bool(getattr(pad, "Midplane", False)):
        raise UnsupportedFeatureError(
            pad.TypeId, f"{pad.Label} (Midplane Pad not yet supported)"
        )

    profile = pad.Profile
    if isinstance(profile, (list, tuple)):
        profile = profile[0]
    sketch_var = profile.Name

    length = float(pad.Length.Value)
    reversed_ = bool(getattr(pad, "Reversed", False))
    amount = -length if reversed_ else length

    unit = TranslationUnit(
        var_name=pad.Name,
        imports={"extrude"},
        lines=[f"{pad.Name} = extrude({sketch_var}, amount={amount})"],
        comment=f"PartDesign::Pad {pad.Label!r}: length={length}"
                + (" (reversed)" if reversed_ else ""),
    )
    ctx.add_step(
        feature_type="pad",
        feature_name=pad.Name,
        depends_on=[sketch_var],
        renamed_from_default=(pad.Label != pad.Name),
        build123d_code=unit.lines[0],
        properties=extract_properties(getattr(pad, "Shape", None)),
    )
    return unit


# ---------------------------------------------------------------------------
# Pocket
# ---------------------------------------------------------------------------


_THROUGH_ALL_LENGTH = 1_000_000.0


def _translate_pocket(
    pocket, base_var: str, ctx: TranslationContext
) -> TranslationUnit:
    pocket_type = str(getattr(pocket, "Type", "Length"))
    if pocket_type not in {"Length", "ThroughAll"}:
        raise UnsupportedFeatureError(
            pocket.TypeId,
            f"{pocket.Label} (Pocket.Type={pocket_type!r}; tier-2 supports "
            f"'Length' and 'ThroughAll' only)",
        )
    if bool(getattr(pocket, "Midplane", False)):
        raise UnsupportedFeatureError(
            pocket.TypeId, f"{pocket.Label} (Midplane Pocket not yet supported)"
        )

    profile = pocket.Profile
    if isinstance(profile, (list, tuple)):
        profile = profile[0]
    sketch_var = profile.Name
    reversed_ = bool(getattr(pocket, "Reversed", False))

    if pocket_type == "ThroughAll":
        length = _THROUGH_ALL_LENGTH
        amount = length if reversed_ else -length
        line = (
            f"{pocket.Name} = {base_var} - "
            f"extrude({sketch_var}, amount={amount})"
        )
        note = "ThroughAll" + (" (reversed)" if reversed_ else "")
    else:
        length = float(pocket.Length.Value)
        amount = length if reversed_ else -length
        line = f"{pocket.Name} = {base_var} - extrude({sketch_var}, amount={amount})"
        note = f"length={length}" + (" (reversed)" if reversed_ else "")

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
        f"Axis(({origin.x}, {origin.y}, {origin.z}), "
        f"({direction.x}, {direction.y}, {direction.z}))"
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
    angle_raw = rev.Angle
    angle = float(angle_raw.Value) if hasattr(angle_raw, "Value") else float(angle_raw)
    reversed_ = bool(getattr(rev, "Reversed", False))
    axis_expr, axis_imports = _axis_expr_from_reference(rev)
    if reversed_:
        angle = -angle

    imports = {"revolve"} | axis_imports

    if base is None:
        line = (
            f"{rev.Name} = revolve({sketch_var}, axis={axis_expr}, "
            f"revolution_arc={angle})"
        )
        depends = [sketch_var]
    else:
        line = (
            f"{rev.Name} = {base} + revolve({sketch_var}, axis={axis_expr}, "
            f"revolution_arc={angle})"
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


def _translate_atomic_revolution(
    rev, ctx: TranslationContext
) -> list[TranslationUnit]:
    """Top-level (non-Body) Revolution. Its sketch is translated separately
    via the Sketcher::SketchObject top-level handler in document order."""
    return [_translate_revolution(rev, base=None, ctx=ctx)]


# ---------------------------------------------------------------------------
# Fillet / Chamfer — tier 3
# ---------------------------------------------------------------------------


def _resolve_edge_midpoints(parent_shape, edge_names) -> list[tuple[float, float, float]]:
    """For each 'Edge<N>' name, return the edge's midpoint in world coords.

    This is where FreeCAD does the work that pure-text parsing of an FCStd
    cannot: it recomputes the BRep and lets us index into it by FreeCAD's own
    internal naming. We then carry the geometric midpoint forward into the
    emitted build123d code, where build123d selects edges by that location.
    """
    midpoints = []
    edges = parent_shape.Edges
    for ename in edge_names:
        if not ename.startswith("Edge"):
            raise UnsupportedFeatureError(
                "PartDesign::Fillet",
                f"edge reference {ename!r} not understood (expected 'Edge<N>')",
            )
        idx = int(ename[len("Edge"):]) - 1
        if idx < 0 or idx >= len(edges):
            raise UnsupportedFeatureError(
                "PartDesign::Fillet",
                f"edge index {idx + 1} out of range (shape has {len(edges)} edges)",
            )
        edge = edges[idx]
        # Edge.valueAt(param) returns the point at parameter t. The midpoint
        # is at (FirstParameter + LastParameter) / 2.
        t = (edge.FirstParameter + edge.LastParameter) / 2.0
        p = edge.valueAt(t)
        midpoints.append((p.x, p.y, p.z))
    return midpoints


def _format_midpoints(midpoints: list[tuple[float, float, float]]) -> str:
    return "[" + ", ".join(f"({x}, {y}, {z})" for x, y, z in midpoints) + "]"


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

    radius = float(getattr(obj, radius_attr).Value)
    midpoints_repr = _format_midpoints(midpoints)
    var = obj.Name

    edge_select_line = (
        f"_{var}_edges = ["
        f"e for e in {base_var}.edges() "
        f"if any((e.position_at(0.5) - Vector(*m)).length < 1e-3 "
        f"for m in {midpoints_repr})]"
    )
    if builder == "fillet":
        result_line = f"{var} = {builder}(_{var}_edges, radius={radius})"
    else:
        result_line = f"{var} = {builder}(_{var}_edges, length={radius})"

    unit = TranslationUnit(
        var_name=var,
        imports={builder, "Vector"},
        lines=[edge_select_line, result_line],
        comment=f"{obj.TypeId} {obj.Label!r}: "
                f"{radius_attr.lower()}={radius} on {len(edge_names)} edges of {parent.Name}",
    )
    ctx.add_step(
        feature_type=feature_type,
        feature_name=obj.Name,
        depends_on=[base_var],
        renamed_from_default=(obj.Label != obj.Name),
        build123d_code="\n".join(unit.lines),
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
# Top-level atomic handlers (Body-less legacy PartDesign + Part-workbench
# equivalents). The Parts Library has many old files where PartDesign features
# live directly in the document without a containing Body.
# ---------------------------------------------------------------------------


def _previous_solid_in_doc(obj) -> str | None:
    """Find the most recent solid-producing object before obj in doc order.

    Body-less PartDesign files don't always set ``BaseFeature`` on Pockets,
    leaving "what does this subtract from?" implicit. The convention is "the
    previous solid in the document" — that's what FreeCAD's old-format
    importer assumes.
    """
    doc = obj.Document
    prev: str | None = None
    for o in doc.Objects:
        if o.Name == obj.Name:
            return prev
        try:
            shape = getattr(o, "Shape", None)
            if shape is not None and not shape.isNull() and shape.Solids:
                prev = o.Name
        except Exception:
            pass
    return None


def _translate_atomic_pad(pad, ctx: TranslationContext) -> list[TranslationUnit]:
    return [_translate_pad(pad, ctx)]


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
        # Body-less Length Pocket: FreeCAD produces just the extruded profile
        # (the prismatic shape), no subtraction. Direction is -normal by
        # default (matching the body-internal "cut into the body" convention).
        length = float(pocket.Length.Value)
        amount = length if reversed_ else -length
        unit = TranslationUnit(
            var_name=pocket.Name,
            imports={"extrude"},
            lines=[f"{pocket.Name} = extrude({sketch_var}, amount={amount})"],
            comment=f"PartDesign::Pocket {pocket.Label!r} (body-less): length={length}"
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
        line = f"{ext.Name} = extrude({base_var}, amount={amount})"
        imports = {"extrude"}
    else:
        line = (
            f"{ext.Name} = extrude({base_var}, amount={amount}, "
            f"dir=({ux}, {uy}, {uz}))"
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
            f"Axis(({base_vec.x}, {base_vec.y}, {base_vec.z}), "
            f"({axis_vec.x}, {axis_vec.y}, {axis_vec.z}))"
        ))
    else:
        axis_expr = (
            f"Axis(({base_vec.x}, {base_vec.y}, {base_vec.z}), "
            f"({axis_vec.x}, {axis_vec.y}, {axis_vec.z}))"
        )

    line = (
        f"{rev.Name} = revolve({source_var}, axis={axis_expr}, "
        f"revolution_arc={angle})"
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


TIER2_HANDLERS = {
    "PartDesign::Body": translate_body,
    # PartDesign features at the document level (no containing Body) —
    # common in older Parts Library files.
    "PartDesign::Pad": _translate_atomic_pad,
    "PartDesign::Pocket": _translate_atomic_pocket,
    "PartDesign::Revolution": _translate_atomic_revolution,
    "PartDesign::Fillet": _translate_atomic_fillet,
    "PartDesign::Chamfer": _translate_atomic_chamfer,
    # Part workbench equivalents.
    "Part::Extrusion": _translate_part_extrusion,
    "Part::Revolution": _translate_part_revolution,
    # Standalone sketch at the document level.
    "Sketcher::SketchObject": translate_sketch,
}
