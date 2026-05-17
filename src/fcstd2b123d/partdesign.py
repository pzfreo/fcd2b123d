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
from .sketch import _plane_expr, translate_sketch


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

    When ``ctx.style == "builder"``, the body's feature chain is post-
    processed into a single ``with BuildPart() as <body_var>:`` block —
    see :func:`_body_to_builder` for the transformation rules.
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
        elif tid == "PartDesign::Hole":
            if current_var is None:
                raise UnsupportedFeatureError(
                    tid,
                    f"{child.Label} (Hole with no preceding solid in body)",
                )
            unit = _translate_hole(child, current_var, ctx)
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
        elif tid == "PartDesign::Draft":
            if current_var is None:
                raise UnsupportedFeatureError(
                    tid,
                    f"{child.Label} (Draft with no preceding solid in body)",
                )
            unit = _translate_draft(child, current_var, ctx)
            units.append(unit)
            current_var = unit.var_name
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
            # Algebra-mode polar absorption: when this is a single-Original
            # PolarPattern over a Pocket whose sketch is a simple offset
            # Circle, merge Pocket + Pattern into a single
            # ``Pattern = <pre_pocket> - PolarLocations(R, N) * extrude(
            # at-origin Circle, A)`` line and drop the now-unused Pocket
            # and offset-sketch units. The visible improvement is the same
            # one builder-mode gets via _move_offset_to_polar (#110), but
            # for the default algebra emit.
            if tid == "PartDesign::PolarPattern":
                units, current_var = _try_absorb_polar_algebra(
                    child, units, current_var
                )
            elif tid == "PartDesign::LinearPattern":
                units, current_var = _try_absorb_linear_algebra(
                    child, units, current_var
                )
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
                    label=body.Label,
                    imports=extra_imports,
                    lines=[f"{placed_var} = {expr}"],
                    comment=comment,
                )
            )

    if getattr(ctx, "style", "algebra") == "builder":
        units = _body_to_builder(units, body, current_var)
    return units


# ---------------------------------------------------------------------------
# Algebra-mode polar absorption (#101 algebra-side equivalent)
# ---------------------------------------------------------------------------
#
# When a single-Original PolarPattern follows a Pocket whose sketch is a
# simple offset Circle, the two-line algebra-mode emit can be collapsed:
#
#   hole = Pos(18, 0) * Circle(2)
#   pocket = pad - extrude(hole, amount=12)
#   polar = pocket - PolarLocations(0, 5, start_angle=60, angular_range=300) * extrude(hole, amount=12)
#
# becomes:
#
#   polar = pad - PolarLocations(18, 6) * extrude(Sketch() + Circle(2), amount=12)
#
# The ``hole`` sketch and ``pocket`` lines are dropped. ``pad`` (the pre-
# Pocket base) becomes the absorbed expression's base.


import re as _re_alg  # extra alias so the late helpers don't collide

_ALG_POCKET_LINE_RE = _re_alg.compile(
    r"^(\w+)\s*=\s*(\w+)\s*-\s*extrude\(\s*(\w+)\s*,\s*amount=([^)]+?)\s*\)$"
)
_ALG_PATTERN_LINE_RE = _re_alg.compile(
    r"^(\w+)\s*=\s*(\w+)\s*-\s*PolarLocations\(\s*"
    r"0,\s*(\d+),\s*start_angle=([^,]+?)\s*,\s*angular_range=([^)]+?)\s*\)"
    r"\s*\*\s*extrude\(\s*(\w+)\s*,\s*amount=([^)]+?)\s*\)$"
)


def _try_absorb_polar_algebra(
    pattern_obj, units: list, current_var: str
) -> tuple[list, str]:
    """Try to collapse Pocket + PolarPattern into a single absorbed line.

    Operates on the in-progress ``units`` list. If the last unit is a
    polar-pattern line matching the absorbable shape, and the second-
    to-last unit is the corresponding Pocket, and the sketch referenced
    by both is a simple ``Pos(R, 0) * Circle(r)`` form, then:

    * Pop the Pocket unit.
    * Pop the sketch unit (if it's an offset-Circle, indicating it was
      used solely for the absorbed pattern).
    * Rewrite the pattern unit's line to the absorbed form.

    Falls through silently when conditions aren't met — patterns over
    non-circle sketches or with multiple Originals keep the existing
    emit.
    """
    if len(units) < 2:
        return units, current_var
    pat_unit = units[-1]
    pocket_unit = units[-2]
    if not pat_unit.lines or not pocket_unit.lines:
        return units, current_var

    m_pat = _ALG_PATTERN_LINE_RE.match(pat_unit.lines[0].strip())
    if m_pat is None:
        return units, current_var
    pat_var, pat_base, extra_count, start_angle, angular_range, pat_sk, pat_amount = (
        m_pat.group(1), m_pat.group(2), int(m_pat.group(3)),
        m_pat.group(4).strip(), m_pat.group(5).strip(),
        m_pat.group(6), m_pat.group(7).strip(),
    )

    m_poc = _ALG_POCKET_LINE_RE.match(pocket_unit.lines[0].strip())
    if m_poc is None:
        return units, current_var
    poc_var, poc_base, poc_sk, poc_amount = (
        m_poc.group(1), m_poc.group(2), m_poc.group(3), m_poc.group(4).strip(),
    )

    # Continuity checks: the pattern subtracts from the pocket, and both
    # use the same sketch + amount.
    if (
        pat_base != poc_var
        or pat_sk != poc_sk
        or pat_amount != poc_amount
    ):
        return units, current_var

    # Uniformity check: this is a "skip the first" form. ``start_angle``
    # equals the step, ``angular_range`` equals extra * step,
    # step = 360 / (extra + 1).
    total = extra_count + 1
    try:
        sa = float(start_angle)
        ar = float(angular_range)
    except ValueError:
        return units, current_var
    expected_step = 360.0 / total
    if (
        abs(sa - expected_step) > 1e-6
        or abs(ar - extra_count * expected_step) > 1e-6
    ):
        return units, current_var

    # Find the sketch unit by var_name and parse it for offset + radius.
    sk_unit = None
    sk_idx = -1
    for i in range(len(units) - 3, -1, -1):
        if units[i].var_name == poc_sk:
            sk_unit = units[i]
            sk_idx = i
            break
    if sk_unit is None:
        return units, current_var
    extracted = _extract_offset_circle_sketch(sk_unit)
    if extracted is None:
        return units, current_var
    R, r = extracted

    # Build the absorbed pattern unit.
    absorbed_line = (
        f"{pat_var} = {poc_base} - "
        f"PolarLocations({format_value(R)}, {total}) "
        f"* extrude(Sketch() + Circle({format_value(r)}), amount={pat_amount})"
    )
    new_imports = set(pat_unit.imports) | {"Sketch", "Circle"}
    new_unit = TranslationUnit(
        var_name=pat_var,
        label=pat_unit.label,
        imports=new_imports,
        lines=[absorbed_line],
        comment=f"{pat_unit.comment} (absorbed Pocket+Pattern)",
        helpers=pat_unit.helpers,
    )

    # Build a new units list: drop the pocket unit and the sketch unit,
    # replace the pattern unit.
    new_units = list(units[:-2])     # everything before Pocket and Pattern
    # Drop the sketch unit from earlier in the list.
    new_units = [u for i, u in enumerate(new_units) if i != sk_idx]
    new_units.append(new_unit)

    return new_units, pat_var


_ALG_LINEAR_LINE_RE = _re_alg.compile(
    r"^(\w+)\s*=\s*(\w+)\s*-\s*Locations\((.*)\)\s*\*\s*extrude\(\s*(\w+)\s*,\s*amount=([^)]+?)\s*\)$"
)


