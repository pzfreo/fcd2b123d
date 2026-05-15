"""Hausdorff-distance comparison: catches geometric differences that the
four-scalar invariants (volume / area / COM / principal MOI) miss.

Volume + surface area + COM + sorted principal MOI eigenvalues are all
preserved under *reflection*: a shape and its mirror have the same values.
They can also coincidentally agree across substantively-different shapes
with the same overall mass and inertia distribution. ADR-0004 documented
this and committed to Hausdorff as the paranoid-case backstop.

Strategy: each snapshot can carry a point cloud sampled from FreeCAD's
tessellated mesh. At test time the build123d shape is tessellated the same
way; Hausdorff distance between the two point sets is compared to a
magnitude-scaled tolerance. Mirrored or topologically-different geometry
shows up as a large Hausdorff even when properties agree.

The check runs by default whenever a sidecar point cloud is present. Set
FCSTD2B123D_HAUSDORFF_SKIP=1 to opt out (e.g. for a perf-sensitive lane);
overhead is ~4% otherwise.
"""

from __future__ import annotations

import math


def hausdorff_distance(points_a, points_b) -> float:
    """Hausdorff distance = max(h(A→B), h(B→A)) where h(A→B) = max_a min_b ||a-b||."""
    import numpy as np

    a = np.asarray(points_a, dtype=float)
    b = np.asarray(points_b, dtype=float)
    if a.size == 0 or b.size == 0:
        return float("inf")

    # Pairwise squared distances via broadcasting. O(|A|*|B|) memory.
    # For point clouds of ~few thousand points, this fits in ~100 MB.
    diff = a[:, None, :] - b[None, :, :]
    sq = (diff * diff).sum(axis=2)

    # h(A→B) = max over a of min over b
    h_ab = math.sqrt(sq.min(axis=1).max())
    h_ba = math.sqrt(sq.min(axis=0).max())
    return max(h_ab, h_ba)


def bbox_diagonal(points) -> float:
    import numpy as np

    p = np.asarray(points, dtype=float)
    extents = p.max(axis=0) - p.min(axis=0)
    return float(math.sqrt((extents * extents).sum()))
