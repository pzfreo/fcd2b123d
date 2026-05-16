"""Runtime helpers for generated build123d source.

When the translator is invoked with ``--shared-helpers``, the emit
imports these from ``fcstd2b123d.runtime`` instead of inlining
``_edges_at`` / ``_faces_at`` / ``_pattern_union`` / etc. at the top
of every generated file. For a consumer translating a directory of
many fixtures this avoids 20-40 lines of duplicated helper
definitions per file.

The helpers are runtime-only — they import ``build123d`` lazily, so
this module is cheap to import in any environment that already has
the translator's deps. They are the canonical implementations; the
inlined string versions in ``emitter.HELPER_DEFINITIONS`` are kept in
lockstep so the default ``--inline-helpers`` mode continues to
produce self-contained files.
"""

from __future__ import annotations

from typing import Any


def _edges_at(
    shape: Any,
    points: list[tuple[float, float, float]],
    tol: float = 1e-3,
) -> list:
    """Select edges whose midpoints match any of the target points.

    Used by translated Fillet / Chamfer features — FreeCAD references
    edges by index, the translator captures their world-frame midpoints
    from FreeCAD's evaluated BRep, and this helper finds the
    corresponding edges in build123d's BRep.
    """
    from build123d import Vector

    targets = [Vector(*p) for p in points]
    return [
        e
        for e in shape.edges()
        if any((e.position_at(0.5) - t).length < tol for t in targets)
    ]


def _faces_at(
    shape: Any,
    points: list[tuple[float, float, float]],
    tol: float = 1e-3,
) -> list:
    """Select faces whose centres match any of the target points.

    Companion to ``_edges_at`` for face-based features like Draft. The
    translator captures FreeCAD's referenced face centres in world
    frame and emits this lookup; build123d returns the matching faces
    of its own BRep so the operation re-targets correctly.
    """
    from build123d import Vector

    targets = [Vector(*p) for p in points]
    return [
        f for f in shape.faces() if any((f.center() - t).length < tol for t in targets)
    ]


def _pattern_union(base, *additions):
    """Boolean-union ``base`` with each addition via BuildPart.

    Chained ``+`` on build123d Part objects returns a Compound that
    does not fuse overlapping geometry — so for pattern features whose
    copies overlap (e.g. sprocket teeth meeting at the hub) the
    resulting volume is wrong. Routing through ``BuildPart.add()``
    invokes OCCT's robust boolean fusion.
    """
    from build123d import BuildPart, add

    with BuildPart() as _bp:
        add(base)
        for s in additions:
            add(s)
    return _bp.part


def _pattern_difference(base, *removals):
    """Boolean-subtract each removal from ``base`` via BuildPart.

    Mirror of ``_pattern_union`` for subtractive (Pocket Original)
    patterns. Chained ``-`` does *not* exhibit the same
    Compound-collapsing bug as ``+`` in current build123d, but using
    BuildPart for both keeps the emit symmetric and future-proofs
    against the inverse issue.
    """
    from build123d import BuildPart, Mode, add

    with BuildPart() as _bp:
        add(base)
        for s in removals:
            add(s, mode=Mode.SUBTRACT)
    return _bp.part


def _pattern_intersection(base, *others):
    """Boolean-intersect ``base`` with each other shape via BuildPart.

    Used by Part::Common / Part::MultiCommon translation. The ``&``
    operator returns a Compound that doesn't always behave correctly
    for multi-shape intersections; BuildPart with Mode.INTERSECT routes
    through OCCT's robust intersection.
    """
    from build123d import BuildPart, Mode, add

    with BuildPart() as _bp:
        add(base)
        for s in others:
            add(s, mode=Mode.INTERSECT)
    return _bp.part


__all__ = [
    "_edges_at",
    "_faces_at",
    "_pattern_union",
    "_pattern_difference",
    "_pattern_intersection",
]