def _try_absorb_linear_algebra(
    pattern_obj, units: list, current_var: str
) -> tuple[list, str]:
    """Mirror of ``_try_absorb_polar_algebra`` for LinearPattern.

    Recognises the uniform-1D form ``Locations((dx, 0, 0), (2dx, 0, 0),
    ..., (N·dx, 0, 0))`` and absorbs the Pocket+Pattern into a single
    ``GridLocations(dx, 0, N+1, 1)`` line when the original sketch's
    offset matches the centered alignment (``R == -(N)·dx/2``). For
    off-centered originals, falls through with the current form.
    """
    if len(units) < 2:
        return units, current_var
    pat_unit = units[-1]
    pocket_unit = units[-2]
    if not pat_unit.lines or not pocket_unit.lines:
        return units, current_var

    m_pat = _ALG_LINEAR_LINE_RE.match(pat_unit.lines[0].strip())
    if m_pat is None:
        return units, current_var
    pat_var = m_pat.group(1)
    pat_base = m_pat.group(2)
    positions_str = m_pat.group(3)
    pat_sk = m_pat.group(4)
    pat_amount = m_pat.group(5).strip()

    m_poc = _ALG_POCKET_LINE_RE.match(pocket_unit.lines[0].strip())
    if m_poc is None:
        return units, current_var
    poc_var = m_poc.group(1)
    poc_base = m_poc.group(2)
    poc_sk = m_poc.group(3)
    poc_amount = m_poc.group(4).strip()

    if pat_base != poc_var or pat_sk != poc_sk or pat_amount != poc_amount:
        return units, current_var

    # Parse uniform-X positions from the Locations call.
    step_dx = _detect_uniform_linear_step_algebra(positions_str)
    if step_dx is None:
        return units, current_var
    args = _split_top_level_args(positions_str)
    extra_count = len(args)
    total = extra_count + 1

    # Sketch must be offset Circle.
    sk_unit = None
    sk_idx = -1
    for i in range(len(units) - 3, -1, -1):
        if units[i].var_name == poc_sk:
            sk_unit = units[i]
            sk_idx = i
            break
    if sk_unit is None:
        return units, current_var
    extracted = _extract_offset_circle_sketch(sk_unit)
    if extracted is None:
        return units, current_var
    R, r = extracted

    # Centered-alignment check: GridLocations(dx, 0, N, 1) defaults to
    # CENTER and places N copies at -(N-1)·dx/2, ..., (N-1)·dx/2. The
    # absorbed form fits iff the original's offset is at the leftmost
    # such position (R == -(N-1)·dx/2). Off-centered originals could be
    # absorbed with explicit alignment, but the common library case is
    # exactly this centered form.
    expected_R = -(total - 1) * step_dx / 2
    if abs(R - expected_R) > 1e-6 * max(1.0, abs(R)):
        return units, current_var

    absorbed_line = (
        f"{pat_var} = {poc_base} - "
        f"GridLocations({format_value(step_dx)}, 0, {total}, 1) "
        f"* extrude(Sketch() + Circle({format_value(r)}), amount={pat_amount})"
    )
    new_imports = (set(pat_unit.imports) | {"Sketch", "Circle", "GridLocations"})
    new_imports.discard("Locations")
    new_unit = TranslationUnit(
        var_name=pat_var,
        label=pat_unit.label,
        imports=new_imports,
        lines=[absorbed_line],
        comment=f"{pat_unit.comment} (absorbed Pocket+Pattern)",
        helpers=pat_unit.helpers,
    )

    new_units = list(units[:-2])
    new_units = [u for i, u in enumerate(new_units) if i != sk_idx]
    new_units.append(new_unit)
    return new_units, pat_var


def _detect_uniform_linear_step_algebra(positions_str: str) -> float | None:
    """Parse ``(dx, 0, 0), (2dx, 0, 0), (3dx, 0, 0)`` style positions
    from an algebra-mode Locations call. Returns dx if uniform, else
    None (also rejects positions with non-zero y or z).
    """
    args = _split_top_level_args(positions_str)
    if not args:
        return None
    xs: list[float] = []
    for a in args:
        a = a.strip()
        if not (a.startswith("(") and a.endswith(")")):
            return None
        coords = _split_top_level_args(a[1:-1])
        if len(coords) != 3:
            return None
        try:
            x = float(coords[0].strip())
            y = float(coords[1].strip())
            z = float(coords[2].strip())
        except ValueError:
            return None
        if abs(y) > 1e-9 or abs(z) > 1e-9:
            return None
        xs.append(x)
    step = xs[0]
    if abs(step) < 1e-9:
        return None
    for i, x in enumerate(xs, start=1):
        if abs(x - i * step) > 1e-6 * max(1.0, abs(i * step)):
            return None
    return step


# ---------------------------------------------------------------------------
# Builder-mode body transformation (#78 phase 2)
# ---------------------------------------------------------------------------

_BODY_FEATURE_TYPES = {
    "pad", "pocket", "revolution", "groove", "hole",
    "fillet", "chamfer", "draft", "pattern",
}

# Regex patterns used to recognise the shape of each algebra-mode body line.
# These are not as fragile as they look — the emit format is fixed by the
# corresponding handler in this module, and the regression tests catch any
# drift between handler and matcher.
import re as _re

_PAD_FIRST_RE = _re.compile(r"^(\w+)\s*=\s*(extrude\(.*\))$", _re.DOTALL)
_REVOL_FIRST_RE = _re.compile(r"^(\w+)\s*=\s*(revolve\(.*\))$", _re.DOTALL)
_CHAINED_ADD_RE = _re.compile(r"^(\w+)\s*=\s*(\w+)\s*\+\s*(.*)$", _re.DOTALL)
_CHAINED_SUB_RE = _re.compile(r"^(\w+)\s*=\s*(\w+)\s*-\s*(.*)$", _re.DOTALL)
_DRESSUP_RE = _re.compile(
    r"^(\w+)\s*=\s*((?:fillet|chamfer|draft)\(.*\))$", _re.DOTALL
)


def _body_to_builder(
    units: list[TranslationUnit], body, final_var: str | None
) -> list[TranslationUnit]:
    """Post-process a body's algebra-mode units into builder-mode output.

    Algebra mode produces SSA-style ``pad = ...; pocket = pad - ...;
    fillet_0 = fillet(...);`` per feature, each rebinding the whole running
    solid. Builder mode collapses the chain into a single

    .. code-block:: python

        with BuildPart() as <body>:
            extrude(...)
            extrude(..., mode=Mode.SUBTRACT)
            fillet(_edges_at(<body>.part, ...), radius=...)
        result = <body>.part

    block. The visible improvement is dropping the ``pad / pocket /
    fillet_NNN`` cascade — bd_warehouse-style.

    Sketch units (the ones emitting ``var = make_face(...)`` or the
    builder-style ``with BuildSketch(...)`` blocks from phase 1) stay
    outside the BuildPart context so they're available as variables
    referenced from inside.

    Limitations of this phase (2a):

    * Pattern features stay as-is — they still emit the algebra-style
      ``add(PolarLocations(...) * extrude(...), mode=Mode.SUBTRACT)``.
      Phase 2b will merge them into ``with PolarLocations(R, N):``
      contexts (issues #101 / #102).
    * If the body's final feature isn't translatable to a BuildPart op
      (e.g., the body's placement wrapper at the end), the whole body
      falls back to algebra-mode emit.
    """
    if final_var is None:
        return units

    # Split units: sketches stay outside, body features go inside the context.
    sketch_units: list[TranslationUnit] = []
    body_units: list[TranslationUnit] = []
    placed_unit: TranslationUnit | None = None
    for u in units:
        if u.var_name == f"{body.Name}_placed":
            placed_unit = u
        elif _is_body_feature_unit(u):
            body_units.append(u)
        else:
            sketch_units.append(u)

    if not body_units:
        return units

    # Builder-mode lines for the body chain. If any unit doesn't translate
    # cleanly, abort and return original units unchanged — algebra-mode
    # fallback is safer than emitting subtly wrong code.
    # If any body unit references a context-aware sketch primitive inline,
    # we can't safely wrap in BuildPart — bail and emit algebra mode for
    # this whole body. The hoisting refactor needed to make those safe is
    # phase-2b work.
    if any(_has_inline_sketch_primitive(u.lines[0]) for u in body_units):
        return units

    inside_lines: list[str] = []
    inside_imports: set[str] = set()
    inside_helpers: set[str] = set()
    for u in body_units:
        translated = _to_builder_lines(u, body.Name)
        if translated is None:
            return units  # bail; emit algebra-mode for this body
        new_lines, new_imports = translated
        for nl in new_lines:
            if u.comment and nl == new_lines[0]:
                inside_lines.append(f"# {u.comment}")
            inside_lines.append(nl)
        # Preserve the original unit's imports — extrude/revolve/fillet/
        # chamfer/_pattern_union/etc. are still referenced in the
        # converted lines, so they need to import the same names.
        inside_imports.update(u.imports)
        inside_imports.update(new_imports)
        # Carry helpers only if the rewritten lines actually still call
        # them. The Mirror builder-mode rewrite (issue #117) drops the
        # ``_pattern_union`` wrapper, so dragging that helper along would
        # emit a dead definition.
        joined = "\n".join(new_lines)
        for h in u.helpers:
            if h in joined:
                inside_helpers.add(h)

    # Phase 2b: hoist pattern prisms out of the BuildPart context.
    # Inside a BuildPart, ``extrude(<sketch>, ...)`` has a side effect —
    # it adds the produced prism to the running part. That corrupts
    # ``add(<Locations> * extrude(<sk>, A), mode=SUBTRACT)`` expressions
    # because the extrude both adds *and* gets multiplied for subtraction.
    # The fix: pre-compute the prism in a module-level variable, then
    # iterate the multiplied list inside the BuildPart.
    inside_lines, hoisted_prism_units, extra_imports, absorbed_sketches = (
        _hoist_pattern_prisms(inside_lines, body_units, sketch_units)
    )
    inside_imports |= extra_imports
    # Drop sketches that polar absorption replaced with an inline
    # at-origin form — they're no longer referenced anywhere.
    sketch_units = [s for s in sketch_units if s.var_name not in absorbed_sketches]
    sketch_units = list(sketch_units) + hoisted_prism_units

    body_var = body.Name
    header = f"with BuildPart() as {body_var}:"
    body_block_lines = [header] + ["    " + line for line in inside_lines]
    # After the context, expose ``<body>.part`` as the running variable
    # so any downstream code (pattern absorption, placement wrap, etc.)
    # still references ``<body>``.
    body_block_lines.append(f"{body_var} = {body_var}.part")

    combined_imports = {"BuildPart"} | inside_imports
    if any("Mode.SUBTRACT" in line for line in inside_lines):
        combined_imports.add("Mode")

    builder_unit = TranslationUnit(
        var_name=body_var,
        label=body.Label,
        imports=combined_imports,
        lines=body_block_lines,
        comment=f"PartDesign::Body {body.Label!r} (builder mode, {len(body_units)} features)",
        helpers=inside_helpers,
    )

    out = list(sketch_units) + [builder_unit]
    if placed_unit is not None:
        # The placement wrap was built when ``current_var`` was the last
        # feature's name (e.g. ``Pad``). After the builder transform that
        # variable no longer exists — the running solid is the body's
        # rebound ``<body_var>``. Rewrite the wrap to reference the new
        # name.
        if final_var and final_var != body.Name:
            placed_unit = TranslationUnit(
                var_name=placed_unit.var_name,
                label=placed_unit.label,
                imports=placed_unit.imports,
                lines=[
                    _re.sub(rf"\b{_re.escape(final_var)}\b", body.Name, line)
                    for line in placed_unit.lines
                ],
                comment=placed_unit.comment,
                helpers=placed_unit.helpers,
            )
        out.append(placed_unit)
    return out


