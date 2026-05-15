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

Unsupported in tier-2: splines, ellipses, B-splines, helix, open or
multi-component sketches that aren't simple outer-minus-inners.
"""

from __future__ import annotations

import math

from .context import TranslationContext
from .emitter import TranslationUnit
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

    Builds Plane(origin=..., x_dir=..., z_dir=...) by applying the FreeCAD
    rotation to (1,0,0) and (0,0,1) — the sketch's local X and Z (normal).
    """
    if _is_xy_plane(placement):
        return None
    import FreeCAD  # lazy
    rot = placement.Rotation
    x_dir = rot.multVec(FreeCAD.Vector(1, 0, 0))
    z_dir = rot.multVec(FreeCAD.Vector(0, 0, 1))
    origin = placement.Base
    return (
        f"Plane(origin=({_fmt(origin.x)}, {_fmt(origin.y)}, {_fmt(origin.z)}), "
        f"x_dir=({_fmt(x_dir.x)}, {_fmt(x_dir.y)}, {_fmt(x_dir.z)}), "
        f"z_dir=({_fmt(z_dir.x)}, {_fmt(z_dir.y)}, {_fmt(z_dir.z)}))"
    )


def _fmt(v: float) -> str:
    """Tidy float representation — strip negative zero, clamp tiny near-integers."""
    if abs(v) < 1e-12:
        return "0"
    if abs(v - round(v)) < 1e-12:
        return f"{int(round(v))}"
    return f"{v}"


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
    return (
        f"CenterArc(center=({_fmt(cx)}, {_fmt(cy)}), radius={_fmt(r)}, "
        f"start_angle={_fmt(start_angle)}, arc_size={_fmt(arc_size)})"
    )


def _emit_chain_loop(chain: list) -> tuple[str, set[str]]:
    """Render a closed chain of (geom, reversed) entries as a build123d face expression."""
    edges_expr = []
    imports: set[str] = {"Line"}
    for g, rev in chain:
        kind = type(g).__name__
        if kind == "LineSegment":
            s, e = _segment_endpoints(g)
            if rev:
                s, e = e, s
            edges_expr.append(_emit_line(s, e))
        elif kind == "ArcOfCircle":
            edges_expr.append(_emit_arc(g, rev))
            imports.add("CenterArc")
        else:
            raise UnsupportedFeatureError(
                "Sketcher::SketchObject",
                f"(unsupported geometry kind {kind!r} in loop)",
            )
    expr = " + ".join(edges_expr)
    return f"make_face({expr})", imports | {"make_face"}


def _emit_circle(g) -> tuple[str, set[str]]:
    """Render a Part.GeomCircle as a build123d Circle face, translated if not at origin."""
    cx, cy = g.Center.x, g.Center.y
    r = g.Radius
    imports: set[str] = {"Circle"}
    if abs(cx) < _TOL and abs(cy) < _TOL:
        return f"Circle({_fmt(r)})", imports
    imports.add("Pos")
    return f"(Pos({_fmt(cx)}, {_fmt(cy)}) * Circle({_fmt(r)}))", imports


# ---------------------------------------------------------------------------
# Loop area (for outer-vs-inner classification)
# ---------------------------------------------------------------------------


def _sample_chain_points(chain: list) -> list[tuple[float, float]]:
    """Sample a chained loop into a polygon. Lines exact; arcs at 16 steps."""
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


def _chain_contains_point(chain: list, p: tuple[float, float]) -> bool:
    return _polygon_contains(_sample_chain_points(chain), p)


def _circle_contains_point(g, p: tuple[float, float]) -> bool:
    dx = p[0] - g.Center.x
    dy = p[1] - g.Center.y
    return dx * dx + dy * dy < g.Radius * g.Radius


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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


_SUPPORTED_KINDS = {"LineSegment", "Circle", "ArcOfCircle"}


def translate_sketch(sketch, ctx: TranslationContext) -> list[TranslationUnit]:
    unsupported_kinds = {
        type(g).__name__ for g in sketch.Geometry
    } - _SUPPORTED_KINDS
    if unsupported_kinds:
        raise UnsupportedFeatureError(
            sketch.TypeId,
            f"{sketch.Label} (unsupported geometry kinds: {sorted(unsupported_kinds)})",
        )

    circles = [g for g in sketch.Geometry if type(g).__name__ == "Circle"]
    chainable = [
        g for g in sketch.Geometry if type(g).__name__ in {"LineSegment", "ArcOfCircle"}
    ]

    if not circles and not chainable:
        raise UnsupportedFeatureError(
            sketch.TypeId, f"{sketch.Label} (empty sketch — no geometry)"
        )

    edge_loops = _chain_loops(chainable) if chainable else []

    # Uniform loop list with area, interior point, containment test, and
    # emitted expression.
    loops: list[dict] = []
    for chain in edge_loops:
        expr, imp = _emit_chain_loop(chain)
        loops.append({
            "area": _chain_area(chain),
            "interior": _interior_point_of_chain(chain),
            "contains": lambda p, c=chain: _chain_contains_point(c, p),
            "expr": expr,
            "imports": imp,
        })
    for c in circles:
        expr, imp = _emit_circle(c)
        loops.append({
            "area": _circle_area(c),
            "interior": _interior_point_of_circle(c),
            "contains": lambda p, c=c: _circle_contains_point(c, p),
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
    pos_expr = " + ".join(L["expr"] for L in positives)
    if len(positives) > 1:
        pos_expr = f"({pos_expr})"
    if negatives:
        face_expr = pos_expr + " - " + " - ".join(L["expr"] for L in negatives)
    else:
        face_expr = pos_expr

    plane = _plane_expr(sketch.Placement)
    if plane is not None:
        imports.add("Plane")
        full_expr = f"{plane} * ({face_expr})"
    else:
        full_expr = face_expr

    n_lines = sum(1 for g in sketch.Geometry if type(g).__name__ == "LineSegment")
    n_arcs = sum(1 for g in sketch.Geometry if type(g).__name__ == "ArcOfCircle")
    n_circles = len(circles)
    parts = []
    if n_lines:
        parts.append(f"{n_lines} line{'s' if n_lines != 1 else ''}")
    if n_arcs:
        parts.append(f"{n_arcs} arc{'s' if n_arcs != 1 else ''}")
    if n_circles:
        parts.append(f"{n_circles} circle{'s' if n_circles != 1 else ''}")
    summary = ", ".join(parts)

    unit = TranslationUnit(
        var_name=var,
        imports=imports,
        lines=[f"{var} = {full_expr}"],
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
