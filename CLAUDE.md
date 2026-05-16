# CLAUDE.md — fcstd2b123d

Project-specific instructions for any agent working on `fcstd2b123d`. The
global engineering rules live in `~/.claude/CLAUDE.md`; this file
augments them with what's specific to this codebase.

## What this project is

A translator from FreeCAD `.FCStd` files to build123d Python source.
The output is *not* a STEP/IGES export — it's hand-readable parametric
code that an engineer or LLM can modify. That goal drives every rule
below.

## Architecture quick-reference

- **Two environments**: the translator imports `FreeCAD` (Python 3.12 in
  `.conda/envs/freecad`), the emit imports `build123d` (the project
  venv). Tests exec emitted source in the build123d env. See ADR-0001.
- **Key modules**:
  - `src/fcstd2b123d/translator.py` — top-level dispatch, document walk.
  - `src/fcstd2b123d/sketch.py` — Sketcher::SketchObject → build123d faces.
  - `src/fcstd2b123d/partdesign.py` — Body, Pad/Pocket/Revolution/Hole/Draft/Pattern/etc.
  - `src/fcstd2b123d/primitives.py` — Part-workbench Box/Cylinder/Sphere/Cone/Torus.
  - `src/fcstd2b123d/tier5_boolean.py` — Part::Cut / Fuse / Common.
  - `src/fcstd2b123d/emitter.py` — TranslationUnit, render_module, helper definitions.
  - `src/fcstd2b123d/snapshot.py` — FreeCAD-side property extraction (.expected.json).
  - `src/fcstd2b123d/verify.py` — build123d-side comparison + Hausdorff backstop.
- **Test contract**: every emitted module must bind `result = <final shape>`.

## Emit code quality — **read this**

Hand-written-quality output is the goal. Before merging any translator
change, run an emit on a representative fixture and ask:

1. Could I delete the auto-gen header and pass this off as hand-written?
2. Are variable names *about the part*, or *about FreeCAD's tree*?
3. Do pattern features spell out copies, or use `Locations` contexts?
4. Are helper definitions inlined that should be imported?
5. Does the `result =` line still exist?

If any answer gets worse, push back on the change before merging.

**Source of truth**: `docs/design/emit-style-guide.md`. Read it before
adding a translator. The bd_warehouse comparison and ranked cons live
in `docs/design/emit-code-quality.md`.

Highlights you should not have to re-derive:

- **Imports**: specific named imports only. Never `import *`.
- **Names**: prefer FreeCAD `Label` over `Name` when they differ. `Pad`,
  `Pocket`, etc. are feature *kinds*, not variable names.
- **Patterns**: detect uniform `Rot(Z=k·θ)` / `Pos(i·dx, …)` and emit
  `with PolarLocations(): …` / `with GridLocations(): …`. Spelled-out
  copies are the single biggest "looks auto-gen" tell (#75).
- **Coordinates**: snap FP-roundoff (`-19.9999…` → `-20`) but never
  snap real solver-computed values (#43 has the worked example of why
  naive snap breaks BRep validity).
- **Helpers**: defined once in `emitter.py:HELPER_DEFINITIONS`, emitted
  on-demand via `TranslationUnit.helpers`. Don't duplicate inline.
- **Comments**: per-feature provenance carrying the FreeCAD `Label` and
  salient properties. Don't comment what build123d calls already say.

## Fixtures

Every issue needs a reproducer fixture. Per the global CLAUDE.md
library-first rule:

1. **Look in the FreeCAD Parts Library / `tests/fixtures/sample_813/` first.**
   If a real-world file isolates the gap, use it. Cite the path in the
   issue.
2. **Synthesise only when no library file isolates the case.** Many
   library files have multiple gaps; synth is fine when the only
   way to test the specific feature is a hand-rolled minimal file.
3. **Closing an issue requires its fixture to move from `EXCLUDED_FROM_TEST` to passing.**
   Fixture generators live in `tools/synthesize_fixtures.py`. Each
   function produces one `.FCStd`; snapshot via `tests/snapshot.py`.

Fixture directories:
- `tests/fixtures/tier{1..6}_*` — synthetic per-tier coverage.
- `tests/fixtures/tier{3,4,6}_corpus*`, `sample_813` — random library samples.

## "Do it properly or not at all"

This project's mantra. Specifically banned:

- **Shape-import / STEP round-trip fallback**: removed in PR #59. Don't
  re-add. If a FreeCAD feature can't be parametrically translated, the
  translator must refuse cleanly (UnsupportedFeatureError) — the user
  can already STEP-export from FreeCAD without us.
- **Pseudo-success**: a translator that emits something that *executes*
  but produces wrong geometry is worse than one that refuses. SPEC §13.5
  documents the cases we deliberately keep out of scope.

## Auto-merge policy

Per the global CLAUDE.md, you may merge without asking when all three
hold:
1. Closes a pre-existing issue (drive-by changes still need approval).
2. CI green on both lanes.
3. Coverage equal-or-better; no fixture moved from passing to EXCLUDED.

Always ask before adding an EXCLUDED entry (acknowledging a known
failure rather than fixing it).
