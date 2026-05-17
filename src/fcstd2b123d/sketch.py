"""Sketcher::SketchObject translator.

Tier-2 scope:
  - Line segments, circles, arcs of circles
  - Single or multi-loop sketches (outer minus inner holes by area)
  - Any sketch plane (XY default; non-XY via build123d Plane)

Strategy
========
A FreeCAD sketch is a collection of 2D geometry primitives, all in the
sketch's local frame. After recompute, ``sketch.Geometry`` contains the
post-solve concrete shapes. We:

1. Split geometry into full circles (each a closed loop) and chainable
   edges (lines + arcs).
2. Chain edges into closed loops by matching endpoints.
3. Emit each loop as a build123d 2D expression: ``Circle(r)`` for circles,
   ``make_face(Line(...) + CenterArc(...) + ...)`` for line+arc loops.
4. Compute each loop's signed area; the largest is the outer boundary,
   the rest are subtracted as holes.
5. If the sketch sits on a non-XY plane, wrap the final face in a
   build123d Plane derived from sketch.Placement.

Unsupported in tier-2: helix, open or multi-component sketches that
aren't simple outer-minus-inners. B-splines (Bezier-form exact, others
via 64-sample interpolation) and ellipses are supported.
"""

from __future__ import annotations

import math

from .context import TranslationContext
from .emitter import TranslationUnit, format_value
from .errors import UnsupportedFeatureError

_TOL = 1e-6


def _is_xy_plane(p) -> bool:
    """True if the placement is identity (sketch on world XY)."""
    return (
        abs(p.Rotation.Angle) < _TOL
        and abs(p.Base.x) < _TOL
        and abs(p.Base.y) < _TOL
        and abs(p.Base.z) < _TOL
    )


def _plane_expr(placement) -> str | None:
    """Return a build123d Plane(...) expression, or None for XY identity.

    Detects canonical planes (XY/XZ/YZ + reverses + normal offsets) and emits
    the short form (``Plane.YZ``, ``Plane.YZ.offset(5)``, ``Plane.YZ.reverse()``)
    when the placement matches. Falls back to the explicit
    ``Plane(origin=..., x_dir=..., z_dir=...)`` constructor otherwise.
    """
    if _is_xy_plane(placement):
        return None
    import FreeCAD  # lazy
    rot = placement.Rotation
    x_dir = rot.multVec(FreeCAD.Vector(1, 0, 0))
    z_dir = rot.multVec(FreeCAD.Vector(0, 0, 1))
    origin = placement.Base

    canonical = _canonical_plane_expr(origin, x_dir, z_dir)
    if canonical is not None:
        return canonical

    return (
        f"Plane(origin=({_fmt(origin.x)}, {_fmt(origin.y)}, {_fmt(origin.z)}), "
        f"x_dir=({_fmt(x_dir.x)}, {_fmt(x_dir.y)}, {_fmt(x_dir.z)}), "
        f"z_dir=({_fmt(z_dir.x)}, {_fmt(z_dir.y)}, {_fmt(z_dir.z)}))"
    )


# Canonical-plane signatures. ``(x_dir, z_dir)`` tuples → short name.
# Sourced from build123d itself (Plane.XY / XZ / YZ / YX / ZX / ZY); these
# are the only orientations the short form actually exists for. Reverses
# and other rotations of these planes don't have a clean build123d
# shorthand (``.reverse()`` does NOT compose how a naive read would
# suggest — see PR-pending discussion), so we don't attempt them.
_CANONICAL_PLANES: list[tuple[tuple[float, ...], tuple[float, ...], str]] = [
    ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), "Plane.XY"),
    ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), "Plane.XZ"),
    ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), "Plane.YZ"),
    ((0.0, 1.0, 0.0), (0.0, 0.0, -1.0), "Plane.YX"),
    ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), "Plane.ZX"),
    ((0.0, 0.0, 1.0), (-1.0, 0.0, 0.0), "Plane.ZY"),
]


def _vec_close(a, b, tol: float = 1e-9) -> bool:
    return all(abs(ax - bx) < tol for ax, bx in zip(a, b))


