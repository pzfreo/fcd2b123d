"""Coherent snap pass for sketch geometry (issue #43).

FreeCAD's constraint solver settles at near-round-but-not-exact coordinates:
arc centers at ``(54.999978668734606, 15)`` for a user-typed ``(55, 15)``,
arc start angles at ``270.00003491975656`` for a typed ``270``. The emit
reproduces these verbatim, which is honest but unreadable.

A naïve fix (snap each value independently) breaks BRep validity. Snapping
arc center to ``(55, 15)`` shifts the whole arc 22 µm in x, so the arc's
endpoints also shift 22 µm — opening a gap with the adjacent Line endpoint
at ``(55, -20)``. OCCT rejects the wire.

This module snaps **coherently**: for each Arc / Circle / Ellipse it tries
round candidates for the anchor parameters (center coords, start angle,
radius). Where a snap is accepted, the arc's endpoints are recomputed
from the snapped params, and the **adjacent Line endpoints** are mutated
to follow — so the wire stays continuous at the new positions.

The dispatcher in ``sketch.py`` calls :func:`snap_geometry` after
extracting the geometry list. Where snaps are accepted, the affected
items in the list are replaced with freshly-constructed (or in-place
mutated, for Lines) FreeCAD objects exposing the same attribute surface.
The rest of the emit code is unchanged.

Tolerances:

* ``SNAP_REL_TOL``: a candidate is acceptable if ``|original - candidate|``
  is below this fraction of ``max(1, |original|)``. Defaults to ``1e-4``
  — generous enough to catch 21 µm shifts on a ``55 mm`` coord, tight
  enough to avoid snapping values the user genuinely typed as
  ``12.5`` or ``1.5875``.
* ``COINCIDENT_TOL``: maximum distance between two endpoints for them to
  count as coincident (a vertex shared by both edges). Set to ``1e-4`` mm
  — broader than OCCT's vertex tolerance, narrow enough to never merge
  intentionally-separate vertices.
"""

from __future__ import annotations

import math


SNAP_REL_TOL = 1e-4
COINCIDENT_TOL = 1e-4


def _try_snap_scalar(v: float, candidates: list[float]) -> float | None:
    """Return the closest candidate within :data:`SNAP_REL_TOL`, or None."""
    best = None
    best_err = SNAP_REL_TOL
    for c in candidates:
        err = abs(v - c) / max(1.0, abs(v))
        if err < best_err:
            best = c
            best_err = err
    return best


def _coord_candidates(v: float) -> list[float]:
    """Round-value candidates for a length / coordinate.

    Restricted to integers and halves only — these are values a human
    really did type. Coarser fractions like ``0.1`` / ``0.01`` would
    also catch *geometrically-derived* coordinates that happen to lie
    near a 2-decimal value (e.g. a sprocket tooth root at
    ``27.087527…`` ≈ ``27.09``), incorrectly snapping them and breaking
    the part's true dimensions. If a user did type ``12.5`` it will be
    caught by the ``halves`` candidate; finer decimals stay untouched.
    """
    near_int = round(v)
    return [
        float(near_int),
        round(v * 2) / 2,
    ]


def _angle_candidates(deg: float) -> list[float]:
    """Round-value candidates for an angle in degrees.

    Angles wrap mod 360. Snap candidates are multiples of common
    sketcher-friendly angles. Both the raw value and its mod-360
    reduction are tried so e.g. ``270.00003`` finds the candidate
    ``270`` (not the candidate ``0`` from ``270.00003 % 360 = 270.00003 → 0``).
    """
    cands: list[float] = []
    for step in (90, 45, 30, 15, 5, 1):
        k = round(deg / step)
        cands.append(k * step)
    deg_mod = deg % 360
    for step in (90, 45, 30, 15, 5, 1):
        k = round(deg_mod / step)
        cands.append(k * step)
    return cands


