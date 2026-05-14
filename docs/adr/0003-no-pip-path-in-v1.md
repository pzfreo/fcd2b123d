# ADR-0003: No pip-installable path in v1

- **Status**: Accepted
- **Date**: 2026-05-14

## Context

ADR-0001 commits us to running inside a FreeCAD Python environment. FreeCAD is not on PyPI as a working module, so a pip-installable distribution of this tool would require one of:

1. A "lite" build that handles only the subset of FreeCAD features achievable without the FreeCAD runtime (primitives, simple boolean operations, no derived-feature references) — shipped alongside the full conda build.
2. Dynamic detection of FreeCAD at runtime, with degraded behavior when it's missing.
3. Wait and see: ship conda-only first, decide later.

The pressure toward a pip path is real. Pip/uv is the lingua franca of Python tooling. Conda dependency excludes some environments (AWS Lambda, Pyodide, lightweight CI setups, users on `uv`-only workflows) and adds friction even for those who can use conda.

The counterforce: a tool that silently mistranslates inputs is much worse than a tool that refuses to handle them. A "lite" pip build would have to either (a) refuse most real-world FCStd files at parse time, or (b) silently produce wrong geometry when asked to translate a fillet on a derived feature. Option (a) makes the lite build nearly useless; option (b) is unacceptable.

## Decision

**Conda + Docker only in v1. No pip path.**

The lite-subset question is revisited *only if* v1 reveals, empirically, a clean and useful subset of input files that the lite version could handle without compromising correctness — and only if real users want that subset enough to justify the additional surface area.

## Consequences

**Positive**

- One code path, one install path, one test matrix. No bifurcation while we're still learning what the hard cases actually are.
- No risk of the lite build silently mistranslating features it can't really handle.
- No marketing pressure to make the lite build "almost as good as" the full build, which would inevitably push toward unsafe shortcuts.
- Clear correctness story: the tool either handles your file or refuses it. No tier of partial correctness.

**Negative**

- Smaller initial reach. Users who can't or won't install conda are blocked.
- CI integration for downstream users is more involved (conda-setup actions, or a Docker step) than `pip install` would be.
- Some environments are effectively excluded: serverless functions, browser-side Pyodide, minimal Alpine containers.
- "Why isn't this on PyPI?" will be a frequently-asked question.

## Reasoning

A pip-installable tool that silently mistranslates fillets is worse than a conda tool that gets them right. The correctness asymmetry is not symmetric in cost: a wrong translation might be committed to a repo, edited by an LLM, 3D-printed, machined, or built before anyone notices. An install friction is a one-time inconvenience.

We will not trade real harm (wrong output, eroded trust) for perceived benefit (easier install) before we have evidence that a safe subset exists. The hybrid `pythonocc-core` route stays on the table as a *future* ADR-superseding option, not a v1 commitment.
