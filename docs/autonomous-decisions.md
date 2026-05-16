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


