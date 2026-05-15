# Known issues — corpus batch 3 (seed=271)

**Pass rate: 29/30.**

Batch-3 sampling drove two real bug fixes in the translator:

- **Face references in fillet**: previously only `Edge<N>` references were
  understood. The Oven fixture has a fillet that references `Face16`
  (meaning "all edges of face 16"). Now expanded to the face's contour
  edges.

- **PartDesign::Pad chaining inside a Body**: the Oven Body has five
  consecutive `Pad` features that union additively. The translator
  used to emit each as standalone — wrong. Fixed in
  `_translate_pad(..., base_var=current_var)`. This also retroactively
  fixed `Winch` from batch 2 (body-less version of the same pattern).

## Remaining failure

### Oven_builtIn — deep fillet cascade fails on build123d's OCCT validation

After the Pad-chaining fix the Oven body translates correctly through
all 5 Pads, the Pocket, and the first Fillet. The translator then runs
through 7 more cascaded fillets and 1 chamfer; at some point build123d
raises `ValueError: Failed creating a fillet with radius of 5.0` —
the edge selected by midpoint match cannot accommodate the requested
radius in build123d's evaluated BRep.

Most likely cause: midpoint-based edge selection drifts as each fillet
modifies the local topology. The chosen edge in build123d may not be
geometrically identical to the chosen edge in FreeCAD (small precision
differences amplify across 8 cascaded operations).

**Path to fix**: a more robust edge identification scheme — by face
adjacency rather than midpoint position, or by walking the topology
graph rather than indexing into a flat edge list. That's a meaningful
refactor; the midpoint approach is fine for shallow cascades (≤ 2–3
fillets) but brittle at depth.

For everyday tier-3 use the midpoint approach is sufficient — the
Oven fixture is an outlier with 9 cascaded fillets.

## How to re-run

```bash
PYTHONPATH=.conda/envs/freecad/lib \
  .conda/bin/micromamba run -n freecad \
  python tools/corpus_validate.py \
    --library /tmp/fc-library \
    --db data/parts-library/coverage.json \
    --out tests/fixtures/tier3_corpus_c \
    --n 30 --seed 271 --max-tier 3

FCSTD2B123D_FREECAD_PYTHON=.conda/envs/freecad/bin/python \
FCSTD2B123D_FREECAD_PYTHONPATH=.conda/envs/freecad/lib \
  uv run python tools/corpus_run_translation.py \
    --corpus tests/fixtures/tier3_corpus_c
```