def _try_snap_arc(g, FreeCAD, Part):
    """Attempt to snap an ArcOfCircle's anchor parameters.

    Returns ``(new_arc, old_start, old_end, new_start, new_end)`` if the
    snap is geometrically meaningful, else ``None``. The caller uses
    ``(old_end, new_end)`` (etc.) to propagate updates to adjacent Line
    endpoints.

    Anchor params snapped (in order): radius, center.y, center.x,
    start_angle. The order matters because some snaps depend on knowing
    the radius (e.g. checking whether old endpoints fall on the snapped
    circle). The end angle is always recomputed from the original
    end-point direction at the snapped radius.
    """
    cx, cy = g.Center.x, g.Center.y
    R = g.Radius
    fp_rad = g.FirstParameter
    lp_rad = g.LastParameter

    snapped_R = _try_snap_scalar(R, _coord_candidates(R))
    snapped_cx = _try_snap_scalar(cx, _coord_candidates(cx))
    snapped_cy = _try_snap_scalar(cy, _coord_candidates(cy))
    fp_deg = math.degrees(fp_rad)
    snapped_fp_deg = _try_snap_scalar(fp_deg, _angle_candidates(fp_deg))

    if (
        snapped_R is None
        and snapped_cx is None
        and snapped_cy is None
        and snapped_fp_deg is None
    ):
        return None

    new_cx = cx if snapped_cx is None else snapped_cx
    new_cy = cy if snapped_cy is None else snapped_cy
    new_R = R if snapped_R is None else snapped_R
    new_fp_rad = fp_rad if snapped_fp_deg is None else math.radians(snapped_fp_deg)

    # Recompute end angle so the arc passes through the original end
    # direction at the new radius. The endpoint will then sit on the
    # snapped circle in the same angular position relative to the new
    # center — its X/Y may shift by tens of µm if center moved.
    orig_end_x = g.EndPoint.x
    orig_end_y = g.EndPoint.y
    dx = orig_end_x - new_cx
    dy = orig_end_y - new_cy
    new_lp_rad_raw = math.atan2(dy, dx)
    sweep = (lp_rad - fp_rad) % (2 * math.pi)
    new_sweep = (new_lp_rad_raw - new_fp_rad) % (2 * math.pi)
    # Pick the new_sweep branch closest to the original sweep so we don't
    # invert direction or pick a near-full-turn alternative.
    while new_sweep < sweep - math.pi:
        new_sweep += 2 * math.pi
    while new_sweep > sweep + math.pi:
        new_sweep -= 2 * math.pi
    final_lp_rad = new_fp_rad + new_sweep

    axis = FreeCAD.Vector(0, 0, 1)
    new_circle = Part.Circle(FreeCAD.Vector(new_cx, new_cy, 0), axis, new_R)
    new_arc = Part.ArcOfCircle(new_circle, new_fp_rad, final_lp_rad)

    old_start = (g.StartPoint.x, g.StartPoint.y)
    old_end = (g.EndPoint.x, g.EndPoint.y)
    new_start = (new_arc.StartPoint.x, new_arc.StartPoint.y)
    new_end = (new_arc.EndPoint.x, new_arc.EndPoint.y)
    return new_arc, old_start, old_end, new_start, new_end


def _try_snap_circle(g, FreeCAD, Part):
    """Attempt to snap a Circle's center + radius. Circles are self-closing
    so there are no shared-vertex constraints to worry about.

    Returns the new Circle if any snap applied, else None.
    """
    cx, cy = g.Center.x, g.Center.y
    R = g.Radius
    snapped_cx = _try_snap_scalar(cx, _coord_candidates(cx))
    snapped_cy = _try_snap_scalar(cy, _coord_candidates(cy))
    snapped_R = _try_snap_scalar(R, _coord_candidates(R))
    if snapped_cx is None and snapped_cy is None and snapped_R is None:
        return None
    new_cx = cx if snapped_cx is None else snapped_cx
    new_cy = cy if snapped_cy is None else snapped_cy
    new_R = R if snapped_R is None else snapped_R
    axis = FreeCAD.Vector(0, 0, 1)
    return Part.Circle(FreeCAD.Vector(new_cx, new_cy, 0), axis, new_R)