def _is_body_feature_unit(u: TranslationUnit) -> bool:
    """Heuristic: a body-feature unit is a single-line algebra assignment
    of one of the known body-chain shapes. Multi-line units (sketches with
    pre-lines, BuildSketch blocks) are NOT body features.

    The shapes we recognise:

    * ``<var> = extrude(...)`` — first Pad
    * ``<var> = revolve(...)`` — first Revolution
    * ``<var> = <base> + <rhs>`` — additive chain (Pad / Revolution / etc.)
    * ``<var> = <base> - <rhs>`` — subtractive chain (Pocket / Pattern-via-Locations)
    * ``<var> = fillet(...) | chamfer(...) | draft(...)`` — dressup
    * ``<var> = _pattern_union(<base>, ...)`` — Mirror / additive pattern

    Without the ``_pattern_union`` line, the Mirror feature's emit slips
    through the body-unit filter, gets treated as a sketch-level unit,
    and ends up outside the BuildPart context — but its expression
    references a body-internal variable that no longer exists by name.
    Recognising it here lets ``_to_builder_lines`` return None for the
    line shape, bailing the whole body to algebra mode cleanly.
    """
    if len(u.lines) != 1:
        return False
    line = u.lines[0]
    if (
        _PAD_FIRST_RE.match(line)
        or _REVOL_FIRST_RE.match(line)
        or _CHAINED_ADD_RE.match(line)
        or _CHAINED_SUB_RE.match(line)
        or _DRESSUP_RE.match(line)
        or _PATTERN_UNION_RE.match(line)
    ):
        return True
    return False


# ``<var> = _pattern_union(<base>, <copy1>, <copy2>, ...)`` — the Mirror
# feature and additive patterns (LinearPattern adding copies of a Pad)
# emit through this helper because chained ``+`` on Part objects produces
# a Compound rather than a fused solid.
_PATTERN_UNION_RE = _re.compile(
    r"^(\w+)\s*=\s*_pattern_union\(.*\)$", _re.DOTALL
)


# Build123d sketch primitives are context-aware: calling them inside a
# BuildPart context raises ``BuildPart doesn't have a Circle object or
# operation``. Body units whose lines reference these inline (typically
# Hole's drill profile constructed as ``Sketch() + Circle(...)``) can't
# be safely wrapped in a BuildPart block without first hoisting the
# expression — a refactor deferred to phase 2b. For now, detect and
# fall back to algebra mode for the whole body.
_CONTEXT_AWARE_SKETCH_RE = _re.compile(
    r"\b(Circle|Rectangle|RegularPolygon|Ellipse|Polygon|Trapezoid|SlotArc|SlotOverall)\("
)


def _has_inline_sketch_primitive(line: str) -> bool:
    return bool(_CONTEXT_AWARE_SKETCH_RE.search(line))


# ---------------------------------------------------------------------------
# Phase 2b: Hoist pattern prisms (#101 / #102)
# ---------------------------------------------------------------------------
#
# Background: build123d's ``extrude(<sketch>, ...)`` inside a BuildPart
# context has a *side effect* — it adds the produced prism to the running
# part (Mode.ADD by default). The phase-2a emit for a pattern,
#
#     add(PolarLocations(...) * extrude(<sk>, A), mode=Mode.SUBTRACT)
#
# evaluates ``extrude(<sk>, A)`` while inside the context, so the prism
# is added once *and* then the multiplied list is subtracted —
# producing the wrong number of carves.
#
# Fix: pre-compute the prism in a module-level variable, then iterate the
# multiplied list inside the BuildPart:
#
#     <sk>_prism = extrude(<sk>, A)              # module level, no side effect
#     with BuildPart() as body:
#         ...
#         for s in PolarLocations(...) * <sk>_prism:
#             add(s, mode=Mode.SUBTRACT)
#
# Detects these patterns in the converted ``inside_lines`` and rewrites
# them. Pattern-free lines pass through unchanged.

_LOCATIONS_NAMES = ("PolarLocations", "GridLocations", "Locations")


def _parse_pattern_line(line: str) -> tuple[str, str, str, str] | None:
    """Parse a line of the form ``add(<Locations>(...) * extrude(<sk>, amount=A), mode=Mode.SUBTRACT)``.

    Returns ``(locations_expr, sketch_var, amount, mode_suffix)`` if matched,
    else None. Handles nested parens in the Locations call (e.g.
    ``Locations((14.66, 0, 0), (29.33, 0, 0))``) which a naive regex can't.

    ``mode_suffix`` is the literal text of the ``mode=Mode.SUBTRACT`` arg
    (currently always ``"Mode.SUBTRACT"`` — only subtract patterns hit
    this path).
    """
    s = line.strip()
    if not (s.startswith("add(") and s.endswith(")")):
        return None
    inner = s[len("add("):-1]
    # Split top-level args of add(): expect 2 args (the shape expr + mode kwarg).
    args = _split_top_level_args(inner)
    if len(args) != 2:
        return None
    shape_expr = args[0].strip()
    mode_arg = args[1].strip()
    if not mode_arg.startswith("mode="):
        return None
    mode_val = mode_arg[len("mode="):].strip()
    if mode_val != "Mode.SUBTRACT":
        return None

    # shape_expr should be "<Locations>(...) * extrude(<sk>, amount=A)".
    # Find the top-level "*" splitting Locations from extrude.
    depth = 0
    star_idx = -1
    for i, ch in enumerate(shape_expr):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "*" and depth == 0:
            star_idx = i
            break
    if star_idx < 0:
        return None
    left = shape_expr[:star_idx].strip()
    right = shape_expr[star_idx + 1:].strip()
    if not any(left.startswith(name + "(") for name in _LOCATIONS_NAMES):
        return None
    if not left.endswith(")"):
        return None
    if not (right.startswith("extrude(") and right.endswith(")")):
        return None

    # Parse extrude(<sk>, amount=A) — args of extrude.
    extrude_inner = right[len("extrude("):-1]
    ex_args = _split_top_level_args(extrude_inner)
    if len(ex_args) != 2:
        return None
    sketch_var = ex_args[0].strip()
    amount_arg = ex_args[1].strip()
    if not amount_arg.startswith("amount="):
        return None
    amount = amount_arg[len("amount="):].strip()
    return left, sketch_var, amount, mode_val


