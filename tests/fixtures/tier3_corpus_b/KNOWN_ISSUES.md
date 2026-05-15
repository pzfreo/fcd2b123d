# Known issues — corpus batch 2 (seed=137)

Initial pass rate: 26/30. After fixes during batch 3:
- `Winch-Model1-Horizontal-roll` now passes (body-less Pad chaining was
  added to the translator).
- `F623ZZ_Ball_Bearing` now passes (body-less Pocket Length chaining was
  added, gated on the previous solid coming from PartDesign workbench).

**Current pass rate: 28/30.**

## Remaining failures

### Mannequin_mp-dummy-1850mm-standing-003 and -007 — multi-Body composition

Each fixture contains 15 `PartDesign::Body` objects assembled into a
figure via Body.Placement transforms. The translator currently requires
identity Placement on the Body itself; the mannequin's Head, Torso,
Arm, and Leg Bodies are positioned relative to the world origin to
compose the figure.

The mannequins also use `PartDesign::Groove` (a subtractive revolution)
which we haven't translated yet. So fixing Body.Placement alone would
not make them pass; a Groove translator is also needed.

**Path to fix**: multi-Body composition (tier-5-ish) plus a
`PartDesign::Groove` translator plus rotation in Body.Placement (which
we deferred in tier-1).

## How to re-run

```bash
PYTHONPATH=.conda/envs/freecad/lib \
  .conda/bin/micromamba run -n freecad \
  python tools/corpus_validate.py \
    --library /tmp/fc-library \
    --db data/parts-library/coverage.json \
    --out tests/fixtures/tier3_corpus_b \
    --n 30 --seed 137 --max-tier 3

FCSTD2B123D_FREECAD_PYTHON=.conda/envs/freecad/bin/python \
FCSTD2B123D_FREECAD_PYTHONPATH=.conda/envs/freecad/lib \
  uv run python tools/corpus_run_translation.py \
    --corpus tests/fixtures/tier3_corpus_b
```

The first command is idempotent — re-running picks the same 30 files
because the seed is fixed.
