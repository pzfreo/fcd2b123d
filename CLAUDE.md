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

## Do it properly or not at all

**The bar is human-readable, parametrically meaningful build123d
Python. If we can't meet it for a feature, the translator refuses with
`UnsupportedFeatureError` — it never ships theatre.**

This was the lesson from PR #40 → PR #59 (the shape-import incident).
A "shape-import fallback" was added that, for features outside v1
scope, would export the resolved BRep from FreeCAD to STEP and emit
`import_step("sidecar.step")`. It made the coverage numbers look good
but produced output the user could already get in two clicks from
FreeCAD's STEP export plus build123d's `import_step` — the translator
added nothing, and the resulting `.py` was opaque, depended on a
sidecar file, and counted as a "pass" only in name. PR #59 ripped
it out. SPEC §13.5 documents the cases we deliberately keep out of
scope as a result.

Rules that fall out of that lesson:

- **Output must be readable, parametrically meaningful build123d
  Python.** Code another engineer or LLM could read, modify, and
  re-run with different dimensions. If the emit is mechanically-
  generated noise, depends on a sidecar BRep, or is just a wrapper
  around two existing tools, it's not done.
- **Pseudo-success is worse than honest failure.** A translator that
  emits something that *executes* but produces wrong / unusable
  geometry is worse than one that refuses cleanly — silent failure
  poisons the corpus metrics and trains the user to trust output
  they shouldn't.
- **Round-trip / wrapper patterns are banned.** Specifically: STEP /
  IGES / BRep export-then-import paths. The user can already do that
  with FreeCAD's exporter plus `build123d.import_step` — we add zero
  value and lose the parametric story. Banned even when it would
  "fix" a hard-to-translate fixture.
- **Refuse rather than fake it.** When a FreeCAD feature can't be
  parametrically translated, raise `UnsupportedFeatureError` with a
  message naming the gap. The user can choose to file an issue, fix
  the translator, or fall back to FreeCAD's own export — we don't
  pretend.
- **When you spot this pattern in existing code, flag it.** Don't
  extend a workaround because it's already there. Surface it to the
  user; let them decide whether to keep it, remove it, or replace it
  with a real translator.

This rule overrides "make tests pass" and "improve the corpus
pass-rate" when those are in tension with output quality. The
sample_813 audit (currently 60/86 pass) is honest because the 26
failures are real "we can't yet" cases, not fake handlers.

## Auto-merge policy

Per the global CLAUDE.md, you may merge without asking when all three
hold:
1. Closes a pre-existing issue (drive-by changes still need approval).
2. CI green on both lanes.
3. Coverage equal-or-better; no fixture moved from passing to EXCLUDED.

Always ask before adding an EXCLUDED entry (acknowledging a known
failure rather than fixing it).