def _hoist_pattern_prisms(
    inside_lines: list[str],
    body_units: list[TranslationUnit],
    sketch_units: list[TranslationUnit],
) -> tuple[list[str], list[TranslationUnit], set[str], set[str]]:
    """Rewrite pattern lines to use a hoisted prism. See module-level
    comment block above for the why.

    Also runs polar-absorption as a post-pass: when a Pocket
    immediately precedes a polar for-loop on the same prism, the two
    are collapsed into a single ``PolarLocations(0, N+1) * prism``
    loop that covers all positions.

    Finally, when polar absorption succeeds AND the underlying sketch
    is a single offset-Circle (``Sketch + Pos(R, 0) * Circle(r)``), the
    offset is moved from the sketch into ``PolarLocations(R, N+1)`` and
    the hoisted prism uses the at-origin Circle directly. The original
    BuildSketch becomes redundant and is returned in ``absorbed_sketches``
    so the caller can drop it.

    Returns: ``(rewritten_lines, hoisted_units, extra_imports,
    absorbed_sketch_names)``.
    """
    new_lines: list[str] = []
    hoisted: list[TranslationUnit] = []
    hoisted_names: set[str] = set()
    extra_imports: set[str] = set()

    for line in inside_lines:
        parsed = _parse_pattern_line(line)
        if parsed is None:
            new_lines.append(line)
            continue
        locations_expr, sketch_var, amount, _mode = parsed

        prism_var = f"{sketch_var}_prism"
        if prism_var not in hoisted_names:
            hoisted_names.add(prism_var)
            hoisted.append(
                TranslationUnit(
                    var_name=prism_var,
                    label=prism_var,
                    imports={"extrude"},
                    lines=[f"{prism_var} = extrude({sketch_var}, amount={amount})"],
                    comment=(
                        f"Prism for ``{sketch_var}`` hoisted out of BuildPart "
                        f"so it can be multiplied by the pattern locations "
                        f"without triggering extrude's in-context side effect."
                    ),
                )
            )
            extra_imports.add("extrude")

        new_lines.append(f"for s in {locations_expr} * {prism_var}:")
        new_lines.append(f"    add(s, mode=Mode.SUBTRACT)")
        extra_imports.add("add")

    new_lines = _absorb_polar_original(new_lines)

    # Phase-2b polish: drop the offset BuildSketch when polar absorption
    # produced a ``PolarLocations(0, N) * <sk>_prism`` loop AND the
    # underlying sketch is a simple offset Circle. The offset moves
    # into PolarLocations.
    new_lines, dropped_sketches = _move_offset_to_polar(
        new_lines, hoisted, sketch_units
    )
    if dropped_sketches:
        extra_imports.add("Sketch")
        extra_imports.add("Circle")

    return new_lines, hoisted, extra_imports, dropped_sketches


# Match a polar for-loop after the first absorption pass — at this point
# absorbed loops use ``PolarLocations(0, N) * <prism>`` form.
_ABSORBED_POLAR_RE = _re.compile(
    r"^for s in PolarLocations\(\s*0\s*,\s*(\d+)\s*\)\s*\*\s*(\w+):\s*$"
)


def _move_offset_to_polar(
    new_lines: list[str],
    hoisted: list[TranslationUnit],
    sketch_units: list[TranslationUnit],
) -> tuple[list[str], set[str]]:
    """When an absorbed-polar for-loop's prism comes from a sketch that's
    just ``Sketch() + Pos(R, 0) * Circle(r)`` (or builder-mode
    equivalent), move ``R`` into ``PolarLocations(R, N)`` and rewrite the
    prism to extrude an at-origin Circle directly. The original sketch
    is no longer referenced.

    Returns: ``(rewritten_lines, dropped_sketch_names)``.
    """
    sk_by_name = {su.var_name: su for su in sketch_units}
    prism_by_name = {pu.var_name: pu for pu in hoisted}
    dropped: set[str] = set()
    out: list[str] = []

    for line in new_lines:
        m = _ABSORBED_POLAR_RE.match(line)
        if m is None:
            out.append(line)
            continue
        count = int(m.group(1))
        prism_var = m.group(2)
        prism_unit = prism_by_name.get(prism_var)
        if prism_unit is None:
            out.append(line)
            continue
        # Extract the source sketch name from the prism line:
        # "<prism_var> = extrude(<src_sketch>, amount=A)"
        prism_line = prism_unit.lines[0]
        prism_m = _re.match(
            r"^(\w+) = extrude\((\w+),\s*amount=(.+?)\)$", prism_line
        )
        if prism_m is None:
            out.append(line)
            continue
        src_sk = prism_m.group(2)
        amount = prism_m.group(3).strip()
        sk_unit = sk_by_name.get(src_sk)
        if sk_unit is None:
            out.append(line)
            continue
        extracted = _extract_offset_circle_sketch(sk_unit)
        if extracted is None:
            out.append(line)
            continue
        R, r = extracted
        # Rewrite the prism unit's lines to use at-origin Circle.
        prism_unit.lines[0] = (
            f"{prism_var} = extrude(Sketch() + Circle({format_value(r)}), "
            f"amount={amount})"
        )
        prism_unit.imports = set(prism_unit.imports) | {"Sketch", "Circle"}
        # Rewrite the loop to use PolarLocations(R, N).
        out.append(f"for s in PolarLocations({format_value(R)}, {count}) * {prism_var}:")
        dropped.add(src_sk)

    return out, dropped


def _extract_offset_circle_sketch(
    sketch_unit: TranslationUnit,
) -> tuple[float, float] | None:
    """Recognise a sketch unit emitted as either:

    * algebra mode: a single ``<var> = Pos(R, 0) * Circle(r)`` line
    * builder mode: a 4-line ``with BuildSketch() ... with Locations((R, 0)):
      Circle(r); <var> = <var>.sketch`` block

    Returns ``(R, r)`` or None.
    """
    lines = [ln for ln in sketch_unit.lines if ln.strip()]
    # Algebra single-line form.
    if len(lines) == 1:
        m = _re.match(
            r"^(\w+)\s*=\s*\(?\s*Pos\(\s*([^,]+?)\s*,\s*0(?:\.0?)?\s*\)\s*\*\s*Circle\(\s*([^)]+?)\s*\)\s*\)?\s*$",
            lines[0],
        )
        if m is None:
            return None
        try:
            return float(m.group(2)), float(m.group(3))
        except ValueError:
            return None
    # Builder-mode 4-line form.
    if len(lines) == 4:
        try:
            R = float(_re.match(
                r"^\s*with Locations\(\(\s*([^,]+?)\s*,\s*0(?:\.0?)?\s*\)\):\s*$",
                lines[1],
            ).group(1))
            r = float(_re.match(
                r"^\s*Circle\(\s*([^)]+?)\s*\)\s*$", lines[2]
            ).group(1))
            return R, r
        except (AttributeError, ValueError):
            return None
    return None


# Match an in-context Pocket extrude (the original carve before a Pattern).
_EXTRUDE_SUBTRACT_RE = _re.compile(
    r"^\s*extrude\(\s*(\w+)\s*,\s*amount=([^,]+?)\s*,\s*mode=Mode\.SUBTRACT\s*\)\s*$"
)


# Match a polar for-loop line emitted by ``_hoist_pattern_prisms`` so we
# can detect ``Pocket + Pattern`` pairs for absorption.
_POLAR_FOR_LOOP_RE = _re.compile(
    r"^for s in PolarLocations\(\s*"
    r"0\s*,\s*(\d+)\s*,\s*"
    r"start_angle=([^,]+?)\s*,\s*"
    r"angular_range=([^)]+?)\s*"
    r"\)\s*\*\s*(\w+):\s*$"
)


def _absorb_polar_original(lines: list[str]) -> list[str]:
    """Collapse ``Pocket + polar for-loop`` pairs into a single absorbed
    polar for-loop covering all positions.

    Pre-absorption (after the hoist pass):

    ::

        extrude(<sk>, amount=A, mode=Mode.SUBTRACT)              # original Pocket at angle 0
        for s in PolarLocations(0, N, start_angle=θ, angular_range=R) * <sk>_prism:
            add(s, mode=Mode.SUBTRACT)                            # 5 more rotated copies

    Post-absorption (single loop covering N+1 positions at angles
    0, step, 2·step, …, N·step):

    ::

        for s in PolarLocations(0, N+1) * <sk>_prism:
            add(s, mode=Mode.SUBTRACT)

    Validity check: ``start_angle ≈ step`` and ``angular_range ≈ N·step``
    where ``step = 360/(N+1)``. Only collapses when both hold — refuses
    to absorb partial / off-step patterns.
    """
    out: list[str] = []
    i = 0
    while i < len(lines):
        prev = lines[i]
        m_extrude = _EXTRUDE_SUBTRACT_RE.match(prev)
        if m_extrude is None or i + 1 >= len(lines):
            out.append(prev)
            i += 1
            continue
        sk_var, prev_amount = m_extrude.group(1), m_extrude.group(2).strip()
        # Skip any interleaved comment lines.
        j = i + 1
        while j < len(lines) and lines[j].lstrip().startswith("#"):
            j += 1
        if j + 1 >= len(lines):
            out.append(prev)
            i += 1
            continue
        loop_header = lines[j]
        loop_body = lines[j + 1]
        m_polar = _POLAR_FOR_LOOP_RE.match(loop_header)
        if m_polar is None:
            out.append(prev)
            i += 1
            continue
        try:
            extra = int(m_polar.group(1))
            start_angle = float(m_polar.group(2).strip())
            angular_range = float(m_polar.group(3).strip())
        except ValueError:
            out.append(prev)
            i += 1
            continue
        prism_var = m_polar.group(4)
        if prism_var != f"{sk_var}_prism":
            out.append(prev)
            i += 1
            continue
        # Validate it's a uniform "skip the first" form.
        total = extra + 1
        expected_step = 360 / total
        if (
            abs(start_angle - expected_step) > 1e-6
            or abs(angular_range - extra * expected_step) > 1e-6
        ):
            out.append(prev)
            i += 1
            continue
        # Validate the body is add(s, mode=Mode.SUBTRACT) — anything else
        # would be unexpected.
        if loop_body.strip() != "add(s, mode=Mode.SUBTRACT)":
            out.append(prev)
            i += 1
            continue

        # Absorb. Carry forward any comments between the two original
        # lines so the user can still see what features fed in.
        out.extend(lines[i + 1 : j])
        out.append(f"for s in PolarLocations(0, {total}) * {prism_var}:")
        out.append(loop_body)
        i = j + 2

    return out


