# ADR-0004: Excluded geometric properties from comparison tests

- **Status**: Accepted
- **Date**: 2026-05-14

## Context

ADR-0002 commits us to property-based regression testing. The naive instinct is to compare every property OCCT exposes — face count, edge count, vertex count, bounding box, full moment-of-inertia tensor — on the principle that "more checks = stricter test = better confidence."

This instinct is wrong for this project. Several geometric properties of a BRep solid are *not invariant* under operations that preserve the geometric shape:

- **Face/edge/vertex counts** depend on operation order. Two fillets applied in different order on the same edges can produce different topology — the same solid, but with edges merged or split differently. A boolean union of two overlapping solids can produce different face counts depending on which operand is "base" vs "tool" in OCCT's algorithm, even when the resulting volume is identical.
- **Axis-aligned bounding box** is not invariant under rotation. If the translated solid is correctly geometrically equivalent but oriented in a different frame (e.g. because of how `Placement` was applied), the AABB will differ — but the shape is right.
- **Full moment-of-inertia tensor** depends on the choice of coordinate axes. Two equivalent solids in differently-oriented frames will have different tensors. The rotation-invariant scalar measure is the sorted eigenvalue triple (principal moments).

Including any of these as test assertions produces **false failures**: tests that fail when the translation is correct, just topologically expressed differently. False failures erode trust in the test suite, and the usual remedy — relaxing the assertion until it passes — defeats the point.

## Decision

Compare only **rotation-and-topology-invariant properties**:

| Included | Tolerance |
|---|---|
| Volume | relative 1e-6 |
| Surface area | relative 1e-6 |
| Center of mass (world frame, after Placement applied) | absolute, 1e-5 × bbox diagonal |
| Principal moments of inertia (sorted eigenvalues) | relative 1e-5 |

**Explicitly excluded:**

- Face count, edge count, vertex count
- Axis-aligned bounding box
- Full moment-of-inertia tensor
- Specific face/edge/vertex coordinates or identifiers

For paranoid cases — when included properties pass but visual inspection suggests something is wrong — Hausdorff distance between sampled meshes of both solids is available as a fallback. It is slow and not part of the default test suite.

## Consequences

**Positive**

- No false failures from legitimate topology differences. Tests survive emitter refactors, boolean-op-order changes, and equivalent-but-different-history translations.
- Tests measure what the user actually cares about: "is the translated shape the same as the original?" Not "is the BRep structurally identical?"
- Tolerances can be set tight on invariants without fragility, because invariants don't vary across legitimate equivalent expressions.

**Negative**

- A translation bug that produces a subtly different solid with the same volume, surface area, COM, and principal MOI would pass. This requires a fairly contrived bug — these four invariants over-constrain shape in practice for the kinds of parts FreeCAD users build — but it is theoretically possible. The Hausdorff fallback exists for this.
- Contributors will be tempted to "strengthen" tests by adding face-count assertions. This ADR exists in part to make that decision visible and prevent it.

## Alternatives rejected

- **Compare full MOI tensor**: rejected. Tensor depends on axis choice; principal moments are the rotation-invariant scalar measure. Comparing the tensor would force coordinate-frame alignment, which is fragile and adds no information beyond eigenvalues.
- **Include face/edge counts as "soft" assertions** (warning, not failure): rejected. Warnings that don't fail tests get ignored. If the property is not a reliable invariant, it should not be in the test at all.
- **Mesh-Hausdorff as the primary comparison**: rejected for the default suite. Slow (seconds per test, growing with mesh density), tolerance harder to reason about, and the four scalar invariants are sufficient for the common case. Hausdorff stays available as an opt-in fallback.
