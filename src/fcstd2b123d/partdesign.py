"""PartDesign feature translators.

Tier-2 scope:
  - PartDesign::Body container — walks Group, chains features.
  - PartDesign::Pad — extrude (with Reversed option).
  - PartDesign::Pocket — subtractive extrude (with Reversed and ThroughAll
    options).
  - PartDesign::Revolution — revolve around X/Y/Z (with Reversed option).
    Handled both inside a Body and as a top-level atomic feature (some
    legacy library files have a free-standing Revolution at document level).

Not in scope: TwoLengths / Midplane Pad/Pocket modes, UpToFace, Up to
arbitrary axis revolutions, Hole, Groove, sweep / loft / helix features.
"""

from __future__ import annotations

from .emitter import TranslationUnit
from .errors import UnsupportedFeatureError
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


def translate_body(body) -> list[TranslationUnit]:
    """Walk a PartDesign::Body and emit units in feature order.

    Subtractive features (Pocket) consume the running body shape; we track
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
            units.extend(translate_sketch(child))
        elif tid == "PartDesign::Pad":
            unit = _translate_pad(child)
            units.append(unit)
            current_var = unit.var_name
        elif tid == "PartDesign::Pocket":
            if current_var is None:
                raise UnsupportedFeatureError(
                    tid,
                    f"{child.Label} (Pocket with no preceding solid in body — "
                    f"unsupported)",
                )
            unit = _translate_pocket(child, current_var)
            units.append(unit)
            current_var = unit.var_name
        elif tid == "PartDesign::Revolution":
            unit = _translate_revolution(child, base=current_var)
            units.append(unit)
            current_var = unit.var_name
        else:
            raise UnsupportedFeatureError(
                tid,
                f"{child.Label} (feature kind not supported in tier-2; "
                f"only Pad, Pocket, Revolution and their sketches)",
            )
    return units


# ---------------------------------------------------------------------------
# Pad
# ---------------------------------------------------------------------------


def _translate_pad(pad) -> TranslationUnit:
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

    return TranslationUnit(
        var_name=pad.Name,
        imports={"extrude"},
        lines=[f"{pad.Name} = extrude({sketch_var}, amount={amount})"],
        comment=f"PartDesign::Pad {pad.Label!r}: length={length}"
                + (" (reversed)" if reversed_ else ""),
    )


# ---------------------------------------------------------------------------
# Pocket
# ---------------------------------------------------------------------------


# A "very large" length for ThroughAll — big enough to span any realistic part
# without being so big it kills OCCT precision. 1e6 mm = 1 km — handles
# everything the Parts Library actually contains.
_THROUGH_ALL_LENGTH = 1_000_000.0


def _translate_pocket(pocket, base_var: str) -> TranslationUnit:
    """A pocket subtracts an extruded profile from the running body.

    Type=Length:     extrude by Length in the sketch's -normal direction
                     (or +normal if Reversed).
    Type=ThroughAll: extrude both ways far enough to cut the entire body,
                     then subtract.
    """
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
        # Cut "through" the body in one direction. FreeCAD's ThroughAll
        # extrudes the profile only along the chosen normal direction
        # (flipped by Reversed); both-directions cutting would over-remove
        # material on bodies that extend on both sides of the sketch plane.
        length = _THROUGH_ALL_LENGTH
        amount = length if reversed_ else -length
        line = (
            f"{pocket.Name} = {base_var} - "
            f"extrude({sketch_var}, amount={amount})"
        )
        note = "ThroughAll" + (" (reversed)" if reversed_ else "")
    else:
        length = float(pocket.Length.Value)
        # Pocket extrudes INTO the body — opposite to the sketch normal.
        # Default direction: -normal → amount=-length. Reversed flips that.
        amount = length if reversed_ else -length
        line = f"{pocket.Name} = {base_var} - extrude({sketch_var}, amount={amount})"
        note = f"length={length}" + (" (reversed)" if reversed_ else "")

    return TranslationUnit(
        var_name=pocket.Name,
        imports={"extrude"},
        lines=[line],
        comment=f"PartDesign::Pocket {pocket.Label!r}: {note}",
    )


# ---------------------------------------------------------------------------
# Revolution
# ---------------------------------------------------------------------------


def _axis_expr_from_reference(rev) -> tuple[str, set[str]]:
    """Map a PartDesign::Revolution.ReferenceAxis to a build123d Axis expression.

    Three supported reference shapes:
    1. A Body's Origin line (X_Axis / Y_Axis / Z_Axis) — use Axis.X/.Y/.Z.
    2. A Sketch (subelement = '' or 'H_Axis'): the sketch's local X axis,
       transformed through the sketch.Placement to world coordinates. When
       that turns out to be one of the world X/Y/Z directions we emit the
       Axis.{X,Y,Z} shortcut; otherwise an explicit Axis((origin), (dir)).
    3. A Sketch with subelement 'V_Axis': the sketch's local Y axis.

    Anything else raises UnsupportedFeatureError.
    """
    ref = rev.ReferenceAxis
    if not ref:
        raise UnsupportedFeatureError(
            rev.TypeId,
            f"{rev.Label} (Revolution has no ReferenceAxis set)",
        )
    obj, subs = ref

    # Body origin axis case.
    name = getattr(obj, "Name", "") or ""
    label = getattr(obj, "Label", "") or ""
    for candidate in (name, label):
        if candidate in _AXIS_OF_ORIGIN:
            return _AXIS_OF_ORIGIN[candidate], {"Axis"}

    # Sketch axis case.
    if getattr(obj, "TypeId", "") == "Sketcher::SketchObject":
        return _sketch_axis_expr(obj, subs, rev)

    raise UnsupportedFeatureError(
        rev.TypeId,
        f"{rev.Label} (ReferenceAxis={name!r}/{label!r} not supported in tier-2)",
    )


def _sketch_axis_expr(sketch, subs, rev) -> tuple[str, set[str]]:
    """Resolve a sketch-internal axis to a build123d Axis expression."""
    import FreeCAD  # lazy

    sub = (subs[0] if subs else "") or ""  # often empty for default
    if sub in ("", "H_Axis"):
        local = FreeCAD.Vector(1, 0, 0)
    elif sub == "V_Axis":
        local = FreeCAD.Vector(0, 1, 0)
    else:
        raise UnsupportedFeatureError(
            rev.TypeId,
            f"{rev.Label} (Sketch axis subelement {sub!r} not supported; "
            f"only '' / 'H_Axis' / 'V_Axis')",
        )

    rot = sketch.Placement.Rotation
    direction = rot.multVec(local)
    origin = sketch.Placement.Base

    # Snap to an axis constant when direction aligns with a world axis.
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
        f"Axis(("
        f"{origin.x}, {origin.y}, {origin.z}), "
        f"({direction.x}, {direction.y}, {direction.z}))"
    ), {"Axis"}


def _translate_revolution(rev, base: str | None) -> TranslationUnit:
    """Translate a PartDesign::Revolution feature.

    Atomic Revolution (base=None): result = revolve(profile, axis, angle).
    Body-internal Revolution after a Pad/Pocket: revolve added to running shape.
    In the v1 Parts Library set, body-internal Revolutions always start the
    body (base remains None at that point), so the additive-only branch is
    sufficient for now.
    """
    if str(getattr(rev, "Type", "Angle")) != "Angle":
        raise UnsupportedFeatureError(
            rev.TypeId, f"{rev.Label} (Revolution.Type={rev.Type!r}; only 'Angle' supported)"
        )
    if bool(getattr(rev, "Midplane", False)):
        raise UnsupportedFeatureError(
            rev.TypeId, f"{rev.Label} (Midplane Revolution not yet supported)"
        )

    profile = rev.Profile
    if isinstance(profile, (list, tuple)):
        profile = profile[0]
    sketch_var = profile.Name
    angle = float(rev.Angle.Value) if hasattr(rev.Angle, "Value") else float(rev.Angle)
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
    else:
        line = (
            f"{rev.Name} = {base} + revolve({sketch_var}, axis={axis_expr}, "
            f"revolution_arc={angle})"
        )

    return TranslationUnit(
        var_name=rev.Name,
        imports=imports,
        lines=[line],
        comment=f"PartDesign::Revolution {rev.Label!r}: angle={angle}"
                + (" (reversed)" if reversed_ else ""),
    )


def _translate_atomic_revolution(rev) -> list[TranslationUnit]:
    """Top-level (non-Body) Revolution. Its sketch is translated separately
    via the Sketcher::SketchObject top-level handler in document order."""
    return [_translate_revolution(rev, base=None)]


TIER2_HANDLERS = {
    "PartDesign::Body": translate_body,
    "PartDesign::Revolution": _translate_atomic_revolution,
    # Standalone sketch at the document level (rare — most sketches live in
    # a Body and are reached via translate_body). Used by legacy files like
    # tapon.FCStd where a free-standing Revolution references a top-level Sketch.
    "Sketcher::SketchObject": translate_sketch,
}