def _canonical_plane_expr(origin, x_dir, z_dir) -> str | None:
    """Return e.g. ``Plane.YZ`` or ``Plane.YZ.offset(5)`` if the placement
    matches one of the 6 canonical build123d planes (possibly with origin
    offset along the normal), else None.

    Conservative on purpose: we only short-form exact matches of the 6
    canonical (x_dir, z_dir) frames. Other rotations of those planes
    (e.g. an XZ plane rotated 180° about its normal) emit the explicit
    ``Plane(origin=..., x_dir=..., z_dir=...)`` form because the
    candidates build123d gives us — ``.reverse()`` etc. — don't compose
    the way they read (see cabin_flashlight regression).
    """
    x_tuple = (x_dir.x, x_dir.y, x_dir.z)
    z_tuple = (z_dir.x, z_dir.y, z_dir.z)
    for x_ref, z_ref, name in _CANONICAL_PLANES:
        if _vec_close(x_tuple, x_ref) and _vec_close(z_tuple, z_ref):
            ox, oy, oz = origin.x, origin.y, origin.z
            # Decompose origin into (offset along z_ref) + (residual in plane).
            offset = ox * z_ref[0] + oy * z_ref[1] + oz * z_ref[2]
            residual_x = ox - offset * z_ref[0]
            residual_y = oy - offset * z_ref[1]
            residual_z = oz - offset * z_ref[2]
            if (
                abs(residual_x) < 1e-9
                and abs(residual_y) < 1e-9
                and abs(residual_z) < 1e-9
            ):
                if abs(offset) < 1e-9:
                    return name
                return f"{name}.offset({_fmt(offset)})"
    return None


_fmt = format_value


# ---------------------------------------------------------------------------
# Geometry chaining
# ---------------------------------------------------------------------------


