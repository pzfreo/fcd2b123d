"""Printed-in-place hinge test piece.

Two rectangular paddles joined by 3 interlocking knuckles. Print flat on the
bed with the hinge axis vertical (Z). Wiggle the paddles after printing to
break the clearance gaps free.

Sized for FDM at 0.2 mm layer height; increase clearance to 0.4 for less
well-tuned printers, decrease to 0.2 for resin.
"""

from pathlib import Path

from build123d import Align, Box, Compound, Cylinder, Location, export_step
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound

# ── parameters ───────────────────────────────────────────────────────────────
leaf_length = 50      # mm, each paddle extends this far from the pin axis
leaf_width  = 30      # mm, paddle width (perpendicular to hinge axis)
leaf_thick  = 4       # mm, paddle thickness
knuckle_r   = 5       # mm, outer radius of each knuckle
pin_r       = 1.8     # mm, pin radius (integral to leaf_b)
knuckle_h   = 8       # mm, height of each knuckle segment
clearance   = 0.3     # mm, gap between mating faces

# ── derived ───────────────────────────────────────────────────────────────────
# 3 knuckles: A-bottom, B-middle, A-top (leaf_a owns 2, leaf_b owns 1)
z_a0 = 0
z_b  = knuckle_h + clearance
z_a1 = 2 * knuckle_h + 2 * clearance
total_h = 3 * knuckle_h + 2 * clearance

# ── leaf_a: positive-X paddle + bottom and top knuckles ──────────────────────
leaf_a = Box(
    leaf_length, leaf_width, leaf_thick,
    align=(Align.MIN, Align.CENTER, Align.MIN),
).move(Location((0, 0, 0)))

for z0 in (z_a0, z_a1):
    knuckle = Cylinder(knuckle_r, knuckle_h, align=(Align.CENTER, Align.CENTER, Align.MIN))
    bore    = Cylinder(pin_r + clearance, knuckle_h, align=(Align.CENTER, Align.CENTER, Align.MIN))
    leaf_a  = leaf_a + knuckle.move(Location((0, 0, z0))) - bore.move(Location((0, 0, z0)))

# ── leaf_b: negative-X paddle + middle knuckle + integral pin ────────────────
leaf_b = Box(
    leaf_length, leaf_width, leaf_thick,
    align=(Align.MAX, Align.CENTER, Align.MIN),
).move(Location((0, 0, 0)))

knuckle_b = Cylinder(knuckle_r - clearance, knuckle_h, align=(Align.CENTER, Align.CENTER, Align.MIN))
leaf_b    = leaf_b + knuckle_b.move(Location((0, 0, z_b)))

# Pin runs the full height, attached to leaf_b, captured by leaf_a's bores
pin    = Cylinder(pin_r, total_h, align=(Align.CENTER, Align.CENTER, Align.MIN))
leaf_b = leaf_b + pin

_builder = BRep_Builder()
_occ = TopoDS_Compound()
_builder.MakeCompound(_occ)
for _solid in [*leaf_a.solids(), *leaf_b.solids()]:
    _builder.Add(_occ, _solid.wrapped)

result = Compound(_occ)
export_step(result, Path(__file__).with_suffix(".step"))
