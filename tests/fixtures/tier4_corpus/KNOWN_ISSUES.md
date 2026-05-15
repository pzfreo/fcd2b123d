# tier4_corpus — known issues

Sampled at seed `613` from the 211 in-scope Parts Library files where:
- `extension_types` is empty,
- `max_tier_required` is in `{1, 2, 3, 4, 6}`, and
- the file uses at least one of `PartDesign::LinearPattern`, `PartDesign::PolarPattern`, or `PartDesign::Mirrored`.

30 sampled → 29 retained (1 dropped at snapshot time; see below).
2 pass end-to-end (`door-brasket`, `door-wall`). The other 27 are excluded
from `test_translator_corpus.py` until the limit they reveal is addressed.

## Pass / fail breakdown

| Category | Count | Notes |
|---|---:|---|
| **PASS** | 2 | `door-brasket`, `door-wall` — clean tier-2 + Mirrored with Body Origin plane |
| PartDesign::Groove (tier-2 gap) | 11 | All sprockets use `Groove` to cut tooth profiles |
| Mirrored with non-origin Plane (tier-4 limit) | 5 | MirrorPlane references a DatumPlane, not a Body Origin plane |
| Atomic (body-less) pattern (tier-4 gap) | 3 | Translator dispatch for top-level `Mirrored` / `PolarPattern` not wired up |
| Pad/Pocket Type beyond Length (tier-2 limit) | 3 | `TwoLengths` Pad; `UpToFirst` / `UpToFace` Pocket |
| Pattern Original Type='ThroughAll' (tier-4 limit) | 1 | `door-hinge` |
| PolarPattern multi-Original (tier-4 limit) | 1 | `LMXXXUU` — 2 Originals |
| PartDesign::Draft (tier-3 gap) | 1 | `Water_tank_500L_flat` |
| Part::Helix (tier-2 gap) | 1 | `Beam-coupling-5mm-5mm` |
| Disconnected sketch geometry | 1 | `45x45_mm_` |

The high Groove count (11/29 = 38%) is not a tier-4 finding — the random
sample happened to pull eight sprockets, and `Groove` is how FreeCAD models
sprocket tooth profiles. It's also the most-occurring tier-2-and-below
unimplemented feature surfaced by *any* corpus batch to date.

The five Mirror-via-DatumPlane failures (DA-XXX, Battery, Fan support) are
the most-actionable tier-4 follow-up: many real Parts Library files mirror
across a `PartDesign::Plane` rather than one of the Body's three origin
planes. Adding that support would convert ~17% of this batch.

## Dropped fixture

`Sprocket_ISO606_simplex_6x2_8_z76` — 76-tooth sprocket with a 76-copy
PolarPattern of a Groove. FreeCAD's `recompute()` runs past 5 minutes on
this file; not feasible to snapshot in CI. The fixture would have been
unsupported anyway (Groove + tier-4 multi-pattern combo).

## Per-fixture detail (failures only)

### PartDesign::Groove (tier-2 unimplemented)
- `Sprocket_ISO606_simplex_5x2_5_z33`
- `Sprocket_ISO606_simplex_6x2_8_z20`
- `Lego_basic_Doll`
- `Sprocket_ISO606_simplex_6x2_8_z36`
- `Plate_Wheel_simplex_8x3`
- `Sprocket_ISO606_simplex_6x2_8_z37`
- `Sprocket_ISO606_simplex_8x3_0_z26`
- `Sprocket_ISO606_simplex_8x3_0_z39`
- `Sprocket_ISO606_simplex_6x2_8_z40`
- `Sprocket_ISO606_simplex_8x3_0_z16`
- `Sprocket_ISO606_simplex_8x3_0_z36`

### Mirrored with non-origin Plane (tier-4 limit)
The MirrorPlane reference points at a `PartDesign::Plane` (DatumPlane)
that the file defines explicitly, not the Body Origin's XY/XZ/YZ.
v1 supports the three origin planes only.

- `DA-40-XXX-TCA` — `XZ_Plane001`
- `DA-63-XXX-TCA` — `XZ_Plane001`
- `DA-XX-XXX-TCA` — `XZ_Plane001`
- `Support_Fan_CoolMaster_70mmx70mm` — `YZ_Plane003`
- `Baterry_9_volts` — `XY_Plane001`

### Atomic (body-less) pattern (tier-4 gap)
The pattern feature lives at document level, not inside a `PartDesign::Body`.
The translator handles atomic Pad/Pocket/Revolution/Fillet/Chamfer this way
(`_translate_atomic_*` in `partdesign.py`) but lacks atomic-pattern handlers.

- `DIN471_CLASS_A_M28RetainingRings`
- `DIN471_CLASS_A_M26RetainingRings`
- `Jante-Arriere`

### Pattern Original outside Length convention (tier-4 limit)
- `door-hinge` — Pocket Original has `Type='ThroughAll'`; v1 supports `Type='Length'` only.
- `LMXXXUU` — PolarPattern has 2 Originals; v1 supports a single Original.

### Pad/Pocket Type beyond Length (tier-2 limit)
- `T8_housing_bracket` — Pad `Type='TwoLengths'`.
- `2020x50_V_slot_profile` — Pocket `Type='UpToFirst'`.
- `Base` — Pocket `Type='UpToFace'`.

### Other tier-2/3 gaps
- `Water_tank_500L_flat` — uses `PartDesign::Draft`.
- `Beam-coupling-5mm-5mm` — uses `Part::Helix`.
- `45x45_mm_` — disconnected sketch geometry (translator-side sketch limit).