def _split_top_level_args(arglist: str) -> list[str]:
    """Split a comma-separated argument list, respecting nested parentheses.

    ``_split_top_level_args("a, b, foo(c, d), e")`` → ``["a", " b", " foo(c, d)", " e"]``.
    """
    args: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(arglist):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(arglist[start:i])
            start = i + 1
    args.append(arglist[start:])
    return args


def _inject_private_mode_in_inner_extrude(expr: str) -> tuple[str, bool]:
    """If ``expr`` wraps a bare ``extrude(...)`` call (e.g. inside
    ``mirror(extrude(...), about=...)``), inject ``mode=Mode.PRIVATE``
    into that inner extrude so its in-BuildPart Mode.ADD side effect
    doesn't add the source prism a second time.

    Returns ``(rewritten_expr, did_inject)``. The top-level ``extrude(...)``
    case (where the extrude *is* the intended add) is left untouched —
    only inner ones get the Mode.PRIVATE treatment.
    """
    # Top-level ``extrude(...)`` is the intended add — no injection.
    if expr.startswith("extrude("):
        return expr, False
    # Find an inner ``extrude(`` call. Match the substring and balanced
    # parens for the call's argument list.
    idx = expr.find("extrude(")
    if idx == -1:
        return expr, False
    arg_start = idx + len("extrude(")
    depth = 1
    i = arg_start
    while i < len(expr) and depth > 0:
        if expr[i] == "(":
            depth += 1
        elif expr[i] == ")":
            depth -= 1
        i += 1
    if depth != 0:
        return expr, False  # unbalanced; bail
    close_paren = i - 1  # position of the matching ')'
    inner_args = expr[arg_start:close_paren].rstrip()
    sep = "" if inner_args.endswith(",") else ", "
    rewritten = (
        expr[:arg_start] + inner_args + sep + "mode=Mode.PRIVATE" + expr[close_paren:]
    )
    return rewritten, True


def _to_builder_lines(
    u: TranslationUnit, body_var: str
) -> tuple[list[str], set[str]] | None:
    """Convert one algebra-mode body-feature unit into builder-mode lines.

    Returns (lines, extra_imports), or None if the unit doesn't match a
    recognised pattern — callers should fall back to algebra mode.
    """
    line = u.lines[0]
    body_part = f"{body_var}.part"

    # First-feature Pad: ``var = extrude(...)``.
    m = _PAD_FIRST_RE.match(line)
    if m:
        return [m.group(2)], set()

    # First-feature Revolution: ``var = revolve(...)``.
    m = _REVOL_FIRST_RE.match(line)
    if m:
        return [m.group(2)], set()

    # Dressup (fillet/chamfer/draft) on the running body. The base var in
    # the existing line is the prior unit's var_name; rewrite it to
    # ``<body>.part``.
    m = _DRESSUP_RE.match(line)
    if m:
        expr = m.group(2)
        # Replace _edges_at(<base_var>, ...) / _faces_at(...) bases with
        # <body>.part. We don't know the base var from the unit alone, so
        # use a generic pattern: the first arg to _edges_at/_faces_at.
        expr = _re.sub(
            r"(_edges_at|_faces_at)\(\s*(\w+)\s*,",
            lambda mm: f"{mm.group(1)}({body_part},",
            expr,
        )
        return [expr], set()

    # Chained additive: ``var = base + rhs``. Common for chained Pads,
    # Revolutions inside a body, additive patterns via _pattern_union.
    m = _CHAINED_ADD_RE.match(line)
    if m:
        base_var = m.group(2)
        rhs = m.group(3).strip()
        # Bare extrude(...)/revolve(...) → emit as a statement; that's
        # the cleanest builder-mode form for added prisms.
        if rhs.startswith(("extrude(", "revolve(")):
            return [rhs], set()
        # ``_pattern_union(base, copy1, copy2, ...)``: the helper unions
        # the base with each copy. In builder mode, the base is already
        # in the running part — adding ``_pattern_union(base, ...)``
        # would double-add it. Convert to one ``add(copyN)`` per copy.
        if rhs.startswith("_pattern_union(") and rhs.endswith(")"):
            inner = rhs[len("_pattern_union("):-1]
            args = _split_top_level_args(inner)
            if args and args[0].strip() == base_var:
                copies = [a.strip() for a in args[1:]]
                if copies:
                    return [f"add({c})" for c in copies], {"add"}
        # Generic additive composition (mirror, etc.).
        return [f"add({rhs})"], {"add"}

    # Direct ``var = _pattern_union(<base>, copy1, copy2, ...)``. Used by
    # the Mirror feature and any additive pattern that emits through the
    # ``_pattern_union`` helper without a leading composition. The base is
    # already in the running part (because ``_pattern_union`` is the body's
    # final aggregate), so we drop the wrapper and emit one ``add(copyN)``
    # per copy.
    #
    # Inner extrudes inside wrapping calls (e.g. ``mirror(extrude(...),
    # about=...)``) need ``mode=Mode.PRIVATE`` so the extrude's Mode.ADD
    # side effect doesn't add the source prism *again* inside the BuildPart
    # context (the source was added by an earlier Pad in the same body).
    m = _PATTERN_UNION_RE.match(line)
    if m:
        full_call = line.split("=", 1)[1].strip()
        inner = full_call[len("_pattern_union("):-1]
        args = _split_top_level_args(inner)
        if len(args) >= 2:
            copies = [a.strip() for a in args[1:]]
            out_lines: list[str] = []
            extra_imports: set[str] = {"add"}
            for c in copies:
                c_with_private, used_private = _inject_private_mode_in_inner_extrude(c)
                if used_private:
                    extra_imports.add("Mode")
                out_lines.append(f"add({c_with_private})")
            return out_lines, extra_imports

    # Chained subtractive: ``var = base - rhs``.
    m = _CHAINED_SUB_RE.match(line)
    if m:
        rhs = m.group(3).strip()
        # Bare extrude(...) / revolve(...) — inject mode=Mode.SUBTRACT.
        # extrude(profile, amount=N) → extrude(profile, amount=N,
        # mode=Mode.SUBTRACT).
        if rhs.startswith(("extrude(", "revolve(")):
            # Inject the mode kwarg before the closing paren of the call.
            if rhs.endswith(")"):
                rewritten = rhs[:-1].rstrip()
                if rewritten.endswith(","):
                    rewritten = rewritten + " mode=Mode.SUBTRACT)"
                else:
                    rewritten = rewritten + ", mode=Mode.SUBTRACT)"
                return [rewritten], set()
        # Pattern with PolarLocations / Locations / mirror() / etc.
        return [f"add({rhs}, mode=Mode.SUBTRACT)"], {"add"}

    return None


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
        label=pad.Label,
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


def _resolve_pocket_uptoface_length(pocket, base_volume: float | None = None) -> float:
    """For Pocket.Type in ('UpToFirst', 'UpToFace'), compute the effective
    carve length by inspecting the FreeCAD-evaluated body shape.

    ``carved_volume = base_volume - Pocket.Shape.Volume``; dividing by the
    sketch profile's area gives the depth the carve actually went. This
    lets us emit ``UpToFirst`` / ``UpToFace`` as a regular ``Type='Length'``
    extrude — the translator never needs to track or resolve build123d's
    face-selection API.

    ``base_volume`` defaults to ``pocket.BaseFeature.Shape.Volume`` (the
    in-Body case). For atomic Pockets the caller passes the previous
    solid's volume — there's no BaseFeature.

    Assumes the carve is a prism (no taper, no curved bottom). Holds for
    the way both modes are used in the library: a sketch + planar normal.
    """
    import Part  # lazy
    if base_volume is None:
        base_feature = pocket.BaseFeature
        if base_feature is None or not hasattr(base_feature, "Shape"):
            raise UnsupportedFeatureError(
                pocket.TypeId,
                f"{pocket.Label} ({pocket.Type} Pocket without resolvable BaseFeature)",
            )
        base_volume = float(base_feature.Shape.Volume)
    own_vol = float(pocket.Shape.Volume)
    carved = base_volume - own_vol
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
        label=pocket.Label,
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

    type_id = getattr(obj, "TypeId", "")
    if type_id == "Sketcher::SketchObject":
        return _sketch_axis_expr(obj, subs, rev)
    if type_id == "PartDesign::Line":
        return _datum_line_axis_expr(obj)

    raise UnsupportedFeatureError(
        rev.TypeId,
        f"{rev.Label} (ReferenceAxis={name!r}/{label!r} not supported in tier-2)",
    )


