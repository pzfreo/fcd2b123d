# STATUS

Live progress of autonomous work. The agent updates this file after
every merged PR while running in `/loop` mode. When you (the human)
come back, read this first — it summarises what changed and what's
still in flight without needing to scan every PR.

**Last update**: 2026-05-16 (during `/loop`, bailed on #36; about to start #43)

## Currently working on

*Transitioning to #43 (coherent snap) — top-of-tier-2 work.* Bailed
on #36 fillet face-adjacency refactor after investigation showed it
wouldn't help the cited fixture (decision logged).

## Recently merged (last 5)

- PR #84 — `feat: FreeCAD Labels for variable names + module docstring (closes #76)`
- PR #83 — `feat: Locations contexts for uniform patterns (closes #75)`
- PR #82 — `docs+tools: autonomous runway`
- PR #81 — `test: emit-source assertions`
- PR #80 — `docs: prioritised roadmap`

## Open / WIP (mine)

*none — just merged*

## Abandoned / deferred (with reason)

- **#36** Fillet face-adjacency — bailed after investigation.
  Root cause for `Oven_builtIn` is build123d-side OCCT can't fillet
  B-spline edges that emerge after deep cascades; even radius 0.1
  fails. Face-adjacency refactor would solve precision-drift
  *selection* problems, but selection is already correct here. Full
  decision in `docs/autonomous-decisions.md`; comment on issue #36.

## Top 3 status

- ✅ **#75** Locations contexts — merged (PR #83)
- ✅ **#76** FreeCAD Labels for names — merged (PR #84)
- ❌ **#36** Fillet face-adjacency — bailed (see decisions log)

## Next planned (per docs/roadmap.md)

1. **#43** — Coherent snap (tier 2). Risk: anchor-point refactor in
   sketch.py, high regression surface. Last xfail emit-quality test.
2. **#77** — Shared helpers module (tier 2, half day). Safe cleanup.
3. **#33** — Part::Helix (tier 2, half day). Build123d has the
   primitive; basic case should work.
4. **#78** — Builder-mode emit (tier 3). Bigger; skip from autonomous
   run unless the others finish quickly.

## Emit-quality regression gates

- `tests/test_emit_quality.py`:
  - #75 (PolarLocations) — PASS
  - #76 (Labels for naming) — PASS
  - #43 (no solver-noise digits) — xfailed (next to tackle)
  - #77 (--shared-helpers flag) — skip stub
  - #78 (--style=builder flag) — skip stubs ×2

## Stop conditions

The agent stops and waits when:

- It would need to add a fixture to `EXCLUDED_FROM_TEST` (CLAUDE.md
  requires explicit approval).
- It would need to edit `CLAUDE.md`, `SPEC.md`, `docs/adr/`, or
  `.github/`.
- A deep refactor has failed twice with the same class of error —
  revert and skip the issue (logged to `docs/autonomous-decisions.md`).
- The corpus-running-count regression gate trips.

## How to read this file when you're back

1. **Recently merged** — what shipped. Each PR's body has the detail.
2. **Open / WIP** — anything I left mid-flight.
3. **Abandoned / deferred** — issues I tried but couldn't finish.
4. **Top 3 status** — quick view of the highest-priority items.
