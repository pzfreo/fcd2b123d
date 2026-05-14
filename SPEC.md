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

| Tier | Features | Why it's a tier boundary |
|------|----------|--------------------------|
| 1 | `Part::Box`, `Cylinder`, `Sphere`, `Cone`, `Torus` | Sanity check on the comparison harness itself. |
| 2 | `PartDesign::Body`, `Sketcher::SketchObject`, `Pad`, `Pocket`, `Revolution` | Core PartDesign workflow. Covers ~50% of real files. |
| 3 | `PartDesign::Fillet`, `Chamfer`, `Draft` | **Topological naming bites here.** Validates the FreeCAD-runtime approach. |
| 4 | `LinearPattern`, `PolarPattern`, `Mirrored` | Pattern features. Need transform resolution. |
| 5 | Boolean ops between bodies (`Part::Cut`, `Fuse`, `Common`) | Multi-body interaction. |
| 6 | `Spreadsheet::Sheet` + aliases + expressions on properties | The "named variables preserved" promise. |
| 7 | Selected real designs from FreeCAD Parts Library | End-to-end validation on non-toy input. |

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