def _datum_line_axis_expr(datum) -> tuple[str, set[str]]:
    """Resolve a ``PartDesign::Line`` (datum line) into a build123d Axis
    expression. The datum's Placement gives origin (Base) and direction
    (Rotation applied to the local +Z axis — Datum-line convention).

    If the resolved axis matches a coordinate axis through the origin,
    emit the ``Axis.X`` / ``Axis.Y`` / ``Axis.Z`` shorthand. Otherwise
    emit an explicit ``Axis((ox, oy, oz), (dx, dy, dz))`` call.
    """
    import FreeCAD  # lazy

    placement = datum.Placement
    origin = placement.Base
    direction = placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))

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
        label=rev.Label,
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
# Hole — parametric drilled hole (tier 2)
# ---------------------------------------------------------------------------


def _hole_carve_expression(hole) -> tuple[str, str, set[str]]:
    """Build the build123d expression for a Hole's carve (subtractive shape).

    Returns ``(extrude_expr, comment_note, imports)`` where ``extrude_expr``
    is an ``extrude(Sketch() + plane * (Circle(r) + Circle(r) + ...),
    amount=-(depth + tip_height))`` carving the hole into the body.

    Shared between ``_translate_hole`` (body-chain emit) and
    ``_hole_prism_expression`` (Pattern Original emit).
    """
    if str(getattr(hole, "DepthType", "Dimension")) != "Dimension":
        raise UnsupportedFeatureError(
            hole.TypeId,
            f"{hole.Label} (DepthType={hole.DepthType!r}; v1 supports 'Dimension')",
        )
    if str(getattr(hole, "HoleCutType", "None")) != "None":
        raise UnsupportedFeatureError(
            hole.TypeId,
            f"{hole.Label} (HoleCutType={hole.HoleCutType!r}; v1 supports 'None')",
        )
    if bool(getattr(hole, "Threaded", False)) or bool(
        getattr(hole, "ModelThread", False)
    ):
        raise UnsupportedFeatureError(
            hole.TypeId,
            f"{hole.Label} (Threaded={hole.Threaded}, ModelThread={hole.ModelThread} "
            "— v1 doesn't emit thread geometry)",
        )
    if bool(getattr(hole, "Tapered", False)):
        raise UnsupportedFeatureError(
            hole.TypeId, f"{hole.Label} (Tapered Hole not yet supported)",
        )

    profile = hole.Profile
    if isinstance(profile, (list, tuple)):
        profile = profile[0]
    sketch = profile

    diameter = float(hole.Diameter.Value)
    depth = float(hole.Depth.Value)
    drill_point = str(getattr(hole, "DrillPoint", "Flat"))
    drill_angle_deg: float | None = None
    if drill_point == "Angled":
        drill_angle_deg = float(hole.DrillPointAngle.Value)
        half_apex_rad = math.radians((180.0 - drill_angle_deg) / 2.0)
        tip_height = (diameter / 2.0) / math.tan(half_apex_rad)
    else:
        tip_height = 0.0
    total_depth = depth + tip_height
    reversed_ = bool(getattr(hole, "Reversed", False))
    amount = total_depth if reversed_ else -total_depth

    centers: list[tuple[float, float]] = []
    for i, g in enumerate(sketch.Geometry):
        if type(g).__name__ != "Circle":
            continue
        try:
            if sketch.getConstruction(i):
                continue
        except Exception:
            pass
        centers.append((float(g.Center.x), float(g.Center.y)))
    if not centers:
        raise UnsupportedFeatureError(
            hole.TypeId,
            f"{hole.Label} (sketch contains no positional circles)",
        )

    r = diameter / 2.0
    circle_terms = []
    for cx, cy in centers:
        if abs(cx) < 1e-6 and abs(cy) < 1e-6:
            circle_terms.append(f"Circle({format_value(r)})")
        else:
            circle_terms.append(
                f"(Pos({format_value(cx)}, {format_value(cy)}) * Circle({format_value(r)}))"
            )
    inner = " + ".join(circle_terms)

    plane = _plane_expr(sketch.Placement)
    if plane is None:
        face_expr = f"Sketch() + ({inner})"
    else:
        face_expr = f"Sketch() + {plane} * ({inner})"

    imports = {"Sketch", "Circle", "extrude"}
    if any(abs(cx) > 1e-6 or abs(cy) > 1e-6 for cx, cy in centers):
        imports.add("Pos")
    if plane is not None:
        imports.add("Plane")

    extrude_expr = f"extrude({face_expr}, amount={format_value(amount)})"
    tip_note = (
        f" + {tip_height:.3f}mm drill tip ({drill_point}, {drill_angle_deg}°)"
        if drill_point == "Angled"
        else ""
    )
    comment = (
        f"D={diameter}, depth={depth}{tip_note}, "
        f"{len(centers)} location{'s' if len(centers) != 1 else ''}"
    )
    return extrude_expr, comment, imports


def _hole_prism_expression(hole, ctx: TranslationContext):
    """``_prism_expression`` adapter for Hole-as-Pattern-Original. Subtractive."""
    extrude_expr, _comment, imports = _hole_carve_expression(hole)
    return ("-", extrude_expr, imports)


