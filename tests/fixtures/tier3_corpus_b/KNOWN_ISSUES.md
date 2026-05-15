# Known issues — corpus batch 2 (seed=137)

The second batch revealed four fixtures that don't pass the property
comparison. They stay in this directory with snapshots committed (so the
sample is reproducible — anyone re-running `tools/corpus_validate.py`
with seed=137 gets the same files) but are excluded from
`tests/test_translator_corpus.py` via `EXCLUDED_FROM_TEST`.

The four failures fall into three categories.

## 1. Multi-Body files with non-identity Body Placement

- `Mannequin_mp-dummy-1850mm-standing-003.FCStd`
- `Mannequin_mp-dummy-1850mm-standing-007.FCStd`

These each contain 15 `PartDesign::Body` objects assembled into a figure
(head, torso, limbs). The translator currently requires Body.Placement
to be identity; the mannequin's Head, Arm, Leg bodies are positioned
relative to the world origin to compose the figure.

**Path to fix**: handle Body.Placement (translation + rotation) by
wrapping each translated Body in `Loc(...)` or `Pos(...) * Rot(...)`,
and emit one final union/compound across Bodies. Roughly tier-5
territory (multi-body composition) plus rotation handling we deferred
in tier-1.

The mannequins also use `PartDesign::Groove` (a subtractive revolution)
which we haven't implemented. So fixing Body.Placement alone wouldn't
make them pass; Groove translator is also needed.

## 2. Body-less PartDesign Pad cumulative chaining

- `Winch-Model1-Horizontal-roll.FCStd`

Three top-level `PartDesign::Pad` features (Pad, Pad001, Pad002) with
no containing Body. FreeCAD interprets these as a cumulative union —
`Pad002.Shape` is the union of all three Pads' extruded shapes — even
though no Body or `BaseFeature` property explicitly chains them.

Our translator currently emits each top-level Pad as standalone
(`Pad002 = extrude(Sketch002, length)`). So `Pad002.Shape` ends up
being just the third Pad's prism, ~3% of FreeCAD's cumulative result.

**Path to fix**: detect "first vs subsequent" top-level Pads. The
subsequent Pads should emit `Pad = previous_solid + extrude(profile, length)`.
The challenge: FreeCAD's actual chaining logic in legacy files is not
fully consistent across documents (see issue #3 — Pocket Length
sometimes chains, sometimes doesn't). We'd need to reverse-engineer
the heuristic from more examples before committing to a rule.

## 3. Body-less PartDesign Pocket Length cumulative chaining

- `F623ZZ_Ball_Bearing.FCStd`

Two top-level `PartDesign::Pocket` features (Type=Length, not
ThroughAll) following a Revolution. `Pocket001.Shape` is `Revolution -
some_extrude` (vol 300, near Revolution's 311). Our translator emits
the Pocket as standalone (just the extruded profile), so the result is
~5 mm³ instead of ~300 mm³.

Note this disagrees with the M42 screw in batch 1, where a body-less
Pocket Length produced a *standalone* hex prism (no subtraction). So
FreeCAD's behaviour here is file-dependent or version-dependent and
not yet understood.

**Path to fix**: same as issue #2 — needs more examples to understand
when body-less Pocket Length chains vs doesn't. May ultimately depend
on file-format metadata that isn't easily exposed in the Python API.

---

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