def _segment_endpoints(g) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return ((sx, sy), (ex, ey)) for any chainable geometry kind."""
    return (
        (g.StartPoint.x, g.StartPoint.y),
        (g.EndPoint.x, g.EndPoint.y),
    )


def _close(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) < _TOL and abs(a[1] - b[1]) < _TOL


def _chain_loops(edges: list) -> list[list]:
    """Group edges into closed loops.

    Each returned loop is a list of (geom, reversed) pairs in traversal
    order — `reversed` indicates whether the edge needs to be emitted with
    its start/end swapped to fit the chain.

    Raises UnsupportedFeatureError on open or unconnectable geometry.
    """
    loops = []
    remaining = list(edges)

    while remaining:
        seed = remaining.pop(0)
        ss, se = _segment_endpoints(seed)
        chain = [(seed, False)]
        start_pt = ss
        tail_pt = se

        # Follow the chain until we close back on start_pt.
        while not _close(tail_pt, start_pt):
            matched = False
            for i, e in enumerate(remaining):
                es, ee = _segment_endpoints(e)
                if _close(es, tail_pt):
                    chain.append((e, False))
                    tail_pt = ee
                    remaining.pop(i)
                    matched = True
                    break
                if _close(ee, tail_pt):
                    chain.append((e, True))
                    tail_pt = es
                    remaining.pop(i)
                    matched = True
                    break
            if not matched:
                raise UnsupportedFeatureError(
                    "Sketcher::SketchObject",
                    f"(open or disconnected geometry — no edge connects to ({tail_pt[0]:.3f}, {tail_pt[1]:.3f}))",
                )
        loops.append(chain)

    return loops


# ---------------------------------------------------------------------------
# Per-loop emission
# ---------------------------------------------------------------------------


def _emit_line(start: tuple[float, float], end: tuple[float, float]) -> str:
    return f"Line(({_fmt(start[0])}, {_fmt(start[1])}), ({_fmt(end[0])}, {_fmt(end[1])}))"


def _emit_arc(g, reverse: bool) -> str:
    """Emit a build123d CenterArc for a Part.GeomArcOfCircle.

    FreeCAD's ArcOfCircle has Center, Radius, FirstParameter, LastParameter
    (angles in radians, sweep is CCW from First to Last). build123d's
    CenterArc(center, radius, start_angle, arc_size) takes degrees and a
    signed sweep — negative arc_size goes CW. Reversing the chain direction
    means swapping endpoints and negating the sweep direction.
    """
    cx, cy = g.Center.x, g.Center.y
    r = g.Radius
    fpa = math.degrees(g.FirstParameter)
    lpa = math.degrees(g.LastParameter)
    sweep = lpa - fpa
    # Normalise to (-360, 360]
    while sweep > 360:
        sweep -= 360
    while sweep <= -360:
        sweep += 360
    if reverse:
        start_angle, arc_size = lpa, -sweep
    else:
        start_angle, arc_size = fpa, sweep
    # Normalise start_angle to [0, 360). The CenterArc start point depends
    # only on ``cos(start_angle)`` / ``sin(start_angle)`` (360-periodic),
    # so wrapping doesn't change the geometry — but ``start_angle=154.62``
    # reads much better than ``start_angle=514.62`` (raw FC value).
    start_angle = start_angle % 360
    return (
        f"CenterArc(center=({_fmt(cx)}, {_fmt(cy)}), radius={_fmt(r)}, "
        f"start_angle={_fmt(start_angle)}, arc_size={_fmt(arc_size)})"
    )


def _is_bezier_form(g) -> bool:
    """True if a BSplineCurve is in Bezier form: a single segment with all
    knot multiplicity at the endpoints, non-rational, non-periodic.

    A degree-d BSpline in Bezier form has d+1 poles and knots
    ``[0, 1]`` with multiplicities ``[d+1, d+1]``. These are *exactly*
    representable as build123d ``Bezier`` curves through the same poles
    — no discretization needed.
    """
    if g.isRational() or g.isPeriodic():
        return False
    if g.NbPoles != g.Degree + 1:
        return False
    mults = g.getMultiplicities()
    expected = g.Degree + 1
    return len(mults) == 2 and mults[0] == expected and mults[1] == expected


def _emit_bspline(g, reverse: bool) -> str:
    """Render a Part.GeomBSplineCurve as a build123d curve expression.

    Bezier-form B-splines (single segment, degree+1 poles, knots at the
    endpoints only, non-rational, non-periodic) emit as ``Bezier(...)``
    through the control points — an *exact* match for the source curve.
    Anything else falls back to a 64-sample ``Spline`` interpolation,
    which closes within the verify harness's relative tolerance for the
    splines that appear in the FreeCAD parts library.
    """
    if _is_bezier_form(g):
        poles = [(p.x, p.y) for p in g.getPoles()]
        if reverse:
            poles = list(reversed(poles))
        pts = ", ".join(f"({_fmt(x)}, {_fmt(y)})" for x, y in poles)
        return f"Bezier([{pts}])"

    fpa = g.FirstParameter
    lpa = g.LastParameter
    steps = 64
    samples: list[tuple[float, float]] = []
    for i in range(steps + 1):
        t = fpa + (lpa - fpa) * (i / steps)
        p = g.value(t)
        samples.append((p.x, p.y))
    if reverse:
        samples.reverse()
    pts = ", ".join(f"({_fmt(x)}, {_fmt(y)})" for x, y in samples)
    return f"Spline([{pts}])"


def _emit_chain_curve(chain: list) -> tuple[str, set[str]]:
    """Render a closed chain of (geom, reversed) entries as a 1D curve expression.

    Returns the inner curve expression (e.g. ``Polyline(...) + CenterArc(...)``)
    *without* the ``make_face`` wrapper, so the caller can hoist it to a
    named variable for first-class 1D inspection per the build123d algebra
    style.

    Runs of ≥ 2 consecutive ``LineSegment`` edges collapse into a single
    ``Polyline``: ``Line((a,b),(c,d)) + Line((c,d),(e,f))`` becomes
    ``Polyline((a,b),(c,d),(e,f))``. Single isolated segments stay as
    ``Line(...)``.
    """
    edges_expr: list[str] = []
    imports: set[str] = set()
    i = 0
    n = len(chain)
    while i < n:
        kind = type(chain[i][0]).__name__
        if kind == "LineSegment":
            run_start = i
            while i < n and type(chain[i][0]).__name__ == "LineSegment":
                i += 1
            run = chain[run_start:i]
            if len(run) >= 2:
                s0, e0 = _segment_endpoints(run[0][0])
                if run[0][1]:
                    s0, e0 = e0, s0
                points: list[tuple[float, float]] = [s0]
                for g, rev in run:
                    s, e = _segment_endpoints(g)
                    if rev:
                        s, e = e, s
                    points.append(e)
                edges_expr.append(_emit_polyline(points))
                imports.add("Polyline")
            else:
                s, e = _segment_endpoints(run[0][0])
                if run[0][1]:
                    s, e = e, s
                edges_expr.append(_emit_line(s, e))
                imports.add("Line")
        elif kind == "ArcOfCircle":
            edges_expr.append(_emit_arc(chain[i][0], chain[i][1]))
            imports.add("CenterArc")
            i += 1
        elif kind == "BSplineCurve":
            edges_expr.append(_emit_bspline(chain[i][0], chain[i][1]))
            if _is_bezier_form(chain[i][0]):
                imports.add("Bezier")
            else:
                imports.add("Spline")
            i += 1
        else:
            raise UnsupportedFeatureError(
                "Sketcher::SketchObject",
                f"(unsupported geometry kind {kind!r} in loop)",
            )
    return " + ".join(edges_expr), imports


def _emit_polyline(points: list[tuple[float, float]]) -> str:
    """Emit a Polyline with the given 2D points."""
    pts = ", ".join(f"({_fmt(x)}, {_fmt(y)})" for x, y in points)
    return f"Polyline({pts})"


def _emit_circle(g) -> tuple[str, set[str]]:
    """Render a Part.GeomCircle as a build123d Circle face, translated if not at origin."""
    cx, cy = g.Center.x, g.Center.y
    r = g.Radius
    imports: set[str] = {"Circle"}
    if abs(cx) < _TOL and abs(cy) < _TOL:
        return f"Circle({_fmt(r)})", imports
    imports.add("Pos")
    return f"(Pos({_fmt(cx)}, {_fmt(cy)}) * Circle({_fmt(r)}))", imports


def _emit_ellipse(g) -> tuple[str, set[str]]:
    """Render a Part.GeomEllipse as a build123d Ellipse face.

    FreeCAD's Ellipse holds ``Center``, ``MajorRadius``, ``MinorRadius``,
    and ``AngleXU`` — the angle (radians) the major axis makes with the
    sketch +X axis. build123d's ``Ellipse(x_radius, y_radius)`` produces
    an ellipse with major axis along X; ``Rot(Z=ang_deg)`` rotates it,
    and ``Pos(cx, cy)`` places its centre. Composition is right-to-left:
    centre last.
    """
    cx, cy = g.Center.x, g.Center.y
    major = g.MajorRadius
    minor = g.MinorRadius
    angle_deg = math.degrees(g.AngleXU)
    parts: list[str] = []
    imports: set[str] = {"Ellipse"}
    if abs(cx) > _TOL or abs(cy) > _TOL:
        parts.append(f"Pos({_fmt(cx)}, {_fmt(cy)})")
        imports.add("Pos")
    if abs(angle_deg) > _TOL:
        parts.append(f"Rot(Z={_fmt(angle_deg)})")
        imports.add("Rot")
    parts.append(f"Ellipse({_fmt(major)}, {_fmt(minor)})")
    expr = " * ".join(parts)
    if len(parts) > 1:
        expr = f"({expr})"
    return expr, imports


# ---------------------------------------------------------------------------
# Loop area (for outer-vs-inner classification)
# ---------------------------------------------------------------------------


def _sample_chain_points(chain: list) -> list[tuple[float, float]]:
    """Sample a chained loop into a polygon. Lines exact; arcs / B-splines at 16 steps."""
    pts: list[tuple[float, float]] = []
    for g, rev in chain:
        kind = type(g).__name__
        s, e = _segment_endpoints(g)
        if rev:
            s, e = e, s
        if kind == "LineSegment":
            pts.append(s)
        elif kind == "ArcOfCircle":
            cx, cy = g.Center.x, g.Center.y
            r = g.Radius
            fpa = g.FirstParameter
            lpa = g.LastParameter
            sweep = lpa - fpa
            if rev:
                fpa, sweep = lpa, -sweep
            steps = 16
            for i in range(steps):
                t = fpa + sweep * (i / steps)
                pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
        elif kind == "BSplineCurve":
            fpa = g.FirstParameter
            lpa = g.LastParameter
            steps = 16
            for i in range(steps):
                u = i / steps
                if rev:
                    u = 1 - u
                t = fpa + (lpa - fpa) * u
                p = g.value(t)
                pts.append((p.x, p.y))
    return pts


def _polygon_contains(poly: list[tuple[float, float]], p: tuple[float, float]) -> bool:
    """Ray-cast even-odd test."""
    x, y = p
    n = len(poly)
    inside = False
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xint = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < xint:
                inside = not inside
    return inside


def _interior_point_of_chain(chain: list) -> tuple[float, float]:
    """Centroid of sampled vertices — a robust-enough interior point for our purposes."""
    pts = _sample_chain_points(chain)
    x = sum(p[0] for p in pts) / len(pts)
    y = sum(p[1] for p in pts) / len(pts)
    return (x, y)


def _interior_point_of_circle(g) -> tuple[float, float]:
    return (g.Center.x, g.Center.y)


def _interior_point_of_ellipse(g) -> tuple[float, float]:
    return (g.Center.x, g.Center.y)


def _chain_contains_point(chain: list, p: tuple[float, float]) -> bool:
    return _polygon_contains(_sample_chain_points(chain), p)


def _circle_contains_point(g, p: tuple[float, float]) -> bool:
    dx = p[0] - g.Center.x
    dy = p[1] - g.Center.y
    return dx * dx + dy * dy < g.Radius * g.Radius


def _ellipse_contains_point(g, p: tuple[float, float]) -> bool:
    """Point-in-ellipse test in the ellipse's local frame.

    Transform ``p`` into the ellipse's centred + de-rotated frame, then
    apply the canonical (x/a)² + (y/b)² < 1 test.
    """
    dx = p[0] - g.Center.x
    dy = p[1] - g.Center.y
    a = g.AngleXU
    # Rotate the point into the ellipse's axis-aligned frame.
    lx = dx * math.cos(a) + dy * math.sin(a)
    ly = -dx * math.sin(a) + dy * math.cos(a)
    rx = g.MajorRadius
    ry = g.MinorRadius
    return (lx / rx) ** 2 + (ly / ry) ** 2 < 1


def _chain_area(chain: list) -> float:
    pts = _sample_chain_points(chain)
    n = len(pts)
    s = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return abs(s) * 0.5


def _circle_area(g) -> float:
    return math.pi * g.Radius * g.Radius


def _ellipse_area(g) -> float:
    return math.pi * g.MajorRadius * g.MinorRadius


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


_SUPPORTED_KINDS = {"LineSegment", "Circle", "ArcOfCircle", "BSplineCurve", "Ellipse"}
_CHAINABLE_KINDS = {"LineSegment", "ArcOfCircle", "BSplineCurve"}


def _is_construction(sketch, idx: int) -> bool:
    """FreeCAD's Sketcher marks reference-only geometry as 'construction' —
    it must not contribute to the resulting face."""
    try:
        return bool(sketch.getConstruction(idx))
    except Exception:
        return False


def translate_sketch(sketch, ctx: TranslationContext) -> list[TranslationUnit]:
    """Emit the sketch in the style indicated by ``ctx.style``.

    Two flavours:
      * ``algebra`` (default): ``var = Sketch() + plane * (Circle(r) -
        make_face(profile))`` — the historical, value-style emit.
      * ``builder``: ``with BuildSketch(plane) as var: Circle(r);
        make_face(mode=Mode.SUBTRACT)`` (etc.) followed by
        ``var = var.sketch`` so downstream extrude / revolve calls
        can use the same ``var`` name unchanged.
    """
    if getattr(ctx, "style", "algebra") == "builder":
        return _translate_sketch_builder(sketch, ctx)
    return _translate_sketch_algebra(sketch, ctx)


def _translate_sketch_algebra(sketch, ctx: TranslationContext) -> list[TranslationUnit]:
    from .sketch_snap import snap_geometry

    raw_geometry = [
        (i, g) for i, g in enumerate(sketch.Geometry)
        if not _is_construction(sketch, i)
    ]
    unsupported_kinds = {
        type(g).__name__ for _i, g in raw_geometry
    } - _SUPPORTED_KINDS
    if unsupported_kinds:
        raise UnsupportedFeatureError(
            sketch.TypeId,
            f"{sketch.Label} (unsupported geometry kinds: {sorted(unsupported_kinds)})",
        )

    # Coherent snap pass (#43): rebuild Arcs / Circles whose anchor params
    # are near round values, recomputing the sweep extent so wire-closure
    # is preserved. Lines pass through unchanged.
    snapped_only = snap_geometry([g for _i, g in raw_geometry])
    geometry = list(zip([i for i, _ in raw_geometry], snapped_only))

    circles = [g for _i, g in geometry if type(g).__name__ == "Circle"]
    ellipses = [g for _i, g in geometry if type(g).__name__ == "Ellipse"]
    chainable = [
        g for _i, g in geometry
        if type(g).__name__ in _CHAINABLE_KINDS
    ]

    if not circles and not ellipses and not chainable:
        raise UnsupportedFeatureError(
            sketch.TypeId, f"{sketch.Label} (empty sketch — no geometry)"
        )

    edge_loops = _chain_loops(chainable) if chainable else []

    # Uniform loop list with area, interior point, containment test, and
    # emitted expression. ``curve_expr`` (when present) is the 1D curve
    # composition that will be hoisted to a named variable; ``expr`` is the
    # final per-loop face expression used in the face composition below.
    loops: list[dict] = []
    for chain in edge_loops:
        curve_expr, imp = _emit_chain_curve(chain)
        loops.append({
            "area": _chain_area(chain),
            "interior": _interior_point_of_chain(chain),
            "contains": lambda p, c=chain: _chain_contains_point(c, p),
            "curve_expr": curve_expr,
            "expr": None,  # filled in once we know the hoisted variable name
            "imports": imp | {"make_face"},
        })
    for c in circles:
        expr, imp = _emit_circle(c)
        loops.append({
            "area": _circle_area(c),
            "interior": _interior_point_of_circle(c),
            "contains": lambda p, c=c: _circle_contains_point(c, p),
            "curve_expr": None,
            "expr": expr,
            "imports": imp,
        })
    for e in ellipses:
        expr, imp = _emit_ellipse(e)
        loops.append({
            "area": _ellipse_area(e),
            "interior": _interior_point_of_ellipse(e),
            "contains": lambda p, e=e: _ellipse_contains_point(e, p),
            "curve_expr": None,
            "expr": expr,
            "imports": imp,
        })

    # Classification by area-descending parent lookup. Two loops at the
    # same area can't strictly contain each other, so they end up siblings
    # — that handles disjoint same-size holes (cabin_flashlight) correctly,
    # while concentric circles (door_rod) still nest properly because the
    # outer is strictly larger.
    by_area_desc = sorted(loops, key=lambda L: -L["area"])
    for i, L in enumerate(by_area_desc):
        depth = 0
        for j in range(i):  # only look at strictly larger loops
            if by_area_desc[j]["contains"](L["interior"]):
                depth = by_area_desc[j]["depth"] + 1
                break
        L["depth"] = depth
        L["sign"] = +1 if depth % 2 == 0 else -1

    imports: set[str] = set()
    for L in loops:
        imports.update(L["imports"])

    positives = [L for L in loops if L["sign"] > 0]
    negatives = [L for L in loops if L["sign"] < 0]
    if not positives:
        raise UnsupportedFeatureError(
            sketch.TypeId,
            f"{sketch.Label} (no top-level positive loop)",
        )

    var = sketch.Name

    # Hoist each chain-loop's 1D curve into its own named variable, then
    # wrap with make_face for the face composition. Circles already produce
    # closed faces directly and stay inline.
    pre_lines: list[str] = []
    chain_loops = [L for L in loops if L["curve_expr"] is not None]
    if len(chain_loops) == 1:
        # Single chain loop — name it ``<sketch>_profile`` for readability.
        L = chain_loops[0]
        curve_var = f"{var}_profile"
        pre_lines.append(f"{curve_var} = {L['curve_expr']}")
        L["expr"] = f"make_face({curve_var})"
    else:
        # Multiple chain loops — disambiguate by index.
        for i, L in enumerate(chain_loops):
            curve_var = f"{var}_loop_{i}"
            pre_lines.append(f"{curve_var} = {L['curve_expr']}")
            L["expr"] = f"make_face({curve_var})"

    pos_expr = " + ".join(L["expr"] for L in positives)
    if len(positives) > 1:
        pos_expr = f"({pos_expr})"
    if negatives:
        face_expr = pos_expr + " - " + " - ".join(L["expr"] for L in negatives)
    else:
        face_expr = pos_expr

    plane = _plane_expr(sketch.Placement)
    if plane is not None:
        # Wrap with ``Sketch() + ...`` so mypy can resolve the type. The bare
        # ``Plane * <face>`` operator's stub returns a union including Plane
        # itself, which downstream extrude/revolve calls then reject under
        # strict typing -- even though the runtime result is a Sketch. Adding
        # ``Sketch() +`` produces the same geometry and types correctly.
        imports.add("Plane")
        imports.add("Sketch")
        full_expr = f"Sketch() + {plane} * ({face_expr})"
    else:
        full_expr = face_expr

    n_lines = sum(1 for g in sketch.Geometry if type(g).__name__ == "LineSegment")
    n_arcs = sum(1 for g in sketch.Geometry if type(g).__name__ == "ArcOfCircle")
    n_circles = len(circles)
    n_ellipses = len(ellipses)
    parts = []
    if n_lines:
        parts.append(f"{n_lines} line{'s' if n_lines != 1 else ''}")
    if n_arcs:
        parts.append(f"{n_arcs} arc{'s' if n_arcs != 1 else ''}")
    if n_circles:
        parts.append(f"{n_circles} circle{'s' if n_circles != 1 else ''}")
    if n_ellipses:
        parts.append(f"{n_ellipses} ellipse{'s' if n_ellipses != 1 else ''}")
    summary = ", ".join(parts)

    unit = TranslationUnit(
        var_name=var,
        label=sketch.Label,
        imports=imports,
        lines=[*pre_lines, f"{var} = {full_expr}"],
        comment=f"Sketcher::SketchObject {sketch.Label!r}: {summary} ({len(loops)} loops)",
    )
    ctx.add_step(
        feature_type="sketch",
        feature_name=sketch.Name,
        renamed_from_default=(sketch.Label != sketch.Name),
        build123d_code=unit.lines[0],
        properties=None,  # sketches are 2D — no volume/MOI
    )
    return [unit]


# ---------------------------------------------------------------------------
# Builder-mode sketch emit (#78)
# ---------------------------------------------------------------------------


def _emit_circle_builder(g, mode: str | None = None) -> tuple[str, set[str]]:
    """Render a Circle inside a ``with BuildSketch():`` block.

    Returns either ``Circle(r)`` or ``with Locations((cx, cy)): Circle(r)`` —
    BuildSketch's algebra-like translation doesn't compose ``Pos`` directly
    inside the context, so off-origin circles need a Locations wrapper.
    """
    cx, cy = g.Center.x, g.Center.y
    r = g.Radius
    mode_arg = f", mode=Mode.{mode}" if mode else ""
    if abs(cx) < _TOL and abs(cy) < _TOL:
        return f"Circle({_fmt(r)}{mode_arg})", {"Circle"}
    return (
        f"with Locations(({_fmt(cx)}, {_fmt(cy)})):\n"
        f"    Circle({_fmt(r)}{mode_arg})",
        {"Circle", "Locations"},
    )


def _emit_ellipse_builder(g, mode: str | None = None) -> tuple[str, set[str]]:
    """Render an Ellipse inside a BuildSketch block, with optional Mode."""
    cx, cy = g.Center.x, g.Center.y
    major, minor = g.MajorRadius, g.MinorRadius
    angle_deg = math.degrees(g.AngleXU)
    mode_arg = f", mode=Mode.{mode}" if mode else ""
    if abs(cx) < _TOL and abs(cy) < _TOL and abs(angle_deg) < _TOL:
        return f"Ellipse({_fmt(major)}, {_fmt(minor)}{mode_arg})", {"Ellipse"}
    parts = []
    imports: set[str] = {"Ellipse", "Locations"}
    if abs(cx) > _TOL or abs(cy) > _TOL:
        parts.append(f"Locations(({_fmt(cx)}, {_fmt(cy)}))")
    if abs(angle_deg) > _TOL:
        parts.append(f"Rot(Z={_fmt(angle_deg)})")
        imports.add("Rot")
    # build123d's BuildSketch doesn't compose ``Pos * Rot * Ellipse`` directly;
    # nest Locations contexts instead. For simplicity at this stage, fall back
    # to algebra-style placement and rely on make_face semantics.
    if abs(angle_deg) > _TOL:
        # Algebra-style placement, kept inside the BuildSketch as a side
        # construction via the ``add`` mode. Rare path; ellipse-with-angle
        # in a builder-mode sketch hits this only when the user really did
        # rotate one. For now, emit a note and fall back to plain Ellipse.
        return (
            f"Ellipse({_fmt(major)}, {_fmt(minor)}{mode_arg})  "
            f"# WARNING: angle={_fmt(angle_deg)}° at ({_fmt(cx)}, {_fmt(cy)}) "
            f"not applied — builder-mode rotated Ellipse not yet supported",
            imports,
        )
    return (
        f"with Locations(({_fmt(cx)}, {_fmt(cy)})):\n"
        f"    Ellipse({_fmt(major)}, {_fmt(minor)}{mode_arg})",
        imports,
    )


def _translate_sketch_builder(sketch, ctx: TranslationContext) -> list[TranslationUnit]:
    """Emit a sketch as a ``with BuildSketch(plane) as <var>:`` block.

    Reuses the loop classification (positive vs negative) computed by the
    algebra-mode path, but writes each loop as a build123d builder-mode
    construction inside the BuildSketch context, with ``mode=Mode.SUBTRACT``
    on negatives. After the context exits, rebinds the var to ``var.sketch``
    so downstream extrude / revolve translators reference the same name as
    the algebra-mode emit.
    """
    from .sketch_snap import snap_geometry

    raw_geometry = [
        (i, g) for i, g in enumerate(sketch.Geometry)
        if not _is_construction(sketch, i)
    ]
    unsupported_kinds = {
        type(g).__name__ for _i, g in raw_geometry
    } - _SUPPORTED_KINDS
    if unsupported_kinds:
        raise UnsupportedFeatureError(
            sketch.TypeId,
            f"{sketch.Label} (unsupported geometry kinds: {sorted(unsupported_kinds)})",
        )

    # Coherent snap pass (#43); see _translate_sketch_algebra for the why.
    snapped_only = snap_geometry([g for _i, g in raw_geometry])
    geometry = list(zip([i for i, _ in raw_geometry], snapped_only))

    circles = [g for _i, g in geometry if type(g).__name__ == "Circle"]
    ellipses = [g for _i, g in geometry if type(g).__name__ == "Ellipse"]
    chainable = [
        g for _i, g in geometry
        if type(g).__name__ in _CHAINABLE_KINDS
    ]
    if not circles and not ellipses and not chainable:
        raise UnsupportedFeatureError(
            sketch.TypeId, f"{sketch.Label} (empty sketch — no geometry)"
        )

    edge_loops = _chain_loops(chainable) if chainable else []

    # Same classification as algebra mode: each loop gets area / interior /
    # containment-test / curve_expr. Then sort by area descending to
    # determine positive / negative depth.
    loops: list[dict] = []
    for chain in edge_loops:
        curve_expr, imp = _emit_chain_curve(chain)
        loops.append({
            "area": _chain_area(chain),
            "interior": _interior_point_of_chain(chain),
            "contains": lambda p, c=chain: _chain_contains_point(c, p),
            "kind": "chain",
            "curve_expr": curve_expr,
            "geom": None,
            "imports": imp,
        })
    for c in circles:
        loops.append({
            "area": _circle_area(c),
            "interior": _interior_point_of_circle(c),
            "contains": lambda p, c=c: _circle_contains_point(c, p),
            "kind": "circle",
            "curve_expr": None,
            "geom": c,
            "imports": set(),
        })
    for e in ellipses:
        loops.append({
            "area": _ellipse_area(e),
            "interior": _interior_point_of_ellipse(e),
            "contains": lambda p, e=e: _ellipse_contains_point(e, p),
            "kind": "ellipse",
            "curve_expr": None,
            "geom": e,
            "imports": set(),
        })

    by_area_desc = sorted(loops, key=lambda L: -L["area"])
    for i, L in enumerate(by_area_desc):
        depth = 0
        for j in range(i):
            if by_area_desc[j]["contains"](L["interior"]):
                depth = by_area_desc[j]["depth"] + 1
                break
        L["depth"] = depth
        L["sign"] = +1 if depth % 2 == 0 else -1

    imports: set[str] = {"BuildSketch"}
    for L in loops:
        imports.update(L["imports"])

    # No positive loop = degenerate sketch.
    if not any(L["sign"] > 0 for L in loops):
        raise UnsupportedFeatureError(
            sketch.TypeId,
            f"{sketch.Label} (no top-level positive loop)",
        )

    # ``Mode`` is only needed when at least one loop is a negative — added
    # below once we know we'll emit ``mode=Mode.SUBTRACT``.
    has_subtract = any(L["sign"] < 0 for L in loops)
    if has_subtract:
        imports.add("Mode")

    plane = _plane_expr(sketch.Placement)
    if plane is not None:
        imports.add("Plane")
        header = f"with BuildSketch({plane}) as {sketch.Name}:"
    else:
        header = f"with BuildSketch() as {sketch.Name}:"

    body_lines: list[str] = []
    # Emit in source order (positives first, then negatives). Negatives use
    # Mode.SUBTRACT so they carve into the prior content.
    ordered = [L for L in loops if L["sign"] > 0] + [L for L in loops if L["sign"] < 0]
    chain_index = 0
    for L in ordered:
        mode = None if L["sign"] > 0 else "SUBTRACT"
        if L["kind"] == "circle":
            expr, imp = _emit_circle_builder(L["geom"], mode=mode)
            imports.update(imp)
            for line in expr.split("\n"):
                body_lines.append(f"    {line}")
        elif L["kind"] == "ellipse":
            expr, imp = _emit_ellipse_builder(L["geom"], mode=mode)
            imports.update(imp)
            for line in expr.split("\n"):
                body_lines.append(f"    {line}")
        else:  # chain
            curve_var = (
                f"{sketch.Name}_profile" if chain_index == 0
                and sum(1 for x in loops if x["kind"] == "chain") == 1
                else f"{sketch.Name}_loop_{chain_index}"
            )
            chain_index += 1
            mode_arg = ", mode=Mode.SUBTRACT" if mode else ""
            body_lines.append(f"    with BuildLine() as {curve_var}:")
            body_lines.append(f"        {L['curve_expr']}")
            body_lines.append(f"    make_face({mode_arg.lstrip(', ')})" if mode_arg
                              else "    make_face()")
            imports.add("BuildLine")
            imports.add("make_face")

    # After the with-block, rebind so downstream code uses ``<var>`` (the
    # sketch face) rather than ``<var>.sketch``.
    body_lines.append(f"{sketch.Name} = {sketch.Name}.sketch")

    n_lines = sum(1 for g in sketch.Geometry if type(g).__name__ == "LineSegment")
    n_arcs = sum(1 for g in sketch.Geometry if type(g).__name__ == "ArcOfCircle")
    parts = []
    if n_lines:
        parts.append(f"{n_lines} line{'s' if n_lines != 1 else ''}")
    if n_arcs:
        parts.append(f"{n_arcs} arc{'s' if n_arcs != 1 else ''}")
    if len(circles):
        parts.append(f"{len(circles)} circle{'s' if len(circles) != 1 else ''}")
    if len(ellipses):
        parts.append(f"{len(ellipses)} ellipse{'s' if len(ellipses) != 1 else ''}")
    summary = ", ".join(parts)

    unit = TranslationUnit(
        var_name=sketch.Name,
        label=sketch.Label,
        imports=imports,
        lines=[header, *body_lines],
        comment=f"Sketcher::SketchObject {sketch.Label!r}: {summary} ({len(loops)} loops)",
    )
    ctx.add_step(
        feature_type="sketch",
        feature_name=sketch.Name,
        renamed_from_default=(sketch.Label != sketch.Name),
        build123d_code="\n".join(unit.lines),
        properties=None,
    )
    return [unit]