def _is_close(p: tuple[float, float], q: tuple[float, float]) -> bool:
    return abs(p[0] - q[0]) < COINCIDENT_TOL and abs(p[1] - q[1]) < COINCIDENT_TOL


def _propagate_to_line(line, old_pt, new_pt, FreeCAD):
    """If ``line``'s StartPoint or EndPoint is at ``old_pt`` (within
    :data:`COINCIDENT_TOL`), mutate a copy of the line so that endpoint
    sits at ``new_pt`` exactly. Returns the (possibly-replaced) line.
    """
    sx, sy = line.StartPoint.x, line.StartPoint.y
    ex, ey = line.EndPoint.x, line.EndPoint.y
    new_line = line
    if _is_close((sx, sy), old_pt):
        new_line = new_line.copy() if new_line is line else new_line
        new_line.StartPoint = FreeCAD.Vector(new_pt[0], new_pt[1], 0)
    if _is_close((ex, ey), old_pt):
        new_line = new_line.copy() if new_line is line else new_line
        new_line.EndPoint = FreeCAD.Vector(new_pt[0], new_pt[1], 0)
    return new_line


def _arc_shares_vertex_with_curve(arc, geometry, exclude_idx: int) -> bool:
    """True if the arc's StartPoint or EndPoint is coincident with any
    other arc / BSpline endpoint (within sketch tolerance).

    Lines we can mutate freely; another curved primitive sharing the
    vertex would also need re-parameterising, which is beyond phase 1.
    """
    a_start = (arc.StartPoint.x, arc.StartPoint.y)
    a_end = (arc.EndPoint.x, arc.EndPoint.y)
    for j, other in enumerate(geometry):
        if j == exclude_idx:
            continue
        kind = type(other).__name__
        if kind in ("LineSegment", "Circle"):
            continue
        if not hasattr(other, "StartPoint"):
            continue
        s = (other.StartPoint.x, other.StartPoint.y)
        e = (other.EndPoint.x, other.EndPoint.y)
        for v in (s, e):
            if _is_close(v, a_start) or _is_close(v, a_end):
                return True
    return False


def snap_geometry(geometry: list) -> list:
    """Return a new geometry list with each Arc / Circle replaced by a snapped
    rebuild where the snap is geometrically meaningful, and any Line whose
    endpoint coincides with a shifted arc endpoint mutated to follow.

    Phase-1 constraint: arc snaps require that all adjacent edges sharing
    a vertex are LineSegments (which we can mutate). Arc-arc, arc-BSpline,
    or arc-ellipse adjacencies skip the snap to avoid breaking continuity
    we can't fix here.

    Pass-through for BSplines and Ellipses (phase-2 targets).
    """
    import FreeCAD
    import Part

    out: list = list(geometry)
    for i, g in enumerate(geometry):
        kind = type(g).__name__
        if kind == "ArcOfCircle":
            if _arc_shares_vertex_with_curve(g, geometry, i):
                continue
            result = _try_snap_arc(g, FreeCAD, Part)
            if result is None:
                continue
            new_arc, old_start, old_end, new_start, new_end = result
            out[i] = new_arc
            # Always propagate. Even sub-µm shifts can fail the chain-loop
            # detector's 1e-6 close-vertex check (see sketch.py:_TOL).
            for j, other in enumerate(out):
                if j == i or type(other).__name__ != "LineSegment":
                    continue
                out[j] = _propagate_to_line(out[j], old_start, new_start, FreeCAD)
                out[j] = _propagate_to_line(out[j], old_end, new_end, FreeCAD)
        elif kind == "Circle":
            new_circle = _try_snap_circle(g, FreeCAD, Part)
            if new_circle is not None:
                out[i] = new_circle
    return out
