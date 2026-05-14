# ADR-0001: FreeCAD-runtime over standalone Document.xml parser

- **Status**: Accepted
- **Date**: 2026-05-14

## Context

`.FCStd` is a zip archive containing `Document.xml` (parametric tree, references) plus binary BRep blobs (evaluated geometry). To translate it into build123d we need a way to read both halves and resolve references between them.

Three architectures were viable:

1. **FreeCAD-runtime**: import FreeCAD as a Python module, use its loader to open the document, walk live Python objects.
2. **Standalone**: parse `Document.xml` ourselves; treat BRep blobs opaquely or skip them.
3. **Hybrid**: parse `Document.xml` ourselves; use `pythonocc-core` (pip-installable OCCT bindings) for BRep operations.

The forces in tension:

- **Install friction**: FreeCAD is not on PyPI. Only conda-forge ships a working Python-importable build. Standalone / hybrid would be pip-installable.
- **Topological references**: FreeCAD operations like `Fillet` reference edges by Open Cascade internal naming (e.g. `Edge8`). Those names are meaningful only in the context of an evaluated BRep, after the parametric model has been recomputed. Without running OCCT, you cannot resolve "Edge8" to the actual physical edge of the resulting solid.
- **Expression evaluation**: FreeCAD's expression grammar (used in property expressions, spreadsheet formulas) is parsed by a C++ Bison grammar that is not exposed to Python. The `ExpressionEngine` API does, however, return the original expression string.
- **Format stability**: FCStd is not a documented stable format. It is defined by what FreeCAD reads and writes; format changes ship with FreeCAD releases without external notice.
- **Maintenance burden**: standalone means we permanently track a format we don't control and reimplement a subset of FreeCAD's interpretation layer.

## Decision

Use the **FreeCAD-runtime** approach. The translator imports FreeCAD as a Python module and operates on live `App::Document` objects.

## Consequences

**Positive**

- Topological naming, sketch constraint solving, expression evaluation, link resolution, and recompute graph are all solved by FreeCAD. We inherit correctness on every operation FreeCAD supports.
- `ExpressionEngine` gives us raw expression strings for free, avoiding a custom Bison-grammar reimplementation.
- Forward compatibility: when FreeCAD adds new feature types, opening the file still works. We may not know what to do with the new types yet, but we don't crash.
- The FCStd format-tracking burden stays with FreeCAD, where it belongs.

**Negative**

- Not pip-installable. Distribution requires conda-forge or Docker (see ADR-0003).
- FreeCAD's Python API is not stable across minor versions. `PropertyExpressionEngine`, link/sub-shape APIs, and Sketcher APIs have all shifted historically. Requires version pinning and a test matrix.
- Conda-forge `freecad` package lags FreeCAD upstream releases by weeks.
- ~500MB install footprint. Multi-second cold start.
- LGPL-3 obligations for any future bundled redistribution (matters only if we ever ship a binary; not a v1 concern).

## Alternatives rejected

- **Standalone parser**: rejected. Cannot resolve topological references without running OCCT. The fillet/chamfer/pattern features that are core to real parametric designs would either be silently mistranslated or refused. The addressable subset (primitives + simple boolean operations only) is too narrow to justify the project.
- **Hybrid (`pythonocc-core` + custom XML parser)**: pip-installable and solves the geometric resolution problem. Rejected because we still reimplement FreeCAD's *interpretation* layer (how `Body` containers work, how `Spreadsheet` cells resolve cross-references, what property combinations mean what operation). The format-tracking burden remains. Trades full FreeCAD dependency for a different, smaller, but equally fragile dependency surface. May be revisited as a separate "lite" build (see ADR-0003).
