"""Sketcher::SketchObject translator.

Tier-2 v1 scope: line-segment polygons on the world XY plane. Anything else
(arcs, circles, splines, non-XY planes) raises UnsupportedFeatureError so we
fail loudly rather than emit subtly wrong geometry.

Strategy: read the sketch's post-solve concrete geometry (FreeCAD stores
the solved coordinates in sketch.Geometry after the constraint solver runs),
chain segments into an ordered point list, emit a build123d Polyline
wrapped in make_face.
"""

from __future__ import annotations

from .emitter import TranslationUnit
from .errors import UnsupportedFeatureError

_TOL = 1e-6


def _is_identity_xy_placement(p) -> bool:
    """True when sketch sits on the world XY plane with no rotation/translation."""
    if abs(p.Rotation.Angle) > _TOL:
        return False
    return all(abs(c) < _TOL for c in (p.Base.x, p.Base.y, p.Base.z))


def _chain_segments(segments: list[tuple[tuple[float, float], tuple[float, float]]]) -> list[tuple[float, float]]:
    """Order a bag of (start, end) segments into a closed point sequence.

    Returns the points in traversal order (not including the closing repeat).
    Raises UnsupportedFeatureError if the segments don't form a single closed
    loop within tolerance — multi-loop and open sketches are tier-2 v2.
    """
    if not segments:
        raise UnsupportedFeatureError(
            "Sketcher::SketchObject", "(empty sketch — no geometry to translate)"
        )

    remaining = list(segments)
    chain = [remaining[0][0], remaining[0][1]]
    remaining.pop(0)

    while remaining:
        tail = chain[-1]
        matched = False
        for i, (s, e) in enumerate(remaining):
            if abs(s[0] - tail[0]) < _TOL and abs(s[1] - tail[1]) < _TOL:
                chain.append(e)
                remaining.pop(i)
                matched = True
                break
            if abs(e[0] - tail[0]) < _TOL and abs(e[1] - tail[1]) < _TOL:
                chain.append(s)
                remaining.pop(i)
                matched = True
                break
        if not matched:
            raise UnsupportedFeatureError(
                "Sketcher::SketchObject",
                "(open or multi-loop sketch — only single closed loops in v1)",
            )

    # Closed? Drop the duplicated start.
    start, end = chain[0], chain[-1]
    if abs(start[0] - end[0]) < _TOL and abs(start[1] - end[1]) < _TOL:
        return chain[:-1]
    raise UnsupportedFeatureError(
        "Sketcher::SketchObject",
        "(sketch does not close — only single closed loops in v1)",
    )


def translate_sketch(sketch) -> list[TranslationUnit]:
    if not _is_identity_xy_placement(sketch.Placement):
        raise UnsupportedFeatureError(
            sketch.TypeId,
            f"{sketch.Label} (only world-XY-plane sketches in tier-2 v1)",
        )

    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for g in sketch.Geometry:
        kind = type(g).__name__
        if kind != "LineSegment":
            raise UnsupportedFeatureError(
                sketch.TypeId,
                f"{sketch.Label} (geometry kind {kind!r} not supported in tier-2 v1; "
                f"only line segments)",
            )
        segments.append(
            ((g.StartPoint.x, g.StartPoint.y), (g.EndPoint.x, g.EndPoint.y))
        )

    pts = _chain_segments(segments)
    pts_repr = ", ".join(f"({x}, {y})" for x, y in pts)
    var = sketch.Name
    return [
        TranslationUnit(
            var_name=var,
            imports={"Polyline", "make_face"},
            lines=[f"{var} = make_face(Polyline({pts_repr}, close=True))"],
            comment=f"Sketcher::SketchObject {sketch.Label!r}: {len(segments)} line segments",
        )
    ]