def _translate_hole(
    hole, base_var: str, ctx: TranslationContext
) -> TranslationUnit:
    """Emit a PartDesign::Hole as a parametric cylindrical subtraction.

    v1 supports: ``DepthType='Dimension'``, ``DrillPoint='Flat' | 'Angled'``,
    ``HoleCutType='None'``, non-threaded, non-tapered. Each circle in the
    Profile sketch becomes a hole location; the *Hole.Diameter* overrides
    the sketch circle's radius (the sketch only positions the hole).

    Angled drill points are approximated by over-extruding the cylinder by
    the tip's nominal height ``(D/2) / tan((180 - DrillPointAngle) / 2)``.
    Exact for through-holes; for blind holes the result is a slight
    over-carve of an annular region near the tip.
    """
    profile = hole.Profile
    sketch = profile[0] if isinstance(profile, (list, tuple)) else profile
    extrude_expr, note, imports = _hole_carve_expression(hole)
    var = hole.Name
    line = f"{var} = {base_var} - {extrude_expr}"
    unit = TranslationUnit(
        var_name=var,
        label=hole.Label,
        imports=imports,
        lines=[line],
        comment=f"PartDesign::Hole {hole.Label!r}: {note}",
    )
    ctx.add_step(
        feature_type="hole",
        feature_name=hole.Name,
        depends_on=[base_var, sketch.Name],
        renamed_from_default=(hole.Label != hole.Name),
        build123d_code=line,
        properties=extract_properties(getattr(hole, "Shape", None)),
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
        label=grv.Label,
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
    # Midpoints are selectors for ``_edges_at`` which uses tol=1e-3.
    # Rounding coordinates to 5 dp (precision 1e-5, 100x tighter than tol)
    # is safe and produces dramatically more readable emit — long
    # solver-computed values like ``7.786755965961233`` become ``7.78676``.
    rounded = [(round(x, 5), round(y, 5), round(z, 5)) for x, y, z in midpoints]
    return "[" + ", ".join(f"({vfmt(x, y, z)})" for x, y, z in rounded) + "]"


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
        label=obj.Label,
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


def _face_center(face) -> tuple[float, float, float]:
    """World-frame centre-of-mass of a face. Companion to ``_edge_midpoint``."""
    c = face.CenterOfMass
    return (float(c.x), float(c.y), float(c.z))


def _resolve_face_centers(parent_shape, face_names) -> list[tuple[float, float, float]]:
    """FreeCAD's ``[Face1, Face2, ...]`` → centres in world frame.

    Mirror of ``_resolve_edge_midpoints`` for face-based features like Draft.
    """
    centers: list[tuple[float, float, float]] = []
    faces = parent_shape.Faces
    for name in face_names:
        if not name.startswith("Face"):
            raise UnsupportedFeatureError(
                "PartDesign::Draft",
                f"reference {name!r} not understood (expected 'Face<N>')",
            )
        idx = int(name[len("Face"):]) - 1
        if idx < 0 or idx >= len(faces):
            raise UnsupportedFeatureError(
                "PartDesign::Draft",
                f"face index {idx + 1} out of range (shape has {len(faces)} faces)",
            )
        centers.append(_face_center(faces[idx]))
    return centers


def _translate_draft(
    draft, base_var: str, ctx: TranslationContext
) -> TranslationUnit:
    """PartDesign::Draft → build123d ``draft(faces, neutral_plane, angle)``.

    Resolves Base.Subs (the draft faces) to centres via FreeCAD's evaluated
    BRep; the emit uses ``_faces_at`` to re-select them on build123d's BRep.
    NeutralPlane is taken from FreeCAD's referenced face: a build123d
    ``Plane(origin=centre, z_dir=normal)`` matches the OCCT side perfectly.

    Limitations: ``PullDirection`` (custom pull direction) is not handled.
    ``Reversed`` flips the angle sign.
    """
    base = draft.Base
    if not isinstance(base, (list, tuple)) or len(base) != 2:
        raise UnsupportedFeatureError(
            draft.TypeId, f"{draft.Label} (Draft.Base has unexpected shape)"
        )
    parent, face_names = base
    neutral = draft.NeutralPlane
    if not isinstance(neutral, (list, tuple)) or len(neutral) != 2:
        raise UnsupportedFeatureError(
            draft.TypeId,
            f"{draft.Label} (Draft.NeutralPlane required — pull-direction "
            "mode not yet supported)",
        )
    neutral_obj, neutral_subs = neutral
    if not neutral_subs:
        raise UnsupportedFeatureError(
            draft.TypeId,
            f"{draft.Label} (Draft.NeutralPlane has no face reference)",
        )

    parent_shape = parent.Shape
    face_centers = _resolve_face_centers(parent_shape, face_names)
    if not face_centers:
        raise UnsupportedFeatureError(
            draft.TypeId, f"{draft.Label} (no faces referenced)",
        )

    # Neutral plane: take first referenced face's centre + outward normal.
    nidx = int(neutral_subs[0][len("Face"):]) - 1
    nface = neutral_obj.Shape.Faces[nidx]
    nc = nface.CenterOfMass
    nn = nface.normalAt(0, 0)

    angle = float(draft.Angle.Value) if hasattr(draft.Angle, "Value") else float(draft.Angle)
    if bool(getattr(draft, "Reversed", False)):
        angle = -angle
    # FreeCAD's positive Draft angle slopes the face INWARD from the
    # neutral plane (removes material); build123d's positive ``draft()``
    # angle slopes the face *outward* in the neutral-plane normal
    # direction. Flip the sign so the two conventions match.
    angle = -angle

    centers_repr = _format_midpoints(face_centers)
    plane_expr = (
        f"Plane(origin=({format_value(nc.x)}, {format_value(nc.y)}, "
        f"{format_value(nc.z)}), z_dir=({format_value(nn.x)}, "
        f"{format_value(nn.y)}, {format_value(nn.z)}))"
    )
    var = draft.Name
    # build123d's ``draft(faces, neutral_plane, angle)`` infers the parent
    # solid from the faces themselves — no separate base shape arg.
    line = (
        f"{var} = draft(faces=_faces_at({base_var}, {centers_repr}), "
        f"neutral_plane={plane_expr}, angle={format_value(angle)})"
    )

    unit = TranslationUnit(
        var_name=var,
        label=draft.Label,
        imports={"draft", "Plane"},
        lines=[line],
        comment=(
            f"PartDesign::Draft {draft.Label!r}: angle={angle}° on "
            f"{len(face_names)} faces of {parent.Name}, "
            f"neutral={neutral_obj.Name}/{neutral_subs[0]}"
        ),
        helpers={"_faces_at"},
    )
    ctx.add_step(
        feature_type="draft",
        feature_name=draft.Name,
        depends_on=[base_var],
        renamed_from_default=(draft.Label != draft.Name),
        build123d_code=line,
        properties=extract_properties(getattr(draft, "Shape", None)),
    )
    return unit


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
    if tid == "PartDesign::Hole":
        return _hole_prism_expression(orig, ctx)
    if tid not in ("PartDesign::Pad", "PartDesign::Pocket"):
        raise UnsupportedFeatureError(
            "PartDesign::Pattern",
            f"Pattern Original is {tid!r}; v1 only supports Pad / Pocket / Hole",
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
    # When a uniform single-Original pattern can be expressed as
    # ``Locations(...) * prism`` (single algebra-mode term), we use that
    # form instead of the helper chain — see ``locations_expr``. Falls
    # back to ``extra_terms`` + helper when the pattern is multi-Original
    # or has a structure Locations can't compactly express.
    locations_expr: str | None = None

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
        # Single-Original uniform linear pattern → emit
        # ``Locations((dx, dy, dz), (2dx, 2dy, 2dz), ...) * prism``. The
        # current_var already contains the i=0 copy; the Locations
        # provides i=1..N-1.
        if len(prism_exprs) == 1 and occurrences > 1:
            positions = []
            for i in range(1, occurrences):
                step = i * offset
                positions.append(
                    f"({format_value(direction[0] * step)}, "
                    f"{format_value(direction[1] * step)}, "
                    f"{format_value(direction[2] * step)})"
                )
            locations_expr = f"Locations({', '.join(positions)}) * {prism_exprs[0]}"
            imports.add("Locations")
        else:
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
        # Single-Original uniform polar pattern about +Z → emit
        # ``PolarLocations(0, N-1, start_angle=step, angular_range=(N-1)*step) * prism``.
        # PolarLocations(0, k) rotates each copy about the origin by
        # start_angle + i·(range/(k-1)); a shape already off-axis is
        # rotated to k angular positions. The body's current_var holds
        # the i=0 copy; Locations provides i=1..N-1.
        # Restricted to +Z-axis patterns (the overwhelming common case
        # in the library); arbitrary-axis polar falls back to the
        # explicit-term form.
        is_z_axis = (
            abs(direction[0]) < 1e-9
            and abs(direction[1]) < 1e-9
            and abs(direction[2] - 1) < 1e-6
        )
        if len(prism_exprs) == 1 and occurrences > 1 and is_z_axis:
            extra_count = occurrences - 1
            # build123d's PolarLocations(0, n, start, range) spaces n
            # copies at start + i·(range/n) for i=0..n-1 (NOT range/(n-1)
            # — the spacing-by-count formula avoids wrap when range=360).
            # To produce copies at angles k·offset for k=1..N-1, set
            # angular_range = extra_count * offset so spacing = offset.
            angular_range = extra_count * offset
            locations_expr = (
                f"PolarLocations(0, {extra_count}, "
                f"start_angle={format_value(offset)}, "
                f"angular_range={format_value(angular_range)}) "
                f"* {prism_exprs[0]}"
            )
            imports.add("PolarLocations")
        else:
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

    if locations_expr is not None:
        # Single-Original uniform pattern: one-liner via Locations.
        op = "+" if sign == "+" else "-"
        line = f"{pat.Name} = {current_var} {op} {locations_expr}"
    elif not extra_terms:
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
        label=pat.Label,
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
    if pocket_type not in {"Length", "ThroughAll", "UpToFirst", "UpToFace"}:
        raise UnsupportedFeatureError(
            pocket.TypeId,
            f"{pocket.Label} (atomic Pocket.Type={pocket_type!r}; tier-2 atomic "
            f"Pocket supports 'Length', 'ThroughAll', 'UpToFirst', 'UpToFace')",
        )

    profile = pocket.Profile
    if isinstance(profile, (list, tuple)):
        profile = profile[0]
    sketch_var = profile.Name
    reversed_ = bool(getattr(pocket, "Reversed", False))

    if pocket_type in ("UpToFirst", "UpToFace"):
        # Atomic UpToFirst/UpToFace: there's no BaseFeature to read the
        # pre-carve volume from. The previous solid in document order
        # plays that role — same chaining semantic as atomic ThroughAll.
        # The effective carve length is resolved from the volume delta.
        prev = _previous_solid_in_doc_with_typeid(pocket)
        if prev is None:
            raise UnsupportedFeatureError(
                pocket.TypeId,
                f"{pocket.Label} (atomic {pocket_type} Pocket with no previous solid)",
            )
        base_var, _base_typeid = prev
        base_volume = float(prev_obj.Shape.Volume) if (prev_obj := pocket.Document.getObject(base_var)) else 0.0
        length = _resolve_pocket_uptoface_length(pocket, base_volume=base_volume)
        amount = length if reversed_ else -length
        unit = TranslationUnit(
            var_name=pocket.Name,
            label=pocket.Label,
            imports={"extrude"},
            lines=[
                f"{pocket.Name} = {base_var} - "
                f"extrude({sketch_var}, amount={format_value(amount)})"
            ],
            comment=f"PartDesign::Pocket {pocket.Label!r} "
                    f"(body-less, {pocket_type} → length={length:.4g})"
                    + (" (reversed)" if reversed_ else ""),
        )
        deps = [base_var, sketch_var]
        ctx.add_step(
            feature_type="pocket",
            feature_name=pocket.Name,
            depends_on=deps,
            renamed_from_default=(pocket.Label != pocket.Name),
            build123d_code=unit.lines[0],
            properties=extract_properties(getattr(pocket, "Shape", None)),
        )
        return [unit]

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
            label=pocket.Label,
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
                label=pocket.Label,
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
                label=pocket.Label,
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
        label=ext.Label,
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
        label=rev.Label,
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


def _spine_path_expression(spine) -> tuple[str, set[str]]:
    """Render a Sweep spine (a sketch/wire) as a build123d path expression.

    Walks the spine's evaluated Shape edges in world frame. v1 supports
    single-edge LineSegment spines. Multi-edge or curved spines (arcs /
    splines) raise UnsupportedFeatureError pending v2 work.
    """
    shape = getattr(spine, "Shape", None)
    if shape is None or shape.isNull():
        raise UnsupportedFeatureError(
            "Part::Sweep",
            f"spine {spine.Name!r} has no Shape — cannot resolve path",
        )
    edges = list(shape.Edges)
    if len(edges) != 1:
        raise UnsupportedFeatureError(
            "Part::Sweep",
            f"spine {spine.Name!r} has {len(edges)} edges; v1 supports a "
            "single-edge LineSegment spine",
        )
    edge = edges[0]
    curve = edge.Curve
    kind = type(curve).__name__
    if kind != "Line":
        raise UnsupportedFeatureError(
            "Part::Sweep",
            f"spine {spine.Name!r} edge is {kind!r}; v1 supports straight "
            "Line spines only",
        )
    p0 = edge.Vertexes[0].Point
    p1 = edge.Vertexes[1].Point
    expr = (
        f"Line(({format_value(p0.x)}, {format_value(p0.y)}, {format_value(p0.z)}), "
        f"({format_value(p1.x)}, {format_value(p1.y)}, {format_value(p1.z)}))"
    )
    return expr, {"Line"}


def _translate_part_sweep(sw, ctx: TranslationContext) -> list[TranslationUnit]:
    """Part::Sweep → build123d ``sweep(profile, path=spine)``.

    v1 supports single-section, single-edge LineSegment spine, Solid=True.
    Multi-section sweeps belong in Loft territory.
    """
    if not bool(getattr(sw, "Solid", True)):
        raise UnsupportedFeatureError(
            sw.TypeId, f"{sw.Label} (Solid=False — open-shell sweep not supported)",
        )
    sections = list(sw.Sections)
    if len(sections) != 1:
        raise UnsupportedFeatureError(
            sw.TypeId,
            f"{sw.Label} ({len(sections)} Sections; v1 supports single section)",
        )
    profile_obj = sections[0]
    spine = sw.Spine
    if isinstance(spine, (list, tuple)):
        spine = spine[0]
    if spine is None:
        raise UnsupportedFeatureError(
            sw.TypeId, f"{sw.Label} (Spine is None)",
        )

    path_expr, path_imports = _spine_path_expression(spine)

    var = sw.Name
    line = f"{var} = sweep({profile_obj.Name}, path={path_expr})"
    imports = {"sweep"} | path_imports
    unit = TranslationUnit(
        var_name=var,
        label=sw.Label,
        imports=imports,
        lines=[line],
        comment=f"Part::Sweep {sw.Label!r}: profile={profile_obj.Name}, "
                f"spine={spine.Name}",
    )
    ctx.add_step(
        feature_type="sweep",
        feature_name=sw.Name,
        depends_on=[profile_obj.Name, spine.Name],
        renamed_from_default=(sw.Label != sw.Name),
        build123d_code=line,
        properties=extract_properties(getattr(sw, "Shape", None)),
    )
    return [unit]


def _translate_part_loft(lt, ctx: TranslationContext) -> list[TranslationUnit]:
    """Part::Loft → build123d ``loft([profile1, profile2, ...], ruled=...)``.

    v1 supports ``Solid=True`` lofts with two or more sections. ``Ruled``
    is honoured (straight-line transition between sections). ``Closed``
    (loop back to first section) is not yet supported.
    """
    if not bool(getattr(lt, "Solid", True)):
        raise UnsupportedFeatureError(
            lt.TypeId, f"{lt.Label} (Solid=False — open-shell loft not supported)",
        )
    if bool(getattr(lt, "Closed", False)):
        raise UnsupportedFeatureError(
            lt.TypeId, f"{lt.Label} (Closed=True — looped loft not yet supported)",
        )
    sections = list(lt.Sections)
    if len(sections) < 2:
        raise UnsupportedFeatureError(
            lt.TypeId, f"{lt.Label} (fewer than 2 sections — invalid loft)",
        )
    ruled = bool(getattr(lt, "Ruled", False))

    section_vars = [s.Name for s in sections]
    var = lt.Name
    args = ", ".join(section_vars)
    ruled_arg = ", ruled=True" if ruled else ""
    line = f"{var} = loft([{args}]{ruled_arg})"
    unit = TranslationUnit(
        var_name=var,
        label=lt.Label,
        imports={"loft"},
        lines=[line],
        comment=f"Part::Loft {lt.Label!r}: {len(sections)} sections"
                + (" (ruled)" if ruled else ""),
    )
    ctx.add_step(
        feature_type="loft",
        feature_name=lt.Name,
        depends_on=section_vars,
        renamed_from_default=(lt.Label != lt.Name),
        build123d_code=line,
        properties=extract_properties(getattr(lt, "Shape", None)),
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
        label=comp.Label,
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
        label=mir.Label,
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


def _translate_part_dressup(
    obj, ctx: TranslationContext, *, builder: str, size_kw: str, feature_type: str
) -> list[TranslationUnit]:
    """Shared emit for ``Part::Chamfer`` / ``Part::Fillet`` — they share property
    layout (``Base`` object + ``Edges`` list of ``(idx, s1, s2)`` tuples).

    Phase-1 scope: all edges must share the same ``s1 == s2`` (symmetric
    bevel/radius) and the same value across edges. Asymmetric or
    variable-per-edge raise UnsupportedFeatureError — the only fixtures
    in current library samples are uniform symmetric, so the gain from
    supporting variants doesn't pay for the risk of subtle mis-translation.
    """
    base = obj.Base
    if base is None or not hasattr(base, "Shape"):
        raise UnsupportedFeatureError(
            obj.TypeId, f"{obj.Label} ({obj.TypeId} has no Base shape)"
        )
    base_var = base.Name
    edges = list(obj.Edges)
    if not edges:
        raise UnsupportedFeatureError(
            obj.TypeId, f"{obj.Label} ({obj.TypeId}.Edges is empty)"
        )
    sizes = [(s1, s2) for _idx, s1, s2 in edges]
    if any(abs(s1 - s2) > 1e-12 for s1, s2 in sizes):
        raise UnsupportedFeatureError(
            obj.TypeId,
            f"{obj.Label} (asymmetric {obj.TypeId} not yet supported; "
            f"all edges must have equal sizes)",
        )
    s0 = sizes[0][0]
    if any(abs(s - s0) > 1e-12 for s, _ in sizes):
        raise UnsupportedFeatureError(
            obj.TypeId,
            f"{obj.Label} (variable-per-edge {obj.TypeId} not yet supported; "
            f"all edges must share the same size)",
        )

    parent_shape = base.Shape
    parent_edges = parent_shape.Edges
    midpoints: list[tuple[float, float, float]] = []
    for idx, _s1, _s2 in edges:
        i = idx - 1  # Part::Chamfer / Part::Fillet use 1-based indices
        if i < 0 or i >= len(parent_edges):
            raise UnsupportedFeatureError(
                obj.TypeId,
                f"{obj.Label} (edge index {idx} out of range; "
                f"parent has {len(parent_edges)} edges)",
            )
        midpoints.append(_edge_midpoint(parent_edges[i]))

    midpoints_repr = _format_midpoints(midpoints)
    line = (
        f"{obj.Name} = {builder}(_edges_at({base_var}, {midpoints_repr}), "
        f"{size_kw}={format_value(s0)})"
    )
    unit = TranslationUnit(
        var_name=obj.Name,
        label=obj.Label,
        imports={builder},
        lines=[line],
        comment=f"{obj.TypeId} {obj.Label!r}: {size_kw}={s0} on {len(edges)} edges of {base_var}",
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


def _translate_part_chamfer(cha, ctx: TranslationContext) -> list[TranslationUnit]:
    """Part workbench ``Part::Chamfer`` → build123d ``chamfer(...)``.

    Different shape from ``PartDesign::Chamfer``: ``cha.Base`` is the parent
    object directly (not a ``(parent, [Edge1, ...])`` tuple), and ``cha.Edges``
    is a list of ``(edge_index, size1, size2)`` tuples (1-based edge indices).
    """
    return _translate_part_dressup(
        cha, ctx, builder="chamfer", size_kw="length", feature_type="chamfer"
    )


def _translate_part_fillet(fil, ctx: TranslationContext) -> list[TranslationUnit]:
    """Part workbench ``Part::Fillet`` → build123d ``fillet(...)``.

    Same property layout as ``Part::Chamfer`` — Edges entries are
    ``(idx, radius1, radius2)`` where the two radii are usually equal.
    """
    return _translate_part_dressup(
        fil, ctx, builder="fillet", size_kw="radius", feature_type="fillet"
    )


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
    "Part::Sweep": _translate_part_sweep,
    "Part::Loft": _translate_part_loft,
    "Part::Chamfer": _translate_part_chamfer,
    "Part::Fillet": _translate_part_fillet,
    "Part::Compound": _translate_part_compound,
    "Part::Mirroring": _translate_part_mirroring,
    # Standalone sketch at the document level.
    "Sketcher::SketchObject": translate_sketch,
}
