# STATUS

Live progress of autonomous work. The agent updates this file after
every merged PR while running in `/loop` mode. When you (the human)
come back, read this first — it summarises what changed and what's
still in flight without needing to scan every PR.

**Last update**: 2026-05-16 (during `/loop`, after PR #84 merge; about to start #36)

## Currently working on

*Idle/transition* — just merged #76 (FreeCAD Labels). Next: #36 Fillet
face-adjacency. About to branch.

## Recently merged (last 5)

- PR #84 — `feat: FreeCAD Labels for variable names + module docstring (closes #76)`
- PR #83 — `feat: Locations contexts for uniform patterns (closes #75)`
- PR #82 — `docs+tools: autonomous runway`
- PR #81 — `test: emit-source assertions`
- PR #80 — `docs: prioritised roadmap`

## Open / WIP (mine)

*none — just merged*

## Abandoned / deferred (with reason)

*none yet*

## Top 3 status

- ✅ **#75** Locations contexts — merged (PR #83)
- ✅ **#76** FreeCAD Labels for names — merged (PR #84)
- ⏭️ **#36** Fillet face-adjacency — next up (deep refactor, 2-3 days est.)

## Next planned (per docs/roadmap.md)

1. **#36** — Fillet face-adjacency (top-3, deep refactor). Will attempt; may bail and log to autonomous-decisions.md if I can't get a robust solution in two attempts.
2. **#43** — Coherent snap (tier 2). Risk: requires anchor-point refactor in sketch.py; high regression surface.
3. **#77** — Shared helpers module (tier 2, half day). Safe cleanup.
4. **#33** — Part::Helix (tier 2, half day).

## Emit-quality regression gates

- `tests/test_emit_quality.py`:
  - #75 (PolarLocations) — PASS
  - #76 (Labels for naming) — PASS
  - #43 (no solver-noise digits) — xfailed (tracked, will gate on #43 close)
  - #77 (--shared-helpers flag) — skip stub (awaits CLI flag)
  - #78 (--style=builder flag) — skip stubs ×2 (awaits CLI flag)

## Stop conditions

The agent stops and waits when:

- It would need to add a fixture to `EXCLUDED_FROM_TEST` (CLAUDE.md
  requires explicit approval).
- It would need to edit `CLAUDE.md`, `SPEC.md`, `docs/adr/`, or
  `.github/`.
- A deep refactor has failed twice with the same class of error —
  revert and skip the issue (logged to `docs/autonomous-decisions.md`).
- The corpus-running-count regression gate trips (someone landed a
  PR that drops it — the agent waits rather than fight the gate).

## How to read this file when you're back

1. **Recently merged** — what shipped. Each PR's body has the detail.
2. **Open / WIP** — anything I left mid-flight. Usually because CI
   was still running when the loop ended.
3. **Abandoned / deferred** — issues I tried but couldn't finish.
   `docs/autonomous-decisions.md` has the why-and-what-I-tried.
4. **Top 3 status** — quick view of the highest-priority items.
