# fcstd2b123d — Project Spec

A translator from FreeCAD `.FCStd` files to [build123d](https://github.com/gumyr/build123d) Python source.

## 1. Purpose

LLMs are bad at *starting* 3D designs from scratch (no spatial grounding) but good at *iterating* on existing build123d Python (named operations, numeric parameters, clean syntax). FreeCAD is the inverse: easy to rough out shapes interactively, but its `.FCStd` format (XML + binary BRep blobs) is opaque to LLMs.

This tool bridges the two: rough a design in FreeCAD's GUI, translate to build123d, then iterate via LLM. Existing FreeCAD designs (Parts Library, user repos) become reachable from the build123d workflow as a bonus.

## 2. Goals

- Translate the parametric structure of an `.FCStd` file to executable, idiomatic build123d Python.
- Preserve **named parameters** (Spreadsheet aliases, expressions) as Python variables so the output is LLM-editable, not flattened to numeric literals.
- Emit **inline comments** mapping each code block back to its source FreeCAD operation (e.g. `# PartDesign::Pad "BottomFlange"`), so a human or LLM can navigate between the original and the translation.
- Be **honest about scope**: detect unsupported features and refuse with a clear error rather than silently mistranslate.
- Validate every supported feature with **property-based regression tests** comparing geometric invariants of the translated solid against the FreeCAD original.

## 3. Non-goals

- Round-tripping. We translate one direction only; build123d → FreeCAD is out of scope.
- Recovering FreeCAD sketch constraints as a constraint system. build123d has no 2D constraint solver; sketches translate to concrete coordinates (FreeCAD stores post-solve geometry, so this is direct).
- Assembly workbench, Draft workbench, Arch workbench, Sheet Metal workbench, FEM, CAM. PartDesign + Part + Sketcher + Spreadsheet only.
- Visual fidelity (colors, materials, view settings).
- A GUI. CLI only.

## 4. Architecture decision: FreeCAD-runtime

The translator runs **inside a FreeCAD Python environment**, using FreeCAD's own loader to parse `.FCStd` and expose the parametric tree as live Python objects.

**Rationale** (see conversation log for full analysis):

- The alternative — parsing `Document.xml` standalone — breaks on anything that references derived BRep topology (fillets/chamfers/patterns reference edges by Open Cascade internal naming, only resolvable by recomputing the model). Standalone parsing can only handle the trivial subset.
- FreeCAD's `ExpressionEngine` API gives us original expression strings for free, avoiding a custom Bison-grammar parser.
- FCStd is **not a documented stable format**; tracking it manually is a permanent maintenance liability we don't need to take on.
- Correctness asymmetry: shipping a pip-installable tool that silently mistranslates fillets is worse than shipping a conda/Docker tool that gets them right.

**Cost accepted**: install friction. FreeCAD is not pip-installable. We will ship via:
1. `conda install -c conda-forge fcstd2b123d` (primary)
2. A Docker image for users who can't or won't install conda
3. **No pip path in v1**. Revisit only if v1 reveals a clean trivial subset that could justify a separate standalone build.

## 5. Scope tiers

Each tier is a milestone. Each tier landing means: translator handles the feature, property-comparison tests pass on representative fixtures, and any new edge cases are documented.

Cumulative coverage percentages are empirical from the Parts Library survey — see §13 for the data and `tools/analyze_fcstd.py` for the canonical TypeId mapping.

| Tier | Features | Cumulative coverage | Why it's a tier boundary |
|------|----------|---------------------|--------------------------|
| 1 | Primitives: `Part::Box/Cylinder/Sphere/Cone/Torus/Helix/...`, `PartDesign::AdditiveBox/Cylinder/...` | 13% | Sanity check on the comparison harness itself. |
| 2 | `PartDesign::Body`, `Sketcher::SketchObject`, `Pad`, `Pocket`, `Revolution`, sweep/loft/helix as features, Part-workbench equivalents (`Extrusion`, `Revolution`, `Loft`, `Sweep`) | **61%** | Core PartDesign workflow. The workhorse. |
| 3 | `Fillet`, `Chamfer`, `Draft`, `Thickness`, `Offset` | 67% | **Topological naming bites here.** Validates the FreeCAD-runtime approach. |
| 4 | `LinearPattern`, `PolarPattern`, `Mirrored`, `MultiTransform` | 69% | Pattern features. Need transform resolution. |
| 5 | Boolean ops (`Part::Cut/Fuse/Common`); `Part::Feature` via shape-import fallback | 82% | Multi-body interaction + the graceful-degradation path for parts without parametric history. |
| 6 | `Spreadsheet::Sheet` aliases + `App::VarSet` + property expressions | **99%** | The "named variables preserved" promise. Second-largest tier. |
| 7 | Selected real designs from FreeCAD Parts Library | n/a | End-to-end validation on non-toy input. |

We stop at the tier where current effort runs out. The project ships meaningfully at any tier ≥ 3.

## 6. Test methodology

**Property-based regression testing**, not strict red-green-refactor TDD.

For each test fixture `foo.FCStd`:

1. Open in FreeCAD → compute geometric properties → snapshot to `foo.expected.json`.
2. Run translator → produce `foo.b123d.py`.
3. Execute `foo.b123d.py` → obtain a build123d `Part`/`Compound`.
4. Compute the same geometric properties → compare against snapshot.

### 6.1 Properties compared

| Property | Tolerance | Notes |
|----------|-----------|-------|
| Volume | relative 1e-6 | Gold standard. Invariant under everything. |
| Surface area | relative 1e-6 | Invariant. |
| Center of mass (world frame) | absolute, 1e-5 × bbox diagonal | Apply FreeCAD `Placement` transforms before comparing. |
| Principal moments of inertia (sorted eigenvalues of MOI tensor) | relative 1e-5 | Rotation-invariant. Full tensor is NOT. |

**Explicitly NOT compared**: face count, edge count, vertex count, axis-aligned bounding box. These vary with operation order even when the geometric result is identical, and produce false failures.

**Fallback for paranoia**: Hausdorff distance between sampled meshes of both solids. Slow; only used when properties pass but visual inspection suggests something is wrong.

### 6.2 Parameter sweep tests (for tier 6+)

For fixtures with `Spreadsheet` parameters, the test:

1. Defines a sweep: `{"width": [10, 20, 30, 50, 100], "height": [5, 15, 25]}`.
2. For each combination, sets the values in FreeCAD, recomputes, snapshots properties.
3. Translator emits `foo.b123d.py` with `width` and `height` as Python variables at the top of the file.
4. Test imports the module, sets the same combinations, computes properties, compares.

This is the **only** test that proves named variables were preserved and wired correctly. Without it, the translator could pass static tests by flattening every parameter to a literal.

### 6.3 Negative tests

For each unsupported feature (Assembly, Draft workbench, External Geometry, Link objects):

- A fixture using the feature.
- Test asserts the translator exits with a specific error class (`UnsupportedFeatureError`) and a message naming the offending object.

A small honest tool > a broad lying one.

### 6.4 Ground truth caching

`foo.expected.json` snapshots are committed to the repo. They're regenerated by running `python -m tests.snapshot foo.FCStd`, which opens FreeCAD and writes the JSON. CI does not run FreeCAD for static property checks; only the parameter-sweep tests need a live FreeCAD instance.

This keeps CI fast (~seconds per static test, ~tens of seconds per sweep test).

## 7. Test model — concrete structure

### 7.1 Fixture layout

```
tests/
  fixtures/
    tier1_primitives/
      box.FCStd
      box.expected.json
      cylinder.FCStd
      cylinder.expected.json
      ...
    tier2_partdesign/
      simple_pad.FCStd
      simple_pad.expected.json
      ...
    tier6_parametric/
      bracket_sweep.FCStd
      bracket_sweep.sweep.json     # parameter ranges
      bracket_sweep.expected.json   # properties keyed by param tuple
    negative/
      assembly_unsupported.FCStd
      assembly_unsupported.expected.json   # {"error": "UnsupportedFeatureError", "match": "Assembly"}
```

### 7.2 Snapshot format

```json
{
  "volume": 1234.5678,
  "surface_area": 678.901,
  "center_of_mass": [10.0, 20.0, 5.0],
  "principal_moi": [123.4, 234.5, 345.6],
  "freecad_version": "0.21.2",
  "snapshot_date": "2026-05-14"
}
```

### 7.3 Comparison utility

A single module `tests/utils/compare.py` exposing:

```python
def compute_properties(part) -> Properties: ...
def compare_properties(actual: Properties, expected: dict) -> ComparisonResult: ...
def assert_equivalent(actual: Properties, expected: dict): ...  # raises on mismatch
```

Written and tested **before** any translator code lands. It must:

- Handle COG frame alignment (apply FreeCAD `Placement` if present in snapshot).
- Sort MOI eigenvalues before comparison.
- Produce informative failure messages (which property, expected, actual, relative error).

## 8. Distribution

- **Primary**: `conda install -c conda-forge fcstd2b123d` (depends on `freecad`, `build123d`).
- **Secondary**: `docker run ghcr.io/<org>/fcstd2b123d <input.FCStd>` for users without conda.
- **CLI surface**: `fcstd2b123d input.FCStd -o output.py` (minimum). Flags for verbosity and tier-strictness added as needed.

## 9. Project structure (initial)

```
src/fcstd2b123d/
  __init__.py
  cli.py
  loader.py              # FreeCAD doc opening, object iteration
  emitter.py             # build123d code generation, naming, formatting
  translate/
    primitives.py
    sketch.py
    partdesign.py
    boolean.py
    spreadsheet.py
  errors.py              # UnsupportedFeatureError and friends
tests/
  utils/compare.py
  fixtures/...
  test_compare.py        # tests the comparison utility itself
  test_tier1.py
  test_tier2.py
  ...
  test_negative.py
pyproject.toml
README.md
SPEC.md (this file)
```

## 10. Open questions

These are deferred until implementation reveals enough to answer them:

1. **Emitter style**: `BuildPart` context-manager style vs. algebraic-mode style? Both are valid build123d; choose based on which is easier to generate consistently and which an LLM finds easier to edit.
2. **Sketch granularity**: emit each Sketcher object as its own `Sketch(...)` block, or inline into the consuming operation? Inlining is more readable; separate blocks are easier to map to the source.
3. **Expression rewriting**: how aggressively do we simplify? `width * 2 / 2` → `width`? Probably no — preserve original intent.
4. **Naming**: how do we generate readable Python identifier names from FreeCAD `Label` strings that may contain spaces, unicode, conflicts? Need a deterministic, idempotent slugifier.
5. **Sketches with external geometry references**: tier 3 or out of scope? Defer until tier 3 fixtures force a decision.

## 11. Success criteria

Tier 3 ships with:
- 20+ static fixtures across tiers 1–3, all passing.
- 1+ parameter-sweep fixture demonstrating spreadsheet preservation (tier 6 work brought forward).
- 3+ negative-test fixtures.
- README showing a real before/after: `.FCStd` → generated `.py` for a non-trivial example.
- Docker image published.

Anything beyond tier 3 is bonus.

## 12. Fixture priorities

### 12.1 Analysis of bundled FreeCAD examples

`tools/analyze_fcstd.py` was run against all 21 `.FCStd` files shipped in the FreeCAD 1.0 conda-forge install. Only **3** are in-scope per Section 3's non-goals.

**In scope (3):**

| File | Source path | Max tier | Composition |
|---|---|---|---|
| `PartDesignExample.FCStd` | `share/examples/` | 2 | 1 Body, 4 Sketches (57 constraints), 1 Pad, 3 Pockets |
| `Drilling_1.FCStd` | `Mod/CAM/CAMTests/` | 4 | 1 Body, 15 Sketches (43 constraints), 3 Pads, 12 Pockets, LinearPattern, PolarPattern |
| `EngineBlock.FCStd` | `share/examples/` | 5 | Part workbench: 5 Box, 3 Cylinder, 7 Extrusion, 3 MultiFuse, 5 Cut, 1 MultiCommon, 3 Mirroring, 8 `Part::Part2DObjectPython` |

**Out of scope (14):**

| File | Reason |
|---|---|
| `AssemblyExample.FCStd` | Assembly workbench (non-goal) |
| `BIMExample.FCStd` | TechDraw + Arch (BIM) |
| `FEMExample.FCStd`, `all_objects_de9b3fb438.FCStd`, 5× `box*.FCStd` and `constraint_contact_*.FCStd` | FEM workbench |
| `draft_test_objects.FCStd` | Draft workbench |
| `drill_test1.FCStd`, `OpHelix_v0-21.FCStd` | CAM workbench (`Path::FeaturePython`) |
| `InvoluteGear_v0-20.FCStd`, `InternalInvoluteGear_v0-20.FCStd` | Only `Part::Part2DObjectPython` extension; no tier-recognised operations to translate |

**Unreadable (4):**

`macro_template.FCStd`, `missing_macro_metadata.FCStd`, `good_macro_metadata.FCStd`, `bad_macro_metadata.FCStd` — all from `Mod/AddonManager/AddonManagerTest/data/`, all fail to open with "Invalid project file" (they are intentionally malformed test stubs for the AddonManager).

**Critical gaps in the bundled set:**

- **Tier 1** (primitives beyond Box): only EngineBlock's nested Box/Cylinder uses; no standalone Cylinder/Sphere/Cone/Torus fixtures.
- **Tier 3** (fillets/chamfers): none — this is the tier where topological naming bites and where ADR-0001's FreeCAD-runtime decision is validated. Most important gap.
- **Tier 6** (spreadsheet-driven): none — this is the killer test for the named-variable-preservation promise.

These gaps are filled by `tools/synthesize_fixtures.py`, which generates 7 minimal fixtures programmatically.

### 12.2 Top 10 selected fixtures

| # | Fixture path | Tier | Provenance | Why it earned a slot |
|---|---|---|---|---|
| 1 | `tier1_primitives/box_10x20x30` | 1 | synthetic | Distinct dimensions ⇒ distinct principal MOI eigenvalues; catches wrong eigenvalue ordering. |
| 2 | `tier1_primitives/cylinder_r10_h30` | 1 | synthetic | Round geometry; MOI has axial symmetry (two eigenvalues equal). |
| 3 | `tier1_primitives/sphere_r15` | 1 | synthetic | Maximally symmetric — all three principal MOI equal. Cheapest invariant check. |
| 4 | `tier1_primitives/cone_r10_r5_h20` | 1 | synthetic | Non-trivial COM (off-center along the axis). |
| 5 | `tier1_primitives/torus_R20_r5` | 1 | synthetic | Genus-1 topology — exercises BRep paths beyond simple solids. |
| 6 | `tier2_partdesign/simple_pad` | 2 | synthetic | Smallest possible Body + Sketch + Pad. Minimal regression case. |
| 7 | `tier2_partdesign/partdesign_example` | 2 | bundled (LGPL) | Canonical FreeCAD example: 1 Pad + 3 Pockets, 57 sketch constraints. Realistic structure. |
| 8 | `tier3_filletchamfer/box_with_fillet` | 3 | synthetic | **The critical test for ADR-0001.** A Pad whose four vertical edges (referenced by OCCT internal names like `Edge8`) are filleted at radius 3. Validates topological-naming resolution. |
| 9 | `tier4_patterns/drilled_plate` | 4 | bundled (LGPL) | Sourced from `Drilling_1.FCStd` (renamed). LinearPattern + PolarPattern + dense hole geometry. |
| 10 | `tier6_parametric/spreadsheet_box` | 6 | synthetic | A `Part::Box` whose Length/Width/Height are bound via `setExpression` to a Spreadsheet's `width`/`depth`/`height` aliases. Proves the ExpressionEngine round-trips through save/load; the translator must emit these as named Python variables. |

### 12.3 Tier coverage and deliberate omissions

Top 10 covers tiers 1, 2, 3, 4, 6. Deferred:

- **Tier 5** (boolean ops between bodies): `EngineBlock.FCStd` was the obvious candidate but was bumped in favour of tier-3 fillet coverage. Tier 3 validates ADR-0001 (the central architectural decision); tier 5 doesn't. Add EngineBlock when the translator handles Boolean ops.
- **Tier 7** (substantial Parts Library designs): the FreeCAD Parts Library is a separate repo of CC-BY 3.0 parts. Pulling a curated subset is a meaningful undertaking — best done after the simpler tiers work end-to-end through an actual translator.

### 12.4 Provenance and license

| Source | License | Count |
|---|---|---|
| Synthetic via `tools/synthesize_fixtures.py` | Same as project | 7 |
| FreeCAD 1.0 `share/examples/` | LGPL-2+ | 1 (`partdesign_example`) |
| FreeCAD 1.0 `Mod/CAM/CAMTests/` | LGPL-2+ | 1 (`drilled_plate`) |

The two bundled fixtures are derivative works of FreeCAD and inherit LGPL-2+. As long as the test suite ships with appropriate attribution, redistribution is fine for an open-source project.

### 12.5 Regenerating

Synthetic fixtures:
```bash
PYTHONPATH=.conda/envs/freecad/lib \
  .conda/bin/micromamba run -n freecad \
  python tools/synthesize_fixtures.py
```

Snapshots (all fixtures):
```bash
for f in $(find tests/fixtures -name "*.FCStd"); do
  PYTHONPATH=.conda/envs/freecad/lib \
    .conda/bin/micromamba run -n freecad \
    python tests/snapshot.py "$f"
done
```

Bundled fixtures: copy from `.conda/envs/freecad/share/examples/` (or `Mod/.../`) into the appropriate `tests/fixtures/tierN_*/` directory.

## 13. Parts Library coverage analysis

### 13.1 Why this section exists

The bundled-examples set (§12) yielded only 3 in-scope files out of 21. That number is misleading because FreeCAD ships *workbench demos* (one Assembly demo, one FEM demo, one BIM demo, etc.) rather than parts representative of typical user work. To get an honest answer to "does this tool address a meaningful fraction of real-world FreeCAD usage?" we ran the analyzer against the full FreeCAD Parts Library (`github.com/FreeCAD/FreeCAD-library`, ~4.1 GB, 3,194 `.FCStd` files at the time of writing).

### 13.2 Methodology

- Shallow clone of the Parts Library to a working directory.
- `tools/analyze_fcstd.py --input-list ...` walks every `.FCStd` in one Python invocation, classifies each by TypeId composition, and writes a per-file JSON record.
- `tools/summarize_analysis.py` aggregates the JSON into coverage stats.

An initial run with the conservative tier map showed 88% in scope. Inspection of the 12% rejected revealed that nearly all of it was tier-map gaps (operations like `PartDesign::AdditivePipe`, `PartDesign::AdditiveLoft`, `Part::Helix`, `Part::Thickness`, `App::VarSet`) — real PartDesign/Part operations that were simply missing from the analyzer's vocabulary. The tier map in `tools/analyze_fcstd.py` was expanded to cover those operations and the analyzer re-run.

### 13.3 Coverage results

**98.7% of files (3,151 of 3,194) in scope.**

| Tier | Files added | % of in-scope | Cumulative files | Cumulative % of all |
|------|-------------|---------------|------------------|---------------------|
| 1 (primitives) | 411 | 13.0% | 411 | 12.9% |
| 2 (PartDesign + Sketcher + sweep/loft/helix) | 1,535 | 48.7% | 1,946 | **60.9%** |
| 3 (fillets / chamfers / shell / offset) | 178 | 5.6% | 2,124 | 66.5% |
| 4 (patterns) | 88 | 2.8% | 2,212 | 69.3% |
| 5 (booleans + `Part::Feature` shape-import fallback) | 392 | 12.4% | 2,604 | 81.5% |
| 6 (`Spreadsheet::Sheet` + `App::VarSet`) | 547 | 17.4% | 3,151 | **98.7%** |

Each tier release is meaningful:
- Tier 2 alone delivers **61% of files** — confirming the "workhorse tier" framing.
- Tier 3 unlocks an additional 6 points for the most architecturally risky work (topological naming).
- Tier 5's 12-point gain comes mostly from including `Part::Feature` as a "translate the resolved BRep" path (see §13.5).
- Tier 6 is the **second-largest tier** at 17% — Spreadsheet+VarSet parametric drivers are real-user-need territory, not a niche.

### 13.4 What is actually out of scope

The remaining 43 files (1.3%) all match the project's documented non-goals:

| Category | Files | Notes |
|----------|-------|-------|
| TechDraw drawings | ~17 | Drawing workbench, not parametric CAD |
| `Mesh::Feature` | 10 | Mesh workbench (different from BRep modelling) |
| `App::Link` / `LinkElement` / `LinkGroup` | 14 | Cross-document linking; assembly-adjacent, deferred with the Assembly non-goal |
| `Part::Circle` | 1 | Easy to add later; one-off legitimate gap |

The tool's scope is well-aligned with how the Parts Library is actually built.

### 13.5 FeaturePython prevalence — degradation strategy

**610 of the 3,151 in-scope files (21%)** contain FeaturePython extensions: community-authored parametric Python objects (gear generators, fastener parts, thread/bolt generators).

- 12.6% of all files use `Part::Part2DObjectPython` (often gear profiles).
- 8.2% use `Part::FeaturePython` (gear/fastener generators).
- 1.0% use `App::FeaturePython`.

These files are "in scope" in the sense that they contain tier-recognized operations alongside the FeaturePython parts. The FeaturePython parts themselves have no canonical translation to build123d — their parametric behaviour is custom Python code we don't have at translation time.

**v1 strategy**: the same as `Part::Feature` (tier 5) — translate the resolved BRep via shape import. Geometry survives; the FeaturePython's parametric driver is lost. The emitted build123d code annotates these blocks so a reader knows the source object was flattened rather than translated.

### 13.6 Implications

The bundled-examples concern ("we can only translate 3 of the examples") is resolved. The real-world coverage is **98.7%** — the tool addresses the vast majority of actual parametric CAD work.

The tier ordering in §5 is empirically validated:
- Tier 2 is correctly identified as the workhorse.
- Tier 6 is the second-largest tier, not a "nice to have" — the named-variable preservation investment is well-founded.
- Tier 3's small percentage (~6 points) reflects that fillets/chamfers are common operations in many files; the work isn't about breadth, it's about correctness on a hard problem (topological naming).

A graceful-degradation path is now part of the v1 surface. Including `Part::Feature` in tier 5 with the shape-import fallback adds 12 points of coverage but commits us to "translate the geometry, drop the parametric history" for arbitrary BRep inputs. This is a deliberate trade — the alternative (rejecting Part::Feature outright) would cost real coverage with no offsetting benefit.
