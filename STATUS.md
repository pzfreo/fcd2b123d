# STATUS

Live progress of autonomous work. The agent updates this file after
every merged PR while running in `/loop` mode. When you (the human)
come back, read this first — it summarises what changed and what's
still in flight without needing to scan every PR.

**Last update**: 2026-05-16 (autonomous runway PR — initial state)

## Currently working on

*idle — awaiting `/loop` invocation*

## Recently merged (last 5)

- PR #81 — `test: emit-source assertions` (xfail gates for #43, #75, #76)
- PR #80 — `docs: prioritised roadmap`
- PR #79 — `docs: emit code-quality review + style guide + project CLAUDE.md`
- PR #74 — `test: wire sample_813 into corpus`
- PR #73 — `feat: Part::Sweep + Part::Loft`

## Open / WIP (mine)

*none*

## Abandoned / deferred (with reason)

*none yet*

## Next planned (per docs/roadmap.md)

1. **#75** — Locations contexts for patterns (top-3, ~1 day)
2. **#76** — FreeCAD Labels for variable names (top-3, 1-2 days)
3. **#36** — Fillet face-adjacency (top-3, 2-3 days deep refactor)
4. **#43** — Coherent snap (tier 2, if time permits)
5. **#77** — Shared helpers module (tier 2, half day)

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
4. **Next planned** — what I'd pick up if `/loop`-ed again.
