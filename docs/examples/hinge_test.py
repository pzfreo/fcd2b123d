"""Print-in-place hinge test piece.

Two flat paddles joined by 3 interlocking knuckles along the Y axis.
Print flat on the bed (hinge open, 180°) — the hinge axis runs parallel
to the bed. After printing, gently flex the leaves to break the 0.4 mm
clearance gaps free.

The layout matches the cross-section of hinge-03.fcstd (piano-hinge
profile: 5 mm-radius knuckle with a 5 mm-thick paddle tangent to its
bottom) but splits the hinge into 3 alternating knuckle segments so it
can be printed as a single assembly.

Sized for FDM at 0.2 mm layer height. Increase ``clear`` to 0.5 for
less well-tuned printers; decrease to 0.2 for resin.
"""

from pathlib import Path

from build123d import (
    Align,
    Box,
    Compound,
    Cylinder,
    Location,
    export_step,
    export_stl,
)
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound

# ── parameters ───────────────────────────────────────────────────────────────
paddle_len = 25     # mm, X extent of each paddle from the hinge axis
R          = 5      # mm, knuckle outer radius
leaf_thick = 4      # mm, paddle thickness (Z)
pin_r      = 1.6    # mm, integral pin radius
clear      = 0.4    # mm, FDM clearance (axial gap + radial pin gap)
knuckle_h  = 10     # mm, Y length of each knuckle segment

# ── derived ──────────────────────────────────────────────────────────────────
notch_pad = clear                # extra gap around opposing knuckles in notches
seam_gap  = 2 * clear            # gap at X=0 between the two paddles
y_a0      = -(knuckle_h + clear) # centre of A-back knuckle
y_b       = 0.0                  # centre of B-middle knuckle
y_a1      =  knuckle_h + clear   # centre of A-front knuckle
total_y   = 3 * knuckle_h + 2 * clear

# ── leaf_a: continuous +X paddle + 2 knuckles + bores, with notch for B ──────
paddle_a = Box(
    R + paddle_len - seam_gap / 2, total_y, leaf_thick,
    align=(Align.MIN, Align.CENTER, Align.MIN),
).move(Location((seam_gap / 2, 0, -leaf_thick)))
leaf_a = paddle_a
for yc in (y_a0, y_a1):
    k = Cylinder(R, knuckle_h, rotation=(90, 0, 0)).move(Location((0, yc, 0)))
    b = Cylinder(pin_r + clear, knuckle_h + 0.2, rotation=(90, 0, 0)).move(
        Location((0, yc, 0))
    )
    leaf_a = leaf_a + k - b
leaf_a = leaf_a - Box(
    2 * R + 2 * notch_pad, knuckle_h + 2 * notch_pad, 2 * R + 2 * notch_pad,
    align=(Align.CENTER, Align.CENTER, Align.CENTER),
).move(Location((0, y_b, 0)))

# ── leaf_b: continuous -X paddle + middle knuckle + pin, notches for A ───────
paddle_b = Box(
    R + paddle_len - seam_gap / 2, total_y, leaf_thick,
    align=(Align.MAX, Align.CENTER, Align.MIN),
).move(Location((-seam_gap / 2, 0, -leaf_thick)))
leaf_b = paddle_b
leaf_b = leaf_b + Cylinder(R, knuckle_h, rotation=(90, 0, 0)).move(
    Location((0, y_b, 0))
)
# Pin runs full Y (minus a tiny end clearance so it doesn't poke out)
leaf_b = leaf_b + Cylinder(pin_r, total_y - 2 * clear, rotation=(90, 0, 0))
for yc in (y_a0, y_a1):
    leaf_b = leaf_b - Box(
        2 * R + 2 * notch_pad, knuckle_h + 2 * notch_pad, 2 * R + 2 * notch_pad,
        align=(Align.CENTER, Align.CENTER, Align.CENTER),
    ).move(Location((0, yc, 0)))

# Wrap both leaves in a single Compound for export (multi-body STL/STEP).
_builder = BRep_Builder()
_occ = TopoDS_Compound()
_builder.MakeCompound(_occ)
for _solid in [*leaf_a.solids(), *leaf_b.solids()]:
    _builder.Add(_occ, _solid.wrapped)
result = Compound(_occ)

_stem = Path(__file__).with_suffix("")
export_step(result, str(_stem) + ".step")
export_stl(result, str(_stem) + ".stl")
