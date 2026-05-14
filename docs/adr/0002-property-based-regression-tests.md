# ADR-0002: Property-based regression tests over emitted-source snapshots

- **Status**: Accepted
- **Date**: 2026-05-14

## Context

The translator produces build123d Python source from a FreeCAD input. We need a test methodology that gives us confidence the translation is correct.

Two approaches dominate code-generator testing:

1. **Snapshot the emitted source code**: commit the expected `.py` output, diff on every test run, fail on any change.
2. **Execute the emitted code, compare runtime behavior**: run the generated build123d Python, measure properties of the resulting solid, compare against expected values.

A complication unique to this project: a given 3D solid can be expressed in many valid build123d ways. Different operation orders, algebraic vs context-manager mode, different sketch decompositions can all produce the *same* geometry. We don't want a translation rewrite that improves the emitter style to break every test.

A second complication: we expect to accept LLM-generated contributions to the translator over time. Test methodology that can be "fixed" by mechanically regenerating snapshots will be — and the regression coverage will silently rot.

## Decision

**Property-based regression testing.** Each test fixture asserts the *geometric properties* of the translated solid match the FreeCAD original within tolerance.

Properties compared:

- Volume (relative tolerance 1e-6)
- Surface area (relative tolerance 1e-6)
- Center of mass in world frame (absolute tolerance scaled to bbox diagonal)
- Principal moments of inertia, sorted eigenvalues (relative tolerance 1e-5)

See ADR-0004 for which properties are explicitly excluded and why.

## Consequences

**Positive**

- Stable under emitter refactor. Changing the code generator to produce cleaner Python doesn't break tests, as long as geometry is preserved.
- Honest validation. "Does this translation reproduce the shape?" is the actual question we care about. Tests measure that directly.
- LLM-friendly. Accepting AI-generated translator changes is safer when tests can't be trivially "fixed" by regenerating an expected output.
- Spreadsheet parameter sweeps become a natural extension (test the same property comparison across varied parameter values) — this is the only honest way to prove named variables were preserved end-to-end.

**Negative**

- Slower than string diff. Each test runs build123d to produce a solid, computes properties via OCCT. Static tests: seconds each. Parameter sweeps: tens of seconds.
- Snapshot generation requires running FreeCAD, which means our test fixture authoring pipeline depends on the heavyweight runtime (see ADR-0001).
- Tolerance values need careful per-property tuning. OCCT operations aren't bit-deterministic across versions or platforms.
- Some translation bugs that produce subtly different topology may pass property tests — though such bugs are by definition geometrically equivalent. Hausdorff distance is reserved as a fallback for paranoid cases.

## Alternatives rejected

- **Golden-file snapshot of emitted Python**: rejected. Brittle under any emitter improvement. Doesn't validate correctness — only that the output is the same as last time. Creates a strong gravitational pull to "fix" failing tests by regenerating snapshots without checking whether the geometry actually changed. This pattern is especially toxic when accepting LLM-generated translator changes.
- **Visual diff of rendered images**: considered as a complement, not a primary mechanism. Rendered comparisons are sensitive to viewpoint, lighting, and rasterization, and produce false failures from cosmetic differences. May be added later as an optional sanity check; not load-bearing.
