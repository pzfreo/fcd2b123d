# Autonomous Decisions Log

Non-obvious calls the agent makes while working unattended, so the
human can spot-check judgement on return. Each entry: date, PR,
decision, reason, alternative considered.

Append-only. New entries at the **top**. Stylistic / one-liner calls
don't need entries; this is for choices the agent had to *think
about*.

## Template

```
### YYYY-MM-DD — PR #N — <one-line summary of the call>

**Context**: <what I was working on>

**Decision**: <what I did>

**Why**: <reasoning>

**Alternative considered**: <what else I could have done, why I didn't>

**Reversibility**: <trivial / requires-rework / hard>
```

---

## Entries

### 2026-05-16 — (no PR) — Bail on #33 Part::Helix; build123d sweep-along-helix has capability gap

**Context**: tier-2 roadmap item #33 — translate Part::Helix and use it as
a Sweep spine for thread-like geometry. Only library fixture cited is
``Beam-coupling-5mm-5mm``.

**What I did**: didn't write code. Reviewed prior attempt context.

  1. During #34 Sweep+Loft work I had already prototyped Helix-as-sweep-spine
     in the partdesign.py spine resolver. The synthetic `sweep_helix.FCStd`
     fixture (Part::Helix + Part::Sweep with a small triangle profile)
     produced a build123d result with **60% volume mismatch** vs FreeCAD's
     evaluated shape — same shape category but different cross-section
     orientation around the helical path.
  2. build123d has ``Helix(pitch, height, radius, ...)`` but **no**
     ``helical_sweep`` higher-level function; ``sweep(profile, path=helix)``
     handles the helical path differently from FreeCAD's Sweep with
     ``Frenet=True``.
  3. Beam-coupling-5mm-5mm has many other blockers (PartDesign::SubtractivePipe,
     ShapeBinder, FeatureBase clones) — Helix support alone wouldn't
     unblock it.

**Decision**: bail; defer #33 for in-session attention. The top-level
Helix emit by itself produces a Wire (no Solid) so even if I added it,
the verify harness can't confirm correctness end-to-end. And the
sweep-along-helix capability gap is between OCCT versions / Frenet
handling — not something fixable in our translator.

**Alternative considered**: emit Part::Helix as a top-level
``Helix(...)`` wire variable that downstream sweep features could use.
Rejected for now — no fixture exists to test it (no Sweep+Helix in
library that's otherwise in-scope), and "do it properly or not at all"
says don't ship untested.

**Reversibility**: trivial — no code changed.

**Follow-up actions**:
  - When build123d gains a ``helical_sweep`` or a Frenet flag on
    ``sweep``, revisit #33.
  - When a different library fixture surfaces top-level Helix without
    a SubtractivePipe blocker, the top-level emit becomes worth
    shipping.

---

### 2026-05-16 — (no PR) — Defer #43 coherent snap; deep refactor too risky for autonomous run

**Context**: tier-2 roadmap item #43 — snap solver-noise coordinates
(``54.999978`` → ``55``) coherently across connected edges in a sketch.

**What I did**: read the issue body, the existing ``format_value`` snap
in emitter.py, and the partdesign_example fixture emit. Confirmed the
problem: snapping arc-center to round shifts the computed arc endpoint
by ~21 nm; the connecting Line's start (stored at the un-snapped arc
end by FreeCAD's solver) no longer matches → BRep invalid.

**Decision**: defer for in-session attention. The fix requires an
anchor-point refactor in sketch.py:

  1. Collect all geometry endpoints + arc parameters before emit.
  2. Build a graph of which endpoints connect to which.
  3. Apply coherent snap: when snapping value V to V', propagate to
     every other coordinate computed from V.
  4. Re-render edges using the snapped values.

This is 1-2 days of careful work in sketch.py with high regression
surface (the issue body cites a prior failed naive-snap experiment
that broke 7 fixtures including the README hero). Per CLAUDE.md
"delay deep refactors till last" and CAUTIOUS post-#36-bail stance,
this isn't safe to attempt mid-loop without a way to A/B test against
the full corpus interactively.

**Status**: cosmetic-only — geometry is correct today, just the
emit has noise digits. No correctness loss in deferring.

**Reversibility**: trivial — no code changed.

---

### 2026-05-16 — (no PR) — Bail on #36 face-adjacency refactor; root cause is build123d-side OCCT, not edge selection

**Context**: top-3 roadmap item #36 — replace midpoint-based fillet/chamfer
edge selection with face-adjacency, to fix the `Oven_builtIn` deep-cascade
failure (build123d raises ``ValueError: Failed creating a fillet with
radius of 5`` partway through 8 cascaded fillets).

**What I did**: didn't write any code. Investigation first.

  1. Translated `Oven_builtIn` and re-exec'd up to the failing
     `fillet_005`.
  2. Confirmed `_edges_at(fillet_004, [...])` returns *exactly one*
     edge — selection is unambiguous, not a midpoint-confusion bug.
  3. The returned edge has `geom_type=GeomType.BSPLINE` (the
     accumulated fillets have turned a previously-straight edge into a
     B-spline curve).
  4. Tried filleting that edge with radii 5, 4, 3, 2, 1, 0.5, **0.1** —
     all fail with the same OCCT error.

**Decision**: face-adjacency refactor wouldn't help here. The right
edge is already being found; OCCT (under build123d) just can't fillet
a B-spline edge that FreeCAD's OCCT version can. This is a *capability*
gap, not a *selection* gap.

**Why bail rather than attempt anyway**: the proposed refactor is
2-3 days of careful work in `partdesign.py` + `emitter.py`. It would
need to be evaluated against fixtures where the edge selection is
actually wrong (different precision drift mode). Without a fixture
where face-adjacency demonstrably wins over midpoints — and with the
cited Oven fixture unblocked by something else entirely — the
refactor is speculative. Per CLAUDE.md "do it properly or not at
all": don't ship a refactor that doesn't have a fixture proving it
helps.

**Alternative considered**: catch the `ValueError` in the emit and
fall back to a smaller radius or skip the operation. Rejected —
that's pseudo-success theatre (the resulting shape isn't equivalent
to FreeCAD's, even if it executes).

**Reversibility**: trivial — no code changed. #36 stays open with this
decision linked in a comment.

**Follow-up actions**:
  - Add a comment to issue #36 pointing here.
  - If a future fixture surfaces *actual* edge mis-selection (multiple
    edges within tolerance, wrong one picked), revisit face-adjacency
    as a fix for that fixture specifically.


