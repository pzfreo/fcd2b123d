# Known issues — corpus batch 4 / tier-6 parametric (seed=519)

**Pass rate: 15 / 29.**

Random sample of 30 in-scope tier-1-to-6 files from the Parts Library that
contain parametric content (Spreadsheet aliases, expressions). One file
(Diamond.FCStd) was dropped because its target Body computes to a 0-solid
Compound — a content issue in the source file. The remaining 29 are wired
into `tests/test_translator_corpus.py`; 14 are listed in
`EXCLUDED_FROM_TEST` because they hit one of the categories below.

## Categories of failure

### Pre-existing tier-2/3/4/5 gaps

These fixtures contain features the translator doesn't yet support at all.
None of these are tier-6 specific — they'd fail on a non-parametric file
with the same content.

| Fixture | Blocker |
|---|---|
| `1x3-male-pin-header-right-angle-type-II` | `PartDesign::LinearPattern` (tier 4) |
| `T-shape_brackets` | `PartDesign::LinearPattern` (tier 4) |
| `SKxx_Linear_Rail_Shaft_Support` | `PartDesign::Hole` (tier 2 in scope, no translator yet) |
| `SKXX` | `Part::Common` Boolean intersection (tier 5) |
| `Straight_brackets` | `PartDesign::ReferencePocket` — not in tier map |
| `Parametric_LiPo` | `Pocket.Type='UpToFirst'` — unimplemented Pocket mode |
| `AerosolBox` | unsupported feature past Midplane (variable depending on which path) |
| `LinearSlide-MGNx-XX-Rail` | unsupported feature past Midplane |
| `L-shape_brackets` | unsupported feature past Midplane |
| `TS35` | unsupported feature past Midplane |

### Sketch geometry gaps

| Fixture | Issue |
|---|---|
| `Googly_eyes` | Sketch contains `Ellipse` — only Line / Circle / ArcOfCircle supported |
| `drawing-pin` | Sketch has disconnected geometry that doesn't form a single closed chain |

### Post-Midplane regressions

These reach the fillet/chamfer step now that Midplane Pad/Pocket is
supported but fail downstream:

| Fixture | Issue |
|---|---|
| `ISO4032_Hex_Nut_M4` | Chamfer's midpoint-based edge selection drifts after Midplane Pad (same brittleness as the Oven cascade in batch 3 — needs face-adjacency identification) |
| `Foot` | ~4% volume mismatch after Midplane support landed — needs investigation |

## What tier-6 *did* deliver

For the 15 passing parametric fixtures, the emit is now a function:

```python
def make_part(depth=15, height=10, width=25):
    """Translated parametric design. Defaults match the source values."""
    # ... build123d code referencing depth, height, width ...
    return result_var


result = make_part()
```

A downstream consumer can call `make_part(width=50)` to produce a variant
without editing module-level constants. The function signature is built
only from spreadsheet aliases that are *actually referenced* by
ExpressionEngine bindings — unused aliases aren't exposed.

`tests/test_translator_parametric.py` exercises this end-to-end:
defaults match the snapshot; doubling a parameter doubles the relevant
geometric measure.

## How to re-run

```bash
PYTHONPATH=.conda/envs/freecad/lib \
  .conda/bin/micromamba run -n freecad \
  python tools/corpus_validate.py \
    --library /tmp/fc-library \
    --db data/parts-library/coverage.json \
    --out tests/fixtures/tier6_corpus \
    --n 30 --seed 519 --max-tier 6

FCSTD2B123D_FREECAD_PYTHON=.conda/envs/freecad/bin/python \
FCSTD2B123D_FREECAD_PYTHONPATH=.conda/envs/freecad/lib \
  uv run python tools/corpus_run_translation.py \
    --corpus tests/fixtures/tier6_corpus
```

Note: `tools/corpus_validate.py` doesn't currently filter for parametric
content. The batch-4 sample was selected by a one-off script that filtered
for `Spreadsheet::Sheet` or `App::VarSet` presence, then sampled with
seed 519.
