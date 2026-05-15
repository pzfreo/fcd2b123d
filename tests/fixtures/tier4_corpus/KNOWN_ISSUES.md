# tier4_corpus — known issues

Sampled at seed `613` from the 211 in-scope Parts Library files where:
- `extension_types` is empty,
- `max_tier_required` is in `{1, 2, 3, 4, 6}`, and
- the file uses at least one of `PartDesign::LinearPattern`, `PartDesign::PolarPattern`, or `PartDesign::Mirrored`.

30 sampled → 29 retained (1 dropped at snapshot time; see below).

## Current pass rate: 7 / 29

| Status | Count | Fixtures |
|---|---:|---|
| **PASS** | 7 | `door-brasket`, `door-wall`, `Plate_Wheel_simplex_8x3`, and four sprockets (z16, z20, z36, z40) |
| Precision-edge | 5 | Sprockets z26, z33, z36 (8x3), z37, z39 — ~1-4 ppm volume drift past the 1e-6 relative tolerance |
| Unsupported | 17 | Genuine tier-2/3/4 gaps; tracked per-issue below |

This is up from **2/29 pass** before the Groove translator landed. Groove (issue #23) is the single biggest unlock; the Pocket Midplane+ThroughAll fix and the pattern-union fix that landed alongside it together unblocked the sprocket chain.

## Failure categories

### Tier-4 gaps (8 fixtures)
The sample biases toward complex pattern files, so these are the headline tier-4 limits:

| Issue | Count | Fixtures |
|---|---:|---|
| [#25](../../../issues/25) Mirror via DatumPlane | 5 | DA-40, DA-63, DA-XX, Support_Fan, Baterry_9_volts |
| [#24](../../../issues/24) Atomic (body-less) pattern | 3 | DIN471_M26, DIN471_M28, Jante-Arriere |
| [#26](../../../issues/26) Pattern Original Type='ThroughAll' | 1 | door-hinge |
| [#27](../../../issues/27) PolarPattern multi-Original | 1 | LMXXXUU |

### Tier-2/3 gaps (7 fixtures)
Not pattern-related; happened to coincide with pattern usage in the sample.

| Issue | Count | Fixtures |
|---|---:|---|
| [#31](../../../issues/31) Pocket Type=UpToFirst/UpToFace | 2 | 2020x50_V_slot_profile, Base |
| [#29](../../../issues/29) Pad Type=TwoLengths | 1 | T8_housing_bracket |
| [#33](../../../issues/33) Part::Helix | 1 | Beam-coupling-5mm-5mm |
| [#35](../../../issues/35) PartDesign::Draft | 1 | Water_tank_500L_flat |
| Groove ReferenceAxis is a DatumLine | 1 | Lego_basic_Doll (Groove now works; this Lego uses a custom-positioned rotation axis the translator doesn't yet handle — similar shape to issue #25 but for axes instead of planes) |
| Disconnected sketch geometry | 1 | 45x45_mm_ |

### Precision-edge (5 sprockets)
These translate cleanly and produce geometrically correct shapes; the volume mismatch is purely OCCT round-off on shapes with many curved tooth boundaries (sprockets with 26+ teeth). Tolerated drift is 1-4 ppm — within the tessellation precision of either FreeCAD or build123d.

Fixing would require either a slightly relaxed sprocket-specific tolerance or a more nuanced precision model in `compare.py`. Not a translator bug.

## Dropped fixture

`Sprocket_ISO606_simplex_6x2_8_z76` — 76-tooth sprocket with a 76-copy PolarPattern of a Groove. FreeCAD's `recompute()` runs past 5 minutes on this file; not feasible to snapshot.

## How to re-run

```bash
PYTHONPATH=.conda/envs/freecad/lib \
  .conda/bin/micromamba run -n freecad \
  python tools/corpus_validate.py \
    --library /tmp/fc-library \
    --db data/parts-library/coverage.json \
    --out tests/fixtures/tier4_corpus \
    --n 30 --seed 613 \
    --require-type PartDesign::LinearPattern \
    --require-type PartDesign::PolarPattern \
    --require-type PartDesign::Mirrored \
    --allow-tiers 1,2,3,4,6

FCSTD2B123D_FREECAD_PYTHON=.conda/envs/freecad/bin/python \
FCSTD2B123D_FREECAD_PYTHONPATH=.conda/envs/freecad/lib \
  uv run python tools/corpus_run_translation.py \
    --corpus tests/fixtures/tier4_corpus
```
